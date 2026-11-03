import copy
from dataclasses import replace

import pytest

from backend.wave import rules
from backend.wave.model import Move, QueueArrival
from backend.wave.moves import _move, apply, fcfs_plan, legal_moves
from backend.wave.utility import evaluate
from tests.fixtures import make_wave


def test_fcfs_plan_shape():
    w = make_wave()
    p = fcfs_plan(w)
    assert sorted(a.callsign for a in p.assignments) == sorted(f.callsign for f in w.flights)
    for a in p.assignments:
        f = w.flight(a.callsign)
        assert a.action == "land" and a.slot_min >= f.eta_earliest_min
        assert a.gate == f.scheduled_gate and a.speed_reduction_min == 0 and a.ground_delay_min == 0
        assert isinstance(a.slot_min, float)
    assert all(c.passed for c in rules.check(w, p) if c.rule == "ATC-WAKE-SEP")


def test_pilot_cannot_move_other_flight():
    w = make_wave()
    p = fcfs_plan(w)
    ms = legal_moves(w, p, "PILOT:DL100")
    assert ms and all(m.callsigns == ["DL100"] and m.agent == "PILOT:DL100" for m in ms)
    assert {m.kind for m in ms} <= {"speed_reduction", "ground_delay", "request_divert", "accept"}
    assert any(m.kind == "accept" for m in ms)


def test_ground_delay_only_if_not_airborne():
    w = make_wave()
    p = fcfs_plan(w)
    air = legal_moves(w, p, "PILOT:DL100")      # airborne
    ground = legal_moves(w, p, "PILOT:UA403")   # not airborne
    assert not any(m.kind == "ground_delay" for m in air)
    assert any(m.kind == "speed_reduction" for m in air)
    assert any(m.kind == "ground_delay" for m in ground)
    # the engine also rejects a ground delay forced onto an airborne flight
    bad = p.with_assignment(replace(p.get("DL100"), ground_delay_min=5.0, slot_min=p.get("DL100").slot_min + 5))
    c = next(c for c in rules.check(w, bad) if c.rule == "PLT-EARLIEST-ETA" and c.callsign == "DL100")
    assert not c.passed and "airborne" in c.detail


def test_moves_within_authority_and_capped():
    w = make_wave()
    p = fcfs_plan(w)
    atc = legal_moves(w, p, "ATC")
    gnd = legal_moves(w, p, "GROUND")
    assert 0 < len(atc) <= 12 and 0 < len(gnd) <= 12
    assert {m.kind for m in atc} <= {"swap_order", "change_runway", "shift_slot", "speed_reduction", "ground_delay"}
    assert all(m.callsigns[0] != w.focus for m in atc if m.kind in ("speed_reduction", "ground_delay"))
    assert {m.kind for m in gnd} <= {"reassign_gate", "remote_stand", "swap_gates"}
    assert len({m.id for m in atc}) == len(atc)
    assert legal_moves(w, p, "ATC")[0].id == atc[0].id   # deterministic


def test_apply_is_pure():
    w = make_wave()
    p = fcfs_plan(w)
    evaluate(w, p)
    before = copy.deepcopy(p.to_dict())
    for ag in ("ATC", "GROUND", "PILOT:DL100", "PILOT:UA403"):
        for m in legal_moves(w, p, ag):
            p2 = apply(w, p, m)
            evaluate(w, p2)   # fills p2 metrics; must not leak into p
            assert p.to_dict() == before
            assert all(x is not y and x.metrics is not y.metrics
                       for x, y in zip(p.assignments, p2.assignments))


def test_apply_reslots_wake_legal():
    w = make_wave()
    p = fcfs_plan(w)
    for m in legal_moves(w, p, "ATC") + legal_moves(w, p, "PILOT:DL100") + legal_moves(w, p, "PILOT:UA403"):
        p2 = apply(w, p, m)
        wake = [c for c in rules.check(w, p2) if c.rule in ("ATC-WAKE-SEP", "PLT-EARLIEST-ETA")]
        assert all(c.passed for c in wake), (m.label, [c.detail for c in wake if not c.passed])


