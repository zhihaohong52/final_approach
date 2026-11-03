"""Human edits: the duty manager moves a flight on the Gantt and the engine re-checks the whole plan.

The edited plan is scored with the same evaluate() the negotiation uses, so an edit passes or fails
exactly the rules (`rules.check`) the agents' plans do. For each flight that breaks a rule, the nearest landing
slot on the same runway where that flight breaks nothing, and nobody else breaks more, is suggested (searched minute by minute)."""
from __future__ import annotations

import math

from backend.wave.model import Assignment, JointPlan, Wave
from backend.wave.utility import evaluate

SEARCH_MIN = 30   # how far either side of the edited slot to look for a legal one
_FIELDS = ("callsign", "action", "runway", "slot_min", "gate", "speed_reduction_min", "ground_delay_min")


SLOT_MARGIN_BEFORE_MIN = 60.0    # slot bounds: this far before the wave's earliest time (or T0) ...
SLOT_MARGIN_AFTER_MIN = 240.0    # ... and this far after its latest one (or T0 + 60, the window end)
DELAY_MAX_MIN = 240.0            # speed_reduction_min / ground_delay_min must lie in [0, DELAY_MAX_MIN]


def slot_bounds(wave: Wave) -> tuple[float, float]:
    """[lo, hi] for an edited slot_min: lo = min(0, every sched / ETA / queue slot) - 60,
    hi = max(60, every sched / ETA / queue slot) + 240 (minutes vs T0 = window_start; the window is 60 min).
    Wide enough for any real plan (a flight 5 h late keeps 4 h of room), small enough that a trajectory
    stays bounded."""
    ts = [0.0, 60.0] + [x for f in wave.flights for x in (f.sched_min, f.eta_earliest_min)] \
        + [q.slot_min for q in wave.queue]
    ts = [float(t) for t in ts if t is not None and math.isfinite(float(t))]
    return min(ts) - SLOT_MARGIN_BEFORE_MIN, max(ts) + SLOT_MARGIN_AFTER_MIN


def plan_from_dicts(rows: list[dict], wave: Wave | None = None) -> JointPlan:
    """Contract Assignment dicts (metrics ignored) -> JointPlan. Raises ValueError on a malformed row: a
    missing callsign, a bad action, a non-number or non-finite (NaN / inf) number. With `wave`, also on
    an input the engine cannot use: more rows than wave flights, a duplicate or unknown callsign (not a wave
    flight), an unknown runway, a slot outside `slot_bounds(wave)`, a speed reduction or ground delay
    outside [0, 240] min."""
    if wave is not None and len(rows) > len(wave.flights):
        raise ValueError(f"{len(rows)} assignments for a wave of {len(wave.flights)} flights")
    bounds = slot_bounds(wave) if wave is not None else None
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("callsign"), str):
            raise ValueError("each assignment needs a callsign")
        cs = r["callsign"]
        kw = {k: r[k] for k in _FIELDS if k in r and r[k] is not None}
        if kw.get("action", "land") not in ("land", "divert"):
            raise ValueError(f"bad action for {cs}")
        for k in ("slot_min", "speed_reduction_min", "ground_delay_min"):
            if k in kw:
                if isinstance(kw[k], bool):
                    raise ValueError(f"{k} of {cs} must be a number")
                kw[k] = float(kw[k])
                if not math.isfinite(kw[k]):
                    raise ValueError(f"{k} of {cs} must be finite")
        if wave is not None:
            if cs in seen:
                raise ValueError(f"duplicate assignment for {cs}")
            if not any(f.callsign == cs for f in wave.flights):
                raise ValueError(f"{cs} is not a flight of wave {wave.id}")
            if "runway" in kw and not any(rw.id == kw["runway"] for rw in wave.airport.runways):
                raise ValueError(f"unknown runway {kw['runway']!r} for {cs}")
            lo, hi = bounds
            if "slot_min" in kw and not lo <= kw["slot_min"] <= hi:
                raise ValueError(f"slot_min of {cs} must lie in [{lo:g}, {hi:g}] min")
            for k in ("speed_reduction_min", "ground_delay_min"):
                if k in kw and not 0.0 <= kw[k] <= DELAY_MAX_MIN:
                    raise ValueError(f"{k} of {cs} must lie in [0, {DELAY_MAX_MIN:g}] min")
        seen.add(cs)
        out.append(Assignment(**kw))
    return JointPlan(out, "edited by the duty manager")


def _failures(ev: dict) -> set[tuple[str, str | None]]:
    return {(c["rule"], c.get("callsign")) for c in ev["checks"] if not c["passed"]}


def _floor(wave: Wave, a: Assignment) -> float | None:
    """Earliest touchdown of `a` on its runway along the engine's route (`trajectory.earliest`), or None for
    a callsign that is not a wave flight."""
    from backend.wave import trajectory
    try:
        return trajectory.earliest(wave, a.callsign, a.runway, a.speed_reduction_min, a.ground_delay_min)
    except ValueError:
        return None


def _nearest_legal(wave: Wave, plan: JointPlan, a: Assignment, others: set) -> float | None:
    """Nearest slot where this flight breaks nothing and no breach is added to anyone else's. Slots before
    the flight's earliest touchdown on that runway (its route there) are never suggested."""
    if a.action != "land" or a.slot_min is None:
        return None
    floor = _floor(wave, a)
    for d in range(1, SEARCH_MIN + 1):
        for s in (a.slot_min + d, a.slot_min - d):
            if floor is not None and s < floor - 1e-9:
                continue
            trial = plan.with_assignment(Assignment(**{**{k: getattr(a, k) for k in _FIELDS}, "slot_min": s}))
            if _failures(evaluate(wave, trial)) <= others:
                return s
    return None


def check_edit(wave: Wave, plan: JointPlan, include_trajectories: bool = False) -> dict:
    """{plan, evaluation, suggestions: {callsign: slot_min}} for an edited plan, plus `trajectories` (the
    plan's `TrajectorySet`) when asked."""
    ev = evaluate(wave, plan)
    suggestions: dict[str, float] = {}
    if ev["checks"] and not ev["feasible"]:
        fails = _failures(ev)
        for cs in sorted({c for _, c in fails if c}):
            try:
                s = _nearest_legal(wave, plan, plan.get(cs), {f for f in fails if f[1] != cs})
            except StopIteration:
                continue
            if s is not None:
                suggestions[cs] = s
    out = {"plan": plan.to_dict(), "evaluation": ev, "suggestions": suggestions}
    if include_trajectories:
        from backend.wave import trajectory
        out["trajectories"] = trajectory.build(wave, plan).to_dict()
    return out
