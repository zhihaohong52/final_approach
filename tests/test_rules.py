from dataclasses import replace

import pytest

from backend.wave import build, route, rules, trajectory
from backend.wave.model import Assignment, JointPlan, QueueArrival, agent_ids
from backend.wave.moves import fcfs_plan
from backend.wave.utility import evaluate
from tests.fixtures import make_wave
from tests.test_trajectory import _far_runway


def _by(checks, rule, cs):
    return next(c for c in checks if c.rule == rule and c.callsign == cs)


def _plan(w, **over):
    p = fcfs_plan(w)
    for cs, kw in over.items():
        p = p.with_assignment(replace(p.get(cs), **kw))
    return p


def test_wake_violation_detected():
    w = make_wave(queue=[])
    # DL100 (HEAVY) at T+5, DL201 (LARGE) 1 min behind on the same runway: need 2.15 min
    p = _plan(w, DL100=dict(runway="27L", slot_min=5.0), DL201=dict(runway="27L", slot_min=6.0),
              AA302=dict(runway="26R", slot_min=8.0), UA403=dict(runway="26R", slot_min=12.0))
    c = _by(rules.check(w, p), "ATC-WAKE-SEP", "DL201")
    assert not c.passed
    assert c.evidence["leader"] == "DL100" and c.evidence["required_behind_min"] == 2.15  # Heavy->Large 5 nm = 129 s
    assert c.source.startswith("challenge2_operational_constraints.xlsx")
    assert _by(rules.check(w, p), "ATC-WAKE-SEP", "DL100").passed
    assert not rules.feasible(w, p)


def test_wake_violation_against_queue_follower():
    w = make_wave()   # F99 (queue) lands 26R at T+10
    p = _plan(w, AA302=dict(runway="26R", slot_min=9.5))
    c = _by(rules.check(w, p), "ATC-WAKE-SEP", "AA302")
    assert not c.passed and c.evidence["follower"] == "F99"


def test_wake_s_arrival_values():
    from backend.wave import constraints
    assert constraints.wake_s_arrival("Heavy", "Large") == 129     # 5 nm at 140 kt
    assert constraints.wake_s_arrival("Large", "Large") == 77      # other pair 3 nm
    assert constraints.wake_s_arrival("Super", "Small") == 206     # 8 nm
    assert constraints.wake_s_arrival("SUPER", "HEAVY") == 154     # model strings, 6 nm


def test_same_runway_pair_closer_than_arrival_spacing_fails():
    w = make_wave(queue=[])
    # DL201 and AA302 both LARGE on 27L: 3 nm = 77 s = 1.28 min. 1.2 min behind fails, 1.3 min passes
    base = dict(DL100=dict(runway="26R", slot_min=5.0), UA403=dict(runway="26R", slot_min=12.0),
                DL201=dict(runway="27L", slot_min=6.0))
    p = _plan(w, **base, AA302=dict(runway="27L", slot_min=7.2))
    c = _by(rules.check(w, p), "ATC-WAKE-SEP", "AA302")
    assert not c.passed and c.evidence["required_behind_min"] == 1.28
    p = _plan(w, **base, AA302=dict(runway="27L", slot_min=7.3))
    assert _by(rules.check(w, p), "ATC-WAKE-SEP", "AA302").passed


def test_wet_weather_factor():
    w = make_wave(queue=[])
    w = replace(w, metar=replace(w.metar, surface="wet"))
    # 2 min behind a HEAVY is legal dry (workbook 120 s) but needs 2.5 wet
    p = _plan(w, DL100=dict(runway="27L", slot_min=5.0), DL201=dict(runway="27L", slot_min=7.0))
    assert not _by(rules.check(w, p), "ATC-WAKE-SEP", "DL201").passed


def test_gate_overlap_detected():
    w = make_wave()
    p = fcfs_plan(w)
    p = p.with_assignment(replace(p.get("DL201"), gate="A2"))   # AA302 also at A2, minutes apart
    viol = rules.violations(w, p)
    gf = [c for c in viol if c.rule == "GND-GATE-FREE"]
    assert {c.callsign for c in gf} == {"DL201", "AA302"}
    # busy window from data (B2 occupied T+0..T+40 by DL55)
    p2 = fcfs_plan(w)
    p2 = p2.with_assignment(replace(p2.get("UA403"), gate="B2"))
    c = _by(rules.check(w, p2), "GND-GATE-FREE", "UA403")
    assert not c.passed and "DL55" in c.detail
    # remote stand has 2 positions: two overlapping aircraft are fine
    p3 = fcfs_plan(w)
    for cs in ("DL201", "AA302"):
        p3 = p3.with_assignment(replace(p3.get(cs), gate="RS"))
    assert all(_by(rules.check(w, p3), "GND-GATE-FREE", cs).passed for cs in ("DL201", "AA302"))


def test_remote_stand_two_positions():
    w = make_wave()
    # all four fixture flights are in block within one 45 min turnaround of each other
    p = fcfs_plan(w)
    for cs in ("DL100", "DL201", "AA302"):
        p = p.with_assignment(replace(p.get(cs), gate="RS"))
    c = _by(rules.check(w, p), "GND-GATE-FREE", "AA302")   # latest in-block of the three
    assert not c.passed and "full" in c.detail and "DL100" in c.detail and "DL201" in c.detail
    assert _by(rules.check(w, p), "GND-GATE-FREE", "DL100").passed
    assert _by(rules.check(w, p), "GND-GATE-FREE", "DL201").passed
    assert not rules.feasible(w, p)
    # two on RS: both pass
    p2 = fcfs_plan(w)
    for cs in ("DL100", "DL201"):
        p2 = p2.with_assignment(replace(p2.get(cs), gate="RS"))
    assert _by(rules.check(w, p2), "GND-GATE-FREE", "DL100").passed
    assert _by(rules.check(w, p2), "GND-GATE-FREE", "DL201").passed


def test_gate_size():
    w = make_wave()
    p = _plan(w, DL100=dict(gate="B1"))   # B772 wide-body into a narrow gate
    c = _by(rules.check(w, p), "GND-GATE-SIZE", "DL100")
    assert not c.passed


def test_wind_rejects_wet_15kt():
    w = make_wave()
    metar = replace(w.metar, raw="KATL 151551Z 18418KT 3SM RA BKN010 24/22 A2992", wind_dir_deg=184,
                    wind_kt=18, gust_kt=None, visibility_sm=3, wx_codes=["RA"], surface="wet")
    w = replace(w, metar=metar)
    p = _plan(w, DL201=dict(runway="26R"))
    c = _by(rules.check(w, p), "ATC-WIND-LIMITS", "DL201")
    assert not c.passed
    assert c.evidence["metar"] == metar.raw
    assert c.evidence["limit_crosswind_kt"] == 15
    assert c.evidence["crosswind_kt"] > 15
    assert "8400.9" in c.source


