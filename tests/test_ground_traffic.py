from dataclasses import replace
import json
from pathlib import Path

import pandas as pd
import pytest

from backend.geo import store
from backend.wave import build, moves, surface
from backend.wave.fixtures import make_wave
from backend.wave.ground_traffic import departure_mode, fixed_traffic, fixed_tracks, reservations
from backend.wave.model import QueueArrival, Runway

# Real BTS On-Time 2015 movement rows (US DOT, public domain) for the fixture days, cut to the scheduled window the
# waves read: ORD 2015-08-19 (F2015-270287), ATL 2015-03-01 (F2015-69315), DFW 2015-01-01 (F2015-75, also a replay).
# F2015-270287 and F2015-69315 were the ORD / ATL replays when these fixtures were cut; they are fixture waves now.
FIXTURES = Path(__file__).resolve().parent / "data" / "movements"
DAYS = {"ORD": "ORD-2015-08-19.csv", "ATL": "ATL-2015-03-01.csv", "DFW": "DFW-2015-01-01.csv"}


@pytest.fixture(autouse=True)
def _fixture_movements(monkeypatch):
    """Movements from the committed fixtures (the real files are gitignored and DB-seeded), so the tests run
    offline and identically with or without data/ref/movements2015."""
    from backend.datastore import files
    real = files._movements
    frames = {k: pd.read_csv(FIXTURES / v, dtype=str, keep_default_na=False) for k, v in DAYS.items()}
    monkeypatch.setattr(files, "_movements", lambda iata: (frames[iata], "date") if iata in frames else real(iata))
    store.load_movements.cache_clear(); reservations.cache_clear()
    yield
    store.load_movements.cache_clear(); reservations.cache_clear()


def test_ord_has_real_departures():
    w = build.build_wave("F2015-270287")
    ft = fixed_traffic(w)
    assert any(m.kind == "departure" for m in ft) and any(p.layer == "ground_traffic" and p.status == "real" for p in w.provenance)


def test_reservations_deterministic_and_headway():
    w = build.build_wave("F2015-270287")
    r1 = reservations(w)
    reservations.cache_clear()                      # otherwise r2 is the cached r1 and the test asserts nothing
    r2 = reservations(build.build_wave("F2015-270287"))
    assert r1 is not r2
    assert r1 == r2
    for busy in r1.node_busy.values():
        s = sorted(busy)
        assert all(b[0] - a[0] >= 0.5 - 1e-9 for a, b in zip(s, s[1:]))   # 30 s


def test_partial_badge_without_movements():
    from backend.geo import store
    from backend.wave import flights2015 as F
    iata = next(r["dest"] for r in F.list_rows(limit=500) if r["dest"] not in store.MOVEMENT_AIRPORTS)
    w = build.build_wave(f"F2015-{F.list_rows(airport=iata, limit=1)[0]['row_id']}")
    pv = next(p for p in w.provenance if p.layer == "ground_traffic")
    assert pv.status == "generic" and "partial" in pv.detail


def test_departures_use_real_times_and_window():
    w = build.build_wave("F2015-270287")
    deps = [m for m in fixed_traffic(w) if m.kind == "departure"]
    assert len(deps) >= 20
    for m in deps:
        assert -30 - 1e-9 <= m.event_min <= 120 + 1e-9          # wheels-off inside [T0 - 30, T0 + 120]
        assert m.t_start_min < m.event_min                      # pushback = WHEELS_OFF - TAXI_OUT
    ft = fixed_traffic(w)
    assert [(m.t_start_min, m.callsign) for m in ft] == sorted((m.t_start_min, m.callsign) for m in ft)


