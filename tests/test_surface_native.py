"""Differential tests: the Rust wait-search loop (`surface._run_loop_native`) must equal `surface._run_loop_py`
bit for bit (arr / dep floats, `why` reasons, converged flag) on recorded real inputs, on mutated inputs built to
force ties and waits at every check, and end to end through `negotiate_wave`."""
import copy
import random
import struct
from types import SimpleNamespace

import pytest

from backend.wave import build, graph, surface

if surface._NATIVE is None:
    pytest.skip("native taxi extension not built (scripts/build_native.sh) or ATM_NATIVE=0", allow_module_level=True)

WAVES = ["ORD-2024-06-04-21", "DFW-2024-06-01-15", "ATL-2024-07-05-21", "F2015-152758", "F2015-208678"]
E2E_WAVES = ["ORD-2024-06-04-21", "ATL-2024-07-05-21", "F2015-152758"]
VOLATILE = ("ts", "elapsed_ms")
TOL = surface._TOL


def _bits(xs):
    return [struct.pack(">d", x) for x in xs]


def assert_same(a, b, ctx=""):
    assert a.converged == b.converged, f"{ctx}: converged {a.converged} vs {b.converged}"
    assert len(a.arr) == len(b.arr) and len(a.dep) == len(b.dep), ctx
    for name in ("arr", "dep"):
        x, y = getattr(a, name), getattr(b, name)
        assert all(type(v) is float for v in y), f"{ctx}: native {name} has non-float"
        for k, (p, q) in enumerate(zip(_bits(x), _bits(y))):
            assert p == q, f"{ctx}: {name}[{k}] py={x[k]!r} ({x[k].hex()}) native={y[k]!r} ({y[k].hex()})"
    assert struct.pack(">d", a.arr0_nb) == struct.pack(">d", b.arr0_nb), f"{ctx}: arr0_nb {a.arr0_nb!r} vs {b.arr0_nb!r}"
    assert a.why == b.why, f"{ctx}: why\n py={a.why}\n native={b.why}"


def _clone(li, **over):
    """A copy of a recorded input that shares no cache with the original (fresh path / base slot), so mutated
    constants never meet a Rust object cached for the unmutated route."""
    kw = {k: getattr(li, k) for k in surface._LoopIn.__slots__ if hasattr(li, k)}
    kw.update(path=SimpleNamespace(_holds=(None, None)), base=None)
    kw.update(over)
    return surface._LoopIn(**kw)


def _both(li, ctx=""):
    py = surface._run_loop_py(li)
    nat = surface._run_loop_native(li)
    assert_same(py, nat, ctx)
    return py


@pytest.fixture(scope="module")
def recorded():
    """Every loop input of a negotiation of several real waves (formulas mode)."""
    rec = []
    orig = surface._LOOP

    def wrap(li):
        rec.append(li)
        return orig(li)

    surface._LOOP = wrap
    try:
        for wid in WAVES:
            build.build_wave.cache_clear()
            graph.negotiate_wave(build.build_wave(wid), "formulas", fairness="firm")
    finally:
        surface._LOOP = orig
        build.build_wave.cache_clear()
    assert len(rec) > 1000
    return rec


# (1) recorded real inputs ---------------------------------------------------------------------------------------------

def test_recorded_inputs_identical(recorded):
    waited = holds = 0
    for k, li in enumerate(recorded):
        py = surface._run_loop_py(li)
        assert_same(py, surface._run_loop_native(li), f"recorded #{k}")
        waited += bool(py.why)
        holds += any(v[1] is not None for v in py.why.values())
    assert waited > 0, "no recorded input had a wait: the test would prove little"
    print(f"\nrecorded inputs: {len(recorded)}, with a wait: {waited}, with a hold short: {holds}")


# (2) synthetic adversarial cases --------------------------------------------------------------------------------------

def _ties(rng, x):
    return x + rng.choice((0.0, 0.0, TOL, -TOL, 2 * TOL, -2 * TOL, 0.5 * TOL, 1e-6, -1e-6, 0.05, -0.05, 0.4))


