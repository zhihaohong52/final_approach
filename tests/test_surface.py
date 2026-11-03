import json
from dataclasses import replace

import pytest

from backend.geo.osm_surface import build_surface
from backend.wave import build
from backend.wave.model import Runway
from backend.wave.surface import (RUNWAY_HOLD_CLEARANCE_M, RUNWAY_ON_PAVEMENT_M, GroundTrack, NoPath, Reservations,
                              TaxiGraph, _from_doc, choose_exit, generic_taxi_min, load_graph, rot_s, schedule,
                              unimpeded, verify_separation)


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
    # the whole time in the 18/36 zone (hold 16 -> node 17, 300 m beyond, ~0.5 min after the centreline) is checked
    res = Reservations({}, {}, {"18/36": [(t_cross + 1.6, "takeoff", "DL5")]})
    assert schedule(mini, path, 0.5, res, "AA1").waits == []                        # zone left > 1 min before it
    res = Reservations({}, {}, {"18/36": [(t_cross + 1.2, "takeoff", "DL5")]})
    assert schedule(mini, path, 0.5, res, "AA1").waits                              # still on 17's side of the zone
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


M_PER_DEG = 111194.92664


def _line_graph(xs_m, strips, holds=None):
    """A west-east taxiway along lat 0 with nodes at `xs_m` metres; each strip {label: x_m} is a north-south runway
    whose centreline node lies on the taxiway; the two taxi edges at a centreline node carry the strip."""
    xs = sorted(set(xs_m) | set(strips.values()))
    nodes = [(0.0, x / M_PER_DEG) for x in xs]
    at = {x: i for i, x in enumerate(xs)}
    adj = {i: [] for i in range(len(xs) + 2 * len(strips))}
    lab = {at[x]: s for s, x in strips.items()}

    def edge(u, v, length, kind, name, strip):
        adj[u].append((v, length, kind, name, strip)); adj[v].append((u, length, kind, name, strip))

    for a, b in zip(xs, xs[1:]):
        edge(at[a], at[b], b - a, "taxiway", "A", lab.get(at[a]) or lab.get(at[b]))
    for s_, x in sorted(strips.items()):
        for lat in (-0.02, 0.02):
            nodes.append((lat, x / M_PER_DEG))
            edge(at[x], len(nodes) - 1, abs(lat) * M_PER_DEG, "runway", s_, None)
    for lst in adj.values():
        lst.sort(key=lambda e: (e[0], e[1], e[3] or ""))
    g = TaxiGraph("KLIN", nodes, adj, {}, {}, {}, {at[x]: st for x, st in (holds or {}).items()})
    return g, at


def _line_path(g, at, xs):
    p = unimpeded(g, at[min(xs)], at[max(xs)])
    assert p.nodes == [at[x] for x in sorted(at)]
    return p


def _assert_no_wait_in_zone(g, s, strips):
    from backend.wave.surface import strip_distance_m
    for w in s.waits:
        for strip in strips:
            ok_hold = g.holds.get(w["node"], "-") in (None, strip)
            assert ok_hold or strip_distance_m(g, w["node"], strip) >= 90.0, (w, strip)


def test_no_wait_next_to_the_centreline_when_blocked_beyond(mini):
    # the reviewer's ORD case: a conflict at c+2 used to park the aircraft at c+1, 16 m from the centreline
    xs = [-400, -300, -200, -100, 16, 40, 100, 300]
    g, at = _line_graph(xs, {"R": 0})
    p = _line_path(g, at, xs)
    c = p.nodes.index(at[0])
    t_c = sum(p.seg_s[:c]) / 60
    res = Reservations({p.nodes[c + 2]: [(0.0, t_c + 6.0, "BLOCK")]}, {}, {"R": [(t_c + 3.0, "landing", "UA9")]})
    s = schedule(g, p, 0.0, res, "AA1")
    assert s.ok and s.waits and all(w["node"] == at[-100] for w in s.waits)    # 100 m out, no OSM hold
    _assert_no_wait_in_zone(g, s, ["R"])
    assert verify_separation([track(s, "AA1")], runway_events=res.runway_events, g=g) == []


