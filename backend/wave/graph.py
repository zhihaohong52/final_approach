"""Wave negotiation as a LangGraph state graph (challenge rules_of_play, extended with airline agents; contract v2.0+).

    triage -> open -> atc -> airlines -> ground -> decide_turn -+-> finalize          (consensus)
                       ^                                        +-> atc               (next turn)
                       +----------------------------------------+-> fallback -> finalize (after turn 3)

Voters (`utility.voters`, = `negotiators`): ATC, one AIRLINE:<code> agent per airline in the wave (codes sorted,
`airlines.groups`: a regional flies under its major) and GROUND. Deviation from rules_of_play, which names exactly
three agents (ATC, the disruption flight's pilot, GROUND): the focus flight's bargaining moves to its airline, so
every flight's commercial owner is at the table, not only the focus flight's. Pilots (`utility.pilots`, one per
wave flight) never vote, never propose and make no model calls: a pilot only sends UNABLE when an instruction or a
plan on the table breaks one of its own pilot-side rules (`unable.pilot_side`: PLT-* plus ATC-WIND-LIMITS). One
turn = each voter sends ONE structured AGENT_TURN, in the order ATC -> airlines (alphabetical, inside the one
`airlines` node) -> GROUND; at most 3 turns. Every voter decision goes through a per-agent `Decider` (model chain ->
local formula); the deterministic rule engine (rules.check) is the only judge of legality. Engine messages
(TRIAGE, OPEN, CHECK, CONSENSUS, NO_CONSENSUS, FALLBACK, MODEL_FALLBACK) and pilot UNABLEs are not agent turns.

Message order: TRIAGE, OPEN, (UNABLE* on the opening plan); per turn, for each voter: AGENT_TURN, (UNABLE*), CHECK
(if its proposal is new), then CONSENSUS or NO_CONSENSUS; FALLBACK last if no consensus after turn 3.

Choices made here (documented for review):
- Chaining: in turn t, ATC starts from the incumbent plan, each airline from the previous proposal, GROUND from
  the last airline's. Each voter makes up to 2 moves (3 when triage severity >= 3) and puts the result on the
  table as its proposal. The engine CHECKs every new proposal right after the voter's message (and its UNABLEs).
- Move rejection: a move is rejected iff the plan after it has a violation (rule, callsign) that the plan before
  it did not have. Rejections are listed in the AGENT_TURN `rejected` with the violations (rule, source,
  evidence) and the post-move `plan_id`. A slot allows at most MAX_REJECTIONS_PER_SLOT vetoed picks. A rejected
  move with a pilot-side violation is followed by that pilot's UNABLE naming the instruction.
- Divert guard (per move): a request_divert is offered only if that flight fails PLT-FUEL-RESERVE or no runway
  is open.
- Agent formula (Choice fallback / formulas mode): ATC / GROUND rank options by (known violated rules after the
  move, -own utility); an airline by (known violations on its own flights, fairness-floor breaches at this turn's
  slack, -own utility). A voter moves only on a strict lexicographic improvement; otherwise accepts/passes.
- Fairness floor (spec 3.4, `fairness.py`): an airline compares each of its flights' delay and fuel with the
  floor plan (status quo when feasible, else the best feasible plan it has seen). When some CHECK-passed plan on
  the table meets its floor, it REJECTs every plan that breaches it (`decided_by: "floor"`, `floor: [breach]`,
  never sent to the model); otherwise (firm escape clause) it votes on utility as ATC / GROUND do, and each vote on
  a breaching plan says so (`decided_by: "escape"`, `floor: [breach]`). Its AGENT_TURN lists `objections`: the
  breaches of each CHECK-passed plan another agent proposed; the rationale words its own vote on each of them.
- Votes: when a voter speaks it votes on every CHECK-passed plan on the table at that moment: the incumbent, the
  proposals made earlier in this turn and its own. Consensus = one CHECK-passed plan with no pilot UNABLE,
  ACCEPTed by every voter within the same turn (best max-min utility over the voters if several). A plan GROUND
  proposes in turn t can therefore only win from turn t+1 on, as incumbent.
- Vote formula: P(accept) = 1.0 if utility >= reservation else 0.0. A model vote ACCEPTs at P >= 0.7 and
  REJECTs at P <= 0.3; in between (uncertain) the formula verdict decides and the vote says so.
  Reservation_t = best own utility seen on a FEASIBLE plan so far * (0.9 - 0.12 * (t - 1)).
- Incumbent for turn t+1: the CHECK-passed plan of turn t with the fewest fairness-floor breaches summed over the
  airlines at turn t+1's slack, then the highest max-min utility (so a floor-meeting plan stays on the table while
  one exists); if none passed, the proposal with the fewest violations.
- No consensus after turn 3: the deterministic non-AI solver (fallback.solve) decides; FALLBACK.reason says which
  voter rejected which plan (fairness floor breach, or utility vs reservation) or that no plan passed CHECK.
- stats.model_calls = decisions answered by a model; stats.fallbacks = Decider fallback events.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.decide import DEFAULT_CHAIN, Decider, DecisionResult
from backend.wave import airlines as airlines_mod
from backend.wave import baseline, fallback, moves, route, rules, surface, trajectory, unable, wind
from backend.wave import fairness as fairness_mod
from backend.wave.model import JointPlan, Move, Wave
from backend.wave.utility import (agent_terms, agent_utility, evaluate, focus_callsign, hold_burn, metrics_map,
                               negotiators, pilots)

MAX_TURNS = 3
MAX_ROUNDS = MAX_TURNS  # back-compat alias
MOVES_PER_ROUND = 2
SEVERE_MOVES_PER_ROUND = 3
SEVERE_LEVEL = 3
MAX_REJECTIONS_PER_SLOT = 2
CONCESSION = 0.12
SEVERITY_LEVELS = ["0 minor: absorbable with normal sequencing",
                   "1 moderate: some holding or gate shuffles",
                   "2 significant: coordinated resequencing needed",
                   "3 severe: runway/wind limits or long delays, diversions possible",
                   "4 critical: most runways unusable or very long delays"]
OBJECTIVES = {
    "ATC": "Keep wake separation and wind limits, spread landings across runways, minimise airborne holding.",
    "PILOT": "Keep fuel reserves, minimise your own delay and holding, protect your passengers' connections.",
    "GROUND": "Avoid gate conflicts and crew overtime, keep scheduled gates, protect next departures.",
    "AIRLINE": "Protect each of your flights: delay, holding fuel, connections and next departure; no flight worse "
               "than the original plan.",
}
PASS = "pass"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role(agent: str) -> str:
    if agent.startswith("PILOT:"):
        return "PILOT"
    if agent.startswith("AIRLINE:"):
        return "AIRLINE"
    return agent


def _code(agent: str) -> str:
    """Airline code of an AIRLINE:<code> id."""
    return agent.split(":", 1)[1]


def _plan_dict(wave: Wave, plan: JointPlan) -> dict:
    mm = metrics_map(wave, plan)
    for a in plan.assignments:
        a.metrics = mm[a.callsign]
    return plan.to_dict()


def _vkeys(checks) -> set[tuple[str, str | None]]:
    return {(c.rule, c.callsign) for c in checks if not c.passed}


def _own_flight(c, cs: str) -> bool:
    """A check belongs to flight `cs` when it names it (callsign field, or the detail text for
    multi-flight rules such as gate conflicts). The detail must name `cs` as a whole token: AA1 is not AA123."""
    return c.callsign == cs or re.search(rf"(?<![A-Za-z0-9]){re.escape(cs)}(?![A-Za-z0-9])", c.detail or "") is not None


def _counted(agent: str, checks, known: set[str], own=None) -> list:
    """Failed checks counted as 'known violations' for `agent`. A PILOT judges only its own flight and an
    AIRLINE only its own flights (they cannot fix other flights' problems by moving their own); ATC/GROUND
    count their own rules plus any rule the engine has published as vetoed. `own` (AIRLINE) is the airline's
    callsigns, passed in by hot callers."""
    role = _role(agent)
    if role == "PILOT":
        cs = agent.split(":", 1)[1]
        return [c for c in checks if not c.passed and _own_flight(c, cs)]
    if role == "AIRLINE":
        return [c for c in checks if not c.passed and any(_own_flight(c, cs) for cs in (own or ()))]
    return [c for c in checks if not c.passed and (c.owner == role or c.rule in known)]


def _known_violations(agent: str, checks, known: set[str], own=None) -> int:
    return len(_counted(agent, checks, known, own))


def _known_rules(agent: str, checks, known: set[str], own=None) -> list[str]:
    """Sorted distinct rule ids counted by _known_violations (what the agent may be told)."""
    return sorted({c.rule for c in _counted(agent, checks, known, own)})


def _divert_ok(w: Wave, mv: Move, checks) -> bool:
    """Divert guard, per move: a request_divert is allowed only if that flight fails PLT-FUEL-RESERVE in the
    plan it is made from, or no runway is open. Any other move kind is allowed."""
    if mv.kind != "request_divert":
        return True
    if not any(r.open for r in w.airport.runways):
        return True
    cs = mv.callsigns[0]
    return any(not c.passed and c.rule == "PLT-FUEL-RESERVE" and c.callsign == cs for c in checks)


def _maxmin(wave: Wave, plan: JointPlan) -> tuple[float, float]:
    us = [agent_utility(wave, plan, ag) for ag in negotiators(wave)]
    return min(us), sum(us) / len(us)


def _meta(kind: str, r: DecisionResult) -> dict:
    formula = r.provider == "formula"
    m: dict[str, Any] = {"kind": "formula" if formula else kind, "model": r.model, "provider": r.provider,
                         "latency_ms": int(r.latency_ms)}
    if not formula:
        if isinstance(r.probabilities, dict) and r.probabilities:
            m["probabilities"] = {str(k): float(v) for k, v in r.probabilities.items()
                                  if isinstance(v, (int, float))}
        if isinstance(r.confidence, (int, float)):
            m["confidence"] = float(r.confidence)
    if r.fallback_from:
        m["fallback_from"] = list(r.fallback_from)
    return m


# ---------------------------------------------------------------- private views (kept small)

def _path_line(wave: Wave, a) -> str:
    """One line on the flight's engine-chosen path (spec §6.7): STAR (or approach tier), shared hold fix and
    base, lap time and the fuel one lap burns (laps x lap_min x holding burn, as metrics_map charges)."""
    if a.action != "land" or not a.runway:
        return "no path (not landing)"
    r = route.routes_for_wave(wave).get((a.callsign, a.runway))
    if r is None:
        return f"no route geometry for {a.runway}"
    if r.star:
        head = f"STAR {r.star}.{r.transition}" if r.transition else f"STAR {r.star}"
    else:
        head = f"{r.approach or r.tier} {a.runway}"
    h = trajectory.hold_for(wave, r)
    kg = h.lap_min * hold_burn(wave.flight(a.callsign))
    return f"{head} · hold {h.fix} base {h.base_ft:,} · lap {h.lap_min:.1f} min = {kg:.0f} kg"


def _wait_clause(o) -> str:
    """Hold-short clause of one flight's ground outcome (spec §6.7): `hold short {strip} {wait:.1f} min for {cs}` for
    its longest hold-short wait (`give way {wait:.1f} min to {cs}` when it only gives way), plus the total when it has
    other waits; "" without waits."""
    if o is None or not o.modelled or not o.waits:
        return ""
    dur = lambda w: w["to_min"] - w["from_min"]
    hs = [w for w in o.waits if w.get("strip")]
    b = max(hs or o.waits, key=dur)
    txt = (f"hold short {b['strip']} {dur(b):.1f} min for {b['for']}" if b.get("strip") else
           f"give way {dur(b):.1f} min to {b['for']}")
    return txt + (f" ({o.wait_min:.1f} min waits in all)" if o.wait_min > dur(b) + 0.05 else "")


def _taxi_line(wave: Wave, a, ts=None) -> str:
    """Per-flight taxi line (spec §6.7): `{exit} → {stand} {unimpeded:.1f} min`, from the per-wave taxi table,
    plus the hold-short clause of the plan's hold-short pass (`ts`, the plan's TrajectorySet) when the flight waits."""
    if a.action != "land" or not a.runway:
        return "no taxi (not landing)"
    tx = surface.taxi_info(wave, a.runway, a.gate, wave.flight(a.callsign).gate_size)
    if tx.tier == "none":
        return f"{a.runway} → {a.gate} {tx.taxi_min:.1f} min (near/far estimate, no runway geometry)"
    stand = tx.stand if tx.stand == a.gate else f"{a.gate}/{tx.stand}"
    line = f"{tx.exit_label or a.runway + ' exit'} → {stand} {tx.taxi_min:.1f} min"
    line += " (straight-line estimate)" if tx.src == "synth" else ""
    clause = _wait_clause(ts.outcomes.get(a.callsign)) if ts is not None else ""
    return line + (f" · {clause}" if clause else "")


def _view(wave: Wave, plan: JointPlan, agent: str) -> dict:
    role = _role(agent)
    ts = trajectory.build(wave, plan)
    if role == "ATC":
        m = wave.metar
        rw = []
        for r in wave.airport.runways:
            row = {"id": r.id, "open": r.open}
            if m is not None and r.open:
                hw, xw, tw = wind.components(m.wind_dir_deg, m.wind_kt, m.gust_kt, r.heading_true)
                lxw, ltw, _ = wind.limits(m.surface, None)
                row.update({"xwind_kt": xw, "tailwind_kt": tw, "xwind_limit_kt": lxw, "tail_limit_kt": ltw})
            rw.append(row)
        return {"metar": m.raw if m else None, "runways": rw,
                "queue": [[q.callsign, q.wake, q.runway, q.slot_min] for q in wave.queue][:20],
                "wave": [[a.callsign, wave.flight(a.callsign).wake, a.action, a.runway, a.slot_min]
                         for a in plan.assignments],
                "path": {a.callsign: _path_line(wave, a) for a in plan.assignments},
                "taxi": {a.callsign: _taxi_line(wave, a, ts) for a in plan.assignments}}
    if role == "GROUND":
        mm = metrics_map(wave, plan)
        return {"gates": [[g.id, g.size, "remote" if g.remote else g.pier,
                           [[b["from_min"], b["to_min"]] for b in g.busy][:4]] for g in wave.gates][:24],
                "flights": [{"cs": a.callsign, "size": wave.flight(a.callsign).gate_size,
                             "gate": a.gate, "scheduled_gate": wave.flight(a.callsign).scheduled_gate,
                             "in_block": mm[a.callsign].in_block_min, "taxi": _taxi_line(wave, a, ts),
                             "crew_shift_end": wave.flight(a.callsign).crew_shift_end_min,
                             "next_dep": wave.flight(a.callsign).next_departure_min}
                            for a in plan.assignments]}
    mm = metrics_map(wave, plan)
    if role == "AIRLINE":
        own = airlines_mod.groups(wave).get(_code(agent), [])
        return {"airline": _code(agent), "flights": [_flight_view(wave, plan, cs, ts, mm) for cs in own]}
    return _flight_view(wave, plan, agent.split(":", 1)[1], ts, mm)


def _flight_view(wave: Wave, plan: JointPlan, cs: str, ts, mm) -> dict:
    """One flight's private view (a PILOT's view; an AIRLINE sees one per own flight)."""
    f, a = wave.flight(cs), plan.get(cs)
    m = mm[cs]
    # §6.1: BTS scheduled arrival is a gate (in-block) time; eta_earliest_min is the earliest touchdown
    return {"flight": cs, "type": f.aircraft_type, "airborne": f.airborne, "earliest_touchdown": f.eta_earliest_min,
            "sched_in_block": f.sched_min, "fuel": f.fuel, "connections": f.connections[:5],
            "assignment": {"action": a.action, "runway": a.runway, "slot": a.slot_min, "gate": a.gate,
                           "speed_reduction": a.speed_reduction_min, "ground_delay": a.ground_delay_min},
            "path": _path_line(wave, a), "taxi": _taxi_line(wave, a, ts),
            "metrics": {"hold": m.hold_min, "fuel_kg": m.fuel_kg, "delay": m.delay_min,
                        "pax_protected": m.pax_protected, "pax_total": m.pax_total}}


# ---------------------------------------------------------------- run context

class _Run:
    def __init__(self, wave: Wave, mode: str, chain: list[str] | None,
                 on_message: Callable[[dict], None] | None, run_id: str, fairness: str | None = None):
        self.wave, self.mode, self.run_id = wave, mode, run_id
        self.fairness = fairness or fairness_mod.current()
        if self.fairness not in fairness_mod.VARIANTS:
            raise ValueError(f"unknown fairness variant {self.fairness!r}; expected one of {fairness_mod.VARIANTS}")
        self.chain = [] if mode == "formulas" else list(chain if chain is not None else DEFAULT_CHAIN)
        self.on_message = on_message
        self.log: list[dict] = []
        self.model_calls = 0
        self.fallbacks = 0
        self.model_errors: list[str] = []
        self.known_rules: set[str] = set()
        self.agents = negotiators(wave)              # the voters: ATC, AIRLINE:* (sorted), GROUND
        self.airlines = self.agents[1:-1]
        self.pilots = pilots(wave)
        self.own = {ag: list(airlines_mod.groups(wave).get(_code(ag), [])) for ag in self.airlines}
        self.focus = focus_callsign(wave)
        self.sq = baseline.status_quo(wave)
        self.sq_ok = rules.feasible(wave, self.sq)
        self.floor_best: dict[str, JointPlan] = {}   # airline code -> best feasible plan seen, by its utility
        self.floor_u: dict[str, float] = {}
        self.proposed_by: dict[str, str] = {}        # plan id -> first voter to put it on the table (ENGINE: OPEN)
        self.unable_plans: set[str] = set()          # plan ids a pilot sent UNABLE on (cannot win)
        self.unable_seen: set[tuple] = set()
        self._breach_cache: dict[tuple, list[dict]] = {}
        self.best_seen: dict[str, float] = {ag: 0.0 for ag in self.agents}
        self.max_moves = MOVES_PER_ROUND
        self.votes: dict[str, list[dict]] = {}
        self.lock = threading.Lock()
        self.t0 = time.monotonic()

    # messages
    def msg(self, rnd: int, sender: str, body: dict, rationale: str, decision: dict | None = None) -> dict:
        m = {"round": rnd, "sender": sender, "type": body["type"], "body": body,
             "rationale": rationale}
        if decision is not None:
            m["decision"] = decision
        return m

    def emit(self, m: dict) -> None:
        m = {"seq": len(self.log), "round": m["round"], "ts": _now(), **m}  # ts stamped at emission
        self.log.append(m)
        if self.on_message is not None:
            self.on_message(m)

    def decider(self, agent: str, rnd: int, buf: list[dict]) -> Decider:
        """One Decider per agent so MODEL_FALLBACK names the agent; messages go to `buf`."""
        def hook(entry: str, nxt: str, err: str) -> None:
            buf.append(self.msg(rnd, agent, {"type": "MODEL_FALLBACK", "agent": agent, "from_model": entry,
                                             "to_model": nxt, "error": str(err)[:300]},
                                f"{entry} failed for {agent}; falling back to {nxt}"))
        return Decider(self.chain, on_fallback=hook)

    def account(self, r: DecisionResult, calls: int = 1) -> None:
        with self.lock:  # called from agent worker threads
            if r.provider != "formula":
                self.model_calls += calls
            self.fallbacks += len(r.fallback_from)
            self.model_errors.extend(r.errors)

    def reservation(self, agent: str, rnd: int) -> float:
        return round(self.best_seen[agent] * (0.9 - CONCESSION * (rnd - 1)), 3)

    def see(self, plan: JointPlan) -> None:
        """Anchor reservations on feasible plans only (an unsafe plan is not a real option)."""
        if not rules.feasible(self.wave, plan):
            return
        for ag in self.best_seen:
            self.best_seen[ag] = max(self.best_seen[ag], agent_utility(self.wave, plan, ag))
        for ag in self.airlines:   # best-seen floor, used only when the status quo is infeasible
            code, u = _code(ag), agent_utility(self.wave, plan, ag)
            if code not in self.floor_best or u > self.floor_u[code] + 1e-9:
                self.floor_best[code], self.floor_u[code] = plan, u

    def floor(self, agent: str) -> tuple[JointPlan | None, str]:
        """(floor plan, floor source) of AIRLINE `agent` now: the status quo when feasible, else the best
        feasible plan it has seen (None before any)."""
        code = _code(agent)
        fp = fairness_mod.floor_source(self.wave, self.sq, self.sq_ok, self.floor_best.get(code))
        return fp, "status_quo" if self.sq_ok else "best_seen"

    def breaches(self, plan: JointPlan, agent: str, rnd: int) -> list[dict]:
        """Fairness-floor breaches of AIRLINE `agent`'s flights in `plan` at turn `rnd` (1..3)."""
        fp, src = self.floor(agent)
        key = (plan.id, agent, rnd, fp.id if fp is not None else None)
        if key not in self._breach_cache:
            self._breach_cache[key] = fairness_mod.breaches(self.wave, plan, _code(agent), fp, self.fairness, rnd,
                                                            source=src)
        return self._breach_cache[key]


