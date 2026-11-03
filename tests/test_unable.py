from dataclasses import replace

from backend.wave import rules
from backend.wave.moves import fcfs_plan
from backend.wave.model import JointPlan, Move
from backend.wave.unable import evidence_words, pilot_side, unable_body
from tests.fixtures import make_wave
from tests.test_graph import _hard_wave


def _late():
    w = make_wave()
    p = fcfs_plan(w)
    asg = [replace(a, slot_min=a.slot_min + 20) if a.callsign == "AA302" else a for a in p.assignments]
    return w, JointPlan(asg)


def _fuel(w, late):
    return next(c for c in rules.check(w, late) if c.rule == "PLT-FUEL-RESERVE" and c.callsign == "AA302")


def test_fuel_unable_text():
    w, late = _late()
    c = _fuel(w, late)
    b = unable_body(1, c, None, late.id, "ATC")
    assert pilot_side(c) and b["agent"] == "PILOT:AA302" and b["text"].startswith("UNABLE ATC's plan: ")
    assert b["text"].endswith(", PLT-FUEL-RESERVE") and "16" in b["text"] and b["source"] == c.source
    assert b["type"] == "UNABLE" and b["turn"] == 1 and b["instruction"] is None
    assert b["plan_id"] == late.id and b["proposed_by"] == "ATC" and b["callsign"] == "AA302"


def test_not_pilot_side():
    w, late = _late()
    p = fcfs_plan(w)
    for c in rules.check(w, p):
        assert not pilot_side(c)                      # all pass on FCFS
    fake = [rules.RuleCheck("GND-GATE-FREE", "GROUND", False, "x", "s", {}, "AA302"),
            rules.RuleCheck("ATC-WAKE-SEP", "ATC", False, "x", "s", {}, "AA302"),
            rules.RuleCheck("PLT-FUEL-RESERVE", "PILOT", False, "x", "s", {}, None)]
    assert not any(pilot_side(c) for c in fake)


def test_divert_check_words():
    w = make_wave()
    c = rules._divert_check(w.flights[0], w.flights[0].callsign)
    c = replace(c, passed=False, detail="AA302 cannot divert: no alternate")
    assert "need_min" not in c.evidence
    assert pilot_side(c)
    assert evidence_words(c) == c.detail[:120]
    assert unable_body(1, c, None, "p", "ATC")["text"].endswith(", PLT-FUEL-RESERVE")


def test_wind_is_pilot_side():
    w = _hard_wave()
    p = fcfs_plan(w)
    c = next(c for c in rules.check(w, p) if c.rule == "ATC-WIND-LIMITS" and not c.passed)
    assert pilot_side(c)
    words = evidence_words(c)
    assert "crosswind" in words and f"{c.evidence['limit_crosswind_kt']:g}" in words and "kt" in words


def test_eta_words_and_fallback():
    w = make_wave()
    p = fcfs_plan(w)
    a0 = p.assignments[0]
    bad = JointPlan([replace(a0, slot_min=a0.slot_min - 30)] + list(p.assignments[1:]))
    c = next(c for c in rules.check(w, bad) if c.rule == "PLT-EARLIEST-ETA" and not c.passed)
    words = evidence_words(c)
    assert words.startswith("slot T") and "before earliest T" in words
    c2 = replace(c, evidence={**c.evidence, "slot_min": "none"})
    assert evidence_words(c2) == c.detail[:120]


def test_crew_duty_uses_detail():
    c = rules.RuleCheck("PLT-CREW-DUTY", "PILOT", False, "AA302: duty ends too soon", "src", {}, "AA302")
    assert pilot_side(c) and evidence_words(c) == "AA302: duty ends too soon"


def test_dict_forms_and_move_label():
    w, late = _late()
    c = _fuel(w, late)
    mv = Move(id="m1", agent="ATC", kind="shift_slot", callsigns=["AA302"], params={}, label="Push AA302 20 min")
    b_obj = unable_body(2, c, mv, late.id, "ATC")
    b_dict = unable_body(2, c.to_dict(), mv.to_dict(), late.id, "ATC")
    assert pilot_side(c.to_dict())
    assert b_obj == b_dict
    assert b_obj["text"].startswith("UNABLE push AA302 20 min: ")
    assert b_obj["instruction"]["id"] == "m1"


def test_engine_proposer_reads_as_opening_plan():
    w, late = _late()
    b = unable_body(0, _fuel(w, late), None, late.id, "ENGINE")
    assert b["text"].startswith("UNABLE the opening plan: ")
