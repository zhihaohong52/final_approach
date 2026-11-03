import json

import pytest

from atm.geo.osm_surface import build_surface
from atm.wave import build
from atm.wave.model import Runway
from atm.wave.surface import (GroundTrack, NoPath, Reservations, TaxiGraph, _from_doc, choose_exit, generic_taxi_min,
                              load_graph, rot_s, schedule, unimpeded, verify_separation)


def _doc():
    osm = json.load(open("tests/data/osm/mini_airport.json"))
    rw = json.load(open("tests/data/osm/mini_runways.json"))
    return build_surface(osm, "KXXX", rw, (0.0, 0.0), "t")


@pytest.fixture
def mini():
    return _from_doc("KXXX", _doc())


def test_exit_choice_narrow_dry_wet_wide(mini):
    assert choose_exit(mini, "09", "narrow", False)["dist_m"] == pytest.approx(1600, abs=50)
    assert choose_exit(mini, "09", "narrow", True)["dist_m"] == pytest.approx(2500, abs=50)   # 1725 m needed
    assert choose_exit(mini, "09", "wide", False)["dist_m"] == pytest.approx(2500, abs=50)


def test_exit_choice_skips_backward_and_falls_back_to_last(mini):
    assert choose_exit(mini, "27", "narrow", False)["dist_m"] == pytest.approx(2500, abs=50)   # 1400 m exit is 150 deg
    assert choose_exit(mini, "09", "wide", True)["dist_m"] == pytest.approx(2500, abs=50)      # 2300 needed: last exit


def test_empty_or_absent_exits_are_generic(mini):
    mini.exits["18"] = []
    for end in ("18", "ZZ"):
        e = choose_exit(mini, end, "wide", True)
        assert e["generic"] and e["node"] is None and e["dist_m"] == pytest.approx(2300)
        assert rot_s(e, 140) > 0


def test_load_graph_drops_empty_exit_ends_and_does_not_mutate_cache():
    g = _from_doc("KXXX", _doc())
    assert "18" not in g.exits and set(g.exits) == {"09", "27"}
    assert g.stands["1"]["node"] == 19


def test_rot_formula():
    assert rot_s({"dist_m": 1600, "angle_deg": 30}, 140) == pytest.approx(2 * 1600 / (140 * 0.514444 + 25 * 0.514444), rel=1e-6)
    assert rot_s({"dist_m": 1600, "angle_deg": 90}, 140) == pytest.approx(2 * 1600 / (140 * 0.514444 + 12 * 0.514444), rel=1e-6)


def test_unimpeded_time_on_mini_graph(mini):
    p = unimpeded(mini, mini.exits["09"][1]["node"], mini.stands["1"]["node"])
    assert p.src == "osm" and p.names[0] == "A2" and p.seg_s[-1] == pytest.approx(30.0)
    assert p.unimpeded_min == pytest.approx(sum(p.seg_s) / 60)
    assert len(p.seg_s) == len(p.names) + 1 and len(p.nodes) == len(p.names) + 1
    assert p.crossings == []


def test_router_never_uses_runway_edges(mini):
    kinds = {(u, v): k for u, es in mini.adj.items() for v, _, k, _, _ in es}
    for ex in mini.exits["09"]:
        p = unimpeded(mini, ex["node"], mini.stands["2"]["node"])
        assert all(kinds[(a, b)] != "runway" for a, b in zip(p.nodes, p.nodes[1:]))


def test_crossing_recorded_once_per_node(mini):
    # from the 18/36 centreline node 6 area: route 16 -> 6 -> 17 crosses the strip once
    p = unimpeded(mini, 16, 17)
    assert p.nodes == [16, 6, 17] and p.crossings == [(6, "18/36")]


def test_unreachable_stand_raises_nopath(mini):
    with pytest.raises(NoPath):
        unimpeded(mini, mini.exits["09"][0]["node"], len(mini.nodes) + 99)   # a node id outside the graph


def test_isolated_stand_is_nopath(mini):
    mini.nodes.append((0.0, 0.0)); mini.adj[len(mini.nodes) - 1] = []
    with pytest.raises(NoPath):
        unimpeded(mini, mini.exits["09"][0]["node"], len(mini.nodes) - 1)


def test_generic_taxi_min():
    r = Runway("09", 90.0, 9800, le_lat=0.0, le_lon=0.0, he_lat=0.0, he_lon=0.0269796)
    # midpoint 1500 m east of the origin; a gate 2000 m north of it -> 2000 * 1.4 m at 15 kt
    m = generic_taxi_min(r, 2000 / 111194.9266, 0.0134898)
    assert m == pytest.approx(2000 * 1.4 / (15 * 0.514444) / 60, rel=1e-3)