def test_no_osm_hold_within_lookback_waits_outside_clearance(mini):
    xs = [-400, -300, -200, -60, 50, 300]
    g, at = _line_graph(xs, {"R": 0}, holds={-400: "R"})                       # the OSM hold is 4 edges back
    p = _line_path(g, at, xs)
    res = Reservations({}, {}, {"R": [(0.3, "landing", "UA9")]})
    s = schedule(g, p, 0.0, res, "AA1")
    assert s.ok and [w["node"] for w in s.waits] == [at[-200]]                 # -60 m is inside the 90 m zone
    _assert_no_wait_in_zone(g, s, ["R"])
    assert verify_separation([track(s, "AA1")], runway_events=res.runway_events, g=g) == []
    g2, at2 = _line_graph(xs, {"R": 0}, holds={-300: "R"})                     # 3 edges back: used
    s2 = schedule(g2, _line_path(g2, at2, xs), 0.0, Reservations({}, {}, {"R": [(0.3, "landing", "UA9")]}), "AA1")
    assert [w["node"] for w in s2.waits] == [at2[-300]]


def test_two_strips_two_nodes_apart_are_one_zone(mini):
    xs = [-400, -300, -200, -100, 30, 300]
    g, at = _line_graph(xs, {"R": 0, "S": 60})                                 # path: ... -100, R, 30, S, 300
    p = _line_path(g, at, xs)
    assert [st for _, st in p.crossings] == ["R", "S"]
    c2 = p.nodes.index(at[60])
    assert p.nodes[c2 - 2] == at[0]
    t_s = sum(p.seg_s[:c2]) / 60
    res = Reservations({at[30]: [(0.0, t_s + 2.0, "BLOCK")]}, {}, {"S": [(t_s + 1.0, "takeoff", "DL5")]})
    s = schedule(g, p, 0.0, res, "AA1")
    assert s.ok and s.waits and all(w["node"] == at[-100] for w in s.waits)
    _assert_no_wait_in_zone(g, s, ["R", "S"])
    assert verify_separation([track(s, "AA1")], runway_events=res.runway_events, g=g) == []


def test_verifier_flags_wait_inside_runway_zone_during_landing(mini):
    # the old behaviour, built by hand: crossing R, then waiting 16 m beyond the centreline while UA9 lands on R
    xs = [-200, -100, 16, 40, 300]
    g, at = _line_graph(xs, {"R": 0})
    pts = [{"t_min": t, "node": at[x], "lat": 0.0, "lon": x / M_PER_DEG}
           for t, x in [(0.0, -200), (0.2, -100), (0.4, 0), (0.45, 16), (6.0, 16), (6.1, 40), (6.5, 300)]]
    bad = GroundTrack("AA1", "arrival", True, None, "", [], pts, [], 0.0, 0.0, "osm")
    ev = {"R": [(3.0, "landing", "UA9")]}
    msgs = verify_separation([bad], runway_events=ev, g=g)
    assert msgs and "UA9" in msgs[0] and "R protected zone" in msgs[0]
    assert verify_separation([bad]) == []                                     # the old call still works
    assert verify_separation([bad], runway_events={"R": [(3.0, "landing", "AA1")]}, g=g) == []   # its own movement


def test_failed_flight_still_reserves_and_later_flights_give_way(mini):
    res = Reservations({}, {}, {"18/36": [(t / 2, "landing", f"L{t}") for t in range(0, 80)]})
    a = schedule(mini, path_across_1836(mini), 0.0, res, "AA1")
    assert not a.ok and "30 min" in a.reason
    assert any(x[2] == "AA1" for x in res.node_busy[6])
    b = schedule(mini, path_across_1836(mini), 0.0, res, "BB1", horizon_min=90.0)
    assert b.ok and any(w["reason"] == "give way to AA1" for w in b.waits)
    assert verify_separation([track(a, "AA1"), track(b, "BB1")], runway_events=res.runway_events, g=mini) == []


def test_verifier_edge_overlap_is_exact(mini):
    def tr(cs, seq):
        return GroundTrack(cs, "arrival", True, None, "", [],
                           [{"t_min": t, "node": n, "lat": 0.0, "lon": 0.0} for t, n in seq], [], 0.0, 0.0, "osm")
    # head-on on edge 1-2 for 0.1 s only, no common node within headway (nodes far apart in time)
    a = tr("A", [(0.0, 1), (1.0, 2)])
    b = tr("B", [(0.9995, 2), (2.0, 1)])
    msgs = verify_separation([a, b], headway_s=0.0)
    assert any("opposite" in m for m in msgs)


# --- round 2: geometric crossings, start-in-zone, vacate stretch, guard ---

