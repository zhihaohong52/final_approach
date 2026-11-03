"""Per-flight metrics, per-agent utility formulas and the contract PlanEvaluation for wave mode.

All times are minutes relative to the wave window start (T0). Weights follow v1 `backend/plans.py`,
extended to joint plans (ATC scores the whole wave, each Pilot its own flight, Ground gates/crew).
"""
from __future__ import annotations

import math

from backend.wave import surface
from backend.wave.model import Assignment, FlightMetrics, JointPlan, Wave, WaveFlight

# ---------------------------------------------------------------- physical constants
HOLD_BURN_KG_MIN = {"SUPER": 180.0, "HEAVY": 110.0, "B757": 70.0, "LARGE": 45.0, "SMALL": 25.0}
SPEED_REDUCTION_BURN_FRACTION = 0.20   # absorbing delay en route burns ~20% of holding fuel
TAXI_NEAR_MIN = 6.0                    # no-geometry tier only (no runway coordinates): v2 near/far taxi
TAXI_FAR_MIN = 10.0
GENERIC_TAXI_IN_MIN = 6.0              # taxi-in reference when neither the row nor the airport has a TAXI_IN
REMOTE_BUS_MIN = 10.0                  # passengers bussed from the remote stand
TURNAROUND_MIN = 45.0                  # default only; per airport: turnaround_min(wave) (workbook)
MIN_CONNECT_MIN = 35.0
DIVERT_EXTRA_DELAY_MIN = 90.0          # divert delay = alternate_min + 90
MAX_SPEED_REDUCTION_MIN = 8.0

# ---------------------------------------------------------------- utility normalisation caps
HOLD_CAP_MIN = 20.0          # ATC: mean hold per flight at which the holding penalty saturates
TAXI_EXTRA_CAP_MIN = TAXI_FAR_MIN - TAXI_NEAR_MIN   # ATC, no-geometry tier: extra taxi beyond near
TAXI_ROUTED_CAP_MIN = 10.0   # ATC, surface tiers: taxi beyond the best unimpeded taxi over the open runways (spec §6.5)
DELAY_CAP_MIN = 60.0         # PILOT: own delay at which the delay penalty saturates
OVERTIME_CAP_MIN = 30.0      # GROUND: mean paid overtime (overtime x OVERTIME_MULT) per flight
OVERTIME_MULT = 1.5          # workbook: ground crew overtime costs 1.5x after the 8 h shift
NEXT_DEP_CAP_MIN = 60.0      # GROUND: mean next-departure delay per flight
DIVERT_FUEL_TERM = 0.5       # PILOT: fuel term for a diversion (fuel is safe but spent flying away)

W_ATC = {"holding": 0.45, "runway_load": 0.25, "taxi": 0.15, "diversions": 0.15}
W_PILOT = {"fuel": 0.35, "delay": 0.25, "connections": 0.30, "divert": 0.10}
W_GROUND = {"overtime": 0.35, "turnaround": 0.35, "reassignment": 0.20, "bussing": 0.10}


def fuel_info(f: WaveFlight) -> tuple[float, str | None, float | None, float]:
    """(extra_min, alternate, alternate_min, final_reserve_min) with missing values defaulted:
    extra 0, final reserve 30; alternate / alternate_min None when absent."""
    fuel = f.fuel or {}
    alt_min = fuel.get("alternate_min")
    return (float(fuel.get("extra_min") or 0), fuel.get("alternate") or None,
            float(alt_min) if alt_min is not None else None, float(fuel.get("final_reserve_min") or 30))


def hold_burn(f: WaveFlight) -> float:
    """Holding burn kg/min from the workbook: narrowbody 2400 kg/h, widebody 6500 kg/h (by gate size)."""
    from backend.wave import constraints
    burn = constraints.RULES["burn_kg_h"]
    return float(burn.get(f.gate_size, burn["narrow"])) / 60.0


