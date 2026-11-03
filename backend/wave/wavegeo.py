"""Contract `WaveGeo` for one wave: the procedures the engine actually flies there (spec §7), surface null in M1.

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
Pure assembly over read-only caches; nothing here decides legality.
"""
from __future__ import annotations

from backend.geo import store
from backend.wave import holding, route, trajectory
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


def wave_geo(wave: Wave) -> dict:
    """Contract `WaveGeo` (surface: null until the surface milestone). Call only when `wave.geo_available()`."""
    return {"engine_version": ENGINE_VERSION, "procedures": procedures(wave), "surface": None}
