import math
import pytest
from atm.wave import build, route, trajectory
from atm.wave.moves import fcfs_plan, fcfs_runways
from atm.wave.fixtures import make_wave

ORD = "F2015-270287"


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
    import glob, json
    from atm.wave.edit import plan_from_dicts
    for f in sorted(glob.glob("data/replay/*.json")):
        d = json.load(open(f)); w = build.build_wave(d["result"]["wave_id"])
        for p in (fcfs_plan(w), plan_from_dicts(d["result"]["final_plan"]["assignments"])):
            for t in trajectory.build(w, p).air:
                assert all(_nm_from_arp(w, q) <= 150 for q in t.points)     # helper: great-circle NM to the airport reference point
                assert all(b["t_min"] >= a["t_min"] for a, b in zip(t.points, t.points[1:]))

def test_queue_traffic_drawn_with_zero_laps():
    w = build.build_wave("F2015-75")   # DFW, 13 queue arrivals
    ts = trajectory.build(w, fcfs_plan(w))
    assert sum(not x.wave for x in ts.air) == len(w.queue) and all(x.laps == 0 for x in ts.air if not x.wave)

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
