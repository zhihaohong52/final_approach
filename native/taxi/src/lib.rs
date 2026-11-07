//! Rust port of the wait-search loop of `backend.wave.surface.schedule` (`_run_loop_py`).
//!
//! Every float operation is the Python one, in the same order (no reassociation, no fused multiply-add), and every
//! `max` / `min` / `bisect_left` / tuple comparison keeps Python's semantics (first of equals wins, lexicographic
//! tuples, strings by code point), so the results are bit-identical to the Python loop. `sum(seg[h:i])` follows
//! CPython 3.12's compensated (Neumaier) float sum. Constants (_TOL, _EPS, _WIN, _WIN_MIN, _MAX_ITER, GAP_MIN) are
//! passed in by the caller at each call, from the Python module's globals.
//!
//! The taxi-route Dijkstra tree builder (`_tree_py`) is ported in `tree.rs`, under the same rules.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use std::cmp::Ordering;
use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex};

mod tree;

/// One reservation interval `(from, to, callsign)`.
struct Iv {
    s: f64,
    e: f64,
    cs: Py<PyAny>, // the callsign object itself: compared (on a float tie) and returned as Python does
}

/// A converted reservation list and its longest interval (`_span`: `max(e - s)`, 0.0 when empty).
#[derive(Clone)]
struct Lst {
    v: Arc<Vec<Iv>>,
    span: f64,
}

static EMPTY: LazyLock<Arc<Vec<Iv>>> = LazyLock::new(|| Arc::new(Vec::new()));

fn empty() -> Lst {
    Lst { v: EMPTY.clone(), span: 0.0 }
}

/// Python's `max((x[1] - x[0] for x in lst), default=0.0)`.
fn span_of(v: &[Iv]) -> f64 {
    let mut it = v.iter();
    match it.next() {
        None => 0.0,
        Some(x0) => {
            let mut m = x0.e - x0.s;
            for x in it {
                let d = x.e - x.s;
                if d > m {
                    m = d;
                }
            }
            m
        }
    }
}

fn convert(obj: &Bound<'_, PyAny>) -> PyResult<Vec<Iv>> {
    let n = obj.len()?;
    let mut out = Vec::with_capacity(n);
    for item in obj.try_iter()? {
        let item = item?;
        let t = item.cast::<PyTuple>()?;
        if t.len() != 3 {
            return Err(pyo3::exceptions::PyTypeError::new_err("reservation interval must be (from, to, callsign)"));
        }
        let s: f64 = t.get_borrowed_item(0)?.extract()?;
        let e: f64 = t.get_borrowed_item(1)?.extract()?;
        let cs = t.get_item(2)?.unbind();
        out.push(Iv { s, e, cs });
    }
    Ok(out)
}

/// Converted lists keyed by the Python list's identity. An entry keeps the list and each of its item tuples alive, so
/// neither id can be reused; a hit needs the same list with the same item objects in the same order (tuples are
/// immutable), so a list mutated in place (appended to, re-sorted, an item removed and another added) is always
/// converted again. Cleared when full.
struct CacheEntry {
    obj: Py<PyAny>,
    items: Vec<Py<PyAny>>,
    lst: Lst,
}

type Cache = HashMap<usize, CacheEntry>;
static CACHE: LazyLock<Mutex<Cache>> = LazyLock::new(|| Mutex::new(HashMap::new()));
const CACHE_MAX: usize = 50_000;

fn same_items(list: &Bound<'_, PyList>, items: &[Py<PyAny>]) -> bool {
    let n = list.len();
    if n != items.len() {
        return false;
    }
    for (k, it) in items.iter().enumerate() {
        // borrowed reference, valid while the list is not mutated (we hold the GIL and run no Python code here)
        let p = unsafe { pyo3::ffi::PyList_GetItem(list.as_ptr(), k as pyo3::ffi::Py_ssize_t) };
        if p != it.as_ptr() {
            return false;
        }
    }
    true
}