def test_all_runways_out_of_limits():
    w = make_wave()
    metar = replace(w.metar, raw="KATL 151551Z 36040G55KT 2SM +TSRA OVC008 22/21 A2970", wind_dir_deg=360,
                    wind_kt=40, gust_kt=55, visibility_sm=2, wx_codes=["+TSRA"], surface="wet")
    w = replace(w, metar=metar)
    for r in w.airport.runways:
        p = JointPlan([Assignment(f.callsign, "land", r.id, f.eta_earliest_min + 4 * i, f.scheduled_gate)
                       for i, f in enumerate(w.flights)])
        checks = rules.check(w, p)
        wl = [c for c in checks if c.rule == "ATC-WIND-LIMITS"]
        assert len(wl) == len(w.flights) and not any(c.passed for c in wl)
        assert not rules.feasible(w, p)
    ev = evaluate(w, fcfs_plan(w))   # must not crash
    assert ev["feasible"] is False


def test_fuel_and_eta_rules():
    w = make_wave()
    p = _plan(w, AA302=dict(slot_min=30.0))   # 22 min hold vs 16 min extra fuel
    c = _by(rules.check(w, p), "PLT-FUEL-RESERVE", "AA302")
    assert not c.passed and c.evidence["extra_min"] == 16 and "121.639" in c.source
    p = _plan(w, DL201=dict(slot_min=3.0))
    assert not _by(rules.check(w, p), "PLT-EARLIEST-ETA", "DL201").passed
    # speed reduction can't be claimed without the slot moving
    p = _plan(w, DL100=dict(speed_reduction_min=8.0))
    assert not _by(rules.check(w, p), "PLT-EARLIEST-ETA", "DL100").passed


def test_divert_skips_landing_rules():
    w = make_wave()
    p = _plan(w, DL100=dict(action="divert", runway=None, slot_min=None, gate=None))
    cs = [c for c in rules.check(w, p) if c.callsign == "DL100"]
    assert [c.rule for c in cs] == ["PLT-FUEL-RESERVE"] and cs[0].passed


def test_evaluate_shape():
    w = make_wave()
    p = fcfs_plan(w)
    ev = evaluate(w, p)
    assert set(ev) == {"plan_id", "feasible", "checks", "utilities", "totals"}
    assert ev["plan_id"] == p.id
    assert set(ev["totals"]) == {"delay_min", "fuel_kg", "hold_min", "pax_protected_pct", "overtime_min", "diversions"}
    from backend.wave.utility import negotiators
    assert list(ev["utilities"]) == negotiators(w)
    assert {"ATC", "GROUND"} <= set(ev["utilities"]) and all(0 <= u <= 1 for u in ev["utilities"].values())
    keys = {"rule", "owner", "passed", "detail", "source", "evidence", "callsign"}
    assert all(set(c) == keys for c in ev["checks"])
    for c in ev["checks"]:
        assert all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in c["evidence"].values()), c
    rules_seen = {c["rule"] for c in ev["checks"]}
    assert rules_seen == {"ATC-RWY-OPEN", "ATC-WAKE-SEP", "ATC-TAXI-ROUTE", "ATC-WIND-LIMITS", "PLT-EARLIEST-ETA",
                          "PLT-FUEL-RESERVE", "GND-GATE-SIZE", "GND-GATE-FREE", "GND-CREW-DUTY",
                          "GND-GATE-NOTICE", "ATC-RWY-CAPACITY", "ATC-HOLD-STACK", "ATC-TAXI-CONFLICT"}
    assert all(a.metrics.in_block_min is not None for a in p.assignments)


def test_closed_runway():
    w = make_wave()
    rw = [replace(r, open=False, closed_reason="NOTAM") if r.id == "26R" else r for r in w.airport.runways]
    w = replace(w, airport=replace(w.airport, runways=rw))
    p = _plan(w, DL100=dict(runway="26R"))
    c = _by(rules.check(w, p), "ATC-RWY-OPEN", "DL100")
    assert not c.passed and "NOTAM" in c.detail
    assert all(a.runway != "26R" for a in fcfs_plan(w).assignments)


def test_strip_of():
    assert rules.strip_of("08L") == ("08L", "26R") == rules.strip_of("26R")
    assert rules.strip_of("09C") == ("09C", "27C")
    assert rules.strip_of("36") == ("18", "36")
    assert rules.strip_of("01R") == ("01R", "19L")


def test_opposite_ends_rejected():
    # review case: no METAR, wave lands 08L while the queue lands 26R on the same strip
    w = make_wave(metar=None)
    p = JointPlan([Assignment("DL100", "land", "08L", 5.0, "A5"), Assignment("DL201", "land", "08L", 8.0, "A1"),
                   Assignment("AA302", "land", "08L", 10.0, "A2"), Assignment("UA403", "land", "08L", 22.0, "B1")])
    viol = rules.violations(w, p)
    rw = [c for c in viol if c.rule == "ATC-RWY-OPEN"]
    assert {c.callsign for c in rw} == {"DL100", "DL201", "AA302", "UA403"}
    assert "opposite-direction use of strip 08L/26R (queue landing" in rw[0].detail
    # wake across ends of the same strip: AA302 at 08L T+10 is simultaneous with queue F99 26R T+10
    assert not _by(rules.check(w, p), "ATC-WAKE-SEP", "AA302").passed
    # SUPER cross-end case: queue SUPER on 27L, wave flight 2 min later on 09R
    q = [QueueArrival("Q1", "A388", "SUPER", "27L", 5.0)]
    w2 = make_wave(queue=q, metar=None)
    p2 = _plan(w2, DL100=dict(runway="09R", slot_min=7.0), DL201=dict(runway="26R", slot_min=6.0),
               AA302=dict(runway="26R", slot_min=8.0), UA403=dict(runway="26R", slot_min=12.0))
    cs = rules.check(w2, p2)
    assert not _by(cs, "ATC-RWY-OPEN", "DL100").passed
    wk = _by(cs, "ATC-WAKE-SEP", "DL100")
    assert not wk.passed and wk.evidence["leader"] == "Q1" and wk.evidence["required_behind_min"] == 2.57  # Super->Heavy 6 nm = 154 s


