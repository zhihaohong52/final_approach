"""Per-plan 4D air trajectories: route + holding laps + vector stretch, timed to touch down at the slot (spec §6.2, §6.3).

Pure assembly over the per-wave route cache (`route.routes_for_wave`, read-only) and `holding`:
  * entry time (§6.2): `t_entry = eta_earliest_min - T_route(nominal)`, nominal = the runway the FCFS plan
    gives the flight (`moves.fcfs_runways`); the earliest landing on runway r is
    `t_entry + SR + GD + T_route(r)`, so it equals v2's `eta_earliest_min + SR + GD` on the nominal runway
    and whenever the wave has no geometry (no route for r or for the nominal runway).
  * delay absorption: `D = slot - earliest(f, r)` splits into `laps = floor(D / lap_min)` whole laps at the
    hold fix plus `vector_min = D - laps x lap_min` drawn as an extended-downwind out-and-back (`synth`)
    before the final. A slot before the earliest landing (D < 0, which PLT-EARLIEST-ETA rejects) is drawn
    with no laps and no stretch, the route simply timed to land at the slot: the hold identity
    `laps x lap_min + vector_min = D` holds only for D >= 0.
  * stack (§6.3): only flights with laps >= 1 enter it; levels come from `holding.stack_levels` per hold
    fix (grouped by fix name), and its violations (more than 5 levels, an exit that is not bottom-first)
    are reported in `TrajectorySet.stack_violations`, never raised. Hold points are drawn at the level the
    flight is assigned on entry (the step-down as lower levels empty is not drawn).
  * fixed traffic (queue arrivals): `wave=False`, zero laps, the nominal route timed to land at its slot.
Nothing here decides legality: the rules read these objects.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from atm.wave import holding, route
from atm.wave.model import JointPlan, Wave
from atm.wave.version import ENGINE_VERSION

CACHE_MAX = 4096
MIN_VECTOR_DRAW_MIN = 1e-6     # below this the stretch is kept in vector_min but not drawn
RADAR_NM = 150.0               # drawn points stay within this of the airport reference point (spec §12)


@dataclass
class AirTrack:
    """One flight's airborne path for a plan.

    `points` are dicts {t_min, lat, lon, alt_ft, kind, fix?, src, alt_src}. `lap_min` is the lap time of
    the hold at its base level (`HoldSpec.lap_min`), used for every level of the stack; it is set whenever
    the flight has a route (even with zero laps) and None in the no-geometry tier, where `points` is empty.
    `hold` (contract shape) and `hold_fix` / `hold_level_ft` are set only when laps >= 1.
    `route_extra_min = T_route(runway) - T_route(nominal)` (0 for fixed traffic and without geometry).
    `src_tier` is the route tier ("star+iap" | "iap" | "straight-in") or "none" without geometry.
    Drawn points stay within 150 NM of the airport reference point: a route entering from farther out is
    clipped at the 150 NM boundary (an interpolated `entry` point, `src "synth"`), and `appears_min` is the
    time there (radar coverage). Route timing (`route_min`, entry time) still covers the full route from the
    published entry fix, kept as `entry_fix` / `entry_fix_min`."""
    callsign: str
    wave: bool
    runway: str
    appears_min: float
    points: list[dict]
    hold: dict | None
    route_min: float
    route_extra_min: float
    laps: int
    vector_min: float
    lap_min: float | None
    hold_fix: str | None
    hold_level_ft: int | None
    src_tier: str
    entry_fix: str | None = None        # published entry fix (route start), kept when the drawn track is clipped
    entry_fix_min: float | None = None  # time at the entry fix (not serialised)

    def to_dict(self) -> dict:
        """Contract `TrajectorySet.air[]` entry (`fix` and `hold` omitted when None; `alt_src` is internal)."""
        pts = []
        for p in self.points:
            q = {k: p[k] for k in ("t_min", "lat", "lon", "alt_ft", "kind")}
            if p.get("fix"):
                q["fix"] = p["fix"]
            q["src"] = p["src"]
            pts.append(q)
        d = {"callsign": self.callsign, "wave": self.wave, "runway": self.runway,
             "appears_min": self.appears_min, "points": pts}
        if self.hold is not None:
            d["hold"] = dict(self.hold)
        return d


@dataclass
class TrajectorySet:
    plan_id: str
    engine_version: str
    air: list[AirTrack]
    ground: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    stack_violations: dict[str, list[str]] = field(default_factory=dict)   # hold fix -> messages (internal)

    def to_dict(self) -> dict:
        """Contract `TrajectorySet` (stack_violations are internal; the rules map them into conflicts)."""
        return {"plan_id": self.plan_id, "engine_version": self.engine_version,
                "air": [a.to_dict() for a in self.air],
                "ground": [g.to_dict() if hasattr(g, "to_dict") else g for g in self.ground],
                "conflicts": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.conflicts]}


# --------------------------------------------------------------------------- per-wave helpers

_NOMINAL: dict[str, dict[str, str]] = {}


def _nominal(wave: Wave) -> dict[str, str]:
    """callsign -> nominal runway: the FCFS runway for wave flights, the queue runway for fixed traffic."""
    k = route.wave_key(wave)
    hit = _NOMINAL.get(k)
    if hit is None:
        from atm.wave import moves   # lazy: moves -> utility -> trajectory would cycle
        hit = {q.callsign: q.runway for q in wave.queue}
        hit.update(moves.fcfs_runways(wave))
        while len(_NOMINAL) >= CACHE_MAX:
            _NOMINAL.pop(next(iter(_NOMINAL)))
        _NOMINAL[k] = hit
    return hit


def _t_route(wave: Wave, callsign: str, runway: str | None) -> float | None:
    r = route.routes_for_wave(wave).get((callsign, runway)) if runway else None
    return r.t_route_min if r is not None else None


def entry_time(wave: Wave, callsign: str) -> float | None:
    """`eta_earliest_min - T_route(nominal)`; None without geometry for the nominal runway."""
    f = next((x for x in wave.flights if x.callsign == callsign), None)
    tn = _t_route(wave, callsign, _nominal(wave).get(callsign))
    if f is None or tn is None:
        return None
    return f.eta_earliest_min - tn


def _route_extra(wave: Wave, callsign: str, runway: str | None) -> float:
    tn = _t_route(wave, callsign, _nominal(wave).get(callsign))
    tr = _t_route(wave, callsign, runway)
    return 0.0 if tn is None or tr is None else tr - tn


def earliest(wave: Wave, callsign: str, runway: str, sr: float = 0.0, gd: float = 0.0) -> float:
    """Earliest touchdown on `runway`: `t_entry + sr + gd + T_route(runway)`; `eta_earliest_min + sr + gd`
    on the nominal runway and in the no-geometry tier."""
    f = wave.flight(callsign)
    return f.eta_earliest_min + sr + gd + _route_extra(wave, callsign, runway)


# --------------------------------------------------------------------------- assembly

def _pt(t, lat, lon, alt, kind, fix, src, alt_src) -> dict:
    return {"t_min": t, "lat": lat, "lon": lon, "alt_ft": alt, "kind": kind, "fix": fix, "src": src,
            "alt_src": alt_src}


def _lap(hold: holding.HoldSpec) -> list[tuple[float, float, float]]:
    """One lap from the fix back to the fix: (lat, lon, fraction of the lap time) per point after the fix.
    Time is proportional to distance (constant TAS through legs and turns)."""
    poly = holding.racetrack(hold)
    seq = poly[2:] + [poly[1]]          # poly[1] is the fix; poly[0] == poly[-1] is the inbound-leg start
    d = [0.0]
    prev = poly[1]
    for p in seq:
        d.append(d[-1] + route._nm(prev[0], prev[1], p[0], p[1]))
        prev = p
    total = d[-1] or 1.0
    return [(p[0], p[1], d[i + 1] / total) for i, p in enumerate(seq)]


_LAPS: dict[tuple, list] = {}


def _lap_cached(hold: holding.HoldSpec) -> list[tuple[float, float, float]]:
    k = (hold.fix, hold.lat, hold.lon, hold.inbound_true, hold.turn, hold.leg_min, hold.base_ft)
    hit = _LAPS.get(k)
    if hit is None:
        if len(_LAPS) >= CACHE_MAX:
            _LAPS.clear()
        hit = _LAPS[k] = _lap(hold)
    return hit


def _assemble(wave: Wave, r: route.Route, slot: float, laps: int, lap_min: float,
              vector_min: float) -> tuple[list[dict], float | None]:
    """Points of route `r` with `laps` laps at the hold fix and `vector_min` of stretch before the final,
    timed so the threshold is crossed at `slot`; and the hold entry time (None without laps). Hold points
    are drawn at the hold's base level; the caller sets the stack level."""
    pts = r.points
    n = len(pts)
    hold_min = laps * lap_min
    appears = slot - (r.t_route_min + hold_min + vector_min)
    hi = r.hold_index
    i_fin = next((i for i, p in enumerate(pts) if p.kind in ("final", "threshold") and i > 0), n - 1)
    draw_vec = vector_min > MIN_VECTOR_DRAW_MIN
    out: list[dict] = []
    t_in = None
    for i, p in enumerate(pts):
        off = (hold_min if i > hi else 0.0) + (vector_min if i >= i_fin else 0.0)
        if i == i_fin and draw_vec:
            q = pts[i - 1]
            t0 = appears + q.t_min + (hold_min if i - 1 >= hi else 0.0)
            gs = holding.tas_kt(route.IAS_IAF, q.alt_ft)
            hdg = (wave.runway(r.runway).heading_true + 180.0) % 360.0
            v = route._dest(q.lat, q.lon, hdg, gs * vector_min / 2.0 / 60.0)
            out.append(_pt(t0 + vector_min / 2.0, v[0], v[1], q.alt_ft, "vector", None, "synth", "interp"))
            out.append(_pt(t0 + vector_min, q.lat, q.lon, q.alt_ft, "vector", None, "synth", "interp"))
        t = slot if i == n - 1 else appears + p.t_min + off
        out.append(_pt(t, p.lat, p.lon, p.alt_ft, p.kind, p.fix, p.src, p.alt_src))
        if i == hi and laps > 0:
            t_in = t
            lap = _lap_cached(r.hold)
            alt = float(r.hold.base_ft)
            for k in range(laps):
                for lat, lon, frac in lap:
                    fix = r.hold.fix if frac >= 1.0 else None
                    out.append(_pt(t_in + (k + frac) * lap_min, lat, lon, alt, "hold", fix, r.hold.src,
                                   "constraint"))
    return out, t_in