fn cached(obj: &Bound<'_, PyAny>, cache: &mut Cache, evicted: &mut Vec<Cache>) -> PyResult<Lst> {
    let list = match obj.cast::<PyList>() {
        Ok(l) => l,
        Err(_) => {
            // a tuple or other sequence: `()` for a key with no list
            if obj.len()? == 0 {
                return Ok(empty());
            }
            let v = convert(obj)?;
            let span = span_of(&v);
            return Ok(Lst { v: Arc::new(v), span });
        }
    };
    if list.is_empty() {
        return Ok(empty());
    }
    let key = list.as_ptr() as usize;
    if let Some(h) = cache.get(&key) {
        if h.obj.as_ptr() == list.as_ptr() && same_items(list, &h.items) {
            return Ok(h.lst.clone());
        }
    }
    let items: Vec<Py<PyAny>> = list.iter().map(|x| x.unbind()).collect();
    let v = convert(list.as_any())?;
    let span = span_of(&v);
    let lst = Lst { v: Arc::new(v), span };
    if cache.len() >= CACHE_MAX {
        evicted.push(std::mem::take(cache));
    }
    cache.insert(key, CacheEntry { obj: list.clone().into_any().unbind(), items, lst: lst.clone() });
    Ok(lst)
}

/// Per-call conversion (own lists: small, and appended to between calls), deduplicated by identity within the call.
fn per_call(obj: &Bound<'_, PyAny>, seen: &mut HashMap<usize, Lst>) -> PyResult<Lst> {
    let key = obj.as_ptr() as usize;
    if let Some(l) = seen.get(&key) {
        return Ok(l.clone());
    }
    let l = if obj.len()? == 0 {
        empty()
    } else {
        let v = convert(obj)?;
        let span = span_of(&v);
        Lst { v: Arc::new(v), span }
    };
    seen.insert(key, l.clone());
    Ok(l)
}

/// The base reservation lists per index of a route: the node's (`nl`), the reversed whole edge's (`rl`) and the
/// step's (`sl`), as `_lists_at` / `_base_lists` build them.
#[pyclass(frozen)]
struct Lists {
    nl: Vec<Lst>,
    rl: Vec<Lst>,
    sl: Vec<Lst>,
}

fn col(py_col: &Bound<'_, PyAny>, cache: &mut Cache, evicted: &mut Vec<Cache>) -> PyResult<Vec<Lst>> {
    let mut out = Vec::new();
    for x in py_col.try_iter()? {
        out.push(cached(&x?, cache, evicted)?);
    }
    Ok(out)
}

#[pymethods]
impl Lists {
    #[new]
    fn new(nl: &Bound<'_, PyAny>, rl: &Bound<'_, PyAny>, sl: &Bound<'_, PyAny>) -> PyResult<Self> {
        let mut evicted = Vec::new();
        let out = {
            let mut c = CACHE.lock().unwrap();
            (col(nl, &mut c, &mut evicted), col(rl, &mut c, &mut evicted), col(sl, &mut c, &mut evicted))
        };
        drop(evicted); // decref evicted lists outside the lock
        Ok(Lists { nl: out.0?, rl: out.1?, sl: out.2? })
    }
}

struct Zone {
    strip: usize,
    h: usize,
    ze: usize,
    stuck: bool,
}

/// The route constants of `_route_holds` / `refine` the loop reads.
#[pyclass(frozen)]
struct Route {
    seg: Vec<f64>,
    park: f64,
    real: Vec<bool>,
    hold: Vec<usize>, // hold[0] is None in Python (index 0 never reads it)
    nxt_real: Vec<usize>,
    prev_real: Vec<usize>,
    zones_at: Vec<Vec<Zone>>,
    strips: Vec<String>,
    nodes: Vec<Py<PyAny>>,
    steps: Vec<Py<PyAny>>,
    revs: Vec<Py<PyAny>>,
}

