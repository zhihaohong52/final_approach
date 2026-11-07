"""Ground-traffic memory housekeeping (perf/memory item 4) must not change a single result: releasing the build-only
caches and reservation records of the fixed traffic (`ground_traffic._release`), storing fixed track points as
`surface.PackedPoints`, private departure paths, the inline `schedule` point builder, the Rust list-cache clear
(`lists_cache_clear`) and the tree trim after each wave build (`surface.trim_trees`). Differential tests against the
behaviour before them (module flags off), bit for bit, native and ATM_NATIVE=0."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import pickle
import random
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.wave import build, ground_traffic as GT, surface, trajectory, utility, wavegeo
from backend.wave.moves import fcfs_plan

WAVES = ["ORD-2024-06-04-21", "ATL-2024-06-07-20", "DEN-2024-06-02-18", "PHX-2024-06-21-19", "F2015-152758"]
HASH_WAVES = ["ORD-2024-06-04-21", "ATL-2024-06-07-20"]
VOLATILE = ("ts", "elapsed_ms", "latency_ms", "run_id", "created_at", "generated_at", "started_at", "finished_at",
            "elapsed_s", "duration_s")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

native = pytest.mark.skipif(surface._NATIVE is None or not hasattr(surface._NATIVE, "lists_cache_clear"),
                            reason="native taxi extension without lists_cache_clear (scripts/build_native.sh)")


def _bits(x) -> bytes:
    return struct.pack(">d", x)


def _same_float(a, b, ctx):
    assert type(a) is type(b), f"{ctx}: {type(a)} vs {type(b)}"
    if type(a) is float:
        assert _bits(a) == _bits(b), f"{ctx}: {a!r} vs {b!r}"
    else:
        assert a == b, f"{ctx}: {a!r} vs {b!r}"


def _same_value(a, b, ctx):
    """Equal with the same types all the way down, floats bit for bit."""
    assert type(a) is type(b), f"{ctx}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, float):
        _same_float(a, b, ctx)
    elif isinstance(a, dict):
        assert list(a) == list(b), f"{ctx}: keys {list(a)} vs {list(b)}"
        for k in a:
            _same_value(a[k], b[k], f"{ctx}.{k}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), f"{ctx}: len {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            _same_value(x, y, f"{ctx}[{i}]")
    else:
        assert a == b, f"{ctx}: {a!r} vs {b!r}"


def _same_table(a: dict, b: dict, ctx):
    assert list(a) == list(b), f"{ctx}: keys differ"
    for k in a:
        _same_value(list(a[k]), list(b[k]), f"{ctx}[{k}]")


def _compute(w, release: bool, monkeypatch) -> GT.FixedTraffic:
    monkeypatch.setattr(GT, "_RELEASE", release)
    monkeypatch.setattr(GT, "_PACK_POINTS", release)
    try:
        return GT._compute(w)
    finally:
        monkeypatch.setattr(GT, "_RELEASE", True)
        monkeypatch.setattr(GT, "_PACK_POINTS", True)


def _fresh_plan_caches():
    """Drop the plan-level memos keyed by wave key, so a run reads the fixed traffic pinned now."""
    for d in (trajectory._SCHED, trajectory._CACHE, trajectory._PATHS, utility._MM):
        d.clear()
    from backend.wave import rules
    rules._CHECKS.clear()


def _pinned(w, ft):
    from backend.wave.route import wave_key
    wc = copy.copy(w)
    wc.__dict__[GT._PIN] = (wave_key(w), GT._GEN[0], ft)
    return wc


def _outputs(w) -> tuple[str, str]:
    """(ground_traffic_part JSON, fcfs evaluate JSON) of a wave, from fresh plan caches."""
    _fresh_plan_caches()
    gp = json.dumps(wavegeo.ground_traffic_part(w), sort_keys=True, default=str)
    ev = json.dumps(utility.evaluate(w, fcfs_plan(w)), sort_keys=True, default=str)
    return gp, ev


def _hashes(w) -> tuple[str, str]:
    return tuple(hashlib.sha256(x.encode()).hexdigest() for x in _outputs(w))


@pytest.fixture(scope="module")
def waves():
    return {wid: build.build_wave(wid) for wid in WAVES}


# 7.1 ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("wid", WAVES)
def test_release_is_identical(wid, waves, monkeypatch):
    w = waves[wid]
    ref = _compute(w, False, monkeypatch)
    new = _compute(w, True, monkeypatch)
    _same_table(ref.reservations.node_busy, new.reservations.node_busy, "node_busy")
    _same_table(ref.reservations.edge_dir, new.reservations.edge_dir, "edge_dir")
    _same_value(ref.reservations.runway_events, new.reservations.runway_events, "runway_events")
    _same_value(ref.occupancy, new.occupancy, "occupancy")
    _same_value(ref.stats, new.stats, "stats")
    _same_value(ref.failures, new.failures, "failures")
    assert ref.takeoffs_shifted == new.takeoffs_shifted and ref.mode == new.mode and ref.real == new.real
    assert list(ref.scheduled) == list(new.scheduled)
    packed = 0
    for lab, a in ref.scheduled.items():
        b = new.scheduled[lab]
        assert (a.ok, a.reason) == (b.ok, b.reason), lab
        _same_float(a.exit_clear_min, b.exit_clear_min, f"{lab}.exit_clear_min")
        _same_value(a.waits, b.waits, f"{lab}.waits")
        assert type(a.points) is list
        packed += isinstance(b.points, surface.PackedPoints)
        _same_value(a.points, list(b.points), f"{lab}.points")
        assert b.reserved == () and not b._reads
    assert packed == len(new.scheduled) > 0          # every fixed schedule's points fit (float times from pushback)
    assert len(ref.tracks) == len(new.tracks)
    for a, b in zip(ref.tracks, new.tracks):
        assert isinstance(b.points, surface.PackedPoints)
        _same_value(a.points, list(b.points), f"{a.callsign}.track")
        assert json.dumps(a.to_dict()) == json.dumps(b.to_dict())
    assert _outputs(_pinned(w, ref)) == _outputs(_pinned(w, new))


# 7.2 ------------------------------------------------------------------------------------------------------------------

def test_packed_points_semantics(waves, monkeypatch):
    w = waves["ORD-2024-06-04-21"]
    ref = _compute(w, False, monkeypatch)
    new = _compute(w, True, monkeypatch)
    lines = bays = 0
    for a, b in zip(ref.tracks, new.tracks):
        o, p = a.points, b.points
        n = len(o)
        assert len(p) == n and p == o and o == p and p == tuple(o) and p == surface.PackedPoints.of(list(o))
        assert p != o[:-1] and p != "x" and not (p == 3)
        assert p[0] == o[0] and p[-1] == o[-1] and all(p[-k] == o[-k] for k in range(1, n + 1))
        assert list(reversed(p)) == list(reversed(o)) and list(iter(p)) == o
        for i, j in ((0, n), (1, n - 1), (2, 2), (-3, None), (None, -2), (n + 5, None)):
            v = p[i:j]
            assert isinstance(v, surface.PackedPoints) and list(v) == o[i:j] and v == o[i:j] and len(v) == len(o[i:j])
            if len(v) > 1:
                assert v[1:] == o[i:j][1:] and v[-1] == o[i:j][-1]
        assert p[::2] == o[::2] and p[::-1] == o[::-1]
        assert p + [{"x": 1}] == o + [{"x": 1}] and [{"x": 1}] + p == [{"x": 1}] + o
        with pytest.raises(IndexError):
            p[n]
        with pytest.raises(IndexError):
            p[-n - 1]
        for x, y in zip(o, p):
            assert list(x) == list(y) and all(type(x[k]) is type(y[k]) for k in x)
            if y.get("line"):
                lines += 1
                assert y["line"] is True and isinstance(y["node"], int)
            elif y["node"] is None:
                assert "line" not in y
                bays += 1
        assert pickle.loads(pickle.dumps(p)) == p and pickle.loads(pickle.dumps(p[1:-1])) == o[1:-1]
        assert copy.deepcopy(p) is p and copy.copy(p) is p
        assert repr(p) == repr(o)
    assert lines and bays                            # stop-line points and holding-bay line-up points were seen
    t = new.tracks[0].points
    with pytest.raises(TypeError):
        hash(t)
    good = list(ref.tracks[0].points)
    for bad in ([{**good[0], "t_min": 3}] + good[1:],                       # int t_min stays a list
                [{**good[0], "x": 1}] + good[1:],                           # extra key
                [{k: v for k, v in good[0].items() if k != "node"}] + good[1:],
                [{**good[0], "line": False}] + good[1:],
                [dict(reversed(list(good[0].items())))] + good[1:]):        # key order
        assert surface.PackedPoints.of(bad) is bad
    assert surface.PackedPoints.of(t) is t
    e = surface.PackedPoints.of([])
    assert isinstance(e, surface.PackedPoints) and len(e) == 0 and list(e) == [] and e == []


# 7.3 ------------------------------------------------------------------------------------------------------------------

def test_inline_pt_matches_route_holds_pts(waves):
    seen = 0
    for wid in WAVES:
        w = waves[wid]
        g = surface.load_graph(w.airport.icao)
        if g is None:
            continue
        from backend.wave.route import wave_key
        paths = [m.path for m in GT._get(w).movers if m.path is not None]
        paths += [ti.path for ti in surface._TABLE.get(wave_key(w), {}).values() if ti.path is not None][:200]
        for path in paths:
            ref = surface.refine(g, path)
            old = [{"lat": g.nodes[x][0], "lon": g.nodes[x][1], "node": x, **({} if ref.real[i] else {"line": True})}
                   for i, x in enumerate(ref.nodes)]
            for i in range(len(ref.nodes)):
                t = 12.5 + i
                a = {"t_min": t, **old[i]}
                b = surface._point(g.nodes, ref.nodes, ref.real, t, i)
                assert list(a) == list(b) and all(a[k] is b[k] for k in a)
            seen += 1
    assert seen > 100


# 7.4 ------------------------------------------------------------------------------------------------------------------

def _record_loop(w):
    """One recorded loop input: a fixed mover scheduled against a plain copy of the fixed tables."""
    ft = GT._get(w)
    g = surface.load_graph(w.airport.icao)
    m = next(m for m in ft.movers if m.path is not None and m.kind == "departure")
    rec = []
    orig = surface._LOOP

    def wrap(li):
        rec.append(li)
        return orig(li)

    res = surface.Reservations({k: list(v) for k, v in ft.reservations.node_busy.items()},
                               {k: list(v) for k, v in ft.reservations.edge_dir.items()},
                               {s: [e for e in lst if e[2] != m.label] for s, lst in ft.reservations.runway_events.items()})
    surface._LOOP = wrap
    try:
        surface.schedule(g, m.path, m.pushback(), res, m.label, commit=False)
    finally:
        surface._LOOP = orig
    assert rec and rec[0].base is None
    return rec[0]


def _run(li, lists):
    return surface._NATIVE.run_loop(surface._native_route(li), lists, li.t0_min, li.hw, li.events, surface.GAP_MIN,
                                    li.busy_own, li.edir_own, surface._TOL, surface._EPS, surface._WIN,
                                    surface._WIN_MIN, surface._MAX_ITER)


def _same_out(a, b):
    assert [_bits(x) for x in a[0]] == [_bits(x) for x in b[0]]
    assert [_bits(x) for x in a[1]] == [_bits(x) for x in b[1]]
    assert _bits(a[2]) == _bits(b[2]) and a[3] == b[3] and a[4] == b[4]


@native
def test_lists_cache_clear(waves):
    N = surface._NATIVE
    N.lists_cache_clear()
    assert N.lists_cache_stats() == (0, 0)
    fresh = [[(1.0, 2.0, "A"), (3.0, 4.5, "B")], [(0.5, 9.0, "C")]]
    keep = N.Lists(fresh, [(), ()], [(), fresh[1]])
    st = N.lists_cache_stats()
    assert st[0] == 2 and st[1] == 3
    assert N.lists_cache_clear() == 2 and N.lists_cache_stats() == (0, 0)
    assert surface.lists_cache_clear() == 0
    del keep
    li = _record_loop(waves["ORD-2024-06-04-21"])
    old = N.Lists(li.nl, li.rl, li.sl)
    assert N.lists_cache_stats()[0] > 0
    before = _run(li, old)
    assert N.lists_cache_clear() > 0 and N.lists_cache_stats() == (0, 0)
    _same_out(before, _run(li, old))                                # a Lists built before the clear still works
    _same_out(before, _run(li, N.Lists(li.nl, li.rl, li.sl)))       # and a fresh conversion is the same
    # after a fixed-traffic build the cache is empty again
    w = waves["ORD-2024-06-04-21"]
    GT.annotate(dataclasses.replace(w, provenance=list(w.provenance)))
    assert N.lists_cache_stats() == (0, 0)


# 7.5 ------------------------------------------------------------------------------------------------------------------

_SUB = r"""
import hashlib, json, pickle, sys
sys.path.insert(0, sys.argv[1])
from backend.wave import build, surface, trajectory, utility, wavegeo, rules
from backend.wave.moves import fcfs_plan
assert surface._NATIVE is None
def fresh():
    for d in (trajectory._SCHED, trajectory._CACHE, trajectory._PATHS, utility._MM, rules._CHECKS):
        d.clear()
