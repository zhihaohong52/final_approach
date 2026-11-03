import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from backend import decide
from backend.decide import Decider
from backend.wave import graph, rules
from backend.wave.graph import negotiate_wave
from backend.wave.model import JointPlan
from tests.fixtures import make_wave

CONTRACT_TYPES = {"TRIAGE", "OPEN", "AGENT_TURN", "CHECK", "CONSENSUS", "NO_CONSENSUS", "FALLBACK",
                  "MODEL_FALLBACK", "UNABLE"}
RESULT_KEYS = {"run_id", "wave_id", "mode", "model", "outcome", "rounds", "turns", "agents", "focus", "revision",
               "limits", "final_plan", "evaluation", "baseline", "narration", "model_errors", "stats", "log",
               "pilots", "airlines", "fairness"}


@pytest.fixture(autouse=True)
def _clean():
    decide._reset_breakers()
    decide._reset_clients()
    yield
    decide._reset_breakers()
    decide._reset_clients()


def _hard_wave():
    w = make_wave()
    m = replace(w.metar, raw="KATL 151551Z 36040G55KT 2SM +TSRA OVC008 22/21 A2970", wind_dir_deg=360,
                wind_kt=40, gust_kt=55, visibility_sm=2, wx_codes=["+TSRA"], surface="wet")
    return replace(w, metar=m)


def _plan(wave, d: dict) -> JointPlan:
    from backend.wave.model import Assignment
    return JointPlan([Assignment(**{k: v for k, v in a.items() if k != "metrics"}) for a in d["assignments"]])


def log_of(r):
    return r["log"]


def _assert_valid(wave, r):
    assert RESULT_KEYS <= set(r)
    assert r["outcome"] in {"consensus", "fallback"}
    assert 1 <= r["rounds"] <= 3
    assert r["evaluation"]["feasible"]
    assert rules.feasible(wave, _plan(wave, r["final_plan"]))
    assert r["final_plan"]["id"] == r["evaluation"]["plan_id"]
    assert r["final_plan"]["label"] == _plan(wave, r["final_plan"]).describe()
    from backend.wave.utility import pilots
    agents = r["agents"]   # the voters: ATC, AIRLINE:* (sorted), GROUND
    assert agents[0] == "ATC" and agents[-1] == "GROUND"
    assert agents[1:-1] == sorted(agents[1:-1]) and agents[1:-1] and all(a.startswith("AIRLINE:") for a in agents[1:-1])
    assert r["pilots"] == pilots(wave) and r["fairness"] in ("firm", "relaxing")
    assert [f"AIRLINE:{a['code']}" for a in r["airlines"]] == agents[1:-1]
    assert r["focus"] == (wave.focus or r["focus"])
    turns = [m for m in r["log"] if m["type"] == "AGENT_TURN"]
    assert {m["sender"] for m in turns} == set(agents)
    for t in range(1, r["turns"] + 1):  # exactly one AGENT_TURN per voter per turn, in order
        assert [m["sender"] for m in turns if m["round"] == t] == agents
        for m in turns:
            assert m["body"]["turn"] == m["round"] and m["body"]["agent"] == m["sender"]
    assert len(turns) == len(agents) * r["turns"] and r["turns"] == r["rounds"] <= 3
    for m in r["log"]:   # pilots only ever send UNABLE
        if m["sender"].startswith("PILOT:"):
            assert m["type"] == "UNABLE" and m["sender"] in r["pilots"]
    passed = {m["body"]["plan_id"] for m in r["log"] if m["type"] == "CHECK" and m["body"]["passed"]}
    opening = next(m for m in r["log"] if m["type"] == "OPEN")
    if opening["body"]["evaluation"]["feasible"]:
        passed.add(opening["body"]["plan"]["id"])
    if r["outcome"] == "consensus":
        cons = [m for m in r["log"] if m["type"] == "CONSENSUS"]
        assert len(cons) == 1 and cons[0]["body"]["plan_id"] == r["final_plan"]["id"] in passed
        for ag in agents:  # every agent voted ACCEPT on it in that turn
            assert any(v["plan_id"] == r["final_plan"]["id"] and v["vote"] == "ACCEPT"
                       for m in turns if m["sender"] == ag and m["round"] == cons[0]["round"]
                       for v in m["body"]["votes"])
    else:
        fb = [m for m in r["log"] if m["type"] == "FALLBACK"]
        assert len(fb) == 1 and fb[0]["body"]["reason"] and r["turns"] == 3
    rev = r["revision"]
    assert rev["callsign"] == r["focus"] and rev["action"] in {"land", "hold", "divert", "ground_delay"}
    assert r["limits"] and all({"limit", "value", "source", "note"} <= set(x) for x in r["limits"])
    assert {x["source"] for x in r["limits"]} <= {"workbook", "real data", "assumption"}
    log = r["log"]
    assert [m["seq"] for m in log] == list(range(len(log)))
    for m in log:
        assert m["type"] in CONTRACT_TYPES
        assert m["body"]["type"] == m["type"]
        assert isinstance(m["rationale"], str) and "\n" not in m["rationale"]
        assert {"seq", "round", "ts", "sender", "type", "body", "rationale"} <= set(m)
        if "decision" in m:
            assert m["decision"]["kind"] in {"choice", "noul", "score", "formula"}
            assert m["decision"]["provider"] in {"openrouter", "typesafe", "formula"}


