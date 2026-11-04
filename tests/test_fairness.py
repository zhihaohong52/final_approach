import logging

import pytest

from backend.wave import fairness, utility
from backend.wave.fairness import FAIRNESS_DEFAULT, VARIANTS, breaches, current, floor_source, slack
from backend.wave.model import FlightMetrics
from backend.wave.moves import fcfs_plan
from backend.wave.utility import (DELAY_CAP_MIN, DIVERT_FUEL_TERM, NEXT_DEP_CAP_MIN, W_AIRLINE, agent_terms,
                                  agent_utility, fuel_info, hold_burn, metrics_map, pilots, voters)
from tests.fixtures import make_wave


def test_negotiators_and_pilots():
    w = make_wave()
    assert voters(w) == ["ATC", "AIRLINE:AA", "AIRLINE:DL", "AIRLINE:UA", "GROUND"]
    assert pilots(w) == ["PILOT:DL100", "PILOT:DL201", "PILOT:AA302", "PILOT:UA403"]
    # from A5 on the negotiators are the voters
    assert utility.negotiators(w) == voters(w)


def _flight_terms(w, p, mm, cs):
    """Per-flight normalised penalties, computed here independently of the AIRLINE branch."""
    a, f, m = p.get(cs), w.flight(cs), mm[cs]
    clip = lambda x: min(max(x, 0.0), 1.0)
    fuel = DIVERT_FUEL_TERM if a.action == "divert" else clip(m.fuel_kg / (max(fuel_info(f)[0], 1.0) * hold_burn(f)))
    return {"fuel": fuel, "delay": clip(m.delay_min / DELAY_CAP_MIN),
            "connections": clip(1 - m.pax_protected / m.pax_total) if m.pax_total else 0.0,
            "next_departure": clip(m.next_dep_delay_min / NEXT_DEP_CAP_MIN),
            "divert": 1.0 if a.action == "divert" else 0.0}


def test_airline_utility_is_own_flights_mean():
    # AIRLINE:DL owns DL100 and DL201: its utility is 1 minus the W_AIRLINE-weighted mean of their per-flight terms
    w = make_wave()
    p = fcfs_plan(w)
    mm = metrics_map(w, p)
    per = [_flight_terms(w, p, mm, cs) for cs in ("DL100", "DL201")]
    expected = {k: -W_AIRLINE[k] * sum(t[k] for t in per) / 2 for k in W_AIRLINE}
    terms = agent_terms(w, p, "AIRLINE:DL")
    assert set(terms) == set(W_AIRLINE)
    assert all(v <= 0 for v in terms.values())
    assert terms == pytest.approx(expected)
    assert agent_utility(w, p, "AIRLINE:DL") == round(min(max(1 + sum(expected.values()), 0), 1), 3)
    assert 0 <= agent_utility(w, p, "AIRLINE:DL") <= 1
    # weights sum to 1, so a flight with no penalty at all scores 1
    assert sum(W_AIRLINE.values()) == pytest.approx(1.0)
    # the PILOT branch is kept (fallback and MCP use it)
    assert set(agent_terms(w, p, "PILOT:DL201")) == {"fuel", "delay", "connections", "divert"}


def test_airline_focus_weight(monkeypatch):
    w = make_wave()
    p = fcfs_plan(w)
    mm = metrics_map(w, p)
    assert utility.FOCUS_WEIGHT == 1.0
    monkeypatch.setattr(utility, "FOCUS_WEIGHT", 3.0)   # DL100 is the focus flight
    t100, t201 = (_flight_terms(w, p, mm, cs) for cs in ("DL100", "DL201"))
    expected = {k: -W_AIRLINE[k] * (3 * t100[k] + t201[k]) / 4 for k in W_AIRLINE}
    assert agent_terms(w, p, "AIRLINE:DL") == pytest.approx(expected)


def test_unknown_airline_raises():
    w = make_wave()
    with pytest.raises(ValueError):
        agent_terms(w, fcfs_plan(w), "AIRLINE:ZZ")


def _mm(delay, fuel):
    return {"DL100": FlightMetrics(delay_min=10.0, fuel_kg=0.0), "DL201": FlightMetrics(delay_min=delay, fuel_kg=fuel)}


def test_firm_zero_fuel_floor():
    # floor metrics with 0 kg on DL201, candidate with 1 kg on DL201: the fuel cap is 30 min x 40 kg/min = 1200 kg,
    # but firm has no slack, so the candidate breaches at allowed == 0 + 0 + 0.5
    w = make_wave()
    p = fcfs_plan(w)
    out = breaches(w, p, "DL", p, "firm", 1, mm_floor=_mm(10.0, 0.0), mm_plan=_mm(10.0, 1.0), source="status_quo")
    assert out == [{"callsign": "DL201", "metric": "fuel_kg", "floor": 0.0, "value": 1.0, "allowed": 0.5,
                    "floor_source": "status_quo"}]
    # the eps absorbs metric rounding only: 0.4 kg over is not a breach
    assert breaches(w, p, "DL", p, "firm", 1, mm_floor=_mm(10.0, 0.0), mm_plan=_mm(10.0, 0.4), source="status_quo") == []