def h(w):
    fresh()
    gp = json.dumps(wavegeo.ground_traffic_part(w), sort_keys=True, default=str)
    ev = json.dumps(utility.evaluate(w, fcfs_plan(w)), sort_keys=True, default=str)
    return [hashlib.sha256(x.encode()).hexdigest() for x in (gp, ev)]
out = {}
for wid in sys.argv[2:]:
    w = build.build_wave(wid)
    out[wid] = {"built": h(w), "pickled": h(pickle.loads(pickle.dumps(w)))}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def python_hashes():
    env = {**os.environ, "ATM_NATIVE": "0", "ATM_STORE": "files"}
    r = subprocess.run([sys.executable, "-c", _SUB, ROOT, *HASH_WAVES], env=env, cwd=ROOT, capture_output=True,
                       text=True, timeout=1800)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_old_so_and_python_fallback(monkeypatch, waves, python_hashes):
    monkeypatch.setattr(surface, "_NATIVE", SimpleNamespace())
    assert surface.lists_cache_clear() == 0
    monkeypatch.setattr(surface, "_NATIVE", None)
    assert surface.lists_cache_clear() == 0
    monkeypatch.undo()
    for wid in HASH_WAVES:
        assert list(_hashes(waves[wid])) == python_hashes[wid]["built"], wid