def test_divert_needs_alternate():
    w = make_wave()
    for fuel in ({"extra_min": 30}, {"extra_min": 30, "alternate": None, "alternate_min": None,
                                     "final_reserve_min": None},
                 {"extra_min": 30, "alternate": "BHM", "alternate_min": 0}):
        wf = make_wave(flights=[replace(w.flights[0], fuel=fuel)] + w.flights[1:])
        p = _plan(wf, DL100=dict(action="divert", runway=None, slot_min=None, gate=None))
        c = _by(rules.check(wf, p), "PLT-FUEL-RESERVE", "DL100")
        assert not c.passed and "121.639" in c.source, fuel
        assert rules.feasible(wf, fcfs_plan(wf)) in (True, False)   # no crash with None fuel values
        evaluate(wf, p)


def test_plan_structure_enforced():
    w = make_wave()
    p = fcfs_plan(w)
    bad = [JointPlan(p.assignments[:1]), JointPlan([]),
           JointPlan(p.assignments + [replace(p.get("DL100"), runway="09R", gate="RS")]),
           JointPlan(p.assignments + [Assignment("XX1", "land", "26R", 30.0, "RS")])]
    for b in bad:
        with pytest.raises(ValueError, match="must match wave flights"):
            rules.check(w, b)
        assert rules.feasible(w, b) is False
        ev = evaluate(w, b)
        assert ev["feasible"] is False and ev["checks"] == []
        assert set(ev) == {"plan_id", "feasible", "checks", "utilities", "totals"}


def test_fuel_uses_unrounded_hold():
    w = make_wave()   # AA302: ETA T+8, extra fuel 16 min
    p = _plan(w, AA302=dict(slot_min=24.04))
    assert not _by(rules.check(w, p), "PLT-FUEL-RESERVE", "AA302").passed
    p = _plan(w, AA302=dict(slot_min=24.0))
    assert _by(rules.check(w, p), "PLT-FUEL-RESERVE", "AA302").passed


def test_nan_slot_rejected_cleanly():
    import json
    w = make_wave()
    p = _plan(w, DL201=dict(slot_min=float("nan")))
    assert not _by(rules.check(w, p), "PLT-EARLIEST-ETA", "DL201").passed
    assert not _by(rules.check(w, p), "ATC-WAKE-SEP", "DL201").passed
    json.dumps(evaluate(w, p), allow_nan=False)


def test_nan_ground_delay_rejected():
    import json
    w = make_wave()
    p = _plan(w, DL100=dict(ground_delay_min=float("nan")))
    assert not _by(rules.check(w, p), "PLT-EARLIEST-ETA", "DL100").passed
    assert not rules.feasible(w, p)
    json.dumps(evaluate(w, p), allow_nan=False)


def test_nonfinite_minutes_rejected():
    from backend.wave.model import Move
    from backend.wave.moves import apply as apply_move
    w = make_wave()
    p = fcfs_plan(w)
    for m in (float("inf"), float("nan")):
        mv = Move(id="x", agent="PILOT:UA403", kind="ground_delay", callsigns=["UA403"],
                  params={"minutes": m}, label="x")
        with pytest.raises(ValueError, match="minutes must be a finite number"):
            apply_move(w, p, mv)
    mv = Move(id="x", agent="ATC", kind="shift_slot", callsigns=["DL201"],
              params={"minutes": float("nan")}, label="x")
    with pytest.raises(ValueError, match="minutes must be a finite number"):
        apply_move(w, p, mv)


def test_assignment_dict_omits_none():
    w = make_wave()
    p = _plan(w, DL100=dict(action="divert", runway=None, slot_min=None, gate=None))
    d = p.to_dict()["assignments"][0]
    assert d["action"] == "divert" and not {"runway", "slot_min", "gate"} & set(d)
    assert {"runway", "slot_min", "gate"} <= set(p.to_dict()["assignments"][1])


def test_divert_delay_includes_existing_delay():
    """A diverting flight keeps the delay it already has (late inbound), plus alternate + 90."""
    from dataclasses import replace as _r
    from backend.wave.model import Assignment, FlightMetrics
    from backend.wave.model import JointPlan
    from backend.wave.moves import fcfs_plan
    from backend.wave.utility import DIVERT_EXTRA_DELAY_MIN, metrics_map, taxi_in_ref
    from tests.fixtures import make_wave
    w = make_wave()
    f = w.flights[0]
    late = _r(f, eta_earliest_min=f.sched_min + 200)
    w2 = _r(w, flights=[late] + list(w.flights[1:]))
    others = [a for a in fcfs_plan(w2).assignments if a.callsign != f.callsign]
    divert = Assignment(f.callsign, "divert", None, None, None, 0.0, 0.0, FlightMetrics())
    m = metrics_map(w2, JointPlan([divert, *others]))[f.callsign]
    # §6.1: the existing delay is measured at in-block (earliest landing + taxi-in reference vs the gate time)
    assert m.delay_min == pytest.approx(200 + taxi_in_ref(w2, f.callsign) + float(f.fuel["alternate_min"])
                                        + DIVERT_EXTRA_DELAY_MIN)


def test_limits_entries_present():
    from backend.wave import rules
    names = {l["limit"]: l for l in rules.LIMITS}
    assert names["remote stand"]["value"] == "1 stand, 2 positions"
    assert "speed reduction cap" in names
    assert "departures not modelled" in names["runway capacity"]["note"]


# --------------------------------------------------------------- airborne model (T14)

ORD = "F2015-270287"


def _shared_hold_pair(w, p):
    """(A, B, runway): two wave flights whose routes on the same runway (A's FCFS runway) hold at the same
    physical fix, A with the earlier ETA; None if there is no such pair."""
    rs = route.routes_for_wave(w)
    by_key: dict = {}
    for a in p.assignments:
        r = rs.get((a.callsign, a.runway))
        if r is not None:
            by_key.setdefault((a.runway, trajectory.stack_key(r.hold)), []).append(a.callsign)
    for (rwy, _), css in sorted(by_key.items()):
        if len(css) >= 2:
            a_cs, b_cs = sorted(css[:2], key=lambda c: w.flight(c).eta_earliest_min)
            return a_cs, b_cs, rwy
    return None


def _clear_caches():
    route.routes_for_wave.cache_clear()
    trajectory.build.cache_clear()