def turnaround_min(wave: Wave) -> float:
    """Workbook min_turnaround_min of the wave airport (gate occupancy and turn)."""
    from backend.wave import constraints
    try:
        return float(constraints.airport(wave.airport.iata).min_turnaround_min)
    except Exception:
        return TURNAROUND_MIN


def focus_callsign(wave: Wave) -> str:
    """The disruption flight: wave.focus if set and present, else the most-delayed disrupted flight
    (BTS arr_delay, then eta - sched), else the most-delayed flight."""
    cs = {f.callsign for f in wave.flights}
    foc = getattr(wave, "focus", None)
    if foc in cs:
        return foc
    pool = [f for f in wave.flights if f.disrupted] or list(wave.flights)

    def late(f):
        return (float(f.delays.get("arr_delay") or 0), f.eta_earliest_min - f.sched_min, f.callsign)
    return max(pool, key=late).callsign


def negotiators(wave: Wave) -> list[str]:
    """Exactly three negotiating agents (rules_of_play): ATC, the disruption flight's Pilot, Ground."""
    return ["ATC", f"PILOT:{focus_callsign(wave)}", "GROUND"]


def taxi_in_ref(wave: Wave, callsign: str) -> float:
    """Taxi-in reference of a wave flight (spec §6.1): its real BTS TAXI_IN when known, else the airport's 2015
    median TAXI_IN, both resolved at build time into `WaveFlight.taxi_in_ref_min`; else GENERIC_TAXI_IN_MIN.
    BTS scheduled arrival is a gate time, so the earliest landing is `sched + attributed delays - taxi_in_ref`.
    Never reads the store (hot path)."""
    return flight_taxi_in_ref(wave.flight(callsign))


def flight_taxi_in_ref(f: WaveFlight) -> float:
    """`taxi_in_ref` of one WaveFlight (the stored reference, else GENERIC_TAXI_IN_MIN)."""
    v = getattr(f, "taxi_in_ref_min", None)
    return float(v) if v is not None and math.isfinite(v) else GENERIC_TAXI_IN_MIN


def _gate(wave: Wave, gid: str | None):
    if gid is None:
        return None
    return next((g for g in wave.gates if g.id == gid), None)


def _group(items: list[str], item: str) -> int:
    """0 for the first half of `items`, 1 for the second half (-1 if unknown)."""
    if item not in items:
        return -1
    return (items.index(item) * 2) // max(len(items), 1)


def taxi_min(wave: Wave, runway: str | None, gate_id: str | None) -> float:
    """v2 near/far heuristic, used only in the no-geometry tier (no runway coordinates, `surface.taxi_info` tier
    "none"): the first half of the airport's runway list pairs with the first half of the (alphabetical) terminal
    piers. Remote stand is always far."""
    gate = _gate(wave, gate_id)
    if gate is None or runway is None or gate.remote:
        return TAXI_FAR_MIN
    rwys = [r.id for r in wave.airport.runways]
    piers = sorted({g.pier for g in wave.gates if not g.remote})
    near = _group(rwys, runway) == _group(piers, gate.pier) != -1
    return TAXI_NEAR_MIN if near else TAXI_FAR_MIN


