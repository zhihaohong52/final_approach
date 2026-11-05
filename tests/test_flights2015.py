import pytest

from backend.wave import flights2015 as F


def test_airport_without_runways_is_skipped_and_unbuildable():
    assert not F._supported("CID")
    assert F.list_rows(airport="CID", limit=5) == []
    with pytest.raises(KeyError, match="unsupported airport"):
        F.build_from_row(310)


def test_supported_airport_builds():
    row = F.list_rows(airport="ATL", limit=1)[0]
    assert row["dest"] == "ATL" and F._supported("ATL")
    assert F.build_from_row(row["row_id"]).id == row["wave_id"]


def test_weather_and_nas_delay_reach_eta_earliest():
    from backend.wave import build
    from backend.wave.graph import negotiate_wave
    w = build.build_wave("F2015-293482")
    f = w.flights[0] if len(w.flights) == 1 else next(x for x in w.flights if x.delays.get("weather"))
    d = f.delays
    assert d["weather"] > 0
    from backend.wave.utility import taxi_in_ref   # §6.1: the scheduled arrival is a gate time; landing = taxi-in earlier
    assert f.eta_earliest_min == pytest.approx(f.sched_min + d["late_aircraft"] + d["carrier"] + d["weather"] + d["nas"]
                                               - taxi_in_ref(w, f.callsign))
    assert negotiate_wave(w, "formulas")["revision"]["delay_vs_schedule_min"] > 0


def test_pilot_does_not_divert_for_gate_conflict():
    from backend.wave import build
    from backend.wave.graph import negotiate_wave
    r = negotiate_wave(build.build_wave("F2015-200000"), "formulas")
    labels = [m["move"]["label"] for x in r["log"] if x["body"]["type"] == "AGENT_TURN"
              for m in x["body"]["moves"]]
    assert not any("diversion" in l for l in labels)


# --------------------------------------------------------------------------- T21: time base (spec §6.1)

def test_eta_earliest_is_wheels_on_for_real_row():
    from backend.datastore import files as _files   # brief wrote F._dest_frame; the frame loader lives in the FileStore
    from backend.wave import build
    w = build.build_wave("F2015-270287")
    f = next(x for x in w.flights if x.callsign == w.focus)
    df = _files._dest_frame(F._locate(270287)["dest"])     # _locate returns the 14-key index row only
    row = df[df["row_id"] == 270287].iloc[0]                # has wheels_on (local minutes, T6)
    t0 = int(w.window_start[11:13]) * 60 + int(w.window_start[14:16])   # window_start is "YYYY-MM-DDTHH:MM:SS" local
    assert f.eta_earliest_min == pytest.approx(row["wheels_on"] - t0, abs=0.01)


def test_taxi_in_ref_is_the_real_taxi_in_of_the_row():
    from backend.datastore import files as _files
    from backend.wave import build
    from backend.wave.utility import taxi_in_ref
    w = build.build_wave("F2015-270287")
    df = _files._dest_frame("ORD")
    row = df[df["row_id"] == 270287].iloc[0]
    assert taxi_in_ref(w, w.focus) == pytest.approx(float(row["taxi_in"]))
    f = w.flight(w.focus)
    assert f.eta_earliest_min == pytest.approx(
        f.sched_min + sum(f.delays[k] for k in ("late_aircraft", "carrier", "weather", "nas")) - taxi_in_ref(w, w.focus))
    flights = next(p for p in f.provenance if p.layer == "flights")
    assert "scheduled times are gate times (BTS); landing = wheels-on" in flights.detail
    assert "TAXI_IN" in flights.detail