def _mutate(li, ref, rng, spans):
    """Own-list intervals and extra base intervals placed on / around the times of the unmutated schedule, so the
    tolerance edges of every check are exercised; `spans` also passes precomputed spans and a base slot."""
    n, hw = li.n, li.hw
    arr, dep, nodes, revs, steps = ref.arr, ref.dep, li.nodes, li.revs, li.steps
    busy_own, edir_own = {}, {}
    nl, rl, sl = ([list(x) for x in col] for col in (li.nl, li.rl, li.sl))
    events = {k: list(v) for k, v in li.events.items()}

    def iv(centre, width):
        s = _ties(rng, centre - rng.choice((0.0, width)))
        e = _ties(rng, s + rng.choice((0.0, width, 2 * width, 0.3)))
        return (min(s, e), max(s, e), rng.choice("ABCabc") + rng.choice(("1", "2", "")))

    for _ in range(rng.randint(1, 6)):
        i = rng.randrange(n)
        t = rng.choice((arr[i], dep[i]))
        c = rng.random()
        if c < 0.3:
            busy_own.setdefault(nodes[i], []).append(iv(t - rng.choice((0.0, hw)), hw))
        elif c < 0.45:
            nl[i].append(iv(t - rng.choice((0.0, hw)), hw))
        elif c < 0.6 and revs[i] is not None:
            edir_own.setdefault(revs[i], []).append(iv(t, 0.1))
        elif c < 0.7 and revs[i] is not None:
            rl[i].append(iv(t, 0.1))
        elif c < 0.85 and steps[i] is not None:
            (edir_own.setdefault(steps[i], []) if rng.random() < 0.5 else sl[i]).append(iv(t, 0.2))
        else:
            for strip in events:
                events[strip].append((_ties(rng, t - rng.choice((0.0, 0.5, 1.0, 1.5, -0.25))),
                                      rng.choice(("landing", "takeoff", "other")), rng.choice(("E1", "E2", "E3"))))
    if rng.random() < 0.4:                                   # long lists: exercise the windowed bisect (> _WIN_MIN)
        for lists, key in ((nl, 0), (rl, 1), (sl, 2)):
            i = rng.randrange(n)
            base = rng.choice((arr[i], dep[i]))
            lists[i] += [iv(base + rng.uniform(-3, 3), rng.choice((0.01, 0.2, 1.0))) for _ in range(surface._WIN_MIN + 3)]
    for lists in (nl, rl, sl):
        for lst in lists:
            lst.sort()
    for d in (busy_own, edir_own):
        for lst in d.values():
            lst.sort()
    for v in events.values():
        v.sort()
    over = dict(nl=nl, rl=rl, sl=sl, events=events, busy_own=busy_own, edir_own=edir_own,
                nsp=None, rsp=None, ssp=None)
    if spans:
        sp = [[max((x[1] - x[0] for x in lst), default=0.0) if len(lst) > surface._WIN_MIN else 0.0 for lst in col]
              for col in (nl, rl, sl)]
        over.update(nsp=sp[0], rsp=sp[1], ssp=sp[2], base=surface._NativeSlot())
    return _clone(li, **over)


def test_mutated_inputs_identical(recorded):
    rng = random.Random(20240604)
    cands = [li for li in recorded if li.n >= 4 and li.zones_at]
    cands += rng.sample([li for li in recorded if li.n >= 4], 300)
    rng.shuffle(cands)
    waited = holds = 0
    for k, li in enumerate(cands[:900]):
        ref = surface._run_loop_py(li)
        mut = _mutate(li, ref, rng, spans=bool(k & 1))
        py = _both(mut, f"mutated #{k}")
        waited += bool(py.why)
        holds += any(v[1] is not None for v in py.why.values())
        # the same mutant again: a Rust object cached in the base slot must give the same answer
        if mut.base is not None:
            assert_same(py, surface._run_loop_native(mut), f"mutated #{k} (cached)")
    assert waited > 100 and holds > 5, (waited, holds)
    print(f"\nmutated inputs: 900, with a wait: {waited}, with a hold short: {holds}")