@pytest.fixture
def two_holders_reversed(monkeypatch):
    """ORD wave, two flights at one hold fix: A enters first and flies 2 laps (slot earliest + 2.2 laps),
    B enters 0.3 lap later and flies 1 lap (slot earliest + 1.2 laps), so B leaves the stack before A
    (not bottom-first). B's ETA is moved so that it reaches the fix 0.3 lap after A. B is
    plan.assignments[1]."""
    _clear_caches()
    w = build.build_wave(ORD)
    pair = _shared_hold_pair(w, fcfs_plan(w))
    if pair is None:
        # no two flights share a hold fix: route every flight with the first origin's STAR
        orig, seen = route.select_star, []

        def same_star(proc, origin_latlon, runway):
            if origin_latlon is not None and not seen:
                seen.append(origin_latlon)
            return orig(proc, seen[0] if seen else origin_latlon, runway)
        monkeypatch.setattr(route, "select_star", same_star)
        _clear_caches()
        pair = _shared_hold_pair(w, fcfs_plan(w))
    assert pair is not None
    a_cs, b_cs, rwy = pair

    def plan_for(wv):
        rs = route.routes_for_wave(wv)
        lap = trajectory.hold_for(wv, rs[(a_cs, rwy)]).lap_min
        p = fcfs_plan(wv)
        a = replace(p.get(a_cs), runway=rwy, slot_min=trajectory.earliest(wv, a_cs, rwy) + 2.2 * lap,
                    speed_reduction_min=0.0, ground_delay_min=0.0)
        b = replace(p.get(b_cs), runway=rwy, slot_min=trajectory.earliest(wv, b_cs, rwy) + 1.2 * lap,
                    speed_reduction_min=0.0, ground_delay_min=0.0)
        plan = JointPlan([a, b] + [x for x in p.assignments if x.callsign not in (a_cs, b_cs)], p.label)
        holds = {t.callsign: t.hold for t in trajectory.build(wv, plan).air}
        return plan, lap, holds[a_cs], holds[b_cs]

    for _ in range(4):   # move B's ETA until it reaches the fix 0.3 lap after A (entry is linear in ETA)
        plan, lap, ha, hb = plan_for(w)
        gap = hb["entry_min"] - ha["entry_min"]
        if abs(gap - 0.3 * lap) < 1e-6:
            break
        f = w.flight(b_cs)
        nf = replace(f, eta_earliest_min=f.eta_earliest_min + 0.3 * lap - gap)
        w = replace(w, flights=[nf if x is f else x for x in w.flights])
    assert ha["laps"] == 2 and hb["laps"] == 1
    assert ha["entry_min"] < hb["entry_min"] and hb["exit_min"] < ha["exit_min"]
    yield w, plan
    _clear_caches()


def test_hold_stack_fifo_violation_cited(two_holders_reversed):
    w, p = two_holders_reversed          # same hold fix; B enters after A but leaves first; both >= 1 lap
    c = _by(rules.check(w, p), "ATC-HOLD-STACK", p.assignments[1].callsign)
    assert not c.passed and c.source.startswith("FAA JO 7110.65 §4-5-1")
    assert {"hold_fix", "laps", "level_ft", "holders", "levels"} <= set(c.evidence)


def test_hold_stack_earlier_holder_passes(two_holders_reversed):
    w, p = two_holders_reversed
    checks = rules.check(w, p)
    a, b = p.assignments[0].callsign, p.assignments[1].callsign
    ca, cb = _by(checks, "ATC-HOLD-STACK", a), _by(checks, "ATC-HOLD-STACK", b)
    assert ca.passed and ca.evidence["laps"] == 2 and ca.evidence["holders"] == 1
    assert cb.evidence["laps"] == 1 and cb.evidence["holders"] == 2 and cb.evidence["levels"] == 5
    assert cb.evidence["level_ft"] == ca.evidence["level_ft"] + 1000
    assert a in cb.detail and "bottom" in cb.detail
    # the trajectory layer reports the same violation at the same fix
    assert cb.evidence["hold_fix"] in trajectory.build(w, p).stack_violations


def test_hold_stack_no_laps_passes():
    w = build.build_wave(ORD)
    p = fcfs_plan(w)
    checks = rules.check(w, p)
    for a in p.assignments:
        c = _by(checks, "ATC-HOLD-STACK", a.callsign)
        assert c.passed and c.evidence["laps"] == 0 and c.evidence["levels"] == 5


def test_stack_eval_six_holders_and_order():
    # (callsign, entry, exit, level): six overlapping holders at a 12,000 ft base, exits in entry order
    hs = [(f"F{i}", float(i), 100.0 + i, 12000 + 1000 * i) for i in range(6)]
    res = rules.stack_eval(hs, 12000)
    assert all(res[f"F{i}"]["ok"] for i in range(5))
    assert not res["F5"]["ok"] and res["F5"]["holders"] == 6 and "levels" in res["F5"]["why"][0]
    # a later entrant leaving while an earlier one still holds fails; the earlier one does not
    res = rules.stack_eval([("A", 0.0, 20.0, 12000), ("B", 1.0, 10.0, 13000)], 12000)
    assert res["A"]["ok"] and not res["B"]["ok"] and "A" in res["B"]["why"][0]
    # equal exit times are bottom-first in entry order: no violation; a level below the base fails
    res = rules.stack_eval([("A", 0.0, 10.0, 12000), ("B", 1.0, 10.0, 13000)], 12000)
    assert res["A"]["ok"] and res["B"]["ok"]
    assert not rules.stack_eval([("A", 0.0, 10.0, 11000)], 12000)["A"]["ok"]


def test_earliest_eta_cites_route_for_far_runway():
    w = build.build_wave(ORD); p = fcfs_plan(w); a = p.assignments[0]
    far = _far_runway(w, a.callsign)      # from tests.test_trajectory
    c = _by(rules.check(w, p.with_assignment(replace(a, runway=far))), "PLT-EARLIEST-ETA", a.callsign)
    assert not c.passed and "route_min" in c.evidence and far in c.detail
    assert {"entry_fix", "t_entry"} <= set(c.evidence)


def test_earliest_eta_regression_ord_mq3327_far_runway():
    """Recorded ORD agreed plan: MQ3327 on 09L at its nominal (22R) slot T+301. Its 09L route is ~5.6 min
    longer, so it lands before its 09L earliest. Before T14 the rule used ETA+SR+GD and passed it."""
    w = build.build_wave(ORD); p = fcfs_plan(w); a = p.get("MQ3327")
    # §6.1 (v3-time): the nominal slot is now the real wheels-on T+287 (301 - 14 min TAXI_IN), not T+301
    nominal = w.flight("MQ3327").eta_earliest_min
    assert nominal == pytest.approx(287.0)
    q = p.with_assignment(replace(a, runway="09L", slot_min=nominal))
    e = trajectory.earliest(w, "MQ3327", "09L")
    assert e - nominal > 5.0
    c = _by(rules.check(w, q), "PLT-EARLIEST-ETA", "MQ3327")
    assert not c.passed and "09L" in c.detail
    assert c.evidence["earliest_min"] == pytest.approx(e, abs=0.01)
    # metrics agree: no hold is drawn for a slot before the earliest landing
    from backend.wave.utility import metrics_map
    assert metrics_map(w, q)["MQ3327"].hold_min == 0.0
    # at its 09L earliest it passes
    ok = p.with_assignment(replace(a, runway="09L", slot_min=e))
    assert _by(rules.check(w, ok), "PLT-EARLIEST-ETA", "MQ3327").passed


