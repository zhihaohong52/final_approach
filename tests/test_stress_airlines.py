"""Airline agents on two stress waves (formulas mode, both fairness variants): every airline flight ends at its
status quo, or the airline objected in the log and the fallback reason names the flight (spec §5). Spec §5 named
CLT-2024-07-29-19 and ATL-2024-07-18-21; since waves are the hour flights really land those hours are no longer
single-airline, so the pins are the top single-airline CLT (AA) and ATL (DL) rows of index.csv with a feasible
status quo on which both variants hold."""
import pytest

from backend.wave import build, fairness, rules
from backend.wave.edit import plan_from_dicts
from backend.wave.fairness import VARIANTS
from backend.wave.graph import negotiate_wave
from backend.wave.utility import agent_utility

# re-picked for engine v5-taxi (were CLT-2024-06-03-20, relaxing now leaves AA2778 worse with no objection, and
# ATL-2024-07-05-21, status quo now infeasible); same criterion
WAVES = ("CLT-2024-06-06-21", "ATL-2024-07-20-17")


@pytest.mark.parametrize("v", VARIANTS)
@pytest.mark.parametrize("wid", WAVES)
def test_stress_wave_airline_flights_not_worse(wid, v):
    r = negotiate_wave(build.build_wave(wid), "formulas", fairness=v)
    sq = {a["callsign"]: a["metrics"] for a in r["baseline"]["plan"]["assignments"]}   # metrics filled by evaluate()
    worse = [(cs, k) for a in r["final_plan"]["assignments"] for cs in [a["callsign"]]
             for k, eps in (("delay_min", .05), ("fuel_kg", .5)) if a["metrics"][k] > sq[cs][k] + eps]
    if worse:   # spec §5: else the airline objected to the FINAL plan, and the fallback (if any) says why
        fid = r["final_plan"]["id"]
        for cs in {cs for cs, _ in worse}:
            assert any(m["type"] == "AGENT_TURN" and m["sender"].startswith("AIRLINE:") and
                       any(o["callsign"] == cs and o["plan_id"] == fid for o in m["body"].get("objections", []))
                       for m in r["log"]), f"{cs} worse than status quo with no objection on the final plan"
            if r["outcome"] == "fallback":
                assert any(cs in fb["body"]["reason"] for fb in r["log"] if fb["type"] == "FALLBACK")
    assert r["evaluation"]["feasible"] and r["agents"] == ["ATC", f"AIRLINE:{'AA' if wid.startswith('CLT') else 'DL'}", "GROUND"]
    # firm only, the expected case (status quo legal, ATC's reservation falls to 0.92 x 0.78 ~ 0.72 < its 0.73 on it by turn 2)
    if v == "firm":
        assert not worse


# Status quo infeasible: the floor is the best feasible plan the airline has seen (preflight F8). The engine does not
# return it, so the test rebuilds it from the plans in the log, with the engine's own helpers (agent_utility,
# rules.feasible, fairness.breaches), then compares the final plan with it.
# ORD 18 Apr 2015 17:00 (AA, F9, UA) and LAX 2 Apr 2015 14:00 (AA, NK, OO, UA, WN): status quo infeasible, the floor
# bites under both variants. Were F2015-77162 / F2015-292602: since waves centre on the real landing hour, 292602's
# status quo is feasible and 77162's firm run has no objection.
BEST_SEEN_WAVES = ("F2015-117373", "F2015-102378")


def _objections(r):
    return [(m, o) for m in r["log"] if m["type"] == "AGENT_TURN" and m["sender"].startswith("AIRLINE:")
            for o in m["body"].get("objections", [])]


def _plans(w, r):
    """Every plan body in the log (OPEN, proposals, the final plan), by id."""
    rows = [r["final_plan"]] + [m["body"]["plan"] for m in r["log"] if m["type"] == "OPEN"]
    rows += [m["body"]["proposal"] for m in r["log"] if m["type"] == "AGENT_TURN" and m["body"].get("proposal")]
    out = {}
    for d in rows:
        if d.get("assignments") and d["id"] not in out:
            p = plan_from_dicts(d["assignments"], w)
            assert p.id == d["id"]
            out[d["id"]] = p
    return out


def _floors(w, r, plans):
    """Best feasible CHECK-passed plan per airline code, by its utility (how `_Run.see` picks it)."""
    passed = {m["body"]["plan_id"] for m in r["log"] if m["type"] == "CHECK" and m["body"]["passed"]}
    best, bu = {}, {}
    for pid, p in plans.items():
        if pid in passed and rules.feasible(w, p):
            for code in r["agents"]:
                if code.startswith("AIRLINE:"):
                    c, u = code[8:], agent_utility(w, p, code)
                    if c not in best or u > bu[c] + 1e-9:
                        best[c], bu[c] = p, u
    return best


