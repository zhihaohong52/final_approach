from backend.wave.benchmark import main

SUMMARY_KEYS = {"consensus_rate_pct", "mean_rounds", "fallback_rate_pct", "rule_violations_in_agreed_plans",
                "delay_min", "fuel_kg", "pax_protected_pct", "overtime_min", "agreement_with_formula_pct", "brier",
                "latency_p50_ms", "latency_p95_ms", "error_rate_pct", "cost_usd_per_wave",
                "lap_share_pct", "taxi_wait_min", "max_prompt_chars"}


def test_small_benchmark(tmp_path):
    import json
    main(["--fixtures", "--n", "3", "--arms", "formula", "--concurrency", "2", "--ablation",
          "--outage-n", "0", "--out", str(tmp_path)])
    rep = json.loads((tmp_path / "benchmark.json").read_text())
    for k in ("generated_at", "n_waves", "seed", "arms", "baseline", "ablation_no_engine_unsafe_pct",
              "outage_test", "selection", "failures", "wave_ids"):
        assert k in rep
    assert rep["n_waves"] == 3 and len(rep["wave_ids"]) == 3
    for arm in rep["arms"]:
        assert set(arm["summary"]) == SUMMARY_KEYS
        assert arm["summary"]["rule_violations_in_agreed_plans"] == 0
    assert 0 <= rep["ablation_no_engine_unsafe_pct"] <= 100
    assert {"delay_min", "fuel_kg", "pax_protected_pct", "overtime_min", "diversions"} <= set(rep["baseline"])
    assert "n_feasible" in rep["baseline"] and 0 <= rep["baseline"]["n_feasible"] <= rep["n_common"]
    assert len(rep["baseline"]["infeasible_ids"]) == rep["n_common"] - rep["baseline"]["n_feasible"]
    assert (tmp_path / "BENCHMARK.md").read_text().startswith("# Benchmark")


def _fake_negotiate(real, dead_arm, fail_ids=()):
    def fake(wave, mode, chain=None, **kw):
        if (chain and chain[0] == dead_arm) or wave.id in fail_ids:
            raise RuntimeError("boom")
        return real(wave, mode, chain=chain, **kw)
    return fake


def test_dead_arm_cannot_win(tmp_path, monkeypatch):
    import json
    from backend.wave import benchmark
    monkeypatch.setattr(benchmark, "negotiate_wave", _fake_negotiate(benchmark.negotiate_wave, "openrouter:dead/m"))
    rep = benchmark.main(["--fixtures", "--n", "3", "--arms", "openrouter:dead/m,formula", "--concurrency", "2",
                          "--no-ablation", "--outage-n", "1", "--out", str(tmp_path)])
    assert rep["selection"]["winner"] != "dead/m"
    dead = next(a for a in rep["arms"] if a["model"] == "dead/m")
    assert dead["summary"]["brier"] is None and dead["summary"]["latency_p95_ms"] is None
    assert dead["n_ok"] == 0 and rep["ablation_no_engine_unsafe_pct"] is None
    json.loads((tmp_path / "benchmark.json").read_text())


def test_failed_waves_excluded_from_means(tmp_path, monkeypatch):
    from backend.wave import benchmark
    ids = [w.id for w in benchmark.fixture_waves(3)]
    monkeypatch.setattr(benchmark, "negotiate_wave",
                        _fake_negotiate(benchmark.negotiate_wave, None, fail_ids={ids[0]}))
    rep = benchmark.main(["--fixtures", "--n", "3", "--arms", "formula", "--concurrency", "1",
                          "--no-ablation", "--outage-n", "0", "--out", str(tmp_path)])
    assert rep["n_common"] == 2
    f = rep["arms"][0]
    assert f["n_ok"] == 2 and f["failed_ids"] == [ids[0]]
    assert f["summary"]["error_rate_pct"] > 0 and f["summary"]["consensus_rate_pct"] < 100
    assert any("1/3 waves failed" in r for r in rep["selection"]["reasons"])