def test_fuel_reserve_counts_route_extra():
    from dataclasses import replace as _r
    w = build.build_wave(ORD); p = fcfs_plan(w); a = p.assignments[0]
    far = _far_runway(w, a.callsign)
    rs = route.routes_for_wave(w)
    extra = rs[(a.callsign, far)].t_route_min - rs[(a.callsign, a.runway)].t_route_min
    f = w.flight(a.callsign)
    w2 = _r(w, flights=[_r(f, fuel={**f.fuel, "extra_min": extra - 0.5}) if x is f else x for x in w.flights])
    q = p.with_assignment(replace(a, runway=far, slot_min=trajectory.earliest(w2, a.callsign, far)))
    c = _by(rules.check(w2, q), "PLT-FUEL-RESERVE", a.callsign)
    assert not c.passed and c.evidence["route_extra_min"] > 0
    assert {"laps", "lap_min", "hold_fix"} <= set(c.evidence)


def test_crew_duty_unavoidable_uses_best_open_runway():
    """'Unavoidable' compares the duty end with the earliest in-block over every open runway, not the
    assigned runway: a flight on a far runway that could have made it on another one fails."""
    from dataclasses import replace as _r
    from backend.wave import surface
    from backend.wave.utility import metrics_map
    w = build.build_wave(ORD); p = fcfs_plan(w); a = p.assignments[0]
    far = _far_runway(w, a.callsign)
    # v3-taxi: earliest in-block = earliest landing + ROT + unimpeded routed taxi to the gate (was + v2 taxi_min)
    best = min(trajectory.earliest(w, a.callsign, r.id) + surface.min_unimpeded_taxi(w, a.callsign, r.id, a.gate)
               for r in w.airport.runways if r.open)
    q = p.with_assignment(replace(a, runway=far, slot_min=trajectory.earliest(w, a.callsign, far)))
    arr = metrics_map(w, q)[a.callsign].in_block_min
    assert arr > best + 1.0
    f = w.flight(a.callsign)
    end = (arr + best) / 2           # between the best in-block and the far-runway one
    w2 = _r(w, flights=[_r(f, crew_duty_end_min=end) if x is f else x for x in w.flights])
    c = _by(rules.check(w2, q), "PLT-CREW-DUTY", a.callsign)
    assert not c.passed and c.evidence["unavoidable_bust"] == "no"
    assert c.evidence["earliest_arrival_min"] == pytest.approx(best, abs=0.05)
    assert c.evidence["earliest_runway"] != far
    w3 = _r(w, flights=[_r(f, crew_duty_end_min=best - 1.0) if x is f else x for x in w.flights])
    c = _by(rules.check(w3, q), "PLT-CREW-DUTY", a.callsign)
    assert c.passed and c.evidence["unavoidable_bust"] == "yes"


def test_limits_list_new_rows():
    rows = {l["limit"]: l for l in rules.LIMITS}
    assert rows["holding pattern geometry"]["source"] == "real data" and rows["holding stack"]["source"] == "assumption"
    assert rows["procedures cycle"]["source"] == "real data"
    assert all(l["source"] in ("workbook", "real data", "assumption") for l in rules.LIMITS)


def test_fourteen_rules_on_ord():
    w = build.build_wave(ORD)
    assert len({c.rule for c in rules.check(w, fcfs_plan(w))}) == 14


def test_earliest_eta_shorter_route_runway_passes_below_eta():
    """Coordinator repro (T13 review): ORD MQ3327 22R -> 22L. The 22L route is shorter, so its earliest
    landing is before the ETA; a slot there passes (the old ETA+SR+GD rule vetoed it)."""
    w = build.build_wave(ORD); p = fcfs_plan(w); a = p.get("MQ3327")
    e = trajectory.earliest(w, "MQ3327", "22L")
    assert e < w.flight("MQ3327").eta_earliest_min - 1.0
    q = p.with_assignment(replace(a, runway="22L", slot_min=e))
    c = _by(rules.check(w, q), "PLT-EARLIEST-ETA", "MQ3327")
    assert c.passed and c.evidence["route_extra_min"] < 0
    late = p.with_assignment(replace(a, runway="22L", slot_min=e - 0.1))
    assert not _by(rules.check(w, late), "PLT-EARLIEST-ETA", "MQ3327").passed


def test_earliest_eta_float_tolerance():
    """A slot rounded by moves._r can sit ~5e-8 min below trajectory.earliest: within EPS_MIN it passes;
    1e-3 min early still fails."""
    w = build.build_wave(ORD); p = fcfs_plan(w); a = p.get("MQ3327")
    e = trajectory.earliest(w, "MQ3327", "09L")
    ok = p.with_assignment(replace(a, runway="09L", slot_min=e - 5e-8))
    assert _by(rules.check(w, ok), "PLT-EARLIEST-ETA", "MQ3327").passed
    assert _by(rules.check(w, ok), "PLT-FUEL-RESERVE", "MQ3327").passed
    bad = p.with_assignment(replace(a, runway="09L", slot_min=e - 1e-3))
    assert not _by(rules.check(w, bad), "PLT-EARLIEST-ETA", "MQ3327").passed


def test_fuel_reserve_float_tolerance():
    from dataclasses import replace as _r
    w = build.build_wave(ORD); p = fcfs_plan(w); a = p.assignments[0]
    far = _far_runway(w, a.callsign)
    e = trajectory.earliest(w, a.callsign, far)
    need = e - w.flight(a.callsign).eta_earliest_min      # route extra, no hold
    f = w.flight(a.callsign)
    for extra, passed in ((need - 5e-8, True), (need - 1e-3, False)):
        w2 = _r(w, flights=[_r(f, fuel={**f.fuel, "extra_min": extra}) if x is f else x for x in w.flights])
        q = p.with_assignment(replace(a, runway=far, slot_min=trajectory.earliest(w2, a.callsign, far)))
        assert _by(rules.check(w2, q), "PLT-FUEL-RESERVE", a.callsign).passed is passed


