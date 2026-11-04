"""Benchmark: decision-model arms vs the formula arm on the same waves (spec §10, §14; contract BenchmarkReport).

    python3 -m backend.wave.benchmark --source flights2015 --n 50 --arms openrouter:typesafe/jev-1.14,formula --concurrency 16 --seed 7

Definitions (also written to selection.reasons):
- fallback_rate_pct = % of waves whose outcome was the deterministic solver fallback.
- error_rate_pct    = % of decisions (all start at the arm) answered after the arm failed (non-empty fallback_from).
- decisions         = DecisionMeta of AGENT_TURN votes and of every move/rejected move, plus TRIAGE.
- latency_*         = DecisionMeta.latency_ms of model-answered decisions only (null when there are none).
- failed waves      = non-consensus + one error decision; outcome means use the waves that succeeded in every arm.
- brier             = mean (p_accept - y)^2 over model-answered votes (AGENT_TURN body.votes[]), y = formula verdict (utility >= reservation).
- worse_than_status_quo_pct = WAVE-TOTAL share: % of waves with a feasible status quo whose FINAL plan (consensus or
  solver fallback) has wave-total delay_min > status quo + 0.05 or fuel_kg > status quo + 0.5. Comparable with
  v3-traffic (archives hold wave totals only). The consensus-only share is arm field worse_than_sq_consensus_pct.
- worse_flight_pct = PER-FLIGHT share: % of agreed (consensus) waves with a feasible status quo where ANY flight's
  delay or fuel is worse than in the status quo (same 0.05 min / 0.5 kg tolerances). The fairness pick ranks on it.
- model_calls_per_wave = mean of stats.model_calls (decisions answered by a model).
- status quo may violate rules: baseline means cover all common waves, feasible_only covers the waves where it is
  feasible, and regressions vs status quo are only reported on those waves.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.datastore import get_store
from backend.wave import fairness as fair, rules
from backend.wave.graph import negotiate_wave
from backend.wave.model import Assignment, JointPlan
from backend.wave.utility import evaluate
from backend.wave.version import ENGINE_VERSION

ROOT = Path(__file__).resolve().parents[2]
DEAD_MODEL = "openrouter:invalid/model-does-not-exist"
P95_LIMIT_MS, ERR_LIMIT_PCT, CONSENSUS_LIMIT_PCT = 1000, 2.0, 90.0
TOTAL_KEYS = ("delay_min", "fuel_kg", "pax_protected_pct", "overtime_min")
PROMPT_BUDGET_CHARS = 32000   # spec §6.7: 8k tokens at chars/4
PROMPT_MAX: dict[str, int] = {}   # arm tag -> largest state + instructions + options any Decider call (choice, score, noul, noul_batch) received
HISTORY_KEYS = ("delay_min", "fuel_kg", "lap_share_pct", "taxi_wait_min", "pax_protected_pct", "overtime_min",
                "consensus_rate_pct", "rule_violations_in_agreed_plans", "worse_than_status_quo_pct")
WORSE_DELAY_MIN, WORSE_FUEL_KG = fair.DELAY_EPS_MIN, fair.FUEL_EPS_KG   # metric rounding, nothing more
TIE_EPS = 1e-9
ATTRIBUTION_KEYS = ("delay_min", "fuel_kg", "lap_share_pct", "taxi_wait_min", "pax_protected_pct", "overtime_min")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- waves

def fixture_waves(n: int) -> list:
    from dataclasses import replace
    from backend.wave.fixtures import make_wave
    out = []
    for i in range(n):
        w = make_wave()
        flights = [replace(f, delays={**f.delays, "weather": 10 + 7 * ((i + j) % 4)},
                           disrupted=((i + j) % 3 == 0),
                           fuel={**f.fuel, "extra_min": f.fuel["extra_min"] - (i % 3) * 4})
                   for j, f in enumerate(w.flights)]
        out.append(replace(w, id=f"{w.id}-fx{i}", flights=flights))
    return out


def sample_wave_ids(n: int, seed: int) -> list[str]:
    from backend.wave.build import list_waves
    rows = list_waves(limit=10000)
    by_ap: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_ap[r["airport"]["iata"]].append(r["id"])
    rng = random.Random(seed)
    for ids in by_ap.values():
        rng.shuffle(ids)
    airports = sorted(by_ap)
    rng.shuffle(airports)
    out: list[str] = []
    while len(out) < n and any(by_ap.values()):  # round-robin = stratified by airport
        for ap in airports:
            if by_ap[ap] and len(out) < n:
                out.append(by_ap[ap].pop())
    return out


def sample_flights2015_ids(n: int, seed: int) -> list[str]:
    """Seeded sample of flights.csv rows, round-robin (stratified) over the 30 hub destinations so IEM weather
    is fetched for a limited set of stations. Airports without runway data are skipped by flights2015."""
    from backend.wave import flights2015
    from backend.wave.hubs import HUBS
    rng = random.Random(seed)
    by_ap: dict[str, list[str]] = {}
    for ap in HUBS:
        rows = flights2015.list_rows(airport=ap, limit=10 ** 7)
        if rows:
            ids = [r["wave_id"] for r in rows]
            rng.shuffle(ids)
            by_ap[ap] = ids
    airports = sorted(by_ap)
    rng.shuffle(airports)
    out: list[str] = []
    while len(out) < n and any(by_ap.values()):
        for ap in airports:
            if by_ap[ap] and len(out) < n:
                out.append(by_ap[ap].pop())
    return out


def load_waves(n: int, seed: int, fixtures: bool, source: str = "flights2015") -> tuple[list, list[dict]]:
    """Returns (waves, build-failure rows)."""
    if fixtures:
        return fixture_waves(n), []
    if source == "flights2015":
        from backend.wave import flights2015
        ids, build = sample_flights2015_ids(n, seed), lambda wid: flights2015.build_from_row(int(wid[len("F2015-"):]))
    else:
        from backend.wave.build import build_wave
        ids, build = sample_wave_ids(n, seed), build_wave
    waves, bad = [], []
    for wid in ids:
        try:
            waves.append(build(wid))
        except Exception as e:  # noqa: BLE001
            log(f"skip {wid}: build failed: {e}")
            bad.append({"wave_id": wid, "model": "-", "reason": f"build failed: {str(e)[:200]}"})
    return waves, bad


# ---------------------------------------------------------------- running

def decisions(result: dict) -> list[dict]:
    """Every DecisionMeta in a result log: message-level decisions (TRIAGE, AGENT_TURN vote; the body.decision of an
    AGENT_TURN is the same object) plus the per-move decisions in AGENT_TURN moves[] and rejected[]."""
    out = []
    for m in result["log"]:
        body = m.get("body") or {}
        d = m.get("decision") or body.get("decision")
        if d:
            out.append(d)
        if m["type"] == "AGENT_TURN":
            out += [x["decision"] for x in body.get("moves", []) + body.get("rejected", []) if x.get("decision")]
    return out


def arm_parts(arm: str) -> tuple[str, str]:
    if arm == "formula":
        return "formula", "formula"
    prov, _, model = arm.partition(":")
    return prov, model


def run_arm(arm: str, waves: list, chain: list[str], mode: str, concurrency: int, tag: str,
            fairness: str | None = None) -> dict[str, dict]:
    from backend.decide import Decider, _reset_breakers, _stringify
    _reset_breakers()
    out: dict[str, dict] = {}
    PROMPT_MAX[arm] = 0
    lock = threading.Lock()
    methods = ("choice", "score", "noul", "noul_batch")
    real = {m: getattr(Decider, m) for m in methods}
    params = {"choice": ("state", "instructions", "options"), "score": ("state", "instructions", "levels"),
              "noul": ("state", "instructions"), "noul_batch": ("state", "items", "formula", "criteria")}

    def size(m, args, kw):
        """Chars a call sends: state + instructions + options/criteria/levels (noul_batch: every item)."""
        a = dict(zip(params[m], args), **kw)
        a.pop("formula", None)
        return len(json.dumps(_stringify(a.pop("state")))) + sum(len(json.dumps(v, default=str)) for v in a.values())

    def wrap(m):
        def call(self, *args, **kw):
            if chain != ["formula"]:   # the formula arm sends no prompt
                n = size(m, args, kw)
                with lock:
                    PROMPT_MAX[arm] = max(PROMPT_MAX[arm], n)
            return real[m](self, *args, **kw)
        return call

    def one(w):
        t0 = time.monotonic()
        try:
            r = negotiate_wave(w, mode, chain=chain, fairness=fairness)
            out[w.id] = {"result": r}
            log(f"[{tag}] {w.id}: {r['outcome']} rounds={r['rounds']} delay={r['evaluation']['totals']['delay_min']} "
                f"({time.monotonic() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            out[w.id] = {"error": f"{type(e).__name__}: {e}"[:300]}
            log(f"[{tag}] {w.id}: EXCEPTION {e}")

    for m in methods:   # arms run one after another, so the class patch is not shared between arms
        setattr(Decider, m, wrap(m))
    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            list(ex.map(one, waves))
    finally:
        for m in methods:
            setattr(Decider, m, real[m])
    return out


@contextmanager
def engine_disabled():
    """Not thread-safe: replaces the rule engine's verdicts for the duration of the block."""
    saved = (rules.feasible, rules.violations, rules.check)
    rules.feasible = lambda wave, plan: True
    rules.violations = lambda wave, plan: []
    rules.check = lambda wave, plan, mm=None: []
    try:
        yield
    finally:
        rules.feasible, rules.violations, rules.check = saved