ACCEPT_AT = 0.7     # model P(accept) at or above: ACCEPT
ACCEPT_BELOW = 0.3  # at or below: REJECT; in between the formula decides

class S(TypedDict, total=False):
    ctx: Any
    round: int                  # current turn (1..3)
    table: Any                  # incumbent JointPlan
    proposals: list             # [(agent, JointPlan)] this turn, in speaking order
    checked: dict               # plan_id -> (JointPlan, passed) for plans on the table this turn
    ballots: dict               # agent -> votes this turn
    fallback_reason: str
    route: str
    final: Any                  # JointPlan
    outcome: str
    method: str


# ---------------------------------------------------------------- nodes

def _triage(st: S) -> dict:
    run: _Run = st["ctx"]
    w = run.wave
    worst_delay = max((max(float(f.delays.get("weather", 0) or 0), float(f.delays.get("late_aircraft", 0) or 0))
                       for f in w.flights), default=0.0)
    m = w.metar
    usable, total, max_gust = 0, 0, 0.0
    if m is not None:
        max_gust = float(max(m.wind_kt, m.gust_kt or 0))
        for r in w.airport.runways:
            if not r.open:
                continue
            total += 1
            hw, xw, tw = wind.components(m.wind_dir_deg, m.wind_kt, m.gust_kt, r.heading_true)
            lxw, ltw, _ = wind.limits(m.surface, None)
            usable += wind.within_limits(xw, tw, lxw, ltw)
    vis = m.visibility_sm if m is not None else None

    def formula() -> int:
        d = 0 if worst_delay < 15 else 1 if worst_delay < 30 else 2 if worst_delay < 60 else 3 if worst_delay < 120 else 4
        wx = 0
        if total and usable == 0:
            wx = 4
        elif total and usable < total / 2:
            wx = 3
        elif max_gust >= 30:
            wx = 2
        elif max_gust >= 20:
            wx = 1
        if vis is not None and vis < 3:
            wx = min(4, wx + 1)
        return int(max(d, wx))

    state = {"airport": w.airport.icao, "metar": m.raw if m else None, "runways_open": total,
             "runways_within_wind_limits": usable, "visibility_sm": vis, "worst_flight_delay_min": worst_delay,
             "flights": len(w.flights), "disrupted": sum(f.disrupted for f in w.flights),
             "queue": len(w.queue)}
    buf: list[dict] = []
    d = run.decider("DECIDER", 0, buf)  # triage has no agent; MODEL_FALLBACK is attributed to DECIDER
    r = d.score(state, "Rate the operational severity of this arrival-wave disruption at the hub.",
                SEVERITY_LEVELS, formula)
    run.account(r)
    for b in buf:
        run.emit(b)
    score = int(r.value)
    probs = [0.0] * len(SEVERITY_LEVELS)
    p = r.probabilities
    if isinstance(p, dict) and p:
        for i, lv in enumerate(SEVERITY_LEVELS):
            v = p.get(lv, p.get(str(i), p.get(i, 0.0)))
            probs[i] = float(v) if isinstance(v, (int, float)) else 0.0
    elif isinstance(p, (list, tuple)) and len(p) == len(SEVERITY_LEVELS):
        probs = [float(x) for x in p]
    if not any(probs):
        probs[score] = 1.0
    body = {"type": "TRIAGE", "score": score, "levels": SEVERITY_LEVELS, "probabilities": probs}
    if isinstance(r.confidence, (int, float)):
        body["confidence"] = float(r.confidence)
    if score >= SEVERE_LEVEL:
        run.max_moves = SEVERE_MOVES_PER_ROUND
    run.emit(run.msg(0, "DECIDER", body,
                     f"Severity {score}/4 (worst delay {worst_delay:g} min, {usable}/{total} runways within "
                     f"wind limits); up to {run.max_moves} moves per agent per turn", _meta("score", r)))
    return {}