def test_queue_lands_at_real_wheels_on():
    """Queue arrivals land at their real wheels-on (clock time, so compared modulo a day: a queue row may come from
    the day before or after the window, and the split's wheels_on of a very late flight reads a day early).
    F2015-7053 (ORD, 15 queued); F2015-270287's landing window has no queue left."""
    from datetime import date
    from backend.datastore import files as _files
    from backend.wave import build
    w = build.build_wave("F2015-7053")
    assert w.queue
    df = _files._dest_frame("ORD")
    d0 = date.fromisoformat(w.window_start[:10])
    t0 = int(w.window_start[11:13]) * 60
    for q in w.queue:
        rows = df[(df["carrier"] + df["flight_no"].astype(str)) == q.callsign]
        rows = rows[rows["arr_date"].map(lambda d: abs((date.fromisoformat(d) - d0).days) <= 1)]
        gaps = [abs((q.slot_min - (float(r["wheels_on"]) - t0) + 720) % 1440 - 720) for _, r in rows.iterrows()]
        assert gaps and min(gaps) == pytest.approx(0.0, abs=0.01), q.callsign


def test_crew_floor_uses_routed_taxi():
    from backend.wave import build, constraints, surface
    for wid in ("F2015-270287", "F2015-58926", "F2015-69315", "F2015-75", "F2015-77162"):
        w = build.build_wave(wid)
        turn = float(constraints.airport(w.airport.iata).min_turnaround_min)
        for f in w.flights:   # §6.1 (v3-taxi): floor = earliest landing + min over open runways of ROT + unimpeded
            t = min(surface.min_unimpeded_taxi(w, f.callsign, r.id) for r in w.airport.runways if r.open)
            assert f.crew_shift_end_min >= f.eta_earliest_min + t + turn - 1e-9   # taxi + turnaround (T21: taxi_in_ref)


def test_airport_taxi_median_and_generic_fallback():
    from backend.wave import utility as U
    from tests.fixtures import make_wave
    w = make_wave()
    assert all(f.taxi_in_ref_min is None for f in w.flights)
    assert U.taxi_in_ref(w, w.flights[0].callsign) == 6.0          # generic: no real TAXI_IN on the wave
    assert U.GENERIC_TAXI_IN_MIN == 6.0


def test_revision_delay_vs_schedule_is_in_block_delay():
    from backend.wave import build
    from backend.wave.graph import revision
    from backend.wave.moves import fcfs_plan
    from backend.wave.utility import metrics_map
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    r = revision(w, p, w.focus)
    m = metrics_map(w, p)[w.focus]   # §6.1: delay vs the scheduled gate time, measured at in-block
    assert r["delay_vs_schedule_min"] == m.delay_min
    assert r["delay_vs_schedule_min"] == pytest.approx(m.in_block_min - w.flight(w.focus).sched_min, abs=0.05)


def test_queue_slot_survives_wheels_on_day_wrap():
    """split_flights2015 wraps wheels_on only when it is < sched - 720, so an arrival ~12 h late reads a day
    early (UA1121 on F2015-37071 had slot T-696). The slot is sched + arr_delay - taxi_in (exact by BTS definition).
    AA165 (ORD, 20 May 20:36 sched, 1244 min late, TAXI_IN 11) lands 21 May 17:09: the split's wheels_on reads 1029
    (17:09 on 20 May, a day early); on F2015-148025 (21 May 16:00) it is a queue arrival at T+69."""
    from backend.datastore import files as _files
    from backend.wave import build
    w = build.build_wave("F2015-148025")
    assert w.window_start == "2015-05-21T16:00:00"
    q = next(x for x in w.queue if x.callsign == "AA165")
    assert q.slot_min == pytest.approx(69.0, abs=1.0)
    df = _files._dest_frame("ORD")
    r = df[(df["arr_date"] == "2015-05-20") & (df["carrier"] == "AA") & (df["flight_no"] == 165)].iloc[0]
    assert float(r["wheels_on"]) == pytest.approx(1029.0)       # the split's day-early reading the slot must not use


def test_pilot_view_keeps_fuel_and_connections_with_time_base_labels():
    from backend.wave import build
    from backend.wave.graph import _view
    from backend.wave.moves import fcfs_plan
    w = build.build_wave("F2015-270287")
    v = _view(w, fcfs_plan(w), f"PILOT:{w.focus}")
    f = w.flight(w.focus)
    assert v["fuel"] == f.fuel and v["connections"] == f.connections[:5]
    assert v["earliest_touchdown"] == f.eta_earliest_min and v["sched_in_block"] == f.sched_min   # §6.1 labels
    assert "eta_earliest" not in v and "sched" not in v