def plan_from_dict(d: dict) -> JointPlan:
    fields = ("callsign", "action", "runway", "slot_min", "gate", "speed_reduction_min", "ground_delay_min")
    return JointPlan([Assignment(**{k: a[k] for k in fields if k in a}) for a in d["assignments"]], d.get("label", ""))


def mean(xs: list[float]) -> float:
    return round(statistics.fmean(xs), 3) if xs else 0.0


def ci95(xs: list[float]) -> list[float] | None:
    """mean +/- 1.96*sd/sqrt(n); null when n < 2."""
    if len(xs) < 2:
        return None
    m, h = statistics.fmean(xs), 1.96 * statistics.stdev(xs) / len(xs) ** 0.5
    return [round(m - h, 3), round(m + h, 3)]


def pct(num: float, den: float) -> float:
    return round(100.0 * num / den, 2) if den else 0.0


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def brier(results: list[dict]) -> float:
    errs = []
    for r in results:
        for m in r["log"]:
            body = m.get("body") or {}
            if m["type"] not in ("VOTE", "AGENT_TURN") or \
                    (m.get("decision") or body.get("decision") or {}).get("provider", "formula") == "formula":
                continue
            for v in body.get("votes", []):
                if "p_accept" in v:
                    y = 1.0 if v["utility"] >= v["reservation"] else 0.0
                    errs.append((v["p_accept"] - y) ** 2)
    return round(statistics.fmean(errs), 4) if errs else 0.0


def feasible_real(wave, result: dict) -> bool:
    return rules.feasible(wave, plan_from_dict(result["final_plan"]))


def status_quo_feasible(wave, result: dict) -> bool:
    ev = result["baseline"]["evaluation"]
    if "feasible" in ev:
        return bool(ev["feasible"])
    return rules.feasible(wave, plan_from_dict(result["baseline"]["plan"]))


def _plan_laps(assignments: list[dict]) -> bool:
    return any((a.get("metrics") or {}).get("hold_laps", 0) >= 1 for a in assignments)


def lap_share_pct(results: dict[str, dict]) -> float:
    """% of agreed plans (outcome consensus) with at least one wave-flight holding lap. results: wave id -> run."""
    plans = [x["result"]["final_plan"]["assignments"] for x in results.values()
             if "result" in x and x["result"].get("outcome") == "consensus"]
    return pct(sum(_plan_laps(p) for p in plans), len(plans))


def veto_counts(results: dict[str, dict]) -> dict[str, int]:
    """Per rule, the failed checks in the agents' rejected (engine-vetoed) proposals."""
    c: Counter = Counter()
    for x in results.values():
        for m in x.get("result", {}).get("log", []):
            for rj in (m.get("body") or {}).get("rejected", []):
                c.update(v["rule"] for v in rj.get("violations", []) if not v.get("passed", False))
    return dict(sorted(c.items()))


def taxi_wait_mean(plans: list[list[dict]]) -> float:
    """Mean over plans of the summed taxi_wait_min of their assignments."""
    return mean([sum((a.get("metrics") or {}).get("taxi_wait_min", 0.0) for a in p) for p in plans])


REAL_TAXI_IN = "(real TAXI_IN)"   # build.taxi_in_ref_row's provenance marker; the airport-median fallback lacks it


def _taxi_cause(routed: float, real: float) -> str:
    """Heuristic label for a large routed-vs-real gap (not a diagnosis)."""
    if real > routed + 10:
        return "BTS includes gate wait"
    if routed > real + 5:
        return "long route / remote stand"
    return "-"


def taxi_validation(waves: list, results: dict[str, dict]) -> dict:
    """Taxi-in validation vs BTS 2015 (spec §10.5, no fitting): for the focus flight of every wave in `results`
    (membership only: value["wave_id"], else the key), the routed taxi-in under the FCFS plan against that flight's
    real BTS TAXI_IN. Routed = ROT + unimpeded taxi (`TaxiInfo.total_min`, wheels-on to in-block, the span BTS
    TAXI_IN measures), unrounded. Only focus flights whose taxi is on the real surface tier (OSM route, src "osm")
    and whose TAXI_IN is real (not the airport-median fallback) count. bias = mean(routed - real); BTS includes
    gate waits, so bias < 0 is expected and reported, not corrected. per_airport: routed median of the counted
    flights vs the airport's 2015 BTS median TAXI_IN (`build.airport_taxi_median`)."""
    from backend.wave import moves, surface
    from backend.wave.build import airport_taxi_median
    from backend.wave.utility import focus_callsign
    ids = {(r or {}).get("wave_id", k) for k, r in results.items()}
    rows: list[dict] = []
    for w in waves:
        if w.id not in ids:
            continue
        cs = focus_callsign(w)
        f, a = w.flight(cs), moves.fcfs_plan(w).get(cs)
        if a is None or a.action != "land" or f.taxi_in_ref_min is None:
            continue
        if not any(p.layer == "flights" and REAL_TAXI_IN in (p.detail or "") for p in f.provenance):
            continue
        tx = surface.taxi_info(w, a.runway, a.gate, f.gate_size)
        if tx.tier != "real" or tx.src != "osm":
            continue
        rows.append({"wave_id": w.id, "callsign": cs, "iata": w.airport.iata,
                     "routed": tx.total_min, "real": float(f.taxi_in_ref_min)})
    if not rows:
        return {"n": 0, "mae_min": None, "bias_min": None, "per_airport": {}, "worst": []}
    by_ap: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ap[r["iata"]].append(r)
    worst = sorted(rows, key=lambda r: (-abs(r["routed"] - r["real"]), r["wave_id"]))[:5]
    return {
        "n": len(rows),
        "mae_min": round(statistics.fmean(abs(r["routed"] - r["real"]) for r in rows), 2),
        "bias_min": round(statistics.fmean(r["routed"] - r["real"] for r in rows), 2),
        "per_airport": {ap: {"n": len(rs), "routed_median": round(statistics.median(r["routed"] for r in rs), 2),
                             "bts_median": airport_taxi_median(ap)} for ap, rs in sorted(by_ap.items())},
        "worst": [{"wave_id": r["wave_id"], "callsign": r["callsign"], "routed": round(r["routed"], 1),
                   "real": r["real"], "cause": _taxi_cause(r["routed"], r["real"])} for r in worst],
    }


_PROC_RANK = ("star+iap", "iap", "straight-in")


