"""Airline agents in the negotiation (spec 2026-11-04 §3.3-3.5): N voters, airline turns, fairness-floor votes,
objections and pilot UNABLE. Formulas mode only (no model calls, no database)."""
import json
from dataclasses import replace

import pytest

from backend import decide
from backend.decide import Decider, DecisionResult
from backend.wave import baseline, benchmark, build, fairness, graph, moves, rules, unable
from backend.wave.graph import negotiate_wave
from tests.fixtures import make_wave
from tests.test_graph import _assert_valid, _check_order, _hard_wave

# single-airline 2024 hours with a feasible status quo: the top CLT (AA only) and ATL (DL only) rows of index.csv
# whose stress checks hold (tests/test_stress_airlines.py WAVES)
CLT, ATL = "CLT-2024-06-03-20", "ATL-2024-07-05-21"


@pytest.fixture(autouse=True)
def _clean():
    decide._reset_breakers()
    decide._reset_clients()
    yield
    decide._reset_breakers()
    decide._reset_clients()


@pytest.fixture(scope="module")
def clt_firm():
    w = build.build_wave(CLT)
    return w, negotiate_wave(w, "formulas", fairness="firm")


def _turns(r, sender=None):
    return [m for m in r["log"] if m["type"] == "AGENT_TURN" and (sender is None or m["sender"] == sender)]


def test_fixture_roster():
    w = make_wave()
    r = negotiate_wave(w, "formulas")
    _assert_valid(w, r)
    assert r["agents"] == ["ATC", "AIRLINE:AA", "AIRLINE:DL", "AIRLINE:UA", "GROUND"]
    assert r["pilots"] == ["PILOT:DL100", "PILOT:DL201", "PILOT:AA302", "PILOT:UA403"]
    assert r["airlines"] == w.to_dict()["airlines"]
    opening = next(m for m in r["log"] if m["type"] == "OPEN")["body"]
    assert opening["agents"] == r["agents"] and opening["pilots"] == r["pilots"]
    assert opening["airlines"] == r["airlines"]
    assert {m["body"]["role"] for m in _turns(r)} == {"ATC", "AIRLINE", "GROUND"}


def test_fairness_variant_is_per_call(monkeypatch):
    w = make_wave()
    monkeypatch.setenv("ATM_FAIRNESS", "relaxing")
    assert negotiate_wave(w, "formulas")["fairness"] == "relaxing"
    assert negotiate_wave(w, "formulas", fairness="firm")["fairness"] == "firm"
    with pytest.raises(ValueError):
        negotiate_wave(w, "formulas", fairness="soft")


def _unable_follows_rejections(r):
    """Every rejected move with a pilot-side violation is followed, before the next AGENT_TURN, by that pilot's
    UNABLE naming the move (or the same refusal was already said earlier: UNABLE is deduped)."""
    log = r["log"]
    said = set()
    n = 0
    for i, m in enumerate(log):
        if m["type"] == "UNABLE":
            b = m["body"]
            assert m["sender"] == f"PILOT:{b['callsign']}" == b["agent"] and m["sender"] in r["pilots"]
            assert unable.pilot_side({**b, "passed": False, "owner": "PILOT" if b["rule"].startswith("PLT-") else "ATC"})
            assert b["text"].startswith("UNABLE ") and b["text"].endswith(", " + b["rule"])
            said.add((b["plan_id"], (b["instruction"] or {}).get("id"), b["callsign"], b["rule"]))
        if m["type"] != "AGENT_TURN":
            continue
        nxt = next((j for j in range(i + 1, len(log)) if log[j]["type"] == "AGENT_TURN"), len(log))
        after = [x["body"] for x in log[i + 1:nxt] if x["type"] == "UNABLE"]
        for x in m["body"]["rejected"]:
            for c in x["violations"]:
                if not unable.pilot_side(c):
                    continue
                n += 1
                key = (x["plan_id"], x["move"]["id"], c["callsign"], c["rule"])
                assert key in said or any(
                    (u["plan_id"], (u["instruction"] or {}).get("id"), u["callsign"], u["rule"]) == key
                    for u in after), (m["sender"], key)
    return n


@pytest.mark.parametrize("wid", ["fixture", "hard", CLT, ATL])
def test_unable_after_rejected_pilot_side_move(wid):
    w = {"fixture": make_wave, "hard": _hard_wave}.get(wid, lambda: build.build_wave(wid))()
    r = negotiate_wave(w, "formulas")
    _assert_valid(w, r)
    _check_order(r["log"])
    _unable_follows_rejections(r)