# --------------------------------------------------------------- ATC-TAXI-CONFLICT (Task 31, spec §6.4-6.6)

import json as _json   # noqa: E402
from pathlib import Path as _Path   # noqa: E402

from backend.wave import surface   # noqa: E402
from backend.wave.surface import verify_separation   # noqa: E402
from backend.wave.utility import metrics_map   # noqa: E402


@pytest.fixture
def _fixture_movements(monkeypatch):
    """Real BTS 2015 ORD/ATL/DFW movements from the committed fixtures (as tests/test_ground_traffic.py), so the
    ATC-TAXI-CONFLICT tests schedule against real departures with or without data/ref/movements2015."""
    import pandas as pd
    from backend.datastore import files
    from backend.geo import store
    from backend.wave import ground_traffic
    from tests.test_ground_traffic import DAYS, FIXTURES
    real = files._movements
    frames = {k: pd.read_csv(FIXTURES / v, dtype=str, keep_default_na=False) for k, v in DAYS.items()}
    monkeypatch.setattr(files, "_movements", lambda iata: (frames[iata], "date") if iata in frames else real(iata))
    real_text = files._movements_text   # the fixture frames replace the file text path for those airports too
    monkeypatch.setattr(files, "_movements_text", lambda iata: None if iata in frames else real_text(iata))
    store.load_movements.cache_clear(); ground_traffic._cache_clear(); trajectory.build.cache_clear()
    yield
    store.load_movements.cache_clear(); ground_traffic._cache_clear(); trajectory.build.cache_clear()


def _bench_ids():
    return _json.loads((_Path(__file__).resolve().parents[1] / "eval" / "benchmark.json").read_text())["wave_ids"]


def _ord_plan_with_crossing():
    """ORD fixture wave (F2015-270287, movements fixture day), FCFS plan, with one wave flight A moved to a runway
    whose taxi route crosses another strip and a second wave flight B landing on that strip 0.5 min after A would
    reach the crossing unimpeded, so A must hold short. A is `assignments[0]`."""
    w = build.build_wave(ORD)
    p = fcfs_plan(w)
    flow = set(moves_fcfs_runways(w).values())
    for a in p.assignments:
        f = w.flight(a.callsign)
        for r in sorted(r.id for r in w.airport.runways if r.open):
            tx = surface.taxi_info(w, r, a.gate, f.gate_size)
            if tx.tier != "real" or tx.path is None or not tx.path.crossings:
                continue
            strip = tx.path.crossings[0][1]
            ends = [e for e in strip.split("/") if any(x.id == e and x.open for x in w.airport.runways)]
            end = next((e for e in ends if e in flow), ends[0] if ends else None)
            if end is None:
                continue
            b = next(x for x in p.assignments if x.callsign != a.callsign)
            c = tx.path.nodes.index(tx.path.crossings[0][0])
            at_crossing = a.slot_min + tx.rot_s / 60.0 + sum(tx.path.seg_s[:c]) / 60.0   # unimpeded, at the crossing
            q = p.with_assignment(replace(a, runway=r)).with_assignment(replace(b, runway=end,
                                                                                 slot_min=round(at_crossing + 0.5, 1)))
            first = q.get(a.callsign)
            return w, JointPlan([first] + [x for x in q.assignments if x.callsign != a.callsign], q.label)
    raise AssertionError("no crossing route on the ORD fixture wave")


def moves_fcfs_runways(w):
    from backend.wave import moves
    return moves.fcfs_runways(w)


@pytest.mark.usefixtures("_fixture_movements")
def test_taxi_wait_costs_the_atc_taxi_term():
    """Spec §6.5: the ATC taxi term is (taxi_min - best unimpeded) / 10 with taxi_min = unimpeded + waits
    (review T31)."""
    from backend.wave import utility
    w, p = _ord_plan_with_crossing()
    cs = p.assignments[0].callsign
    mm = metrics_map(w, p)
    assert mm[cs].taxi_wait_min > 0
    no_wait = dict(mm)
    no_wait[cs] = replace(mm[cs], taxi_wait_min=0.0, taxi_min=mm[cs].taxi_unimpeded_min)
    a = p.assignments[0]
    assert utility._taxi_extra(w, a, mm[cs]) == pytest.approx(
        utility._taxi_extra(w, a, no_wait[cs]) + mm[cs].taxi_wait_min / utility.TAXI_ROUTED_CAP_MIN, abs=1e-9)
    assert utility.agent_terms(w, p, "ATC", mm)["taxi"] < utility.agent_terms(w, p, "ATC", no_wait)["taxi"]


@pytest.mark.usefixtures("_fixture_movements")
def test_taxi_wait_feeds_in_block():
    # helper: a wave flight whose route crosses a strip with a landing 0.5 min after its exit
    w, p = _ord_plan_with_crossing()
    m = metrics_map(w, p)[p.assignments[0].callsign]
    assert m.taxi_wait_min > 0 and m.taxi_min == pytest.approx(m.taxi_unimpeded_min + m.taxi_wait_min, abs=1e-6)
    assert m.in_block_min == pytest.approx(p.assignments[0].slot_min + m.rot_s / 60.0 + m.taxi_min, abs=0.05)
    assert m.hold_short_strip is not None


@pytest.mark.usefixtures("_fixture_movements")
def test_taxi_conflict_rule_passes_with_waits_and_cites_source():
    w, p = _ord_plan_with_crossing()
    c = _by(rules.check(w, p), "ATC-TAXI-CONFLICT", p.assignments[0].callsign)
    assert c.passed and c.evidence["waits"] >= 1 and c.source.startswith("FAA JO 7110.65 §3-7-2")
    for k in ("waits", "wait_min", "crossings", "exit_clear_min", "next_landing_min"):
        assert k in c.evidence


@pytest.mark.usefixtures("_fixture_movements")
def test_taxi_conflict_fails_when_runway_cannot_be_vacated(monkeypatch):
    from backend.wave import ground_traffic, surface
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    a = p.get(w.focus)
    ex = next(t for t in trajectory.build(w, p).ground if t.callsign == w.focus).points[0]   # exit point
    nxt = min((b for b in p.assignments if b.action == "land" and b.runway == a.runway and b.slot_min > a.slot_min),
              key=lambda b: b.slot_min)
    real = ground_traffic.reservations(w)
    blocked = surface.Reservations({**real.node_busy, ex["node"]: [(a.slot_min, nxt.slot_min + 1.0, "BLOCK")]},
                                   real.edge_dir, real.runway_events)
    monkeypatch.setattr(ground_traffic, "reservations", lambda wave: blocked)
    trajectory.build.cache_clear()
    c = _by(rules.check(w, p), "ATC-TAXI-CONFLICT", w.focus)
    assert not c.passed and nxt.callsign in c.detail
    ts = trajectory.build(w, p)
    hit = [x for x in ts.conflicts if x["callsign"] == w.focus]
    assert hit and hit[0]["rule"] == "ATC-TAXI-CONFLICT" and {"at_min", "lat", "lon", "detail"} <= set(hit[0])
    assert not any(k in x for x in ts.to_dict()["conflicts"] for k in ("node",))