def _clip(wave: Wave, pts: list[dict]) -> list[dict]:
    """Drop the leading points beyond RADAR_NM of the airport reference point and start the track with one
    interpolated `entry` point (`synth`, no fix) on the boundary; t_min, alt_ft and lat/lon are interpolated
    along the segment (bisection on the segment fraction)."""
    a = wave.airport
    k = next((i for i, p in enumerate(pts) if route._nm(p["lat"], p["lon"], a.lat, a.lon) <= RADAR_NM), None)
    if k is None or k == 0:
        return pts
    p, q = pts[k - 1], pts[k]

    def at(f):
        return {key: p[key] + f * (q[key] - p[key]) for key in ("t_min", "lat", "lon", "alt_ft")}

    lo, hi = 0.0, 1.0   # d(lo) > RADAR_NM >= d(hi)
    for _ in range(50):
        mid = (lo + hi) / 2.0
        m = at(mid)
        if route._nm(m["lat"], m["lon"], a.lat, a.lon) > RADAR_NM:
            lo = mid
        else:
            hi = mid
    b = at(hi)
    return [_pt(b["t_min"], b["lat"], b["lon"], b["alt_ft"], "entry", None, "synth", "interp"), *pts[k:]]


def _build(wave: Wave, plan: JointPlan) -> TrajectorySet:
    routes = route.routes_for_wave(wave)
    nominal = _nominal(wave)
    rows = []   # (callsign, is_wave, runway, slot, route or None, laps, lap_min, vector, extra)
    for a in plan.assignments:
        if a.action != "land" or not a.runway or a.slot_min is None or not math.isfinite(a.slot_min):
            continue
        if not any(f.callsign == a.callsign for f in wave.flights):
            continue
        slot = float(a.slot_min)
        r = routes.get((a.callsign, a.runway))
        D = slot - earliest(wave, a.callsign, a.runway, a.speed_reduction_min, a.ground_delay_min)
        if r is None:
            rows.append((a.callsign, True, a.runway, slot, None, 0, None, max(D, 0.0), 0.0))
            continue
        lap = r.hold.lap_min
        laps = int(math.floor(D / lap)) if D > 0 else 0
        vec = D - laps * lap if D > 0 else 0.0
        if not 0.0 <= vec < lap:   # floating point at an exact multiple of the lap
            vec = min(max(vec, 0.0), lap)
        rows.append((a.callsign, True, a.runway, slot, r, laps, lap, vec,
                     _route_extra(wave, a.callsign, a.runway)))
    for q in wave.queue:
        r = routes.get((q.callsign, q.runway))
        rows.append((q.callsign, False, q.runway, float(q.slot_min), r, 0,
                     r.hold.lap_min if r is not None else None, 0.0, 0.0))

    tracks = []   # (row, points, hold entry time)
    for row in rows:
        cs, is_wave, rwy, slot, r, laps, lap, vec, extra = row
        if r is None:
            tracks.append((row, [], None))
        else:
            tracks.append((row, *_assemble(wave, r, slot, laps, lap, vec)))

    # stack per hold fix: only flights with laps >= 1
    groups: dict[str, list] = {}
    for (cs, _, _, _, r, laps, lap, _, _), _, t_in in tracks:
        if r is not None and laps >= 1:
            groups.setdefault(r.hold.fix, []).append((cs, t_in, t_in + laps * lap, r.hold))
    levels: dict[str, int] = {}
    violations: dict[str, list[str]] = {}
    for fix, hs in groups.items():
        hs.sort(key=lambda h: (h[1], h[0]))
        seen, holders, bad = set(), [], []
        for cs, t_in, t_out, _ in hs:
            if cs in seen:
                bad.append(f"{cs} listed twice at {fix}; second entry ignored")
                continue
            if t_out < t_in:
                bad.append(f"{cs} exits {fix} before it enters ({t_out:.1f} < {t_in:.1f} min); ignored")
                continue
            seen.add(cs)
            holders.append((cs, t_in, t_out))
        base = min((h[3] for h in hs), key=lambda h: h.base_ft)
        lv, sbad = holding.stack_levels(holders, base)
        levels.update(lv)
        bad += sbad
        if bad:
            violations[fix] = bad

    air: list[AirTrack] = []
    for (cs, is_wave, rwy, slot, r, laps, lap, vec, extra), pts, t_in in tracks:
        if r is None:
            air.append(AirTrack(cs, is_wave, rwy, slot, [], None, 0.0, 0.0, 0, vec, None, None, None, "none"))
            continue
        level = levels.get(cs) if laps >= 1 else None
        hold = None
        if laps >= 1:
            for p in pts:
                if p["kind"] == "hold" and level is not None:
                    p["alt_ft"] = float(level)
            hold = {"fix": r.hold.fix, "laps": laps, "level_ft": level, "entry_min": t_in,
                    "exit_min": t_in + laps * lap, "lap_min": lap}
        fix_min = pts[0]["t_min"]
        pts = _clip(wave, pts)
        air.append(AirTrack(cs, is_wave, rwy, pts[0]["t_min"], pts, hold, r.t_route_min, extra, laps, vec, lap,
                            r.hold.fix if laps >= 1 else None, level, r.tier, r.entry_fix, fix_min))
    return TrajectorySet(plan.id, ENGINE_VERSION, air, stack_violations=violations)


_CACHE: dict[tuple[str, str], TrajectorySet] = {}


def build(wave: Wave, plan: JointPlan) -> TrajectorySet:
    """Trajectories of every landing in `plan` plus the wave's fixed traffic, memoised on
    (wave content key, plan id). The returned object is shared: treat it as read-only."""
    k = (route.wave_key(wave), plan.id)
    hit = _CACHE.get(k)
    if hit is not None:
        return hit
    ts = _build(wave, plan)
    while len(_CACHE) >= CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[k] = ts
    return ts


def _clear() -> None:
    _CACHE.clear()
    _NOMINAL.clear()
    _LAPS.clear()


build.cache_clear = _clear