def test_formulas_run_terminates_valid():
    w = make_wave()
    r = negotiate_wave(w, "formulas")
    _assert_valid(w, r)
    assert r["model"] == "formula" and r["narration"] is None
    assert r["stats"]["model_calls"] == 0 and r["stats"]["fallbacks"] == 0
    assert all(m["decision"]["kind"] == "formula" for m in r["log"] if "decision" in m)
    for m in r["log"]:
        if m["type"] == "AGENT_TURN":
            assert all("p_accept" not in v for v in m["body"]["votes"])
    assert r["baseline"]["plan"]["id"] == r["baseline"]["evaluation"]["plan_id"]


def _check_order(log):
    r0 = [m["type"] for m in log if m["round"] == 0 and m["type"] != "MODEL_FALLBACK"]
    assert r0[:2] == ["TRIAGE", "OPEN"] and set(r0[2:]) <= {"UNABLE"}
    n_agents = len(next(m for m in log if m["type"] == "OPEN")["body"]["agents"])
    last = max(m["round"] for m in log)
    for t in range(1, last + 1):
        types = [m["type"] for m in log if m["round"] == t and m["type"] not in ("MODEL_FALLBACK", "FALLBACK")]
        assert types[-1] in ("CONSENSUS", "NO_CONSENSUS")
        assert types.count("CONSENSUS") + types.count("NO_CONSENSUS") == 1
        assert types.count("AGENT_TURN") == n_agents
        for i, ty in enumerate(types):  # every CHECK follows the AGENT_TURN whose proposal it checks (and its UNABLEs)
            if ty == "CHECK":
                j = i - 1
                while types[j] == "UNABLE":
                    j -= 1
                assert types[j] == "AGENT_TURN"
            if ty == "UNABLE":   # pilots speak right after the voter whose plan or move they refuse
                assert types[i - 1] in ("AGENT_TURN", "UNABLE")
    rounds_seq = [m["round"] for m in log]
    assert rounds_seq == sorted(rounds_seq)
    fb = [i for i, m in enumerate(log) if m["type"] == "FALLBACK"]
    if fb:
        assert len(fb) == 1 and fb[0] == len(log) - 1
        assert last == 3
        assert log[fb[0] - 1]["type"] == "NO_CONSENSUS" and log[fb[0] - 1]["round"] == 3


def test_message_order():
    for w in (make_wave(), _hard_wave()):
        _check_order(negotiate_wave(w, "formulas")["log"])


def test_providers_down_still_resolves(monkeypatch):
    def boom(self, entry, state, questions):
        raise RuntimeError("provider down")
    monkeypatch.setattr(Decider, "_call", boom)
    w = make_wave()
    r = negotiate_wave(w, "decision", chain=["openrouter:x/y"])
    _assert_valid(w, r)
    _check_order(r["log"])
    mf = [m for m in r["log"] if m["type"] == "MODEL_FALLBACK"]
    assert mf
    assert all(m["body"]["from_model"] == "openrouter:x/y" and m["body"]["to_model"] == "formula" for m in mf)
    assert all(m["body"]["agent"] == m["sender"] for m in mf)
    assert r["stats"]["fallbacks"] >= len(mf) and r["stats"]["model_calls"] == 0
    assert r["model_errors"]
    decided = [m for m in r["log"] if "decision" in m]
    assert decided and all(m["decision"]["kind"] == "formula" for m in decided)
    assert any(m["decision"].get("fallback_from") == ["openrouter:x/y"] for m in decided)


def test_on_message_streams():
    got = []
    r = negotiate_wave(make_wave(), "formulas", on_message=got.append, run_id="abc123")
    assert r["run_id"] == "abc123"
    assert got == r["log"]


def test_hard_wave_falls_back_safely():
    w = _hard_wave()
    r = negotiate_wave(w, "formulas")
    _assert_valid(w, r)
    assert any(m["type"] == "TRIAGE" and m["body"]["score"] >= 3 for m in r["log"])