def _forced_unable_run(monkeypatch):
    """Formulas never pick a fuel-breaking push, so a stub decider forces ATC's first pick: the only option is
    `Vector AA302 +2 min`, which breaks AA302's PLT-FUEL-RESERVE (1 min extra fuel)."""
    w = make_wave()
    w = replace(w, flights=[replace(f, fuel={**f.fuel, "extra_min": 1}) if f.callsign == "AA302" else f
                            for f in w.flights])
    push = "ATC:shift_slot:AA302:minutes=2"
    orig_lm, orig_choice = moves.legal_moves, Decider.choice
    calls = {"lm": 0, "choice": 0}

    def lm(wave, plan, agent):
        if agent == "ATC" and calls["lm"] == 0:
            calls["lm"] += 1
            return [m for m in orig_lm(wave, plan, agent) if m.id == push] or \
                [m for m in moves._candidates(wave, plan, agent) if m.id == push]
        return orig_lm(wave, plan, agent)

    def choice(self, state, instructions, options, formula):
        if state.get("agent") == "ATC" and calls["choice"] == 0:
            calls["choice"] += 1
            return DecisionResult("m1", None, None, "formula", "formula", 0)
        return orig_choice(self, state, instructions, options, formula)
    monkeypatch.setattr(moves, "legal_moves", lm)
    monkeypatch.setattr(Decider, "choice", choice)
    return w, push, negotiate_wave(w, "formulas")


def test_forced_fuel_breaking_push_gets_one_unable(monkeypatch):
    """The engine rejects the push and PILOT:AA302 sends exactly one UNABLE naming that instruction, right
    after ATC's AGENT_TURN."""
    w, push, r = _forced_unable_run(monkeypatch)
    _assert_valid(w, r)
    _check_order(r["log"])
    atc1 = _turns(r, "ATC")[0]
    rej = atc1["body"]["rejected"]
    assert [x["move"]["id"] for x in rej] == [push]
    assert ("PLT-FUEL-RESERVE", "AA302") in {(v["rule"], v["callsign"]) for v in rej[0]["violations"]}
    un = [m for m in r["log"] if m["type"] == "UNABLE" and m["sender"] == "PILOT:AA302"]
    assert len(un) == 1
    b = un[0]["body"]
    assert b["instruction"]["id"] == push and b["rule"] == "PLT-FUEL-RESERVE" and b["proposed_by"] == "ATC"
    assert b["plan_id"] == rej[0]["plan_id"] and b["turn"] == 1
    assert r["log"][atc1["seq"] + 1] is un[0]          # the pilot speaks right after the voter it refuses
    assert b["text"].startswith("UNABLE vector AA302 +2 min") and b["text"].endswith(", PLT-FUEL-RESERVE")
    assert _unable_follows_rejections(r) >= 1


def test_unable_plan_cannot_win():
    """Invariant guard (preflight F6): an UNABLE plan fails a pilot-side check, so CHECK already keeps it out of
    the vote; the `unable_plans` filter in _decide_turn is defensive. This asserts it never wins."""
    for w in (make_wave(), _hard_wave(), build.build_wave(CLT), build.build_wave("F2015-77162")):
        r = negotiate_wave(w, "formulas")
        refused = {m["body"]["plan_id"] for m in r["log"] if m["type"] == "UNABLE"}
        won = {m["body"]["plan_id"] for m in r["log"] if m["type"] == "CONSENSUS"}
        assert not (refused & won)


def test_unable_filter_blocks_consensus(monkeypatch):
    """The defensive filter itself: a plan marked UNABLE is never declared CONSENSUS even if every voter accepts."""
    w = make_wave()
    base = negotiate_wave(w, "formulas")
    assert base["outcome"] == "consensus"
    won = base["final_plan"]["id"]
    orig = graph._Run.__init__

    def init(self, *a, **kw):
        orig(self, *a, **kw)
        self.unable_plans.add(won)
    monkeypatch.setattr(graph._Run, "__init__", init)
    r = negotiate_wave(w, "formulas")
    assert all(m["body"]["plan_id"] != won for m in r["log"] if m["type"] == "CONSENSUS")


def test_floor_reject_names_flight(clt_firm):
    w, r = clt_firm
    floor_rej = [(m, v) for m in _turns(r) if m["body"]["role"] == "AIRLINE" for v in m["body"]["votes"]
                 if v["vote"] == "REJECT" and v.get("decided_by") == "floor"]
    assert floor_rej                                       # preflight F15: not vacuous
    t1 = [(m, v) for m, v in floor_rej if m["round"] == 1]
    assert t1 and t1[0][1]["plan_id"] == _turns(r, "ATC")[0]["body"]["proposal"]["id"]   # ATC's turn-1 plan
    for m, v in floor_rej:
        b = v["floor"][0]
        assert b["callsign"] in v["reason"] and "firm fairness floor" in v["reason"]
        assert b["floor_source"] == "status_quo" and b["value"] > b["allowed"]
        assert v["reason"].startswith(f"{b['callsign']} {b['metric']} {b['value']:g} > {b['allowed']:g}")


