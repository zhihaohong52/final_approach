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
    return TaxiGraph(icao, nodes, adj, exits, stands, reach)


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