def _open(st: S) -> dict:
    run: _Run = st["ctx"]
    plan = moves.fcfs_plan(run.wave)
    plan.label = ""  # label = per-flight description; authorship lives in sender/rationale
    ev = evaluate(run.wave, plan)
    run.see(plan)
    run.known_rules |= {c["rule"] for c in ev["checks"] if not c["passed"]}
    nv = sum(not c["passed"] for c in ev["checks"])
    run.proposed_by.setdefault(plan.id, "ENGINE")
    run.emit(run.msg(0, "ENGINE", {"type": "OPEN", "plan": plan.to_dict(), "evaluation": ev,
                                   "agents": run.agents, "pilots": run.pilots,
                                   "airlines": airlines_mod.describe(run.wave), "focus": run.focus},
                     f"Opening plan: first come first served from the schedule; {nv} rule violation(s) on the "
                     f"table; agents {', '.join(run.agents)}; {len(run.pilots)} pilot(s)"))
    _unables(run, 0, "ENGINE", [], plan, rules.violations(run.wave, plan))
    return {"round": 1, "table": plan}


def _unables(run: _Run, rnd: int, proposer: str, rejected: list[dict], plan: JointPlan | None, viol) -> None:
    """Emit the pilots' UNABLEs: one per pilot-side violation (`unable.pilot_side`) of each rejected move
    (instruction = the move, plan_id = the post-move plan) and of `plan` (a new proposal or the opening plan;
    `viol` = its failed checks, None when it was not CHECKed now). Deduped on (plan id, move id, callsign, rule):
    the same refusal of the same instruction on the same plan is said once. Every refused plan id goes into
    `run.unable_plans` (it cannot win). Pure formula: pilots make no model calls."""
    items = [(x["plan_id"], x["move"], c) for x in rejected for c in x["violations"]]
    if plan is not None and viol:
        items += [(plan.id, None, c) for c in viol]
    for pid, mv, c in items:
        if not unable.pilot_side(c):   # unable_body does not re-check
            continue
        body = unable.unable_body(rnd, c, mv, pid, proposer)
        run.unable_plans.add(pid)
        key = (pid, mv["id"] if mv else None, body["callsign"], body["rule"])
        if key in run.unable_seen:
            continue
        run.unable_seen.add(key)
        run.emit(run.msg(rnd, body["agent"], body, body["text"][:400]))


