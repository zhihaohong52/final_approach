"""Contract `WaveGeo` for one wave: the procedures and the airport surface the engine actually uses (spec §7).

Built from the engine's own objects, never from raw CIFP alone, so the map shows only what the trajectories
use ("never draw a path the engine did not use"):
  * `routes`: the distinct (runway, star, transition, approach, named fixes) over `route.routes_for_wave`
    (every wave and queue flight over every open runway end).
  * `fixes`: every named point on those routes (thresholds excluded) plus every hold fix, deduplicated by
    name and position (0.001 deg). Kind, by priority when a fix plays several roles:
    hold > corner > faf > if > iaf > entry > star. faf / if / iaf come from the CIFP approach coding of the
    route's approach (`route._desc` / `route._iaf`); a synthetic `CPnnn` entry is `corner`; any other named
    fix (STAR fix or uncoded approach step-down fix) is `star`.
  * `holds`: the wave's shared hold per physical fix (`trajectory.wave_holds`), with the racetrack polygon
    the laps are drawn on (`holding.racetrack`, [lat, lon] pairs).
  * `cycle` / `source` from the procedures document header (read through `store.load_procedures`, i.e. the
    datastore); "" / "synthetic" when the airport has none. `status` is "real" iff some route is `star+iap`.
  * `surface` (`surface_part`): the OSM drawing layer of the airport (taxiways, runways), every stand (labelled as the
    ground tracks label them: OSM `ref`, else id) with the wave gate bound to it (`surface._geo`, the same binding the
    taxi times use; stands are listed only for a `real` tier), the holding positions (node id, runway strip when known). `status` is the wave's taxi tier
    (`surface.wave_tier`). null when the airport has no OSM surface.
  * `ground_traffic` (`ground_traffic_part`, v3-traffic): the wave's fixed ground traffic, plan-independent, drawn grey
    on the surface map: one track per scheduled fixed mover (queue arrivals, real 2015 departures) thinned to what the
    map draws (`[t_min, lat, lon]` points, waits), plus the runway events (landings and takeoffs per strip) the wave
    flights are scheduled against. null without a real surface tier (no ground traffic is scheduled then).
Pure assembly over read-only caches; nothing here decides legality.
"""
from __future__ import annotations

from backend.geo import store
from backend.wave import ground_traffic, holding, route, surface, trajectory
from backend.wave.model import Wave
from backend.wave.version import ENGINE_VERSION

_PRIORITY = ("hold", "corner", "faf", "if", "iaf", "entry", "star")
_RANK = {k: i for i, k in enumerate(_PRIORITY)}
COORD_DP = 6   # ~0.1 m


def _r(x: float) -> float:
    return round(float(x), COORD_DP)


def _approach_roles(proc: dict | None, approach_id: str | None) -> dict[str, str]:
    """fix -> "faf" | "if" | "iaf" from the CIFP coding of the approach `approach_id` (all its legs)."""
    if not proc or not approach_id:
        return {}
    app = next((a for a in (proc.get("approaches") or {}).values() if a.get("id") == approach_id), None)
    if app is None:
        return {}
    out: dict[str, str] = {}
    legs = [g for legs in (app.get("transitions") or {}).values() for g in legs] + list(app.get("final") or [])
    for g in legs:
        fix = g.get("fix")
        if not fix:
            continue
        if route._desc(g, 3) == "F":
            role = "faf"
        elif route._desc(g, 3) in route.IF_CODES:
            role = "if"
        elif route._iaf(g):
            role = "iaf"
        else:
            continue
        if fix not in out or _RANK[role] < _RANK[out[fix]]:
            out[fix] = role
    return out


def procedures(wave: Wave) -> dict:
    proc = store.load_procedures(wave.airport.icao)
    header = (proc or {}).get("header") or {}
    fixes: dict[tuple, dict] = {}

    def add(fid: str, lat: float, lon: float, kind: str, src: str) -> None:
        k = (fid, round(lat, 3), round(lon, 3))
        cur = fixes.get(k)
        if cur is None:
            fixes[k] = {"id": fid, "lat": _r(lat), "lon": _r(lon), "kind": kind, "src": src}
        else:
            if _RANK[kind] < _RANK[cur["kind"]]:
                cur["kind"] = kind
            if src == "cifp":
                cur["src"] = "cifp"

    routes: dict[tuple, dict] = {}
    roles_by_app: dict[str | None, dict[str, str]] = {}
    real = False
    for (_, rwy), r in sorted(route.routes_for_wave(wave).items()):
        real = real or r.tier == "star+iap"
        roles = roles_by_app.get(r.approach)
        if roles is None:
            roles = roles_by_app[r.approach] = _approach_roles(proc, r.approach)
        names: list[str] = []
        for i, p in enumerate(r.points):
            if not p.fix or p.kind == "threshold":
                continue
            if i == 0:
                kind = "corner" if p.src == "synth" and p.fix.startswith("CP") else "entry"
            else:
                kind = roles.get(p.fix, "star")
            if i == 0 and p.fix in roles and _RANK[roles[p.fix]] < _RANK[kind]:
                kind = roles[p.fix]
            add(p.fix, p.lat, p.lon, kind, p.src)
            names.append(p.fix)
        d = {"runway": rwy}
        for k in ("star", "transition", "approach"):
            if getattr(r, k):
                d[k] = getattr(r, k)
        d["fixes"] = names
        routes.setdefault((rwy, r.star, r.transition, r.approach, tuple(names)), d)

    holds = []
    for h in trajectory.wave_holds(wave).values():
        add(h.fix, h.lat, h.lon, "hold", h.src)
        holds.append({"fix": h.fix, "inbound_true": h.inbound_true, "turn": h.turn, "leg_min": h.leg_min,
                      "base_ft": h.base_ft, "levels_ft": list(h.levels_ft), "lap_min": h.lap_min, "src": h.src,
                      "polygon": [[_r(lat), _r(lon)] for lat, lon in holding.racetrack(h)]})
    holds.sort(key=lambda x: (x["fix"], x["polygon"][1]))

    return {"cycle": str(header.get("cycle") or ""), "source": (header.get("source") or "FAA CIFP") if proc else "synthetic",
            "status": "real" if proc and real else "generic",
            "fixes": sorted(fixes.values(), key=lambda f: (f["id"], f["lat"], f["lon"])),
            "routes": list(routes.values()), "holds": holds}