def test_decisions_and_brier_read_agent_turns():
    from backend.wave.benchmark import brier, decisions
    mdl = {"kind": "noul", "model": "m", "provider": "openrouter", "latency_ms": 300}
    mv = {"kind": "choice", "model": "m", "provider": "openrouter", "latency_ms": 200, "fallback_from": ["x"]}
    fm = {"kind": "formula", "model": "formula", "provider": "formula", "latency_ms": 0}
    votes = [{"plan_id": "a", "vote": "ACCEPT", "p_accept": 0.8, "utility": 0.7, "reservation": 0.6},
             {"plan_id": "b", "vote": "REJECT", "p_accept": 0.5, "utility": 0.5, "reservation": 0.6}]
    log = [{"type": "TRIAGE", "round": 0, "body": {"type": "TRIAGE"}, "decision": fm},
           {"type": "AGENT_TURN", "round": 1, "decision": mdl,
            "body": {"type": "AGENT_TURN", "decision": mdl, "votes": votes,
                     "moves": [{"decision": mv}], "rejected": [{"decision": mv}]}},
           {"type": "AGENT_TURN", "round": 1, "body": {"type": "AGENT_TURN", "votes": [], "moves": [], "rejected": []}}]
    ds = decisions({"log": log})
    assert len(ds) == 4 and sum(1 for d in ds if d.get("fallback_from")) == 2
    assert brier([{"log": log}]) == round(((0.8 - 1) ** 2 + (0.5 - 0) ** 2) / 2, 4)


def test_outage_uses_best_model_arm_even_if_formula_wins():
    from backend.wave.benchmark import select
    summ = lambda d: {"delay_min": d, "pax_protected_pct": 90, "brier": 0.1, "latency_p95_ms": 100.0,
                      "error_rate_pct": 0.0, "consensus_rate_pct": 100.0, "fallback_rate_pct": 0.0}
    arm = lambda m, d, p: {"model": m, "provider": p, "label": m, "n_ok": 3, "failed_ids": [], "p95_all_ms": 100.0,
                           "summary": summ(d)}
    base = {"delay_min": 50}
    sel, best = select([arm("m1", 40, "openrouter"), arm("formula", 30, "formula")], 3, base)
    assert sel["winner"] == "formula" and best["model"] == "m1"
    sel, best = select([arm("formula", 30, "formula")], 3, base)
    assert best is None


def test_high_fallback_arm_with_better_delay_loses():
    from backend.wave.benchmark import select
    def arm(m, d, cons, fb, p="openrouter"):
        return {"model": m, "provider": p, "label": m, "n_ok": 50, "failed_ids": [], "p95_all_ms": 100.0,
                "summary": {"delay_min": d, "pax_protected_pct": 90, "brier": 0.1, "latency_p95_ms": 100.0,
                            "error_rate_pct": 0.0, "consensus_rate_pct": cons, "fallback_rate_pct": fb}}
    sel, best = select([arm("fast", 200, 68.0, 32.0), arm("steady", 250, 95.0, 5.0),
                        arm("formula", 300, 98.0, 2.0, "formula")], 50, {"delay_min": 400})
    assert sel["winner"] == "steady"
    assert any("fast missed consensus >= 90%: 68.0%" in r for r in sel["reasons"])
    assert any("fast outcomes partly from solver fallback on 16/50 waves" in r for r in sel["reasons"])


def test_engine_history_and_attribution(tmp_path):
    from backend.wave import benchmark as B
    prev = {"engine_version": "v2", "baseline": {"delay_min": 10.0, "fuel_kg": 1.0}, "arms": [{"model": "formula", "summary": {"delay_min": 8.0, "fuel_kg": 1.0}}]}
    cur = {"engine_version": "v3-air", "baseline": {"delay_min": 12.0, "fuel_kg": 3.0}, "arms": [{"model": "formula", "summary": {"delay_min": 9.0, "fuel_kg": 2.0}}]}
    a = B.attribution(prev, cur)
    assert a["physics_delta"]["delay_min"] == 2.0 and a["negotiation_delta"]["formula"]["delay_min"] == -3.0


def test_report_has_engine_version_and_lap_share(tmp_path):
    from backend.wave import benchmark as B
    from backend.wave.version import ENGINE_VERSION
    rep = B.main(["--fixtures", "--n", "3", "--arms", "formula", "--no-ablation", "--outage-n", "0",
                  "--concurrency", "2", "--out", str(tmp_path)])
    assert rep["engine_version"] == ENGINE_VERSION and "lap_share_pct" in rep["baseline"]
    assert "lap_share_pct" in rep["arms"][0]["summary"] and isinstance(rep["regressions"], list)
    md = (tmp_path / "BENCHMARK.md").read_text()
    assert "## Engine history" in md and "## Attribution" in md and "32,000" in md
    assert any(r["engine_version"] == "v2" and r["arm"] == "baseline" for r in rep["engine_history"])
    assert rep["arms"][0]["summary"]["max_prompt_chars"] == 0   # formula arm