def _fair_words(br: list[dict]) -> str:
    """An airline option's fairness, in words (the engine computed the breaches; the model only reads them)."""
    if not br:
        return "keeps every flight within its floor"
    parts = [f"{b['callsign']} +{b['value'] - b['floor']:.1f} min over its floor" if b["metric"] == "delay_min"
             else f"{b['callsign']} +{b['value'] - b['floor']:.0f} kg fuel over its floor" for b in br[:3]]
    return ", ".join(parts) + (f" (+{len(br) - 3} more)" if len(br) > 3 else "")


def _agent_moves(run: _Run, agent: str, rnd: int, table: JointPlan, known: set[str]
                 ) -> tuple[list[dict], JointPlan, list[dict], list[dict]]:
    """The agent's moves for this turn. Returns (MODEL_FALLBACK messages, resulting plan,
    applied [{move, utility_before, utility_after, decision}], rejected [{move, violations, reason, plan_id,
    decision}], plan_id = the post-move plan the engine refused)."""
    w = run.wave
    buf: list[dict] = []
    applied: list[dict] = []
    rejected: list[dict] = []
    dec = run.decider(agent, rnd, buf)
    airline = _role(agent) == "AIRLINE"
    own = run.own.get(agent)
    nb = (lambda p: len(run.breaches(p, agent, rnd))) if airline else (lambda p: 0)
    plan = table
    for _slot in range(run.max_moves):
        opts = moves.legal_moves(w, plan, agent)
        before_checks = rules.check(w, plan)
        before_v = _vkeys(before_checks)
        u_now = agent_utility(w, plan, agent)
        k_now = _known_violations(agent, before_checks, known, own)
        b_now = nb(plan)
        info = {}
        for i, mv in enumerate(opts):
            after = moves.apply(w, plan, mv)
            chk = rules.check(w, after)
            info[f"m{i + 1}"] = (mv, after, chk, agent_utility(w, after, agent),
                                 _known_violations(agent, chk, known, own),
                                 run.breaches(after, agent, rnd) if airline else [])
        # option rank: (known violations, [airline: floor breaches at this turn's slack,] -own utility, key)
        rank = lambda v, k: (v[4], len(v[5]), -v[3], k)
        excluded: set[str] = set()
        moved = False
        stop = False
        for _attempt in range(MAX_REJECTIONS_PER_SLOT):  # each failed attempt is one rejection
            avail = {k: v for k, v in info.items() if k not in excluded}
            # a flight may divert only for its own fuel problem or when every runway is closed (`_divert_ok`): the
            # model is offered exactly the moves the formula may take
            avail = {k: v for k, v in avail.items() if _divert_ok(w, v[0], before_checks)} or avail
            if not avail:
                stop = True
                break
            # System One models judge, code does the maths: every comparison is pre-computed into words
            ranked = sorted(avail, key=lambda k: rank(avail[k], k))
            how = ("fewest rule breaches on your flights, then fewest fairness-floor breaches, then best for you"
                   if airline else "fewest rule breaches, then best for you")
            options = {}
            for k, v in avail.items():
                o = {"move": v[0].label,
                     "rule_breaches": _breach_words(v[4], k_now, _known_rules(agent, v[2], known, own)),
                     "for_you": _gain_words(v[3] - u_now)}
                if airline:
                    o["fairness"] = _fair_words(v[5])
                o["engine_rank"] = f"{ranked.index(k) + 1} of {len(ranked)} ({how})"
                options[k] = o
            has_accept = any(v[0].kind == "accept" for v in avail.values())
            if not has_accept:
                options[PASS] = {"move": "Make no further move this turn", "rule_breaches": _breach_words(k_now, k_now, []),
                                 "for_you": "no change for you", "engine_rank": "keep the plan as it is"}
                if airline:
                    options[PASS]["fairness"] = _fair_words(run.breaches(plan, agent, rnd))

            def formula(avail=avail, has_accept=has_accept) -> str:
                best = min(avail, key=lambda k: rank(avail[k], k))
                b = avail[best]
                # strict lexicographic improvement over the plan as it is (utility with a 1e-9 tolerance)
                better = b[4] < k_now or (b[4] == k_now and (len(b[5]) < b_now or
                                                              (len(b[5]) == b_now and b[3] > u_now + 1e-9)))
                if b[0].kind != "accept" and better:
                    return best
                if has_accept:
                    return next(k for k, v in avail.items() if v[0].kind == "accept")
                return PASS

            state = {"role": _role(agent), "agent": agent, "turn": rnd, "turns_left": MAX_TURNS - rnd,
                     "objective": OBJECTIVES[_role(agent)],
                     "view": _view(w, plan, agent),
                     "rules_published_by_engine": sorted(known),
                     "rule_breaches_on_the_table": _breach_words(k_now, k_now, []),
                     "guidance": "Fixing rule breaches comes first; then "
                                 + ("keep each of your flights within its fairness floor; then " if airline else "")
                                 + "pick what is better for you. Make no further move if nothing is better."}
            r = dec.choice(state, f"You are {agent}. Pick ONE move for your own authority, or make no further move. "
                                  "Each option's `rule_breaches` and `for_you` were computed by the safety engine; "
                                  "trust them, do not recompute.",
                           options, formula)
            run.account(r)
            pick = r.value
            if pick == PASS:
                stop = True
                break
            mv, after, chk, u_after = avail[pick][:4]
            if mv.kind == "accept":
                stop = True  # accepting is a vote, not a move: the turn's moves end here
                break
            new_v = [c for c in chk if not c.passed and (c.rule, c.callsign) not in before_v]
            if new_v:
                excluded.add(pick)
                known |= {c.rule for c in new_v}
                with run.lock:
                    run.known_rules |= {c.rule for c in new_v}
                rejected.append({"move": mv.to_dict(), "violations": [c.to_dict() for c in new_v],
                                 "reason": f"{new_v[0].rule}: {new_v[0].detail}"[:300], "plan_id": after.id,
                                 "decision": _choice_meta(r, info)})
                continue
            applied.append({"move": mv.to_dict(), "utility_before": u_now, "utility_after": u_after,
                            "decision": _choice_meta(r, info)})
            plan = after
            moved = True
            break
        if stop or not moved:
            break
    return buf, plan, applied, rejected