def _flight_metrics(wave: Wave, a: Assignment, track, ground=None) -> FlightMetrics:
    """One flight's metrics given its AirTrack and GroundOutcome from the plan's TrajectorySet (None when not drawn).
    In-block (spec §6.5, v3-traffic) = slot + ROT + taxi, taxi = unimpeded taxi to the gate (`surface.taxi_info`, the
    same object the ground track is drawn from) + the hold-short / give-way waits the plan's hold-short pass gave it
    (`taxi_wait_min`; 0 when ground conflicts are not modelled); slot + v2 near/far taxi in the no-geometry tier."""
    f = wave.flight(a.callsign)
    pax_total = sum(int(c.get("pax", 0)) for c in f.connections)
    burn = hold_burn(f)
    if a.action == "divert":
        alt = max(fuel_info(f)[2] or 0.0, 0.0)
        # a diverting flight still carries the delay it already has (late inbound / carrier):
        # without the base a 300-min-late flight looked *less* delayed by diverting (alt + 90).
        # §6.1: measured at in-block, so the earliest landing plus the taxi-in reference
        base = max(0.0, f.eta_earliest_min + flight_taxi_in_ref(f) - f.sched_min)
        delay = base + alt + DIVERT_EXTRA_DELAY_MIN
        return FlightMetrics(hold_min=0.0, fuel_kg=round(alt * burn, 1), delay_min=round(delay, 1),
                             in_block_min=None, pax_protected=0, pax_total=pax_total,
                             overtime_min=0.0, next_dep_delay_min=round(delay, 1))
    # a missing / non-finite slot is scored as "at ETA" (PLT-EARLIEST-ETA rejects it separately)
    slot = float(a.slot_min) if isinstance(a.slot_min, (int, float)) and math.isfinite(a.slot_min) \
        else float(f.eta_earliest_min)
    path: dict = {}
    if track is not None and track.lap_min is not None:
        # geometry tier (spec §6.2): D = slot - earliest(f, r) is split into laps and vector stretch;
        # a slot before the earliest landing gives 0 laps and 0 vector (PLT-EARLIEST-ETA reports it)
        hold = track.laps * track.lap_min + track.vector_min
        extra = track.route_extra_min
        path = {"hold_laps": track.laps, "hold_fix": track.hold_fix, "hold_level_ft": track.hold_level_ft,
                "lap_min": track.lap_min, "vector_min": track.vector_min, "route_min": track.route_min,
                "route_extra_min": extra}
    else:
        # no-geometry tier (no route for this runway / no runway): v2's hold, no route term
        hold = max(0.0, slot - f.eta_earliest_min - a.speed_reduction_min - a.ground_delay_min)
        extra = 0.0
    # route extra may be negative (a shorter route than nominal) but never takes fuel below zero
    fuel = (hold + max(extra, -hold)) * burn + a.speed_reduction_min * burn * SPEED_REDUCTION_BURN_FRACTION
    gate = _gate(wave, a.gate)
    tx = surface.taxi_info(wave, a.runway, a.gate, f.gate_size)
    if tx.tier == "none":
        in_block = slot + tx.taxi_min
    else:
        wait = ground.wait_min if ground is not None and ground.modelled else 0.0
        in_block = slot + tx.total_min + wait
        path.update({"exit": tx.exit_label, "rot_s": tx.rot_s, "taxi_unimpeded_min": tx.taxi_min,
                     "taxi_wait_min": wait, "taxi_min": tx.taxi_min + wait,
                     "hold_short_strip": ground.hold_short_strip if ground is not None else None})
    pax_ready = in_block + (REMOTE_BUS_MIN if gate is not None and gate.remote else 0.0)
    protected = sum(int(c.get("pax", 0)) for c in f.connections
                    if float(c["departs_min"]) - pax_ready >= MIN_CONNECT_MIN)
    turn_end = in_block + turnaround_min(wave)
    return FlightMetrics(
        hold_min=round(hold, 1), fuel_kg=round(fuel, 1),
        delay_min=round(max(0.0, in_block - f.sched_min), 1),   # §6.1: sched_min is the BTS gate (in-block) time
        in_block_min=round(in_block, 1), pax_protected=protected, pax_total=pax_total,
        overtime_min=round(max(0.0, turn_end - f.crew_shift_end_min), 1),
        next_dep_delay_min=round(max(0.0, turn_end - f.next_departure_min), 1), **path)