def test_swap_and_speed_reduction_effects():
    """Workbook wake times (Heavy->Large 5 nm = 129 s): FCFS DL100 T+5, DL201 T+7.2 (holds 1.2), AA302 T+8, UA403 T+12."""
    from backend.wave.moves import _move
    w = make_wave()
    p = fcfs_plan(w)
    sw = _move("ATC", "swap_order", ["DL201", "AA302"], {"runway": "26R"}, "swap")
    p2 = apply(w, p, sw)
    assert p2.get("AA302").slot_min < p2.get("DL201").slot_min
    assert evaluate(w, p) and round(p.get("DL201").metrics.hold_min, 1) == 1.2
    # DL201 absorbs 3 min en route: only DL201 moves (pilot authority), to its own earliest wake-legal slot
    sr = next(m for m in legal_moves(w, p, "PILOT:DL201") if m.kind == "speed_reduction" and m.params["minutes"] == 3)
    p5 = apply(w, p, sr)
    assert p5.get("DL201").slot_min >= 9.0 and p5.get("DL201").speed_reduction_min == 3
    assert all(p5.get(c).slot_min == p.get(c).slot_min for c in ("DL100", "AA302", "UA403"))
    assert all(c.passed for c in rules.check(w, p5) if c.rule == "ATC-WAKE-SEP")
    dv = next(m for m in legal_moves(w, p, "PILOT:AA302") if m.kind == "request_divert")
    p4 = apply(w, p, dv)
    assert p4.get("AA302").action == "divert" and evaluate(w, p4)["totals"]["diversions"] == 1


def test_pilot_move_does_not_move_others():
    w = make_wave()
    p = fcfs_plan(w)
    for cs in (f.callsign for f in w.flights):
        for m in legal_moves(w, p, f"PILOT:{cs}"):
            p2 = apply(w, p, m)
            changed = [a.callsign for a, b in zip(p.assignments, p2.assignments) if a.key() != b.key()]
            assert set(changed) <= {cs}, (m.id, changed)


def test_foreign_pilot_move_rejected():
    w = make_wave()
    p = fcfs_plan(w)
    with pytest.raises(ValueError, match="own flight"):
        apply(w, p, _move("PILOT:DL100", "speed_reduction", ["AA302"], {"minutes": 8.0}, "x"))
    with pytest.raises(ValueError, match="authority"):
        apply(w, p, _move("PILOT:DL100", "change_runway", ["DL100"], {"runway": "27L"}, "x"))
    with pytest.raises(ValueError, match="authority"):
        apply(w, p, _move("GROUND", "shift_slot", ["DL100"], {"minutes": 2.0}, "x"))
    with pytest.raises(ValueError, match="authority"):
        apply(w, p, _move("ATC", "reassign_gate", ["DL100"], {"gate": "B5"}, "x"))
    with pytest.raises(ValueError, match="unknown agent"):
        apply(w, p, Move("x", "TOWER", "accept", ["DL100"], {}, "x"))


def test_apply_validates_against_plan():
    w = make_wave()
    p = fcfs_plan(w)
    pd = apply(w, p, _move("PILOT:DL100", "request_divert", ["DL100"], {}, "x"))
    for m in (_move("ATC", "shift_slot", ["DL100"], {"minutes": 2.0}, "x"),
              _move("PILOT:DL100", "speed_reduction", ["DL100"], {"minutes": 8.0}, "x"),
              _move("GROUND", "remote_stand", ["DL100"], {"gate": "RS"}, "x")):
        with pytest.raises(ValueError, match="diverted"):
            apply(w, pd, m)
    stale = apply(w, p, _move("ATC", "change_runway", ["AA302"], {"runway": "27L"}, "x"))
    with pytest.raises(ValueError, match="same runway"):
        apply(w, stale, _move("ATC", "swap_order", ["DL201", "AA302"], {"runway": "26R"}, "x"))
    with pytest.raises(ValueError, match="not a flight"):
        apply(w, p, _move("ATC", "shift_slot", ["ZZ9"], {"minutes": 2.0}, "x"))


