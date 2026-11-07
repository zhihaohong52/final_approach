"""Differential test: the Rust Dijkstra tree (surface._tree_native) against the Python one (surface._tree_py).

Skips only when the build has no native tree builder (surface._TREE_NATIVE false: no extension, an old one, or
ATM_NATIVE=0); any error from the native tree fails. The airports in
NAMED always run; the other surface documents run a small deterministic sample, or all with ATM_TREE_SWEEP=1.
"""
from __future__ import annotations

import os
import pickle
import random
import subprocess
import sys

import pytest

from backend.geo import store
from backend.wave import surface

NAMED = ["KORD", "KATL", "KDEN", "KDFW", "KLAX", "KJFK", "KSFO", "KSEA", "KMCO", "KPHX", "KCLT", "KLGA"]
SWEEP = os.environ.get("ATM_TREE_SWEEP") == "1"
ROOT_CAP = 10_000 if SWEEP else 40       # exits / stands sampled per graph (exits: every one for the named airports)
PATH_SAMPLE = 25                         # non-stand nodes whose path is compared, per root


def _can_run() -> bool:
    return (hasattr(surface, "_tree_py") and hasattr(surface, "_tree_native")
            and bool(getattr(surface, "_TREE_NATIVE", False)))


@pytest.fixture(scope="module", autouse=True)
def _need_native():
    if not _can_run():
        pytest.skip("surface has no native Dijkstra tree (_tree_py/_tree_native) in this build")


def _all_icaos() -> list[str]:
    d = os.path.join(os.path.dirname(store.__file__), "..", "..", "data", "ref", "surface")
    d = os.path.normpath(d)
    return sorted(f[:-8] for f in os.listdir(d) if f.endswith(".json.gz"))


def _airports() -> list[str]:
    allc = _all_icaos()
    named = [a for a in NAMED if a in allc]
    rest = [a for a in allc if a not in named]
    if not SWEEP:
        rng = random.Random(7)
        rest = sorted(rng.sample(rest, min(6, len(rest))))
    return named + rest


_GRAPHS: dict = {}


def _graph(icao):
    if icao not in _GRAPHS:
        _GRAPHS[icao] = surface.load_graph(icao)
    return _GRAPHS[icao]


@pytest.fixture(scope="module")
def graphs():
    return _graph


def _roots(g, seed):
    rng = random.Random(seed)
    exits = sorted({e["node"] for lst in g.exits.values() for e in lst})
    if len(exits) > ROOT_CAP and icao_not_named(g):
        exits = sorted(rng.sample(exits, ROOT_CAP))
    stands = sorted({s["node"] for s in g.stands.values()})
    if len(stands) > ROOT_CAP:
        stands = sorted(rng.sample(stands, ROOT_CAP))
    rand = [rng.randrange(len(g.nodes)) for _ in range(5)]
    return list(dict.fromkeys(exits + stands + rand))


def icao_not_named(g):
    return g.icao not in NAMED


def _hex_path(p):
    if p is None:
        return None
    return [(n, float(d).hex(), a, b) for n, d, a, b in p]


def _compare_tree(g, root, rng):
    tp, tn = surface._tree_py(g, root), surface._tree_native(g, root)
    n = len(g.nodes)
    for v in range(n):
        a, b = surface.tree_best(tp, v), surface.tree_best(tn, v)
        assert (a is None) == (b is None), (g.icao, root, v)
        if a is not None:
            assert a.hex() == b.hex(), (g.icao, root, v, a, b)
    reach = [v for v in range(n) if surface.tree_best(tp, v) is not None]
    stand_nodes = [s["node"] for s in g.stands.values()]
    others = rng.sample(range(n), min(PATH_SAMPLE, n))
    for v in dict.fromkeys(stand_nodes + others):
        a, b = surface.tree_path(tp, v), surface.tree_path(tn, v)
        assert _hex_path(a) == _hex_path(b), (g.icao, root, v)
        if a is not None:
            assert a[0] == (root, 0.0, None, None) and a[-1][0] == v
    # a few unreachable-or-not nodes beyond the sample: the root itself
    assert surface.tree_best(tn, root) == 0.0 and surface.tree_path(tn, root) == [(root, 0.0, None, None)]
    return len(reach)


def _norm(d):
    """Every best / parent / via / dist entry, floats as hex (bit identity)."""
    return ({k: (v[0].hex(), v[1]) for k, v in d["best"].items()}, dict(d["parent"]), dict(d["via"]),
            {k: v.hex() for k, v in d["dist"].items()})


@pytest.mark.parametrize("icao", NAMED)
def test_whole_tree_equal(icao):
    """The whole native tree (as_dict) equals the Python one, for a few exits, a random node and the last node."""
    g = _graph(icao)
    if g is None:
        pytest.skip(f"no surface graph for {icao}")
    rng = random.Random("w" + icao)
    exits = sorted({e["node"] for lst in g.exits.values() for e in lst})
    roots = dict.fromkeys(exits[:2] + [rng.randrange(len(g.nodes)), len(g.nodes) - 1])
    for root in roots:
        tn = surface._tree_native(g, root)
        assert not isinstance(tn, dict)
        assert _norm(tn.as_dict()) == _norm(surface._tree_py(g, root)), (icao, root)