def metrics_map(wave: Wave, plan: JointPlan) -> dict[str, FlightMetrics]:
    """Joint per-flight metrics of `plan` (the only metrics entry point, spec §6.5): every landing is read
    from the plan's 4D trajectories (`trajectory.build`, memoised on (wave key, plan id)).

    Geometry tier: `hold_min = laps x lap_min + vector_min`, `fuel_kg = (hold_min + max(route_extra_min,
    -hold_min)) x burn + SR x burn x 0.20`; path fields (`hold_laps`, `hold_fix`, `hold_level_ft`,
    `lap_min`, `vector_min`, `route_min`, `route_extra_min`) are copied unrounded from the AirTrack.
    No-geometry tier (no route for the assigned runway): v2's `hold = max(0, slot - ETA - SR - GD)`, no
    route term, path fields left at their defaults. Taxi fields (`exit`, `rot_s`, `taxi_unimpeded_min`,
    `taxi_wait_min`, `taxi_min` = unimpeded + waits, `hold_short_strip`) come from `surface.taxi_info` and the plan's
    hold-short pass (`TrajectorySet.outcomes`), unrounded; unset in the no-geometry taxi tier."""
    from backend.wave import trajectory   # lazy: build / rules / moves import this module (import cycle)
    ts = trajectory.build(wave, plan)
    tracks = {t.callsign: t for t in ts.air if t.wave}
    return {a.callsign: _flight_metrics(wave, a, tracks.get(a.callsign) if a.action == "land" else None,
                                        ts.outcomes.get(a.callsign) if a.action == "land" else None)
            for a in plan.assignments}


