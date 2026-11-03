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
  * a slot at exactly `earliest + k x lap_min` (the `hold` move) gives k laps and no stretch (D / lap_min
    is snapped by 1e-9 before the floor).
  * hold per physical fix per wave (`wave_holds`, plan-independent, memoised on the wave key): one hold
    per fix (name + position to 0.001 deg) over ALL the wave's routes (wave and fixed traffic) that hold
    there; geometry from a published (cifp) hold if any route has one, else from the route with the smallest
    (runway, callsign); base = the highest published-constraint base if any route has one there, else the
    lowest of the routes' (interpolated or corner-post) bases, an interpolated one floored at 4,000 ft AGL
    (arrivals are never climbed into a hold; `_shared_hold`). Every flight holding there uses it: its
    `lap_min` (base level) for the laps/vector split, the drawn racetrack, and the stack levels. So a
    flight's lap time never depends on who else holds in the plan.
  * stack (§6.3): only flights with laps >= 1 enter it; levels come from `holding.stack_levels`; its
    violations (more than 5 levels, an exit that is not bottom-first) are reported in
    `TrajectorySet.stack_violations`, never raised. Laps are drawn at the level assigned on entry (the
    step-down is not drawn).
  * fixed traffic (queue arrivals): `wave=False`, zero laps, the nominal route timed to land at its slot.
Nothing here decides legality: the rules read these objects.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

from atm.wave import holding, route
from atm.wave.model import JointPlan, Wave
from atm.wave.version import ENGINE_VERSION

CACHE_MAX = 4096
MIN_VECTOR_DRAW_MIN = 1e-6     # below this the stretch is kept in vector_min but not drawn
INTERP_FLOOR_AGL_FT = 4000.0   # lowest shared base at a fix whose bases are interpolated route altitudes
RADAR_NM = 150.0               # drawn points stay within this of the airport reference point (spec §12)