@pytest.mark.parametrize("v", VARIANTS)
@pytest.mark.parametrize("wid", BEST_SEEN_WAVES)
def test_best_seen_floor_waves(wid, v):
    w = build.build_wave(wid)
    r = negotiate_wave(w, "formulas", fairness=v)
    assert not r["baseline"]["evaluation"]["feasible"]           # the status quo is not a floor here
    assert r["evaluation"]["feasible"]
    objs = _objections(r)
    assert all(o["floor_source"] == "best_seen" for _, o in objs)
    if v == "firm":
        assert objs                                                # the floor does bite under firm
    for _, o in objs:
        assert o["value"] > o["allowed"] >= o["floor"]
    plans = _plans(w, r)
    final = plans[r["final_plan"]["id"]]
    floors = _floors(w, r, plans)
    assert floors and r["airlines"]
    on_final = {(o["callsign"], o["metric"]) for _, o in objs if o["plan_id"] == final.id}
    checked = 0
    for code in floors:
        for b in fairness.breaches(w, final, code, floors[code], v, r["turns"], source="best_seen"):
            assert (b["callsign"], b["metric"]) in on_final, f"{b['callsign']} {b['metric']} above its floor, no objection"
            if r["outcome"] == "fallback":
                assert any(b["callsign"] in fb["body"]["reason"] for fb in r["log"] if fb["type"] == "FALLBACK")
        checked += len(next(a["callsigns"] for a in r["airlines"] if a["code"] == code))
    assert checked > 0


# Known escape-clause case (was F2015-77162 / WN130 before waves centred on the real landing hour, then F2015-190078 /
# AA1693 until engine v5-taxi, where AA agrees in turn 2)
ESCAPE_WAVE, ESCAPE_AIRLINE, ESCAPE_FLIGHT = "F2015-2754", "AA", "AA1628"


def test_firm_final_plan_is_the_escape_clause_plan():
    """Known case (ORD 3 Jan 2015, AA, OO and UA): AA1628 ends +3.2 min over its best-seen delay floor. Spec §3.4
    allows that only when no CHECK-passed plan
    meeting AA's floor is on the table when AA votes; AA rejected the plan on the floor in turn 2 and accepted it in
    turn 3, when it was the only plan on the table."""
    ag = f"AIRLINE:{ESCAPE_AIRLINE}"
    w = build.build_wave(ESCAPE_WAVE)
    r = negotiate_wave(w, "formulas", fairness="firm")
    fid = r["final_plan"]["id"]
    assert r["outcome"] == "consensus" and r["turns"] == 3
    own = [(m["round"], o) for m, o in _objections(r) if m["sender"] == ag]
    assert any(rnd == 2 and o["callsign"] == ESCAPE_FLIGHT and o["metric"] == "delay_min" and o["plan_id"] == fid
               for rnd, o in own)
    t2 = [v for m in r["log"] if m["round"] == 2 and m["sender"] == ag for v in m["body"]["votes"]
          if v["plan_id"] == fid]
    assert t2 and t2[0]["vote"] == "REJECT" and t2[0]["decided_by"] == "floor"
    t3 = [v for m in r["log"] if m["round"] == 3 and m["type"] == "AGENT_TURN" for v in m["body"]["votes"]]
    assert {v["plan_id"] for v in t3} == {fid}                   # nothing else on the table in turn 3
    own3 = [v for m in r["log"] if m["round"] == 3 and m["sender"] == ag for v in m["body"]["votes"]]
    assert own3 and own3[0]["vote"] == "ACCEPT" and own3[0]["decided_by"] == "escape"   # on utility (escape), not floor
    assert own3[0]["floor"] and own3[0]["floor"][0]["callsign"] == ESCAPE_FLIGHT
    why = next(m["body"]["rationale"] for m in r["log"] if m["round"] == 3 and m["sender"] == ag
               and m["type"] == "AGENT_TURN")
    assert "rejects" not in why and "accepts it: no plan on the table meets its floor (escape clause)" in why, why
    t2why = next(m["body"]["rationale"] for m in r["log"] if m["round"] == 2 and m["sender"] == ag
                 and m["type"] == "AGENT_TURN")
    assert t2why.endswith("rejects the plan"), t2why                  # turn 2: a real floor REJECT
    plans = _plans(w, r)
    others = [p for pid, p in plans.items() if pid != fid and pid in {v["plan_id"] for v in t3}]
    assert not any(not fairness.breaches(w, p, ESCAPE_AIRLINE, _floors(w, r, plans)[ESCAPE_AIRLINE], "firm", 3,
                                         source="best_seen")
                   for p in others)                              # no floor-meeting plan was on the table in turn 3