def test_ord_routes_exist_for_all_open_ends_in_osm():
    w = build.build_wave("F2015-270287"); g = load_graph("KORD")
    # ends that are open in the wave but absent from OSM (e.g. closed since 2015) use the generic tier (spec §6.9, C14)
    ends = [x for x in w.airport.runways if x.open and x.id in g.exits]
    assert ends
    for r in ends:
        e = choose_exit(g, r.id, "narrow", False)
        ok = []
        for s in g.stands.values():
            try:
                ok.append(unimpeded(g, e["node"], s["node"]).unimpeded_min)
            except NoPath:   # a few OSM stands lie beyond the 150 m snap and stay isolated: a clean typed result
                pass
        assert len(ok) >= 0.95 * len(g.stands) and max(ok) < 30


def test_exit_leading_to_a_stubless_fragment_is_skipped(mini):
    # a dead-end taxi fragment (no stand) at 1,600 m in place of A2: the next exit that reaches stands wins
    mini.nodes.append((0.0, 0.0)); k = len(mini.nodes) - 1
    mini.adj[k] = []
    mini.exits["09"][1] = {"node": k, "dist_m": 1600.0, "angle_deg": 30.0}
    assert choose_exit(mini, "09", "narrow", False)["dist_m"] == pytest.approx(2500, abs=50)


# --- hold-short scheduler (single-path safe-interval search) and the independent verifier ---

def path_S1_to_exit(g):
    return unimpeded(g, g.stands["1"]["node"], g.exits["09"][1]["node"])      # stand S1 (19) -> exit A2 (2)


def path_exit_to_S1(g):
    return unimpeded(g, g.exits["09"][1]["node"], g.stands["1"]["node"])      # the same taxiways, head-on


def path_across_1836(g):
    p = unimpeded(g, 10, 17)                                                  # A/B junction -> across 18/36 at node 6
    assert p.crossings == [(6, "18/36")]
    return p


def track(s, cs):
    return GroundTrack(cs, "arrival", True, None, "", [], s.points, s.waits, 0.0, 0.0, "osm")


def test_head_on_resolved_by_wait(mini):
    res = Reservations({}, {}, {})
    a = schedule(mini, path_S1_to_exit(mini), 0.0, res, "DEP1")
    b = schedule(mini, path_exit_to_S1(mini), 0.2, res, "ARR1")
    assert a.ok and b.ok and b.waits and "give way to DEP1" in b.waits[0]["reason"]
    assert verify_separation([track(a, "DEP1"), track(b, "ARR1")]) == []


def test_crossing_waits_for_landing_gap(mini):
    res = Reservations({}, {}, {"18/36": [(1.0, "landing", "UA12")]})
    path = path_across_1836(mini)
    s = schedule(mini, path, 0.5, res, "AA1")
    assert s.ok and s.waits[0]["reason"] == "hold short 18/36 for landing UA12"
    # spec §4.4 gap: no landing in [t - 1, t + 1.5] -> crossings blocked for t in [-0.5, 2.0]
    node = next(n for n, strip in path.crossings if strip == "18/36")
    assert next(q["t_min"] for q in s.points if q["node"] == node) > 2.0


def test_crossing_blocked_beyond_horizon_fails(mini):
    res = Reservations({}, {}, {"18/36": [(t / 2, "landing", f"L{t}") for t in range(0, 80)]})
    s = schedule(mini, path_across_1836(mini), 0.0, res, "AA1")
    assert not s.ok and "30 min" in s.reason


def test_unimpeded_schedule_has_no_waits_and_reserves(mini):
    res = Reservations({}, {}, {})
    path = path_across_1836(mini)
    s = schedule(mini, path, 0.5, res, "AA1")
    assert s.ok and s.reason is None and s.waits == []
    assert s.points[-1]["t_min"] == pytest.approx(0.5 + path.unimpeded_min)
    assert s.exit_clear_min == pytest.approx(0.5)
    assert [q["node"] for q in s.points] == path.nodes + [path.nodes[-1]]          # arrival at each node, then parked
    assert all(isinstance(q["node"], int) for q in s.points)
    assert set(res.node_busy) == set(path.nodes) and all(v[0][2] == "AA1" for v in res.node_busy.values())
    assert set(res.edge_dir) == set(zip(path.nodes, path.nodes[1:]))