def test_unknown_agent_and_bad_gate_robust():
    w = make_wave()
    p = fcfs_plan(w)
    with pytest.raises(ValueError):
        legal_moves(w, p, "PILOT:ZZ9")
    with pytest.raises(ValueError):
        legal_moves(w, p, "TOWER")
    # 4c: a scheduled gate missing from the gate list must not crash Ground's move generation
    wb = make_wave(flights=[replace(w.flights[0], scheduled_gate="Z9")] + w.flights[1:])
    pb = fcfs_plan(wb)
    ms = legal_moves(wb, pb, "GROUND")
    assert ms and not any(m.kind == "swap_gates" and "DL100" in m.callsigns for m in ms)
    assert not rules.feasible(wb, pb)


def test_fcfs_follows_queue_direction_when_no_metar():
    w = make_wave(metar=None)    # queue lands 26R / 27L
    p = fcfs_plan(w)
    assert all(a.runway in ("26R", "27L") for a in p.assignments), p.describe()
    assert rules.feasible(w, p)
    calm = make_wave(metar=replace(make_wave().metar, wind_dir_deg=90, wind_kt=2))
    pc = fcfs_plan(calm)
    assert all(a.runway in ("26R", "27L") for a in pc.assignments), pc.describe()


def test_no_change_runway_against_flow():
    w = make_wave()
    p = fcfs_plan(w)
    for m in legal_moves(w, p, "ATC"):
        if m.kind == "change_runway":
            assert m.params["runway"] not in ("08L", "09R"), m.label


# ---------------------------------------------------------------- hold move and path-aware labels (spec §6.7)

def test_hold_move_offered_and_applied():
    from backend.wave import build, moves, trajectory
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    hm = [m for m in moves._candidates(w, p, "ATC") if m.kind == "hold"]   # legal_moves caps at MAX_MOVES=12 and may rank holds out
    assert hm and all(1 <= m.params["laps"] <= 4 for m in hm)
    m = hm[0]; q = apply(w, p, m)
    t = next(x for x in trajectory.build(w, q).air if x.callsign == m.callsigns[0])
    assert t.laps == m.params["laps"] and m.label.startswith(f"Hold {m.callsigns[0]} ")


def test_labels_within_120_chars():
    from backend.wave import build
    from backend.wave.utility import negotiators
    w = build.build_wave("F2015-75"); p = fcfs_plan(w)
    for ag in negotiators(w):
        assert all(len(m.label) <= 120 for m in legal_moves(w, p, ag))


def test_hold_label_states_fix_level_minutes_and_charged_fuel():
    import re
    from backend.wave import build, moves, trajectory
    from backend.wave.utility import hold_burn, metrics_map
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    hm = [m for m in moves._candidates(w, p, "ATC") if m.kind == "hold"]
    assert hm
    for m in hm:
        cs, laps = m.callsigns[0], m.params["laps"]
        rx = rf"Hold {cs} {laps} laps? at (\S+) \(base (FL\d{{3}}|\d{{1,2}},\d{{3}} ft), (\d+\.\d) min, ~(\d+) kg\)"
        g = re.fullmatch(rx, m.label)
        assert g, m.label
        q = apply(w, p, m)
        t = next(x for x in trajectory.build(w, q).air if x.callsign == cs)
        mm = metrics_map(w, q)[cs]
        assert g.group(1) == t.hold_fix
        assert mm.hold_laps == laps
        # minutes and fuel are those of the applied slot: laps x lap_min plus any wake-push vector,
        # at the holding burn metrics_map charges
        mins = t.laps * t.lap_min + t.vector_min
        assert g.group(3) == f"{mins:.1f}"
        assert g.group(4) == f"{mins * hold_burn(w.flight(cs)):.0f}"