def _choice_meta(r: DecisionResult, info: dict) -> dict:
    """DecisionMeta for a Choice, with probabilities keyed by Move.id (not the internal m1..m12 keys)."""
    m = _meta("choice", r)
    if "probabilities" in m:
        m["probabilities"] = {(info[k][0].id if k in info else k): v for k, v in m["probabilities"].items()}
    return m


def _gain_words(d: float) -> str:
    if d > 0.02:
        return "clearly better for you"
    if d > 1e-9:
        return "slightly better for you"
    if d > -1e-9:
        return "no change for you"
    return "slightly worse for you" if d > -0.02 else "clearly worse for you"


def _meets_words(margin: float) -> str:
    if margin >= 0.05:
        return "yes, clearly"
    if margin >= 0:
        return "yes, just"
    return "no, just short" if margin > -0.05 else "no, well short"


def _breach_words(after: int, now: int, rules_after: list[str]) -> str:
    tail = f" ({', '.join(rules_after)})" if after and rules_after else ""
    if after < now:
        return f"fixes {now - after} rule breach{'es' if now - after > 1 else ''}; {after} left{tail}"
    if after > now:
        return f"adds {after - now} rule breach{'es' if after - now > 1 else ''}; {after} in total{tail}"
    return f"no rule breaches" if after == 0 else f"rule breaches unchanged at {after}{tail}"


def _floor_reason(b: dict, variant: str) -> str:
    src = "status quo" if b["floor_source"] == "status_quo" else "best plan seen"
    return (f"{b['callsign']} {b['metric']} {b['value']:g} > {b['allowed']:g} "
            f"({variant} fairness floor {b['floor']:g}, {src})")