def test_objections_list_other_agents_plans(clt_firm):
    w, r = clt_firm
    aa = _turns(r, "AIRLINE:AA")
    assert aa and all("objections" in m["body"] for m in aa)
    obj = aa[0]["body"]["objections"]
    atc_plan = _turns(r, "ATC")[0]["body"]["proposal"]["id"]
    assert obj and {o["plan_id"] for o in obj} == {atc_plan} and {o["proposed_by"] for o in obj} == {"ATC"}
    assert all({"callsign", "metric", "floor", "value", "allowed", "floor_source"} <= set(o) for o in obj)
    assert "objects: " in aa[0]["rationale"] and "vs the original plan" in aa[0]["rationale"]
    for m in _turns(r):   # only airlines object
        assert ("objections" in m["body"]) == (m["body"]["role"] == "AIRLINE")


def test_floor_plan_stays_incumbent(clt_firm):
    """CLT under firm: AA floor-rejects ATC's plan (AA2843 +3.8 min) and ATC rejects the status quo in turn 1,
    so the status quo, which meets AA's floor, must stay the incumbent for turn 2 (not ATC's higher max-min plan)."""
    w, r = clt_firm
    sq = baseline.status_quo(w)
    assert rules.feasible(w, sq)
    # precondition (review M3): turn 1 must end without agreement, else this test fails rather than skips
    nc1 = [m for m in r["log"] if m["type"] == "NO_CONSENSUS" and m["round"] == 1]
    assert nc1 and r["turns"] == 2, f"turn 1 reached {r['outcome']} in {r['turns']} turn(s)"
    atc2 = _turns(r, "ATC")[1]
    assert atc2["round"] == 2 and atc2["body"]["votes"][0]["plan_id"] == sq.id
    assert r["outcome"] == "consensus" and r["final_plan"]["id"] == sq.id


def test_no_floor_before_feasible():
    """Preflight F5: votes are only cast on CHECK-passed plans, which `see` has already made the best-seen floor,
    so the no-floor path is tested directly: no floor -> no breach, no objection."""
    w = make_wave()
    p = moves.fcfs_plan(w)
    assert fairness.breaches(w, p, "DL", None, "firm", 1, source="best_seen") == []
    run = graph._Run(w, "formulas", None, None, "t", "firm")
    run.sq_ok, run.floor_best = False, {}                 # status quo infeasible, nothing feasible seen yet
    for ag in run.airlines:
        assert run.floor(ag) == (None, "best_seen")
        assert run.breaches(p, ag, 1) == []


def test_best_seen_floor_on_infeasible_status_quo():
    """F2015-77162: the status quo is infeasible, so any floor REJECT or objection cites the best plan seen."""
    w = build.build_wave("F2015-77162")
    assert not rules.feasible(w, baseline.status_quo(w))
    for v in fairness.VARIANTS:
        r = negotiate_wave(w, "formulas", fairness=v)
        _assert_valid(w, r)
        for m in _turns(r):
            for o in m["body"].get("objections", []):
                assert o["floor_source"] == "best_seen"
            for vt in m["body"]["votes"]:
                if vt.get("decided_by") == "floor":
                    assert all(b["floor_source"] == "best_seen" for b in vt["floor"])
                    assert "best plan seen" in vt["reason"]


def test_pilots_make_no_model_calls(monkeypatch):
    built = []
    orig = graph._Run.decider

    def spy(self, agent, rnd, buf):
        built.append(agent)
        return orig(self, agent, rnd, buf)

    def boom(self, entry, state, questions):
        raise RuntimeError("stub decider: provider down")
    monkeypatch.setattr(graph._Run, "decider", spy)
    monkeypatch.setattr(Decider, "_call", boom)
    w = replace(make_wave(), flights=[replace(f, fuel={**f.fuel, "extra_min": 1}) if f.callsign == "DL100" else f
                                      for f in make_wave().flights])
    r = negotiate_wave(w, "decision", chain=["openrouter:x/y"])
    _assert_valid(w, r)
    assert built and not any(a.startswith("PILOT:") for a in built)
    assert set(built) <= set(r["agents"]) | {"DECIDER"}
    assert not any(m["type"] == "MODEL_FALLBACK" and m["sender"].startswith("PILOT:") for m in r["log"])