#[pymethods]
impl Route {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        seg: Vec<f64>,
        park: f64,
        real: Vec<bool>,
        hold: Vec<Option<usize>>,
        nxt_real: Vec<usize>,
        prev_real: Vec<usize>,
        zones_at: &Bound<'_, PyDict>,
        nodes: &Bound<'_, PyList>,
        steps: &Bound<'_, PyList>,
        revs: &Bound<'_, PyList>,
    ) -> PyResult<Self> {
        let n = nodes.len();
        if seg.len() + 1 != n && !(n == 0 && seg.is_empty()) {
            return Err(pyo3::exceptions::PyValueError::new_err("seg must have one entry per step"));
        }
        if real.len() != n || hold.len() != n || nxt_real.len() != n || prev_real.len() != n
            || steps.len() != n || revs.len() != n
        {
            return Err(pyo3::exceptions::PyValueError::new_err("route arrays must have one entry per index"));
        }
        let mut za: Vec<Vec<Zone>> = (0..n).map(|_| Vec::new()).collect();
        let mut strips: Vec<String> = Vec::new();
        for (k, v) in zones_at.iter() {
            let c: usize = k.extract()?;
            if c >= n {
                return Err(pyo3::exceptions::PyValueError::new_err("zone index out of range"));
            }
            for z in v.try_iter()? {
                let (strip, h, ze, stuck): (String, usize, usize, bool) = z?.extract()?;
                if h >= n || ze >= n {
                    return Err(pyo3::exceptions::PyValueError::new_err("zone bound out of range"));
                }
                let si = match strips.iter().position(|s| *s == strip) {
                    Some(p) => p,
                    None => {
                        strips.push(strip);
                        strips.len() - 1
                    }
                };
                za[c].push(Zone { strip: si, h, ze, stuck });
            }
        }
        for (i, h) in hold.iter().enumerate() {
            if i > 0 && h.map_or(true, |h| h >= i) {
                return Err(pyo3::exceptions::PyValueError::new_err("hold must precede its index"));
            }
        }
        if nxt_real.iter().chain(prev_real.iter()).any(|&j| j >= n) {
            return Err(pyo3::exceptions::PyValueError::new_err("real-node index out of range"));
        }
        Ok(Route {
            seg,
            park,
            real,
            hold: hold.into_iter().map(|h| h.unwrap_or(0)).collect(),
            nxt_real,
            prev_real,
            zones_at: za,
            strips,
            nodes: nodes.iter().map(|x| x.unbind()).collect(),
            steps: steps.iter().map(|x| x.unbind()).collect(),
            revs: revs.iter().map(|x| x.unbind()).collect(),
        })
    }
}

/// A runway event usable by the gap check: `up = ev + gap_before`, `down = ev - gap_after` (Python computes these
/// sums first in every expression that uses them).
struct Ev {
    up: f64,
    down: f64,
    kind: Py<PyAny>,
    cs: Py<PyAny>,
}

/// Python's `bisect_left(lst, (v,), lo, hi)` on a list of `(s, e, cs)`: the same binary search, with
/// `lst[mid] < (v,)` being `s < v` (a 1-tuple sorts before any longer tuple with an equal first item).
#[inline]
fn bisect_left(v: &[Iv], x: f64, mut lo: usize, mut hi: usize) -> usize {
    while lo < hi {
        let mid = (lo + hi) / 2;
        if v[mid].s < x {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    lo
}

/// Python float comparison as used inside a tuple comparison (no NaN on these paths).
#[inline]
fn fcmp(a: f64, b: f64) -> Ordering {
    if a == b {
        Ordering::Equal
    } else if a < b {
        Ordering::Less
    } else {
        Ordering::Greater
    }
}

/// Python's comparison of two tuple items that may differ (`==` first, identity included, then `<`).
#[inline]
fn pycmp(py: Python<'_>, a: &Py<PyAny>, b: &Py<PyAny>) -> Ordering {
    if a.as_ptr() == b.as_ptr() {
        return Ordering::Equal;
    }
    let (a, b) = (a.bind(py), b.bind(py));
    if a.eq(b).unwrap_or(false) {
        Ordering::Equal
    } else if a.lt(b).unwrap_or(false) {
        Ordering::Less
    } else {
        Ordering::Greater
    }
}

/// CPython 3.12 `sum(xs)` for a non-empty list of floats: `0 + xs[0]`, then Neumaier compensation; an empty slice
/// sums to int 0 (`x - 0` is `x - 0.0`).
#[inline]
fn py_sum(xs: &[f64]) -> f64 {
    let mut it = xs.iter();
    let mut f = match it.next() {
        None => return 0.0,
        Some(&x) => 0.0 + x,
    };
    let mut c = 0.0f64;
    for &x in it {
        let t = f + x;
        if f.abs() >= x.abs() {
            c += (f - t) + x;
        } else {
            c += (x - t) + f;
        }
        f = t;
    }
    if c != 0.0 && c.is_finite() {
        f += c;
    }
    f
}

/// `sum(xs)` as CPython 3.12 computes it (for tests against Python's `sum`).
#[pyfunction]
fn fsum_compat(xs: Vec<f64>) -> f64 {
    py_sum(&xs)
}

/// The binding wait of an index: (strip index for a hold-short, else None; event kind; callsign).
struct Why {
    strip: Option<usize>,
    kind: Option<Py<PyAny>>,
    cs: Py<PyAny>,
}

struct St<'a> {
    r: &'a Route,
    t0: f64,
    arr: Vec<f64>,
    dep: Vec<f64>,
    nb: Vec<f64>,
    arr0_nb: f64,
    why: HashMap<i64, Why>,
}

impl St<'_> {
    fn times(&mut self, start: usize) {
        let n = self.arr.len();
        if start == 0 {
            // max(t0_min, arr0_nb): the first unless the second is greater
            self.arr[0] = if self.arr0_nb > self.t0 { self.arr0_nb } else { self.t0 };
        }
        let last = n - 1;
        for i in start..last {
            let a = self.arr[i];
            let b = self.nb[i];
            let d = if a >= b { a } else { b };
            self.dep[i] = d;
            self.arr[i + 1] = d + self.r.seg[i];
        }
        if start <= last {
            self.dep[last] = self.arr[last] + self.r.park;
        }
    }

    fn push(&mut self, i: usize, target: f64, reason: Why) -> usize {
        if i == 0 {
            if target > self.arr0_nb {
                self.arr0_nb = target;
                self.why.insert(-1, reason);
            }
            return 0;
        }
        let h = self.r.hold[i];
        let need = target - py_sum(&self.r.seg[h..i]);
        if need > self.nb[h] {
            self.nb[h] = need;
            self.why.insert(h as i64, reason);
        }
        h
    }
}

