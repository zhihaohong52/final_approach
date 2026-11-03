import pytest
from backend.wave import holding as H

def test_lap_times_pinned():
    assert H.lap_min(12000) == pytest.approx(5.36, abs=0.03)   # 230 KIAS, 25 deg bank
    assert H.lap_min(5000) == pytest.approx(4.59, abs=0.03)    # 200 KIAS
    assert H.lap_min(16000) == pytest.approx(7.12, abs=0.05)   # 265 KIAS, 1.5-min legs

def test_turn_rate_uses_least_bank():
    assert H.turn_rate_dps(H.tas_kt(230, 12000)) == pytest.approx(1.786, abs=0.01)

def test_racetrack_size_and_closure():
    h = H.make_hold("BENKY", 41.511831, -88.709017, 90.0, "R", 1.0, 12000, "synth")
    poly = H.racetrack(h)
    assert poly[0] == pytest.approx(poly[-1])
    # width = 2 x turn radius = 5.08 NM, on the right of the inbound course (south for 090)
    lats = [p[0] for p in poly]
    assert (max(lats) - min(lats)) * 60 == pytest.approx(5.08, abs=0.1) and min(lats) < 41.511831 - 0.05

def test_stack_fifo_and_capacity():
    h = H.make_hold("X", 0, 0, 0, "R", 1.0, 12000, "synth")
    lv, bad = H.stack_levels([("A", 0, 10), ("B", 2, 15), ("C", 3, 20)], h)
    assert lv == {"A": 12000, "B": 13000, "C": 14000} and bad == []
    _, bad = H.stack_levels([("A", 0, 20), ("B", 2, 10)], h)
    assert bad and "B" in bad[0]
    _, bad = H.stack_levels([(c, i, 50) for i, c in enumerate("ABCDEF")], h)
    assert any("6 > 5 levels" in b for b in bad)

def test_holding_speeds_and_legs():
    assert [H.holding_ias_kt(a) for a in (0, 6000, 6001, 14000, 14001)] == [200, 200, 230, 230, 265]
    assert H.leg_min_for(14000) == 1.0 and H.leg_min_for(14001) == 1.5
    assert H.tas_kt(230, 12000) == pytest.approx(285.2)

def test_null_leg_defaults_to_aim():
    lo = H.make_hold("A", 0, 0, 0, "R", None, 12000, "cifp")
    hi = H.make_hold("A", 0, 0, 0, "R", None, 16000, "cifp")
    assert lo.leg_min == 1.0 and hi.leg_min == 1.5
    assert lo.levels_ft == [12000, 13000, 14000, 15000, 16000]
    assert lo.lap_min == pytest.approx(H.lap_min(12000))

def test_left_turn_mirrors_and_stack_step_down():
    r = H.racetrack(H.make_hold("B", 10, 10, 90.0, "R", 1.0, 12000, "synth"))
    l = H.racetrack(H.make_hold("B", 10, 10, 90.0, "L", 1.0, 12000, "synth"))
    assert max(p[0] for p in l) > 10 and min(p[0] for p in r) < 10
    h = H.make_hold("X", 0, 0, 0, "R", 1.0, 12000, "synth")
    lv, bad = H.stack_levels([("A", 0, 5), ("B", 1, 10), ("C", 6, 12)], h)
    assert lv["C"] == 13000 and bad == []   # B stepped down to bottom when A left
