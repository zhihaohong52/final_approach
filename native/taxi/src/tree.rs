//! Rust port of the taxi-route Dijkstra tree builder `backend.wave.surface._tree_py`.
//!
//! Same search, bit for bit: states `(node, prev)` starting at `(root, -1)`; the heap pops `(t, v, u)` in Python tuple
//! order (float t, then node, then prev; equal tuples are the same state); popped-done states are skipped; `best[u]` is
//! the first popped state of `u`; an edge relaxes only if its target state is not done and `nt < dist` (strict), in
//! exactly `g.adj[u]` order (runway edges dropped). The edge time and the turn (`_heading_turn`) repeat the Python
//! float operations in the same order; `math.hypot` is CPython 3.12's `vector_norm` for two coordinates (`hypot2`), and
//! cos / acos / sqrt are the platform libm CPython calls. Constants (KT, TAXI_KT, SLOW_KT, TURN_SLOW_DEG, EARTH_R and
//! math.radians / math.degrees factors) are passed in by the caller from the Python module.
//!
//! Layout: the graph (`TreeGraph`, built once per `TaxiGraph`) holds a CSR adjacency without runway edges, node
//! coordinates, per edge its length, taxilane flag, interned (name, strip) index and state id, and the state table (one
//! id per distinct directed pair u -> v, so parallel edges share it; the root state is one more id, per tree). A tree
//! (`Tree`) holds per state dist (f64), parent (i32) and via (u32 index into the graph's (name, strip) table): 16 bytes
//! per state, plus the best state per node (i32). No Python object per state.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::collections::HashMap;
use std::sync::Arc;

/// Constants of the search, from the Python module.
#[derive(Clone, Copy)]
struct Consts {
    slow_den: f64, // SLOW_KT * KT
    taxi_den: f64, // TAXI_KT * KT
    turn_slow: f64, // TURN_SLOW_DEG
    ky: f64,       // math.radians(1.0) * EARTH_R
    deg2rad: f64,  // CPython's degToRad (math.radians(1.0))
    rad2deg: f64,  // CPython's radToDeg (math.degrees(1.0))
}

/// The pure-Rust part of a graph: shared with the searches (which run without the GIL).
struct Core {
    n: usize,
    lat: Vec<f64>,
    lon: Vec<f64>,
    row: Vec<u32>,    // CSR offsets, n + 1
    to: Vec<u32>,     // per edge
    len: Vec<f64>,    // per edge
    lane: Vec<bool>,  // per edge: kind == "taxilane"
    via: Vec<u32>,    // per edge: index into the (name, strip) table
    state: Vec<u32>,  // per edge u -> v: id of the state (v, u)
    st_node: Vec<u32>, // per state id (root state excluded): node v
    st_prev: Vec<u32>, // per state id: previous node u
    c: Consts,
}

/// CPython 3.12 `frexp` exponent of a finite nonzero positive double (`max = m * 2**e`, 0.5 <= m < 1).
fn frexp_exp(x: f64) -> i32 {
    let bits = x.to_bits();
    let e = ((bits >> 52) & 0x7ff) as i32;
    if e == 0 {
        // subnormal: normalise
        let m = bits & ((1u64 << 52) - 1);
        let lz = m.leading_zeros() as i32 - 12; // leading zeros within the 52-bit mantissa
        -1022 - lz
    } else {
        e - 1022
    }
}

/// `ldexp(1.0, k)`, exact for -1074 <= k <= 1023 (`vector_norm` asks for -1024..=1023: 2**-1024 is subnormal).
fn pow2(k: i32) -> f64 {
    if k >= -1022 {
        f64::from_bits(((k + 1023) as u64) << 52)
    } else {
        f64::from_bits(1u64 << (k + 1074))
    }
}

#[inline]
fn dl_mul(x: f64, y: f64) -> (f64, f64) {
    // CPython 3.12 dl_mul (Algorithm 3.5, fma build, the default)
    let z = x * y;
    let zz = x.mul_add(y, -z);
    (z, zz)
}

#[inline]
fn dl_fast_sum(a: f64, b: f64) -> (f64, f64) {
    let x = a + b;
    let y = (a - x) + b;
    (x, y)
}

const DBL_MIN: f64 = 2.2250738585072014e-308;