def _procedures_tier(wave) -> str:
    """Best approach tier over the wave's open-runway routes (route.routes_for_wave); "none" without routes."""
    from backend.wave import route
    tiers = {r.tier for r in route.routes_for_wave(wave).values()}
    return next((t for t in _PROC_RANK if t in tiers), "none")


def coverage(waves: list, n_failed: int = 0) -> dict[str, dict[str, int]]:
    """Tier counts over the benchmark waves (spec §10.6). procedures: best route tier (star+iap / iap /
    straight-in); surface: the wave's `surface` provenance status (real / generic, decided per wave); ground_traffic:
    real (BTS 2015 movements) or partial (challenge-file subset). Waves that failed to build count as "none", so
    every layer sums to the requested wave count."""
    out: dict[str, Counter] = {"procedures": Counter(), "surface": Counter(), "ground_traffic": Counter()}
    for w in waves:
        st = {p.layer: p.status for p in w.provenance}
        out["procedures"][_procedures_tier(w)] += 1
        out["surface"][st.get("surface", "none")] += 1
        gt = st.get("ground_traffic")
        out["ground_traffic"]["none" if gt is None else "real" if gt == "real" else "partial"] += 1
    for c in out.values():
        if n_failed:
            c["none"] += n_failed
    order = {t: i for i, t in enumerate((*_PROC_RANK, "real", "generic", "partial", "none"))}
    return {k: dict(sorted(c.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0]))) for k, c in out.items()}


def _regressed(delay: float, fuel: float, sq_delay: float, sq_fuel: float) -> bool:
    return delay > sq_delay + WORSE_DELAY_MIN or fuel > sq_fuel + WORSE_FUEL_KG


def worse_than_sq_pct(arm_pw: dict, base_pw: dict, infeasible_ids: list[str]) -> float | None:
    """% of waves with a feasible status quo whose final plan is worse than it (delay or fuel, beyond rounding).
    arm_pw / base_pw: wave id -> {delay_min, fuel_kg}. None when no wave qualifies."""
    ids = [w for w in arm_pw if w in base_pw and w not in set(infeasible_ids)]
    if not ids:
        return None
    return pct(sum(_regressed(arm_pw[w]["delay_min"], arm_pw[w]["fuel_kg"], base_pw[w]["delay_min"], base_pw[w]["fuel_kg"])
                   for w in ids), len(ids))


def worse_flight_pct(results: list[dict]) -> float | None:
    """Per-flight share: % of the given results (consensus runs whose status quo is feasible, chosen by the caller)
    where ANY flight's delay or fuel in the final plan is worse than in the status quo plan. None for no results."""
    if not results:
        return None

    def worse(r: dict) -> bool:
        base = {a["callsign"]: a.get("metrics") or {} for a in r["baseline"]["plan"]["assignments"]}
        for a in r["final_plan"]["assignments"]:
            m, b = a.get("metrics") or {}, base.get(a["callsign"])
            if b is not None and _regressed(m.get("delay_min", 0.0), m.get("fuel_kg", 0.0),
                                            b.get("delay_min", 0.0), b.get("fuel_kg", 0.0)):
                return True
        return False
    return pct(sum(worse(r) for r in results), len(results))


def summarize(runs: dict[str, dict], formula_runs: dict[str, dict], waves: list, common: set[str],
              max_prompt_chars: int = 0) -> tuple[dict, dict]:
    """Returns (contract summary, extras for selection/markdown)."""
    N = len(waves)
    ok = [(wid, x["result"]) for wid, x in runs.items() if "result" in x]
    failed = sorted(wid for wid, x in runs.items() if "result" not in x)
    res = [r for _, r in ok]
    com = [r for wid, r in ok if wid in common]
    metas = [d for r in res for d in decisions(r)]
    model_lat = [d["latency_ms"] for d in metas if d["provider"] != "formula"]
    all_lat = [d["latency_ms"] for d in metas]
    errs = sum(1 for d in metas if d.get("fallback_from")) + len(failed)
    agree = [r["final_plan"]["id"] == formula_runs[wid]["result"]["final_plan"]["id"]
             for wid, r in ok if wid in common and wid in formula_runs and "result" in formula_runs[wid]]
    by_id = {w.id: w for w in waves}
    viol = sum(1 for wid, r in ok if not feasible_real(by_id[wid], r))
    sq_bad = [wid for wid, r in ok if wid in common and not status_quo_feasible(by_id[wid], r)]
    pw = {wid: r["evaluation"]["totals"] for wid, r in ok if wid in common}
    bw = {wid: r["baseline"]["evaluation"]["totals"] for wid, r in ok if wid in common}
    agreed = {wid for wid, r in ok if r["outcome"] == "consensus"}
    worse_agreed = worse_than_sq_pct({w: v for w, v in pw.items() if w in agreed}, bw, sq_bad)
    bad_sq = [r for wid, r in ok if wid in sq_bad]
    flight_runs = [r for wid, r in ok if wid in common and wid in agreed and wid not in sq_bad]
    s = {
        "consensus_rate_pct": pct(sum(r["outcome"] == "consensus" for r in res), N),
        "mean_rounds": mean([r["rounds"] for r in com]),
        "fallback_rate_pct": pct(sum(r["outcome"] == "fallback" for r in res), N),
        "rule_violations_in_agreed_plans": viol,
    }
    for k in TOTAL_KEYS:
        s[k] = mean([r["evaluation"]["totals"][k] for r in com])
    s["lap_share_pct"] = lap_share_pct(runs)
    s["taxi_wait_min"] = taxi_wait_mean([r["final_plan"]["assignments"] for r in com])
    has_model = bool(model_lat)
    s.update({
        "agreement_with_formula_pct": pct(sum(agree), len(agree)),
        "brier": brier(res) if has_model else None,
        "latency_p50_ms": percentile(model_lat, 0.5) if has_model else None,
        "latency_p95_ms": percentile(model_lat, 0.95) if has_model else None,
        "error_rate_pct": pct(errs, len(metas) + len(failed)),
        "cost_usd_per_wave": 0.0,
        "max_prompt_chars": max_prompt_chars,
        "worse_than_status_quo_pct": worse_than_sq_pct(pw, bw, sq_bad),
        "worse_flight_pct": worse_flight_pct(flight_runs),
        "model_calls_per_wave": mean([(r.get("stats") or {}).get("model_calls", 0) for r in res]),   # 0 when stats is absent (old runs)
    })
    return s, {"n_ok": len(ok), "failed_ids": failed, "p95_all_ms": percentile(all_lat, 0.95) if all_lat else None,
               "model_decisions": len(model_lat), "worse_than_sq_consensus_pct": worse_agreed,
               "infeasible_sq_waves": len(bad_sq),
               "consensus_pct_infeasible_sq": pct(sum(r["outcome"] == "consensus" for r in bad_sq), len(bad_sq))
               if bad_sq else None}


def run_optimiser(waves: list, concurrency: int = 4) -> dict[str, dict]:
    """Centralised reference: deterministic beam search (fallback.solve), no models, no negotiation. Scored with
    the same evaluate() as the arms' totals."""
    from backend.wave.fallback import solve
    out: dict[str, dict] = {}

    def one(w):
        t0 = time.monotonic()
        try:
            plan, method = solve(w)
            ev = evaluate(w, plan)
            out[w.id] = {"delay_min": ev["totals"]["delay_min"], "fuel_kg": ev["totals"]["fuel_kg"],
                         "pax_protected_pct": ev["totals"]["pax_protected_pct"],
                         "feasible": bool(rules.feasible(w, plan)), "method": method,
                         "elapsed_ms": round((time.monotonic() - t0) * 1000, 1)}
        except Exception as e:  # noqa: BLE001
            out[w.id] = {"error": f"{type(e).__name__}: {e}"[:300]}
            log(f"[optimiser] {w.id}: EXCEPTION {e}")

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        list(ex.map(one, waves))
    return out


