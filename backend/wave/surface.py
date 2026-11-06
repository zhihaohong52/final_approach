"""Runtime taxi graph over an OSM surface document: runway exit choice, runway occupancy time (ROT) and
unimpeded taxi routes. Pure and deterministic; the model never decides routes. Built at wave-build time only
(rules, utility and negotiation never call the store).
"""
from __future__ import annotations

import heapq
import math
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from backend.geo.store import load_surface

KT = 0.514444                 # m/s per knot
TAXI_KT = 20.0
SLOW_KT = 10.0                # turns > 30 degrees and taxilanes
TURN_SLOW_DEG = 30.0
PARK_S = 30.0
ROLL_M = {"narrow": 1500.0, "wide": 2000.0}
WET_FACTOR = 1.15
BACKWARD_DEG = 90.0
FAST_EXIT_DEG = 45.0
V_EXIT_FAST_KT, V_EXIT_SLOW_KT = 25.0, 12.0
GENERIC_KT, GENERIC_DETOUR = 15.0, 1.4
EARTH_R = 6371000.0


@dataclass
class TaxiGraph:
    icao: str
    nodes: list[tuple[float, float]]
    # adj[u] = [(v, length_m, kind, name, crossing_strip)], both directions, runway edges included
    adj: dict[int, list[tuple[int, float, str, str | None, str | None]]]
    exits: dict[str, list[dict]]
    stands: dict[str, dict]
    reach_pct: dict[str, float]
    holds: dict[int, str | None] = field(default_factory=dict)   # OSM holding_position node -> strip (or None)
    source: str = "OpenStreetMap"   # the surface document's header source: "OpenStreetMap" or "X-Plane Scenery Gateway"
    _trees: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    # stop lines (`stop_lines`): built once per graph; their positions are appended to `nodes` (ids >= the real node
    # count, no edges), so they pickle with the graph and every geometry lookup works on them
    _lines: dict | None = field(default=None, init=False, repr=False, compare=False)

    def __getstate__(self) -> dict:
        """Pickle without the derived caches (trees, components, strip geometry): rebuilt lazily from the graph."""
        d = self.__dict__.copy()
        d["_trees"] = {}
        return d

    def stand_components(self) -> set[int]:
        """Taxi-network component ids (over non-runway edges) that contain at least one stand; cached."""
        hit = self._trees.get("stand_comps")
        if hit is None:
            comp = self._trees["comp"] = _components(self)
            hit = self._trees["stand_comps"] = {comp[s["node"]] for s in self.stands.values() if s["node"] in comp}
        return hit


class NoPath(Exception):
    """The stand is not reachable from the runway exit over taxi edges."""


@dataclass
class TaxiPath:
    nodes: list[int]
    seg_s: list[float]            # one per edge, then the parking time last
    names: list[str]              # one per edge
    crossings: list[tuple[int, str]]
    unimpeded_min: float
    src: Literal["osm", "synth"]
    _ref: object = field(default=None, repr=False, compare=False)      # `refine` cache (per graph)


def _components(g: TaxiGraph) -> dict[int, int]:
    comp: dict[int, int] = {}
    for r in range(len(g.nodes)):
        if r in comp:
            continue
        comp[r] = r
        stack = [r]
        while stack:
            u = stack.pop()
            for v, _, kind, _, _ in g.adj.get(u, ()):
                if kind != "runway" and v not in comp:
                    comp[v] = r
                    stack.append(v)
    return comp


def _from_doc(icao: str, doc: dict) -> TaxiGraph:
    nodes = [(float(p[0]), float(p[1])) for p in doc["nodes"]]
    adj: dict[int, list] = {i: [] for i in range(len(nodes))}
    for u, v, length, kind, name, strip in doc["edges"]:
        adj[u].append((v, float(length), kind, name, strip))
        adj[v].append((u, float(length), kind, name, strip))
    for lst in adj.values():
        lst.sort(key=lambda e: (e[0], e[1], e[3] or ""))
    # ends with no exits are treated as absent from OSM
    exits = {end: [dict(e) for e in lst] for end, lst in (doc.get("exits") or {}).items() if lst}
    raw = doc.get("stands") or []
    refs = [str(s["ref"]) for s in raw if s.get("ref") not in (None, "")]
    stands: dict[str, dict] = {}
    for s in sorted(raw, key=lambda s: str(s["id"])):
        ref = s.get("ref")
        # the ref may repeat across terminals: only a unique ref is a key, otherwise the stand id
        key = str(ref) if ref not in (None, "") and refs.count(str(ref)) == 1 else str(s["id"])
        stands[key] = dict(s)
    reach = dict(((doc.get("quality") or {}).get("reach_pct")) or {})
    holds: dict[int, str | None] = {}
    for h in sorted(doc.get("holding_positions") or [], key=lambda h: (h["node"], h.get("strip") or "")):
        if holds.get(int(h["node"])) is None:          # a node listed twice keeps a named strip over null
            holds[int(h["node"])] = h.get("strip")
    return TaxiGraph(icao, nodes, adj, exits, stands, reach, holds, (doc.get("header") or {}).get("source") or "OpenStreetMap")


@lru_cache(maxsize=16)   # waves of one airport share the graph object (and its cached trees)
def load_graph(icao: str) -> TaxiGraph | None:
    doc = load_surface(icao)
    if doc is None:
        return None
    g = _from_doc(icao, doc)
    stop_lines(g)                  # adds nodes: build them before the graph is shared between threads
    return g


def choose_exit(g: TaxiGraph, runway_end: str, size: Literal["narrow", "wide"], wet: bool) -> dict:
    """First exit with dist_m >= required roll (x1.15 wet), skipping backward exits (> 90 deg), else the last exit.
    An end with no exits (absent, or an empty list) gets a generic exit: node None, the required roll, 90 deg."""
    need = ROLL_M[size] * (WET_FACTOR if wet else 1.0)
    ex = g.exits.get(runway_end) or []
    if not ex:
        return {"node": None, "dist_m": need, "angle_deg": 90.0, "generic": True}
    # an exit whose taxi fragment holds no stand leads nowhere (stub in the OSM data): skip it unless all are such
    comps = g.stand_components()
    comp = g._trees["comp"]
    ex = [e for e in ex if comp.get(e["node"]) in comps] or ex
    fwd = [e for e in ex if e["angle_deg"] <= BACKWARD_DEG] or ex
    for e in fwd:
        if e["dist_m"] >= need:
            return e
    return fwd[-1]


def rot_s(exit: dict, final_gs_kt: float) -> float:
    v_exit = V_EXIT_FAST_KT if exit["angle_deg"] <= FAST_EXIT_DEG else V_EXIT_SLOW_KT
    return 2.0 * exit["dist_m"] / ((final_gs_kt + v_exit) * KT)


def _heading_turn(g: TaxiGraph, p: int, u: int, v: int) -> float:
    ky = math.radians(1.0) * EARTH_R
    kx = ky * math.cos(math.radians(g.nodes[u][0]))
    a = ((g.nodes[u][1] - g.nodes[p][1]) * kx, (g.nodes[u][0] - g.nodes[p][0]) * ky)
    b = ((g.nodes[v][1] - g.nodes[u][1]) * kx, (g.nodes[v][0] - g.nodes[u][0]) * ky)
    na, nb = math.hypot(*a), math.hypot(*b)
    if na == 0 or nb == 0:
        return 0.0
    c = (a[0] * b[0] + a[1] * b[1]) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _tree(g: TaxiGraph, root: int) -> dict:
    """Dijkstra on time from `root` over states (node, previous node), since the speed depends on the turn.
    Runway edges are never traversed. Returns {node: (time_s, state)} and the parent map."""
    key = (g.icao, root)
    hit = g._trees.get(key)
    if hit is not None:
        return hit
    best: dict[int, tuple[float, tuple]] = {}
    parent: dict[tuple, tuple | None] = {}
    via: dict[tuple, tuple[str | None, str | None]] = {}   # state -> (name, strip) of the edge entered by
    done: set[tuple] = set()
    # one tuple object per state and per (name, strip): the tree holds ~10k states at a hub, so sharing them (instead
    # of a fresh tuple per dict value) roughly halves its memory; the returned structure is unchanged
    canon: dict[tuple, tuple] = {}
    names: dict[tuple, tuple] = {}
    # (p, u, v) -> turn at u > TURN_SLOW_DEG: pure geometry, shared by every tree of the graph (each hub build grows
    # ~20 trees over the same turns)
    slow_turn = g._trees.setdefault("turn_slow", {})
    start = (root, -1)
    canon[start] = start
    dist = {start: 0.0}
    parent[start] = None
    heap = [(0.0, root, -1)]
    while heap:
        t, u, p = heapq.heappop(heap)
        st = canon[(u, p)]
        if st in done:
            continue
        done.add(st)
        if u not in best:
            best[u] = (t, st)
        for v, length, kind, name, strip in g.adj.get(u, ()):
            if kind == "runway":
                continue
            if kind == "taxilane":
                slow = True
            elif p < 0:
                slow = False
            else:
                slow = slow_turn.get((p, u, v))
                if slow is None:
                    slow = slow_turn[(p, u, v)] = _heading_turn(g, p, u, v) > TURN_SLOW_DEG
            nt = t + length / ((SLOW_KT if slow else TAXI_KT) * KT)
            ns = (v, u)
            if ns in done or nt >= dist.get(ns, math.inf):
                continue
            ns = canon.setdefault(ns, ns)
            nm = (name, strip)
            dist[ns] = nt
            parent[ns] = st
            via[ns] = names.setdefault(nm, nm)
            heapq.heappush(heap, (nt, v, u))
    out = {"best": best, "parent": parent, "via": via, "dist": dist}
    _keep_tree(g, key, out)
    return out