/// CPython 3.12 `vector_norm(2, vec, max, found_nan)`.
fn vector_norm2(mut vec: [f64; 2], max: f64, found_nan: bool) -> f64 {
    if max.is_infinite() {
        return max;
    }
    if found_nan {
        return f64::NAN;
    }
    if max == 0.0 {
        return max;
    }
    let max_e = frexp_exp(max);
    if max_e < -1023 {
        for x in vec.iter_mut() {
            *x /= DBL_MIN;
        }
        return DBL_MIN * vector_norm2(vec, max / DBL_MIN, found_nan);
    }
    let scale = pow2(-max_e);
    let mut csum = 1.0f64;
    let mut frac1 = 0.0f64;
    let mut frac2 = 0.0f64;
    for &x0 in vec.iter() {
        let x = x0 * scale;
        let pr = dl_mul(x, x);
        let sm = dl_fast_sum(csum, pr.0);
        csum = sm.0;
        frac1 += pr.1;
        frac2 += sm.1;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let pr = dl_mul(-h, h);
    let sm = dl_fast_sum(csum, pr.0);
    csum = sm.0;
    frac1 += pr.1;
    frac2 += sm.1;
    let x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h);
    h / scale
}

/// CPython 3.12 `math.hypot(x, y)`.
fn hypot2(x: f64, y: f64) -> f64 {
    let a = x.abs();
    let b = y.abs();
    let found_nan = a.is_nan() || b.is_nan();
    let mut max = 0.0f64;
    if a > max {
        max = a;
    }
    if b > max {
        max = b;
    }
    vector_norm2([a, b], max, found_nan)
}

/// `_heading_turn(g, p, u, v)`: the turn at `u` in degrees.
fn heading_turn(g: &Core, p: usize, u: usize, v: usize) -> f64 {
    let c = &g.c;
    let ky = c.ky;
    let kx = ky * (g.lat[u] * c.deg2rad).cos();
    let a0 = (g.lon[u] - g.lon[p]) * kx;
    let a1 = (g.lat[u] - g.lat[p]) * ky;
    let b0 = (g.lon[v] - g.lon[u]) * kx;
    let b1 = (g.lat[v] - g.lat[u]) * ky;
    let na = hypot2(a0, a1);
    let nb = hypot2(b0, b1);
    if na == 0.0 || nb == 0.0 {
        return 0.0;
    }
    let cc = (a0 * b0 + a1 * b1) / (na * nb);
    // max(-1.0, min(1.0, c)): Python's min / max keep the first of equals and compare with `<` / `>`
    let m = if cc < 1.0 { cc } else { 1.0 };
    let m = if m > -1.0 { m } else { -1.0 };
    m.acos() * c.rad2deg
}

/// A heap entry `(t, v, u)` ordered as a Python tuple, reversed for the max-heap. `s` (the state id) is determined by
/// (v, u) and does not take part in the order.
struct Entry {
    t: f64,
    v: u32,
    u: i64,
    s: u32,
}

impl PartialEq for Entry {
    fn eq(&self, o: &Self) -> bool {
        self.cmp(o) == Ordering::Equal
    }
}
impl Eq for Entry {}
impl PartialOrd for Entry {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}
impl Ord for Entry {
    fn cmp(&self, o: &Self) -> Ordering {
        // Python: (t1, v1, u1) < (t2, v2, u2) -- the first unequal pair decides (times are never NaN: finite lengths)
        let k = if self.t < o.t {
            Ordering::Less
        } else if self.t > o.t {
            Ordering::Greater
        } else {
            Ordering::Equal
        };
        k.then(self.v.cmp(&o.v)).then(self.u.cmp(&o.u)).reverse()
    }
}

struct Search {
    dist: Vec<f64>,
    parent: Vec<i32>,
    via: Vec<u32>,
    best: Vec<i32>,
}

const NO_VIA: u32 = u32::MAX;

fn search(g: &Core, root: usize) -> Search {
    let ns_states = g.st_node.len() + 1;
    let rs = g.st_node.len() as u32; // the root state (root, -1)
    let mut dist = vec![f64::INFINITY; ns_states];
    let mut parent = vec![-1i32; ns_states];
    let mut via = vec![NO_VIA; ns_states];
    let mut done = vec![false; ns_states];
    let mut best = vec![-1i32; g.n];
    dist[rs as usize] = 0.0;
    let mut heap = BinaryHeap::new();
    heap.push(Entry { t: 0.0, v: root as u32, u: -1, s: rs });
    while let Some(Entry { t, v: u, u: p, s: st }) = heap.pop() {
        let sti = st as usize;
        if done[sti] {
            continue;
        }
        done[sti] = true;
        let ui = u as usize;
        if best[ui] < 0 {
            best[ui] = st as i32;
        }
        let (a, b) = (g.row[ui] as usize, g.row[ui + 1] as usize);
        for e in a..b {
            let v = g.to[e];
            let slow = if g.lane[e] {
                true
            } else if p < 0 {
                false
            } else {
                heading_turn(g, p as usize, ui, v as usize) > g.c.turn_slow
            };
            let nt = t + g.len[e] / (if slow { g.c.slow_den } else { g.c.taxi_den });
            let ns = g.state[e] as usize;
            // Python: `if ns in done or nt >= dist.get(ns, inf): continue`
            if done[ns] || nt >= dist[ns] {
                continue;
            }
            dist[ns] = nt;
            parent[ns] = st as i32;
            via[ns] = g.via[e];
            heap.push(Entry { t: nt, v, u: ui as i64, s: ns as u32 });
        }
    }
    Search { dist, parent, via, best }
}