def test_taxi_conflict_not_modelled_without_surface():
    w = make_wave()                       # fixture airport: no OSM surface
    p = fcfs_plan(w)
    for a in p.assignments:
        c = _by(rules.check(w, p), "ATC-TAXI-CONFLICT", a.callsign)
        assert c.passed and "ground conflicts not modelled" in c.detail


def _verify(w, ts):
    g = surface._geo(w).graph
    if g is None or surface.wave_tier(w, g) != "real":
        return verify_separation(ts.ground)
    return verify_separation(ts.ground, runway_events=ts.runway_events, g=g)


def test_all_benchmark_fcfs_ground_tracks_separated():
    """Brief: verify_separation on the FCFS ground tracks of the first 10 benchmark waves, with the runway events and
    the graph (carry T29 b). Every message involving a wave flight's track is a failure of the test; fixed tracks are
    scheduled before the plan exists, so a fixed track inside a strip's zone during one of the plan's landings must be
    an informational fixed_holds_for_landing conflict (landing priority) or a fixed arrival still on the runway that
    fails the landing (fixed-vs-fixed separation is
    tests/test_ground_traffic.py's). Pairs are checked wave track x every track (fixed-vs-fixed pairs skipped: at
    movement airports there are ~160 fixed tracks)."""
    for wid in _bench_ids()[:10]:
        w = build.build_wave(wid); p = fcfs_plan(w); ts = trajectory.build(w, p)
        from backend.wave import ground_traffic
        wave_t = list(ts.ground)
        assert all(t.wave for t in wave_t)                  # the fixed tracks live in WaveGeo.ground_traffic
        fixed_t = ground_traffic.fixed_tracks(w) if surface._geo(w).graph is not None else []
        msgs = _verify(w, replace(ts, ground=wave_t))
        msgs += [m for a in wave_t for b in fixed_t for m in verify_separation([a, b])]
        checks = {c.callsign: c for c in rules.check(w, p) if c.rule == "ATC-TAXI-CONFLICT"}
        # a wave flight still on its runway at another flight's landing or takeoff (e.g. a mixed-mode fixed takeoff)
        # is the runway-vacate failure of the rule, not a scheduler bug
        vac = [m for m in msgs if "still vacating" in m]
        assert all(not checks[m.split()[0]].passed and m.split()[-3] in checks[m.split()[0]].evidence["blocking"]
                   for m in vac), (wid, vac)
        assert [m for m in msgs if m not in vac] == [], wid
        g = surface._geo(w).graph
        if not ts.runway_events or g is None:
            continue
        info = {(x["fixed"], x["callsign"]) for x in ts.conflicts if x.get("kind") == "fixed_holds_for_landing"}
        for t in fixed_t:
            for m in verify_separation([t], runway_events=ts.runway_events, g=g):
                cs = m.split()[-3]
                if cs in checks:                          # landing priority: the fixed crosser holds (informational)
                    assert (t.callsign, cs) in info or (not checks[cs].passed and t.callsign in
                                                        checks[cs].evidence.get("landing_conflicts", "")), (wid, m)


@pytest.mark.usefixtures("_fixture_movements")
def test_every_still_vacating_message_is_a_failing_taxi_conflict(monkeypatch):
    """Carry T29 (c): the verifier's runway-vacate check (4) and the rule agree: every wave flight it flags fails
    ATC-TAXI-CONFLICT (no pre-landing margin on either side: JO 7110.65 §3-10-3, clear of the runway before the
    next aircraft crosses the threshold)."""
    from backend.wave import ground_traffic
    w = build.build_wave(ORD); p = fcfs_plan(w)
    a = p.get(w.focus)
    pts = next(t for t in trajectory.build(w, p).ground if t.callsign == w.focus).points
    second = next(q["node"] for q in pts if q["node"] != pts[0]["node"])     # still inside the runway's zone
    nxt = min((b for b in p.assignments if b.action == "land" and b.runway == a.runway and b.slot_min > a.slot_min),
              key=lambda b: b.slot_min)
    real = ground_traffic.reservations(w)
    # the node after the exit is busy past the next landing: the focus waits at its exit node (first point at its
    # runway exit, no wait is ever placed inside the vacate stretch) and is still on the runway when OO5570 lands
    blocked = surface.Reservations({**real.node_busy, second: [(a.slot_min, nxt.slot_min + 1.0, "BLOCK")]},
                                   real.edge_dir, real.runway_events)
    monkeypatch.setattr(ground_traffic, "reservations", lambda wave: blocked)
    trajectory.build.cache_clear()
    ts = trajectory.build(w, p)
    g = surface._geo(w).graph
    wave_cs = {x.callsign for x in w.flights}
    flagged = {m.split()[0] for m in verify_separation([t for t in ts.ground if t.wave], runway_events=ts.runway_events,
                                                         g=g) if "still vacating" in m}
    failing = {c.callsign for c in rules.check(w, p) if c.rule == "ATC-TAXI-CONFLICT" and not c.passed}
    assert flagged and flagged <= failing and flagged <= wave_cs


@pytest.mark.usefixtures("_fixture_movements")
def test_stack_labels_come_from_the_trajectory_set():
    """Parked T14: ATC-HOLD-STACK labels and TrajectorySet.stack_violations use one key set."""
    from backend.wave import route
    w = build.build_wave(ORD); p = fcfs_plan(w)
    for a in [x for x in p.assignments if x.action == "land"][:4]:      # 15 min late: two laps each, several stacks
        p = p.with_assignment(replace(a, slot_min=a.slot_min + 15))
    ts = trajectory.build(w, p)
    rs = route.routes_for_wave(w)
    groups = {trajectory.stack_key(rs[(t.callsign, t.runway)].hold) for t in ts.air
              if t.wave and t.laps >= 1 and (t.callsign, t.runway) in rs}
    assert len(groups) >= 1
    assert set(groups) <= set(ts.stack_labels)
    stack = [c for c in rules.check(w, p) if c.rule == "ATC-HOLD-STACK"]
    assert stack and all(any(lab in c.detail for lab in ts.stack_labels.values()) for c in stack)