def optimiser_summary(opt: dict[str, dict], common: set[str]) -> dict:
    rows = [opt[w] for w in sorted(common) if "error" not in opt.get(w, {"error": 1})]
    d = [r["delay_min"] for r in rows]
    return {"n": len(rows), "delay_min": mean(d), "delay_min_ci95": ci95(d),
            "fuel_kg": mean([r["fuel_kg"] for r in rows]),
            "pax_protected_pct": mean([r["pax_protected_pct"] for r in rows]),
            "n_feasible": sum(r["feasible"] for r in rows),
            "elapsed_ms_mean": mean([r["elapsed_ms"] for r in rows]),
            "methods": dict(Counter(r["method"] for r in rows))}


def arm_vs_optimiser(arm_delay: dict[str, float], sq_delay: dict[str, float], sq_feasible: set[str],
                     opt: dict[str, dict], common: set[str]) -> dict:
    """arm_delay / sq_delay: wave id -> delay_min. gap over common waves; share over waves with a feasible status quo
    where the optimiser beats it."""
    ids = [w for w in sorted(common) if w in arm_delay and "error" not in opt.get(w, {"error": 1})]
    gap = [arm_delay[w] - opt[w]["delay_min"] for w in ids]
    sub = [w for w in ids if w in sq_feasible and opt[w]["delay_min"] < sq_delay[w]]
    share = None
    if sub:
        den = sum(sq_delay[w] - opt[w]["delay_min"] for w in sub)
        share = round(100.0 * sum(sq_delay[w] - arm_delay[w] for w in sub) / den, 2) if den else None
    return {"delay_min_ci95": ci95([arm_delay[w] for w in ids]),
            "gap_to_optimiser": mean(gap) if gap else None,
            "share_of_optimiser_gain_pct": share, "share_n": len(sub)}


def run_ablation(waves: list, fairness: str | None = None) -> float | None:
    unsafe = total = 0
    with engine_disabled():
        runs = run_arm("formula", waves, ["formula"], "formulas", 1, "ablation", fairness=fairness)
    by_id = {w.id: w for w in waves}
    for wid, x in runs.items():
        if "result" not in x:
            continue
        total += 1
        plan = plan_from_dict(x["result"]["final_plan"])
        if not evaluate(by_id[wid], plan)["feasible"]:  # real rules (patch already restored)
            unsafe += 1
    return pct(unsafe, total) if total else None


def select(arms: list[dict], n_total: int, baseline: dict) -> tuple[dict, dict | None]:
    """Returns (selection, best eligible model arm by the selection ordering or None). The outage test uses that
    arm even when the overall winner is formula."""
    rule = "best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier"
    reasons = ["Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.",
               "cost_usd_per_wave = 0.0: output tokens free; input cost not measured.",
               "fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions "
               "answered after this arm failed (a failed wave counts as one error decision)."]
    formula = next((a for a in arms if a["provider"] == "formula"), None)
    cands = [a for a in arms if a["provider"] != "formula"]
    for a in arms:
        if a["failed_ids"]:
            reasons.append(f"arm {a['label']}: {len(a['failed_ids'])}/{n_total} waves failed")
    if not cands:
        return {"winner": "formula", "rule": rule, "reasons": reasons + ["No model arms were run."]}, None

    def key(a):
        s = a["summary"]
        return (-s["delay_min"], s["pax_protected_pct"], -(s["brier"] or 0.0))

    eligible = []
    for a in cands:
        s = a["summary"]
        if a["n_ok"] < n_total:
            reasons.append(f"{a['label']} disqualified: only {a['n_ok']}/{n_total} waves succeeded")
        elif s["latency_p95_ms"] is None:
            reasons.append(f"{a['label']} disqualified: zero model-answered decisions")
        else:
            eligible.append(a)
            if a["p95_all_ms"] is not None:
                reasons.append(f"{a['label']} p95 all decisions incl. fallbacks: {a['p95_all_ms']} ms")
            if s["error_rate_pct"] >= ERR_LIMIT_PCT:
                reasons.append(f"{a['label']} missed error < 2%: {s['error_rate_pct']}%")
            if s["latency_p95_ms"] >= P95_LIMIT_MS:
                reasons.append(f"{a['label']} missed p95 < 1000 ms: {s['latency_p95_ms']} ms")
            if s["consensus_rate_pct"] < CONSENSUS_LIMIT_PCT:
                reasons.append(f"{a['label']} missed consensus >= 90%: {s['consensus_rate_pct']}%")
            nfb = round(s["fallback_rate_pct"] * n_total / 100)
            if nfb > 0:
                reasons.append(f"{a['label']} outcomes partly from solver fallback on {nfb}/{n_total} waves")
    if not eligible:
        return {"winner": "formula", "rule": rule,
                "reasons": ["No model arm was eligible; formula selected."] + reasons}, None
    ok = [a for a in eligible if a["summary"]["error_rate_pct"] < ERR_LIMIT_PCT
          and a["summary"]["latency_p95_ms"] < P95_LIMIT_MS
          and a["summary"]["consensus_rate_pct"] >= CONSENSUS_LIMIT_PCT]
    if ok:
        win = max(ok, key=key)
        lead = (f"{win['label']} is the best model arm: it meets all three thresholds (error, p95, consensus) and has "
                "the best outcome among the qualifying model arms.")
    else:
        win = max(eligible, key=key)
        lead = ("No arm met error < 2%, p95 < 1000 ms and consensus >= 90%: " + ", ".join(
            f"{a['label']} p95 {a['summary']['latency_p95_ms']} ms / error {a['summary']['error_rate_pct']}% / "
            f"consensus {a['summary']['consensus_rate_pct']}%"
            for a in eligible) + f". Picked best outcome among eligible model arms: {win['label']}.")
    reasons.insert(0, lead)
    winner = win["model"]
    if formula is not None:
        ws, fs = win["summary"], formula["summary"]
        if ws["delay_min"] > fs["delay_min"] and ws["pax_protected_pct"] <= fs["pax_protected_pct"]:
            reasons.insert(0, f"Formula selected: the best model arm, {win['label']}, is worse than formula on delay "
                              f"({ws['delay_min']} > {fs['delay_min']} min) and not better on pax protected.")
            reasons[1] = reasons[1].replace("is the best model arm:", "is the best model arm but not the overall winner:")
            winner = "formula"
        reasons.append(f"Mean delay: {win['label']} {ws['delay_min']} min vs formula {fs['delay_min']} min "
                       f"vs status quo {baseline['delay_min']} min.")
    return {"winner": winner, "rule": rule, "reasons": reasons}, win


def run_outage(winner_arm: str | None, waves: list, n: int, concurrency: int,
               fairness: str | None = None) -> tuple[dict, list[str]]:
    sub = waves[:n]
    out = {"primary_disabled": DEAD_MODEL.split(":", 1)[1], "waves": len(sub), "resolved": 0, "fallbacks_used": {}}
    if winner_arm is None:
        out["waves"] = 0
        return out, ["outage test skipped (no model arm with model-answered decisions)"]
    if not sub:
        return out, []
    by_id = {w.id: w for w in sub}
    runs = run_arm("outage", sub, [DEAD_MODEL, winner_arm, "formula"], "decision", concurrency, "outage",
                   fairness=fairness)
    used: Counter = Counter()
    for wid, x in runs.items():
        if "result" not in x:
            continue
        if feasible_real(by_id[wid], x["result"]):
            out["resolved"] += 1
        for d in decisions(x["result"]):
            if d.get("fallback_from"):
                used[d["model"]] += 1
    out["fallbacks_used"] = dict(used)
    notes = [] if sum(used.values()) else ["outage test vacuous: dead primary never called"]
    return out, notes


# ---------------------------------------------------------------- engine history / attribution

def _row(version: str, arm: str, s: dict, n_ok: int | None, n_feasible: int | None) -> dict:
    return {"engine_version": version, "arm": arm, **{k: s.get(k) for k in HISTORY_KEYS}, "n_feasible": n_feasible}


