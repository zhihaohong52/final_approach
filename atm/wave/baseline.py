"""Status-quo baseline: today's uncoordinated practice, reported honestly (it may violate rules)."""
from __future__ import annotations

from dataclasses import replace

from atm.wave import moves
from atm.wave.model import Assignment, FlightMetrics, JointPlan, Wave
from atm.wave.utility import metrics_map

LABEL = "Status quo (FCFS, scheduled gates, no coordination)"


def _hold(f, a: Assignment, m: FlightMetrics) -> float:
    """Unrounded hold, as PLT-FUEL-RESERVE compares it: `laps x lap_min + vector_min` from the trajectory,
    or v2's `max(0, slot - ETA - SR - GD)` in the no-geometry tier (`m.hold_min` is rounded to 0.1)."""
    if m.lap_min is not None:
        return m.hold_laps * m.lap_min + m.vector_min
    return max(0.0, float(a.slot_min) - f.eta_earliest_min - a.speed_reduction_min - a.ground_delay_min)


def status_quo(wave: Wave) -> JointPlan:
    """FCFS by earliest ETA on the best-aligned open runway, scheduled gate even if busy, no speed
    reduction / ground delay / remote stand. A flight diverts when its hold plus any extra route time
    (`hold_min + max(0, route_extra_min)`, the PLT-FUEL-RESERVE quantity) exceeds its extra fuel."""
    plan = moves.fcfs_plan(wave)
    mm = metrics_map(wave, plan)
    out = []
    for a in plan.assignments:
        f = wave.flight(a.callsign)
        m = mm[a.callsign]
        if _hold(f, a, m) + max(0.0, m.route_extra_min) > float(f.fuel.get("extra_min", 0)):
            a = Assignment(a.callsign, "divert", None, None, None, 0.0, 0.0, FlightMetrics())
        else:
            a = replace(a, metrics=FlightMetrics())
        out.append(a)
    return JointPlan(out, LABEL)
