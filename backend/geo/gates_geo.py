"""Bind the challenge's gate ids to real OSM stands of an airport surface document (spec section 5, steps 1-4).

Pure and deterministic: no network, no store. Models never decide this; the order below does.
Duplicate stand refs (the same ref in several terminals) resolve to the stand nearest an OSM gate node with that
ref, otherwise the first by stand id.
"""
from __future__ import annotations

import math
import re
from collections import deque

BIND_RADIUS_M = 150.0
_EARTH_R = 6371000.0
_ID = re.compile(r"^([A-Za-z]*)0*([0-9A-Za-z]*)$")
_GENERIC = re.compile(r"^([A-Za-z]+)(\d+)$")


def _split(ref: str) -> tuple[str, str]:
    """('B', '12') for 'B12' / 'b012'; ('', '12') for '12'."""
    m = _ID.match(ref.strip())
    return (m.group(1).upper(), m.group(2).upper()) if m else ("", ref.strip().upper())


class _Plane:
    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.radians(1.0) * _EARTH_R * math.cos(math.radians(lat0))
        self.ky = math.radians(1.0) * _EARTH_R

    def d(self, a, b) -> float:
        return math.hypot((a[1] - b[1]) * self.kx, (a[0] - b[0]) * self.ky)


def _ref_point(surface: dict, ref_latlon) -> tuple[float, float]:
    if ref_latlon is not None:
        return float(ref_latlon[0]), float(ref_latlon[1])
    hdr = (surface.get("header") or {}).get("ref")
    if hdr:
        return float(hdr[0]), float(hdr[1])
    pts = surface.get("terminals") or [[s["lat"], s["lon"]] for s in surface.get("stands") or []] or surface.get("nodes") or []
    if not pts:
        return 0.0, 0.0
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _taxi_nodes(surface: dict) -> tuple[set[int], set[int], set[int]]:
    """(taxilane nodes, any non-runway-edge nodes, nodes of the largest connected non-runway component)."""
    lane, taxi = set(), set()
    adj: dict[int, list[int]] = {}
    for e in surface.get("edges") or []:
        u, w, kind = e[0], e[1], e[3]
        if kind == "runway":
            continue
        taxi.update((u, w))
        if kind == "taxilane":
            lane.update((u, w))
        adj.setdefault(u, []).append(w)
        adj.setdefault(w, []).append(u)
    best: set[int] = set()
    seen: set[int] = set()
    for s in sorted(adj):
        if s in seen:
            continue
        comp = {s}
        q = deque([s])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v not in comp:
                    comp.add(v)
                    q.append(v)
        seen |= comp
        if len(comp) > len(best):
            best = comp
    return lane, taxi, best


