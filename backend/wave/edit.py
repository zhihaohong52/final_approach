"""Human edits: the duty manager moves a flight on the Gantt and the engine re-checks the whole plan.

The edited plan is scored with the same evaluate() the negotiation uses, so an edit passes or fails
exactly the rules (`rules.check`) the agents' plans do. For each flight that breaks a rule, the nearest landing
slot on the same runway where that flight breaks nothing, and nobody else breaks more, is suggested (searched minute by minute)."""
from __future__ import annotations

import math

from backend.wave.model import Assignment, JointPlan, Move, Wave
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


def _finite(k: str, cs: str, v: object) -> float:
    """`v` as a finite float, else ValueError (the message never echoes `v`). A bool is not a number; a JSON
    integer too large for a float (float() raises OverflowError, e.g. 10**400) and NaN / inf are rejected."""
    if isinstance(v, bool):
        raise ValueError(f"{k} of {cs} must be a number")
    try:
        x = float(v)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{k} of {cs} must be a finite number") from None
    if not math.isfinite(x):
        raise ValueError(f"{k} of {cs} must be finite")
    return x


def plan_from_dicts(rows: list[dict], wave: Wave | None = None) -> JointPlan:
    """Contract Assignment dicts (metrics ignored) -> JointPlan. Raises ValueError on a malformed row: a
    missing callsign or one longer than MOVE_CALLSIGN_MAX, a bad action, a runway or gate that is not a string of
    at most MOVE_STR_MAX characters, a non-number or non-finite (NaN / inf, or a JSON integer too large for a
    float) number; messages never echo a number, runway or gate. With `wave`, also on
    an input the engine cannot use: more rows than wave flights, a duplicate or unknown callsign (not a wave
    flight), an unknown runway, a slot outside `slot_bounds(wave)`, a speed reduction or ground delay
    outside [0, 240] min."""
    if wave is not None and len(rows) > len(wave.flights):
        raise ValueError(f"{len(rows)} assignments for a wave of {len(wave.flights)} flights")
    bounds = slot_bounds(wave) if wave is not None else None
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("callsign"), str) or not r["callsign"] \
                or len(r["callsign"]) > MOVE_CALLSIGN_MAX:
            raise ValueError(f"each assignment needs a callsign of at most {MOVE_CALLSIGN_MAX} characters")
        cs = r["callsign"]
        kw = {k: r[k] for k in _FIELDS if k in r and r[k] is not None}
        if kw.get("action", "land") not in ("land", "divert"):
            raise ValueError(f"bad action for {cs}")
        for k in ("runway", "gate"):
            if k in kw and (not isinstance(kw[k], str) or len(kw[k]) > MOVE_STR_MAX):
                raise ValueError(f"{k} of {cs} must be a string of at most {MOVE_STR_MAX} characters")
        for k in ("slot_min", "speed_reduction_min", "ground_delay_min"):
            if k in kw:
                kw[k] = _finite(k, cs, kw[k])
        if wave is not None:
            if cs in seen:
                raise ValueError(f"duplicate assignment for {cs}")
            if not any(f.callsign == cs for f in wave.flights):
                raise ValueError(f"{cs} is not a flight of wave {wave.id}")
            if "runway" in kw and not any(rw.id == kw["runway"] for rw in wave.airport.runways):
                raise ValueError(f"unknown runway for {cs}")
            lo, hi = bounds
            if "slot_min" in kw and not lo <= kw["slot_min"] <= hi:
                raise ValueError(f"slot_min of {cs} must lie in [{lo:g}, {hi:g}] min")
            for k in ("speed_reduction_min", "ground_delay_min"):
                if k in kw and not 0.0 <= kw[k] <= DELAY_MAX_MIN:
                    raise ValueError(f"{k} of {cs} must lie in [0, {DELAY_MAX_MIN:g}] min")
        seen.add(cs)
        out.append(Assignment(**kw))
    return JointPlan(out, "edited by the duty manager")


MOVE_PARAMS_MAX = 4          # a contract Move carries at most this many params ...
MOVE_STR_MAX = 64            # ... each string value, key, kind, agent and id at most this long
MOVE_CALLSIGN_MAX = 16
MOVE_LABEL_MAX = 200
MOVE_NUM_MAX = 1440.0        # |a numeric param| (or a numeric string's value), minutes: a day; real moves use <= 240


def _bounded_number(k: str, v: object) -> None:
    """A numeric param (or a string that parses as one, as `moves` would read it) must be finite and within
    MOVE_NUM_MAX. A huge JSON integer is checked as an int first: float() / isfinite() of it would overflow."""
    if isinstance(v, bool):
        raise ValueError(f"move.params.{k} must be a short string or a finite number")
    if isinstance(v, int):
        if abs(v) > MOVE_NUM_MAX:
            raise ValueError(f"move.params.{k} must lie in [-{MOVE_NUM_MAX:g}, {MOVE_NUM_MAX:g}]")
        return
    x = v
    if isinstance(v, str):
        try:
            x = float(v)
        except ValueError:
            return                   # a name (runway, gate): its own checks are moves.apply's
    if not isinstance(x, float) or not math.isfinite(x) or abs(x) > MOVE_NUM_MAX:
        raise ValueError(f"move.params.{k} must be a finite number in [-{MOVE_NUM_MAX:g}, {MOVE_NUM_MAX:g}]")


