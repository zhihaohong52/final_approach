import math

import pytest
from atm.wave import build, route
from atm.wave.fixtures import make_wave

ORD = "F2015-270287"


@pytest.fixture(autouse=True)
def _fresh_route_cache():
    route.routes_for_wave.cache_clear()
    yield
    route.routes_for_wave.cache_clear()


def _nm(p, rw) -> float:
    """Great-circle NM from a route point to the runway end's threshold (le_lat, le_lon)."""
    p1, p2 = math.radians(p.lat), math.radians(rw.le_lat)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(rw.le_lon - p.lon) / 2) ** 2)
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def test_ord_route_follows_star_and_ends_at_threshold():
    w = build.build_wave(ORD)
    cs = w.flights[0].callsign
    r = route.build_route(w, cs, next(x.id for x in w.airport.runways if x.open))
    assert r.tier == "star+iap" and r.star is not None
    assert r.points[-1].kind == "threshold" and r.points[0].kind == "entry"
    assert all(b.t_min > a.t_min for a, b in zip(r.points, r.points[1:]))
    assert r.hold.fix == next(p.fix for p in r.points[: r.hold_index + 1][::-1] if p.fix)


def test_fm_ended_star_gets_synth_vectors():
    w = build.build_wave(ORD)
    rs = route.routes_for_wave(w)
    assert any(any(p.kind == "vector" and p.src == "synth" for p in r.points) for r in rs.values())


def test_route_skips_runway_not_open_on_wave_date():
    w = build.build_wave(ORD)   # 2015-08-19: 09C/27C opened 2020-11-05
    assert not any(k[1] in ("09C", "27C") for k in route.routes_for_wave(w))


def test_far_runway_costs_more_route_time():
    w = build.build_wave(ORD)
    cs = w.flights[0].callsign
    times = sorted(route.build_route(w, cs, r.id).t_route_min for r in w.airport.runways if r.open)
    assert times[-1] - times[0] >= 1.0


def test_corner_post_when_no_star(monkeypatch):
    # an airport with approaches but no STARs: ABE per GEO_COVERAGE.md
    from atm.wave import flights2015 as F
    row = F.list_rows(airport="ABE", limit=1)[0]["row_id"]
    w = build.build_wave(f"F2015-{row}")
    r = route.build_route(w, w.flights[0].callsign, next(x.id for x in w.airport.runways if x.open))
    assert r.tier == "iap" and r.points[0].src == "synth" and r.hold.src in ("cifp", "synth")


def test_straight_in_when_no_procedures(monkeypatch):
    # spec §12 "straight-in only" (PBI/ISN cannot be built: no runways.csv rows), simulated on ABE
    from atm.geo import store
    from atm.wave import flights2015 as F
    monkeypatch.setattr(store, "load_procedures", lambda icao: None); route.routes_for_wave.cache_clear()
    row = F.list_rows(airport="ABE", limit=1)[0]["row_id"]
    w = build.build_wave(f"F2015-{row}")
    rw = next(x for x in w.airport.runways if x.open)
    r = route.build_route(w, w.flights[0].callsign, rw.id)
    assert r.tier == "straight-in" and all(p.src == "synth" for p in r.points)
    assert r.points[-1].kind == "threshold"
    d0 = _nm(r.points[0], rw)   # helper: great-circle NM from the point to the runway threshold
    assert 9.5 <= d0 <= 40.5    # straight-in from 10 NM, or the corner post at 40 NM before it


def test_cache_keyed_on_content_not_id():
    from dataclasses import replace
    w = build.build_wave(ORD)
    a = route.routes_for_wave(w)
    w2 = replace(w, airport=replace(w.airport, runways=[r for r in w.airport.runways if r.id != next(iter(a))[1]]))
    assert w2.id == w.id and route.routes_for_wave(w2) != a


def test_no_geometry_returns_none():
    w = make_wave()   # fixture runways have no coordinates
    assert route.build_route(w, "DL100", "26R") is None


# --- pinned rules beyond the brief's tests ------------------------------------------------------

def test_runway_transition_b_and_all_match_every_parallel():
    assert route._rwy_key_matches("04B", "04L") and route._rwy_key_matches("04B", "04R")
    assert route._rwy_key_matches("ALL", "27C") and route._rwy_key_matches("27L", "27L")
    assert not route._rwy_key_matches("04B", "22L") and not route._rwy_key_matches("27L", "27R")


def test_select_star_prefers_origin_bearing_and_lower_id_without_origin():
    proc = {"airport": {"lat": 0.0, "lon": 0.0},
            "fixes": {"N1": [1.0, 0.0], "S1": [-1.0, 0.0], "C1": [0.1, 0.0]},
            "stars": [{"id": "BBB1", "enroute": {"S1": [{"fix": "S1", "pt": "IF"}]},
                       "common": [{"fix": "C1", "pt": "TF"}], "runway": {"ALL": []}},
                      {"id": "AAA1", "enroute": {"N1": [{"fix": "N1", "pt": "IF"}]},
                       "common": [{"fix": "C1", "pt": "TF"}], "runway": {"09B": []}}]}
    assert route.select_star(proc, (-5.0, 0.0), "09L") == ("BBB1", "S1")   # origin due south
    assert route.select_star(proc, (5.0, 0.0), "09L") == ("AAA1", "N1")
    assert route.select_star(proc, None, "27L") == ("BBB1", "S1")          # AAA1 has no 27 transition


def test_ord_hold_at_benky_meter_fix():
    w = build.build_wave(ORD)
    rs = route.routes_for_wave(w)
    benky = [r for r in rs.values() if r.star == "BENKY6"]
    if not benky:
        pytest.skip("no flight in this wave is routed via BENKY6")
    r = benky[0]
    assert r.hold.fix == "BENKY" and r.hold.base_ft == 12000 and r.points[r.hold_index].fix == "BENKY"


def test_points_carry_sources_and_altitudes():
    w = build.build_wave(ORD)
    for r in route.routes_for_wave(w).values():
        assert r.points[0].t_min == 0 and r.t_route_min == pytest.approx(r.points[-1].t_min)
        assert all(p.src in ("cifp", "synth") and p.alt_src in ("constraint", "interp") for p in r.points)
        assert r.points[-1].alt_ft < r.points[0].alt_ft
        assert r.tier in ("star+iap", "iap", "straight-in")


def test_wave_key_memoised_and_content_based():
    w = make_wave()
    k = route.wave_key(w)
    assert w.__dict__["_key"] == k and route.wave_key(w) == k and len(k) == 40


def test_queue_arrivals_carry_origin():
    w = build.build_wave(ORD)
    assert w.queue and all(q.origin for q in w.queue)


def test_renamed_runway_uses_its_current_cifp_designator():
    # KAUS 17L/17R/35L/35R in 2015 became 18L/18R/36L/36R in 2021 (runway_history.csv `renamed`)
    from atm.wave import flights2015 as F
    w = build.build_wave(f"F2015-{F.list_rows(airport='AUS', limit=1)[0]['row_id']}")
    rw = next(x for x in w.airport.runways if x.open and x.id.startswith("35"))
    r = route.build_route(w, w.flights[0].callsign, rw.id)
    assert r.runway == rw.id and r.approach is not None and r.approach.endswith("36" + rw.id[2:])