def _archived_worse_pct(rep: dict, model: str) -> float | None:
    """worse_than_status_quo_pct from an archived report's per_wave tables (None when it has none)."""
    pw = rep.get("per_wave") or {}
    if model not in pw or "baseline" not in pw:
        return None
    return worse_than_sq_pct(pw[model], pw["baseline"], rep["baseline"].get("infeasible_ids", []))


def _rows(rep: dict, version: str) -> list[dict]:
    b = rep["baseline"]
    rows = [_row(version, "baseline", {**b, "consensus_rate_pct": None, "rule_violations_in_agreed_plans": None},
                 None, b.get("n_feasible"))]
    for a in rep["arms"]:
        s = a["summary"]
        if "worse_than_status_quo_pct" not in s:
            s = {**s, "worse_than_status_quo_pct": _archived_worse_pct(rep, a["model"])}
        nf = a["n_ok"] - s.get("rule_violations_in_agreed_plans", 0) if "n_ok" in a else None
        rows.append(_row(version, a["model"], s, a.get("n_ok"), nf))
    return rows


VERSION_ORDER = ("v2", "v3-air", "v3-time", "v3-taxi", "v3-traffic", "v4-airlines")


def _version_rank(v: str) -> int:
    return VERSION_ORDER.index(v) if v in VERSION_ORDER else len(VERSION_ORDER)


def load_archive(archive_dir: Path) -> list[dict]:
    """Frozen reports under archive_dir/<name>/benchmark.json, in engine-version order; a report without
    engine_version is v2."""
    out = []
    for f in Path(archive_dir).glob("*/benchmark.json"):
        try:
            r = json.loads(f.read_text())
        except (OSError, ValueError) as e:
            log(f"WARNING: unreadable archive report {f}: {e}")
            continue
        r.setdefault("engine_version", "v2")
        out.append(r)
    return sorted(out, key=lambda r: _version_rank(r["engine_version"]))


def engine_history(archive_dir: Path, current: dict) -> list[dict]:
    """One row per (engine version, arm), baseline included: archived versions first, the current report last.
    Metrics a version did not record are None."""
    cv = current.get("engine_version", ENGINE_VERSION)
    rows: list[dict] = []
    for r in load_archive(archive_dir):
        if r["engine_version"] != cv:
            rows += _rows(r, r["engine_version"])
    return rows + _rows(current, cv)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def attribution(prev: dict, cur: dict) -> dict:
    """physics_delta = baseline(cur) - baseline(prev): what the engine change did to the status quo itself.
    negotiation_delta[arm] = arm(cur) - baseline(cur): what negotiation adds under the current engine."""
    cb, pb = cur["baseline"], prev["baseline"]
    phys = {k: round(cb[k] - pb[k], 3) for k in ATTRIBUTION_KEYS if _num(cb.get(k)) and _num(pb.get(k))}
    neg = {a["model"]: {k: round(a["summary"][k] - cb[k], 3) for k in ATTRIBUTION_KEYS
                        if _num(a["summary"].get(k)) and _num(cb.get(k))} for a in cur["arms"]}
    notes = []
    pw, cw = prev.get("wave_ids"), cur.get("wave_ids")
    if pw is not None and cw is not None and set(pw) != set(cw):
        notes.append(f"wave sets differ ({len(set(pw) & set(cw))} shared of {len(pw)} previous / {len(cw)} current): "
                     "the physics delta mixes waves")
    elif pw is None or cw is None:
        if prev.get("n_common") != cur.get("n_common") and (prev.get("n_common") or cur.get("n_common")):
            notes.append(f"n_common differs ({prev.get('n_common')} vs {cur.get('n_common')}): wave sets may differ")
    return {"physics_delta": phys, "negotiation_delta": neg, "notes": notes}


def per_wave_table(arm_runs: dict[str, dict], common: set[str]) -> dict[str, dict]:
    """wave id -> {delay_min, fuel_kg, cause} of the agreed plan; cause names the lap / vector / taxi-wait time."""
    out = {}
    for wid in sorted(common):
        r = arm_runs[wid]["result"]
        asg = r["final_plan"]["assignments"]
        m = lambda k: sum((a.get("metrics") or {}).get(k, 0.0) or 0.0 for a in asg)   # noqa: E731
        out[wid] = {"delay_min": r["evaluation"]["totals"]["delay_min"], "fuel_kg": r["evaluation"]["totals"]["fuel_kg"],
                    "cause": f"hold {m('hold_min'):.1f} min ({int(m('hold_laps'))} lap(s)), vector "
                             f"{m('vector_min'):.1f} min, taxi wait {m('taxi_wait_min'):.1f} min"}
    return out


def per_wave_baseline(formula_runs: dict[str, dict], common: set[str]) -> dict[str, dict]:
    """wave id -> {delay_min, fuel_kg} of the status quo under the current engine."""
    return {w: {k: formula_runs[w]["result"]["baseline"]["evaluation"]["totals"][k] for k in ("delay_min", "fuel_kg")}
            for w in sorted(common)}


def find_regressions(cur_pw: dict[str, dict[str, dict]], prev_pw: dict | None) -> list[dict]:
    """Waves where an arm got worse in delay or fuel against the previous engine's per-wave table. Without that
    table (e.g. the v2 archive) there is nothing to compare and the list is empty. cause is "physics" when the
    status quo (the "baseline" table) worsened on that wave by at least as much as the arm, else "negotiation"."""
    if not prev_pw:
        return []
    out = []
    for arm, waves in cur_pw.items():
        if arm == "baseline":
            continue
        for wid, v in waves.items():
            p = (prev_pw.get(arm) or {}).get(wid)
            if not p:
                continue
            for k in ("delay_min", "fuel_kg"):
                change = v[k] - p[k]
                if change <= 1e-9:
                    continue
                cb, pb = (cur_pw.get("baseline") or {}).get(wid), (prev_pw.get("baseline") or {}).get(wid)
                moved = (cb[k] - pb[k]) if cb and pb else 0.0
                out.append({"wave_id": wid, "arm": arm, "metric": k, "v_prev": p[k], "v_cur": v[k],
                            "cause": "physics" if moved >= change - 1e-9 else "negotiation"})
    return out


# ---------------------------------------------------------------- failures / markdown

def collect_failures(label: str, runs: dict, waves: list) -> list[dict]:
    by_id = {w.id: w for w in waves}
    out = []
    for wid, x in runs.items():
        if "error" in x:
            out.append({"wave_id": wid, "model": label, "reason": f"exception: {x['error']}"})
            continue
        r = x["result"]
        if r["outcome"] == "fallback":
            out.append({"wave_id": wid, "model": label, "reason": "no consensus: deterministic solver fallback"})
        if not feasible_real(by_id[wid], r):
            out.append({"wave_id": wid, "model": label, "reason": "rule violation in final plan"})
        bd = r["baseline"]["evaluation"]["totals"]["delay_min"]
        nd = r["evaluation"]["totals"]["delay_min"]
        if status_quo_feasible(by_id[wid], r):
            bf = r["baseline"]["evaluation"]["totals"]["fuel_kg"]
            nf = r["evaluation"]["totals"]["fuel_kg"]
            if nd > bd + WORSE_DELAY_MIN:
                out.append({"wave_id": wid, "model": label,
                            "reason": f"regression vs status quo: delay {nd} > {bd} min"})
            if nf > bf + WORSE_FUEL_KG:
                out.append({"wave_id": wid, "model": label,
                            "reason": f"regression vs status quo: fuel {nf} > {bf} kg"})
    return out


