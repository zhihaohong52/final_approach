import math
from pathlib import Path

import pytest
from atm.wave import build, route, trajectory
from atm.wave.moves import fcfs_plan, fcfs_runways
from atm.wave.fixtures import make_wave

ORD = "F2015-270287"
REPLAYS = sorted((Path(__file__).resolve().parents[1] / "data" / "replay").glob("*.json"))


@pytest.fixture(autouse=True)
def _fresh_caches():
    route.routes_for_wave.cache_clear()
    trajectory.build.cache_clear()
    yield
    route.routes_for_wave.cache_clear()
    trajectory.build.cache_clear()


def _nm_from_arp(w, q) -> float:
    """Great-circle NM from a trajectory point to the airport reference point."""
    p1, p2 = math.radians(q["lat"]), math.radians(w.airport.lat)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(w.airport.lon - q["lon"]) / 2) ** 2)
    return 2 * 3440.065 * math.asin(min(1.0, math.sqrt(a)))


def test_fcfs_runways_matches_v2_fcfs():
    w = build.build_wave(ORD)
    assert fcfs_runways(w) == {a.callsign: a.runway for a in fcfs_plan(w).assignments}

def test_touchdown_equals_slot_and_hold_identity():
    w = build.build_wave(ORD)
    p = fcfs_plan(w)
    a = p.assignments[0]
    late = p.with_assignment(type(a)(**{**a.__dict__, "slot_min": a.slot_min + 12.0}))
    t = next(x for x in trajectory.build(w, late).air if x.callsign == a.callsign)
    D = a.slot_min + 12.0 - trajectory.earliest(w, a.callsign, a.runway)   # FCFS slot may already sit behind queue traffic
    assert t.points[-1]["t_min"] == pytest.approx(a.slot_min + 12.0, abs=1e-6)
    assert t.laps == math.floor(D / t.lap_min) and 0 <= t.vector_min < t.lap_min
    assert t.laps * t.lap_min + t.vector_min == pytest.approx(D, abs=1e-6)
    assert all(q["t_min"] >= p_["t_min"] for p_, q in zip(t.points, t.points[1:]))
    assert all(q["src"] in ("cifp", "synth") for q in t.points)

def test_vector_only_flight_not_in_stack():
    w = build.build_wave(ORD)
    p = fcfs_plan(w); a = p.assignments[0]
    lap = trajectory.build(w, p).air[0].lap_min or 5.0
    slot = trajectory.earliest(w, a.callsign, a.runway) + 0.5 * lap
    t = next(x for x in trajectory.build(w, p.with_assignment(type(a)(**{**a.__dict__, "slot_min": slot}))).air
             if x.callsign == a.callsign)
    assert t.laps == 0 and t.vector_min > 0 and t.hold_level_ft is None

def test_points_within_150nm_and_replay_plans_invariant():
    import json
    from atm.wave.edit import plan_from_dicts
    assert REPLAYS
    for f in REPLAYS:
        d = json.loads(f.read_text()); w = build.build_wave(d["result"]["wave_id"])
        for p in (fcfs_plan(w), plan_from_dicts(d["result"]["final_plan"]["assignments"])):
            for t in trajectory.build(w, p).air:
                assert all(_nm_from_arp(w, q) <= 150 for q in t.points)     # helper: great-circle NM to the airport reference point
                assert all(b["t_min"] >= a["t_min"] for a, b in zip(t.points, t.points[1:]))

def test_queue_traffic_drawn_with_zero_laps():
    w = build.build_wave("F2015-75")   # DFW, 13 queue arrivals
    ts = trajectory.build(w, fcfs_plan(w))
    assert sum(not x.wave for x in ts.air) == len(w.queue) and all(x.laps == 0 for x in ts.air if not x.wave)
    slots = {q.callsign: q.slot_min for q in w.queue}
    drawn = [x for x in ts.air if not x.wave and x.points]
    assert drawn and all(x.points[-1]["t_min"] == pytest.approx(slots[x.callsign], abs=1e-6) for x in drawn)

def test_earliest_no_geometry_tier_is_v2():
    w = make_wave()
    f = w.flights[0]
    assert trajectory.earliest(w, f.callsign, "27L", 3, 0) == f.eta_earliest_min + 3

def test_far_entry_clipped_at_150nm_with_full_route_timing():
    w = build.build_wave("F2015-58926")   # MIA: FROGZ5.HONID starts ~410 NM out
    p = fcfs_plan(w)
    ts = trajectory.build(w, p)
    clipped = [t for t in ts.air if t.wave and t.entry_fix_min is not None and t.appears_min > t.entry_fix_min]
    assert clipped
    for t in clipped:
        b = t.points[0]
        assert b["kind"] == "entry" and b["src"] == "synth" and "fix" not in t.to_dict()["points"][0]
        assert abs(_nm_from_arp(w, b) - 150) <= 0.5
        assert t.appears_min == b["t_min"] > trajectory.entry_time(w, t.callsign)
        assert t.entry_fix_min == pytest.approx(trajectory.entry_time(w, t.callsign), abs=1e-6)
        assert t.points[-1]["t_min"] == pytest.approx(p.get(t.callsign).slot_min, abs=1e-6)
        assert all(_nm_from_arp(w, q) <= 150 for q in t.points)