def test_graph_mermaid_written(tmp_path):
    text = graph.mermaid()
    for node in ("triage", "open", "atc", "airlines", "ground", "decide_turn", "fallback", "finalize"):
        assert node in text
    path = tmp_path / "graph.mmd"   # docs/graph.mmd: python3 -m backend.wave.graph --mermaid docs/graph.mmd
    path.write_text(text)
    assert path.stat().st_size > 0
    assert "decide_turn -.-> fallback" in text and "decide_turn -. &nbsp;next&nbsp; .-> atc" in text


def test_narration_template_without_key(monkeypatch):
    from backend.wave import narrate
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = negotiate_wave(make_wave(), "formulas")
    n = narrate.brief(r)
    assert n["source"] == "template" and n["brief"] and n["lines"]
    assert n["error"] == "ANTHROPIC_API_KEY not set"
    r2 = negotiate_wave(make_wave(), "decision+claude", chain=[])
    assert r2["narration"]["source"] == "template" and "error" not in r2["narration"]
    assert "narration: ANTHROPIC_API_KEY not set" in r2["model_errors"]
    assert len(n["brief"].split()) <= narrate.MAX_WORDS + 1


def test_engine_rejects_unsafe_model_pick(monkeypatch):
    """A model that keeps picking moves which delay DL100 (only 1 min extra fuel) is vetoed by the engine
    with PLT-FUEL-RESERVE cited, at most 2 rejections per move slot, and the run still resolves safely."""
    from types import SimpleNamespace as NS

    bad = ("Shift DL100 slot +2", "Swap landing order DL100")

    def fake(self, entry, state, questions):
        q = questions.get("q")
        if getattr(q, "type", None) == "choice":
            pick = next((k for k, v in q.criteria.items() if (v["move"] if isinstance(v, dict) else v).startswith(bad)), None)
            if pick:
                return NS(answers={"q": NS(choice=pick, probabilities={pick: 0.9}, confidence=0.8)})
        raise RuntimeError("defer to formula")
    monkeypatch.setattr(Decider, "_call", fake)
    w = make_wave()
    w = replace(w, flights=[replace(f, fuel={**f.fuel, "extra_min": 1}) if f.callsign == "DL100" else f
                            for f in w.flights])
    r = negotiate_wave(w, "decision", chain=["openrouter:x/y"])
    _assert_valid(w, r)
    _check_order(r["log"])
    rej = [(m, x) for m in r["log"] if m["type"] == "AGENT_TURN" for x in m["body"]["rejected"]]
    assert rej
    for m, x in rej:
        assert m["sender"] == "ATC" and x["move"]["agent"] == "ATC"
        assert x["move"]["label"].startswith(bad)
        assert [v["rule"] for v in x["violations"]] == ["PLT-FUEL-RESERVE"]
        assert x["violations"][0]["evidence"]["extra_min"] == 1
        assert x["decision"]["kind"] == "choice" and x["decision"]["model"] == "x/y"
        assert list(x["decision"]["probabilities"]) == [x["move"]["id"]]
    for m, _ in rej:
        assert len(m["body"]["rejected"]) <= 2 * graph.SEVERE_MOVES_PER_ROUND
    assert not any(y["move"]["label"].startswith(bad) for m in r["log"] if m["type"] == "AGENT_TURN"
                   for y in m["body"]["moves"])


def test_voters_and_pilots_on_real_shape():
    """ATC, one airline per carrier group and GROUND vote; each of the 4 flights has a (non-voting) pilot."""
    w = replace(make_wave(), focus="AA302")
    r = negotiate_wave(w, "formulas")
    _assert_valid(w, r)
    assert r["agents"] == ["ATC", "AIRLINE:AA", "AIRLINE:DL", "AIRLINE:UA", "GROUND"]
    assert r["pilots"] == ["PILOT:DL100", "PILOT:DL201", "PILOT:AA302", "PILOT:UA403"]
    assert list(r["evaluation"]["utilities"]) == r["agents"]
    assert r["revision"]["callsign"] == "AA302"


def test_fallback_logs_reason(monkeypatch):
    """Agents that never accept anything: 3 turns, NO_CONSENSUS each turn, FALLBACK says why."""
    monkeypatch.setattr(graph._Run, "reservation", lambda self, ag, rnd: 2.0)
    w = make_wave()
    r = negotiate_wave(w, "formulas")
    _assert_valid(w, r)
    _check_order(r["log"])
    assert r["outcome"] == "fallback" and r["turns"] == 3
    fb = r["log"][-1]
    assert fb["type"] == "FALLBACK" and "No consensus within 3 turns" in fb["body"]["reason"]
    assert "reservation" in fb["body"]["reason"]