def _arm_rows(variant: str, rep: dict, prev: dict, tol: float, labels: list[str]) -> list[dict]:
    pa = {a["label"]: a for a in prev.get("arms", [])}
    rows = []
    for a in rep.get("arms", []):
        if a["label"] not in labels:
            continue
        s = a["summary"]
        c = s.get("consensus_rate_pct")
        if c is None:
            raise ValueError(f"{variant} arm {a['label']}: consensus_rate_pct is missing")
        p = pa[a["label"]]["summary"].get("consensus_rate_pct")
        if p is None:
            raise ValueError(f"prev arm {a['label']}: consensus_rate_pct is missing")
        rows.append({"variant": variant, "label": a["label"], "consensus_rate_pct": c, "prev_consensus_rate_pct": p,
                     "eligible": c >= p - tol - TIE_EPS,
                     "worse_flight_pct": s.get("worse_flight_pct"),
                     "worse_than_status_quo_pct": s.get("worse_than_status_quo_pct"),
                     "prev_worse_than_status_quo_pct": (pa[a["label"]]["summary"].get("worse_than_status_quo_pct")
                                                        if "worse_than_status_quo_pct" in pa[a["label"]]["summary"]
                                                        else _archived_worse_pct(prev, pa[a["label"]].get("model"))),
                     "delay_min": s.get("delay_min"), "fuel_kg": s.get("fuel_kg"),
                     "model_calls_per_wave": s.get("model_calls_per_wave"), "latency_p95_ms": s.get("latency_p95_ms")})
    return rows


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


def pick_fairness(firm: dict, relaxing: dict, prev: dict) -> dict:
    """Which fairness variant ships. firm / relaxing / prev are benchmark reports (v3-traffic for prev). Both variants
    are scored on the same arms: the labels present in firm, relaxing and prev (ValueError when none). A variant
    passes iff in every such arm its consensus rate is >= the prev arm's minus one wave (100 / n_waves pp). Among
    passing variants the lower mean worse_flight_pct (per-flight share, agreed plans) wins (a difference under 0.05
    is a tie: firm). If neither passes the higher mean consensus rate wins (tie: firm) and `shortfall` is True.
    variant is therefore never None; the type allows None for old reports."""
    lab = lambda r: [a["label"] for a in r.get("arms", [])]   # noqa: E731
    labels = [x for x in lab(firm) if x in lab(relaxing) and x in lab(prev)]
    if not labels:
        raise ValueError(f"no arm label is shared by firm {lab(firm)}, relaxing {lab(relaxing)} and prev {lab(prev)}")
    n = firm.get("n_waves") or relaxing.get("n_waves") or 50
    tol = round(100.0 / n, 2)
    rows = _arm_rows("firm", firm, prev, tol, labels) + _arm_rows("relaxing", relaxing, prev, tol, labels)
    by = {v: [r for r in rows if r["variant"] == v] for v in ("firm", "relaxing")}
    ok = {v: all(r["eligible"] for r in by[v]) for v in by}
    cons = {v: _mean([r["consensus_rate_pct"] for r in by[v]]) for v in by}
    worse = {v: _mean([r["worse_flight_pct"] for r in by[v]]) for v in by}
    dropped = sorted({x for r in (firm, relaxing, prev) for x in lab(r)} - set(labels))
    rule = (f"A variant passes iff every arm's consensus rate >= the v3-traffic arm's minus one wave ({tol} pp at "
            f"n = {n}). Among passing variants the lower mean per-flight worse-than-status-quo share (agreed plans, any "
            "flight worse on delay or fuel) wins (difference under 0.05 is a tie: firm). If neither passes, the higher "
            "mean consensus rate wins and the shortfall is flagged. Scored on the arms common to both variants and "
            "v3-traffic. v3-traffic has wave totals only, so the comparison with it uses the wave-total share.")
    reasons = [f"{v}: " + ("passes" if ok[v] else "fails") + f" the consensus floor; mean consensus {_r(cons[v])}%, "
               f"mean worse-flight share {_r(worse[v])}%" for v in by]
    if dropped:
        reasons.append(f"arms not scored (missing in firm, relaxing or v3-traffic): {', '.join(dropped)}")
    for v in by:
        for r in by[v]:
            if not r["eligible"]:
                reasons.append(f"{v} arm {r['label']}: consensus {r['consensus_rate_pct']}% vs v3-traffic "
                               f"{r['prev_consensus_rate_pct']}% (more than one wave lower)")
    shortfall = not any(ok.values())
    wf, wr = worse["firm"], worse["relaxing"]
    if shortfall:
        win = "relaxing" if cons["relaxing"] > cons["firm"] + TIE_EPS else "firm"
        reasons.insert(0, f"Neither variant keeps consensus within one wave of v3-traffic; shipping {win} with the higher "
                          f"mean consensus ({_r(cons['firm'])}% firm vs {_r(cons['relaxing'])}% relaxing). Shortfall.")
    elif all(ok.values()):
        win = "relaxing" if wf is not None and wr is not None and wf - wr >= 0.05 - TIE_EPS else "firm"
        how = "a lower" if win == "relaxing" else "an equal or lower (within 0.05)"
        reasons.insert(0, f"Both variants pass; {win} has {how} mean worse-flight share "
                          f"(firm {_r(wf)}%, relaxing {_r(wr)}%).")
    else:
        win = next(v for v in by if ok[v])
        reasons.insert(0, f"Only {win} keeps consensus within one wave of v3-traffic in every arm.")
    # auto_variant: what this rule picked; a user override sets variant to the shipped one and adds override: "user"
    return {"variant": win, "auto_variant": win, "rule": rule, "reasons": reasons, "rows": rows, "shortfall": shortfall}


def _f(v) -> str:
    return "n/a" if v is None else str(v)