def test_departure_paths_run_stand_to_holding_point():
    w = build.build_wave("F2015-270287")
    g = surface.load_graph(w.airport.icao)
    strips = surface.runway_strips(g)
    for m in fixed_traffic(w):
        if m.kind != "departure" or m.path is None:
            continue
        assert m.path.nodes[0] == m.stand_node and all(isinstance(n, int) for n in m.path.nodes)
        last = m.path.nodes[-1]
        assert not any(surface.on_runway(g, last, st) for st in strips)        # holds short, off every runway
        assert len(m.path.seg_s) == len(m.path.nodes)                         # one per edge + the line-up buffer


def test_runway_events_landings_and_takeoffs():
    w = build.build_wave("F2015-270287")
    g = surface.load_graph(w.airport.icao)
    res = reservations(w)
    ev = {(cs, kind): (strip, t) for strip, lst in res.runway_events.items() for t, kind, cs in lst}
    for q in w.queue:
        assert ev[(q.callsign, "landing")][1] == pytest.approx(q.slot_min)
    arrivals = {f.callsign for f in w.flights} | {q.callsign for q in w.queue}
    assert not any(cs in arrivals for (cs, kind) in ev if kind == "takeoff")   # own-event skip is by callsign
    relabelled = [m for m in fixed_traffic(w) if m.label != m.callsign]
    assert relabelled and all(m.callsign in arrivals and m.label == f"{m.callsign} (dep)" for m in relabelled)
    for m in fixed_traffic(w):
        if m.kind == "departure":
            strip, t = ev[(m.label, "takeoff")]
            assert t >= m.event_min - 1e-9 and strip in surface.runway_strips(g)


def test_fixed_tracks_are_separated():
    w = build.build_wave("F2015-270287")
    g = surface.load_graph(w.airport.icao)
    res = reservations(w)
    tracks = fixed_tracks(w)
    assert tracks and all(t.wave is False for t in tracks)
    assert surface.verify_separation(tracks) == []
    msgs = surface.verify_separation(tracks, runway_events=res.runway_events, g=g)
    assert [m for m in msgs if "protected zone" in m] == []


def test_provenance_counts_movers_and_failures():
    w = build.build_wave("F2015-270287")
    pv = next(p for p in w.provenance if p.layer == "ground_traffic")
    assert pv.source == "BTS 2015 movements"
    assert "departures" in pv.detail and "arrivals" in pv.detail and "still reserved" in pv.detail


def _two_parallel_wave(open27=True):
    rw = [Runway("08L", 94, 9000), Runway("26R", 274, 9000), Runway("09R", 94, 9000),
          Runway("27L", 274, 9000, open=open27)]
    return make_wave(queue=[QueueArrival("WN1", "B737", "LARGE", "26R", 2)], airport=replace(make_wave().airport,
                                                                                               runways=rw))


def test_departure_mode_segregated_and_mixed(monkeypatch):
    w = _two_parallel_wave()
    monkeypatch.setattr(moves, "fcfs_runways", lambda wave: {f.callsign: "26R" for f in wave.flights})
    assert departure_mode(w) == (False, "26R", ["27L"])          # parallel same-direction end free of arrivals
    assert departure_mode(_two_parallel_wave(open27=False)) == (True, "26R", ["26R"])   # no open parallel: mixed
    one = make_wave(airport=replace(make_wave().airport, runways=[Runway("08L", 94, 9000), Runway("26R", 274, 9000)]))
    assert departure_mode(one) == (True, "26R", ["26R"])
    monkeypatch.setattr(moves, "fcfs_runways", lambda wave: {"DL100": "26R", "DL201": "27L", "AA302": "26R",
                                                             "UA403": "26R"})
    assert departure_mode(w) == (True, "26R", ["26R"])            # both parallels host arrivals: mixed mode


def test_departures_only_on_the_flow_departure_runways():
    from backend.wave import rules
    w = build.build_wave("F2015-270287")
    mixed, arr_rw, cands = departure_mode(w)
    deps = [m for m in fixed_traffic(w) if m.kind == "departure"]
    assert deps and {m.runway for m in deps} <= set(cands)
    assert any(r["limit"] == "departure takeoff spacing" and r["source"] == "assumption" for r in rules.LIMITS)


