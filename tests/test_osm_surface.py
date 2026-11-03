import json

from backend.geo.osm_surface import build_surface

OSM = json.load(open("tests/data/osm/mini_airport.json"))
RWYS = json.load(open("tests/data/osm/mini_runways.json"))


def _s():
    return build_surface(OSM, "KXXX", RWYS, (0.0, 0.0), "2026-11-02T13:23:20Z")


def test_exits_per_end_sorted_with_angles():
    ex = _s()["exits"]["09"]
    assert [round(e["dist_m"], -2) for e in ex] == [500, 1600, 2500]
    assert [round(e["angle_deg"]) for e in ex] == [90, 30, 90]


def test_snap_within_15m_connects_dangling_taxiway():
    s = _s()
    assert s["quality"]["reach_pct"]["09"] == 1.0
    # A3 stops 10 m short of the centreline: only the snap makes it the third exit
    assert len(s["exits"]["09"]) == 3
    assert abs(s["exits"]["09"][2]["dist_m"] - 2500) < 1.0


def test_crossing_edges_labelled():
    assert any(e[5] == "18/36" for e in _s()["edges"])
    assert not any(e[5] == "09/27" for e in _s()["edges"])


def test_degree2_chains_merged():
    assert len(_s()["nodes"]) < sum(len(w["nodes"]) for w in OSM["elements"] if w["type"] == "way")


def test_reach_drops_when_stands_unreachable():
    # Drop lane T and move the S1/S2 cluster 600 m north, beyond the 150 m segment snap of every taxiway:
    # the stands are then reachable by no taxi edge (the old cluster sat ~100 m from taxiway A and would now snap to it).
    broken = json.loads(json.dumps(OSM)); broken["elements"] = [e for e in broken["elements"] if e.get("tags", {}).get("ref") != "T"]
    for e in broken["elements"]:
        if e.get("tags", {}).get("aeroway") == "parking_position":
            e["lat"] = 0.0060
    assert build_surface(broken, "KXXX", RWYS, (0.0, 0.0), "t")["quality"]["reach_pct"]["09"] == 0.0


def test_no_tier_stored_offline():
    assert "tier" not in _s()["quality"]


def test_header_stands_gates_holds_terminals_draw():
    s = _s()
    assert s["header"] == {"source": "OpenStreetMap", "license": "ODbL",
                           "attribution": "© OpenStreetMap contributors", "osm_timestamp": "2026-11-02T13:23:20Z"}
    assert [(x["id"], x["ref"]) for x in s["stands"]] == [("S1", "1"), ("S2", "2")]
    assert all(0 <= x["node"] < len(s["nodes"]) for x in s["stands"])
    assert [g["ref"] for g in s["gates"]] == ["G1"]
    assert [h["strip"] for h in s["holding_positions"]] == ["18/36"]
    assert len(s["terminals"]) == 1
    assert sorted(r["ids"][0] for r in s["draw"]["runways"]) == ["09", "18"]
    assert {t["name"] for t in s["draw"]["taxiways"]} >= {"A", "B", "T"}


def test_both_ends_and_exit_angles_from_other_threshold():
    s = _s()
    assert set(s["exits"]) == {"09", "27", "18", "36"}
    assert s["exits"]["18"] == [] and s["exits"]["36"] == []  # the crossing taxiway B is not an exit
    assert [round(e["angle_deg"]) for e in s["exits"]["27"]] == [90, 150, 90]
    assert [round(e["dist_m"], -2) for e in s["exits"]["27"]] == [500, 1400, 2500]
    assert set(s["quality"]["reach_pct"]) == {"09", "27", "18", "36"}


def test_end_missing_from_osm_is_absent():
    rw = RWYS + [{"runway": "14", "le_lat": 0.02, "le_lon": 0.02, "he_lat": 0.03, "he_lon": 0.03},
                 {"runway": "32", "le_lat": 0.03, "le_lon": 0.03, "he_lat": 0.02, "he_lon": 0.02}]
    s = build_surface(OSM, "KXXX", rw, (0.0, 0.0), "t")
    assert "14" not in s["quality"]["reach_pct"] and "14" not in s["exits"]


def test_crossing_strip_only_on_taxiway_b_edges():
    s = _s()
    labelled = [e for e in s["edges"] if e[5] is not None]
    assert labelled and all(e[3] == "taxiway" and e[4] == "B" and e[5] == "18/36" for e in labelled)
    assert not any(e[3] == "runway" and e[5] is not None for e in s["edges"])


def test_reach_does_not_taxi_along_a_runway():
    # a stand cluster hangs off a stub at the south end of runway 18/36 (an exit of 18/36 only): from 09 it is
    # reachable only by taxiing along the runway, which does not count
    import math
    osm = json.loads(json.dumps(OSM))
    rw = next(e for e in osm["elements"] if e["type"] == "way" and e["tags"].get("ref") == "18/36")
    thr36 = rw["nodes"][-1]
    d = lambda m_: round(math.degrees(m_ / 6371000.0), 7)
    osm["elements"] += [
        {"type": "node", "id": 9002, "lat": d(350), "lon": d(900)},
        {"type": "node", "id": 9003, "lat": d(300), "lon": d(900), "tags": {"aeroway": "parking_position", "name": "SX", "ref": "9"}},
    ]
    osm["elements"].append({"type": "way", "id": 9100, "nodes": [thr36, 9002, 9003], "tags": {"aeroway": "taxilane", "ref": "Z"}})
    s = build_surface(osm, "KXXX", RWYS, (0.0, 0.0), "t")
    assert s["quality"]["reach_pct"]["09"] == round(2 / 3, 4)
    assert s["quality"]["reach_pct"]["36"] == round(1 / 3, 4)