def _line(pts: list) -> list[list[float]]:
    return [[_r(a), _r(b)] for a, b in pts]


def surface_part(wave: Wave) -> dict | None:
    doc = store.load_surface(wave.airport.icao)
    if doc is None:
        return None
    gg = surface._geo(wave)
    hdr = doc.get("header") or {}
    draw = doc.get("draw") or {}
    gate_of: dict[str, tuple[str, str]] = {}      # stand id -> (gate id, geo_src); first gate by id wins
    for gid, b in sorted(gg.bind.items()):
        if b.get("geo_src") != "synth" and b.get("stand") not in (None, "synth"):
            gate_of.setdefault(str(b["stand"]), (gid, b["geo_src"]))
    status = surface.wave_tier(wave, gg.graph)
    names = surface.stand_labels(doc.get("stands") or [], set(gate_of))
    stands = []
    for st in sorted(doc.get("stands") or [], key=lambda x: str(x["id"])) if status == "real" else []:
        sid = str(st["id"])
        d = {"id": names[sid], "lat": _r(st["lat"]), "lon": _r(st["lon"])}
        if sid in gate_of:
            d["gate"], d["geo_src"] = gate_of[sid]
        else:
            d["geo_src"] = "osm"
        stands.append(d)
    nodes = doc.get("nodes") or []
    holding_positions = []
    for h in sorted(doc.get("holding_positions") or [], key=lambda h: (h["node"], h.get("strip") or "")):
        lat, lon = nodes[int(h["node"])]
        d = {"id": f"HP{h['node']}", "lat": _r(lat), "lon": _r(lon)}
        if h.get("strip"):
            d["runway"] = h["strip"]
        holding_positions.append(d)
    ts = str(hdr.get("osm_timestamp") or "")[:10]
    return {"source": f"OpenStreetMap {ts}".strip(), "status": status,
            "attribution": hdr.get("attribution") or "© OpenStreetMap contributors",
            "taxiways": [{"name": t.get("name"), "kind": t["kind"], "line": _line(t["line"])} for t in draw.get("taxiways") or []],
            "runways": [{"ids": list(r["ids"]), "line": _line(r["line"]), "width_m": r.get("width_m")} for r in draw.get("runways") or []],
            "stands": stands, "holding_positions": holding_positions}


T_DP = 2       # minutes, ~0.6 s


def ground_traffic_part(wave: Wave) -> dict | None:
    """`WaveGeo.ground_traffic`: the fixed tracks (`ground_traffic.fixed_tracks`) and runway events, thinned."""
    g = surface._geo(wave).graph
    if g is None or surface.wave_tier(wave, g) != "real":
        return None
    ft = ground_traffic._get(wave)
    tracks = [{"callsign": t.callsign, "kind": t.kind, "runway": t.exit, "stand": t.stand,
               "points": [[round(q["t_min"], T_DP), _r(q["lat"]), _r(q["lon"])] for q in t.points],
               "waits": [[round(w["from_min"], T_DP), round(w["to_min"], T_DP), _r(w["lat"]), _r(w["lon"]), w["reason"]]
                         for w in t.waits]}
              for t in ft.tracks]
    events = {strip: [[round(t, T_DP), kind, cs] for t, kind, cs in sorted(lst)]
              for strip, lst in sorted(ft.reservations.runway_events.items())}
    return {"source": next((p.source for p in wave.provenance if p.layer == "ground_traffic"), ""),
            "tracks": tracks, "runway_events": events}


def wave_geo(wave: Wave) -> dict:
    """Contract `WaveGeo`. Call only when `wave.geo_available()`."""
    return {"engine_version": ENGINE_VERSION, "procedures": procedures(wave), "surface": surface_part(wave),
            "ground_traffic": ground_traffic_part(wave)}
