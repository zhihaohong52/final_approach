from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from backend.geo import store
from backend.wave import build, moves, surface
from backend.wave.fixtures import make_wave
from backend.wave.ground_traffic import departure_mode, fixed_traffic, fixed_tracks, reservations
from backend.wave.model import QueueArrival, Runway

ORD_FIXTURE = Path("tests/data/movements/ORD-2015-08-19.csv")   # real BTS 2015 ORD rows, sched 01:00-11:39 local


@pytest.fixture(autouse=True)
def _ord_movements(monkeypatch):
    """ORD 2015-08-19 movements from the committed fixture (the real files are gitignored and DB-seeded), so the
    tests run offline and identically with or without data/ref/movements2015."""
    from backend.datastore import files
    real = files._movements
    frame = pd.read_csv(ORD_FIXTURE, dtype=str, keep_default_na=False)
    monkeypatch.setattr(files, "_movements", lambda iata: (frame, "date") if iata == "ORD" else real(iata))
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
    for m in fixed_traffic(w):
        if m.kind == "departure":
            strip, t = ev[(m.callsign, "takeoff")]
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
    assert "departures" in pv.detail and "arrivals" in pv.detail and "not scheduled" in pv.detail


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