def _res(outcome, laps):
    a = [{"callsign": f"X{i}", "metrics": {"hold_laps": n}} for i, n in enumerate(laps)]
    return {"result": {"outcome": outcome, "final_plan": {"assignments": a}}}


def test_lap_share_pct():
    from backend.wave.benchmark import lap_share_pct
    runs = {"a": _res("consensus", [0, 2]), "b": _res("consensus", [0, 0]), "c": _res("fallback", [3]),
            "d": {"error": "boom"}}
    assert lap_share_pct(runs) == 50.0
    assert lap_share_pct({}) == 0.0


def test_veto_counts():
    from backend.wave.benchmark import veto_counts
    chk = lambda r, p=False: {"rule": r, "passed": p}   # noqa: E731
    log = [{"type": "AGENT_TURN", "body": {"rejected": [{"violations": [chk("R1"), chk("R2"), chk("R1")]},
                                                        {"violations": [chk("R1", True)]}]}},
           {"type": "TRIAGE", "body": {}}]
    assert veto_counts({"a": {"result": {"log": log}}, "b": {"error": "x"}}) == {"R1": 2, "R2": 1}


def test_engine_history_reads_archive(tmp_path):
    import json
    from backend.wave.benchmark import engine_history
    arm = {"model": "formula", "n_ok": 3, "summary": {"delay_min": 8.0, "fuel_kg": 1.0, "pax_protected_pct": 50.0,
           "overtime_min": 0.0, "consensus_rate_pct": 100.0, "rule_violations_in_agreed_plans": 0}}
    (tmp_path / "engine-v2").mkdir()
    (tmp_path / "engine-v2" / "benchmark.json").write_text(json.dumps(
        {"engine_version": "v2", "baseline": {"delay_min": 10.0, "n_feasible": 2}, "arms": [arm]}))
    cur = {"engine_version": "v3-air", "baseline": {"delay_min": 12.0, "lap_share_pct": 5.0, "n_feasible": 3},
           "arms": [{**arm, "summary": {**arm["summary"], "lap_share_pct": 20.0}}]}
    rows = engine_history(tmp_path, cur)
    assert [(r["engine_version"], r["arm"]) for r in rows] == [
        ("v2", "baseline"), ("v2", "formula"), ("v3-air", "baseline"), ("v3-air", "formula")]
    assert rows[0]["delay_min"] == 10.0 and rows[0]["lap_share_pct"] is None
    assert rows[3]["lap_share_pct"] == 20.0 and rows[3]["n_feasible"] == 3


def test_frozen_v2_archive():
    import json
    from backend.wave.benchmark import ROOT
    d = ROOT / "eval" / "archive" / "engine-v2"
    assert json.loads((d / "benchmark.json").read_text())["engine_version"] == "v2"
    assert (d / "BENCHMARK.md").read_text().startswith("# Benchmark")


def test_max_prompt_chars_tracks_every_decider_call(monkeypatch):
    import json
    from dataclasses import replace
    from backend.decide import Decider
    from backend.wave import benchmark as B
    from backend.wave.fixtures import make_wave

    def fake(wave, mode, chain=None, **kw):
        d = Decider(chain=["formula"])   # the formula entry never reaches the network
        d.choice({"k": "v" * 10}, "i" * 5, {"a": "b" * 3}, lambda: "a")
        d.score({"k": "v"}, "i" * 40, ["lo", "hi"], lambda: 0)
        d.noul({"k": "v"}, "q" * 20, lambda: 0.5)
        d.noul_batch({"k": "v"}, {"x": "y" * 300, "z": "w" * 100}, lambda i: 0.5)   # the largest call
        return {"outcome": "consensus", "rounds": 1, "evaluation": {"totals": {"delay_min": 0}}}
    monkeypatch.setattr(B, "negotiate_wave", fake)
    waves = [replace(make_wave(), id=f"w{i}") for i in range(6)]
    B.run_arm("model-arm", waves, ["openrouter:m", "formula"], "decision", 4, "t")
    want = len(json.dumps({"k": "v"})) + len(json.dumps({"x": "y" * 300, "z": "w" * 100}))
    assert B.PROMPT_MAX["model-arm"] == want
    B.run_arm("formula", waves, ["formula"], "formulas", 2, "t")
    assert B.PROMPT_MAX["formula"] == 0


def _pw(delay, fuel, cause="x"):
    return {"delay_min": delay, "fuel_kg": fuel, "cause": cause}