def _short_str(name: str, v: object, n: int, required: bool = True) -> str:
    if v is None and not required:
        return ""
    if not isinstance(v, str) or len(v) > n:
        raise ValueError(f"{name} must be a string of at most {n} characters")
    return v


def move_from_dict(d: object) -> Move:
    """A contract Move dict (e.g. a RejectedMove's `move`) -> Move. Raises ValueError on a malformed or oversized one
    (messages never echo the input): not an object; kind / agent / id not strings of <= MOVE_STR_MAX chars, label
    not one of <= MOVE_LABEL_MAX; callsigns not a list of 1-2 strings of <= MOVE_CALLSIGN_MAX; params not an object
    of at most MOVE_PARAMS_MAX entries with keys of <= MOVE_STR_MAX chars, each value a string of <= MOVE_STR_MAX
    chars or a finite number, and every number (or numeric string) within +-MOVE_NUM_MAX. Authority and
    applicability are `moves.apply`'s checks."""
    if not isinstance(d, dict):
        raise ValueError("move must be an object")
    kind = _short_str("move.kind", d.get("kind"), MOVE_STR_MAX)
    agent = _short_str("move.agent", d.get("agent"), MOVE_STR_MAX)
    mid = _short_str("move.id", d.get("id"), MOVE_STR_MAX, required=False)
    label = _short_str("move.label", d.get("label"), MOVE_LABEL_MAX, required=False)
    cs, params = d.get("callsigns"), d.get("params", {})
    if not isinstance(cs, list) or not 1 <= len(cs) <= 2 or \
            not all(isinstance(x, str) and len(x) <= MOVE_CALLSIGN_MAX for x in cs):
        raise ValueError(f"move.callsigns must be a list of 1 or 2 callsigns of at most {MOVE_CALLSIGN_MAX} characters")
    if not isinstance(params, dict) or len(params) > MOVE_PARAMS_MAX:
        raise ValueError(f"move.params must be an object of at most {MOVE_PARAMS_MAX} entries")
    for k, v in params.items():
        if not isinstance(k, str) or len(k) > MOVE_STR_MAX:
            raise ValueError(f"move.params keys must be at most {MOVE_STR_MAX} characters")
        if isinstance(v, str):
            if len(v) > MOVE_STR_MAX:
                raise ValueError(f"move.params.{k} must be a short string or a finite number")
        elif not isinstance(v, int | float):
            raise ValueError(f"move.params.{k} must be a short string or a finite number")
        _bounded_number(k, v)
    return Move(id=mid, agent=agent, kind=kind, callsigns=list(cs), params=dict(params), label=label)


def apply_move(wave: Wave, plan: JointPlan, d: object) -> JointPlan:
    """`plan` with the contract move `d` applied by the engine's own `moves.apply` (the plan a rejected move would
    have produced), re-bounded like any input plan. ValueError if the move is malformed, outside its agent's
    authority, not applicable, or would take the plan outside `plan_from_dicts`' bounds."""
    from backend.wave import moves
    m = move_from_dict(d)
    try:
        moved = moves.apply(wave, plan, m)
    except (KeyError, TypeError, ArithmeticError):   # backstop: a param of the wrong type, or one that overflows
        raise ValueError(f"move {m.kind!r} does not apply to this plan") from None
    return plan_from_dicts([a.to_dict() for a in moved.assignments], wave)


# rule evidence fields that name the other flight(s) a breach is with (comma-separated callsigns)
OTHER_FLIGHT_KEYS = ("leader", "follower", "conflict", "overlapping", "earlier_holding", "blocked_by")


def _named(check: dict) -> set[str]:
    """Callsigns a failing check names in its structured evidence (OTHER_FLIGHT_KEYS)."""
    ev = check.get("evidence") or {}
    return {x.strip() for k in OTHER_FLIGHT_KEYS for x in str(ev.get(k, "")).split(",") if x.strip()}


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
    from backend.wave import trajectory
    ev = evaluate(wave, plan)
    suggestions: dict[str, float] = {}
    if ev["checks"] and not ev["feasible"]:
        fails = _failures(ev)
        with trajectory.scratch():   # up to 60 trial plans per failing flight: kept out of the shared plan cache
            _suggest(wave, plan, ev, fails, suggestions)
    out = {"plan": plan.to_dict(), "evaluation": ev, "suggestions": suggestions}
    if include_trajectories:
        out["trajectories"] = trajectory.build(wave, plan).to_dict()
    return out


def _suggest(wave: Wave, plan: JointPlan, ev: dict, fails: set, suggestions: dict[str, float]) -> None:
    """Fill `suggestions` (callsign -> nearest legal slot) for each flight with a failing check."""
    for cs in sorted({c for _, c in fails if c}):
        # tolerate other flights' breaches, except those that name this flight (e.g. a flight still on the
        # runway at this one's landing): those it must clear too
        caused = {(c["rule"], c.get("callsign")) for c in ev["checks"] if not c["passed"]
                  and c.get("callsign") != cs and cs in _named(c)}
        try:
            s = _nearest_legal(wave, plan, plan.get(cs), {f for f in fails if f[1] != cs} - caused)
        except StopIteration:
            continue
        if s is not None:
            suggestions[cs] = s