@dataclass
class AirTrack:
    """One flight's airborne path for a plan.

    `points` are dicts {t_min, lat, lon, alt_ft, kind, fix?, src, alt_src}. `lap_min` is the lap time of
    the wave's shared hold at the flight's hold fix (`wave_holds`), at its base level (`HoldSpec.lap_min`),
    used for every level of the stack; it is set whenever
    the flight has a route (even with zero laps) and None in the no-geometry tier, where `points` is empty.
    `hold` (contract shape) and `hold_fix` / `hold_level_ft` are set only when laps >= 1 and the stack gave
    the flight a level (otherwise a note says why).
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
    notes: list[str] = field(default_factory=list)   # drawing caveats (clip, stack), not serialised

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

_NOMINAL: dict[str, dict[str, str | None]] = {}
_LOCK = threading.Lock()   # FIFO eviction of the shared caches (the benchmark negotiates in a thread pool)


def _put(cache: dict, k, v) -> None:
    """Insert into a shared FIFO cache of at most CACHE_MAX entries, thread-safely."""
    with _LOCK:
        while len(cache) >= CACHE_MAX:
            cache.pop(next(iter(cache)), None)
        cache[k] = v


def _nominal(wave: Wave) -> dict[str, str | None]:
    """callsign -> nominal runway: the FCFS runway for wave flights, the queue runway for fixed traffic."""
    k = route.wave_key(wave)
    hit = _NOMINAL.get(k)
    if hit is None:
        from atm.wave import moves   # lazy: moves -> utility -> trajectory would cycle
        hit = {q.callsign: q.runway for q in wave.queue}
        hit.update(moves.fcfs_runways(wave))
        _put(_NOMINAL, k, hit)
    return hit


def _t_route(wave: Wave, callsign: str, runway: str | None) -> float | None:
    r = route.routes_for_wave(wave).get((callsign, runway)) if runway else None
    return r.t_route_min if r is not None else None


def _wave_flight(wave: Wave, callsign: str):
    f = next((x for x in wave.flights if x.callsign == callsign), None)
    if f is None:
        raise ValueError(f"{callsign} is not a wave flight of {wave.id}")
    return f


def entry_time(wave: Wave, callsign: str) -> float | None:
    """`eta_earliest_min - T_route(nominal)`; None without geometry for the nominal runway.
    Raises ValueError when `callsign` is not a wave flight (queue traffic has no earliest time)."""
    f = _wave_flight(wave, callsign)
    tn = _t_route(wave, callsign, _nominal(wave).get(callsign))
    return None if tn is None else f.eta_earliest_min - tn


def _route_extra(wave: Wave, callsign: str, runway: str | None) -> float:
    tn = _t_route(wave, callsign, _nominal(wave).get(callsign))
    tr = _t_route(wave, callsign, runway)
    return 0.0 if tn is None or tr is None else tr - tn


def earliest(wave: Wave, callsign: str, runway: str, sr: float = 0.0, gd: float = 0.0) -> float:
    """Earliest touchdown on `runway`: `t_entry + sr + gd + T_route(runway)`; `eta_earliest_min + sr + gd`
    on the nominal runway and in the no-geometry tier. Raises ValueError when `callsign` is not a wave
    flight (like `entry_time`)."""
    f = _wave_flight(wave, callsign)
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


def _i_final(r: route.Route) -> int:
    """Index of the first final-approach point (the vector stretch is inserted before it)."""
    return next((i for i, p in enumerate(r.points) if p.kind in ("final", "threshold") and i > 0),
                len(r.points) - 1)


def _hold_entry(r: route.Route, slot: float, laps: int, lap_min: float, vector_min: float) -> float:
    """Time the flight reaches its hold fix (as drawn by `_assemble`)."""
    appears = slot - (r.t_route_min + laps * lap_min + vector_min)
    return appears + r.points[r.hold_index].t_min + (vector_min if r.hold_index >= _i_final(r) else 0.0)


def _assemble(wave: Wave, r: route.Route, slot: float, laps: int, lap_min: float, vector_min: float,
              draw: holding.HoldSpec, level_ft: float) -> list[dict]:
    """Points of route `r` with `laps` laps (each `lap_min`) at the hold fix and `vector_min` of stretch
    before the final, timed so the threshold is crossed at `slot`. Laps fly the racetrack of `draw` (the
    stack's shared hold) at `level_ft`. After the laps no later point is drawn above `level_ft`: route
    altitudes are capped at it (alt_src "interp") until the route itself falls below it."""
    pts = r.points
    n = len(pts)
    hold_min = laps * lap_min
    appears = slot - (r.t_route_min + hold_min + vector_min)
    hi = r.hold_index
    i_fin = _i_final(r)
    draw_vec = vector_min > MIN_VECTOR_DRAW_MIN
    out: list[dict] = []
    cap: float | None = None          # the hold level, while the route after the hold is still above it
    for i, p in enumerate(pts):
        off = (hold_min if i > hi else 0.0) + (vector_min if i >= i_fin else 0.0)
        if i == i_fin and draw_vec:
            q = pts[i - 1]
            t0 = appears + q.t_min + (hold_min if i - 1 >= hi else 0.0)
            gs = holding.tas_kt(route.IAS_IAF, q.alt_ft)
            hdg = (wave.runway(r.runway).heading_true + 180.0) % 360.0
            v = route._dest(q.lat, q.lon, hdg, gs * vector_min / 2.0 / 60.0)
            q_alt = q.alt_ft if cap is None else min(q.alt_ft, cap)
            out.append(_pt(t0 + vector_min / 2.0, v[0], v[1], q_alt, "vector", None, "synth", "interp"))
            out.append(_pt(t0 + vector_min, q.lat, q.lon, q_alt, "vector", None, "synth", "interp"))
        t = slot if i == n - 1 else appears + p.t_min + off
        alt, alt_src = p.alt_ft, p.alt_src
        if cap is not None:
            if alt > cap:
                alt, alt_src = cap, "interp"
            else:
                cap = None
        out.append(_pt(t, p.lat, p.lon, alt, p.kind, p.fix, p.src, alt_src))
        if i == hi and laps > 0:
            cap = float(level_ft)
            for k in range(laps):
                for lat, lon, frac in _lap_cached(draw):
                    fix = draw.fix if frac >= 1.0 else None
                    out.append(_pt(t + (k + frac) * lap_min, lat, lon, float(level_ft), "hold", fix, draw.src,
                                   "constraint"))
    return out


def _clip(wave: Wave, pts: list[dict]) -> tuple[list[dict], str | None]:
    """Drop the leading points beyond RADAR_NM of the airport reference point and start the track with one
    interpolated `entry` point (`synth`, no fix) on the boundary; t_min, alt_ft and lat/lon are interpolated
    along the segment (bisection on the segment fraction). Returns (points, note); when no point is within
    RADAR_NM the points are returned unclipped with a note."""
    a = wave.airport
    k = next((i for i, p in enumerate(pts) if route._nm(p["lat"], p["lon"], a.lat, a.lon) <= RADAR_NM), None)
    if k is None:
        return pts, f"no point within {RADAR_NM:.0f} NM of the airport reference point; track not clipped"
    if k == 0:
        return pts, None
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
    return [_pt(b["t_min"], b["lat"], b["lon"], b["alt_ft"], "entry", None, "synth", "interp"), *pts[k:]], None


def stack_key(h: holding.HoldSpec) -> tuple:
    """One stack per physical fix: name plus position rounded to 0.001 deg (~0.06 NM); straight-in holds are
    all named "HOLD" and corner posts "CPnnn" while sitting at different places."""
    return (h.fix, round(h.lat, 3), round(h.lon, 3))


_stack_key = stack_key   # alias kept for existing callers (tests/test_trajectory.py)


def stack_labels(keys) -> dict[tuple, str]:
    """Display label per physical stack key: the fix name, or `NAME@lat,lon` when several keys in `keys`
    share a name. Used for `TrajectorySet.stack_violations` and by ATC-HOLD-STACK."""
    keys = list(keys)
    n: dict[str, int] = {}
    for k in keys:
        n[k[0]] = n.get(k[0], 0) + 1
    return {k: k[0] if n[k[0]] == 1 else f"{k[0]}@{k[1]:.3f},{k[2]:.3f}" for k in keys}


def _shared_hold(members: list[tuple[str, str, holding.HoldSpec]], elev_ft: float = 0.0) -> holding.HoldSpec:
    """One fix's hold from the (runway, callsign, HoldSpec) of every route holding there.

    Geometry (inbound course, turn, leg): a published (cifp) hold if any has one, else the route with the
    smallest (runway, callsign). Base: if any route's base is a published altitude constraint at the fix,
    the highest constraint base; otherwise the lowest of the routes' bases (arrivals are never climbed into
    a hold), floored at `elev_ft` + 4,000 ft (rounded up to 100 ft) when any of them is an interpolated
    route altitude (guards an anomalous low route). Corner posts already sit at their IAF / 6,000 ft AGL
    base."""
    ms = sorted(members, key=lambda m: (m[0], m[1]))
    ref = next((m[2] for m in ms if m[2].src == "cifp"), ms[0][2])
    cons = [m[2].base_ft for m in ms if m[2].base_src == "constraint"]
    if cons:
        base, base_src = max(cons), "constraint"
    else:
        base = min(m[2].base_ft for m in ms)
        base_src = "interp" if any(m[2].base_src == "interp" for m in ms) else "corner"
        if base_src == "interp":
            base = max(base, int(math.ceil((elev_ft + INTERP_FLOOR_AGL_FT) / 100.0) * 100))
    if base == ref.base_ft and base_src == ref.base_src:
        return ref
    return holding.make_hold(ref.fix, ref.lat, ref.lon, ref.inbound_true, ref.turn,
                             ref.leg_min if ref.src == "cifp" else None, base, ref.src, base_src)


_HOLDS: dict[str, dict[tuple, holding.HoldSpec]] = {}


def wave_holds(wave: Wave) -> dict[tuple, holding.HoldSpec]:
    """Physical hold fix key (`stack_key`) -> the wave's shared hold there (see the module docstring).
    Plan-independent; memoised on the wave key. Treat as read-only."""
    k = route.wave_key(wave)
    hit = _HOLDS.get(k)
    if hit is None:
        members: dict[tuple, list] = {}
        elev = 0.0
        for (cs, rwy), r in route.routes_for_wave(wave).items():
            members.setdefault(stack_key(r.hold), []).append((rwy, cs, r.hold))
            elev = r.elev_ft
        hit = {key: _shared_hold(ms, elev) for key, ms in members.items()}
        _put(_HOLDS, k, hit)
    return hit


def hold_for(wave: Wave, r: route.Route) -> holding.HoldSpec:
    """The wave's shared hold at route `r`'s hold fix."""
    return wave_holds(wave)[stack_key(r.hold)]


def _build(wave: Wave, plan: JointPlan) -> TrajectorySet:
    routes = route.routes_for_wave(wave)
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
        lap = hold_for(wave, r).lap_min
        # snap: a slot at exactly earliest + k laps (the hold move) gives k laps, not k-1 plus a full lap
        laps = int(math.floor(D / lap + 1e-9)) if D > 0 else 0
        vec = max(D - laps * lap, 0.0) if D > 0 else 0.0
        rows.append((a.callsign, True, a.runway, slot, r, laps, lap, vec,
                     _route_extra(wave, a.callsign, a.runway)))
    for q in wave.queue:
        r = routes.get((q.callsign, q.runway))
        rows.append((q.callsign, False, q.runway, float(q.slot_min), r, 0,
                     hold_for(wave, r).lap_min if r is not None else None, 0.0, 0.0))

    # stack per physical hold fix: only flights with laps >= 1
    groups: dict[tuple, list] = {}
    for cs, _, rwy, slot, r, laps, lap, vec, _ in rows:
        if r is not None and laps >= 1:
            t_in = _hold_entry(r, slot, laps, lap, vec)
            groups.setdefault(stack_key(r.hold), []).append((cs, t_in, t_in + laps * lap))
    labels = stack_labels(groups)
    levels: dict[str, int] = {}
    entry: dict[str, float] = {}
    violations: dict[str, list[str]] = {}
    for key, hs in groups.items():
        fix = labels[key]
        spec = wave_holds(wave)[key]
        hs.sort(key=lambda h: (h[1], h[0]))
        seen, holders, bad = set(), [], []
        for cs, t_in, t_out in hs:
            if cs in seen:
                bad.append(f"{cs} listed twice at {fix}; second entry ignored")
                continue
            if t_out < t_in:
                bad.append(f"{cs} exits {fix} before it enters ({t_out:.1f} < {t_in:.1f} min); ignored")
                continue
            seen.add(cs)
            holders.append((cs, t_in, t_out))
            entry[cs] = t_in
        lv, sbad = holding.stack_levels(holders, spec)
        levels.update(lv)
        bad += sbad
        if bad:
            violations[fix] = bad

    air: list[AirTrack] = []
    for cs, is_wave, rwy, slot, r, laps, lap, vec, extra in rows:
        if r is None:
            air.append(AirTrack(cs, is_wave, rwy, slot, [], None, 0.0, 0.0, 0, vec, None, None, None, "none"))
            continue
        notes: list[str] = []
        draw = hold_for(wave, r)
        level = levels.get(cs) if laps >= 1 else None
        hold = None
        if laps >= 1:
            if level is None:
                notes.append(f"{cs}: {laps} laps at {draw.fix} but no stack level assigned; hold not reported")
            else:
                hold = {"fix": draw.fix, "laps": laps, "level_ft": level, "entry_min": entry[cs],
                        "exit_min": entry[cs] + laps * lap, "lap_min": lap}
        pts = _assemble(wave, r, slot, laps, lap, vec, draw, level if level is not None else draw.base_ft)
        fix_min = pts[0]["t_min"]
        pts, note = _clip(wave, pts)
        if note:
            notes.append(note)
        a = wave.airport
        if route._nm(r.hold.lat, r.hold.lon, a.lat, a.lon) > RADAR_NM:
            notes.append(f"hold fix {r.hold.fix} is beyond {RADAR_NM:.0f} NM of the airport reference point")
        air.append(AirTrack(cs, is_wave, rwy, pts[0]["t_min"], pts, hold, r.t_route_min, extra, laps, vec, lap,
                            draw.fix if hold is not None else None, level if hold is not None else None,
                            r.tier, r.entry_fix, fix_min, notes))
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
    _put(_CACHE, k, ts)
    return ts


def _clear() -> None:
    _CACHE.clear()
    _NOMINAL.clear()
    _LAPS.clear()
    _HOLDS.clear()


build.cache_clear = _clear