def _votes(run: _Run, agent: str, rnd: int, plans: list[JointPlan]) -> tuple[list[dict], list[dict], dict | None]:
    """(MODEL_FALLBACK messages, votes, DecisionMeta) for `agent` on the CHECK-passed `plans`.
    An AIRLINE first applies its fairness floor: when some plan in `plans` has no breach, every plan with a
    breach is REJECTed by the floor (`decided_by: "floor"`) and is not sent to the model."""
    w = run.wave
    buf: list[dict] = []
    res = run.reservation(agent, rnd)
    if not plans:
        return buf, [], None
    us = {p.id: agent_utility(w, p, agent) for p in plans}
    floor_votes: dict[str, dict] = {}
    escape: dict[str, list[dict]] = {}   # plan id -> its breaches, when no plan here meets the floor (escape clause)
    if _role(agent) == "AIRLINE":
        br = {p.id: run.breaches(p, agent, rnd) for p in plans}
        if any(not b for b in br.values()):
            for p in plans:
                if br[p.id]:
                    floor_votes[p.id] = {"plan_id": p.id, "vote": "REJECT", "utility": us[p.id], "reservation": res,
                                         "decided_by": "floor", "floor": [dict(b) for b in br[p.id]],
                                         "reason": _floor_reason(br[p.id][0], run.fairness)}
        else:
            escape = {pid: b for pid, b in br.items() if b}
    asked = [p for p in plans if p.id not in floor_votes]
    # code compares utility with the reservation; the model only judges whether to accept, given the verdict
    best_u = max(us[p.id] for p in asked)
    items = {p.id: {"question": f"As {agent}, do you accept plan {p.id}?",
                    "plan": p.describe()[:160],
                    "passes_every_safety_rule": "yes (engine CHECK passed)",
                    "meets_your_minimum_this_turn": _meets_words(us[p.id] - res),
                    "for_you_vs_best_plan_on_the_table": "this is the best plan for you"
                    if us[p.id] >= best_u - 1e-9 else _gain_words(us[p.id] - best_u)}
             for p in asked}
    state = {"role": _role(agent), "agent": agent, "turn": rnd, "turns_left": MAX_TURNS - rnd,
             "objective": OBJECTIVES[_role(agent)],
             "note": f"If no plan is accepted by all {len(run.agents)} agents within {MAX_TURNS} turns, a "
                     "deterministic solver decides for everyone; your minimum drops each turn."}
    criteria = {"true": "Accept: the plan meets your minimum this turn (`meets_your_minimum_this_turn` starts "
                        "with yes). Safety is already guaranteed by the engine.",
                "false": "Reject: `meets_your_minimum_this_turn` starts with no. Not a reason to reject: the "
                         "plan is only slightly worse for you than another plan but still meets your minimum."}
    rs = run.decider(agent, rnd, buf).noul_batch(state, items, lambda pid: 1.0 if us[pid] >= res else 0.0,
                                                 criteria=criteria)
    first = next(iter(rs.values()))
    run.account(first)
    votes = []
    for p in plans:
        if p.id in floor_votes:
            votes.append(floor_votes[p.id])
            continue
        r = rs[p.id]
        pa = float(r.value)
        formula_acc = us[p.id] >= res
        # TypeSafe confidence routing: act on a confident model answer, defer the uncertain band to the formula
        uncertain = r.provider != "formula" and ACCEPT_BELOW < pa < ACCEPT_AT
        acc = formula_acc if (r.provider == "formula" or uncertain) else pa >= ACCEPT_AT
        v = {"plan_id": p.id, "vote": "ACCEPT" if acc else "REJECT", "utility": us[p.id], "reservation": res}
        if run.mode != "formulas":
            v["p_accept"] = round(pa, 3)
        if r.provider == "formula":
            v["reason"] = f"utility {us[p.id]:.3f} {'>=' if acc else '<'} reservation {res:.3f}"
        elif uncertain:
            v["decided_by"] = "formula"
            v["reason"] = (f"{r.model}: P(accept)={pa:.2f} is uncertain ({ACCEPT_BELOW:g}-{ACCEPT_AT:g}); formula decides: "
                           f"utility {us[p.id]:.3f} {'>=' if acc else '<'} reservation {res:.3f}")
        else:
            v["reason"] = f"{r.model}: P(accept)={pa:.2f} (utility {us[p.id]:.3f}, reservation {res:.3f})"
        if p.id in escape:
            # firm escape clause: the plan breaches the floor but no plan on the table meets it, so the vote is on
            # utility; "escape" replaces "formula" (the reason still says when the formula decided)
            v["decided_by"] = "escape"
            v["floor"] = [dict(b) for b in escape[p.id]]
            v["reason"] = ("escape clause (no plan on the table meets the floor, "
                           f"{_floor_reason(escape[p.id][0], run.fairness)}): " + v["reason"])
        votes.append(v)
    return buf, votes, _meta("noul", first)


def _objection_words(objections: list[dict], applied: list[dict], proposal_breaches: list[dict],
                     votes: list[dict]) -> str:
    """`objects: <cs> <metric> +<over the floor> vs <floor source> on <plan>; <tail>`. The tail is the counter-offer
    (own moves whose plan meets the floor) or, from the airline's own vote on each objected plan: "rejects the plan"
    / "rejects <id>" when it voted REJECT, "accepts it/<id>: no plan on the table meets its floor (escape clause)"
    when it voted ACCEPT under the escape clause."""
    b = objections[0]
    d = b["value"] - b["floor"]
    over = f"+{d:.1f} min" if b["metric"] == "delay_min" else f"+{d:.0f} kg"
    metric = "delay" if b["metric"] == "delay_min" else "fuel"
    src = "the original plan" if b["floor_source"] == "status_quo" else "the best plan so far"
    more = f" (+{len(objections) - 1} more)" if len(objections) > 1 else ""
    if applied and not proposal_breaches:
        tail = "counter-offer: " + "; ".join(x["move"]["label"] for x in applied)
    else:
        pids = list(dict.fromkeys(o["plan_id"] for o in objections))
        vote = {v["plan_id"]: v for v in votes}
        one = len(pids) == 1
        parts = []
        for pid in pids:
            v = vote.get(pid)
            if v is not None and v["vote"] == "ACCEPT":
                parts.append(f"accepts {'it' if one else pid}: no plan on the table meets its floor (escape clause)")
            else:
                parts.append("rejects the plan" if one else f"rejects {pid}")
        tail = "; ".join(parts)
    return f"objects: {b['callsign']} {metric} {over} vs {src} on {b['plan_id']}{more}; {tail}"


def _speak(st: S, agent: str) -> dict:
    """One voter's turn: moves from the latest plan on the table, one AGENT_TURN message, the pilots' UNABLEs,
    engine CHECK."""
    run: _Run = st["ctx"]
    w = run.wave
    rnd = st["round"]
    table: JointPlan = st["table"]
    props: list = list(st.get("proposals") or [])
    checked: dict = dict(st.get("checked") or {})
    if table.id not in checked:
        checked[table.id] = (table, not rules.violations(w, table))
    start = props[-1][1] if props else table
    buf, plan, applied, rejected = _agent_moves(run, agent, rnd, start, set(run.known_rules))
    for m in buf:
        run.emit(m)
    proposal = JointPlan(plan.assignments, "")
    run.proposed_by.setdefault(proposal.id, agent)
    run.see(proposal)
    new = proposal.id not in checked
    viol = rules.violations(w, proposal) if new else None
    if new:
        checked[proposal.id] = (proposal, not viol)
        run.known_rules |= {c.rule for c in viol}
    # vote on every CHECK-passed plan on the table now: incumbent, earlier proposals this turn, own proposal
    seen, on_table = set(), []
    for p in [table] + [q for _, q in props] + [proposal]:
        if p.id not in seen:
            seen.add(p.id)
            on_table.append(p)
    valid = [p for p in on_table if checked[p.id][1]]
    vbuf, votes, vmeta = _votes(run, agent, rnd, valid)
    for m in vbuf:
        run.emit(m)
    u = agent_utility(w, proposal, agent)
    n_acc = sum(v["vote"] == "ACCEPT" for v in votes)
    if applied:
        what = "; ".join(x["move"]["label"] for x in applied)
    else:
        what = "no move (keeps the plan on the table)"
    rationale = (f"Turn {rnd}: {what}; {len(rejected)} move(s) vetoed by the engine; own utility {u:.3f}; "
                 f"accepts {n_acc}/{len(votes)} valid plan(s) at reservation {run.reservation(agent, rnd):.3f}")
    body = {"type": "AGENT_TURN", "turn": rnd, "agent": agent, "role": _role(agent),
            "moves": applied, "rejected": rejected,
            "proposal": _plan_dict(w, proposal), "proposal_utility": u,
            "votes": votes, "rationale": rationale}
    if _role(agent) == "AIRLINE":
        # the breaches of each CHECK-passed plan another voter (or the engine) put on the table
        objections = [{**b, "plan_id": p.id, "proposed_by": run.proposed_by.get(p.id, "ENGINE")}
                      for p in valid if run.proposed_by.get(p.id) != agent
                      for b in run.breaches(p, agent, rnd)]
        body["objections"] = objections
        if objections:
            pb = run.breaches(proposal, agent, rnd) if checked[proposal.id][1] else []
            rationale += "; " + _objection_words(objections, applied, pb, votes)
            body["rationale"] = rationale
    if vmeta is not None:
        body["decision"] = vmeta
    run.emit(run.msg(rnd, agent, body, rationale, vmeta))
    _unables(run, rnd, agent, rejected, proposal if new else None, viol)
    if new:
        nchk = len(rules.check(w, proposal))
        run.emit(run.msg(rnd, "ENGINE", {"type": "CHECK", "plan_id": proposal.id, "passed": not viol,
                                         "proposed_by": agent, "violations": [c.to_dict() for c in viol]},
                         f"{agent}'s proposal passes all {nchk} checks" if not viol else
                         f"{agent}'s proposal fails {len(viol)} check(s): " + ", ".join(sorted({c.rule for c in viol}))))
    ballots = dict(st.get("ballots") or {})
    ballots[agent] = votes
    props.append((agent, proposal))
    return {"proposals": props, "checked": checked, "ballots": ballots}