/// The Rust-side taxi graph of one `TaxiGraph`.
#[pyclass(frozen)]
pub struct TreeGraph {
    core: Arc<Core>,
    names: Vec<(Py<PyAny>, Py<PyAny>)>, // interned (name, strip) objects
}

#[pymethods]
impl TreeGraph {
    /// lat, lon: per node; row: CSR offsets (n + 1); to, length, lane, via: per non-runway edge in `g.adj[u]` order;
    /// names: the interned (name, strip) tuples `via` indexes.
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        lat: Vec<f64>,
        lon: Vec<f64>,
        row: Vec<u32>,
        to: Vec<u32>,
        length: Vec<f64>,
        lane: Vec<bool>,
        via: Vec<u32>,
        names: &Bound<'_, PyList>,
        slow_den: f64,
        taxi_den: f64,
        turn_slow: f64,
        ky: f64,
        deg2rad: f64,
        rad2deg: f64,
    ) -> PyResult<Self> {
        let err = |m: &str| Err(pyo3::exceptions::PyValueError::new_err(m.to_string()));
        let n = lat.len();
        if lon.len() != n || row.len() != n + 1 || row[0] != 0 {
            return err("lat, lon and row must have n, n and n + 1 entries");
        }
        let m = to.len();
        if length.len() != m || lane.len() != m || via.len() != m || row[n] as usize != m {
            return err("edge arrays must have one entry per edge");
        }
        if row.windows(2).any(|w| w[0] > w[1]) {
            return err("row must be non-decreasing");
        }
        if to.iter().any(|&v| v as usize >= n) {
            return err("edge target out of range");
        }
        let mut nm = Vec::with_capacity(names.len());
        for it in names.iter() {
            let t = it.cast::<PyTuple>()?;
            if t.len() != 2 {
                return err("names must be (name, strip) tuples");
            }
            nm.push((t.get_item(0)?.unbind(), t.get_item(1)?.unbind()));
        }
        if via.iter().any(|&k| k as usize >= nm.len()) {
            return err("via index out of range");
        }
        if m >= (i32::MAX as usize) - 1 || n >= (u32::MAX as usize) {
            return err("graph too large");
        }
        // one state id per distinct directed pair u -> v (the state (v, u)), in edge order
        let mut ids: HashMap<(u32, u32), u32> = HashMap::with_capacity(m);
        let mut state = Vec::with_capacity(m);
        let mut st_node = Vec::new();
        let mut st_prev = Vec::new();
        for u in 0..n {
            for e in row[u] as usize..row[u + 1] as usize {
                let v = to[e];
                let id = *ids.entry((u as u32, v)).or_insert_with(|| {
                    st_node.push(v);
                    st_prev.push(u as u32);
                    (st_node.len() - 1) as u32
                });
                state.push(id);
            }
        }
        let c = Consts { slow_den, taxi_den, turn_slow, ky, deg2rad, rad2deg };
        let core = Core { n, lat, lon, row, to, len: length, lane, via, state, st_node, st_prev, c };
        Ok(TreeGraph { core: Arc::new(core), names: nm })
    }

    #[getter]
    fn n(&self) -> usize {
        self.core.n
    }

    /// Number of states a tree of this graph holds (directed pairs + the root state).
    #[getter]
    fn states(&self) -> usize {
        self.core.st_node.len() + 1
    }

    /// `_heading_turn(g, p, u, v)` (for the bit-identity checks).
    fn turn_deg(&self, p: usize, u: usize, v: usize) -> PyResult<f64> {
        let n = self.core.n;
        if p >= n || u >= n || v >= n {
            return Err(pyo3::exceptions::PyIndexError::new_err("node out of range"));
        }
        Ok(heading_turn(&self.core, p, u, v))
    }
}

