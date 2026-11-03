"""Pure OSM (Overpass `elements`) -> airport surface graph builder. No network, no store, no database.

Geometry is projected to a local flat plane (metres) around `ref_latlon`. The graph keeps nodes at way
junctions, turns > 10 degrees and stand/holding positions; straight degree-2 chains are merged into one edge.
"""
from __future__ import annotations

import math
import re
from collections import deque

EARTH_R = 6371000.0
SNAP_M = 15.0              # taxiway end -> runway centreline
STAND_SNAP_M = 150.0       # off-graph stand -> nearest point on the nearest taxiway/taxilane segment
TURN_DEG = 10.0
RUNWAY_END_M = 150.0       # runway vertex must be this close to a threshold for the end to be "in OSM"
STRIP_AXIS_M = 30.0        # runway vertex belongs to a strip if this close to its axis
HOLD_STRIP_M = 150.0
CROSS_SIDE = 0.17          # |sin| of the angle to the axis below which an edge is "along" the runway
DEFAULT_RUNWAY_WIDTH_M = 45.0
KINDS = {"runway": "runway", "taxiway": "taxiway", "taxilane": "taxilane"}
HEADER = {"source": "OpenStreetMap", "license": "ODbL", "attribution": "© OpenStreetMap contributors"}


class _Proj:
    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.radians(1.0) * EARTH_R * math.cos(math.radians(lat0))
        self.ky = math.radians(1.0) * EARTH_R

    def fwd(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def inv(self, p: tuple[float, float]) -> list[float]:
        return [round(self.lat0 + p[1] / self.ky, 7), round(self.lon0 + p[0] / self.kx, 7)]


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _seg_dist(p, a, b):
    """(distance, t in [0,1], closest point) from p to segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    q = (a[0] + t * dx, a[1] + t * dy)
    return _dist(p, q), t, q


def _unit(a, b):
    d = _dist(a, b)
    return (0.0, 0.0) if d == 0 else ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _angle_deg(u, v) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))))


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _lead(name: str) -> int:
    m = re.match(r"\d+", name)
    return int(m.group()) if m else 0


def _strips(runways: list[dict], proj: _Proj) -> list[dict]:
    """Pair runway-end rows (each row: its own threshold le, opposite threshold he) into strips."""
    ends = []
    for r in runways:
        vals = [_num(r.get(k)) for k in ("le_lat", "le_lon", "he_lat", "he_lon")]
        if any(v is None for v in vals):
            continue
        ends.append((str(r["runway"]), proj.fwd(vals[0], vals[1]), proj.fwd(vals[2], vals[3])))
    strips, used = [], set()
    for i, (name, thr, other) in enumerate(ends):
        if i in used:
            continue
        used.add(i)
        mate = None
        for j, (n2, t2, o2) in enumerate(ends):
            if j not in used and _dist(t2, other) < 50 and _dist(o2, thr) < 50:
                mate = j
                break
        if mate is None:
            strips.append({"label": name, "ends": [(name, thr, other)]})
            continue
        used.add(mate)
        pair = sorted([ends[i], ends[mate]], key=lambda e: (_lead(e[0]), e[0]))
        strips.append({"label": f"{pair[0][0]}/{pair[1][0]}", "ends": pair})
    for s in strips:
        s["axis"] = (s["ends"][0][1], s["ends"][0][2])
    return strips


def _centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def build_surface(osm: dict, icao: str, runways: list[dict], ref_latlon: tuple[float, float], osm_timestamp: str) -> dict:
    proj = _Proj(*ref_latlon)
    onode: dict = {}
    oways: list[dict] = []
    for el in osm.get("elements", []):
        if el.get("type") == "node" and "lat" in el:
            onode[el["id"]] = {"p": proj.fwd(el["lat"], el["lon"]), "tags": el.get("tags") or {}}
        elif el.get("type") == "way":
            oways.append(el)
    pos = {k: v["p"] for k, v in onode.items()}   # vertex key -> (x, y); snapped vertices get ("snap", n) keys

    paths: list[dict] = []
    stand_srcs: list[dict] = []
    terminals: list[list[float]] = []
    for w in oways:
        tags = w.get("tags") or {}
        keys = [n for n in w.get("nodes", []) if n in onode]
        keys = [k for i, k in enumerate(keys) if i == 0 or k != keys[i - 1]]
        aero = tags.get("aeroway")
        if aero in KINDS and len(keys) >= 2:
            paths.append({"kind": KINDS[aero], "name": tags.get("ref") or tags.get("name"), "keys": keys, "tags": tags})
        elif aero == "parking_position" and keys:
            stand_srcs.append({"type": "w", "id": w["id"], "tags": tags, "p": _centroid([pos[k] for k in keys]), "key": None})
        elif aero == "terminal" and keys:
            terminals.append(proj.inv(_centroid([pos[k] for k in set(keys)])))

    # snap dangling taxiway ends onto runway centrelines (inserting a vertex into the runway way)
    rpaths = [p for p in paths if p["kind"] == "runway"]
    rkeys = {k for p in rpaths for k in p["keys"]}
    nsnap = 0
    for p in paths:
        if p["kind"] == "runway":
            continue
        for end in (0, -1):
            k = p["keys"][end]
            if k in rkeys:
                continue
            best = None
            for rp in rpaths:
                ks = rp["keys"]
                for i in range(len(ks) - 1):
                    d, _t, q = _seg_dist(pos[k], pos[ks[i]], pos[ks[i + 1]])
                    if d <= SNAP_M and (best is None or d < best[0]):
                        best = (d, rp, i, q)
            if best is None:
                continue
            _d, rp, i, q = best
            new = next((rp["keys"][j] for j in (i, i + 1) if _dist(q, pos[rp["keys"][j]]) <= 1.0), None)
            if new is None:
                nsnap += 1
                new = ("snap", nsnap)
                pos[new] = q
                rp["keys"].insert(i + 1, new)
            p["keys"][end] = new
            rkeys.add(new)

    # undirected segment graph
    adj: dict = {}
    for p in paths:
        ks = p["keys"]
        for a, b in zip(ks, ks[1:]):
            if a == b:
                continue
            attrs = (p["kind"], p["name"])
            adj.setdefault(a, {}).setdefault(b, attrs)
            adj.setdefault(b, {}).setdefault(a, attrs)

    # stands (nodes with aeroway=parking_position) and holding positions
    for nid, n in onode.items():
        if n["tags"].get("aeroway") == "parking_position":
            stand_srcs.append({"type": "n", "id": nid, "tags": n["tags"], "p": n["p"], "key": nid if nid in adj else None})
    segs, seen_seg = [], set()
    for a, nb in adj.items():
        for b, attrs in nb.items():
            if attrs[0] != "runway" and frozenset((a, b)) not in seen_seg:
                seen_seg.add(frozenset((a, b)))
                segs.append([a, b, attrs])
    special: set = set()
    nstand = 0
    for s in sorted(stand_srcs, key=lambda s: (s["type"], s["id"])):
        if s["key"] is None and segs:
            best = None
            for i, (a, b, _at) in enumerate(segs):
                d, _t, q = _seg_dist(s["p"], pos[a], pos[b])
                if d <= STAND_SNAP_M and (best is None or d < best[0]):
                    best = (d, i, q)
            if best is not None:
                _d, i, q = best
                a, b, attrs = segs[i]
                end = next((k for k in (a, b) if _dist(q, pos[k]) <= 1.0), None)
                if end is None:   # split the segment with a new node at the projection
                    nstand += 1
                    end = ("stand", nstand)
                    pos[end] = q
                    del adj[a][b], adj[b][a]
                    adj[a][end] = adj[b][end] = attrs
                    adj[end] = {a: attrs, b: attrs}
                    segs[i] = [a, end, attrs]
                    segs.append([end, b, attrs])
                s["key"] = end
        if s["key"] is not None:
            special.add(s["key"])
    hold_keys = [nid for nid, n in onode.items() if n["tags"].get("aeroway") == "holding_position" and nid in adj]
    special.update(hold_keys)

    # nodes: junctions, endpoints, kind/name changes, turns > 10 degrees, special vertices
    keep = []
    for v, nb in adj.items():
        if v in special or len(nb) != 2:
            keep.append(v)
            continue
        (n1, a1), (n2, a2) = nb.items()
        turn = _angle_deg(_unit(pos[n1], pos[v]), _unit(pos[v], pos[n2]))
        if a1 != a2 or turn > TURN_DEG:
            keep.append(v)
    keepset = set(keep)
    index = {v: i for i, v in enumerate(keep)}
    nodes_xy = [pos[v] for v in keep]

    edges: list[list] = []
    geoms: list[list] = []   # polyline (u -> v) in plane metres per edge
    seen: set = set()
    for v in keep:
        for n, attrs in adj[v].items():
            if frozenset((v, n)) in seen:
                continue
            seen.add(frozenset((v, n)))
            chain, prev, cur = [v], v, n
            while cur not in keepset:
                chain.append(cur)
                nxt = next(m for m in adj[cur] if m != prev)
                seen.add(frozenset((cur, nxt)))
                prev, cur = cur, nxt
            chain.append(cur)
            pts = [pos[k] for k in chain]
            length = sum(_dist(a, b) for a, b in zip(pts, pts[1:]))
            edges.append([index[v], index[cur], round(length, 2), attrs[0], attrs[1], None])
            geoms.append(pts)

    nbrs: dict[int, list[tuple[int, int]]] = {i: [] for i in range(len(keep))}
    for ei, (u, w, *_r) in enumerate(edges):
        nbrs[u].append((ei, w))
        if w != u:
            nbrs[w].append((ei, u))

    def away(ei: int, v: int):
        """Unit direction of edge `ei` leaving node `v`."""
        pts = geoms[ei]
        a = pts if edges[ei][0] == v else pts[::-1]
        return _unit(a[0], a[1])

    # strips present in OSM; crossings and exits
    strips = _strips(runways, proj)
    rw_nodes = [i for i in range(len(keep)) if any(edges[ei][3] == "runway" for ei, _ in nbrs[i])]
    exits: dict[str, list] = {}
    thr_node: dict[str, int] = {}
    for s in strips:
        near = []
        for _name, thr, _o in s["ends"]:
            c = min(rw_nodes, key=lambda i: _dist(nodes_xy[i], thr), default=None)
            near.append(c if c is not None and _dist(nodes_xy[c], thr) <= RUNWAY_END_M else None)
        s["present"] = all(c is not None for c in near) and bool(near)
        if not s["present"]:
            continue
        a0, a1 = s["axis"]
        axis = _unit(a0, a1)
        on = [i for i in rw_nodes if _seg_dist(nodes_xy[i], a0, a1)[0] <= STRIP_AXIS_M]
        crossing = set()
        for i in on:
            sides = set()
            for ei, _ in nbrs[i]:
                if edges[ei][3] == "runway":
                    continue
                d = away(ei, i)
                cr = axis[0] * d[1] - axis[1] * d[0]
                if abs(cr) > CROSS_SIDE:
                    sides.add(cr > 0)
            if len(sides) == 2:
                crossing.add(i)
                for ei, _ in nbrs[i]:
                    if edges[ei][3] != "runway":
                        edges[ei][5] = s["label"]
        for (name, thr, other), c in zip(s["ends"], near):
            thr_node[name] = c
            direction = _unit(thr, other)
            found = []
            for i in on:
                if i in crossing:
                    continue
                for ei, _ in nbrs[i]:
                    if edges[ei][3] == "runway":
                        continue
                    d = away(ei, i)
                    found.append({"node": i,
                                  "dist_m": round((nodes_xy[i][0] - thr[0]) * direction[0] + (nodes_xy[i][1] - thr[1]) * direction[1], 2),
                                  "angle_deg": round(_angle_deg(direction, d), 1)})
            found.sort(key=lambda e: (e["dist_m"], e["angle_deg"]))
            exits[name] = found

    # stands, gates, holding positions
    stands, isolated = [], []
    for s in sorted(stand_srcs, key=lambda s: (s["type"], s["id"])):
        tags = s["tags"]
        if s["key"] is not None:
            node = index[s["key"]]
        else:
            isolated.append(s["p"])
            node = len(keep) + len(isolated) - 1
        lat, lon = proj.inv(s["p"])
        stands.append({"id": tags.get("name") or f"{s['type']}{s['id']}", "ref": tags.get("ref"), "node": node, "lat": lat, "lon": lon})
    ids = [x["id"] for x in stands]
    for x, s in zip(stands, sorted(stand_srcs, key=lambda s: (s["type"], s["id"]))):
        if ids.count(x["id"]) > 1:
            x["id"] = f"{s['type']}{s['id']}"
    nodes_xy = nodes_xy + isolated

    gates = []
    for nid, n in onode.items():
        if n["tags"].get("aeroway") == "gate" and n["tags"].get("ref"):
            lat, lon = proj.inv(n["p"])
            gates.append({"ref": n["tags"]["ref"], "lat": lat, "lon": lon})
    for nid, n in onode.items():
        if n["tags"].get("aeroway") == "terminal":
            terminals.append(proj.inv(n["p"]))

    holds = []
    for k in hold_keys:
        best = min(((_seg_dist(pos[k], *s["axis"])[0], s["label"]) for s in strips), default=(None, None))
        holds.append({"node": index[k], "strip": best[1] if best[0] is not None and best[0] <= HOLD_STRIP_M else None})

    # reachability of ALL stands from each runway end present in OSM: seeded at the end's exit nodes, taxi edges
    # only (never along a runway; a taxiway crossing a runway node passes through taxi -> taxi)
    reach = {}
    for name in thr_node:
        seeds = {e["node"] for e in exits.get(name, [])}
        seen_n = set(seeds)
        q = deque(seeds)
        while q:
            u = q.popleft()
            for ei, w in nbrs.get(u, []):
                if edges[ei][3] != "runway" and w not in seen_n:
                    seen_n.add(w)
                    q.append(w)
        reach[name] = round(sum(1 for x in stands if x["node"] in seen_n) / len(stands), 4) if stands else 0.0

    # drawing geometry
    draw_tw, draw_rw = [], []
    for p in paths:
        line = [proj.inv(pos[k]) for k in p["keys"]]
        if p["kind"] == "runway":
            mid = pos[p["keys"][len(p["keys"]) // 2]]
            strip = min(strips, key=lambda s: _seg_dist(mid, *s["axis"])[0], default=None)
            if strip is not None and _seg_dist(mid, *strip["axis"])[0] <= STRIP_AXIS_M:
                ids_ = [e[0] for e in strip["ends"]]
            else:
                ids_ = [x for x in (p["name"] or "").split("/") if x]
            draw_rw.append({"ids": ids_, "line": line, "width_m": _num(p["tags"].get("width")) or DEFAULT_RUNWAY_WIDTH_M})
        else:
            draw_tw.append({"name": p["name"], "kind": p["kind"], "line": line})

    return {
        "header": {**HEADER, "osm_timestamp": osm_timestamp},
        "nodes": [proj.inv(p) for p in nodes_xy],
        "edges": edges,
        "exits": exits,
        "stands": stands,
        "gates": gates,
        "holding_positions": holds,
        "terminals": terminals,
        "draw": {"taxiways": draw_tw, "runways": draw_rw},
        "quality": {"reach_pct": reach},
    }