def _fixed_landing_hits(w, ts):
    """(fixed label, wave landing callsign) of every verifier message on a fixed track during a wave flight's
    landing."""
    from backend.wave import ground_traffic
    g = surface._geo(w).graph
    wave_cs = {f.callsign for f in w.flights}
    msgs = [m for t in ground_traffic.fixed_tracks(w)
            for m in verify_separation([t], runway_events=ts.runway_events, g=g)]
    return [(m.split()[0], m.split()[-3]) for m in msgs if m.split()[-3] in wave_cs]


@pytest.mark.usefixtures("_fixture_movements")
def test_landing_inside_a_fixed_crossing_is_landing_priority_not_a_failure(monkeypatch):
    """Ruling T31: landings have runway priority, a controller holds the crosser. A plan landing inside a fixed mover's
    scheduled crossing gap window passes ATC-TAXI-CONFLICT and is an informational `fixed_holds_for_landing` conflict
    (fixed callsign, strip, the hold it would need); every verifier message about a fixed track during a wave landing
    is one of them."""
    from backend.wave import ground_traffic
    w = build.build_wave(ORD); p = fcfs_plan(w)
    # put OO5559's landing inside a scheduled fixed crossing of its strip
    g = surface._geo(w).graph
    a = p.get("OO5559")
    strip = trajectory._strip_key(g, a.runway)
    cr = sorted(o for o in ground_traffic._get(w).occupancy.get(strip, ()) if o[3] == "crossing")
    assert cr
    t_in, t_out, lab = cr[0][0], cr[0][1], cr[0][2]
    q = p.with_assignment(replace(a, slot_min=round(t_in + 0.1, 2)))
    ts = trajectory.build(w, q)
    c = _by(rules.check(w, q), "ATC-TAXI-CONFLICT", "OO5559")
    assert c.evidence["fixed_holds"] >= 1 and lab in c.evidence["fixed_holds_for_landing"]
    assert c.passed or "landing_conflicts" in c.evidence or "blocking" in c.evidence or "reason" in c.evidence
    info = [x for x in ts.conflicts if x.get("kind") == "fixed_holds_for_landing" and x["callsign"] == "OO5559"]
    assert any(x["fixed"] == lab and x["strip"] == strip and x["hold_min"] > 0 for x in info)
    assert all(x["lat"] is not None for x in info)
    hits = _fixed_landing_hits(w, ts)
    assert (lab.split()[0], "OO5559") in hits
    named = {(x["fixed"].split()[0], x["callsign"]) for x in ts.conflicts if x.get("kind") == "fixed_holds_for_landing"}
    vac = {(m_f, m_c) for m_f, m_c in hits if (m_f, m_c) not in named}
    for fixed, cs in vac:                     # the only other kind: a fixed arrival still on the runway (a failure)
        assert not _by(rules.check(w, q), "ATC-TAXI-CONFLICT", cs).passed


@pytest.mark.usefixtures("_fixture_movements")
def test_landing_just_after_a_fixed_takeoff_is_landing_priority_not_a_failure():
    """Ruling T31 (review): a plan landing less than LANDING_TAKEOFF_GAP_MIN after a fixed takeoff on the same strip
    passes (the fixed departure would have been held) and is an informational `fixed_departure_holds` conflict."""
    from backend.wave import ground_traffic
    w = build.build_wave(ORD); p = fcfs_plan(w)
    g = surface._geo(w).graph
    res = ground_traffic.reservations(w)
    rw_of = {m.label: m.runway for m in ground_traffic._get(w).movers if m.kind == "departure"}   # takeoff end
    # isolated fixed takeoffs (no other event on their strip within 4 min after) on strips a wave flight can use;
    # each wave landing 0.5 min after one: the takeoff never fails the rule (only scheduler / vacate / fixed-arrival
    # reasons can), and it is always reported as an informational conflict
    picks = [(strip, t, lab) for strip, lst in sorted(res.runway_events.items())
             for t, kind, lab in lst if kind == "takeoff" and 0 < t < 100
             and not any(e[2] != lab and t - 0.1 < e[0] < t + 4.0 for e in lst)]
    assert picks, "no isolated fixed takeoff"
    passed = 0
    for strip, t, lab in picks[:8]:
        for a in [x for x in p.assignments if x.action == "land"][:3]:
            q = p.with_assignment(replace(a, runway=rw_of[lab], slot_min=round(t + 0.5, 2)))
            c = _by(rules.check(w, q), "ATC-TAXI-CONFLICT", a.callsign)
            assert lab in c.evidence["fixed_departure_holds"]
            assert c.passed == ({"reason", "blocking", "landing_conflicts"}.isdisjoint(c.evidence)), c.detail
            ts = trajectory.build(w, q)
            info = [x for x in ts.conflicts if x.get("kind") == "fixed_departure_holds" and x["callsign"] == a.callsign]
            assert any(x["fixed"] == lab and x["strip"] == strip and x["lat"] is not None
                       and x["hold_min"] == pytest.approx(0.5 + ground_traffic.LANDING_TAKEOFF_GAP_MIN, abs=0.011)
                       for x in info)
            if c.passed:
                passed += 1
                assert not [x for x in ts.conflicts if x["callsign"] == a.callsign and "kind" not in x]
    assert passed >= 1


@pytest.mark.usefixtures("_fixture_movements")
def test_hold_short_shows_in_move_labels_and_views(monkeypatch):
    """Spec §6.7: a move whose resulting plan makes the flight hold short says so in its label (`+{wait} hold-short
    {strip}`, <= 120 chars), and the agent views add `hold short {strip} {wait} min for {cs}`."""
    from backend.wave import graph, moves
    w, p = _ord_plan_with_crossing()
    a = p.assignments[0]
    m = metrics_map(w, p)[a.callsign]
    line = graph._view(w, p, "ATC")["taxi"][a.callsign]
    assert f"hold short {m.hold_short_strip}" in line and " min for " in line and len(line) <= 120
    # every taxi-bearing label appends moves._wait_txt of the plan the move produces: here that plan is `p`
    mv = moves._move("GROUND", "reassign_gate", [a.callsign], {"gate": a.gate}, "")
    with monkeypatch.context() as mp:
        mp.setattr(moves, "apply", lambda wave, plan, move: p)
        txt = moves._wait_txt(w, p, mv, a.callsign)
    assert txt == f" +{m.taxi_wait_min:.1f} hold-short {m.hold_short_strip}"
    labels = [x.label for ag in ("ATC", "GROUND") for x in moves._candidates(w, p, ag)]
    assert all(len(x) <= 120 for x in labels)