def _reslot(p, a, **kw):
    return p.with_assignment(type(a)(**{**a.__dict__, **kw}))


def test_exact_lap_multiple_gives_whole_laps():
    w = build.build_wave(ORD)
    p = fcfs_plan(w); a = p.assignments[0]
    e = trajectory.earliest(w, a.callsign, a.runway)
    lap = next(x for x in trajectory.build(w, p).air if x.callsign == a.callsign).lap_min
    for k in (1, 2, 3, 4):
        t = next(x for x in trajectory.build(w, _reslot(p, a, slot_min=e + k * lap)).air if x.callsign == a.callsign)
        assert t.laps == k and t.vector_min == pytest.approx(0.0, abs=1e-6)
        assert t.hold is not None and t.hold["laps"] == k and isinstance(t.hold["level_ft"], int)


def test_slot_before_earliest_draws_no_hold_and_lands_at_slot():
    w = build.build_wave(ORD)
    p = fcfs_plan(w); a = p.assignments[0]
    slot = trajectory.earliest(w, a.callsign, a.runway) - 5.0
    t = next(x for x in trajectory.build(w, _reslot(p, a, slot_min=slot)).air if x.callsign == a.callsign)
    assert t.laps == 0 and t.vector_min == 0 and t.hold is None
    assert t.points[-1]["t_min"] == pytest.approx(slot, abs=1e-6)
    assert all(q["t_min"] >= p_["t_min"] for p_, q in zip(t.points, t.points[1:]))


def test_stack_shares_one_hold_per_physical_fix_with_highest_base():
    from atm.wave import holding
    w = build.build_wave(ORD)
    p = fcfs_plan(w)
    pl = p
    for i, a in enumerate(p.assignments):   # everyone to one runway, holding together
        rw = p.assignments[0].runway
        pl = _reslot(pl, a, runway=rw, slot_min=trajectory.earliest(w, a.callsign, rw) + 30 + i * 0.1)
    ts = trajectory.build(w, pl)
    routes = route.routes_for_wave(w)
    holders = [t for t in ts.air if t.wave and t.laps >= 1]
    assert holders
    by_fix = {}
    for t in holders:
        by_fix.setdefault(t.hold_fix, []).append(t)
    for fix, ts_ in by_fix.items():
        bases = [routes[(t.callsign, t.runway)].hold.base_ft for t in ts_]
        assert all(t.hold["level_ft"] >= max(bases) for t in ts_)
        for x in ts_:   # holders at the fix at the same time are on different levels
            for y in ts_:
                if x is not y and x.hold["entry_min"] <= y.hold["entry_min"] < x.hold["exit_min"]:
                    assert x.hold["level_ft"] != y.hold["level_ft"]
        polys = {tuple((round(q["lat"], 6), round(q["lon"], 6)) for q in t.points if q["kind"] == "hold")[:24]
                 for t in ts_}
        assert len(polys) == 1   # one shared racetrack (first lap) per stack
    assert all(isinstance(t.to_dict().get("hold", {}).get("level_ft", 0), int) for t in ts.air)


def test_unknown_callsign_raises():
    w = build.build_wave(ORD)
    with pytest.raises(ValueError):
        trajectory.earliest(w, "NOPE1", "22R")
    with pytest.raises(ValueError):
        trajectory.entry_time(w, "NOPE1")


def test_shared_fix_hold_lap_time_is_plan_independent_and_matches_drawing():
    w = build.build_wave(ORD)
    p = fcfs_plan(w)
    pl = p
    for i, a in enumerate(p.assignments):
        rw = p.assignments[0].runway
        pl = _reslot(pl, a, runway=rw, slot_min=trajectory.earliest(w, a.callsign, rw) + 30 + i * 0.1)
    ts = trajectory.build(w, pl)
    by_fix = {}
    for t in ts.air:
        if t.laps >= 1:
            by_fix.setdefault(t.hold_fix, []).append(t)
    pair = next(v for v in by_fix.values() if len(v) >= 2)
    assert len({t.lap_min for t in pair}) == 1
    routes = route.routes_for_wave(w)
    shared = trajectory.hold_for(w, routes[(pair[0].callsign, pair[0].runway)])
    assert pair[0].lap_min == shared.lap_min
    assert shared.base_ft == max(r.hold.base_ft for r in routes.values()
                                 if trajectory._stack_key(r.hold) == trajectory._stack_key(shared))
    for t in pair:
        crossings = [q["t_min"] for q in t.points if q["kind"] == "hold" and q.get("fix") == t.hold_fix]
        assert len(crossings) == t.laps
        laps_t = [b - a for a, b in zip([t.hold["entry_min"], *crossings], crossings)]
        assert all(x == pytest.approx(t.lap_min, abs=1e-9) for x in laps_t)
    # a flight's lap time does not depend on who else holds: hold one flight alone
    a = pl.get(pair[0].callsign)
    solo = _reslot(p, p.get(a.callsign), runway=a.runway, slot_min=a.slot_min)
    t1 = next(x for x in trajectory.build(w, solo).air if x.callsign == a.callsign)
    assert t1.lap_min == pair[0].lap_min