# 7.6 ------------------------------------------------------------------------------------------------------------------

def _paths(ft):
    return [m.path for m in ft.movers if m.path is not None]


@pytest.mark.parametrize("wid", ["ORD-2024-06-04-21", "F2015-152758"])
def test_paths_private(wid, waves):
    w = waves[wid]
    g = surface.load_graph(w.airport.icao)
    a, b = GT._compute(w), GT._compute(w)
    for p in _paths(a):
        assert p._ref is None and p._holds is None and p._keys is None and p._lists is None
    cores = list(g._trees.get("dep_paths", {}).values())
    assert cores
    core_ids = {id(c) for c in cores}
    assert not any(id(p) in core_ids for p in _paths(a) + _paths(b))
    for c in cores:
        assert c._ref is None and c._holds is None and c._keys is None and c._lists is None
    assert not {id(p) for p in _paths(a)} & {id(p) for p in _paths(b)}
    info = {id(ti.path) for tab in surface._TABLE.values() for ti in tab.values() if ti.path is not None}
    assert info and not any(id(p) in info for p in _paths(a))
    for s in a.scheduled.values():
        assert s.reserved == ()


# 7.7 ------------------------------------------------------------------------------------------------------------------

@native
def test_native_route_none_holds(waves):
    li0 = _record_loop(waves["ATL-2024-06-07-20"])
    kw = {k: getattr(li0, k) for k in surface._LoopIn.__slots__ if hasattr(li0, k)}
    kw.update(path=SimpleNamespace(_holds=None), base=None)
    li = surface._LoopIn(**kw)
    py = surface._run_loop_py(li)
    nat = surface._run_loop_native(li)
    assert [_bits(x) for x in py.arr] == [_bits(x) for x in nat.arr]
    assert [_bits(x) for x in py.dep] == [_bits(x) for x in nat.dep]
    assert py.why == nat.why and py.converged == nat.converged
    h = li.path._holds
    assert isinstance(h, tuple) and len(h) == 3 and h[0] is li.g and h[1] is li.rh
    assert isinstance(h[2], surface._NativeSlot) and h[2].obj is not None