MAX_TREES = 24   # Dijkstra trees kept in total, over every graph (FIFO): ~1.9 MB each at ORD (tracemalloc); an ORD wave build uses 24
_TREE_ORDER: list[tuple[TaxiGraph, tuple]] = []
_TREE_LOCK = threading.Lock()


def _keep_tree(g: TaxiGraph, key: tuple, tree: dict) -> None:
    """Cache a Dijkstra tree on its graph, evicting the oldest tree of ANY graph beyond MAX_TREES: graphs are pinned
    on live waves (one object per load), so a per-graph cap would let tree memory grow with the number of waves. The
    per-wave taxi table holds the routes the hot path reads, so an evicted tree only costs a recompute."""
    with _TREE_LOCK:
        g._trees[key] = tree
        _TREE_ORDER.append((g, key))
        while len(_TREE_ORDER) > MAX_TREES:
            og, ok = _TREE_ORDER.pop(0)
            og._trees.pop(ok, None)


def unimpeded(g: TaxiGraph, exit_node: int, stand_node: int) -> TaxiPath:
    """Fastest taxi from the runway exit node to the stand over taxi edges, plus 30 s to park."""
    if exit_node is None or not 0 <= exit_node < len(g.nodes):
        raise NoPath(f"exit node {exit_node} is not in the graph")
    tr = _tree(g, exit_node)
    if stand_node not in tr["best"]:
        raise NoPath(f"stand node {stand_node} is not reachable from exit node {exit_node}")
    states = []
    st = tr["best"][stand_node][1]
    while st is not None:
        states.append(st)
        st = tr["parent"][st]
    states.reverse()
    nodes = [s[0] for s in states]
    seg_s, names, crossings = [], [], []
    for i in range(1, len(states)):
        seg_s.append(tr["dist"][states[i]] - tr["dist"][states[i - 1]])
        names.append(tr["via"][states[i]][0] or "")
        # taxi -> runway node -> taxi: both taxi edges carry the strip, count the node once
        if i >= 2:
            a, b = tr["via"][states[i - 1]][1], tr["via"][states[i]][1]
            if a is not None and a == b:
                crossings.append((nodes[i - 1], a))
    seg_s.append(PARK_S)
    # OSM strip tags are incomplete: merge in the geometric crossings (on-pavement nodes, edges across a centreline)
    crossings = merge_crossings(g, nodes, crossings)
    return TaxiPath(nodes, seg_s, names, crossings, sum(seg_s) / 60.0, "osm")


def generic_taxi_min(runway, gate_lat: float, gate_lon: float) -> float:
    """Generic tier: straight line from the runway midpoint to the gate x 1.4 at 15 kt, in minutes."""
    if None in (runway.le_lat, runway.le_lon, runway.he_lat, runway.he_lon):
        raise ValueError(f"runway {runway.id} has no end coordinates")
    mlat = (runway.le_lat + runway.he_lat) / 2.0
    mlon = (runway.le_lon + runway.he_lon) / 2.0
    ky = math.radians(1.0) * EARTH_R
    kx = ky * math.cos(math.radians(mlat))
    d = math.hypot((gate_lon - mlon) * kx, (gate_lat - mlat) * ky)
    return d * GENERIC_DETOUR / (GENERIC_KT * KT) / 60.0


# --- runway geometry (pure lookups, shared by the router, the scheduler and the verifier) ---

RUNWAY_HOLD_CLEARANCE_M = 90.0   # no waiting closer than this to a crossed strip's centreline, unless at an OSM hold
# A node closer than this to a strip's centreline (between its ends) is on the runway. 45 m = half of a 200 ft (61 m)
# runway (30.5 m) + a 35 ft (10.7 m) shoulder (FAA AC 150/5300-13B Table 3-8, ADG V/VI) + ~4 m OSM node tolerance.
RUNWAY_ON_PAVEMENT_M = 45.0


def _strip_data(g: TaxiGraph, strip: str) -> tuple[list[tuple[int, int]], frozenset[int]]:
    """Centreline segments (runway edges named `strip`) and its end nodes (degree 1 within those edges); cached."""
    key = ("strip_segs", strip)
    hit = g._trees.get(key)
    if hit is None:
        segs = sorted({(min(u, v), max(u, v)) for u, es in g.adj.items() for v, _, kind, name, _ in es
                       if kind == "runway" and name == strip})
        deg: dict[int, int] = {}
        for u, v in segs:
            deg[u] = deg.get(u, 0) + 1
            deg[v] = deg.get(v, 0) + 1
        hit = g._trees[key] = (segs, frozenset(n for n, d in deg.items() if d == 1))
    return hit


def runway_strips(g: TaxiGraph) -> list[str]:
    hit = g._trees.get("strips")
    if hit is None:
        hit = g._trees["strips"] = sorted({name for es in g.adj.values() for _, _, kind, name, _ in es
                                           if kind == "runway" and name})
    return hit


def runway_strips_at(g: TaxiGraph, node: int) -> set[str]:
    """Strips whose runway edges touch `node` (for a runway exit node: the runway it is on)."""
    return {name for _, _, kind, name, _ in g.adj.get(node, ()) if kind == "runway" and name}


def _local(g: TaxiGraph, lat0: float):
    ky = math.radians(1.0) * EARTH_R
    kx = ky * math.cos(math.radians(lat0))
    return kx, ky


def _strip_geom(g: TaxiGraph, node: int, strip: str) -> tuple[float, bool]:
    """(distance to the centreline polyline in m, whether the nearest point lies between the strip's ends); cached."""
    key = ("strip_g", node, strip)
    hit = g._trees.get(key)
    if hit is not None:
        return hit
    hit = g._trees[key] = _strip_geom_at(g, *g.nodes[node], strip)
    return hit


def _strip_geom_at(g: TaxiGraph, lat0: float, lon0: float, strip: str) -> tuple[float, bool]:
    segs, ends = _strip_data(g, strip)
    kx, ky = _local(g, lat0)
    best, between = math.inf, False
    for u, v in segs:
        ax, ay = (g.nodes[u][1] - lon0) * kx, (g.nodes[u][0] - lat0) * ky
        bx, by = (g.nodes[v][1] - lon0) * kx, (g.nodes[v][0] - lat0) * ky
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else -(ax * dx + ay * dy) / L2
        f = max(0.0, min(1.0, t))
        d = math.hypot(ax + f * dx, ay + f * dy)
        if d < best:
            best = d
            between = not ((t < 0 and u in ends) or (t > 1 and v in ends))
    return best, between


def strip_distance_m(g: TaxiGraph, node: int, strip: str) -> float:
    """Pure geometry: distance in metres from a node to the centreline of `strip` (its runway edges); inf if the strip
    has no runway edges in the graph."""
    return _strip_geom(g, node, strip)[0]


def on_runway(g: TaxiGraph, node: int, strip: str) -> bool:
    """Pure geometry: the node is on the strip's pavement (within RUNWAY_ON_PAVEMENT_M, between its ends)."""
    d, between = _strip_geom(g, node, strip)
    return between and d < RUNWAY_ON_PAVEMENT_M


def edge_crosses_strip(g: TaxiGraph, u: int, v: int, strip: str) -> bool:
    """Pure geometry: the straight edge u-v crosses the strip's centreline strictly inside both segments; cached."""
    key = ("edge_x", u, v, strip)
    hit = g._trees.get(key)
    if hit is None:
        hit = g._trees[key] = _edge_crosses_strip(g, u, v, strip)
    return hit


def _edge_crosses_strip(g: TaxiGraph, u: int, v: int, strip: str) -> bool:
    du, dv = strip_distance_m(g, u, strip), strip_distance_m(g, v, strip)
    lat0, lon0 = g.nodes[u]
    kx, ky = _local(g, lat0)
    px, py = (g.nodes[v][1] - lon0) * kx, (g.nodes[v][0] - lat0) * ky
    if du + dv > 2.0 * math.hypot(px, py) + 1.0:      # cannot reach the centreline
        return False
    for a, b in _strip_data(g, strip)[0]:
        ax, ay = (g.nodes[a][1] - lon0) * kx, (g.nodes[a][0] - lat0) * ky
        bx, by = (g.nodes[b][1] - lon0) * kx, (g.nodes[b][0] - lat0) * ky
        rx, ry = bx - ax, by - ay
        den = px * ry - py * rx
        if abs(den) < 1e-12:
            continue
        t = (ax * ry - ay * rx) / den                 # along u-v
        s = (ax * py - ay * px) / den                 # along a-b
        if 1e-9 < t < 1 - 1e-9 and 0.0 <= s <= 1.0:
            return True
    return False