def test_multi_candidate_airport_spreads_departures():
    w = build.build_wave("F2015-75")                     # DFW, segregated: three free parallel ends
    mixed, arr_rw, cands = departure_mode(w)
    assert not mixed and len(cands) > 1
    used = {m.runway for m in fixed_traffic(w) if m.kind == "departure"}
    assert len(used) > 1 and used <= set(cands)


def test_trial_scheduling_does_not_leak(monkeypatch):
    import copy
    from backend.wave import ground_traffic as GT
    real = GT._settle_runway
    calls = []

    def checked(g, m, res, spans, settled=None):
        before = copy.deepcopy((res.node_busy, res.edge_dir, spans))
        ev = copy.deepcopy(res.runway_events)
        real(g, m, res, spans, settled)
        assert (res.node_busy, res.edge_dir, spans) == before          # trials wrote only to their views
        mine = lambda evs: sorted((s, e) for s, l in evs.items() for e in l if e[2] == m.label)
        others = lambda evs: {s: [e for e in l if e[2] != m.label] for s, l in evs.items()}
        assert others(res.runway_events) == {s: l for s, l in others(ev).items()}
        assert len(mine(res.runway_events)) == len(mine(ev)) == 1        # its own takeoff, maybe on another strip
        calls.append(m.label)

    monkeypatch.setattr(GT, "_settle_runway", checked)
    reservations.cache_clear()
    w = build.build_wave("F2015-75")
    assert calls and fixed_traffic(w)


@pytest.mark.parametrize("wid", ["F2015-75", "F2015-69315"])   # DFW, ATL (departures crossing a departure runway)
def test_no_fixed_track_in_a_protected_zone(wid):
    from backend.wave import ground_traffic as GT
    w = build.build_wave(wid)
    g = surface.load_graph(w.airport.icao)
    ft = GT._get(w)
    msgs = surface.verify_separation(ft.tracks, runway_events=ft.reservations.runway_events, g=g)
    assert [m for m in msgs if "protected zone" in m] == []
    assert not any("crossing gap" in r for _, r in ft.failures)


def test_cow_table_reads_through_and_copies_on_write():
    from backend.wave.ground_traffic import _CowTable
    base = {1: [(0.0, 1.0, "A")]}
    v = _CowTable(base)
    assert 1 in v and v[1] == base[1] and v.get(2) is None and 2 not in v
    v.setdefault(1, []).append((2.0, 3.0, "B"))
    v.setdefault(2, []).append((4.0, 5.0, "C"))
    assert base == {1: [(0.0, 1.0, "A")]} and v[1][-1][2] == "B" and v[2] == [(4.0, 5.0, "C")]


# --------------------------------------------------------------- Task 31 carries (holding-point queue, mixed mode)

def test_takeoff_kept_clear_of_landings_on_its_strip():
    """Mixed mode (MIA): a takeoff stays LANDING_TAKEOFF_GAP_MIN (1 min) from every landing on its strip."""
    from backend.wave.ground_traffic import LANDING_TAKEOFF_GAP_MIN, _takeoff_time
    assert LANDING_TAKEOFF_GAP_MIN == 1.0
    ev = [(10.5, "landing", "UA1"), (12.0, "takeoff", "AA2")]
    t = _takeoff_time(ev, [], 10.0)
    assert t == pytest.approx(13.0)                      # 11.5 clears the landing, then 1 min after AA2's takeoff
    assert _takeoff_time([(20.0, "landing", "UA1")], [], 10.0) == pytest.approx(10.0)
    assert _takeoff_time([(9.2, "landing", "UA1")], [], 10.0) == pytest.approx(10.2)


def test_takeoffs_clear_of_landings_on_every_strip():
    w = build.build_wave("F2015-75")
    res = reservations(w)
    for strip, lst in res.runway_events.items():
        lands = [t for t, k, _ in lst if k == "landing"]
        for t, k, _ in lst:
            if k == "takeoff":
                assert all(abs(t - x) >= 1.0 - 1e-6 for x in lands), strip