/// A Dijkstra tree from one root.
#[pyclass(frozen)]
pub struct Tree {
    graph: Py<TreeGraph>,
    root: u32,
    dist: Vec<f64>,
    parent: Vec<i32>,
    via: Vec<u32>,
    best: Vec<i32>,
}

impl Tree {
    fn best_state(&self, node: &Bound<'_, PyAny>) -> Option<usize> {
        let k: i64 = node.extract().ok()?;
        if k < 0 || k as usize >= self.best.len() {
            return None;
        }
        let s = self.best[k as usize];
        if s < 0 {
            None
        } else {
            Some(s as usize)
        }
    }
}

#[pymethods]
impl Tree {
    #[getter]
    fn root(&self) -> u32 {
        self.root
    }

    /// Fastest time in seconds from the root to `node`, None if unreachable.
    fn best(&self, node: &Bound<'_, PyAny>) -> Option<f64> {
        self.best_state(node).map(|s| self.dist[s])
    }

    /// The states from the root to `node`'s best state: [(node, dist_s, via_name, via_strip)], the root first as
    /// (root, 0.0, None, None); None if unreachable.
    fn path<'py>(&self, py: Python<'py>, node: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyList>>> {
        let Some(mut s) = self.best_state(node) else { return Ok(None) };
        let g = self.graph.get();
        let core = &g.core;
        let rs = core.st_node.len();
        let mut chain = Vec::new();
        loop {
            chain.push(s);
            let p = self.parent[s];
            if p < 0 {
                break;
            }
            s = p as usize;
        }
        let out = PyList::empty(py);
        for &s in chain.iter().rev() {
            let t = if s == rs {
                (self.root, self.dist[s], py.None(), py.None()).into_pyobject(py)?
            } else {
                let (nm, sp) = &g.names[self.via[s] as usize];
                (core.st_node[s], self.dist[s], nm.clone_ref(py), sp.clone_ref(py)).into_pyobject(py)?
            };
            out.append(t)?;
        }
        Ok(Some(out))
    }

    /// Bytes held by the tree's arrays.
    #[getter]
    fn nbytes(&self) -> usize {
        self.dist.len() * 8 + self.parent.len() * 4 + self.via.len() * 4 + self.best.len() * 4
    }

    /// The whole tree as Python's `_tree_py` dict ({"best", "parent", "via", "dist"}), for checks.
    fn as_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let g = self.graph.get();
        let core = &g.core;
        let rs = core.st_node.len();
        let key = |s: usize| -> PyResult<Bound<'py, PyTuple>> {
            if s == rs {
                PyTuple::new(py, [self.root as i64, -1])
            } else {
                PyTuple::new(py, [core.st_node[s] as i64, core.st_prev[s] as i64])
            }
        };
        let best = PyDict::new(py);
        let parent = PyDict::new(py);
        let via = PyDict::new(py);
        let dist = PyDict::new(py);
        for (u, &s) in self.best.iter().enumerate() {
            if s >= 0 {
                best.set_item(u, (self.dist[s as usize], key(s as usize)?))?;
            }
        }
        for s in 0..self.dist.len() {
            if self.dist[s] == f64::INFINITY && s != rs {
                continue;
            }
            let k = key(s)?;
            dist.set_item(&k, self.dist[s])?;
            if self.parent[s] < 0 {
                parent.set_item(&k, py.None())?;
            } else {
                parent.set_item(&k, key(self.parent[s] as usize)?)?;
            }
            if s != rs {
                let (nm, sp) = &g.names[self.via[s] as usize];
                via.set_item(&k, (nm.clone_ref(py), sp.clone_ref(py)))?;
            }
        }
        let out = PyDict::new(py);
        out.set_item("best", best)?;
        out.set_item("parent", parent)?;
        out.set_item("via", via)?;
        out.set_item("dist", dist)?;
        Ok(out)
    }
}

/// Build the tree from `root` (the search runs without the GIL).
#[pyfunction]
pub fn build_tree(py: Python<'_>, graph: &Bound<'_, TreeGraph>, root: usize) -> PyResult<Tree> {
    let core = graph.get().core.clone();
    if root >= core.n {
        return Err(pyo3::exceptions::PyIndexError::new_err("root out of range"));
    }
    let r = py.detach(move || search(&core, root));
    Ok(Tree { graph: graph.clone().unbind(), root: root as u32, dist: r.dist, parent: r.parent, via: r.via, best: r.best })
}

/// CPython 3.12 `math.hypot(x, y)` (for the bit-identity checks).
#[pyfunction]
pub fn hypot_compat(x: f64, y: f64) -> f64 {
    hypot2(x, y)
}