def test_hold_only_with_a_hold_fix_and_validated():
    from backend.wave import moves
    w = make_wave(); p = fcfs_plan(w)   # fixture wave: no procedures, no-geometry tier
    assert not [m for m in moves._candidates(w, p, "ATC") if m.kind == "hold"]
    a = p.assignments[0]
    with pytest.raises(ValueError):
        apply(w, p, _move("ATC", "hold", [a.callsign], {"laps": 2}, "x"))


def test_hold_laps_out_of_range_rejected():
    from backend.wave import build
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    cs = p.assignments[0].callsign
    for bad in (0, 5, 1.5, "2", None):
        with pytest.raises(ValueError):
            apply(w, p, _move("ATC", "hold", [cs], {"laps": bad}, "x"))
    with pytest.raises(ValueError):
        apply(w, p, _move(f"PILOT:{cs}", "hold", [cs], {"laps": 1}, "x"))


def test_shift_and_change_runway_labels_mention_path():
    from backend.wave import build, moves
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    c = moves._candidates(w, p, "ATC")
    sh = [m for m in c if m.kind == "shift_slot"]
    assert sh and all(m.label in (f"Vector {m.callsigns[0]} +2 min (path stretch)",
                                  f"Vector {m.callsigns[0]} -2 min (path stretch)") for m in sh)
    cr = [m for m in c if m.kind == "change_runway"]
    assert cr and all(m.label.endswith(" min route)") for m in cr)


def test_hold_label_counts_wake_push_as_vector(monkeypatch):
    # a slot pushed past earliest + n laps by wake separation: the label states the applied minutes
    from backend.wave import build, moves, trajectory
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    real = moves._earliest_legal
    monkeypatch.setattr(moves, "_earliest_legal", lambda wv, o, wk, fl: real(wv, o, wk, fl + 1.3))
    m = next(m for m in moves._candidates(w, p, "ATC") if m.kind == "hold")
    t = next(x for x in trajectory.build(w, apply(w, p, m)).air if x.callsign == m.callsigns[0])
    assert t.vector_min > 1.0 and f", {t.laps * t.lap_min + t.vector_min:.1f} min," in m.label


def _own_violations(w, plan):
    return sorted((c.rule, c.callsign) for c in rules.check(w, plan)
                  if not c.passed and c.rule.startswith(("ATC-", "PLT-")))


def test_hold_offered_only_when_it_reduces_atc_or_pilot_violations():
    from backend.wave import build
    for wid in ("F2015-69315", "F2015-58926"):
        w = build.build_wave(wid); p = fcfs_plan(w)
        base = len(_own_violations(w, p))
        for m in (m for m in legal_moves(w, p, "ATC") if m.kind == "hold"):
            assert len(_own_violations(w, apply(w, p, m))) < base, m.label


@pytest.mark.parametrize("wid", ["F2015-69315", "F2015-58926"])
def test_formula_negotiation_does_not_hold_for_gate_rules(wid):
    # GND-GATE-FREE is Ground's to fix (gate moves); ATC must not buy it with airborne holding
    from backend.wave import build, graph
    from backend.wave.model import Assignment, JointPlan
    from backend.wave.utility import metrics_map
    w = build.build_wave(wid)
    r = graph.negotiate_wave(w, "formulas")
    held = [x["move"] for e in r["log"] if e["type"] == "AGENT_TURN"
            for x in e["body"]["moves"] if x["move"]["kind"] == "hold"]
    assert not held, [m["label"] for m in held]
    fp = JointPlan([Assignment(**{k: v for k, v in a.items() if k in Assignment.__dataclass_fields__})
                    for a in r["final_plan"]["assignments"]], "final")
    assert all(m.hold_laps == 0 for m in metrics_map(w, fp).values())