def test_firm_delay_floor_and_label():
    w = make_wave()
    p = fcfs_plan(w)
    out = breaches(w, p, "DL", p, "firm", 2, mm_floor=_mm(10.0, 0.0), mm_plan=_mm(10.1, 0.0),
                   source="best_seen")
    assert out == [{"callsign": "DL201", "metric": "delay_min", "floor": 10.0, "value": 10.1,
                    "allowed": pytest.approx(10.05), "floor_source": "best_seen"}]
    assert breaches(w, p, "DL", p, "firm", 1, mm_floor=_mm(10.0, 0.0), mm_plan=_mm(10.05, 0.0), source="status_quo") == []
    with pytest.raises(ValueError):
        breaches(w, p, "DL", p, "firm", 1, source="original")
    with pytest.raises(ValueError):
        breaches(w, p, "DL", p, "lenient", 1, source="status_quo")
    with pytest.raises(ValueError):
        breaches(w, p, "ZZ", p, "firm", 1, source="status_quo")


def test_relaxing_admits_fuel_from_turn_1():
    assert slack("relaxing", 1) == pytest.approx(0.10) and slack("relaxing", 2) == pytest.approx(0.22)
    assert slack("relaxing", 3) == pytest.approx(0.34) and slack("firm", 3) == 0
    w = make_wave()
    p = fcfs_plan(w)
    # the same 1 kg that breaches under firm is inside the turn-1 allowance (0.10 x 1200 + 0.5 kg)
    assert breaches(w, p, "DL", p, "relaxing", 1, mm_floor=_mm(10.0, 0.0), mm_plan=_mm(10.0, 1.0), source="status_quo") == []
    # delay: turn 1 allows floor + 0.10 x 60 + 0.05 min; 6.1 min over breaches, 6.0 does not
    assert breaches(w, p, "DL", p, "relaxing", 1, mm_floor=_mm(10.0, 0.0), mm_plan=_mm(16.0, 0.0), source="status_quo") == []
    out = breaches(w, p, "DL", p, "relaxing", 1, mm_floor=_mm(10.0, 0.0), mm_plan=_mm(16.1, 0.0), source="status_quo")
    assert [(b["callsign"], b["metric"]) for b in out] == [("DL201", "delay_min")]
    assert out[0]["allowed"] == pytest.approx(16.05)


def test_breaches_real_metrics():
    w = make_wave()
    p = fcfs_plan(w)
    assert breaches(w, p, "DL", p, "firm", 1, source="status_quo") == []          # a plan never breaches its own floor
    assert breaches(w, p, "DL", None, "firm", 1, source="status_quo") == []       # no floor yet: nothing to breach (F5)


def test_floor_source():
    w = make_wave()
    sq, best = fcfs_plan(w), fcfs_plan(w)
    assert floor_source(w, sq, True, None) is sq and floor_source(w, sq, False, None) is None
    assert floor_source(w, sq, False, best) is best
    assert floor_source(w, sq, True, best) is sq


def test_current(monkeypatch, caplog):
    assert VARIANTS == ("firm", "relaxing") and FAIRNESS_DEFAULT == "relaxing"
    monkeypatch.delenv("ATM_FAIRNESS", raising=False)
    monkeypatch.setattr(fairness, "_warned", set())
    assert current() == FAIRNESS_DEFAULT
    monkeypatch.setenv("ATM_FAIRNESS", "firm")
    assert current() == "firm"
    monkeypatch.setenv("ATM_FAIRNESS", "bogus")
    with caplog.at_level(logging.WARNING, logger="backend.wave.fairness"):
        assert current() == FAIRNESS_DEFAULT
        assert current() == FAIRNESS_DEFAULT      # warned once per bad value, not on every call
    assert len([r for r in caplog.records if "ATM_FAIRNESS" in r.getMessage()]) == 1
    monkeypatch.setenv("ATM_FAIRNESS", "")
    assert current() == FAIRNESS_DEFAULT
    assert not hasattr(fairness, "set_override") and not hasattr(fairness, "_override")   # per-call only (I-1)


def test_source_is_required_keyword():
    w = make_wave()
    p = fcfs_plan(w)
    with pytest.raises(TypeError):
        breaches(w, p, "DL", p, "firm", 1)


def test_slack_turn_range():
    for rnd in (0, 4, -1):
        with pytest.raises(ValueError):
            slack("relaxing", rnd)
        with pytest.raises(ValueError):
            slack("firm", rnd)
    w = make_wave()
    p = fcfs_plan(w)
    with pytest.raises(ValueError):
        breaches(w, p, "DL", p, "relaxing", 0, source="status_quo")


def test_fuel_budget_shared():
    w = make_wave()
    assert utility.fuel_budget(w.flight("DL201")) == pytest.approx(1200.0)   # 30 min x 2400 kg/h
    assert utility.fuel_budget(w.flight("DL100")) == pytest.approx(3250.0)   # 30 min x 6500 kg/h


def test_focus_callsign_skipped_at_unit_weight(monkeypatch):
    w = make_wave()
    p = fcfs_plan(w)
    mm = metrics_map(w, p)
    def boom(_):
        raise AssertionError("focus_callsign called with FOCUS_WEIGHT == 1.0")
    monkeypatch.setattr(utility, "focus_callsign", boom)
    agent_terms(w, p, "AIRLINE:DL", mm)
