"""Status-quo baseline: today's uncoordinated practice, reported honestly (it may violate rules)."""
from __future__ import annotations

from dataclasses import replace

from atm.wave import moves
from atm.wave.model import Assignment, FlightMetrics, JointPlan, Wave
from atm.wave.utility import metrics_map

LABEL = "Status quo (FCFS, scheduled gates, no coordination)"


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
        if m.hold_min + max(0.0, m.route_extra_min) > float(f.fuel.get("extra_min", 0)):
            a = Assignment(a.callsign, "divert", None, None, None, 0.0, 0.0, FlightMetrics())
        else:
            a = replace(a, metrics=FlightMetrics())
        out.append(a)
    return JointPlan(out, LABEL)
