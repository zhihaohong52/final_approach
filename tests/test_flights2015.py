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
    from backend.datastore import files as _files
    from backend.wave import build
    w = build.build_wave("F2015-270287")
    df = _files._dest_frame("ORD")
    df = df[df["arr_date"] == w.window_start[:10]]
    t0 = int(w.window_start[11:13]) * 60
    for q in w.queue:
        r = df[(df["carrier"] + df["flight_no"].astype(str)) == q.callsign].iloc[0]
        assert q.slot_min == pytest.approx(float(r["wheels_on"]) - t0, abs=0.01)


def test_crew_floor_uses_taxi_in_ref():
    from backend.wave import build, constraints
    from backend.wave.utility import taxi_in_ref
    for wid in ("F2015-270287", "F2015-58926", "F2015-69315", "F2015-75", "F2015-77162"):
        w = build.build_wave(wid)
        turn = float(constraints.airport(w.airport.iata).min_turnaround_min)
        for f in w.flights:   # §6.1: floor = earliest landing + taxi_in_ref + turnaround (was + TYPICAL_TAXI_MIN)
            assert f.crew_shift_end_min >= f.eta_earliest_min + taxi_in_ref(w, f.callsign) + turn - 1e-9


def test_airport_taxi_median_and_generic_fallback():
    from backend.wave import utility as U
    from tests.fixtures import make_wave
    w = make_wave()
    assert all(f.taxi_in_ref_min is None for f in w.flights)
    assert U.taxi_in_ref(w, w.flights[0].callsign) == 6.0          # generic: no real TAXI_IN on the wave
    assert U.GENERIC_TAXI_IN_MIN == 6.0
