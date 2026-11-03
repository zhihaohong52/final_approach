"""Runtime taxi graph over an OSM surface document: runway exit choice, runway occupancy time (ROT) and
unimpeded taxi routes. Pure and deterministic; the model never decides routes. Built at wave-build time only
(rules, utility and negotiation never call the store).
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from atm.geo.store import load_surface

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
    _trees: dict = field(default_factory=dict, init=False, repr=False, compare=False)

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
    return TaxiGraph(icao, nodes, adj, exits, stands, reach, holds)


@lru_cache(maxsize=4)
def load_graph(icao: str) -> TaxiGraph | None:
    doc = load_surface(icao)
    return None if doc is None else _from_doc(icao, doc)


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
    start = (root, -1)
    dist = {start: 0.0}
    parent[start] = None
    heap = [(0.0, root, -1)]
    while heap:
        t, u, p = heapq.heappop(heap)
        st = (u, p)
        if st in done:
            continue
        done.add(st)
        if u not in best:
            best[u] = (t, st)
        for v, length, kind, name, strip in g.adj.get(u, ()):
            if kind == "runway":
                continue
            slow = kind == "taxilane" or (p >= 0 and _heading_turn(g, p, u, v) > TURN_SLOW_DEG)
            nt = t + length / ((SLOW_KT if slow else TAXI_KT) * KT)
            ns = (v, u)
            if ns in done or nt >= dist.get(ns, math.inf):
                continue
            dist[ns] = nt
            parent[ns] = st
            via[ns] = (name, strip)
            heapq.heappush(heap, (nt, v, u))
    out = {"best": best, "parent": parent, "via": via, "dist": dist}
    g._trees[key] = out
    return out


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


# --- ground traffic: hold-short scheduling (single-path safe-interval search) ---

HEADWAY_S = 30.0              # node headway (assumption)
HORIZON_MIN = 30.0            # search horizon (assumption)
GAP_MIN = {"landing": (1.0, 1.5), "takeoff": (1.0, 1.0)}   # crossing blocked if the event is in [t - a, t + b] min
_TOL = 1e-9
_EPS = 1e-6                   # release just after a closed crossing window
HOLD_LOOKBACK = 3             # edges before a crossing within which an OSM holding position is used


@dataclass
class Reservations:
    """Ground reservation table, times in minutes. `node_busy[n]` and `edge_dir[(u, v)]` hold `(from, to, callsign)`
    intervals (an edge entry is a traversal from u to v); `runway_events[strip]` holds `(t, "landing"|"takeoff", cs)`."""
    node_busy: dict[int, list[tuple[float, float, str]]]
    edge_dir: dict[tuple[int, int], list[tuple[float, float, str]]]
    runway_events: dict[str, list[tuple[float, str, str]]]


@dataclass
class Scheduled:
    points: list[dict]            # {t_min, lat, lon, node}: arrival at each node, departure after a wait, then parked
    waits: list[dict]             # {node, lat, lon, from_min, to_min, reason}
    ok: bool
    reason: str | None
    exit_clear_min: float         # time the aircraft leaves the first node (the runway exit for arrivals)


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


def _hold_index(g: TaxiGraph, nodes: list[int], crossings: list[tuple[int, str]]) -> list[int | None]:
    """For each path index, where to wait before entering it: the previous node, except that from the OSM holding
    position up to the node after a runway crossing the aircraft waits at the holding position (or, without one,
    the node before the strip), so it never stops on the runway."""
    hold: list[int | None] = [None] + list(range(len(nodes) - 1))
    lo, k, prev_h = 0, 1, None
    for node, strip in crossings:
        c = next((i for i in range(max(k, 1), len(nodes)) if nodes[i] == node), None)
        if c is None:
            continue
        if c - 1 < lo:
            # adjacent strips (no node between the two centrelines): cross both as one block from the earlier hold
            h = prev_h if prev_h is not None else c - 1
        else:
            # an OSM holding position at most HOLD_LOOKBACK edges before the strip, else the node before it
            h = next((i for i in range(c - 1, max(lo, c - HOLD_LOOKBACK) - 1, -1)
                      if nodes[i] in g.holds and g.holds[nodes[i]] in (None, strip)), c - 1)
        prev_h = h
        for i in range(h + 1, min(c + 2, len(nodes))):
            hold[i] = h
        lo, k = c + 1, c + 1
    return hold


def schedule(g: TaxiGraph, path: TaxiPath, t0_min: float, res: Reservations, callsign: str,
             horizon_min: float = HORIZON_MIN, headway_s: float = HEADWAY_S) -> Scheduled:
    """Move along the fixed route from `t0_min`, waiting (at the previous node, or at the OSM holding position before
    a runway) until (a) the node is clear of reservations by the headway, (b) the next edge is not in use in the
    opposite direction and (c) a runway crossing meets the landing/takeoff gap. Waits only grow, so the search is
    monotone and deterministic. On success the flight's own intervals are added to `res`."""
    nodes = path.nodes
    n = len(nodes)
    seg = [s / 60.0 for s in path.seg_s[:n - 1]]
    park = path.seg_s[n - 1] / 60.0 if len(path.seg_s) >= n else 0.0
    hw = headway_s / 60.0
    hold = _hold_index(g, nodes, path.crossings)
    cross_at: dict[int, str] = {}
    k = 1
    for node, strip in path.crossings:
        c = next((i for i in range(k, n) if nodes[i] == node), None)
        if c is not None:
            cross_at[c], k = strip, c + 1
    events = {strip: sorted(res.runway_events.get(strip, ())) for strip in set(cross_at.values())}
    free_arr_last = t0_min + sum(seg)

    arr0_nb = t0_min                       # earliest arrival at the first node
    nb = [-math.inf] * n                   # earliest departure from each node
    why: dict[int, str] = {}               # index -> reason of the binding wait there (-1 = before the first node)
    arr = [0.0] * n
    dep = [0.0] * n

    def times() -> None:
        arr[0] = max(t0_min, arr0_nb)
        for i in range(n):
            dep[i] = max(arr[i], nb[i]) if i < n - 1 else arr[i] + park
            if i + 1 < n:
                arr[i + 1] = dep[i] + seg[i]

    def push(i: int, target: float, reason: str) -> int:
        """Make the arrival at index i no earlier than `target`; returns the index to rescan from."""
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

    times()
    ok, fail, i, guard = True, None, 0, 0
    while i < n:
        guard += 1
        if arr[-1] - free_arr_last > horizon_min + _TOL or guard > 100000:
            ok = False
            fail = (f"taxi wait {arr[-1] - free_arr_last:.1f} min exceeds the {horizon_min:.0f} min search horizon"
                    if guard <= 100000 else "hold-short search did not converge")
            break
        node, restart = nodes[i], None
        # (c) runway crossing gap at the strip's centreline node
        if i in cross_at:
            t, strip = arr[i], cross_at[i]
            hit = [(ev + GAP_MIN[kind][0] + _EPS, kind, cs) for ev, kind, cs in events[strip]
                   if kind in GAP_MIN and t - GAP_MIN[kind][0] - _TOL <= ev <= t + GAP_MIN[kind][1] + _TOL]
            if hit:
                target, kind, cs = max(hit)
                restart = push(i, target, f"hold short {strip} for {kind} {cs}")
        # (a) the node is clear of other reservations by the headway, over the whole dwell
        if restart is None:
            hit = [(e + hw, cs) for s, e, cs in res.node_busy.get(node, ())
                   if s < dep[i] + hw - _TOL and arr[i] < e + hw - _TOL]
            if hit:
                target, cs = max(hit)
                restart = push(i, target, f"give way to {cs}")
        # (b) the next edge is not in use in the opposite direction during the traversal
        if restart is None and i + 1 < n:
            hit = [(e + seg[i], cs) for s, e, cs in res.edge_dir.get((nodes[i + 1], node), ())
                   if s < arr[i + 1] - _TOL and dep[i] < e - _TOL]
            if hit:
                target, cs = max(hit)
                restart = push(i + 1, target, f"give way to {cs}")
        if restart is None:
            i += 1
        else:
            times()
            i = restart

    def pt(t: float, node: int) -> dict:
        return {"t_min": t, "lat": g.nodes[node][0], "lon": g.nodes[node][1], "node": node}

    def wait(node: int, a: float, b: float, reason: str) -> dict:
        return {"node": node, "lat": g.nodes[node][0], "lon": g.nodes[node][1], "from_min": a, "to_min": b,
                "reason": reason}

    points, waits = [], []
    if arr[0] > t0_min + _TOL:
        waits.append(wait(nodes[0], t0_min, arr[0], why[-1]))
    for i in range(n):
        points.append(pt(arr[i], nodes[i]))
        if i < n - 1 and dep[i] > arr[i] + _TOL:
            points.append(pt(dep[i], nodes[i]))
            waits.append(wait(nodes[i], arr[i], dep[i], why[i]))
    points.append(pt(dep[-1], nodes[-1]))
    if ok:
        for i in range(n):
            lst = res.node_busy.setdefault(nodes[i], [])
            lst.append((arr[i], dep[i], callsign))
            lst.sort()
            if i + 1 < n:
                lst = res.edge_dir.setdefault((nodes[i], nodes[i + 1]), [])
                lst.append((dep[i], arr[i + 1], callsign))
                lst.sort()
    return Scheduled(points, waits, ok, fail, dep[0] if n > 1 else arr[0])