@pytest.mark.parametrize("check", ["a", "b", "c", "d"])
def test_each_check_forces_a_wait(recorded, check):
    """A hand-placed conflict per check type on a real route, with exact-tie variants around its tolerance edge."""
    hits = 0
    for li in [x for x in recorded if x.n >= 6 and x.zones_at][:400]:
        ref = surface._run_loop_py(li)
        for i in range(1, li.n - 1):
            if check == "c" and not li.zones_at.get(i):
                continue
            if check == "b" and not (li.real[i] and li.revs[i] is not None):
                continue
            if check == "d" and li.steps[i] is None:
                continue
            for delta in (0.0, TOL, -TOL, 2 * TOL, -2 * TOL, 1e-3):
                over = dict(nsp=None, rsp=None, ssp=None)
                a, b = ref.dep[i], ref.arr[i + 1]
                if check == "a":
                    over["busy_own"] = {li.nodes[i]: [(ref.arr[i] - 1.0, ref.arr[i] - li.hw + delta, "ZZ"),
                                                      (ref.arr[i] - 1.0, ref.arr[i] - li.hw + delta, "AA")]}
                elif check == "b":
                    over["edir_own"] = {li.revs[i]: [(a - 0.1, a + delta, "RV")]}
                elif check == "d":
                    over["edir_own"] = {li.steps[i]: [(a + delta + 2 * TOL, b - delta - 2 * TOL, "ST"),
                                                      (a - 0.1, b + 0.1 + delta, "ST2")]}
                else:
                    ev = {strip: sorted(li.events[strip] + [(ref.arr[i] + delta - 1.0, "landing", "LC"),
                                                            (ref.arr[i] + delta - 1.0, "takeoff", "LB")])
                          for strip in li.events}
                    over["events"] = ev
                li2 = _clone(li, **over)
                py = _both(li2, f"check {check} node {i} delta {delta}")
                hits += py.why != ref.why
                if hits >= 40:
                    return
    assert hits > 0, f"check ({check}) never changed a schedule"


def test_empty_lists_and_single_node(recorded):
    li = next(x for x in recorded if x.n >= 3)
    empty = _clone(li, nl=[()] * li.n, rl=[()] * li.n, sl=[()] * li.n, events={k: [] for k in li.events},
                   busy_own={}, edir_own={}, nsp=None, rsp=None, ssp=None)
    _both(empty, "empty lists")
    for lone in (x for x in recorded if x.n == 1):
        _both(_clone(lone), "recorded single node")
        break
    one = _clone(li, n=1, seg=[], real=[True], hold=[0], nxt_real=[0], prev_real=[0], zones_at={},
                 nodes=li.nodes[:1], steps=li.steps[:1], revs=li.revs[:1],
                 nl=[[(li.t0_min - 1.0, li.t0_min + 5.0, "BLK")]], rl=[()], sl=[()], busy_own={}, edir_own={},
                 nsp=None, rsp=None, ssp=None)
    py = _both(one, "single node, blocked")
    assert py.arr[0] > li.t0_min
    _both(_clone(one, nl=[()]), "single node, free")


def test_max_iter_guard(recorded, monkeypatch):
    li = next(x for x in recorded if x.n >= 8)
    for limit in (1, 2, 3, 7, 25):
        monkeypatch.setattr(surface, "_MAX_ITER", limit)       # read at call time, passed to Rust per call
        py = _both(_clone(li), f"_MAX_ITER={limit}")
        assert py.converged is False
    # a conflict-rich input trips it mid-search with a partly built schedule
    rng = random.Random(7)
    tripped = 0
    monkeypatch.setattr(surface, "_MAX_ITER", 40)
    for x in [y for y in recorded if y.n >= 8][:60]:
        mut = _mutate(x, surface._run_loop_py(x), rng, spans=False)
        tripped += not _both(mut, "_MAX_ITER=40 mutated").converged
    assert tripped > 0


# (3) end to end -------------------------------------------------------------------------------------------------------

def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if k not in VOLATILE}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


@pytest.mark.parametrize("wid", E2E_WAVES)
def test_negotiate_wave_equal(wid, monkeypatch):
    out = {}
    for name, fn in (("py", surface._run_loop_py), ("native", surface._run_loop_native)):
        monkeypatch.setattr(surface, "_LOOP", fn)
        build.build_wave.cache_clear()
        out[name] = _strip(graph.negotiate_wave(build.build_wave(wid), "formulas", run_id="r", fairness="firm"))
    assert out["py"] == out["native"]
