import json

from atm.geo.osm_surface import build_surface

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
    broken = json.loads(json.dumps(OSM)); broken["elements"] = [e for e in broken["elements"] if e.get("tags", {}).get("ref") != "T"]
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