def test_holding_node_reserved_until_line_up_and_queue_forms_upstream():
    """Carry T30: a departure keeps its holding-point node until it lines up (30 s before takeoff), so the queue
    behind it waits upstream on the taxiway (visible to the wave flights), and the hold lengths are reported."""
    from backend.wave import ground_traffic as GT
    w = build.build_wave("F2015-270287")
    ft = GT._get(w)
    to = {e[2]: e[0] for lst in ft.reservations.runway_events.values() for e in lst if e[1] == "takeoff"}
    held = 0
    for tr in ft.tracks:
        if tr.kind != "departure":
            continue
        last = tr.points[-1]["node"]
        mine = [b for b in ft.reservations.node_busy[last] if b[2] == tr.callsign]
        assert len(mine) == 1 and mine[0][1] == pytest.approx(max(to[tr.callsign] - 0.5, mine[0][1]))
        if tr.waits and tr.waits[-1]["reason"].startswith("hold for takeoff"):
            held += 1
            assert mine[0][1] == pytest.approx(to[tr.callsign] - 0.5)
            assert tr.points[-1]["t_min"] == pytest.approx(to[tr.callsign] - 0.5)
    assert held > 0
    deps = {m.label for m in ft.movers if m.kind == "departure"}
    assert any(w_["for"] in deps for tr in ft.tracks if tr.kind == "departure" for w_ in tr.waits
               if w_["reason"].startswith("give way"))            # a queue upstream, behind another departure
    for strip, lst in ft.reservations.runway_events.items():    # takeoffs still 1 a minute per strip
        tos = sorted(t for t, k, _ in lst if k == "takeoff")
        assert all(b - a >= 1.0 - 1e-6 for a, b in zip(tos, tos[1:])), strip
    hs = GT.hold_stats(ft)
    assert hs["n"] == len([m for m in ft.movers if m.kind == "departure" and m.path is not None])
    pv = next(p for p in w.provenance if p.layer == "ground_traffic")
    assert "holding point" in pv.detail and "p95" in pv.detail


def test_fixed_traffic_is_pinned_on_the_built_wave():
    from backend import datastore
    from backend.wave import ground_traffic as GT
    w = build.build_wave("F2015-270287")
    ft = GT._get(w)
    GT._CACHE.clear()                                   # evicted from the shared cache: the pin still serves it

    class Exploding:
        def __getattr__(self, name):
            raise AssertionError(f"store.{name} called")
    datastore.set_store(Exploding())
    try:
        assert GT._get(w) is ft and GT.reservations(w) is ft.reservations
    finally:
        datastore.set_store(None)
    reservations.cache_clear()                          # bumps the generation: the pin no longer serves
    assert GT._get(w) is not ft