def test_takeoff_gap_is_one_minute_each_side(mini):
    path = path_across_1836(mini)
    t_cross = 0.5 + sum(path.seg_s[:3]) / 60          # 10 -> 15 -> 16 -> 6
    res = Reservations({}, {}, {"18/36": [(t_cross + 1.2, "takeoff", "DL5")]})
    assert schedule(mini, path, 0.5, res, "AA1").waits == []                        # 1.2 min later: outside +1 min
    res = Reservations({}, {}, {"18/36": [(t_cross + 0.9, "takeoff", "DL5")]})
    s = schedule(mini, path, 0.5, res, "AA1")
    assert s.waits[0]["reason"] == "hold short 18/36 for takeoff DL5"
    assert next(q["t_min"] for q in s.points if q["node"] == 6) > t_cross + 1.9


def test_crossing_wait_is_at_the_osm_holding_position(mini):
    # move the 18/36 holding position one node back (15): the aircraft holds there, not on the node next to the strip
    mini.holds = {15: "18/36"}
    res = Reservations({}, {}, {"18/36": [(1.0, "landing", "UA12")]})
    s = schedule(mini, path_across_1836(mini), 0.5, res, "AA1")
    assert s.waits[0]["node"] == 15 and s.waits[0]["to_min"] > s.waits[0]["from_min"]
    assert all(w["node"] != 16 for w in s.waits)


def test_node_headway_gives_way_and_verifier_flags_violations(mini):
    res = Reservations({}, {}, {})
    path = path_across_1836(mini)
    a = schedule(mini, path, 0.0, res, "LEAD")
    b = schedule(mini, path, 0.1, res, "TRAIL")                                     # 6 s behind: inside 30 s
    assert b.ok and b.waits[0]["reason"] == "give way to LEAD"
    assert verify_separation([track(a, "LEAD"), track(b, "TRAIL")]) == []
    for n in path.nodes:                                                            # node passages >= 30 s apart
        ta = [q["t_min"] for q in a.points if q["node"] == n]
        tb = [q["t_min"] for q in b.points if q["node"] == n]
        assert min(tb) >= max(ta) + 0.5 - 1e-9
    # the verifier is independent: tracks scheduled without each other's reservations are flagged
    c = schedule(mini, path, 0.1, Reservations({}, {}, {}), "TRAIL")
    assert any("LEAD" in m and "TRAIL" in m for m in verify_separation([track(a, "LEAD"), track(c, "TRAIL")]))


def test_verifier_flags_head_on(mini):
    a = schedule(mini, path_S1_to_exit(mini), 0.0, Reservations({}, {}, {}), "DEP1")
    b = schedule(mini, path_exit_to_S1(mini), 0.2, Reservations({}, {}, {}), "ARR1")
    msgs = verify_separation([track(a, "DEP1"), track(b, "ARR1")])
    assert any("opposite" in m for m in msgs)


def test_exit_node_blocked_delays_runway_vacate(mini):
    path = unimpeded(mini, 2, 19)
    res = Reservations({2: [(0.0, 3.0, "BLOCK")]}, {}, {})
    s = schedule(mini, path, 0.0, res, "AA1")
    assert s.ok and s.exit_clear_min >= 3.5 - 1e-9 and s.points[0]["node"] == 2
    assert s.waits[0] == {**s.waits[0], "node": 2, "from_min": 0.0, "reason": "give way to BLOCK"}


def test_schedule_is_deterministic(mini):
    def run():
        res = Reservations({}, {}, {"18/36": [(1.0, "landing", "UA12"), (1.0, "takeoff", "AB1")]})
        a = schedule(mini, path_S1_to_exit(mini), 0.0, res, "DEP1")
        b = schedule(mini, path_exit_to_S1(mini), 0.2, res, "ARR1")
        c = schedule(mini, path_across_1836(mini), 0.3, res, "AA1")
        return a, b, c, res
    assert run() == run()


def test_hold_index_adjacent_strips_and_lookback_cap(mini):
    from atm.wave.surface import _hold_index
    mini.holds = {}
    # a -> R1 -> R2 -> b: two strips with no node between them are crossed as one block from index 0
    assert _hold_index(mini, [100, 101, 102, 103], [(101, "A"), (102, "B")]) == [None, 0, 0, 0]
    # a holding position more than 3 edges before the strip is not used: wait at the node before it
    mini.holds = {200: None}
    assert _hold_index(mini, [200, 201, 202, 203, 204, 205], [(204, "A")])[4:] == [3, 3]
    assert _hold_index(mini, [200, 201, 202, 204, 205], [(204, "A")])[1:] == [0, 0, 0, 0]