def test_consensus_accepted_by_all_voters():
    for w in (make_wave(), build.build_wave(CLT), build.build_wave("F2015-7053")):
        r = negotiate_wave(w, "formulas")
        _assert_valid(w, r)
        if r["outcome"] != "consensus":
            continue
        c = next(m for m in r["log"] if m["type"] == "CONSENSUS")
        assert c["body"]["accepted_by"] == r["agents"]
        assert f"all {len(r['agents'])} agents" in c["rationale"]


def test_fallback_reason_names_floor_reject(monkeypatch):
    """No consensus: the FALLBACK reason carries `_no_consensus_reason`, which names each floor REJECT's breach."""
    monkeypatch.setattr(graph._Run, "reservation", lambda self, ag, rnd: 2.0)
    w = build.build_wave(CLT)
    r = negotiate_wave(w, "formulas", fairness="firm")
    assert r["outcome"] == "fallback"
    reason = r["log"][-1]["body"]["reason"]
    assert "AIRLINE:AA (fairness floor: " in reason and "firm fairness floor" in reason
    assert "accepted by all 3 agents" in reason


def test_airline_view_within_prompt_budget():
    w = build.build_wave(CLT)
    p = moves.fcfs_plan(w)
    v = graph._view(w, p, "AIRLINE:AA")
    assert v["airline"] == "AA" and [f["flight"] for f in v["flights"]] == [f.callsign for f in w.flights]
    assert len(json.dumps(v)) <= benchmark.PROMPT_BUDGET_CHARS


def test_divert_guard_per_move():
    w = make_wave()
    p = moves.fcfs_plan(w)
    chk = rules.check(w, p)
    dv = [m for m in moves._candidates(w, p, "AIRLINE:DL") if m.kind == "request_divert"]
    assert dv and not any(graph._divert_ok(w, m, chk) for m in dv)      # no fuel problem, runways open
    fuel = [replace(c, passed=False) if c.rule == "PLT-FUEL-RESERVE" and c.callsign == "DL100" else c for c in chk]
    assert [graph._divert_ok(w, m, fuel) for m in dv] == [m.callsigns[0] == "DL100" for m in dv]
    closed = replace(w, airport=replace(w.airport, runways=[replace(r, open=False) for r in w.airport.runways]))
    assert all(graph._divert_ok(closed, m, chk) for m in dv)
    assert graph._divert_ok(w, next(m for m in moves._candidates(w, p, "ATC")), chk)


def test_own_flight_matches_whole_callsign():
    """Review M4: a check names flight AA1 only as a whole token, never as a prefix of AA123."""
    from types import SimpleNamespace as NS
    c = NS(callsign=None, detail="Gate B7 busy: AA123 needs T+10..T+55")
    assert graph._own_flight(c, "AA123") and not graph._own_flight(c, "AA1") and not graph._own_flight(c, "A123")
    assert graph._own_flight(NS(callsign="AA1", detail="x"), "AA1")
    assert graph._own_flight(NS(callsign=None, detail="AA1: taxi 18L -> B7"), "AA1")
    fails = [NS(callsign=None, detail="Gate B7 busy: AA123 needs T+10..T+55", passed=False)]
    assert graph._counted("AIRLINE:AA", fails, set(), ["AA1"]) == []
    assert graph._counted("AIRLINE:AA", fails, set(), ["AA123"]) == fails


def test_escape_clause_judged_at_end_of_turn(monkeypatch):
    """Review M1: an airline voted on utility because no floor-meeting plan was on the table when it spoke, then
    GROUND proposed one. The breaching plan, although every voter accepted it, must not win this turn; the
    floor-meeting plan becomes the next incumbent. Control: with no floor-meeting plan, the breaching plan wins."""
    w = make_wave()
    run = graph._Run(w, "formulas", None, None, "t", "firm")
    p = moves.fcfs_plan(w)
    q = next(moves.apply(w, p, m) for m in moves._candidates(w, p, "GROUND")
             if rules.feasible(w, moves.apply(w, p, m)) and moves.apply(w, p, m).id != p.id)
    assert rules.feasible(w, p)
    breach = {"callsign": "DL100", "metric": "delay_min", "floor": 0.0, "value": 9.0, "allowed": 0.05,
              "floor_source": "status_quo"}
    floor_ok = {q.id}
    monkeypatch.setattr(graph._Run, "breaches",
                        lambda self, plan, ag, rnd: [] if plan.id in floor_ok or ag != "AIRLINE:DL" else [breach])
    ballots = {ag: [{"plan_id": p.id, "vote": "ACCEPT", "utility": 0.5, "reservation": 0.1}] for ag in run.agents}
    ballots["GROUND"].append({"plan_id": q.id, "vote": "ACCEPT", "utility": 0.5, "reservation": 0.1})
    st = {"ctx": run, "round": 1, "checked": {p.id: (p, True), q.id: (q, True)}, "ballots": ballots,
          "proposals": [("ATC", p), ("GROUND", q)]}
    out = graph._decide_turn(st)
    assert out["route"] == "next" and out["table"].id == q.id
    assert not any(m["type"] == "CONSENSUS" for m in run.log)
    floor_ok.clear()                       # control: no plan meets DL's floor, so the escape clause stands
    run2 = graph._Run(w, "formulas", None, None, "t2", "firm")
    out2 = graph._decide_turn({**st, "ctx": run2})
    assert out2["route"] == "finalize" and out2["final"].id == p.id


