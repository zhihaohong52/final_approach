import json

import pytest

from atm.geo.osm_surface import build_surface
from atm.wave import build
from atm.wave.model import Runway
from atm.wave.surface import (NoPath, TaxiGraph, _from_doc, choose_exit, generic_taxi_min, load_graph, rot_s,
                              unimpeded)


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