def bind_gates(gate_ids: list[tuple[str, bool]], surface: dict, ref_latlon: tuple[float, float] | None = None) -> dict[str, dict]:
    """gate id -> {lat, lon, stand, node, geo_src}. Steps: exact ref, normalised ref, nearest stand to the OSM gate
    node (<= 150 m), the OSM gate node itself, generic ids, RS, synth."""
    ref0 = _ref_point(surface, ref_latlon)
    plane = _Plane(*ref0)
    stands = sorted(surface.get("stands") or [], key=lambda s: str(s["id"]))
    gates = surface.get("gates") or []
    nodes = surface.get("nodes") or []
    lane, taxi, main = _taxi_nodes(surface)
    out: dict[str, dict] = {}
    used_any: set[str] = set()       # stands taken by any binding
    used_generic: set[str] = set()   # stands taken by a generic or RS binding

    def gate_nodes(ref: str) -> list[dict]:
        exact = [g for g in gates if str(g["ref"]) == ref]
        return exact or [g for g in gates if str(g["ref"]).strip().upper() == ref.strip().upper()]

    def near_gate(stand: dict, gs: list[dict]) -> float:
        return min((plane.d((stand["lat"], stand["lon"]), (g["lat"], g["lon"])) for g in gs), default=math.inf)

    def pick(cands: list[dict], gs: list[dict]) -> dict:
        """Nearest to the OSM gate nodes `gs`, else first by id (cands is id-sorted)."""
        if gs:
            return min(cands, key=lambda s: (near_gate(s, gs), str(s["id"])))
        return cands[0]

    def bind(gid: str, stand: dict, src: str) -> None:
        out[gid] = {"lat": stand["lat"], "lon": stand["lon"], "stand": str(stand["id"]), "node": int(stand["node"]), "geo_src": src}
        used_any.add(str(stand["id"]))

    todo = []
    for gid, remote in gate_ids:
        if gid in out:
            continue
        if remote:
            todo.append((gid, remote))
            continue
        gs = gate_nodes(gid)
        exact = [s for s in stands if s.get("ref") is not None and str(s["ref"]) == gid]               # 1
        if exact:
            bind(gid, pick(exact, gs), "osm-ref")
            continue
        pre, num = _split(gid)
        norm = [s for s in stands if num and s.get("ref") is not None and _split(str(s["ref"])) == (pre, num)]   # 2
        if not norm and pre and num:
            pier = [g for g in gates if str(g["ref"]).upper().startswith(pre)]
            norm = [s for s in stands if s.get("ref") is not None and _split(str(s["ref"]))[1] == num
                    and _split(str(s["ref"]))[0] in ("", pre) and near_gate(s, pier) <= BIND_RADIUS_M]
            if norm:
                norm = [min(norm, key=lambda s: (near_gate(s, pier), str(s["id"])))]
        elif norm:
            norm = [pick(norm, gs)]
        if norm:
            bind(gid, norm[0], "osm-ref")
            continue
        near = [(near_gate(s, gs), str(s["id"]), s) for s in stands]                                     # 3
        near = [t for t in near if t[0] <= BIND_RADIUS_M]
        if near:
            bind(gid, min(near, key=lambda t: t[:2])[2], "osm-near")
            continue
        if gs:                                                                                           # 4
            g = min(gs, key=lambda g: (g["lat"], g["lon"]))
            pool = [i for i in (lane or taxi) if i < len(nodes)]
            if pool:
                node = min(pool, key=lambda i: (plane.d((g["lat"], g["lon"]), nodes[i]), i))
                out[gid] = {"lat": g["lat"], "lon": g["lon"], "stand": str(g["ref"]), "node": node, "geo_src": "osm-gate"}
                continue
        todo.append((gid, remote))

    term = surface.get("terminals") or []
    centre = (sum(t[0] for t in term) / len(term), sum(t[1] for t in term) / len(term)) if term else ref0

    def free(order: list[dict]) -> list[dict]:
        """Prefer stands nobody has taken, then stands only a real-ref binding has taken."""
        for avoid in (used_any, used_generic):
            c = [s for s in order if str(s["id"]) not in avoid]
            if c:
                return c
        return []

    ok = [s for s in stands if s["node"] in main] or stands
    generic = sorted((g for g in todo if not g[1] and _GENERIC.match(g[0])),
                     key=lambda g: (_GENERIC.match(g[0]).group(1).upper(), int(_GENERIC.match(g[0]).group(2)), g[0]))
    for gid, _r in generic:
        c = free(sorted(ok, key=lambda s: (plane.d(centre, (s["lat"], s["lon"])), str(s["id"]))))
        if c:
            bind(gid, c[0], "osm-generic")
            used_generic.add(c[0]["id"])
            out[gid]["geo_src"] = "osm-generic"
    for gid, remote in todo:
        if gid in out:
            continue
        reach = [s for s in stands if s["node"] in main]
        c = free(sorted(reach, key=lambda s: (-plane.d(centre, (s["lat"], s["lon"])), str(s["id"])))) if remote else []
        if c:
            bind(gid, c[0], "osm-generic")
            used_generic.add(c[0]["id"])
        else:
            out[gid] = {"lat": ref0[0], "lon": ref0[1], "stand": "synth", "node": -1, "geo_src": "synth"}
    return out