def _route_via(g, end, pred):
    stands = sorted(g.stands.values(), key=lambda s: s["node"])
    for e in g.exits[end][:3]:
        for st in stands[::max(1, len(stands) // 15)]:
            try:
                p = unimpeded(g, e["node"], st["node"])
            except NoPath:
                continue
            if pred(p):
                return p
    raise AssertionError("no such route")


def test_constants_exported():
    assert RUNWAY_HOLD_CLEARANCE_M == 90.0 and RUNWAY_ON_PAVEMENT_M == 45.0


def _any_route(g, pred, ends=None):
    """First route (exit end order, first 3 exits per end, ~15 sampled stands) that satisfies `pred`."""
    stands = sorted(g.stands.values(), key=lambda s: s["node"])
    for end in sorted(ends or g.exits):
        for e in g.exits[end][:3]:
            for st in stands[::max(1, len(stands) // 15)]:
                try:
                    p = unimpeded(g, e["node"], st["node"])
                except NoPath:
                    continue
                if pred(p):
                    return p
    raise AssertionError("no such route")


def test_untagged_crossing_found_geometrically_ord_ss():
    # KORD taxiway SS meets 09R/27L at a runway node, but its OSM edges carry no strip tag (found by property,
    # not by node id, so a surface rebuild does not break the test)
    from backend.wave.surface import runway_strips_at
    g = load_graph("KORD")

    def untagged_ss(u):
        es = g.adj[u]
        return ("09R/27L" in runway_strips_at(g, u)
                and sum(1 for _, _, k, n, st in es if k != "runway" and n == "SS" and st is None) >= 2
                and not any(k != "runway" and st for _, _, k, _, st in es))

    ss = [u for u in range(len(g.nodes)) if untagged_ss(u)]
    assert ss
    p = _any_route(g, lambda p: any(u in p.nodes[1:-1] for u in ss))
    u = next(u for u in p.nodes[1:-1] if u in ss)
    assert (u, "09R/27L") in p.crossings


def test_ksea_untagged_crossing_waits_at_the_osm_hold():
    # KSEA taxiway N crosses 16L/34R with no shared node (node 71 is 1 m from the centreline)
    from backend.wave.surface import strip_distance_m
    g = load_graph("KSEA")
    p = _route_via(g, "16R", lambda p: 71 in p.nodes and 72 in p.nodes)
    assert any(st == "16L/34R" for _, st in p.crossings)
    i71 = p.nodes.index(71)
    t71 = sum(p.seg_s[:i71]) / 60
    res = Reservations({72: [(0.0, t71 + 6, "BLOCK")]}, {}, {"16L/34R": [(t71 + 3, "landing", "UA9")]})
    s = schedule(g, p, 0.0, res, "AA1")
    assert s.ok and s.waits
    for w in s.waits:
        assert w["node"] != 71
        assert g.holds.get(w["node"], "-") in (None, "16L/34R") or strip_distance_m(g, w["node"], "16L/34R") >= 90
    assert verify_separation([track(s, "AA1")], runway_events=res.runway_events, g=g) == []


def test_starting_inside_another_strips_zone_fails_ord_27c():
    # a KORD 27C exit on 09C/27C that lies inside 04L/22R's zone (45-90 m: off its pavement) and whose route crosses
    # 04L/22R next: the aircraft cannot back out
    from backend.wave.surface import runway_strips_at, strip_distance_m
    g = load_graph("KORD")
    starts = {e["node"] for e in g.exits["27C"] if "09C/27C" in runway_strips_at(g, e["node"])
              and RUNWAY_ON_PAVEMENT_M <= strip_distance_m(g, e["node"], "04L/22R") < RUNWAY_HOLD_CLEARANCE_M}
    assert starts
    p = _any_route(g, lambda p: p.nodes[0] in starts and any(st == "04L/22R" for _, st in p.crossings), ["27C"])
    res = Reservations({}, {}, {"04L/22R": [(1.0, "landing", "UA9")]})
    s = schedule(g, p, 0.0, res, "AA1")
    assert not s.ok and "04L/22R" in s.reason and "UA9" in s.reason
    assert any("04L/22R" in m and "UA9" in m
               for m in verify_separation([track(s, "AA1")], runway_events=res.runway_events, g=g))
    quiet = schedule(g, p, 0.0, Reservations({}, {}, {"04L/22R": [(9.0, "landing", "UA9")]}), "AA1")
    assert quiet.ok


def test_no_wait_in_the_vacate_stretch_and_exit_clear_is_off_the_runway_zone():
    # starting on runway R at x=0: nodes 20 m and 50 m off it are still in its zone; a conflict at 120 m waits at 0
    xs = [0, 20, 50, 120, 400]
    g, at = _line_graph(xs, {"R": 0})
    p = _line_path(g, at, xs)
    assert p.crossings == []                                      # vacating the runway it starts on is not a crossing
    res = Reservations({at[120]: [(0.0, 2.0, "BLOCK")]}, {}, {})
    s = schedule(g, p, 0.0, res, "AA1")
    assert s.ok and [w["node"] for w in s.waits] == [at[0]]
    assert s.exit_clear_min == pytest.approx(next(q["t_min"] for q in s.points if q["node"] == at[120]))


def test_guard_trip_reserves_nothing(mini, monkeypatch):
    import backend.wave.surface as S
    monkeypatch.setattr(S, "_MAX_ITER", 2)
    res = Reservations({}, {}, {"18/36": [(t / 2, "landing", f"L{t}") for t in range(0, 80)]})
    s = schedule(mini, path_across_1836(mini), 0.0, res, "AA1")
    assert not s.ok and "converge" in s.reason and res.node_busy == {} and res.edge_dir == {}


def test_verifier_needs_events_and_graph_together(mini):
    with pytest.raises(ValueError):
        verify_separation([], runway_events={"18/36": []})
    with pytest.raises(ValueError):
        verify_separation([], g=mini)


# --- round 3: OSM holds snapped onto the runway pavement are not holding positions ---

def _hand_track(g, p, cs, t0, wait_idx, wait_to):
    """A track along `p` from t0 at unimpeded speed, with one wait at index `wait_idx` until `wait_to` (old bug)."""
    pts, t = [], t0
    for i, nd in enumerate(p.nodes):
        pts.append({"t_min": t, "node": nd, "lat": g.nodes[nd][0], "lon": g.nodes[nd][1]})
        if i == wait_idx:
            t = wait_to
            pts.append({"t_min": t, "node": nd, "lat": g.nodes[nd][0], "lon": g.nodes[nd][1]})
        if i < len(p.nodes) - 1:
            t += p.seg_s[i] / 60
    return GroundTrack(cs, "arrival", True, None, "", [], pts, [], 0.0, 0.0, "osm")


# Rebuilt surface data drops OSM holds on the pavement, so these tests inject one into a synthetic line graph.

def test_osm_hold_on_the_runway_does_not_end_the_vacate_stretch():
    # the route starts on runway R; an OSM hold for R snapped 20 m off its centreline must not end the vacate stretch
    from backend.wave.surface import on_runway
    xs = [0, 20, 60, 120, 400]
    g, at = _line_graph(xs, {"R": 0}, holds={20: "R"})
    assert on_runway(g, at[20], "R")
    p = _line_path(g, at, xs)
    t120 = sum(p.seg_s[:3]) / 60
    res = Reservations({at[120]: [(0.0, t120 + 3.0, "BLOCK")]}, {}, {"R": [(2.0, "landing", "UA9")]})
    s = schedule(g, p, 0.0, res, "AA1")
    assert [w["node"] for w in s.waits] == [at[0]]                            # held at the exit, not on the hold
    assert s.exit_clear_min == pytest.approx(next(q["t_min"] for q in s.points if q["node"] == at[120]))
    assert s.exit_clear_min > 2.0                                              # T31 fails it: still on R
    ev = res.runway_events
    msgs = verify_separation([track(s, "AA1")], runway_events=ev, g=g)
    assert any("vacating R" in m and "UA9" in m for m in msgs)
    # the old behaviour, by hand: off the exit, then waiting on the hold at 20 m through UA9's landing
    old = _hand_track(g, p, "AA1", 0.0, 1, 3.6)
    assert any("UA9" in m for m in verify_separation([old], runway_events=ev, g=g))


def test_osm_hold_on_the_runway_is_not_a_wait_spot():
    # crossing R: an OSM hold for R 30 m from the centreline (on the pavement) is skipped; the one at 75 m is used
    from backend.wave.surface import on_runway
    xs = [-400, -200, -75, -30, 40, 76, 300]
    g, at = _line_graph(xs, {"R": 0}, holds={-75: "R", -30: "R"})
    assert on_runway(g, at[-30], "R") and not on_runway(g, at[-75], "R")
    p = _line_path(g, at, xs)
    i = p.nodes.index(at[76])
    t76 = sum(p.seg_s[:i]) / 60
    res = Reservations({at[76]: [(0.0, t76 + 4.0, "BLOCK")]}, {}, {"R": [(t76 + 2.0, "landing", "UA9")]})
    s = schedule(g, p, 0.0, res, "AA1")
    assert s.ok and [w["node"] for w in s.waits] == [at[-75]]
    assert verify_separation([track(s, "AA1")], runway_events=res.runway_events, g=g) == []
    # the old behaviour, by hand: waiting on the on-pavement hold through the landing
    old = _hand_track(g, p, "AA1", 0.0, p.nodes.index(at[-30]), t76 + 4.5)
    msgs = verify_separation([old], runway_events=res.runway_events, g=g)
    assert any("R protected zone" in m and "UA9" in m for m in msgs)


# --- Task 24: routed taxi drives in-block; real ATC-TAXI-ROUTE; gate coordinates ---

from backend.wave import moves, rules, surface, trajectory   # noqa: E402
from backend.wave.moves import fcfs_plan   # noqa: E402
from backend.wave.utility import metrics_map   # noqa: E402


def _by(checks, rule, cs):
    return next(c for c in checks if c.rule == rule and c.callsign == cs)


@pytest.fixture
def fresh_taxi():
    """The NoPath test monkeypatches surface.unimpeded: drop every derived taxi cache before and after."""
    trajectory.build.cache_clear(); surface.wave_tier.cache_clear()
    yield
    trajectory.build.cache_clear(); surface.wave_tier.cache_clear()


def test_in_block_is_slot_plus_rot_plus_taxi():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    ts = trajectory.build(w, p); mm = metrics_map(w, p)
    assert any(x.wave for x in ts.ground)
    for g in (x for x in ts.ground if x.wave):
        a = p.get(g.callsign)
        assert mm[a.callsign].in_block_min == pytest.approx(a.slot_min + mm[a.callsign].rot_s / 60 + g.taxi_min, abs=0.05)
        assert g.points[-1]["t_min"] == pytest.approx(mm[a.callsign].in_block_min, abs=0.05)   # in_block_min is rounded to 0.1


def test_taxi_route_rule_cites_route():
    w = build.build_wave("F2015-270287"); c = _by(rules.check(w, fcfs_plan(w)), "ATC-TAXI-ROUTE", w.focus)
    assert c.passed and c.evidence["tier"] == "real" and c.evidence["route"] and c.source.startswith("OpenStreetMap")


def test_taxi_route_fails_when_stand_unreachable(monkeypatch, fresh_taxi):
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    gate = p.get(w.focus).gate
    real = surface.unimpeded
    def fake(g, exit_node, stand):
        if stand == surface.stand_node_for(w, gate):
            raise surface.NoPath(gate)
        return real(g, exit_node, stand)
    monkeypatch.setattr(surface, "unimpeded", fake)
    trajectory.build.cache_clear(); surface.wave_tier.cache_clear()
    c = _by(rules.check(w, p), "ATC-TAXI-ROUTE", w.focus)
    assert not c.passed and gate in c.detail


def test_crew_floor_uses_routed_taxi():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    c = _by(rules.check(w, p), "PLT-CREW-DUTY", w.focus)
    assert "taxi" in c.evidence and c.evidence["taxi"] == pytest.approx(
        min(surface.min_unimpeded_taxi(w, w.focus, r.id) for r in w.airport.runways if r.open), abs=0.05)


def test_change_runway_label_has_taxi():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    ms = [m for m in moves._candidates(w, p, "ATC") if m.kind == "change_runway"]
    assert ms and all("taxi" in m.label and len(m.label) <= 120 for m in ms)


def test_gates_have_geo_on_replay_hub():
    w = build.build_wave("F2015-270287")
    assert all(g.lat is not None and g.geo_src for g in w.gates)


# --- Task 24: tiers, provenance, serialisation (ours) ---

def test_wave_tier_and_surface_provenance_on_ord():
    w = build.build_wave("F2015-270287")
    assert surface.wave_tier(w, surface.load_graph("KORD")) == "real"
    pv = next(p for p in w.provenance if p.layer == "surface")
    assert pv.status == "real" and pv.source.startswith("OpenStreetMap 20")


def test_runway_end_missing_from_osm_uses_generic_taxi():
    # ORD 14L (open in 2015) is not in today's OSM: straight-line generic estimate, src synth, still passes
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    a = p.get(w.focus)
    assert "14L" in {r.id for r in w.airport.runways if r.open} and "14L" not in surface.load_graph("KORD").exits
    q = p.with_assignment(replace(a, runway="14L"))
    c = _by(rules.check(w, q), "ATC-TAXI-ROUTE", w.focus)
    assert c.passed and c.evidence["tier"] == "generic" and c.source.startswith("generic surface")
    g = next(x for x in trajectory.build(w, q).ground if x.callsign == w.focus)
    assert g.src == "synth" and g.route_names == []
    rw = w.runway("14L"); gt = w.gate(a.gate)
    assert g.taxi_min == pytest.approx(surface.generic_taxi_min(rw, gt.lat, gt.lon))


def test_no_geometry_tier_keeps_v2_taxi():
    from tests.fixtures import make_wave
    from backend.wave.utility import taxi_min
    w = make_wave(); p = fcfs_plan(w); mm = metrics_map(w, p)
    assert trajectory.build(w, p).ground == []
    for a in p.assignments:
        assert mm[a.callsign].in_block_min == pytest.approx(a.slot_min + taxi_min(w, a.runway, a.gate), abs=0.05)
        assert mm[a.callsign].rot_s is None
    c = _by(rules.check(w, p), "ATC-TAXI-ROUTE", "DL100")
    assert c.passed and c.evidence["tier"] == "none" and c.source == "ops rule (no geometry)"


def test_ground_track_serialises_node_as_string():
    import json
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    d = trajectory.build(w, p).to_dict()
    json.dumps(d)
    g = next(x for x in d["ground"] if x["callsign"] == w.focus)
    assert g["src"] == "osm" and all(isinstance(q["node"], str) for q in g["points"] if "node" in q)
    assert g["waits"] == [] and g["taxi_min"] == g["unimpeded_min"]


def test_taxi_points_are_monotonic_and_start_after_rot():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w); mm = metrics_map(w, p)
    for g in trajectory.build(w, p).ground:
        if not g.wave:
            continue                       # fixed traffic (v3-traffic): starts at its own exit / pushback
        ts = [q["t_min"] for q in g.points]
        assert ts == sorted(ts)
        a = p.get(g.callsign)
        assert ts[0] == pytest.approx(a.slot_min + mm[a.callsign].rot_s / 60)


def test_hot_path_taxi_is_warm_after_build(monkeypatch, fresh_taxi):
    # the per-wave taxi table is built at wave-build time: evaluating a plan (metrics, rules, utilities) never runs
    # a Dijkstra
    from backend.wave.utility import evaluate
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    trajectory.build.cache_clear(); surface.precompute(w)

    def boom(g, root):
        raise AssertionError("Dijkstra in the hot path")
    monkeypatch.setattr(surface, "_tree", boom)
    evaluate(w, p)
    for ag in ("ATC", "GROUND", f"PILOT:{w.focus}"):
        moves._candidates(w, p, ag)


def test_exit_fallback_keeps_choose_exit_order(monkeypatch, fresh_taxi):
    # the chosen exit cannot reach the stand: the fallback is the next forward exit past the required roll, never a
    # short or backward exit (which would understate the ROT and the in-block)
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    a = p.get(w.focus); size = w.flight(w.focus).gate_size
    g = surface.load_graph("KORD")
    first = surface.choose_exit(g, a.runway, size, False)
    real = surface.unimpeded

    def fake(gr, exit_node, stand):
        if exit_node == first["node"]:
            raise surface.NoPath("blocked")
        return real(gr, exit_node, stand)
    monkeypatch.setattr(surface, "unimpeded", fake)
    trajectory.build.cache_clear()
    tx = surface.taxi_info(w, a.runway, a.gate, size)
    assert tx.tier == "real" and tx.no_path is None and tx.exit["node"] != first["node"]
    assert tx.exit["angle_deg"] <= surface.BACKWARD_DEG and tx.exit["dist_m"] >= surface.ROLL_M[size]
    short = [e for e in g.exits[a.runway] if e["dist_m"] < surface.ROLL_M[size] or e["angle_deg"] > 90]
    assert short                     # the end has exits the old fallback order could have picked first


def test_surface_cache_clear():
    w = build.build_wave("F2015-270287")
    surface.cache_clear()
    assert not surface._GEO and not surface._TABLE and not surface._TREE_ORDER
    assert surface.taxi_info(w, "22R", w.flight(w.focus).scheduled_gate, "narrow").tier == "real"   # pin still valid
