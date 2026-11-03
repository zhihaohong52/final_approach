"""Spec §12 end-to-end: the 50 benchmark waves build and negotiate (formulas) with path invariants, and the
recorded replay waves negotiate in under 2 s."""
import json
import math
import time
from pathlib import Path

import pytest

from backend.wave import build, graph, trajectory
from backend.wave.edit import plan_from_dicts
from backend.wave.moves import fcfs_plan

ROOT = Path(__file__).resolve().parent.parent
WAVES = json.loads((ROOT / "eval/benchmark.json").read_text())["wave_ids"]          # the 50 benchmark waves
REPLAYS = sorted(p.stem for p in (ROOT / "data/replay").glob("*.json"))
RADAR_EPS_NM = 1e-6   # the 150 NM clip point is interpolated; allow float noise on the boundary


def _nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def _invariants(w, plan):
    slots = {a.callsign: a.slot_min for a in plan.assignments if a.action == "land"}
    for t in trajectory.build(w, plan).air:
        assert all(b["t_min"] >= a["t_min"] for a, b in zip(t.points, t.points[1:])), t.callsign
        assert isinstance(t.laps, int) and t.laps >= 0
        assert all(q["src"] in ("cifp", "synth") for q in t.points)
        assert all(_nm(q["lat"], q["lon"], w.airport.lat, w.airport.lon) <= 150 + RADAR_EPS_NM
                   for q in t.points), t.callsign
        if t.wave and t.callsign in slots and t.points:     # the no-geometry tier draws no points
            assert t.points[-1]["t_min"] == pytest.approx(slots[t.callsign], abs=1e-6)


def test_wave_lists():
    assert len(WAVES) == 50 and REPLAYS


@pytest.mark.parametrize("wid", WAVES)
def test_benchmark_wave_builds_and_negotiates(wid):
    w = build.build_wave(wid)
    _invariants(w, fcfs_plan(w))
    r = graph.negotiate_wave(w, "formulas")
    if r["outcome"] == "agreed":
        assert r["evaluation"]["feasible"]                      # no failed hard rule in an agreed plan
    _invariants(w, plan_from_dicts(r["final_plan"]["assignments"], w))


@pytest.mark.parametrize("wid", REPLAYS)
def test_replay_wave_negotiates_under_2s(wid):
    w = build.build_wave(wid)
    t0 = time.perf_counter()
    graph.negotiate_wave(w, "formulas")
    assert time.perf_counter() - t0 < 2.0