def test_departure_candidates_widen_to_non_crossing_ends_near_the_flow(monkeypatch):
    """Controller ruling T31: ORD's only parallel (22L) saturated at 1 takeoff a minute. Free ends within 60 deg of the
    flow, without tailwind, that cross no arrival strip and no chosen strip also take departures; a crossing end
    does not."""
    from backend.wave import ground_traffic as GT
    from backend.wave.model import RunwayWind
    # arrivals on 26R (east-west strip at lat 0), 27L parallel; 30 crosses the 26R strip, 31 lies clear north of it
    rw = [Runway("08L", 90, 9000, le_lat=0.0, le_lon=0.0, he_lat=0.0, he_lon=0.04),
          Runway("26R", 270, 9000, le_lat=0.0, le_lon=0.04, he_lat=0.0, he_lon=0.0),
          Runway("09R", 90, 9000, le_lat=0.01, le_lon=0.0, he_lat=0.01, he_lon=0.04),
          Runway("27L", 270, 9000, le_lat=0.01, le_lon=0.04, he_lat=0.01, he_lon=0.0),
          Runway("30", 300, 9000, le_lat=-0.01, le_lon=0.03, he_lat=0.01, he_lon=0.01),       # crosses 26R
          Runway("12", 120, 9000, le_lat=0.01, le_lon=0.01, he_lat=-0.01, he_lon=0.03),
          Runway("31", 310, 9000, le_lat=0.02, le_lon=0.03, he_lat=0.04, he_lon=0.01),        # clear of all
          Runway("13", 130, 9000, le_lat=0.04, le_lon=0.01, he_lat=0.02, he_lon=0.03)]
    w = make_wave(queue=[QueueArrival("WN1", "B737", "LARGE", "26R", 2)], airport=replace(make_wave().airport,
                                                                                           runways=rw))
    monkeypatch.setattr(moves, "fcfs_runways", lambda wave: {f.callsign: "26R" for f in wave.flights})
    assert departure_mode(w) == (False, "26R", ["27L", "31"])
    w.runway_wind = [RunwayWind("31", 0.0, 0.0, 3.0, 20.0, 10.0, "x", True)]
    assert departure_mode(w) == (False, "26R", ["27L"])               # a tailwind end is not used


def test_ord_departures_spread_over_non_crossing_runways():
    from backend.wave import ground_traffic as GT
    from backend.wave.rules import strip_of
    w = build.build_wave("F2015-270287")
    mixed, arr_rw, cands = departure_mode(w)
    assert not mixed and len(cands) > 1
    by = {r.id: r for r in w.airport.runways}
    arr = {strip_of(r) for r in moves.fcfs_runways(w).values()} | {strip_of(q.runway) for q in w.queue}
    for i, a in enumerate(cands):
        assert not any(GT._strips_cross(by[a], r) for r in w.airport.runways if strip_of(r.id) in arr)
        assert not any(GT._strips_cross(by[a], by[b]) for b in cands[i + 1:])
    used = {m.runway for m in fixed_traffic(w) if m.kind == "departure"}
    assert len(used) > 1 and used <= set(cands)


@pytest.mark.parametrize("wid", ["F2015-270287", "F2015-69315", "F2015-75"])
def test_no_internal_sentinel_in_served_waits(wid):
    """Review T31: a holding-point queue wait names the mover ahead, never an internal marker (served in /geo)."""
    from backend.wave import wavegeo
    w = build.build_wave(wid)
    labels = {m.label for m in fixed_traffic(w)}
    for t in fixed_tracks(w):
        for x in t.waits:
            assert "\x00" not in x["reason"] and "\x00" not in str(x.get("for"))
            if x.get("for") is not None and x["reason"].startswith("give way"):
                assert x["for"] in labels or x["for"] in {f.callsign for f in w.flights}
    gt = wavegeo.ground_traffic_part(w)
    assert "\x00" not in json.dumps(gt)


def test_queue_retry_exhaustion_keeps_the_mover_and_reports_it(monkeypatch):
    """Review T31: with one retry allowed, an unresolved holding-point order must not crash build_wave: the departure
    is scheduled once without the queue block, keeps its reservations and is reported as a fixed failure."""
    from backend.wave import ground_traffic as GT
    monkeypatch.setattr(GT, "HOLD_QUEUE_MAX_RETRY", 1)
    GT._cache_clear()
    try:
        w = build.build_wave("F2015-270287")
        ft = GT._get(w)
        unresolved = [lab for lab, why in ft.failures if why == "holding-point queue order not resolved"]
        assert unresolved
        drawn = {t.callsign for t in ft.tracks}
        assert set(unresolved) <= drawn
        for lab in unresolved:
            t = next(t for t in ft.tracks if t.callsign == lab)
            hold = t.points[-1]["node"]
            assert any(b[2] == lab for b in ft.reservations.node_busy[hold])
    finally:
        GT._cache_clear()