def test_find_regressions():
    from backend.wave.benchmark import find_regressions
    cur = {"formula": {"w1": _pw(12, 5), "w2": _pw(10, 5), "w3": _pw(9, 5)},
           "baseline": {"w1": _pw(14, 5), "w2": _pw(10, 5), "w3": _pw(9, 5)}}
    prev = {"formula": {"w1": _pw(10, 5), "w2": _pw(10, 5), "w3": _pw(10, 5)},
            "baseline": {"w1": _pw(10, 5), "w2": _pw(10, 5), "w3": _pw(10, 5)}}
    r = find_regressions(cur, prev)
    assert [(x["wave_id"], x["metric"], x["cause"]) for x in r] == [("w1", "delay_min", "physics")]
    assert (r[0]["v_prev"], r[0]["v_cur"]) == (10, 12)   # unchanged w2 and improved w3 produce none
    cur["baseline"]["w1"] = _pw(10, 5)   # the status quo did not move: the arm itself regressed
    assert find_regressions(cur, prev)[0]["cause"] == "negotiation"
    assert find_regressions(cur, None) == [] and find_regressions(cur, {}) == []


def test_per_wave_table():
    from backend.wave.benchmark import per_wave_table
    asg = [{"metrics": {"hold_min": 4.0, "hold_laps": 1, "vector_min": 1.5, "taxi_wait_min": 0.5}},
           {"metrics": {"hold_min": 0.0, "hold_laps": 0}}]
    runs = {"w1": {"result": {"final_plan": {"assignments": asg},
                              "evaluation": {"totals": {"delay_min": 7.0, "fuel_kg": 2.0}}}}}
    t = per_wave_table(runs, {"w1"})
    assert t["w1"]["delay_min"] == 7.0 and t["w1"]["fuel_kg"] == 2.0
    assert t["w1"]["cause"] == "hold 4.0 min (1 lap(s)), vector 1.5 min, taxi wait 0.5 min"


def test_archive_order_and_attribution_notes(tmp_path):
    import json
    from backend.wave.benchmark import attribution, load_archive
    for d, v in (("a-late", "v3-time"), ("z-early", "v2")):
        (tmp_path / d).mkdir()
        (tmp_path / d / "benchmark.json").write_text(json.dumps({"engine_version": v, "baseline": {}, "arms": []}))
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "benchmark.json").write_text("{")
    assert [r["engine_version"] for r in load_archive(tmp_path)] == ["v2", "v3-time"]
    a = attribution({"baseline": {}, "arms": [], "wave_ids": ["a", "b"]},
                    {"baseline": {}, "arms": [], "wave_ids": ["a", "c"]})
    assert a["notes"] and "differ" in a["notes"][0]


import pytest


@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL; the v2 row survives as non-current history
def test_frozen_v2_row_is_not_current(live_db_url):
    import psycopg
    with psycopg.connect(live_db_url) as c:
        cur = c.execute("select count(*) from eval.benchmark_reports where is_current").fetchone()[0]
    assert cur == 1


def test_markdown_qualifies_empty_regressions_physics_and_winner():
    import copy, json
    from pathlib import Path
    from backend.wave import benchmark as B
    rep = json.loads((Path(__file__).resolve().parent.parent / "eval" / "benchmark.json").read_text())
    rep = copy.deepcopy(rep)
    rep["attribution"]["prev_has_per_wave"] = False
    rep["regressions"] = []
    rep["engine_notes"] = {"title": "vX notes", "items": ["note a"], "limits": ["limit b"]}
    md = B.markdown(rep)
    assert "empty by construction" in md and "never renegotiated" in md
    assert "indicative, not statistically significant)" in md.split("**Winner:**")[1].splitlines()[0]
    assert "## vX notes\n\n- note a" in md and md.rstrip().endswith("- limit b")
    rep.pop("engine_notes")
    assert "## vX notes" not in B.markdown(rep)


def test_markdown_definition_change_caveat_under_regressions():
    import copy, json
    from pathlib import Path
    from backend.wave import benchmark as B
    rep = copy.deepcopy(json.loads((Path(__file__).resolve().parent.parent / "eval" / "benchmark.json").read_text()))
    rep["attribution"]["prev_has_per_wave"] = True
    rep["engine_notes"] = {"title": "t", "items": [], "definition_change": True}
    md = B.markdown(rep)
    head = md.split("Regressions (")[1].split("\n\n", 2)
    assert "definition change" in head[1]
    rep["engine_notes"].pop("definition_change")
    assert "not flight physics" not in B.markdown(rep)