def _clip(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _taxi_extra(wave: Wave, a: Assignment, m: FlightMetrics | None = None) -> float:
    """ATC taxi term of one landing, normalised (spec §6.5): (taxi_min - best unimpeded taxi for its gate over the
    open runways) / 10 min, with taxi_min = unimpeded + taxi waits (`FlightMetrics.taxi_min`, so a hold-short or
    give-way wait costs ATC); no-geometry tier: v2's (near/far taxi - 6) / 4. Taxi here excludes the ROT on both
    sides."""
    size = wave.flight(a.callsign).gate_size
    tx = surface.taxi_info(wave, a.runway, a.gate, size)
    if tx.tier == "none":
        return (tx.taxi_min - TAXI_NEAR_MIN) / TAXI_EXTRA_CAP_MIN
    alts = [t.taxi_min for t in (surface.taxi_info(wave, r.id, a.gate, size) for r in wave.airport.runways if r.open)
            if t.tier != "none"]
    taxi = m.taxi_min if m is not None and m.taxi_min is not None else tx.taxi_min
    return max(0.0, taxi - min(alts, default=tx.taxi_min)) / TAXI_ROUTED_CAP_MIN


def agent_terms(wave: Wave, plan: JointPlan, agent_id: str,
                mm: dict[str, FlightMetrics] | None = None) -> dict[str, float]:
    """Weighted penalty terms (each <= 0) of an agent's utility. utility = 1 + sum(terms)."""
    mm = mm or metrics_map(wave, plan)
    asg = plan.assignments
    n = max(len(asg), 1)
    if agent_id == "ATC":
        landing = [a for a in asg if a.action == "land"]
        mean_hold = sum(mm[a.callsign].hold_min for a in landing) / n
        counts: dict[str, int] = {}
        for a in landing:
            counts[a.runway or "?"] = counts.get(a.runway or "?", 0) + 1
        # concentration of wave landings on one runway: 0 when spread, 1 when all on one
        load = (max(counts.values()) - 1) / (len(landing) - 1) if len(landing) > 1 else 0.0
        taxi_extra = sum(_taxi_extra(wave, a, mm[a.callsign]) for a in landing) / n
        div = sum(1 for a in asg if a.action == "divert") / n
        return {"holding": -W_ATC["holding"] * _clip(mean_hold / HOLD_CAP_MIN),
                "runway_load": -W_ATC["runway_load"] * _clip(load),
                "taxi": -W_ATC["taxi"] * _clip(taxi_extra),
                "diversions": -W_ATC["diversions"] * _clip(div)}
    if agent_id == "GROUND":
        over = sum(mm[a.callsign].overtime_min for a in asg) / n
        nxt = sum(mm[a.callsign].next_dep_delay_min for a in asg) / n
        reassign, bus = 0.0, 0.0
        for a in asg:
            if a.action != "land":
                continue
            f = wave.flight(a.callsign)
            g, sg = _gate(wave, a.gate), _gate(wave, f.scheduled_gate)
            if g is None or g.remote:
                reassign += 1.0
                bus += 1.0
            elif g.id != f.scheduled_gate:
                reassign += 0.4 if sg is not None and g.pier == sg.pier else 0.6
        return {"overtime": -W_GROUND["overtime"] * _clip(OVERTIME_MULT * over / OVERTIME_CAP_MIN),
                "turnaround": -W_GROUND["turnaround"] * _clip(nxt / NEXT_DEP_CAP_MIN),
                "reassignment": -W_GROUND["reassignment"] * _clip(reassign / n),
                "bussing": -W_GROUND["bussing"] * _clip(bus / n)}
    if agent_id.startswith("PILOT:"):
        cs = agent_id.split(":", 1)[1]
        a = plan.get(cs)
        f = wave.flight(cs)
        m = mm[cs]
        if a.action == "divert":
            fuel_pen = DIVERT_FUEL_TERM
        else:
            budget = max(fuel_info(f)[0], 1.0) * hold_burn(f)
            fuel_pen = _clip(m.fuel_kg / budget)
        conn = 1 - m.pax_protected / m.pax_total if m.pax_total else 0.0
        return {"fuel": -W_PILOT["fuel"] * fuel_pen,
                "delay": -W_PILOT["delay"] * _clip(m.delay_min / DELAY_CAP_MIN),
                "connections": -W_PILOT["connections"] * _clip(conn),
                "divert": -W_PILOT["divert"] * (1.0 if a.action == "divert" else 0.0)}
    raise ValueError(f"unknown agent {agent_id!r}")


def agent_utility(wave: Wave, plan: JointPlan, agent_id: str,
                  mm: dict[str, FlightMetrics] | None = None) -> float:
    return round(_clip(1 + sum(agent_terms(wave, plan, agent_id, mm).values())), 3)


def evaluate(wave: Wave, plan: JointPlan) -> dict:
    """Contract PlanEvaluation. Fills each Assignment.metrics in place."""
    from backend.wave import rules  # lazy: rules imports this module at module level

    try:
        rules.validate_structure(wave, plan)
    except ValueError:
        # Malformed plan (missing / unknown / duplicate callsigns): infeasible, nothing scored.
        return {"plan_id": plan.id, "feasible": False, "checks": [],
                "utilities": {ag: 0.0 for ag in negotiators(wave)},
                "totals": {"delay_min": 0.0, "fuel_kg": 0.0, "hold_min": 0.0, "pax_protected_pct": 0.0,
                           "overtime_min": 0.0, "diversions": 0}}
    mm = metrics_map(wave, plan)
    for a in plan.assignments:
        a.metrics = mm[a.callsign]
    checks = rules.check(wave, plan, mm)
    ms = list(mm.values())
    pax_total = sum(m.pax_total for m in ms)
    totals = {
        "delay_min": round(sum(m.delay_min for m in ms), 1),
        "fuel_kg": round(sum(m.fuel_kg for m in ms), 1),
        "hold_min": round(sum(m.hold_min for m in ms), 1),
        "pax_protected_pct": round(100.0 * sum(m.pax_protected for m in ms) / pax_total, 1) if pax_total else 100.0,
        "overtime_min": round(sum(m.overtime_min for m in ms), 1),
        "diversions": sum(1 for a in plan.assignments if a.action == "divert"),
    }
    return {"plan_id": plan.id,
            "feasible": all(c.passed for c in checks),
            "checks": [c.to_dict() for c in checks],
            "utilities": {ag: agent_utility(wave, plan, ag, mm) for ag in negotiators(wave)},
            "totals": totals}
