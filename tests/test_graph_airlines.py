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

CLT, ATL = "CLT-2024-07-29-19", "ATL-2024-07-18-21"


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


def test_forced_fuel_breaking_push_gets_one_unable(monkeypatch):
    """Formulas never pick a fuel-breaking push, so a stub decider forces ATC's first pick: the only option is
    `Vector AA302 +2 min`, which breaks AA302's PLT-FUEL-RESERVE (1 min extra fuel). The engine rejects it and
    PILOT:AA302 sends exactly one UNABLE naming that instruction, right after ATC's AGENT_TURN."""
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
    r = negotiate_wave(w, "formulas")
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
    """CLT under firm: AA floor-rejects ATC's plan (two AA flights +17 min) and ATC rejects the status quo in turn 1,
    so the status quo, which meets AA's floor, must stay the incumbent for turn 2 (not ATC's higher max-min plan)."""
    w, r = clt_firm
    sq = baseline.status_quo(w)
    assert rules.feasible(w, sq)
    if r["turns"] == 1:   # preflight F25: if turn 1 already agrees, report it rather than force turn 2
        pytest.skip(f"turn 1 reached {r['outcome']}")
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
