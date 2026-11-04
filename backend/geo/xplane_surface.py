"""Pure X-Plane apt.dat (Scenery Gateway) -> Overpass-like `elements`, so `osm_surface.build_surface` builds the graph.

apt.dat 1100 rows used: 1201 taxi node, 1202 taxi edge (+ 1204 runway hot zone), 1300 ramp start, 100 land runway.
- each 1202 edge becomes a two-node way: aeroway=runway (ref "15L/33R") or aeroway=taxiway (ref "B" from "taxiway_B");
- a taxi node joining an edge inside a runway's hot zone to one outside it becomes an aeroway=holding_position
  (the hold-short line), unless it lies on the runway itself;
- gate and tie-down ramp starts become aeroway=parking_position nodes (ref = the ramp name, e.g. "CC1").
Gateway data is GPL v2 or later (see the pack's COPYING); the derived surface keeps that licence.
"""
from __future__ import annotations

HEADER = {"source": "X-Plane Scenery Gateway", "license": "GPL-2.0-or-later",
          "attribution": "X-Plane Scenery Gateway contributors (GPL)"}
RAMP_KINDS = {"gate", "tie_down"}


def _name(raw: str) -> str | None:
    n = raw.strip()
    for p in ("taxiway_", "taxilane_", "runway_"):
        if n.lower().startswith(p):
            n = n[len(p):]
    return n or None


def parse(text: str) -> dict:
    """apt.dat text -> {"nodes": {id: (lat, lon)}, "edges": [(u, v, kind, name, hot)], "ramps": [...], "runways": [...]}."""
    nodes: dict[str, tuple[float, float]] = {}
    edges: list[list] = []
    ramps: list[dict] = []
    runways: list[dict] = []
    for ln in text.splitlines():
        f = ln.split()
        if not f:
            continue
        code = f[0]
        if code == "1201" and len(f) >= 5:
            nodes[f[4]] = (float(f[1]), float(f[2]))
        elif code == "1202" and len(f) >= 5:
            kind = "runway" if f[4] == "runway" else "taxiway"
            edges.append([f[1], f[2], kind, _name(" ".join(f[5:])) if len(f) > 5 else None, set()])
        elif code == "1204" and edges and len(f) >= 3:
            edges[-1][4].update(x for x in f[2].split(",") if x)
        elif code == "1300" and len(f) >= 6 and f[4] in RAMP_KINDS:
            ramps.append({"lat": float(f[1]), "lon": float(f[2]), "kind": f[4], "name": " ".join(f[6:]) or None})
        elif code == "100" and len(f) >= 20:
            runways.append({"ids": [f[8], f[17]], "width_m": float(f[1]),
                            "ends": [(float(f[9]), float(f[10])), (float(f[18]), float(f[19]))]})
    return {"nodes": nodes, "edges": edges, "ramps": ramps, "runways": runways}


def to_elements(text: str) -> dict:
    """Overpass-like {"elements": [...]} for `build_surface`. Node ids are ints: taxi nodes keep theirs (+1),
    ramp starts and holds follow after the largest."""
    p = parse(text)
    p["edges"] = [e for e in p["edges"] if e[0] in p["nodes"] and e[1] in p["nodes"] and e[0] != e[1]]
    ids = {k: i + 1 for i, k in enumerate(sorted(p["nodes"], key=lambda k: int(k) if k.isdigit() else 0))}
    used = {k for e in p["edges"] for k in e[:2]}
    width = {}
    for r in p["runways"]:
        width["/".join(r["ids"])] = r["width_m"]
    els: list[dict] = []
    # hold-short lines: a node between a hot-zone taxi edge and a cold one, not itself on a runway edge
    on_runway = {k for e in p["edges"] if e[2] == "runway" for k in e[:2]}
    hot_at: dict[str, set] = {}
    cold_at: set = set()
    for u, v, kind, _n, hot in p["edges"]:
        if kind == "runway":
            continue
        for k in (u, v):
            if hot:
                hot_at.setdefault(k, set()).update(hot)
            else:
                cold_at.add(k)
    holds = {k for k in hot_at if k in cold_at and k not in on_runway}
    for k in sorted(used, key=lambda k: ids[k]):
        lat, lon = p["nodes"][k]
        el = {"type": "node", "id": ids[k], "lat": lat, "lon": lon}
        if k in holds:
            el["tags"] = {"aeroway": "holding_position"}
        els.append(el)
    nid = max(ids.values(), default=0)
    for r in p["ramps"]:
        nid += 1
        els.append({"type": "node", "id": nid, "lat": r["lat"], "lon": r["lon"],
                    "tags": {"aeroway": "parking_position", **({"ref": r["name"]} if r["name"] else {})}})
    for i, (u, v, kind, name, _hot) in enumerate(p["edges"]):
        tags = {"aeroway": kind}
        if name:
            tags["ref"] = name
        if kind == "runway" and name in width:
            tags["width"] = str(width[name])
        els.append({"type": "way", "id": i + 1, "nodes": [ids[u], ids[v]], "tags": tags})
    return {"elements": els}