def _atc(st: S) -> dict:
    return _speak({**st, "proposals": [], "checked": {}, "ballots": {}}, "ATC")


def _airlines(st: S) -> dict:
    """Every airline voter speaks in turn (alphabetical), inside one node: each starts from the previous
    proposal, exactly as separate nodes would chain; the state deltas are merged."""
    cur, out = dict(st), {}
    for ag in st["ctx"].airlines:
        d = _speak(cur, ag)
        cur.update(d)
        out.update(d)
    return out


def _ground(st: S) -> dict:
    return _speak(st, "GROUND")


def _no_consensus_reason(run: _Run, rnd: int, checked: dict, ballots: dict) -> str:
    valid = [pid for pid, (_, ok) in checked.items() if ok]
    if not valid:
        return f"turn {rnd}: no plan on the table passed the engine CHECK"
    parts = []
    for pid in valid:
        rej = []
        for ag in run.agents:
            for v in ballots.get(ag, []):
                if v["plan_id"] != pid or v["vote"] != "REJECT":
                    continue
                if v.get("decided_by") == "floor":
                    rej.append(f"{ag} (fairness floor: {v['reason']})")
                elif v["utility"] < v["reservation"]:
                    rej.append(f"{ag} (utility {v['utility']:.3f} < reservation {v['reservation']:.3f})")
                else:
                    rej.append(f"{ag} ({v.get('reason', 'rejected')})")
        missing = [ag for ag in run.agents if not any(v["plan_id"] == pid for v in ballots.get(ag, []))]
        why = []
        if pid in run.unable_plans:
            why.append("refused by a pilot (UNABLE)")
        if rej:
            why.append("rejected by " + ", ".join(rej))
        if missing:
            why.append("not yet on the table when " + ", ".join(missing) + " spoke")
        parts.append(f"plan {pid}: " + "; ".join(why))
    return f"turn {rnd}: no CHECK-passed plan accepted by all {len(run.agents)} agents (" + " | ".join(parts) + ")"


def _escape_guard(run: _Run, rnd: int, valid: list[JointPlan], unanimous: list[JointPlan]) -> list[JointPlan]:
    """Firm escape clause, judged at the end of the turn: an airline voted on utility (escape) only if no
    floor-meeting plan was on the table when it spoke. A later voter (e.g. GROUND) may still put one on the table
    in the same turn; then a plan that breaches that airline's floor cannot win this turn. The floor-meeting plan
    is not declared here (the airline never voted on it); the incumbent rule carries it into turn t+1."""
    out = []
    for p in unanimous:
        blocked = any(run.breaches(p, ag, rnd) and any(not run.breaches(q, ag, rnd) for q in valid if q.id != p.id)
                      for ag in run.airlines)
        if not blocked:
            out.append(p)
    return out


def _decide_turn(st: S) -> dict:
    run: _Run = st["ctx"]
    rnd = st["round"]
    w = run.wave
    agents = run.agents
    checked, ballots = st["checked"], st["ballots"]
    valid = [p for p, ok in checked.values() if ok]
    unanimous = [p for p in valid if p.id not in run.unable_plans
                 and all(any(v["plan_id"] == p.id and v["vote"] == "ACCEPT" for v in ballots.get(ag, []))
                         for ag in agents)]
    unanimous = _escape_guard(run, rnd, valid, unanimous)
    if unanimous:
        best = max(unanimous, key=lambda p: (*_maxmin(w, p), p.id))
        if rules.feasible(w, best):
            run.emit(run.msg(rnd, "ENGINE", {"type": "CONSENSUS", "plan_id": best.id, "turn": rnd,
                                             "accepted_by": agents},
                             f"Turn {rnd}: all {len(agents)} agents accept {best.id}; max-min utility "
                             f"{_maxmin(w, best)[0]:.3f} among {len(unanimous)} unanimous plan(s)"))
            return {"route": "finalize", "final": best, "outcome": "consensus", "method": "consensus"}
    reason = _no_consensus_reason(run, rnd, checked, ballots)
    last = rnd >= MAX_TURNS
    nxt = {ag: run.reservation(ag, rnd if last else rnd + 1) for ag in agents}
    run.emit(run.msg(rnd, "ENGINE", {"type": "NO_CONSENSUS", "turn": rnd, "valid_plan_ids": [p.id for p in valid],
                                     "next_reservation": nxt, "reason": reason},
                     (reason + ("; turn limit reached" if last else "; agents concede"))[:400]))
    if last:
        return {"route": "fallback", "fallback_reason": reason}
    # incumbent for the next turn: fewest floor breaches over the airlines at the next turn's slack, then max-min
    # (a floor-meeting plan stays on the table while one exists; an airline cannot move a flight back alone)
    if valid:
        def key(p):
            nb = sum(len(run.breaches(p, ag, rnd + 1)) for ag in run.airlines)
            return (-nb, *_maxmin(w, p), p.id)
        table = max(valid, key=key)
    else:
        plans = [p for _, p in st["proposals"]]
        table = min(plans, key=lambda p: (len(rules.violations(w, p)), -_maxmin(w, p)[0], p.id))
    return {"route": "next", "round": rnd + 1, "table": table}


def _fallback(st: S) -> dict:
    run: _Run = st["ctx"]
    plan, method = fallback.solve(run.wave)
    if st.get("outcome") == "unsafe":
        reason = "the agreed plan failed the final safety check"
    else:
        reason = f"No consensus within {MAX_TURNS} turns: " + (st.get("fallback_reason") or "no unanimous plan")
    run.emit(run.msg(min(st.get("round", MAX_TURNS), MAX_TURNS), "ENGINE",
                     {"type": "FALLBACK", "plan_id": plan.id, "method": method, "reason": reason},
                     f"Deterministic solver (no AI) decides: {method}. Why: {reason}"[:500]))
    return {"final": plan, "outcome": "fallback", "method": method}


