"""Holding-pattern geometry (AIM 5-3-8): racetrack, lap time, speeds, stack levels.

Pure functions: no store, no network. Pinned physics (global constraints):
IAS 200 kt <= 6,000 ft, 230 kt to 14,000 ft, 265 kt above; inbound leg 1.0 min
<= 14,000 ft, 1.5 min above; TAS = IAS x (1 + 0.02 x alt/1000); turn rate is the
least of 3 deg/s, 30 deg bank and 25 deg bank. Stack: 1,000 ft levels, 5 levels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

G = 9.80665
KT_MS = 1852.0 / 3600.0
STACK_LEVELS = 5
LEVEL_STEP_FT = 1000


def tas_kt(ias_kt: float, alt_ft: float) -> float:
    return ias_kt * (1.0 + 0.02 * alt_ft / 1000.0)


def holding_ias_kt(alt_ft: float) -> float:
    if alt_ft <= 6000:
        return 200.0
    if alt_ft <= 14000:
        return 230.0
    return 265.0


def leg_min_for(alt_ft: float) -> float:
    return 1.0 if alt_ft <= 14000 else 1.5


def turn_rate_dps(tas: float) -> float:
    v = max(tas, 1.0) * KT_MS
    bank = [math.degrees(G * math.tan(math.radians(b)) / v) for b in (30.0, 25.0)]
    return min([3.0] + bank)


def lap_min(alt_ft: float, leg_min: float | None = None) -> float:
    leg = leg_min_for(alt_ft) if leg_min is None else leg_min
    tas = tas_kt(holding_ias_kt(alt_ft), alt_ft)
    return 2.0 * leg + 2.0 * (180.0 / turn_rate_dps(tas)) / 60.0


@dataclass
class HoldSpec:
    fix: str
    lat: float
    lon: float
    inbound_true: float
    turn: Literal["R", "L"]
    leg_min: float
    base_ft: int
    levels_ft: list[int]
    lap_min: float
    src: Literal["cifp", "synth"]


def make_hold(fix, lat, lon, inbound_true, turn, leg_min, base_ft, src) -> HoldSpec:
    """Build a hold; a null leg time (e.g. CIFP HA holds) defaults to the AIM value."""
    leg = leg_min_for(base_ft) if leg_min is None else float(leg_min)
    return HoldSpec(
        fix=fix, lat=lat, lon=lon, inbound_true=inbound_true, turn=turn,
        leg_min=leg, base_ft=int(base_ft),
        levels_ft=[int(base_ft) + k * LEVEL_STEP_FT for k in range(STACK_LEVELS)],
        lap_min=lap_min(base_ft, leg), src=src,
    )


def racetrack(h: HoldSpec, step_deg: float = 15.0) -> list[tuple[float, float]]:
    """Closed racetrack polygon (lat, lon), sized at the base level.

    The fix is the end of the inbound leg; turns go to the right of the inbound
    course for "R" and to the left for "L".
    """
    tas = tas_kt(holding_ias_kt(h.base_ft), h.base_ft)
    r = tas / 3600.0 / math.radians(turn_rate_dps(tas))  # NM
    length = h.leg_min * tas / 60.0                      # NM
    th = math.radians(h.inbound_true)
    d = (math.sin(th), math.cos(th))
    s = 1.0 if h.turn == "R" else -1.0
    n = (s * math.cos(th), -s * math.sin(th))            # side the turns go to
    n_steps = max(2, int(round(180.0 / step_deg)))

    def pt(ox, oy, a, b):  # origin + a*n + b*d
        return (ox + a * n[0] + b * d[0], oy + a * n[1] + b * d[1])

    pts = [(-length * d[0], -length * d[1]), (0.0, 0.0)]
    c1 = (r * n[0], r * n[1])
    for i in range(1, n_steps + 1):
        phi = math.radians(180.0 * i / n_steps)
        pts.append(pt(*c1, -r * math.cos(phi), r * math.sin(phi)))
    c2 = (r * n[0] - length * d[0], r * n[1] - length * d[1])
    for i in range(1, n_steps + 1):
        phi = math.radians(180.0 * i / n_steps)
        pts.append(pt(*c2, r * math.cos(phi), -r * math.sin(phi)))
    pts[-1] = pts[0]
    kx = 60.0 * max(math.cos(math.radians(h.lat)), 1e-6)
    return [(h.lat + y / 60.0, h.lon + x / kx) for x, y in pts]


def stack_levels(holders, h: HoldSpec):
    """Assign levels FIFO from the bottom; occupants above step down as lower ones leave.

    holders are (callsign, entry_min, exit_min). Returns (callsign -> level at entry,
    violations). Violations: more than 5 holders at once, or an exit that is not
    bottom-first.
    """
    events = []
    for cs, t_in, t_out in holders:
        events.append((t_out, 0, cs))   # exits sort before entries at equal time
        events.append((t_in, 1, cs))
    events.sort(key=lambda e: (e[0], e[1]))
    exits = {cs: t_out for cs, _, t_out in holders}
    occ: list[str] = []
    levels: dict[str, int] = {}
    bad: list[str] = []
    for t, kind, cs in events:
        if kind == 1:
            levels[cs] = h.base_ft + len(occ) * LEVEL_STEP_FT
            occ.append(cs)
            if len(occ) > STACK_LEVELS:
                bad.append(f"{cs} enters at {t:.1f} min: {len(occ)} > {STACK_LEVELS} levels at {h.fix}")
        elif cs in occ:
            i = occ.index(cs)
            if i != 0:
                bad.append(
                    f"{cs} exits at {t:.1f} min above {occ[0]} (exits {exits[occ[0]]:.1f} min): not bottom-first"
                )
            occ.pop(i)
    return levels, bad