def markdown(rep: dict) -> str:
    L = [f"# Benchmark ({rep['n_waves']} waves, seed {rep['seed']}, source {rep.get('source', 'bts')})",
         f"Generated {rep['generated_at']}", "",
         "Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm "
         f"({rep['n_common']} of {rep['n_waves']}).", "",
         "## Arms vs status quo", "",
         "| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min "
         "| agree w/ formula % | Brier | p50 ms | p95 ms | error % |", "|" + "---|" * 15]
    b = rep["baseline"]
    L.append(f"| status quo | - | - | - | - | - | {b['delay_min']} | {b['fuel_kg']} | {b['pax_protected_pct']} "
             f"| {b['overtime_min']} | - | - | - | - | - |")
    for a in rep["arms"]:
        s = a["summary"]
        L.append(f"| {a['label']} | {a['n_ok']}/{rep['n_waves']} | {s['consensus_rate_pct']} | {s['mean_rounds']} "
                 f"| {s['fallback_rate_pct']} | {s['rule_violations_in_agreed_plans']} | {s['delay_min']} | {s['fuel_kg']} "
                 f"| {s['pax_protected_pct']} | {s['overtime_min']} | {s['agreement_with_formula_pct']} | {_f(s['brier'])} "
                 f"| {_f(s['latency_p50_ms'])} | {_f(s['latency_p95_ms'])} | {s['error_rate_pct']} |")
    op = rep.get("optimiser")
    if op:
        L.insert(L.index(f"| status quo | - | - | - | - | - | {b['delay_min']} | {b['fuel_kg']} | {b['pax_protected_pct']} "
                         f"| {b['overtime_min']} | - | - | - | - | - |") + 1,
                 f"| Optimiser (centralised beam search, no negotiation) | - | - | - | - | {op['n'] - op['n_feasible']} "
                 f"| {op['delay_min']} | {op['fuel_kg']} | {op['pax_protected_pct']} | - | - | - | - | - | - |")
    fo = b["feasible_only"]
    L += ["", f"Status quo infeasible on {len(b['infeasible_ids'])} waves: {', '.join(b['infeasible_ids']) or 'none'}.", ""]
    L += [f"- {a['label']} vs status quo on {b['n_feasible']} feasible-status-quo waves: delay "
          f"{a['delay_min_vs_feasible_status_quo']} min vs {fo['delay_min']} min" for a in rep["arms"]]
    if op:
        ci = lambda c: "n/a" if c is None else f"[{c[0]}, {c[1]}]"  # noqa: E731
        L += ["", f"Optimiser reference (n={op['n']}, {op['n_feasible']} feasible): mean delay {op['delay_min']} min, "
                  f"95% CI {ci(op['delay_min_ci95'])}; no models, no negotiation."]
        for a in rep["arms"]:
            sh = a.get("share_of_optimiser_gain_pct")
            L.append(f"- {a['label']}: delay 95% CI {ci(a.get('delay_min_ci95'))}; gap to optimiser "
                     f"{_f(a.get('gap_to_optimiser'))} min; share of optimiser gain over status quo "
                     f"{'n/a' if sh is None else f'{sh}%'} (n={a.get('share_n', 0)} waves with feasible status quo "
                     "beaten by the optimiser).")
    sel = rep["selection"]
    o = rep["outage_test"]
    ab = rep["ablation_no_engine_unsafe_pct"]
    L += ["", f"Status-quo diversions (mean): {b['diversions']}", "", "## Selection", "",
          f"**Winner:** {sel['winner']} (n = {rep['n_waves']} waves: indicative, not statistically significant)", "",
          f"Rule: {sel['rule']}", ""] + [f"- {r}" for r in sel["reasons"]]
    L += ["", "## Outage test", "",
          f"Primary `{o['primary_disabled']}` disabled on {o['waves']} wave(s): {o['resolved']} resolved. "
          f"Fallbacks used: {json.dumps(o['fallbacks_used'])}", "",
          "## Ablation: safety engine off", "",
          "not run" if ab is None else f"{ab}% of formula-arm final plans are infeasible under the real rules "
          "when the engine is disabled.", "", "## Failures", ""]
    L += [f"- `{f['wave_id']}` ({f['model']}): {f['reason']}" for f in rep["failures"]] or ["- none"]
    L += ["", "## Prompt budget (largest prompt of any kind)", "",
          f"Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + "
          f"options) per arm, against the {PROMPT_BUDGET_CHARS:,}-char "
          "(8k-token) budget:", ""]
    L += [f"- {a['label']}: {a['summary'].get('max_prompt_chars', 0):,} chars "
          f"({'within' if a['summary'].get('max_prompt_chars', 0) <= PROMPT_BUDGET_CHARS else 'OVER'} budget)"
          for a in rep["arms"]]
    L += ["", "## Engine veto counts", ""]
    L += [f"- {a['label']}: " + (", ".join(f"{r} {n}" for r, n in a.get("veto_counts", {}).items()) or "none")
          for a in rep["arms"]]
    L += ["", "## Engine history", "",
          "| engine | arm | delay min | fuel kg | lap share % | taxi wait min | pax prot % | overtime min "
          "| consensus % | violations | n feasible |", "|" + "---|" * 11]
    for h in rep.get("engine_history", []):
        L.append("| " + " | ".join(_f(h[k]) for k in ("engine_version", "arm", "delay_min", "fuel_kg", "lap_share_pct",
                                                        "taxi_wait_min", "pax_protected_pct", "overtime_min",
                                                        "consensus_rate_pct", "rule_violations_in_agreed_plans",
                                                        "n_feasible")) + " |")
    L += ["", "## Attribution", ""]
    at = rep.get("attribution")
    if not at:
        L.append("No earlier engine version is archived; nothing to attribute.")
    else:
        sg = lambda v: f"{v:+g}"   # noqa: E731
        better = {"delay_min": "lower", "fuel_kg": "lower", "lap_share_pct": "lower", "taxi_wait_min": "lower",
                  "pax_protected_pct": "higher", "overtime_min": "lower"}
        L += [f"Engine {at['from']} to {at['to']}. Better is: " + ", ".join(f"{k} {v}" for k, v in better.items()) + ".",
              ""] + [f"Note: {n}." for n in at.get("notes", [])]
        L += ["Physics (status quo under the new engine minus under the old): "
              + (", ".join(f"{k} {sg(v)}" for k, v in at["physics_delta"].items()) or "n/a")
              + ". The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; "
              "engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.",
              "",
              "Negotiation (arm minus status quo, same engine); headline claims come from these deltas:", ""]
        L += [f"- {arm}: " + (", ".join(f"{k} {sg(v)}" for k, v in d.items()) or "n/a")
              for arm, d in at["negotiation_delta"].items()]
        best = min(at["negotiation_delta"].items(), key=lambda kv: kv[1].get("delay_min", 0.0), default=None)
        if best and "delay_min" in best[1]:
            d = best[1]["delay_min"]
            L += ["", f"Headline: under engine {at['to']}, negotiation ({best[0]}) "
                      f"{'cuts' if d < 0 else 'adds'} {abs(d):g} min of mean delay against the status quo."]
        if not at.get("prev_has_per_wave", True):
            L += ["", f"No per-wave data in {at['from']}; regressions start next run."]
    rg = rep.get("regressions", [])
    if at and not at.get("prev_has_per_wave", True):
        L += ["", f"Regressions vs {at['from']} ({len(rg)}): empty by construction, not evidence of no regressions "
                  f"({at['from']} has no per-wave data to compare against).", ""]
    else:
        L += ["", f"Regressions ({len(rg)}):", ""]
        if (rep.get("engine_notes") or {}).get("definition_change"):
            L += ["Note: this version is a definition change, so the \"physics\" cause below means the new definition "
                  "moved the status quo, not flight physics.", ""]
    L += [f"- `{r['wave_id']}` ({r['arm']}) {r['metric']} {r['v_prev']} to {r['v_cur']}; {r['cause']}"
          for r in rg[:20]] or ["- none"]
    if len(rg) > 20:
        L.append(f"- ... and {len(rg) - 20} more in benchmark.json")
    fv = rep.get("fairness_variants")
    if fv:
        pk = fv["pick"]
        L += ["", "## Fairness variants", "",
              "Same waves and arms under each fairness variant. worse flight % (per-flight, what the pick ranks on) = % of "
              "agreed waves with a feasible status quo where any flight is worse than in the status quo on delay (> 0.05 min) "
              "or fuel (> 0.5 kg). worse wave-total % = % of waves with a feasible status quo whose final plan (consensus "
              "or fallback) has a worse wave-total delay or fuel. v3-traffic records wave totals only, so the comparison "
              "with it is on the wave-total share.", "",
              "| variant | arm | consensus % | v3-traffic consensus % | worse flight % | worse wave-total % "
              "| v3-traffic worse wave-total % | delay min | fuel kg | model calls/wave | p95 ms |", "|" + "---|" * 11]
        L += ["| " + " | ".join(_f(r.get(k)) for k in ("variant", "label", "consensus_rate_pct", "prev_consensus_rate_pct",
                                                        "worse_flight_pct", "worse_than_status_quo_pct",
                                                        "prev_worse_than_status_quo_pct", "delay_min", "fuel_kg",
                                                        "model_calls_per_wave", "latency_p95_ms")) + " |"
              for r in pk["rows"]]
        L += ["", f"**Pick:** {_f(pk['variant'])}" + (" (shortfall)" if pk.get("shortfall") else ""), "",
              f"Rule: {pk['rule']}", ""] + [f"- {r}" for r in pk["reasons"]]
    tv = rep.get("taxi_validation")
    if tv:
        L += ["", "## Taxi-in validation vs BTS 2015", "",
              "Focus flight of each benchmark wave on the real surface tier (OSM route) with a real BTS TAXI_IN, under the "
              "FCFS plan. Routed = ROT + unimpeded routed taxi (wheels-on to in-block, the span TAXI_IN measures). "
              "No fitting: speeds are fixed up front. BTS TAXI_IN includes gate waits and ramp delays, so routed < real "
              "(bias < 0) is expected; the bias is stated, not corrected.", "",
              f"n = {tv['n']}, MAE {_f(tv['mae_min'])} min, bias (routed - real) {_f(tv['bias_min'])} min.", "",
              "| airport | n | routed median min | BTS 2015 median min |", "|---|---|---|---|"]
        L += [f"| {ap} | {d['n']} | {d['routed_median']} | {_f(d['bts_median'])} |"
              for ap, d in tv["per_airport"].items()]
        L += ["", "Largest gaps (cause is a heuristic):", ""]
        L += [f"- `{r['wave_id']}` {r['callsign']}: routed {r['routed']} min, real {r['real']} min; {r['cause']}"
              for r in tv["worst"]] or ["- none"]
    cov = rep.get("coverage")
    if cov:
        L += ["", "## Coverage", "", f"Tier counts over the {rep['n_waves']} benchmark waves (waves that failed to build "
              "count as none):", ""]
        L += [f"- {layer}: " + ", ".join(f"{t} {n}" for t, n in tiers.items()) for layer, tiers in cov.items()]
    en = rep.get("engine_notes")   # hand-written analysis of the engine change, stored in benchmark.json
    if en:
        L += ["", f"## {en['title']}", ""] + [f"- {x}" for x in en.get("items", [])]
    L += ["", "## Limits", "",
          "- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.",
          "- Brier is against the formula verdict (utility >= reservation), not human judgement.",
          f"- n = {rep['n_waves']} waves: differences between arms are indicative, not statistically significant.",
          "- Cost is not measured (0.0)."] + [f"- {x}" for x in (en or {}).get("limits", [])]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--arms", default="formula")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--outage-n", type=int, default=20)
    ap.add_argument("--ablation", action="store_true", help="(default on)")
    ap.add_argument("--no-ablation", action="store_true")
    ap.add_argument("--source", choices=("bts", "flights2015"), default="flights2015")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--fairness", choices=("firm", "relaxing"), default=None,
                    help="fairness-floor variant for every arm, the ablation and the outage run (default: ATM_FAIRNESS)")
    ap.add_argument("--out", default=str(ROOT / "eval"))
    a = ap.parse_args(argv)
    from backend.wave import trajectory
    # worker threads interleave waves, so the shared plan cache gets 256 x concurrency entries for this run (the
    # server keeps the 256-entry default; see trajectory.PLAN_CACHE_MAX); restored afterwards (tests call main)
    prev = trajectory.PLAN_CACHE_MAX
    trajectory.PLAN_CACHE_MAX = max(prev, trajectory.PLAN_CACHE_PER_WORKER * max(1, a.concurrency))
    try:
        return _main(a)
    finally:
        trajectory.PLAN_CACHE_MAX = prev