def _finalize(st: S) -> dict:
    run: _Run = st["ctx"]
    plan = st["final"]
    if rules.feasible(run.wave, plan):
        return {}
    if st.get("outcome") == "consensus":  # agreed plan failed the final check: solver decides instead
        out = _fallback({**st, "outcome": "unsafe"})
        if rules.feasible(run.wave, out["final"]):
            return out
    raise RuntimeError("no feasible plan exists for this wave")


def build_graph():
    g = StateGraph(S)
    for name, fn in [("triage", _triage), ("open", _open), ("atc", _atc), ("airlines", _airlines),
                     ("ground", _ground), ("decide_turn", _decide_turn), ("fallback", _fallback),
                     ("finalize", _finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "triage")
    g.add_edge("triage", "open")
    g.add_edge("open", "atc")
    g.add_edge("atc", "airlines")
    g.add_edge("airlines", "ground")
    g.add_edge("ground", "decide_turn")
    g.add_conditional_edges("decide_turn", lambda st: st["route"],
                            {"finalize": "finalize", "next": "atc", "fallback": "fallback"})
    g.add_edge("fallback", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def _hhmm(window_start: str, minutes: float) -> str:
    try:
        t0 = datetime.fromisoformat(window_start)
    except (TypeError, ValueError):
        return f"T{minutes:+g}"
    from datetime import timedelta
    return (t0 + timedelta(minutes=float(minutes))).strftime("%H:%M")


def revision(wave: Wave, plan: JointPlan, focus: str) -> dict:
    """Challenge output for the disruption flight: revised arrival time, runway, gate and action.
    delay_vs_schedule_min = the metrics delay: in-block - scheduled arrival (BTS gate time, spec §6.1)."""
    a = plan.get(focus)
    f = wave.flight(focus)
    m = metrics_map(wave, plan)[focus]
    if a.action == "divert":
        alt = (f.fuel or {}).get("alternate")
        return {"callsign": focus, "action": "divert", "alternate": alt, "revised_arrival": None,
                "revised_arrival_min": None, "runway": None, "gate": None,
                "scheduled_arrival": _hhmm(wave.window_start, f.sched_min), "scheduled_gate": f.scheduled_gate,
                "delay_vs_schedule_min": m.delay_min,
                "summary": f"{focus} diverts to {alt or 'its alternate'} (delay ~{m.delay_min:g} min)"}
    action = "ground_delay" if a.ground_delay_min > 0 else "hold" if m.hold_min > 0 else "land"
    slot = float(a.slot_min)
    d = m.delay_min   # §6.1: in-block vs the scheduled gate time (not landing slot - sched: mixed time bases)
    hhmm = _hhmm(wave.window_start, slot)
    extra = (f", ground delay {a.ground_delay_min:g} min" if a.ground_delay_min > 0 else "") + \
            (f", speed reduction {a.speed_reduction_min:g} min" if a.speed_reduction_min > 0 else "") + \
            (f", holding {m.hold_min:g} min" if m.hold_min > 0 else "")
    return {"callsign": focus, "action": action, "revised_arrival": hhmm, "revised_arrival_min": slot,
            "revised_arrival_t": f"T{slot:+g}", "runway": a.runway, "gate": a.gate,
            "scheduled_arrival": _hhmm(wave.window_start, f.sched_min), "scheduled_gate": f.scheduled_gate,
            "speed_reduction_min": a.speed_reduction_min, "ground_delay_min": a.ground_delay_min,
            "hold_min": m.hold_min, "delay_vs_schedule_min": d,
            "summary": f"{focus} lands {hhmm} local (T{slot:+g}) on runway {a.runway}, gate {a.gate}"
                       f"{extra}; {d:+g} min vs schedule"}


_GRAPH = None


def negotiate_wave(wave: Wave, mode: str, chain: list[str] | None = None,
                   on_message: Callable[[dict], None] | None = None, run_id: str | None = None,
                   narrate: bool = False, fairness: str | None = None) -> dict:
    """`narrate`: Claude writes the duty-manager brief in any mode (always on in decision+claude).
    Narration only explains the finished outcome; it never changes the plan.
    `fairness`: the airline fairness-floor variant ("firm" | "relaxing"), per call; None = `fairness.current()`."""
    global _GRAPH
    if mode not in ("formulas", "decision", "decision+claude"):
        raise ValueError(f"unknown mode {mode!r}")
    run = _Run(wave, mode, chain, on_message, run_id or uuid.uuid4().hex[:12], fairness)
    if _GRAPH is None:
        _GRAPH = build_graph()
    st = _GRAPH.invoke({"ctx": run}, config={"recursion_limit": 200})
    plan: JointPlan = st["final"]
    if not rules.feasible(wave, plan):
        raise RuntimeError("no feasible plan exists for this wave")
    plan.label = plan.describe()
    ev = evaluate(wave, plan)
    base = baseline.status_quo(wave)
    result = {
        "run_id": run.run_id, "wave_id": wave.id, "mode": mode,
        "model": run.chain[0] if run.chain else "formula",
        "outcome": st["outcome"], "rounds": min(st["round"], MAX_TURNS), "turns": min(st["round"], MAX_TURNS),
        "agents": run.agents, "pilots": run.pilots, "airlines": airlines_mod.describe(wave),
        "fairness": run.fairness, "focus": run.focus,
        "revision": revision(wave, plan, run.focus), "limits": rules.limits_for(wave),
        "final_plan": plan.to_dict(), "evaluation": ev,
        "baseline": {"plan": None, "evaluation": evaluate(wave, base)},
        "narration": None,
        "model_errors": run.model_errors[:100],
        "stats": {"model_calls": run.model_calls, "fallbacks": run.fallbacks, "elapsed_ms": 0},
        "log": run.log,
    }
    result["baseline"]["plan"] = base.to_dict()
    from backend.wave import trajectory                 # v2.2: the paths the metrics were computed from
    from backend.wave.version import ENGINE_VERSION
    result["engine_version"] = ENGINE_VERSION
    result["trajectories"] = {"final": trajectory.build(wave, plan).to_dict(),
                              "baseline": trajectory.build(wave, base).to_dict()}
    if mode == "decision+claude" or narrate:
        from backend.wave.narrate import brief
        nar = brief(result)
        err = nar.pop("error", None)  # keep the contract shape; surface the failure in model_errors
        if err:
            result["model_errors"].append(f"narration: {err}")
        result["narration"] = nar
    result["stats"]["elapsed_ms"] = int((time.monotonic() - run.t0) * 1000)
    return result


def mermaid() -> str:
    return build_graph().get_graph().draw_mermaid()


if __name__ == "__main__":  # pragma: no cover
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--mermaid":  # python3 -m backend.wave.graph --mermaid docs/graph.mmd
        with open(sys.argv[2], "w") as fh:
            fh.write(mermaid())
        print(f"wrote {sys.argv[2]}")
        sys.exit(0)
    from tests.fixtures import make_wave
    r = negotiate_wave(make_wave(), "formulas", on_message=lambda m: print(m["seq"], m["round"], m["sender"], m["type"], m["rationale"]))
    print(json.dumps({k: r[k] for k in ("outcome", "rounds", "stats")}))