@pytest.mark.parametrize("icao", _airports())
def test_tree_matches_python(icao):
    g = _graph(icao)
    if g is None:
        pytest.skip(f"no surface graph for {icao}")
    rng = random.Random(icao)
    for root in _roots(g, icao):
        _compare_tree(g, root, rng)


def _turn_triples(g):
    """Every (p, u, v) Dijkstra can ask the turn of: p -> u and u -> v non-runway edges, u -> v not a taxilane."""
    into: dict[int, set[int]] = {}
    for p, es in g.adj.items():
        for e in es:
            if e[2] != "runway":
                into.setdefault(e[0], set()).add(p)
    for u in sorted(into):
        vs = sorted({e[0] for e in g.adj.get(u, ()) if e[2] not in ("runway", "taxilane")})
        for p in sorted(into[u]):
            for v in vs:
                yield p, u, v


def _xplane_graph():
    path = os.environ.get("ATM_KIAH_XPLANE")
    if not path or not os.path.exists(path):
        return None
    import json
    try:
        return surface._from_doc("KIAH", json.load(open(path)))
    except Exception:
        return None


@pytest.mark.parametrize("icao", _airports() + ["KIAH.xplane"])
def test_heading_turn_bit_identical(icao):
    """The Rust turn (TreeGraph.turn_deg) equals _heading_turn bit for bit on every triple Dijkstra can visit."""
    g = _xplane_graph() if icao == "KIAH.xplane" else _graph(icao)
    if g is None:
        pytest.skip(f"no graph for {icao}")
    ng = surface._native_graph(g)
    assert ng is not None
    n = 0
    for p, u, v in _turn_triples(g):
        want = surface._heading_turn(g, p, u, v)
        got = ng.turn_deg(p, u, v)
        assert got.hex() == want.hex(), (icao, p, u, v, got, want)
        n += 1
    if n == 0:
        pytest.skip(f"{icao}: no turn Dijkstra can ask (taxilanes only)")


def test_hypot_compat_bit_identical():
    import math
    rng = random.Random(3)
    vals = [0.0, -0.0, 5e-324, 1e-310, 2.2250738585072014e-308, 1e-300, 1.0, 3.0, 4.0, 1e300, 1e308, 1.7976931348623157e308,
            math.inf, -math.inf]
    pairs = [(a, b) for a in vals for b in vals]
    for _ in range(50_000):
        pairs.append((rng.uniform(-1, 1) * 10 ** rng.uniform(-8, 6), rng.uniform(-1, 1) * 10 ** rng.uniform(-8, 6)))
    for x, y in pairs:
        assert surface._NATIVE.hypot_compat(x, y).hex() == math.hypot(x, y).hex(), (x, y)
    assert math.isnan(surface._NATIVE.hypot_compat(math.nan, 1.0))


def _clear_trees(g):
    with surface._TREE_LOCK:
        for k in [k for k in g._trees if isinstance(k, tuple)]:
            g._trees.pop(k, None)
        for k in [k for k in surface._TREE_ORDER if k[0] == id(g)]:
            surface._TREE_ORDER.pop(k, None)


def _taxi_fields(p):
    return (p.nodes, [x.hex() for x in p.seg_s], p.names, p.crossings, p.unimpeded_min.hex(), p.src)


@pytest.mark.parametrize("icao", ["KORD", "KATL", "KDEN", "KLGA", "KSFO", "KJFK"])
def test_unimpeded_equal(icao, monkeypatch):
    g = _graph(icao)
    rng = random.Random("u" + icao)
    exits = sorted({e["node"] for lst in g.exits.values() for e in lst})
    stands = sorted({s["node"] for s in g.stands.values()})
    pairs = [(rng.choice(exits), rng.choice(stands)) for _ in range(60)]
    # plus a few unreachable / invalid ones
    pairs += [(rng.choice(exits), rng.randrange(len(g.nodes))) for _ in range(15)] + [(-1, stands[0]), (None, stands[0])]

    def run():
        _clear_trees(g)
        out = []
        for e, s in pairs:
            try:
                out.append(_taxi_fields(surface.unimpeded(g, e, s)))
            except surface.NoPath as ex:
                out.append(("NoPath", str(ex)))
        return out

    native = run()
    assert any(r[0] != "NoPath" for r in native)
    assert not any(isinstance(t, dict) for k, t in g._trees.items() if isinstance(k, tuple) and k[0] == g.icao)
    monkeypatch.setattr(surface, "_tree_native", surface._tree_py)
    py = run()
    assert isinstance(next(t for k, t in g._trees.items() if isinstance(k, tuple) and k[0] == g.icao), dict)
    monkeypatch.undo()
    _clear_trees(g)
    assert native == py