def _main(a: argparse.Namespace) -> dict:
    arm_list = [x.strip() for x in a.arms.split(",") if x.strip()]
    if "formula" not in arm_list:
        arm_list.append("formula")  # needed for agreement_with_formula_pct
    waves, build_fail = load_waves(a.n, a.seed, a.fixtures, a.source)
    log(f"{len(waves)} waves, arms={arm_list}")

    all_runs: dict[str, dict] = {}
    for arm in arm_list:
        f = arm == "formula"
        all_runs[arm] = run_arm(arm, waves, ["formula"] if f else [arm, "formula"],
                                "formulas" if f else "decision", a.concurrency, arm, fairness=a.fairness)
    formula_runs = all_runs["formula"]
    common = {w.id for w in waves if all("result" in all_runs[arm].get(w.id, {}) for arm in arm_list)}

    by_id = {w.id: w for w in waves}
    sq_ok = sorted(w for w in common if status_quo_feasible(by_id[w], formula_runs[w]["result"]))
    sq_bad = sorted(common - set(sq_ok))
    keys = (*TOTAL_KEYS, "diversions")
    ok_f = [formula_runs[w]["result"] for w in common]
    sq_f = [formula_runs[w]["result"] for w in sq_ok]
    base = {k: mean([r["baseline"]["evaluation"]["totals"][k] for r in ok_f]) for k in keys}
    base["lap_share_pct"] = lap_share_pct({w: {"result": {"outcome": "consensus", "final_plan": formula_runs[w]["result"]["baseline"]["plan"]}}
                                           for w in common})
    base["taxi_wait_min"] = taxi_wait_mean([r["baseline"]["plan"]["assignments"] for r in ok_f])
    base["n_feasible"] = len(sq_ok)
    base["infeasible_ids"] = sq_bad
    base["feasible_only"] = {k: mean([r["baseline"]["evaluation"]["totals"][k] for r in sq_f]) for k in keys}

    opt = run_optimiser(waves, a.concurrency)
    optimiser = optimiser_summary(opt, common)
    sq_delay = {w: formula_runs[w]["result"]["baseline"]["evaluation"]["totals"]["delay_min"] for w in common}
    arms, failures = [], list(build_fail)
    for arm in arm_list:
        prov, model = arm_parts(arm)
        summ, extra = summarize(all_runs[arm], formula_runs, waves, common, PROMPT_MAX.get(arm, 0))
        arm_delay = mean([all_runs[arm][w]["result"]["evaluation"]["totals"]["delay_min"] for w in sq_ok])
        arms.append({"model": model, "provider": prov, "label": model, "summary": summ,
                     "delay_min_vs_feasible_status_quo": arm_delay, "veto_counts": veto_counts(all_runs[arm]), **extra,
                     **arm_vs_optimiser({w: all_runs[arm][w]["result"]["evaluation"]["totals"]["delay_min"]
                                         for w in common}, sq_delay, set(sq_ok), opt, common)})
        failures += collect_failures(model, all_runs[arm], waves)

    selection, best_model = select(arms, len(waves), base)
    if len(waves) < a.n:
        selection["reasons"].append(f"loaded {len(waves)} of {a.n} requested waves")
    outage_arm = next((x for x in arm_list if best_model and x != "formula" and arm_parts(x)[1] == best_model["model"]),
                      None)
    outage, notes = run_outage(outage_arm, waves, a.outage_n, a.concurrency, fairness=a.fairness)
    if outage_arm and selection["winner"] == "formula":
        selection["reasons"].append(f"Outage test ran with the best eligible model arm {best_model['label']} "
                                    "(overall winner is formula).")
    selection["reasons"] += notes
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_waves": len(waves), "n_common": len(common), "seed": a.seed,
        "source": "fixtures" if a.fixtures else a.source, "arms": arms, "baseline": base,
        "optimiser": optimiser,
        "ablation_no_engine_unsafe_pct": None if a.no_ablation else run_ablation(waves, fairness=a.fairness),
        "outage_test": outage,
        "selection": selection, "failures": failures, "wave_ids": [w.id for w in waves],
    }
    report = {"engine_version": ENGINE_VERSION, "fairness": a.fairness or fair.current(), **report}
    report["coverage"] = coverage(waves, n_failed=len(build_fail))
    report["taxi_validation"] = taxi_validation(waves, {w: x["result"] for w, x in formula_runs.items() if "result" in x})
    report["per_wave"] = {model: per_wave_table(all_runs[arm], common)
                          for arm in arm_list for model in [arm_parts(arm)[1]]}
    archive = [r for r in load_archive(ROOT / "eval" / "archive") if r["engine_version"] != ENGINE_VERSION]
    prev = archive[-1] if archive else None
    report["per_wave"]["baseline"] = per_wave_baseline(formula_runs, common)
    report["regressions"] = find_regressions(report["per_wave"], (prev or {}).get("per_wave"))
    report["engine_history"] = engine_history(ROOT / "eval" / "archive", report)
    report["attribution"] = ({"from": prev["engine_version"], "to": ENGINE_VERSION,
                              "prev_has_per_wave": bool(prev.get("per_wave")), **attribution(prev, report)}
                             if prev else None)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(report, indent=2))
    (out / "BENCHMARK.md").write_text(markdown(report))
    md = (out / "BENCHMARK.md").read_text()
    default = out.resolve() == (ROOT / "eval").resolve()
    store = get_store()
    store.put_benchmark(report, md, label="main" if default else out.name, current=default)
    st = store.status()
    if st.degraded:   # Postgres configured (DATABASE_URL_POOLED or ATM_STORE=pg) but the write fell back to files
        log(f"WARNING: benchmark written to files only; the database write fell back ({st.reason})")
    log(f"wrote {out}/benchmark.json and BENCHMARK.md")
    return report


if __name__ == "__main__":
    main()