def _with_stand(dy_m, name="SX", extra=()):
    import math
    osm = json.loads(json.dumps(OSM))
    osm["elements"] += [{"type": "node", "id": 9500 + i, "lat": round(math.degrees((300 + dy_m) / 6371000.0), 7),
                         "lon": round(math.degrees((1600 + 5 * i) / 6371000.0), 7),
                         "tags": {"aeroway": "parking_position", "name": name, "ref": "x"}}
                        for i in range(1 + len(extra))]
    return build_surface(osm, "KXXX", RWYS, (0.0, 0.0), "t")


def test_off_graph_stand_snaps_within_150m_else_isolated():
    near = _with_stand(30)    # 30 m north of T's end of the S1/S2 row, within 150 m of a T segment
    far = _with_stand(500)
    n_near = next(x for x in near["stands"] if x["id"] == "SX")
    n_far = next(x for x in far["stands"] if x["id"] == "SX")
    assert n_near["node"] < len(_s()["nodes"])
    assert n_far["node"] == len(far["nodes"]) - 1       # isolated node appended after the graph nodes
    assert far["quality"]["reach_pct"]["09"] == round(2 / 3, 4)


def test_duplicate_stand_names_fall_back_to_osm_ids():
    s = _with_stand(30, extra=(1,))
    ids = [x["id"] for x in s["stands"]]
    assert len(set(ids)) == len(ids) and "S1" in ids and "SX" not in ids


def test_present_end_with_no_exits_reports_reach():
    s = _s()
    assert s["exits"]["18"] == []
    assert s["quality"]["reach_pct"]["18"] == 0.0   # no exit node to seed from


def test_stand_far_from_vertices_snaps_onto_the_middle_of_a_long_segment():
    import math
    osm = json.loads(json.dumps(OSM))
    lat = lambda m: round(math.degrees(m / 6371000.0), 7)
    osm["elements"] += [
        {"type": "node", "id": 9801, "lat": lat(2000), "lon": 0.0251810},
        {"type": "way", "id": 9802, "nodes": [1017, 9801], "tags": {"aeroway": "taxiway", "ref": "C"}},   # one 1.8 km segment
        {"type": "node", "id": 9803, "lat": lat(1100), "lon": round(0.0251810 + math.degrees(100 / 6371000.0), 7),
         "tags": {"aeroway": "parking_position", "name": "SM", "ref": "m"}}]   # 100 m east of the segment's middle
    s = build_surface(osm, "KXXX", RWYS, (0.0, 0.0), "t")
    sm = next(x for x in s["stands"] if x["id"] == "SM")
    assert sm["node"] < len(s["nodes"]) and any(sm["node"] in e[:2] for e in s["edges"] if e[3] == "taxiway")
    assert sum(e[4] == "C" for e in s["edges"]) == 2            # the segment was split at the stand's projection
    assert abs(sum(e[2] for e in s["edges"] if e[4] == "C") - (2000 - 200)) < 5   # lengths stay consistent
    assert s["quality"]["reach_pct"]["09"] == 1.0


def _with_nodes(*extra):
    osm = json.loads(json.dumps(OSM))
    osm["elements"] = osm["elements"] + [
        {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": tags} for i, lat, lon, tags in extra]
    return osm


def _xy(s, i):
    lat, lon = s["nodes"][i]
    return lon * 111195.0, lat * 111195.0


def _on_runway(s, i):
    return any(e[3] == "runway" and i in e[:2] for e in s["edges"])


def test_hold_off_way_splits_taxiway_at_its_osm_position_not_nearest_runway_node():
    hp = {"aeroway": "holding_position", "holding_position:type": "runway"}
    s = build_surface(_with_nodes((9001, 0.00185, 0.0112, hp)), "KXXX", RWYS, (0.0, 0.0), "t")
    new = [h for h in s["holding_positions"] if abs(_xy(s, h["node"])[0] - 0.0112 * 111195.0) < 1.0]
    assert len(new) == 1
    x, y = _xy(s, new[0]["node"])
    assert abs(y - 0.0017986 * 111195.0) < 1.0               # on taxiway A, not the 6 m-off OSM node, not a runway node
    assert not _on_runway(s, new[0]["node"])


def test_hold_tagged_on_runway_vertex_dropped_with_warning():
    import pytest
    osm = json.loads(json.dumps(OSM))
    for e in osm["elements"]:
        if e["type"] == "node" and e["id"] == 1003:       # runway vertex that is also the A2 junction
            e["tags"] = {"aeroway": "holding_position"}
    with pytest.warns(UserWarning, match="dropped 1 holding_position"):
        s = build_surface(osm, "KXXX", RWYS, (0.0, 0.0), "t")
    assert [h["strip"] for h in s["holding_positions"]] == ["18/36"]
    assert not any(_on_runway(s, h["node"]) for h in s["holding_positions"])


def test_hold_on_pavement_snapped_to_taxiway_is_dropped():
    import pytest
    hp = {"aeroway": "holding_position"}
    with pytest.warns(UserWarning, match="dropped 1"):       # 11 m up A2, inside the 45 m pavement band
        s = build_surface(_with_nodes((9002, 0.0001, 0.0143991, hp)), "KXXX", RWYS, (0.0, 0.0), "t")
    assert len(s["holding_positions"]) == 1


def test_hold_strip_inferred_from_runway_way_when_no_strip_pairs():
    hp = {"aeroway": "holding_position"}
    s = build_surface(_with_nodes((9003, 0.0009, 0.0045066, hp)), "KXXX", [], (0.0, 0.0), "t")
    assert "09/27" in [h["strip"] for h in s["holding_positions"]]