def merge_crossings(g: TaxiGraph, nodes: list[int], tagged: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Runway crossings of a route: OSM-tagged crossings (both taxi edges at a node carry the strip) merged with
    geometric ones, since OSM strip tags are often missing. Geometric: a stretch of consecutive on-pavement nodes
    (except the stretch at the route's first node on the runway it starts on: that is vacating, not crossing), or an
    edge crossing the centreline. Detections of one strip within one stretch of nodes closer than
    RUNWAY_HOLD_CLEARANCE_M collapse to one crossing (tagged node first, else the node nearest the centreline).
    Returns (node, strip) ordered along the route."""
    n = len(nodes)
    idx_tag: list[tuple[int, str]] = []
    k = 1
    for node, strip in tagged:
        c = next((i for i in range(k, n) if nodes[i] == node), None)
        if c is not None:
            idx_tag.append((c, strip))
            k = c + 1
    start_on = runway_strips_at(g, nodes[0]) if n else set()
    out: list[tuple[int, str]] = []
    elen = []                                 # straight edge lengths, computed exactly as in _edge_crosses_strip
    for i in range(n - 1):
        u, v = nodes[i], nodes[i + 1]
        lat0, lon0 = g.nodes[u]
        kx, ky = _local(g, lat0)
        elen.append(math.hypot((g.nodes[v][1] - lon0) * kx, (g.nodes[v][0] - lat0) * ky))
    for strip in runway_strips(g):
        sd = g._trees.setdefault(("strip_dist", strip), {})    # node -> distance (same values, fewer lookups)
        d = []
        for nd in nodes:
            x = sd.get(nd)
            if x is None:
                x = sd[nd] = strip_distance_m(g, nd, strip)
            d.append(x)
        if min(d, default=math.inf) > 5000.0:
            continue
        cand: list[tuple[int, int, float]] = [(c, 0, d[c]) for c, st in idx_tag if st == strip]   # (index, prio, d)
        # on_runway(node) needs d < RUNWAY_ON_PAVEMENT_M: test that first (same result, far fewer calls)
        pave = [d[i] < RUNWAY_ON_PAVEMENT_M and on_runway(g, nodes[i], strip) for i in range(n)]
        i = 0
        while i < n:
            if not pave[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and pave[j + 1]:
                j += 1
            if not (i == 0 and strip in start_on):
                c = min(range(i, j + 1), key=lambda x: (d[x], x))
                cand.append((c, 1, d[c]))
            i = j + 1
        for i in range(n - 1):
            # _edge_crosses_strip's own first test (the edge cannot reach the centreline), done here without the call
            if d[i] + d[i + 1] > 2.0 * elen[i] + 1.0:
                continue
            if edge_crosses_strip(g, nodes[i], nodes[i + 1], strip):
                c = i if d[i] <= d[i + 1] else i + 1
                cand.append((c, 2, d[c]))
        if not cand:
            continue
        group: list[object] = []
        gid = 0
        for i in range(n):
            if d[i] < RUNWAY_HOLD_CLEARANCE_M:
                if i == 0 or d[i - 1] >= RUNWAY_HOLD_CLEARANCE_M:
                    gid += 1
                group.append(gid)
            else:
                group.append(("solo", i))
        best: dict[object, tuple[int, int, float]] = {}
        for c, prio, dc in sorted(cand):
            key = group[c]
            if key not in best or (prio, dc, c) < (best[key][1], best[key][2], best[key][0]):
                best[key] = (c, prio, dc)
        out.extend((c, strip) for c, _, _ in best.values())
    return [(nodes[c], strip) for c, strip in sorted(out)]


# --- ground traffic: hold-short scheduling (single-path safe-interval search) ---

# --- stop lines (block signalling): where a taxiing aircraft may wait (assumption, rules.LIMITS "taxi stop lines") ---
JUNCTION_CLEAR_M = 40.0       # a waiting aircraft's nose this far short of a junction (and its tail this far past one)
TAXI_AIRCRAFT_LEN_M = 45.0    # queue: an aircraft this long ...
TAXI_QUEUE_GAP_M = 15.0       # ... this far behind the one ahead (nose to nose: 60 m)
_QUEUE_M = TAXI_AIRCRAFT_LEN_M + TAXI_QUEUE_GAP_M
_TAIL_M = TAXI_AIRCRAFT_LEN_M + JUNCTION_CLEAR_M
_LINES_LOCK = threading.Lock()


def junctions(g: TaxiGraph) -> frozenset[int]:
    """Taxiway junctions: nodes with three or more distinct non-runway neighbours, not counting dead-end stubs (a stand
    lead); cached."""
    hit = g._trees.get("junctions")
    if hit is None:
        nb = {u: {v for v, _, kind, _, _ in es if kind != "runway"} for u, es in g.adj.items()}
        hit = g._trees["junctions"] = frozenset(u for u, vs in nb.items()
                                                if sum(1 for v in vs if len(nb.get(v, ())) > 1) >= 3)
    return hit


def stop_lines(g: TaxiGraph) -> dict[tuple[int, int], tuple[float, tuple[tuple[int, float], ...]]]:
    """Per directed taxi edge (u, v): (length m, ((stop-line node, metres from u), ...) in order from u). The line
    nearest v is JUNCTION_CLEAR_M short of v when v is a junction, else one queue spacing (v itself holds an aircraft);
    the others follow every TAXI_AIRCRAFT_LEN_M + TAXI_QUEUE_GAP_M back towards u, keeping that spacing from u (the
    tail clear of u when u is a junction). Lines closer than RUNWAY_HOLD_CLEARANCE_M to any runway centreline are left
    out. Built once per graph, in sorted order (deterministic ids), the positions appended to `g.nodes`."""
    if g._lines is not None:
        return g._lines
    with _LINES_LOCK:
        if g._lines is not None:
            return g._lines
        junc = junctions(g)
        strips = runway_strips(g)
        lines: dict[tuple[int, int], tuple[float, tuple[tuple[int, float], ...]]] = {}
        new: list[tuple[float, float]] = []
        n0 = len(g.nodes)
        for u in sorted(g.adj):
            for v, length, kind, _, _ in g.adj[u]:
                if kind == "runway" or (u, v) in lines:
                    continue
                pos, d = [], JUNCTION_CLEAR_M if v in junc else _QUEUE_M
                lim = _TAIL_M if u in junc else _QUEUE_M
                while length - d >= lim - 1e-9:
                    pos.append(length - d)
                    d += _QUEUE_M
                out = []
                for x in sorted(pos):
                    f = x / length
                    lat = g.nodes[u][0] + f * (g.nodes[v][0] - g.nodes[u][0])
                    lon = g.nodes[u][1] + f * (g.nodes[v][1] - g.nodes[u][1])
                    # |point - u| = x: the triangle inequality skips the exact distance for lines far from a runway
                    if any(strip_distance_m(g, u, st) - x < RUNWAY_HOLD_CLEARANCE_M
                           and strip_distance_m(g, v, st) - (length - x) < RUNWAY_HOLD_CLEARANCE_M
                           and _strip_geom_at(g, lat, lon, st)[0] < RUNWAY_HOLD_CLEARANCE_M for st in strips):
                        continue
                    out.append((n0 + len(new), x))
                    new.append((lat, lon))
                lines[(u, v)] = (length, tuple(out))
        g.nodes.extend(new)
        for k in range(n0, n0 + len(new)):
            g.adj[k] = []
        g._lines = lines
    return lines


@dataclass
class _Refined:
    """A path with its stop lines inserted: `nodes` (real and line nodes), `seg` (minutes per step, each edge's time
    split by distance), `dist` (metres from the start), `real` (a graph node, not a line) and `line_ok` (a stop line
    where the aircraft may wait on this path: its tail is clear of the junction it came through)."""
    nodes: list[int]
    seg: list[float]
    park: float
    dist: list[float]
    real: list[bool]
    junction: list[bool]


def refine(g: TaxiGraph, path: TaxiPath) -> _Refined:
    """The path with every stop line of its edges inserted (`stop_lines`); cached on the path."""
    hit = path._ref
    if hit is not None and hit[0] is g:
        return hit[1]
    lines = stop_lines(g)
    junc = junctions(g)
    p = path.nodes
    nodes, seg, dist, real = [p[0]] if p else [], [], [0.0] if p else [], [True] if p else []
    for i in range(len(p) - 1):
        u, v = p[i], p[i + 1]
        t = path.seg_s[i] / 60.0
        length, lst = lines.get((u, v)) or (_flat_m(g, u, v), ())
        base, x0 = dist[-1], 0.0
        for nd, x in lst:
            nodes.append(nd)
            seg.append(t * (x - x0) / length)
            dist.append(base + x)
            real.append(False)
            x0 = x
        nodes.append(v)
        seg.append(t * (length - x0) / length if length > 0 else t)
        dist.append(base + length)
        real.append(True)
    park = path.seg_s[len(p) - 1] / 60.0 if len(path.seg_s) >= len(p) else 0.0
    out = _Refined(nodes, seg, park, dist, real, [r and nd in junc for r, nd in zip(real, nodes)])
    path._ref = (g, out)
    return out


def _flat_m(g: TaxiGraph, u: int, v: int) -> float:
    kx, ky = _local(g, g.nodes[u][0])
    return math.hypot((g.nodes[v][1] - g.nodes[u][1]) * kx, (g.nodes[v][0] - g.nodes[u][0]) * ky)


def _stop_holds(g: TaxiGraph, r: _Refined, hold0: list[int | None], zones: list, vacate: int) -> list[int | None]:
    """Where to wait before entering each index of a refined path. Inside a runway protected zone and the vacate
    stretch, the zone's hold (`_crossing_zones`). Elsewhere the last stop point at least JUNCTION_CLEAR_M short of the
    index when it is a junction, else a queue spacing (TAXI_AIRCRAFT_LEN_M + TAXI_QUEUE_GAP_M) behind it. Stop points:
    the first index, each zone's hold, the end of the vacate stretch, OSM holding positions, and every other node that
    is not a junction, is not inside a zone, has its nose JUNCTION_CLEAR_M short of the next junction on the path and
    its tail clear of the last one (TAXI_AIRCRAFT_LEN_M + JUNCTION_CLEAR_M past it). Past the vacate stretch the hold
    never goes back before its end (onto the runway just vacated), even when that end is closer than the spacing."""
    n = len(r.nodes)
    fixed = set(range(1, vacate + 1))
    for _, _, h, ze, _ in zones:
        fixed.update(range(h + 1, ze + 1))
    always = {0, vacate} | {z[2] for z in zones}
    nxt, last = [math.inf] * n, math.inf          # distance to the next junction ahead (after the index)
    for i in range(n - 1, -1, -1):
        nxt[i] = last - r.dist[i]
        if r.junction[i]:
            last = r.dist[i]
    stop, prev = [False] * n, -math.inf           # distance past the last junction behind
    for i in range(n):
        if i in always or (r.real[i] and r.nodes[i] in g.holds):
            stop[i] = True
        elif i not in fixed and not r.junction[i]:
            stop[i] = nxt[i] >= JUNCTION_CLEAR_M - _TOL and r.dist[i] - prev >= _TAIL_M - _TOL
        if r.junction[i]:
            prev = r.dist[i]
    hold: list[int | None] = [None] * n
    pts: list[int] = []
    for i in range(1, n):
        if stop[i - 1]:
            pts.append(i - 1)
        if i in fixed:
            hold[i] = hold0[i]
            continue
        need = JUNCTION_CLEAR_M if r.junction[i] else _QUEUE_M
        k = len(pts) - 1
        # never back onto the runway it has just vacated: past the vacate stretch the hold is at its end or later
        while k > 0 and pts[k] > (vacate if i > vacate else 0) and r.dist[i] - r.dist[pts[k]] < need - _TOL:
            k -= 1
        hold[i] = pts[k]
    return hold


HEADWAY_S = 30.0              # node headway (assumption)
HORIZON_MIN = 30.0            # search horizon (assumption)
# crossing blocked if the event is in [t_in - a, t_out + b] min. A takeoff: the departure has rolled past the crossing
# point 1 min after it started; the next one starts 15 s after the crossers leave the zone (rules.LIMITS)
GAP_MIN = {"landing": (1.0, 1.5), "takeoff": (1.0, 0.25)}
HOLD_LOOKBACK = 3             # edges before a crossing within which an OSM holding position is used
_TOL = 1e-9
_EPS = 1e-6                   # release just after a closed crossing window
_MAX_ITER = 200000


@dataclass
class Reservations:
    """Ground reservation table, times in minutes. `node_busy[n]` and `edge_dir[(u, v)]` hold `(from, to, callsign)`
    intervals (an edge entry is a traversal from u to v); `runway_events[strip]` holds
    `(t, "landing"|"takeoff", cs)`."""
    node_busy: dict[int, list[tuple[float, float, str]]]
    edge_dir: dict[tuple[int, int], list[tuple[float, float, str]]]
    runway_events: dict[str, list[tuple[float, str, str]]]


@dataclass
class Scheduled:
    points: list[dict]            # {t_min, lat, lon, node}: arrival at each node, departure after a wait, then parked
    waits: list[dict]             # {node, lat, lon, from_min, to_min, reason, strip, kind, for}
    ok: bool
    reason: str | None
    # arrivals: time the aircraft reaches the first node outside the protected zone of the runway it exits (it has
    # vacated); a route that starts off any runway (departures): time it leaves the first node
    exit_clear_min: float
    # what this call added to `res`, in order: ("node", node, interval) / ("edge", (u, v), interval); lets a caller
    # that reuses the result add the same reservations (`trajectory._ground`)
    reserved: tuple = field(default=(), repr=False, compare=False)


@dataclass
class GroundTrack:
    callsign: str
    kind: Literal["arrival", "departure"]
    wave: bool
    exit: str | None
    stand: str
    route_names: list[str]
    points: list[dict]
    waits: list[dict]
    unimpeded_min: float
    taxi_min: float
    src: str

    def to_dict(self) -> dict:
        """Contract `TrajectorySet.ground[]` entry: node ids serialise as strings; `exit` omitted when None."""
        pts = []
        for q in self.points:
            d = {"t_min": q["t_min"], "lat": q["lat"], "lon": q["lon"]}
            if q.get("node") is not None and not q.get("line"):
                d["node"] = str(q["node"])
            pts.append(d)
        out = {"callsign": self.callsign, "kind": self.kind, "wave": self.wave}
        if self.exit is not None:
            out["exit"] = self.exit
        out.update({"stand": self.stand, "route_names": list(self.route_names), "points": pts,
                    "waits": [{k: w[k] for k in ("lat", "lon", "from_min", "to_min", "reason")} for w in self.waits],
                    "unimpeded_min": self.unimpeded_min, "taxi_min": self.taxi_min, "src": self.src})
        return out


def _osm_hold_for(g: TaxiGraph, node: int, strip: str) -> bool:
    """An OSM holding position for `strip` (or for no named strip) that is off the strip's pavement. Some OSM holds
    were snapped onto runway nodes by the surface builder; those are on the runway and never a place to hold."""
    return node in g.holds and g.holds[node] in (None, strip) and not on_runway(g, node, strip)


def _in_zone(g: TaxiGraph, node: int, strip: str) -> bool:
    if _osm_hold_for(g, node, strip):
        return False
    return strip_distance_m(g, node, strip) < RUNWAY_HOLD_CLEARANCE_M


def _crossing_zones(g: TaxiGraph, nodes: list[int], crossings: list[tuple[int, str]], real: list[bool] | None = None
                    ) -> tuple[list[int | None], list[tuple[int, str, int, int, bool]], int]:
    """Where to wait before entering each path index, and the protected zone of each crossing.

    A node is inside a strip's protected zone when it is closer than RUNWAY_HOLD_CLEARANCE_M to the centreline and is
    not an OSM holding position for that strip (or for no named strip). Around a crossing at index c the zone runs
    backwards to the first node outside it (or further back, to an OSM holding position within HOLD_LOOKBACK edges)
    = the hold index h, and forwards to the first node outside it = ze. Arriving anywhere in (h, ze] waits at h, so
    the aircraft never stops inside the zone; overlapping zones (strips close together) merge onto the earlier hold.
    When the route starts inside the zone, h = 0 and `start_in` is True. On a refined path (`real` marks graph nodes,
    the rest are stop lines) HOLD_LOOKBACK counts graph nodes.
    The route's first stretch inside the zone of the runway it starts on (vacating, up to index `vacate`, the first
    node outside it and off every runway's pavement) is a no-wait stretch too: waits there go to the first node,
    and a crossing zone that starts inside it merges onto index 0.
    Returns (hold per index, [(c, strip, h, ze, start_in)], vacate)."""
    n = len(nodes)
    hold: list[int | None] = [None] + list(range(n - 1))
    zones: list[list] = []
    vacate = 0
    own = runway_strips_at(g, nodes[0]) if n else set()
    if own:
        vacate = 1
        # ... and never ends on any runway's pavement (an exit that runs straight onto a parallel runway)
        while vacate < n - 1 and (any(_in_zone(g, nodes[vacate], st) for st in own)
                                  or any(on_runway(g, nodes[vacate], st) for st in runway_strips(g))):
            vacate += 1
        vacate = min(vacate, n - 1)
        for i in range(1, vacate + 1):
            hold[i] = 0
    k, prev = 0, ((0, vacate) if vacate else None)     # prev = (h, ze) of the last zone
    for node, strip in crossings:
        c = next((i for i in range(k, n) if nodes[i] == node), None)
        if c is None:
            continue
        k = c
        h = max(c - 1, 0)
        while h > 0 and _in_zone(g, nodes[h], strip):
            h -= 1
        osm, seen = None, 0
        for i in range(c - 1, -1, -1):
            if real is not None and not real[i]:
                continue
            seen += 1
            if seen > HOLD_LOOKBACK:
                break
            if _osm_hold_for(g, nodes[i], strip):
                osm = i
                break
        if osm is not None and osm < h:
            h = osm
        ze = c + 1
        while ze < n - 1 and _in_zone(g, nodes[ze], strip):
            ze += 1
        ze = min(ze, n - 1)
        if prev is not None and h < prev[1]:           # starts inside (or before the end of) the previous zone
            h = min(h, prev[0])
        for i in range(h + 1, ze + 1):
            hold[i] = h
        zones.append([c, strip, h, ze, h == 0 and _in_zone(g, nodes[0], strip)])
        prev = (h, ze)
    # a later merge can move a zone's hold back: give every zone the hold its entry index now uses
    for z in zones:
        if z[2] + 1 < n and hold[z[2] + 1] is not None:
            z[2] = hold[z[2] + 1]
            z[4] = z[2] == 0 and _in_zone(g, nodes[0], z[1])
    return hold, [tuple(z) for z in zones], vacate


def schedule(g: TaxiGraph, path: TaxiPath, t0_min: float, res: Reservations, callsign: str,
             horizon_min: float = HORIZON_MIN, headway_s: float = HEADWAY_S) -> Scheduled:
    """Move along the fixed route from `t0_min`, with the stop lines of its edges inserted (`refine`), waiting at the
    last stop point before an index (`_stop_holds`: never in a junction, a queue spacing behind an occupied position,
    at the hold before a runway's protected zone) until (a) the position is clear of reservations by the headway,
    (b) the next edge is not in use in the opposite direction, (c) for a runway crossing, the whole time inside the
    strip's protected zone (from leaving the hold to reaching the first node outside the zone) meets the
    landing/takeoff gap and (d) it neither overtakes nor is overtaken on a step (entry and exit order match every
    other traversal of the same step in the same direction). Waits only grow, so the search is monotone and
    deterministic. A wait on a stop line has the line's position; points on a stop line carry `line: True`.

    It runs to a complete conflict-free schedule; `ok` is False when the total wait exceeds `horizon_min`, and the
    flight's intervals are still added to `res` so later flights give way to it. If the iteration guard trips (no
    convergence), `ok` is False and nothing is reserved, since the times may still conflict.

    If the first node itself is blocked, its arrival is delayed: the aircraft is still upstream and off the graph
    (rolling out on the runway, or at the stand before pushback) during `waits[0]`, which runs from `t0_min` to the
    first point; `points` start at the delayed arrival.

    A route that starts inside a crossed strip's zone: on the runway the aircraft is vacating (the strip whose runway
    edges touch the first node), the dwell at the first node is left to the runway-vacate check (`exit_clear_min`);
    for any other strip the aircraft cannot back out, so if its time in that zone from the first point meets a
    landing/takeoff window the result is `ok=False`."""
    ref = refine(g, path)
    nodes, seg, park, real = ref.nodes, ref.seg, ref.park, ref.real
    n = len(nodes)
    hw = headway_s / 60.0
    hold0, zones, vacate = _crossing_zones(g, nodes, path.crossings, real)
    hold = _stop_holds(g, ref, hold0, zones, vacate)
    nxt_real = [0] * n                     # index of the next graph node after each index (head-on: whole edges)
    j = n - 1
    for i in range(n - 1, -1, -1):
        nxt_real[i] = j
        if real[i]:
            j = i
    # a wait on a stop line lengthens its whole edge: rescan from the edge's first node, so (b) sees it
    prev_real = [0] * n
    for i in range(n):
        prev_real[i] = i if real[i] else prev_real[i - 1]
    vacating = runway_strips_at(g, nodes[0]) if n else set()
    zones_at: dict[int, list[tuple[str, int, int, bool]]] = {}
    for c, strip, h, ze, start_in in zones:
        zones_at.setdefault(c, []).append((strip, h, ze, start_in and strip not in vacating))
    events = {z[1]: sorted(res.runway_events.get(z[1], ())) for z in zones}
    free_arr_last = t0_min + sum(seg)

    arr0_nb = t0_min                       # earliest arrival at the first node
    nb = [-math.inf] * n                   # earliest departure from each node
    # index -> (reason, strip, kind, cs) of the binding wait (-1 = before the first node)
    why: dict[int, tuple] = {}
    arr = [0.0] * n
    dep = [0.0] * n

    def times() -> None:
        arr[0] = max(t0_min, arr0_nb)
        for i in range(n):
            dep[i] = max(arr[i], nb[i]) if i < n - 1 else arr[i] + park
            if i + 1 < n:
                arr[i + 1] = dep[i] + seg[i]

    def push(i: int, target: float, reason: tuple[str, str | None, str | None, str]) -> int:
        """Make the arrival at index i no earlier than `target`; returns the index to rescan from. `reason` is
        (text, strip or None, event kind or None, callsign)."""
        nonlocal arr0_nb
        if i == 0:
            if target > arr0_nb:
                arr0_nb, why[-1] = target, reason
            return 0
        h = hold[i]
        need = target - sum(seg[h:i])
        if need > nb[h]:
            nb[h], why[h] = need, reason
        return h

    def gap_hits(strip: str, t_in: float, t_out: float) -> list[tuple[float, str, str]]:
        return [(ev + GAP_MIN[kind][0] + _EPS, kind, cs) for ev, kind, cs in events[strip]
                if kind in GAP_MIN and t_in <= ev + GAP_MIN[kind][0] + _TOL and t_out >= ev - GAP_MIN[kind][1] - _TOL]

    times()
    converged, i, it = True, 0, 0
    while i < n:
        it += 1
        if it > _MAX_ITER:
            converged = False
            break
        node, restart = nodes[i], None
        # (c) runway crossing gap over the whole time inside the strip's protected zone
        for strip, h, ze, stuck in zones_at.get(i, ()):
            if stuck:                      # starts inside another strip's zone: cannot hold short, judged below
                continue
            hit = gap_hits(strip, dep[h], arr[ze])
            if hit:
                target, kind, cs = max(hit)
                restart = push(h + 1, target + seg[h], (f"hold short {strip} for {kind} {cs}", strip, kind, cs))
                break
        # (a) the node is clear of other reservations by the headway, over the whole dwell
        if restart is None:
            hit = [(e + hw, cs) for s, e, cs in res.node_busy.get(node, ())
                   if s < dep[i] + hw - _TOL and arr[i] < e + hw - _TOL]
            if hit:
                target, cs = max(hit)
                restart = push(i, target, (f"give way to {cs}", None, None, cs))
        # (b) the next edge (graph node to graph node, stop lines and their waits included) is not in use in the
        # opposite direction: wait before entering it
        if restart is None and i + 1 < n and real[i]:
            j = nxt_real[i]
            hit = [(e + seg[i], cs) for s, e, cs in res.edge_dir.get((nodes[j], node), ())
                   if s < arr[j] - _TOL and dep[i] < e - _TOL]
            if hit:
                target, cs = max(hit)
                restart = push(i + 1, target, (f"give way to {cs}", None, None, cs))
        # (d) same direction: no overtaking on the step to the next position
        if restart is None and i + 1 < n:
            a, b = dep[i], arr[i + 1]
            for s_, e, cs in res.edge_dir.get((node, nodes[i + 1]), ()):
                if s_ > a + _TOL and e < b - _TOL:          # it enters after and leaves before: enter after it
                    restart = push(i + 1, s_ + seg[i] + _EPS, (f"give way to {cs}", None, None, cs))
                    break
                if s_ < a - _TOL and e > b + _TOL:          # it enters before and leaves after: leave after it
                    restart = push(i + 1, e + _EPS, (f"give way to {cs}", None, None, cs))
                    break
        if restart is None:
            i += 1
        else:
            times()
            i = prev_real[restart]

    total_wait = arr[-1] - free_arr_last
    ok, fail = True, None
    if not converged:
        ok, fail = False, "hold-short search did not converge"
    else:
        for c in sorted(zones_at):
            for strip, h, ze, stuck in zones_at[c]:
                hit = gap_hits(strip, arr[0], arr[ze]) if stuck else []
                if hit and ok:
                    _, kind, cs = min(hit)
                    ok, fail = False, (f"starts inside the {strip} protected zone during {kind} {cs} "
                                       f"and cannot hold short")
        if ok and total_wait > horizon_min + _TOL:
            ok, fail = False, f"taxi wait {total_wait:.1f} min exceeds the {horizon_min:.0f} min search horizon"

    def pt(t: float, i: int) -> dict:
        node = nodes[i]
        q = {"t_min": t, "lat": g.nodes[node][0], "lon": g.nodes[node][1], "node": node}
        if not real[i]:
            q["line"] = True
        return q

    def wait(node: int, a: float, b: float, reason: tuple) -> dict:
        # contract keys (lat, lon, from_min, to_min, reason) plus structured ones for the rule and the labels:
        # strip (None for a give-way), kind ("landing" | "takeoff" | None) and the callsign waited for
        return {"node": node, "lat": g.nodes[node][0], "lon": g.nodes[node][1], "from_min": a, "to_min": b,
                "reason": reason[0], "strip": reason[1], "kind": reason[2], "for": reason[3]}

    points, waits = [], []
    if arr[0] > t0_min + _TOL:
        waits.append(wait(nodes[0], t0_min, arr[0], why[-1]))
    for i in range(n):
        points.append(pt(arr[i], i))
        if i < n - 1 and dep[i] > arr[i] + _TOL:
            points.append(pt(dep[i], i))
            waits.append(wait(nodes[i], arr[i], dep[i], why[i]))
    points.append(pt(dep[-1], n - 1))
    added = []
    if converged:
        for i in range(n):
            added.append(("node", nodes[i], (arr[i], dep[i], callsign)))
            if i + 1 < n:
                added.append(("edge", (nodes[i], nodes[i + 1]), (dep[i], arr[i + 1], callsign)))
                j = nxt_real[i]
                if real[i] and j != i + 1:                  # the whole edge, for the head-on check (b)
                    added.append(("edge", (nodes[i], nodes[j]), (dep[i], arr[j], callsign)))
        reserve(res, added)
    return Scheduled(points, waits, ok, fail, arr[vacate] if vacate else (dep[0] if n > 1 else arr[0]), tuple(added))


def reserve(res: Reservations, added) -> None:
    """Add `schedule`'s reservations (its `Scheduled.reserved`) to `res`, each list kept sorted."""
    for kind, k, iv in added:
        lst = (res.node_busy if kind == "node" else res.edge_dir).setdefault(k, [])
        lst.append(iv)
        lst.sort()


def verify_separation(tracks: list[GroundTrack], headway_s: float = HEADWAY_S,
                      runway_events: dict[str, list[tuple[float, str, str]]] | None = None,
                      g: TaxiGraph | None = None) -> list[str]:
    """Brute-force check, independent of `schedule`; reads only the track points (and, for the runway check, pure
    geometry lookups on the graph). Returns one message per violation (empty when separated):
    (1) for every pair of tracks, node passages (first to last point at a node) are at least `headway_s` apart;
    (2) for every pair, no two traversals of the same edge in opposite directions overlap in time (an edge runs from
        graph node to graph node: points on stop lines, `line: True`, and the waits on them are inside it);
    (5) for every pair, no overtaking: two passes over the same step (consecutive positions, stop lines included) in
        the same direction leave and arrive in the same order;
    (3) with `runway_events` and `g` (both or neither): for every strip with events, each stretch of a track inside
        the strip's protected zone (closer than RUNWAY_HOLD_CLEARANCE_M to the centreline, OSM holding positions
        excepted) that crosses the strip, from leaving the node before it to reaching the node after it, misses every
        other flight's landing window [ev - 1.5, ev + 1] and takeoff window [ev - 0.25, ev + 1] (GAP_MIN). A stretch
        crosses the
        strip if it holds a node on the pavement (RUNWAY_ON_PAVEMENT_M, between the ends), a node entered and left
        over taxi edges that both carry the strip, or an edge across the centreline; an edge across the centreline
        between two nodes outside the zone counts on its own. Stretches that only run parallel (none of these) are
        not checked. The stretch the track starts on, on the runway it starts on (vacating), counts only from leaving
        the first node and only if it crosses after the first on-pavement run; for any other strip the dwell at the
        first node counts. OSM holding positions on the pavement do not end a zone.
    (4) with `runway_events` and `g`: a track that starts on a runway leaves that runway's zone, and every runway's
        pavement, before another flight's landing or takeoff on it (from its first point; the scheduler leaves this to
        the runway-vacate rule on `exit_clear_min`, so a track can be ok and still be flagged here)."""
    if (runway_events is None) != (g is None):
        raise ValueError("verify_separation: pass runway_events and g together")
    hw = headway_s / 60.0
    visits, trav, steps = [], [], []
    by_node, by_edge, by_step = [], [], []
    for tr in tracks:
        v: list[tuple[int, float, float]] = []
        e: list[tuple[int, int, float, float]] = []
        st: list[tuple[int, int, float, float]] = []
        pts = [q for q in tr.points if q.get("node") is not None]
        for q in pts:
            if v and v[-1][0] == q["node"]:
                v[-1] = (q["node"], v[-1][1], q["t_min"])
            else:
                v.append((q["node"], q["t_min"], q["t_min"]))
        for a, b in zip(pts, pts[1:]):
            if a["node"] != b["node"]:
                st.append((a["node"], b["node"], a["t_min"], b["t_min"]))
        gp = [q for q in pts if not q.get("line")]
        for a, b in zip(gp, gp[1:]):
            if a["node"] != b["node"]:
                e.append((a["node"], b["node"], a["t_min"], b["t_min"]))
        visits.append(v)
        trav.append(e)
        steps.append(st)
        bn: dict = {}
        for x in v:
            bn.setdefault(x[0], []).append(x)
        be: dict = {}
        for x in e:
            be.setdefault((x[0], x[1]), []).append(x)
        bs: dict = {}
        for x in st:
            bs.setdefault((x[0], x[1]), []).append(x)
        by_node.append(bn)
        by_edge.append(be)
        by_step.append(bs)
    out: list[str] = []
    for i in range(len(tracks)):
        for j in range(i + 1, len(tracks)):
            ca, cb = tracks[i].callsign, tracks[j].callsign
            for na, a0, a1 in visits[i]:
                for _, b0, b1 in by_node[j].get(na, ()):
                    if a0 < b1 + hw - _TOL and b0 < a1 + hw - _TOL:
                        out.append(f"{ca} and {cb} at node {na} within {headway_s:.0f} s "
                                   f"({a0:.2f}-{a1:.2f} vs {b0:.2f}-{b1:.2f} min)")
            for u, v_, s0, s1 in trav[i]:
                for _, _, r0, r1 in by_edge[j].get((v_, u), ()):
                    if s0 < r1 - _TOL and r0 < s1 - _TOL:
                        out.append(f"{ca} and {cb} on edge {u}-{v_} in opposite directions "
                                   f"({s0:.2f}-{s1:.2f} vs {r0:.2f}-{r1:.2f} min)")
            for u, v_, s0, s1 in steps[i]:
                for _, _, r0, r1 in by_step[j].get((u, v_), ()):
                    if (s0 < r0 - _TOL and s1 > r1 + _TOL) or (r0 < s0 - _TOL and r1 > s1 + _TOL):
                        out.append(f"{ca} and {cb} overtake on {u}-{v_} "
                                   f"({s0:.2f}-{s1:.2f} vs {r0:.2f}-{r1:.2f} min)")
    if not runway_events:
        return out
    window = {k: (b, a) for k, (a, b) in GAP_MIN.items()}      # (before, after) the event, minutes
    strips_g = set(runway_strips(g))     # strips with runway edges in the graph (cached on g)

    def tagged(u: int, v: int, strip: str) -> bool:
        return any(w == v and kind != "runway" and st == strip for w, _, kind, _, st in g.adj.get(u, ()))

    for tr, vis in zip(tracks, visits):
        if not vis:
            continue
        nv = len(vis)
        first_rw = {name for _, _, kind, name, _ in g.adj.get(vis[0][0], ()) if kind == "runway" and name}
        if first_rw:
            # (4) vacating: out of the zone of the runway it starts on, and off every runway's pavement, before another
            # flight lands or takes off on that runway (the check T31 makes with exit_clear_min)
            all_rw = strips_g
            x = 0
            while x < nv - 1 and (any(not (vis[x][0] in g.holds and g.holds[vis[x][0]] in (None, st)
                                               and not on_runway(g, vis[x][0], st))
                                          and strip_distance_m(g, vis[x][0], st) < RUNWAY_HOLD_CLEARANCE_M
                                          for st in first_rw)
                                      or any(on_runway(g, vis[x][0], st) for st in all_rw)):
                x += 1
            t_a, t_b = vis[0][1], vis[x][1]
            for strip in sorted(first_rw):
                for ev, kind, cs in sorted(runway_events.get(strip, ())):
                    if cs != tr.callsign and kind in window and t_a - _TOL <= ev < t_b - _TOL:
                        out.append(f"{tr.callsign} still vacating {strip} ({t_a:.2f}-{t_b:.2f} min) at {kind} "
                                   f"{cs} at {ev:.2f}")
        for strip in sorted(runway_events):
            evs = [(ev, kind, cs) for ev, kind, cs in sorted(runway_events[strip]) if cs != tr.callsign
                   and kind in window]
            if not evs or strip not in strips_g:
                continue
            # an OSM hold ends the zone only when it is off the pavement (some were snapped onto runway nodes)
            zone = [not (nd in g.holds and g.holds[nd] in (None, strip) and not on_runway(g, nd, strip))
                    and strip_distance_m(g, nd, strip) < RUNWAY_HOLD_CLEARANCE_M for nd, _, _ in vis]
            pave = [on_runway(g, nd, strip) for nd, _, _ in vis]
            if strip in first_rw:          # the first on-pavement run of the runway it starts on is vacating
                x = 0
                while x < nv and pave[x]:
                    pave[x] = False
                    x += 1
            mark = [pave[x] or (0 < x < nv - 1 and tagged(vis[x - 1][0], vis[x][0], strip)
                                and tagged(vis[x][0], vis[x + 1][0], strip)) for x in range(nv)]
            across = [edge_crosses_strip(g, vis[x][0], vis[x + 1][0], strip) for x in range(nv - 1)]
            spans: list[tuple[float, float]] = []
            k = 0
            while k < nv:
                if not zone[k]:
                    k += 1
                    continue
                m = k
                while m + 1 < nv and zone[m + 1]:
                    m += 1
                if any(mark[k:m + 1]) or any(across[x] for x in range(max(k - 1, 0), min(m + 1, nv - 1))):
                    if k > 0:
                        t_in = vis[k - 1][2]
                    else:
                        t_in = vis[0][2] if strip in first_rw else vis[0][1]
                    t_out = vis[m + 1][1] if m + 1 < nv else vis[m][2]
                    spans.append((t_in, t_out))
                k = m + 1
            for x in range(nv - 1):
                if across[x] and not zone[x] and not zone[x + 1]:
                    spans.append((vis[x][2], vis[x + 1][1]))
            for t_in, t_out in spans:
                for ev, kind, cs in evs:
                    before, after = window[kind]
                    if t_in < ev + after + _TOL and ev - before - _TOL < t_out:
                        out.append(f"{tr.callsign} inside the {strip} protected zone {t_in:.2f}-{t_out:.2f} "
                                   f"min during {kind} {cs} at {ev:.2f}")
    return out


# --- wave integration (spec §6.4-6.6, §6.9): gate binding, per-flight taxi tiers, the per-wave taxi table ---
#
# Tiers per flight (ATC-TAXI-ROUTE evidence `tier`):
#   real    - the wave's surface tier is real (`wave_tier`), the flight's runway end is in the OSM exits and its gate
#             is bound to an OSM stand: exit by `choose_exit`, ROT, and the fastest taxi path (`unimpeded`) from that
#             exit (or, if the stand is not reachable from it, the next exit of the end that reaches it). No exit
#             reaching the stand is a rule failure; the metrics then use the generic estimate (`src: "synth"`).
#   generic - the end is missing from OSM (e.g. ORD 14L/32R/14R, open in 2015), the wave tier is generic, or the gate
#             is `synth`: a generic exit (the required roll, right-angle exit at 12 kt) and `generic_taxi_min`.
#   none    - no runway coordinates (or a gate with no coordinates): v2's near/far `utility.taxi_min`, no ROT.

SRC_ROUTED = "OpenStreetMap surface (ODbL), routed"
SRC_ROUTED_XPLANE = "X-Plane Scenery Gateway surface (GPL), routed"
XPLANE = "X-Plane Scenery Gateway"


def source_label(header: dict | None) -> str:
    """The surface's source as the UI and provenance name it: "OpenStreetMap 2026-11-02" or
    "X-Plane Scenery Gateway (scenery 99761)"."""
    h = header or {}
    if h.get("source") == XPLANE:
        return f"{XPLANE} (scenery {h['gateway_scenery']})" if h.get("gateway_scenery") else XPLANE
    ts = str(h.get("osm_timestamp") or "")[:10]
    return f"OpenStreetMap {ts or 'snapshot'}"
SRC_GENERIC = "generic surface (straight line x1.4 at 15 kt)"
SRC_NO_GEOMETRY = "ops rule (no geometry)"
TIER_REACH = 0.80             # quality gate: each open end in OSM reaches >= 80 % of the wave's bound stands
WAVE_CACHE_MAX = 32           # waves kept in the per-wave tier / taxi-table caches (FIFO; = build_wave's LRU)
_PIN = "_surface"             # wave.__dict__ slot: (wave_key, GateGeo) pinned at build time (not a dataclass field)


@dataclass
class GateGeo:
    """The wave's surface inputs, fixed at build time: the airport's taxi graph (None without an OSM surface) and the
    gate -> stand binding (`gates_geo.bind_gates`). Shared and read-only (deepcopy returns the same object)."""
    icao: str
    graph: TaxiGraph | None
    bind: dict[str, dict]
    stand_label: dict[str, str]
    source: str | None              # `source_label` of the surface document

    def __deepcopy__(self, memo):
        return self


@dataclass
class TaxiInfo:
    """Unimpeded taxi-in of one (runway end, gate, size): the plan-independent part of a ground track."""
    tier: Literal["real", "generic", "none"]
    runway: str | None
    gate: str | None
    stand: str
    exit_label: str | None
    exit: dict | None
    rot_s: float | None           # None in the no-geometry tier
    taxi_min: float               # unimpeded taxi (incl. 30 s to park), without the ROT
    src: Literal["osm", "synth", "none"]
    path: TaxiPath | None = None
    no_path: str | None = None    # real tier: no exit of the end reaches the stand (ATC-TAXI-ROUTE fails)
    line: tuple | None = None     # generic tier: ((lat, lon) runway midpoint, (lat, lon) gate)
    routed_on: str = "OpenStreetMap"   # TaxiGraph.source of the routed path

    @property
    def total_min(self) -> float:
        """ROT + taxi: exit-to-gate time after touchdown."""
        return (self.rot_s or 0.0) / 60.0 + self.taxi_min

    @property
    def source(self) -> str:
        if self.src == "osm":
            return SRC_ROUTED_XPLANE if self.routed_on == XPLANE else SRC_ROUTED
        return SRC_NO_GEOMETRY if self.tier == "none" else SRC_GENERIC


_LOCK = threading.Lock()
_GEO: dict[tuple, GateGeo] = {}
_TIER: dict[str, str] = {}
_TABLE: dict[str, dict[tuple, TaxiInfo]] = {}
_EMPTY = GateGeo("", None, {}, {}, None)


def _put(cache: dict, k, v) -> None:
    with _LOCK:
        while len(cache) >= WAVE_CACHE_MAX:
            cache.pop(next(iter(cache)), None)
        cache[k] = v


def stand_labels(stands: list[dict], bound: set[str]) -> dict[str, str]:
    """OSM stand id -> unique label: the stand's `ref` (else its id). A ref shared by several stands labels one owner
    (the lowest-id gate-bound stand, else the lowest id); every other stand of that ref is `ref/osm-id`. Used by both the
    ground tracks' `stand` and the drawn stands (`wavegeo`), so they always agree."""
    sts = sorted(stands, key=lambda x: str(x["id"]))
    owner: dict[str, str] = {}
    for st in sts:
        sid, lab = str(st["id"]), str(st.get("ref") or st["id"])
        if lab not in owner or (sid in bound and owner[lab] not in bound):
            owner[lab] = sid
    return {str(st["id"]): (str(st.get("ref") or st["id"]) if owner[str(st.get("ref") or st["id"])] == str(st["id"])
                            else f"{st.get('ref') or st['id']}/{st['id']}") for st in sts}


def gate_geo(icao: str, gates: list[tuple[str, bool]]) -> GateGeo:
    """Bind the gate ids (id, remote) to OSM stands of the airport's surface. Reads the store: call at build time
    (cached per airport and gate set)."""
    key = (icao, tuple(gates))
    hit = _GEO.get(key)
    if hit is not None:
        return hit
    from backend.geo.gates_geo import bind_gates
    doc = load_surface(icao)
    if doc is None:
        geo = GateGeo(icao, None, {}, {}, None)
    else:
        b = bind_gates(list(gates), doc)
        names = stand_labels(doc.get("stands") or [], {str(x["stand"]) for x in b.values() if x.get("geo_src") != "synth"})
        labels = {gid: names.get(x["stand"], x["stand"]) for gid, x in b.items()}
        geo = GateGeo(icao, load_graph(icao), b, labels, source_label(doc.get("header")))
    _put(_GEO, key, geo)
    return geo


def apply_gate_geo(geo: GateGeo, gates: list, ref_latlon: tuple[float, float] | None) -> None:
    """Set `lat/lon/geo_src` on the wave's Gate objects (build time, before the wave key exists). Without an OSM
    surface every gate sits at the airport reference point (`synth`); without that either, gates stay unplaced."""
    for g in gates:
        b = geo.bind.get(g.id)
        if b is not None:
            g.lat, g.lon, g.geo_src = float(b["lat"]), float(b["lon"]), b["geo_src"]
        elif ref_latlon is not None and tuple(ref_latlon) != (0.0, 0.0):
            g.lat, g.lon, g.geo_src = float(ref_latlon[0]), float(ref_latlon[1]), "synth"


def _geo(wave) -> GateGeo:
    """The pinned GateGeo when it belongs to the wave's content key; else (an unpinned variant) bind again (reads
    the store, cached); a wave whose gates carry no coordinates has no surface inputs."""
    from backend.wave.route import wave_key
    pin = wave.__dict__.get(_PIN)
    if pin is not None and pin[0] == wave_key(wave):
        return pin[1]
    if not any(getattr(g, "geo_src", None) for g in wave.gates):
        return _EMPTY
    return gate_geo(wave.airport.icao, [(g.id, g.remote) for g in wave.gates])


def _bound_nodes(bind: dict[str, dict]) -> list[int]:
    return [int(b["node"]) for b in bind.values() if b.get("geo_src") != "synth" and int(b.get("node", -1)) >= 0]


def _tier(g: TaxiGraph | None, runways, bind: dict[str, dict]) -> Literal["real", "generic"]:
    """Spec §6.9 quality gate: real iff every open runway end present in `g.exits` reaches >= 80 % of the bound
    stands (gates not `synth`). Reachable = an exit node of the end shares a taxi component (non-runway edges, the
    edges the router uses) with the stand. No graph, no bound stand or no open end in OSM: generic."""
    if g is None:
        return "generic"
    stands = _bound_nodes(bind)
    ends = [r.id for r in runways if r.open and r.id in g.exits]
    if not stands or not ends:
        return "generic"
    g.stand_components()
    comp = g._trees["comp"]
    for end in ends:
        reach = {comp.get(e["node"]) for e in g.exits[end]}
        if sum(1 for s in stands if comp.get(s) in reach) < TIER_REACH * len(stands) - 1e-9:
            return "generic"
    return "real"


def wave_tier(wave, g: TaxiGraph | None) -> Literal["real", "generic"]:
    """The wave's surface tier (spec §6.9), memoised on the wave key (`wave_tier.cache_clear()` also drops the taxi
    table, which depends on it). `g` (kept for the T24 interface) must be the wave's graph, `_geo(wave).graph`:
    it is what the gate binding refers to; the memo is keyed on the wave only."""
    from backend.wave.route import wave_key
    k = wave_key(wave)
    hit = _TIER.get(k)
    if hit is None:
        hit = _tier(g, wave.airport.runways, _geo(wave).bind)
        _put(_TIER, k, hit)
    return hit


def stand_node_for(wave, gate_id: str | None) -> int | None:
    """Taxi-graph node of the OSM stand the gate is bound to; None for an unbound or `synth` gate."""
    b = _geo(wave).bind.get(gate_id or "")
    if b is None or b.get("geo_src") == "synth" or int(b.get("node", -1)) < 0:
        return None
    return int(b["node"])


def final_gs_kt(wave, runway_end: str) -> float:
    """Touchdown speed for the ROT: 140 kt final minus the METAR headwind on that runway (as the route's final)."""
    from backend.wave.route import IAS_FINAL, MIN_GS_KT
    rw = next((x for x in wave.runway_wind if x.runway == runway_end), None)
    hw = (rw.headwind_kt - rw.tailwind_kt) if rw is not None else 0.0
    return max(IAS_FINAL - hw, MIN_GS_KT)


def _compute(wave, geo: GateGeo, tier_w: str, runway_end: str | None, gate_id: str | None,
             size: Literal["narrow", "wide"]) -> TaxiInfo:
    """One TaxiInfo (see the tiers above). `wave` only needs airport, runway_wind, metar and gates, so the builders
    can call this before the wave's flights exist (crew floors)."""
    rw = next((r for r in wave.airport.runways if r.id == runway_end), None) if runway_end else None
    gate = next((x for x in wave.gates if x.id == gate_id), None) if gate_id else None
    label = geo.stand_label.get(gate_id or "", "synth" if gate is not None else str(gate_id))
    if (rw is None or None in (rw.le_lat, rw.le_lon, rw.he_lat, rw.he_lon) or gate is None
            or gate.lat is None or gate.lon is None):
        from backend.wave import utility   # lazy: utility -> trajectory -> surface
        return TaxiInfo("none", runway_end, gate_id, label, None, None, None,
                        utility.taxi_min(wave, runway_end, gate_id), "none")
    g, b = geo.graph, geo.bind.get(gate.id)
    wet = wave.metar is not None and wave.metar.surface == "wet"
    v_td = final_gs_kt(wave, rw.id)
    need = ROLL_M[size] * (WET_FACTOR if wet else 1.0)
    generic_exit = {"node": None, "dist_m": need, "angle_deg": 90.0, "generic": True}
    mid = ((rw.le_lat + rw.he_lat) / 2.0, (rw.le_lon + rw.he_lon) / 2.0)
    line = (mid, (gate.lat, gate.lon))
    if (g is not None and tier_w == "real" and rw.id in g.exits and b is not None and b.get("geo_src") != "synth"
            and int(b.get("node", -1)) >= 0):
        first = choose_exit(g, rw.id, size, wet)
        seen, cands = set(), []
        # fallback order as choose_exit ranks exits: forward and past the required roll, then forward, then any
        ex = list(g.exits[rw.id])
        fwd = [e for e in ex if e["angle_deg"] <= BACKWARD_DEG]
        for e in [first] + [e for e in fwd if e["dist_m"] >= need] + fwd + ex:
            if e["node"] not in seen:
                seen.add(e["node"])
                cands.append(e)
        for e in cands:
            try:
                p = unimpeded(g, e["node"], int(b["node"]))   # module global: a monkeypatched surface.unimpeded applies
            except NoPath:
                continue
            name = next((n for n in p.names if n), None)
            return TaxiInfo("real", rw.id, gate.id, label, name or f"{rw.id} exit {e['dist_m']:.0f} m", e,
                            rot_s(e, v_td), p.unimpeded_min, "osm", p, routed_on=getattr(g, "source", "OpenStreetMap"))
        return TaxiInfo("real", rw.id, gate.id, label, None, first, rot_s(first, v_td),
                        generic_taxi_min(rw, gate.lat, gate.lon), "synth",
                        no_path=f"no taxi route from any exit of {rw.id} to gate {gate.id} (stand {label}) on the "
                                f"OSM surface", line=line)
    return TaxiInfo("generic", rw.id, gate.id, label, None, generic_exit, rot_s(generic_exit, v_td),
                    generic_taxi_min(rw, gate.lat, gate.lon), "synth", line=line)


def taxi_info(wave, runway_end: str | None, gate_id: str | None, size: Literal["narrow", "wide"]) -> TaxiInfo:
    """Memoised per wave key in the per-wave taxi table (filled at build time by `precompute`, so the hot path only
    reads it). Never reads the store for a wave built by the builders (its graph is pinned)."""
    from backend.wave.route import wave_key
    k = wave_key(wave)
    tab = _TABLE.get(k)
    if tab is None:
        tab = {}
        _put(_TABLE, k, tab)
    key = (runway_end, gate_id, size)
    hit = tab.get(key)
    if hit is None:
        geo = _geo(wave)
        hit = tab[key] = _compute(wave, geo, wave_tier(wave, geo.graph), runway_end, gate_id, size)
    return hit


def min_unimpeded_taxi(wave, callsign: str, runway_end: str, gate_id: str | None = None) -> float:
    """ROT + unimpeded taxi (minutes) from `runway_end` to the flight's gate (the scheduled gate unless `gate_id` is
    given); the generic estimate when the end is missing from OSM, v2's near/far taxi without runway geometry."""
    f = wave.flight(callsign)
    return taxi_info(wave, runway_end, gate_id or f.scheduled_gate, f.gate_size).total_min


def floor_taxi(wave, geo: GateGeo, gate_id: str, size: Literal["narrow", "wide"]) -> float | None:
    """Build-time crew floor term: the minimum over open runway ends of ROT + unimpeded taxi to `gate_id` (None
    without an open runway). `wave` may be a flight-less proto wave (airport, runway_wind, metar, gates)."""
    tier_w = _tier(geo.graph, wave.airport.runways, geo.bind)
    vals = [_compute(wave, geo, tier_w, r.id, gate_id, size).total_min for r in wave.airport.runways if r.open]
    return min(vals) if vals else None


def provenance(wave, geo: GateGeo):
    """Provenance layer `surface`: real / generic per the wave's quality gate (spec §6.9)."""
    from backend.wave.model import Provenance
    if geo.graph is None:
        return Provenance("surface", "generic", "no OpenStreetMap surface",
                          "taxi = straight line runway midpoint -> gate x1.4 at 15 kt; gates at the airport "
                          "reference point (synth)")
    g = geo.graph
    tier = _tier(g, wave.airport.runways, geo.bind)
    open_ends = [r.id for r in wave.airport.runways if r.open]
    missing = [e for e in open_ends if e not in g.exits]
    bound = len(_bound_nodes(geo.bind))
    src = "X-Plane" if g.source == XPLANE else "OSM"
    det = (f"{tier} quality gate: every open runway end in {src} reaches >= {TIER_REACH:.0%} of the wave's "
           f"{bound} {src}-bound stands" if tier == "real" else
           f"generic: an open runway end in {src} reaches < {TIER_REACH:.0%} of the wave's {bound} {src}-bound stands "
           f"(or none is bound); taxi is the straight-line estimate")
    if missing:
        det += f"; open ends missing from today's {src} ({', '.join(missing)}) use the straight-line estimate"
    return Provenance("surface", tier, geo.source or "OpenStreetMap snapshot", det)


def precompute(wave) -> None:
    """Pin the wave's GateGeo on the object (as `route.precompute` pins routes) and fill its taxi table for every
    open runway end x gate x the wave's aircraft sizes, so metrics, rules, moves and views only read dicts. Called by
    every wave builder after `route.precompute`."""
    from backend.wave.route import wave_key
    geo = _geo(wave)
    wave.__dict__[_PIN] = (wave_key(wave), geo)
    sizes = sorted({f.gate_size for f in wave.flights})
    for r in wave.airport.runways:
        if r.open:
            for size in sizes:
                for gt in wave.gates:
                    taxi_info(wave, r.id, gt.id, size)


def _clear() -> None:
    with _LOCK:
        _TIER.clear()
        _TABLE.clear()


wave_tier.cache_clear = _clear
taxi_info.cache_clear = _clear


def cache_clear() -> None:
    """Drop every surface cache (gate bindings, tiers, taxi tables, the global tree FIFO and the trees it holds);
    for `build._reset_caches`. Pinned GateGeo objects on live waves stay valid (their trees rebuild lazily)."""
    _clear()
    with _LOCK:
        _GEO.clear()
    with _TREE_LOCK:
        for og, ok in _TREE_ORDER:
            og._trees.pop(ok, None)
        _TREE_ORDER.clear()
    load_graph.cache_clear()


def arrival_track(wave, callsign: str, info: TaxiInfo, slot_min: float) -> GroundTrack | None:
    """The unimpeded taxi-in of a wave flight landing at `slot_min` (no waits: hold-short scheduling is
    ATC-TAXI-CONFLICT's): it leaves the runway at slot + ROT and parks at slot + ROT + taxi. Real tier: one point per
    route node (`src: "osm"`), the last one repeated after the 30 s to park; generic: a straight line from the runway
    midpoint to the gate (`src: "synth"`); None in the no-geometry tier (nothing is drawn)."""
    if info.tier == "none" or info.rot_s is None:
        return None
    t0 = slot_min + info.rot_s / 60.0
    names: list[str] = []
    if info.path is not None:
        g = _geo(wave).graph
        p = info.path
        pts, t = [], t0
        for i, n in enumerate(p.nodes):
            pts.append({"t_min": t, "lat": g.nodes[n][0], "lon": g.nodes[n][1], "node": n})
            if i < len(p.nodes) - 1:
                t += p.seg_s[i] / 60.0
        pts.append({"t_min": t0 + p.unimpeded_min, "lat": g.nodes[p.nodes[-1]][0], "lon": g.nodes[p.nodes[-1]][1],
                    "node": p.nodes[-1]})
        for nm in p.names:
            if nm and (not names or names[-1] != nm):
                names.append(nm)
    else:
        (la0, lo0), (la1, lo1) = info.line
        pts = [{"t_min": t0, "lat": la0, "lon": lo0, "node": None},
               {"t_min": t0 + info.taxi_min, "lat": la1, "lon": lo1, "node": None}]
    return GroundTrack(callsign, "arrival", True, info.exit_label, info.stand, names, pts, [], info.taxi_min,
                       info.taxi_min, info.src)