def test_escape_guard_does_not_apply_on_the_last_turn(monkeypatch):
    """Review M7: on turn 3 a later floor-meeting plan cannot be carried into a turn 4, so it must not block a
    unanimous plan that breaches only under the escape clause (that would turn a consensus into a fallback)."""
    w = make_wave()
    run = graph._Run(w, "formulas", None, None, "t", "firm")
    p = moves.fcfs_plan(w)
    q = next(moves.apply(w, p, m) for m in moves._candidates(w, p, "GROUND")
             if rules.feasible(w, moves.apply(w, p, m)) and moves.apply(w, p, m).id != p.id)
    breach = {"callsign": "DL100", "metric": "delay_min", "floor": 0.0, "value": 9.0, "allowed": 0.05,
              "floor_source": "status_quo"}
    monkeypatch.setattr(graph._Run, "breaches",
                        lambda self, plan, ag, rnd: [] if plan.id == q.id or ag != "AIRLINE:DL" else [breach])
    assert graph._escape_guard(run, 2, [p, q], [p]) == []                 # turn 2: blocked, q carries on
    assert [x.id for x in graph._escape_guard(run, graph.MAX_TURNS, [p, q], [p])] == [p.id]
    ballots = {ag: [{"plan_id": p.id, "vote": "ACCEPT", "utility": 0.5, "reservation": 0.1}] for ag in run.agents}
    ballots["GROUND"].append({"plan_id": q.id, "vote": "ACCEPT", "utility": 0.5, "reservation": 0.1})
    out = graph._decide_turn({"ctx": run, "round": graph.MAX_TURNS, "checked": {p.id: (p, True), q.id: (q, True)},
                              "ballots": ballots, "proposals": [("ATC", p), ("GROUND", q)]})
    assert out["route"] == "finalize" and out["final"].id == p.id


def test_objection_words_follow_the_airlines_vote():
    """Review I1: the rationale tail comes from the airline's own vote on each objected plan."""
    o = {"callsign": "WN130", "metric": "delay_min", "floor": 30.7, "value": 31.2, "allowed": 30.75,
         "floor_source": "best_seen", "plan_id": "aaaaaaaaaa", "proposed_by": "ATC"}
    acc = [{"plan_id": "aaaaaaaaaa", "vote": "ACCEPT", "decided_by": "escape"}]
    rej = [{"plan_id": "aaaaaaaaaa", "vote": "REJECT", "decided_by": "floor"}]
    assert graph._objection_words([o], [], [], acc).endswith(
        "; accepts it: no plan on the table meets its floor (escape clause)")
    assert graph._objection_words([o], [], [], rej).endswith("; rejects the plan")
    o2 = {**o, "plan_id": "bbbbbbbbbb"}
    two = graph._objection_words([o, o2], [], [], acc + [{"plan_id": "bbbbbbbbbb", "vote": "REJECT"}])
    assert "accepts aaaaaaaaaa: no plan" in two and two.endswith("; rejects bbbbbbbbbb")


def test_narration_mentions_unable_and_airlines(monkeypatch):
    """A pilot's UNABLE is a key message, so the narration lines carry the refusal; the brief names the voters."""
    from backend.wave import narrate
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert "UNABLE" in narrate.KEY_TYPES
    _, _, r = _forced_unable_run(monkeypatch)
    assert any(m["type"] == "UNABLE" for m in r["log"])
    out = narrate._template(r)
    assert any("PILOT:AA302" in line and "UNABLE" in line for line in out["lines"]), out["lines"]
    assert "airline agent" in out["brief"] and "pilot and ground" not in out["brief"]
    assert any(m["type"] == "UNABLE" for m in narrate._key_messages(r))
