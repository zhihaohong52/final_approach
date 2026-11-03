import pytest
from backend.wave import build as B
from backend.wave import edit
from backend.wave.graph import negotiate_wave
from tests.test_rules import _fixture_movements  # noqa: F401  (pytest fixture: committed 2015 movements)


def _agreed(wave_id="F2015-270287"):
    w = B.build_wave(wave_id)
    return w, negotiate_wave(w, "formulas")["final_plan"]["assignments"]


def test_unchanged_plan_passes():
    w, rows = _agreed()
    out = edit.check_edit(w, edit.plan_from_dicts(rows))
    assert out["evaluation"]["feasible"] and out["suggestions"] == {}


def test_edit_that_breaks_wake_separation_is_caught_and_a_legal_slot_suggested():
    w, rows = _agreed()
    rows = [dict(r) for r in rows]
    lead = next(r for r in rows if r["callsign"] == "OO5559")
    other = next(r for r in rows if r["callsign"] == "MQ3318")
    other.update(runway=lead["runway"], slot_min=lead["slot_min"] + 0.5)
    out = edit.check_edit(w, edit.plan_from_dicts(rows))
    failed = {(c["rule"], c["callsign"]) for c in out["evaluation"]["checks"] if not c["passed"]}
    assert ("ATC-WAKE-SEP", "MQ3318") in failed
    s = out["suggestions"].get("MQ3318")
    assert s is not None and abs(s - (lead["slot_min"] + 0.5)) <= edit.SEARCH_MIN
    fixed = [dict(r, slot_min=s) if r["callsign"] == "MQ3318" else r for r in rows]
    assert edit.check_edit(w, edit.plan_from_dicts(fixed))["evaluation"]["feasible"]


@pytest.mark.usefixtures("_fixture_movements")
def test_suggestion_also_clears_breaches_that_name_the_edited_flight():
    """Review T31: with the 2015 ground traffic, moving MQ3318 0.5 min behind OO5559 also leaves OO5559 still on the
    runway at MQ3318's landing (ATC-TAXI-CONFLICT of OO5559, evidence blocked_by MQ3318). That breach names MQ3318,
    so its suggested slot must clear it too: the suggested plan is feasible."""
    test_edit_that_breaks_wake_separation_is_caught_and_a_legal_slot_suggested()
    w, rows = _agreed()
    rows = [dict(r) for r in rows]
    lead = next(r for r in rows if r["callsign"] == "OO5559")
    other = next(r for r in rows if r["callsign"] == "MQ3318")
    other.update(runway=lead["runway"], slot_min=lead["slot_min"] + 0.5)
    ev = edit.check_edit(w, edit.plan_from_dicts(rows))["evaluation"]
    named = [c for c in ev["checks"] if not c["passed"] and c["callsign"] != "MQ3318" and "MQ3318" in edit._named(c)]
    assert any(c["rule"] == "ATC-TAXI-CONFLICT" and c["callsign"] == "OO5559" for c in named)


def test_suggestion_must_clear_a_breach_naming_the_flight(monkeypatch):
    """Review T31 (deterministic, no data): B's edit breaks wake separation and leaves A on the runway at B's landing
    (A's ATC-TAXI-CONFLICT, evidence blocked_by B). The first trial slot keeps A's breach; it must not be suggested
    although A's breach was there before: B caused it. The second trial slot clears both."""
    w, rows = _agreed()
    rows = [dict(r) for r in rows]
    b = next(r for r in rows if r["callsign"] == "MQ3318"); a_cs = "OO5559"
    s0 = b["slot_min"]

    def chk(rule, cs, ok, **ev):
        return {"rule": rule, "callsign": cs, "passed": ok, "detail": "", "evidence": ev}

    def fake_eval(wave, plan):
        s = plan.get("MQ3318").slot_min
        if s == s0:
            checks = [chk("ATC-WAKE-SEP", "MQ3318", False, leader=a_cs),
                      chk("ATC-TAXI-CONFLICT", a_cs, False, blocked_by="MQ3318")]
        elif s == s0 + 1:
            checks = [chk("ATC-TAXI-CONFLICT", a_cs, False, blocked_by="MQ3318")]
        else:
            checks = [chk("ATC-TAXI-CONFLICT", a_cs, True)]
        return {"checks": checks, "feasible": all(c["passed"] for c in checks)}
    monkeypatch.setattr(edit, "evaluate", fake_eval)
    monkeypatch.setattr(edit, "_floor", lambda wave, a: None)
    out = edit.check_edit(w, edit.plan_from_dicts(rows))
    assert out["suggestions"]["MQ3318"] == s0 - 1


def test_runways_follow_the_wave_date():
    on_2015 = {r.id for r in B._runways("ORD", "2015-08-19")}
    assert "09C" not in on_2015 and "10R" not in on_2015      # opened 2020-11-05 and 2015-10-15
    assert {"09C", "10R"} <= {r.id for r in B._runways("ORD", "2024-07-01")}
    plan = negotiate_wave(B.build_wave("F2015-270287"), "formulas")["final_plan"]["assignments"]
    assert not {a["runway"] for a in plan} & {"09C", "27C", "10R", "28L"}


def test_evaluate_endpoint():
    from fastapi.testclient import TestClient
    from backend import server
    c = TestClient(server.app)
    _, rows = _agreed()
    r = c.post("/api/evaluate", json={"wave_id": "F2015-270287", "assignments": rows})
    assert r.status_code == 200 and r.json()["evaluation"]["feasible"]
    assert c.post("/api/evaluate", json={"wave_id": "F2015-270287", "assignments": []}).status_code == 422
    assert c.post("/api/evaluate", json={"wave_id": "nope", "assignments": rows}).status_code == 404


def test_runway_history_layouts():
    from backend.wave import build as B
    ids = lambda a, d: {r.id: r for r in B._runways(a, d)}
    ord15 = ids("ORD", "2015-08-19")
    assert "09C" not in ord15 and "10R" not in ord15                 # opened 2020 / 2015-10-15
    assert ord15["14L"].open and ord15["09R"].length_ft == 7967     # closed 2015-08-20; extended 2021
    assert not ord15["32L"].open and ord15["32L"].closed_reason == "closed to arrivals"
    assert "14L" not in ids("ORD", "2015-08-20")
    assert {"01", "19"} <= set(ids("MSY", "2015-06-01"))             # renamed 02/20 on 2015-10-15
    mdw = ids("MDW", "2015-06-01")
    assert mdw["13L"].length_ft == 5141 and "13C" in mdw             # old 13L and today's 13L (then 13C)
    msp = ids("MSP", "2024-07-01")
    assert not msp["12L"].open and msp["12R"].open                   # construction Jun-Sep 2024
    assert set(ids("ORD", None)) == set(ids("ORD", "2026-11-01"))