def verify_separation(tracks: list[GroundTrack], headway_s: float = HEADWAY_S) -> list[str]:
    """Brute-force check, independent of `schedule`: for every pair of tracks, (1) node passages (first to last point
    at a node) are at least `headway_s` apart and (2) sampled every 1 s, the two are never on the same taxi edge in
    opposite directions. Reads only the track points; returns one message per violation (empty when separated)."""
    hw = headway_s / 60.0
    visits, edges = [], []
    for tr in tracks:
        v: list[tuple[int, float, float]] = []
        e: dict[int, tuple[int, int]] = {}                 # second -> (from node, to node)
        pts = [q for q in tr.points if q.get("node") is not None]
        for q in pts:
            if v and v[-1][0] == q["node"]:
                v[-1] = (q["node"], v[-1][1], q["t_min"])
            else:
                v.append((q["node"], q["t_min"], q["t_min"]))
        for a, b in zip(pts, pts[1:]):
            if a["node"] == b["node"]:
                continue
            for sec in range(math.floor(a["t_min"] * 60.0) + 1, math.ceil(b["t_min"] * 60.0)):
                if a["t_min"] * 60.0 < sec < b["t_min"] * 60.0:
                    e[sec] = (a["node"], b["node"])
        visits.append(v)
        edges.append(e)
    out: list[str] = []
    for i in range(len(tracks)):
        for j in range(i + 1, len(tracks)):
            ca, cb = tracks[i].callsign, tracks[j].callsign
            for na, a0, a1 in visits[i]:
                for nb_, b0, b1 in visits[j]:
                    if na == nb_ and a0 < b1 + hw - _TOL and b0 < a1 + hw - _TOL:
                        out.append(f"{ca} and {cb} at node {na} within {headway_s:.0f} s "
                                   f"({a0:.2f}-{a1:.2f} vs {b0:.2f}-{b1:.2f} min)")
            seen = set()
            for sec in sorted(edges[i].keys() & edges[j].keys()):
                u, v = edges[i][sec]
                if edges[j][sec] == (v, u) and (u, v) not in seen:
                    seen.add((u, v))
                    out.append(f"{ca} and {cb} on edge {u}-{v} in opposite directions at {sec / 60.0:.2f} min")
    return out
