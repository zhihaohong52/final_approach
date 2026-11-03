from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from backend.geo import store
from backend.wave import build, moves, surface
from backend.wave.fixtures import make_wave
from backend.wave.ground_traffic import departure_mode, fixed_traffic, fixed_tracks, reservations
from backend.wave.model import QueueArrival, Runway

# Real BTS On-Time 2015 movement rows (US DOT, public domain) for the replay days, cut to the scheduled window the
# waves read: ORD 2015-08-19 (F2015-270287), ATL 2015-03-01 (F2015-69315), DFW 2015-01-01 (F2015-75).
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

    def checked(g, m, res, spans):
        before = copy.deepcopy((res.node_busy, res.edge_dir, spans))
        ev = copy.deepcopy(res.runway_events)
        real(g, m, res, spans)
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