def test_pickle_roundtrip_with_native_tree():
    g = _graph("KLGA")
    root = sorted({e["node"] for lst in g.exits.values() for e in lst})[0]
    tr = surface._tree(g, root)
    assert not isinstance(tr, dict)
    g2 = pickle.loads(pickle.dumps(g))
    assert g2._trees == {}
    tr2 = surface._tree(g2, root)
    for v in range(0, len(g.nodes), 7):
        a, b = surface.tree_best(tr, v), surface.tree_best(tr2, v)
        assert (a is None) == (b is None) and (a is None or a.hex() == b.hex())
    _clear_trees(g2)


def test_native_cached_per_root():
    g = _graph("KLGA")
    root = sorted({e["node"] for lst in g.exits.values() for e in lst})[0]
    _clear_trees(g)
    assert surface._tree(g, root) is surface._tree(g, root)


def test_atm_native_zero_gives_dict():
    code = (
        "import os; os.environ['ATM_STORE']='files'\n"
        "from backend.wave import surface as s\n"
        "assert not s._NATIVE\n"
        "g = s.load_graph('KLGA'); r = sorted({e['node'] for l in g.exits.values() for e in l})[0]\n"
        "t = s._tree(g, r)\n"
        "assert isinstance(t, dict) and set(t) >= {'best','parent','via','dist'}, type(t)\n"
        "assert s.tree_best(t, r) == 0.0\n"
    )
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
    env = dict(os.environ, ATM_NATIVE="0", PYTHONPATH=root)
    r = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]


def _fresh_copy(icao):
    import copy
    g = copy.deepcopy(_graph(icao))
    g._trees = {}
    return g


def test_unconvertible_graph_falls_back_to_python():
    """A graph the Rust side cannot take (a coordinate that is not a number) gets the Python tree, and the failure
    is remembered (no rebuild attempt per call)."""
    g = _fresh_copy("KLGA")
    root = sorted({e["node"] for lst in g.exits.values() for e in lst})[0]
    g.nodes.append((None, None))         # no edge reaches it: the Python tree does not care
    tr = surface._tree(g, root)
    assert isinstance(tr, dict)
    assert g._trees["native_graph"] is False
    assert surface._native_graph(g) is None
    assert surface.tree_best(tr, root) == 0.0
    _clear_trees(g)


def test_non_finite_length_falls_back_to_python():
    import math
    g = _fresh_copy("KLGA")
    u = next(u for u, es in g.adj.items() if any(e[2] != "runway" for e in es))
    i = next(i for i, e in enumerate(g.adj[u]) if e[2] != "runway")
    e = list(g.adj[u][i]); e[1] = math.nan; g.adj[u][i] = tuple(e)
    assert surface._native_graph(g) is None
    assert isinstance(surface._tree(g, u), dict)
    _clear_trees(g)


def test_integral_float_node_like_dict():
    g = _graph("KLGA")
    root = sorted({e["node"] for lst in g.exits.values() for e in lst})[0]
    tp, tn = surface._tree_py(g, root), surface._tree_native(g, root)
    v = next(s["node"] for s in g.stands.values() if surface.tree_best(tp, s["node"]) is not None)
    assert surface.tree_best(tn, float(v)) == surface.tree_best(tp, float(v)) == surface.tree_best(tp, v)
    assert surface.tree_path(tn, float(v)) == surface.tree_path(tp, float(v))
    assert surface.tree_best(tn, v + 0.5) is None and surface.tree_best(tp, v + 0.5) is None


def test_native_graph_tolerates_stop_lines_mid_build():
    """stop_lines appending edgeless nodes while the native graph is built must not disable the native tree: keys
    past the node count with no edges are ignored, and the next call rebuilds for the new count."""
    g = _fresh_copy("KLGA")
    n = len(g.nodes)
    g.adj[n] = []                        # as if stop_lines added the adjacency of a node not yet in the snapshot
    ng = surface._native_graph(g)
    assert ng is not None and ng.n == n
    g.nodes.append(g.nodes[0])
    ng2 = surface._native_graph(g)
    assert ng2 is not None and ng2.n == n + 1
    del g.adj[n]


def test_turn_mismatch_falls_back_to_python(monkeypatch):
    """The run-time guard: a platform whose Rust turn angle differs from Python's builds that graph's trees in Python."""
    if not surface._TREE_NATIVE:
        pytest.skip("native tree not built")
    g = surface.load_graph("KATL")
    real = surface._heading_turn
    monkeypatch.setattr(surface, "_heading_turn", lambda g, p, u, v: real(g, p, u, v) + 1e-9)
    g._trees.pop("native_graph", None)
    try:
        assert surface._native_graph(g) is None and g._trees["native_graph"] is False
    finally:
        g._trees.pop("native_graph", None)