/// Run the loop. Returns (arr, dep, arr0_nb, why {index: (strip | None, kind | None, callsign)}, converged).
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn run_loop<'py>(
    py: Python<'py>,
    route: &Bound<'py, Route>,
    lists: &Bound<'py, Lists>,
    t0_min: f64,
    hw: f64,
    events: &Bound<'py, PyDict>,
    gap_min: &Bound<'py, PyDict>,
    busy_own: &Bound<'py, PyDict>,
    edir_own: &Bound<'py, PyDict>,
    tol: f64,
    eps: f64,
    win: f64,
    win_min: usize,
    max_iter: i64,
) -> PyResult<(Vec<f64>, Vec<f64>, f64, Bound<'py, PyDict>, bool)> {
    let r: &Route = route.get();
    let l: &Lists = lists.get();
    let n = r.nodes.len();
    if n == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err("empty route"));
    }
    if l.nl.len() != n || l.rl.len() != n || l.sl.len() != n {
        return Err(pyo3::exceptions::PyValueError::new_err("lists do not match the route"));
    }

    // runway events per strip of the route, in their (sorted) order, kinds outside GAP_MIN dropped
    let mut gaps: HashMap<String, (f64, f64)> = HashMap::new();
    for (k, v) in gap_min.iter() {
        let kind: String = k.extract()?;
        let (a, b): (f64, f64) = v.extract()?;
        gaps.insert(kind, (a, b));
    }
    let mut evs: Vec<Vec<Ev>> = Vec::with_capacity(r.strips.len());
    for strip in &r.strips {
        let mut out = Vec::new();
        let lst = events.get_item(strip.as_str())?.ok_or_else(|| {
            pyo3::exceptions::PyKeyError::new_err(strip.clone())
        })?;
        for x in lst.try_iter()? {
            let x = x?;
            let t = x.cast::<PyTuple>()?;
            if t.len() != 3 {
                return Err(pyo3::exceptions::PyTypeError::new_err("runway event must be (t, kind, callsign)"));
            }
            let ev: f64 = t.get_borrowed_item(0)?.extract()?;
            let kind_o = t.get_item(1)?;
            let kind: String = kind_o.extract()?;
            if let Some(&(g0, g1)) = gaps.get(&kind) {
                out.push(Ev { up: ev + g0, down: ev - g1, kind: kind_o.unbind(), cs: t.get_item(2)?.unbind() });
            }
        }
        evs.push(out);
    }

    // own lists for the keys this route reads
    let mut seen: HashMap<usize, Lst> = HashMap::new();
    let mut own_n: Vec<Lst> = Vec::new();
    if !busy_own.is_empty() {
        for x in &r.nodes {
            own_n.push(match busy_own.get_item(x.bind(py))? {
                Some(lst) => per_call(&lst, &mut seen)?,
                None => empty(),
            });
        }
    }
    let mut own_r: Vec<Lst> = Vec::new();
    let mut own_s: Vec<Lst> = Vec::new();
    if !edir_own.is_empty() {
        for (keys, out) in [(&r.revs, &mut own_r), (&r.steps, &mut own_s)] {
            for x in keys.iter() {
                let x = x.bind(py);
                out.push(if x.is_none() {
                    empty()
                } else {
                    match edir_own.get_item(x)? {
                        Some(lst) => per_call(&lst, &mut seen)?,
                        None => empty(),
                    }
                });
            }
        }
    }
    let have_own_n = !own_n.is_empty();
    let have_own_e = !own_r.is_empty();

    let mut st = St {
        r,
        t0: t0_min,
        arr: vec![0.0; n],
        dep: vec![0.0; n],
        nb: vec![f64::NEG_INFINITY; n],
        arr0_nb: t0_min,
        why: HashMap::new(),
    };
    st.times(0);

    let mut converged = true;
    let mut i: usize = 0;
    let mut it: i64 = 0;
    let mut recheck: usize = 0;
    while i < n {
        it += 1;
        if it > max_iter {
            converged = false;
            break;
        }
        let mut restart: Option<usize> = None;
        let only_b = i < recheck;

        // (c) runway crossing gap over the whole time inside the strip's protected zone
        if !only_b {
            for z in &r.zones_at[i] {
                if z.stuck {
                    continue;
                }
                let t_in = st.dep[z.h];
                let t_out = st.arr[z.ze];
                // max over (ev + gap_before + _EPS, kind, cs); the first maximal one is kept
                let mut best: Option<(f64, &Ev)> = None;
                for ev in &evs[z.strip] {
                    if t_in <= ev.up + tol && t_out >= ev.down - tol {
                        let t = ev.up + eps;
                        let better = match best {
                            None => true,
                            Some((bt, be)) => fcmp(t, bt)
                                .then_with(|| pycmp(py, &ev.kind, &be.kind))
                                .then_with(|| pycmp(py, &ev.cs, &be.cs))
                                == Ordering::Greater,
                        };
                        if better {
                            best = Some((t, ev));
                        }
                    }
                }
                if let Some((t, ev)) = best {
                    let target = t + r.seg[z.h];
                    let why = Why { strip: Some(z.strip), kind: Some(ev.kind.clone_ref(py)), cs: ev.cs.clone_ref(py) };
                    restart = Some(st.push(z.h + 1, target, why));
                    break;
                }
            }
        }

        // (a) the node is clear of other reservations by the headway, over the whole dwell
        if restart.is_none() && !only_b {
            let ai = st.arr[i];
            let di = st.dep[i];
            let mut best: Option<(f64, &Py<PyAny>)> = None;
            let lst = &l.nl[i];
            if !lst.v.is_empty() {
                let v = &lst.v[..];
                let hi = bisect_left(v, (di + hw) - tol, 0, v.len());
                let lo = if hi > win_min { bisect_left(v, ((ai - hw) - lst.span) - win, 0, hi) } else { 0 };
                for x in &v[lo.min(hi)..hi] {
                    let t = x.e + hw;
                    if ai < t - tol {
                        best = max2(py, best, t, &x.cs);
                    }
                }
            }
            if have_own_n {
                for x in own_n[i].v.iter() {
                    let t = x.e + hw;
                    if x.s < (di + hw) - tol && ai < t - tol {
                        best = max2(py, best, t, &x.cs);
                    }
                }
            }
            if let Some((t, cs)) = best {
                let why = Why { strip: None, kind: None, cs: cs.clone_ref(py) };
                restart = Some(st.push(i, t, why));
            }
        }

        // (b) the next edge is not in use in the opposite direction: wait before entering it
        if restart.is_none() && i + 1 < n && r.real[i] {
            let j = r.nxt_real[i];
            let aj = st.arr[j];
            let di = st.dep[i];
            let si = r.seg[i];
            let mut best: Option<(f64, &Py<PyAny>)> = None;
            let lst = &l.rl[i];
            if !lst.v.is_empty() {
                let v = &lst.v[..];
                let hi = bisect_left(v, aj - tol, 0, v.len());
                let lo = if hi > win_min { bisect_left(v, (di - lst.span) - win, 0, hi) } else { 0 };
                for x in &v[lo.min(hi)..hi] {
                    if di < x.e - tol {
                        best = max2(py, best, x.e + si, &x.cs);
                    }
                }
            }
            if have_own_e {
                for x in own_r[i].v.iter() {
                    if x.s < aj - tol && di < x.e - tol {
                        best = max2(py, best, x.e + si, &x.cs);
                    }
                }
            }
            if let Some((t, cs)) = best {
                let why = Why { strip: None, kind: None, cs: cs.clone_ref(py) };
                restart = Some(st.push(i + 1, t, why));
            }
        }

        // (d) same direction: no overtaking on the step to the next position
        if restart.is_none() && !only_b && i + 1 < n {
            let a = st.dep[i];
            let b = st.arr[i + 1];
            let mut first: Option<&Iv> = None;
            let base = &l.sl[i];
            let own = if have_own_e { Some(&own_s[i]) } else { None };
            for lst in std::iter::once(base).chain(own) {
                let v = &lst.v[..];
                let slice: &[Iv] = if v.len() > win_min {
                    let sp = lst.span;
                    let bs = b - sp;
                    let lo_v = if bs < a { bs } else { a }; // min(a, b - sp)
                    let hi_v = if b > a { b } else { a }; // max(a, b)
                    let lo = bisect_left(v, lo_v - win, 0, v.len());
                    let hi = bisect_left(v, hi_v, 0, v.len());
                    if lo < hi { &v[lo..hi] } else { &v[0..0] }
                } else {
                    v
                };
                for x in slice {
                    if (x.s > a + tol && x.e < b - tol) || (x.s < a - tol && x.e > b + tol) {
                        let smaller = match first {
                            None => true,
                            Some(f) => fcmp(x.s, f.s)
                                .then_with(|| fcmp(x.e, f.e))
                                .then_with(|| pycmp(py, &x.cs, &f.cs))
                                == Ordering::Less,
                        };
                        if smaller {
                            first = Some(x);
                        }
                        break;
                    }
                }
            }
            if let Some(x) = first {
                let why = Why { strip: None, kind: None, cs: x.cs.clone_ref(py) };
                let target = if x.s > a + tol { (x.s + r.seg[i]) + eps } else { x.e + eps };
                restart = Some(st.push(i + 1, target, why));
            }
        }

        match restart {
            None => i = if only_b { recheck } else { i + 1 },
            Some(rs) => {
                st.times(rs);
                i = r.prev_real[rs];
                recheck = rs;
            }
        }
    }

    let why = PyDict::new(py);
    for (k, w) in st.why.iter() {
        let strip: Option<&str> = w.strip.map(|s| r.strips[s].as_str());
        why.set_item(*k, (strip, w.kind.as_ref().map(|x| x.bind(py)), w.cs.bind(py)))?;
    }
    Ok((st.arr, st.dep, st.arr0_nb, why, converged))
}

/// `max` over `(t, cs)` tuples, keeping the first of equals: replace only when strictly greater.
#[inline]
fn max2<'a>(
    py: Python<'_>,
    best: Option<(f64, &'a Py<PyAny>)>,
    t: f64,
    cs: &'a Py<PyAny>,
) -> Option<(f64, &'a Py<PyAny>)> {
    match best {
        None => Some((t, cs)),
        Some((bt, bcs)) => {
            if fcmp(t, bt).then_with(|| pycmp(py, cs, bcs)) == Ordering::Greater {
                Some((t, cs))
            } else {
                best
            }
        }
    }
}

#[pymodule]
fn _taxi_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Route>()?;
    m.add_class::<Lists>()?;
    m.add_function(wrap_pyfunction!(run_loop, m)?)?;
    m.add_function(wrap_pyfunction!(fsum_compat, m)?)?;
    m.add_class::<tree::TreeGraph>()?;
    m.add_class::<tree::Tree>()?;
    m.add_function(wrap_pyfunction!(tree::build_tree, m)?)?;
    m.add_function(wrap_pyfunction!(tree::hypot_compat, m)?)?;
    Ok(())
}
