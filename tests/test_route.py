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
    assert r.hold.src == "synth" and r.hold.fix.startswith("CP") and r.points[0].fix == r.hold.fix


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


def test_ord_hold_at_benky_meter_fix(monkeypatch):
    # an origin due along BENKY6's BFORD transition (SHAIN2 shares BFORD; the tie goes to the lower id)
    from atm.geo import store
    w = build.build_wave(ORD)
    bford = store.load_procedures("KORD")["fixes"]["BFORD"]
    monkeypatch.setattr(build, "_airport_coords", lambda iata: tuple(bford))
    r = route.build_route(w, w.flights[0].callsign, "27L")
    assert r.star == "BENKY6" and r.transition == "BFORD"
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


def _wave_at(iata, date, rids):
    from dataclasses import replace
    from atm.wave.model import Runway
    w = make_wave()
    rws = [Runway(x, 0.0, 9000) for x in rids]
    return replace(w, airport=replace(w.airport, iata=iata, runways=rws), window_start=f"{date}T12:00:00")


def test_current_id_refuses_closed_or_reassigned_designators():
    w = _wave_at("MDW", "2015-06-01", ["13L", "13C", "31R", "31C"])
    assert route._current_id(w, "13L") is None and route._current_id(w, "31R") is None   # closed 2023
    assert route._current_id(w, "13C") == "13L" and route._current_id(w, "31C") == "31R"  # renamed 2025
    assert route._current_id(_wave_at("MDW", "2026-01-01", ["13L"]), "13L") == "13L"


def test_clt_2015_routes_land_on_their_own_threshold():
    from atm.wave import flights2015 as F
    w = build.build_wave(f"F2015-{F.list_rows(airport='CLT', limit=1)[0]['row_id']}")
    for (cs, rid), r in route.routes_for_wave(w).items():
        rw = w.runway(rid)
        if r.approach is None:
            assert r.tier == "straight-in" and r.notes
            continue
        fr = route._Frame(rw.le_lat, rw.le_lon, rw.heading_true)
        last_fix = [p for p in r.points if p.kind == "final" and p.src == "cifp"][-1]
        assert fr.xy(last_fix.lat, last_fix.lon)[0] > 0.3     # on the approach side of its threshold
        assert _nm(last_fix, rw) < 12


def test_star_ending_on_the_final_joins_there():
    # KMIA FROGZ5 runway 12 transition ends at PIANA, a step-down fix between IF GLRIA and FAF VEPCO
    w = build.build_wave("F2015-58926")
    r = route.build_route(w, "MQ2910", "12")
    assert r.star == "FROGZ5" and not any(p.kind == "vector" for p in r.points)
    assert [p.fix for p in r.points].count("PIANA") == 1
    assert [p.fix for p in r.points][-3:] == ["PIANA", "VEPCO", "RW12"]


def test_rf_arc_from_end_course_matches_centre_fix():
    # quarter circle, radius 5 NM around (0, 0): from due west to due north, turning right (end course 090)
    start, end, ctr = route._dest(0, 0, 270, 5), route._dest(0, 0, 0, 5), (0.0, 0.0)
    a = route.rf_arc(start, end, ctr, None, "R")
    b = route.rf_arc(start, end, None, 90.0, "R")
    assert len(a) == len(b) == 8
    for (la, loa), (lb, lob) in zip(a, b):
        assert route._nm(la, loa, 0, 0) == pytest.approx(5, abs=0.01) and route._nm(la, loa, lb, lob) < 0.05
    assert all(la > 0 or lo < 0 for la, lo in b)                  # north-west quadrant
    assert route.rf_arc(start, end, None, None, "R") is None      # nothing to build from: caller chords
    assert route.rf_arc(start, end, None, 270.0, "R") is None     # start on the wrong side: no circle


def test_cifp_end_check_accepts_same_centreline_rejects_other_ends():
    from atm.geo import store
    w = build.build_wave(ORD)
    proc = store.load_procedures("KORD")
    assert route._cifp_end_check(proc, "09R", w.runway("09R")) is None     # 0.59 NM out, on the centreline
    assert route._cifp_end_check(proc, "10C", w.runway("10C")) is None
    assert route._cifp_end_check(proc, "28C", w.runway("10C")) is not None   # the opposite end, 1.6 NM away
    assert route._cifp_end_check(proc, "10L", w.runway("10C")) is not None   # the parallel
    assert route._cifp_end_check(proc, None, w.runway("10C")) is not None
    rev = route._cifp_end_check({**proc, "runways": {"X": {**proc["runways"]["10C"], "heading_mag": 273.0}}},
                                "X", w.runway("10C"))
    assert "heading" in rev                                                  # same spot, reversed course


def test_hold_fix_on_final_moves_before_the_if():
    from atm.wave.model import Runway
    w = make_wave()
    rw = Runway("27L", 270.0, 9000, le_lat=33.63, le_lon=-84.40, he_lat=33.63, he_lon=-84.43)
    b = route._Builder(w, w.flights[0].callsign, rw, None)
    f = b.frame
    b.pts = [route._P(*f.ll(30.0, 0.0), "entry", "ENTRY", "cifp", 9000.0, "constraint"),
             route._P(*f.ll(20.0, 0.0), "fix", "ALPHA", "cifp"),
             route._P(*f.ll(12.0, 0.0), "final", "BRAVO", "cifp", 3000.0, "constraint"),   # the IF
             route._P(*f.ll(5.0, 0.0), "final", "CHRLI", "cifp", 1600.0, "constraint", faf=True),
             route._P(*b.thr, "threshold", "RW27L", "cifp", 50.0, "constraint")]
    b.i_app, b.i_if = 2, 2
    r = b._finish("STAR1", None, None, "CHRLI", "star+iap")   # STAR's hold fix lies on the final
    assert r.hold.fix == "ALPHA" and r.hold_index == 1
    assert r.hold.base_src == "interp" and r.hold.base_ft == round(r.points[1].alt_ft / 1000) * 1000
    assert any("CHRLI is on the final approach" in n for n in r.notes)
    r2 = route._Builder(w, w.flights[0].callsign, rw, None)
    r2.pts, r2.i_app, r2.i_if = [route._P(p.lat, p.lon, p.kind, p.fix, p.src, p.alt, p.alt_src, faf=p.faf)
                                 for p in b.pts], 2, 2
    for p in r2.pts[1:2]:
        p.alt = None
    r2r = r2._finish("STAR1", None, None, "ENTRY", "star+iap")   # a hold before the IF is kept
    assert r2r.hold.fix == "ENTRY" and r2r.hold.base_ft == 9000 and r2r.hold.base_src == "constraint"