# 7.8 ------------------------------------------------------------------------------------------------------------------

def _negotiate(w):
    from backend.wave.graph import negotiate_wave
    _fresh_plan_caches()
    r = negotiate_wave(w, "formulas", fairness="firm")

    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in VOLATILE}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return json.dumps(strip(r), sort_keys=True, default=str)


@pytest.mark.skipif(not surface._TREE_NATIVE, reason="native tree builder not loaded")
def test_trim_trees_identical(waves):
    w = build.build_wave("F2015-152758")
    assert len(surface._TREE_ORDER) <= surface.TREES_KEEP or surface.TREES_KEEP >= surface.MAX_TREES
    before = _negotiate(w)
    g = surface.load_graph(w.airport.icao)
    root = next(m.path.nodes[0] for m in GT._get(w).movers if m.path is not None and m.kind == "arrival")
    tr = surface._tree(g, root)
    stands = sorted(s["node"] for s in g.stands.values())
    sample = random.Random(5).sample(stands, min(50, len(stands)))
    want = [surface.tree_path(tr, s) for s in sample]
    assert surface.trim_trees(0) >= 1 and not surface._TREE_ORDER
    tr2 = surface._tree(g, root)
    assert tr2 is not tr
    assert [surface.tree_path(tr2, s) for s in sample] == want
    surface.trim_trees(0)
    assert _negotiate(w) == before


# 7.9 ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("wid", HASH_WAVES)
def test_wave_pickle_roundtrip(wid, waves, python_hashes):
    w = waves[wid]
    want = _outputs(w)
    w2 = pickle.loads(pickle.dumps(w))
    ft2 = GT._get(w2)
    assert ft2 is not GT._get(w) and all(isinstance(t.points, surface.PackedPoints) for t in ft2.tracks)
    assert _outputs(w2) == want
    assert python_hashes[wid]["pickled"] == python_hashes[wid]["built"]
