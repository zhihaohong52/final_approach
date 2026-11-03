"""Human edits: the duty manager moves a flight on the Gantt and the engine re-checks the whole plan.

The edited plan is scored with the same evaluate() the negotiation uses, so an edit passes or fails
exactly the rules (`rules.check`) the agents' plans do. For each flight that breaks a rule, the nearest landing
slot on the same runway where that flight breaks nothing, and nobody else breaks more, is suggested (searched minute by minute)."""
from __future__ import annotations

from backend.wave.model import Assignment, JointPlan, Wave
from backend.wave.utility import evaluate

SEARCH_MIN = 30   # how far either side of the edited slot to look for a legal one
_FIELDS = ("callsign", "action", "runway", "slot_min", "gate", "speed_reduction_min", "ground_delay_min")


def plan_from_dicts(rows: list[dict]) -> JointPlan:
    """Contract Assignment dicts (metrics ignored) -> JointPlan. Raises ValueError on a malformed row."""
    out = []
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("callsign"), str):
            raise ValueError("each assignment needs a callsign")
        kw = {k: r[k] for k in _FIELDS if k in r and r[k] is not None}
        if kw.get("action", "land") not in ("land", "divert"):
            raise ValueError(f"bad action for {r['callsign']}")
        for k in ("slot_min", "speed_reduction_min", "ground_delay_min"):
            if k in kw:
                kw[k] = float(kw[k])
        out.append(Assignment(**kw))
    return JointPlan(out, "edited by the duty manager")


def _failures(ev: dict) -> set[tuple[str, str | None]]:
    return {(c["rule"], c.get("callsign")) for c in ev["checks"] if not c["passed"]}


def _nearest_legal(wave: Wave, plan: JointPlan, a: Assignment, others: set) -> float | None:
    """Nearest slot where this flight breaks nothing and no breach is added to anyone else's."""
    if a.action != "land" or a.slot_min is None:
        return None
    for d in range(1, SEARCH_MIN + 1):
        for s in (a.slot_min + d, a.slot_min - d):
            trial = plan.with_assignment(Assignment(**{**{k: getattr(a, k) for k in _FIELDS}, "slot_min": s}))
            if _failures(evaluate(wave, trial)) <= others:
                return s
    return None


def check_edit(wave: Wave, plan: JointPlan) -> dict:
    """{plan, evaluation, suggestions: {callsign: slot_min}} for an edited plan."""
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
    return {"plan": plan.to_dict(), "evaluation": ev, "suggestions": suggestions}