def _result_with_focus_metrics(**metrics):
    """A formulas-run result whose focus flight's final-plan metrics are overridden with `metrics`."""
    import copy
    r = copy.deepcopy(negotiate_wave(make_wave(), "formulas"))
    a = next(a for a in r["final_plan"]["assignments"] if a["callsign"] == r["focus"])
    a["metrics"] = {**(a.get("metrics") or {}), **metrics}
    return r


def test_template_mentions_laps():
    from backend.wave import narrate
    r = _result_with_focus_metrics(hold_laps=2, hold_fix="BENKY", fuel_kg=428.0)
    assert "2 laps at BENKY (428 kg)" in narrate._template(r)["brief"]


def test_template_one_lap_singular():
    from backend.wave import narrate
    r = _result_with_focus_metrics(hold_laps=1, hold_fix="BENKY", fuel_kg=214.0)
    assert "1 lap at BENKY" in narrate._template(r)["brief"]


def test_template_no_lap_clause_when_absent():
    from backend.wave import narrate
    r = _result_with_focus_metrics(hold_laps=0, hold_fix=None)
    assert " lap" not in narrate._template(r)["brief"].replace("overlap", "")
    r2 = _result_with_focus_metrics(hold_laps=None, hold_fix=None, taxi_wait_min=None)
    assert "hold-short" not in narrate._template(r2)["brief"]


def test_template_hold_short_and_laps_survive_trim():
    from backend.wave import narrate
    r = _result_with_focus_metrics(hold_laps=3, hold_fix="BENKY", fuel_kg=500.0, taxi_wait_min=2.5,
                                   hold_short_strip="27R/09L")
    b = narrate._template(r)["brief"]
    assert "3 laps at BENKY (500 kg)" in b and "hold-short 27R/09L 2.5 min" in b


def test_compact_passes_path_fields():
    from backend.wave import narrate
    r = _result_with_focus_metrics(hold_laps=2, hold_fix="BENKY", taxi_wait_min=1.5)
    c = narrate._compact(r)
    assert (c["hold_laps"], c["hold_fix"], c["taxi_wait_min"]) == (2, "BENKY", 1.5)


# ---------------------------------------------------------------- agent views and prompt budget (spec §6.7)

REPLAY_IDS = sorted(p.stem for p in (Path(__file__).resolve().parents[1] / "data" / "replay").glob("*.json"))


def _prompt_chars(wid, monkeypatch) -> int:
    from backend.wave import build
    sizes = []
    orig = Decider.choice

    def spy(self, state, instructions, options, *a, **kw):
        sizes.append(len(json.dumps(state)) + len(instructions) + len(json.dumps(options)))
        return orig(self, state, instructions, options, *a, **kw)
    monkeypatch.setattr(Decider, "choice", spy)
    graph.negotiate_wave(build.build_wave(wid), "decision", chain=["formula"])
    assert sizes
    return max(sizes)


def test_largest_prompt_under_8k_tokens(monkeypatch):
    # over the replay waves, the biggest prompt any agent receives (chars/4)
    assert max(_prompt_chars(wid, monkeypatch) for wid in REPLAY_IDS) / 4 <= 8000


def test_views_show_each_flights_path():
    from backend.wave import build, moves
    from backend.wave.utility import pilots
    w = build.build_wave("F2015-270287"); p = moves.fcfs_plan(w)
    atc, pilot = "ATC", f"PILOT:{w.focus}" if w.focus else pilots(w)[0]
    v = graph._view(w, p, atc)
    assert set(v["path"]) == {a.callsign for a in p.assignments}
    assert any(" · hold " in s and " base " in s and " · lap " in s and s.endswith(" kg") for s in v["path"].values())
    pv = graph._view(w, p, pilot)
    assert pv["path"] == v["path"][pilot.split(":", 1)[1]]


def test_view_path_without_geometry_and_when_diverted():
    from backend.wave import moves
    from backend.wave.utility import pilots
    w = make_wave(); p = moves.fcfs_plan(w)    # fixture wave: no procedures
    v = graph._view(w, p, "ATC")
    assert all(v["path"][a.callsign] == f"no route geometry for {a.runway}" for a in p.assignments)
    pilot = pilots(w)[0]
    cs = pilot.split(":", 1)[1]
    d = next(m for m in moves.legal_moves(w, p, pilot) if m.kind == "request_divert")
    q = moves.apply(w, p, d)
    assert graph._view(w, q, "ATC")["path"][cs] == "no path (not landing)"
    assert graph._view(w, q, pilot)["path"] == "no path (not landing)"
