"""FAA CIFP (ARINC 424-18) records: leg parser and per-airport procedure extractor.

Columns are 1-based as published (cycle 2611). This module is pure parsing; it never decides
legality and never touches the network or a database.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

log = logging.getLogger(__name__)

SCRIPT = "fetch_cifp.py/1"
HOLD_PT = {"HF": "HF", "HM": "HM", "HA": "HM"}   # HA (hold to altitude) is a manual-termination hold here


def parse_lat(s: str) -> float:
    """`NddmmssSS` -> degrees, north positive."""
    s = s.strip()
    if len(s) != 9 or s[0] not in "NS":
        raise ValueError(f"bad ARINC latitude {s!r}")
    v = int(s[1:3]) + int(s[3:5]) / 60 + int(s[5:9]) / 360000
    return -v if s[0] == "S" else v


def parse_lon(s: str) -> float:
    """`WdddmmssSS` -> degrees, east positive."""
    s = s.strip()
    if len(s) != 10 or s[0] not in "EW":
        raise ValueError(f"bad ARINC longitude {s!r}")
    v = int(s[1:4]) + int(s[4:6]) / 60 + int(s[6:10]) / 360000
    return -v if s[0] == "W" else v


@dataclass
class Leg:
    seq: int
    fix: str | None
    desc: str
    turn: str | None
    pt: str
    course_mag: float | None
    dist_nm: float | None
    time_min: float | None
    alt_desc: str
    alt1_ft: int | None
    alt2_ft: int | None
    vpa_deg: float | None
    center: str | None
    speed_kt: int | None
    route_type: str
    transition: str


def _c(line: str, a: int, b: int | None = None) -> str:
    """1-based inclusive column slice."""
    return line[a - 1:(b if b is not None else a)]


def _tenths(s: str) -> float | None:
    s = s.strip()
    return int(s) / 10 if s.isdigit() else None


def _alt(s: str) -> int | None:
    s = s.strip()
    if s.startswith("FL") and s[2:].isdigit():
        return int(s[2:]) * 100
    return int(s) if s.isdigit() else None


def parse_leg(line: str) -> Leg:
    pt = _c(line, 48, 49).strip()
    dt = _c(line, 75, 78).strip()
    time_min = dist_nm = None
    if dt.startswith("T") and dt[1:].isdigit():
        time_min = int(dt[1:]) / 10
    else:
        dist_nm = _tenths(dt)
    vpa = _c(line, 103, 106).strip()
    try:
        vpa_deg = abs(int(vpa)) / 100 if vpa else None
    except ValueError:
        vpa_deg = None
    spd = _c(line, 100, 102).strip()
    return Leg(
        seq=int(_c(line, 27, 29)),
        fix=_c(line, 30, 34).strip() or None,
        desc=_c(line, 40, 43),
        turn=_c(line, 44).strip() or None,
        pt=pt,
        course_mag=_tenths(_c(line, 71, 74)),
        dist_nm=dist_nm,
        time_min=time_min,
        alt_desc=_c(line, 83).strip(),
        alt1_ft=_alt(_c(line, 85, 89)),
        alt2_ft=_alt(_c(line, 90, 94)),
        vpa_deg=vpa_deg,
        center=(_c(line, 107, 111).strip() or None) if pt == "RF" else None,
        speed_kt=int(spd) if spd.isdigit() else None,
        route_type=_c(line, 20),
        transition=_c(line, 21, 25).strip(),
    )


# --- fix records -------------------------------------------------------------------------------

def _coords(line: str) -> list[float] | None:
    try:
        return [parse_lat(_c(line, 33, 41)), parse_lon(_c(line, 42, 51))]
    except ValueError:
        return None


def _fix_key(line: str) -> tuple[str, str, str] | None:
    """(ident, region, kind) of an EA / D / DB / PC record, else None."""
    if line[4:6] == "EA":
        return _c(line, 14, 18).strip(), _c(line, 20, 21), "EA"
    if line[4:6] == "D ":
        return _c(line, 14, 17).strip(), _c(line, 20, 21), "D"
    if line[4:6] == "DB":
        return _c(line, 14, 17).strip(), _c(line, 20, 21), "DB"
    if line[4] == "P" and _c(line, 13) == "C":
        return _c(line, 14, 18).strip(), _c(line, 11, 12), "PC"
    return None


def _kind(route_type: str) -> str:
    return "ILS" if route_type == "I" else "RNAV" if route_type in "RH" else "OTHER"


_RWY = re.compile(r"^[A-Z](\d{2}[LRCB]?)")
_RANK = {"ILS": 0, "RNAV": 1, "OTHER": 2}


def extract_airport(lines: Iterable[str], icao: str, cycle: str, fetched_at: str | None = None) -> dict | None:
    """Procedures schema for one airport, or None if the file has no PA record for it."""
    lines = lines if isinstance(lines, list) else list(lines)
    pa: str | None = None
    pg: dict[str, str] = {}
    stars: dict[str, list[str]] = {}
    iaps: dict[str, list[str]] = {}
    pc_here: dict[str, str] = {}
    for ln in lines:
        if len(ln) < 20 or ln[4] != "P" or _c(ln, 7, 10) != icao:
            continue
        sub = _c(ln, 13)
        if sub == "A":
            pa = ln
        elif sub == "G":
            pg[_c(ln, 14, 18).strip()] = ln
        elif sub == "E":
            stars.setdefault(_c(ln, 14, 19).strip(), []).append(ln)
        elif sub == "F":
            iaps.setdefault(_c(ln, 14, 19).strip(), []).append(ln)
        elif sub == "C":
            pc_here[_c(ln, 14, 18).strip()] = ln
    if pa is None:
        return None

    # STARs: RNAV types (4/5/6) override conventional (1/2/3) for the same slot.
    star_out = []
    for sid, recs in stars.items():
        slots: dict[tuple[str, str], tuple[int, list[Leg]]] = {}
        for ln in recs:
            rt = _c(ln, 20)
            if rt not in "123456" or not rt.strip():
                continue
            slot_kind = ("enroute", "common", "runway")[(int(rt) - 1) % 3]
            leg = parse_leg(ln)
            key = (slot_kind, leg.transition)
            prio = 1 if rt in "456" else 0
            cur = slots.get(key)
            if cur is None or cur[0] < prio:
                cur = slots[key] = (prio, [])
            if cur[0] == prio:
                cur[1].append(leg)
        enroute, common, runway = {}, [], {}
        for (k, trans), (_, legs) in slots.items():
            legs.sort(key=lambda g: g.seq)
            if k == "enroute":
                enroute[trans] = legs
            elif k == "common":
                common = legs
            else:
                runway[trans[2:] if trans.startswith("RW") else trans] = legs
        star_out.append({"id": sid, "enroute": enroute, "common": common, "runway": runway})

    # Approaches: one per runway, ILS > RNAV > other.
    best: dict[str, tuple[tuple[int, str], str, str]] = {}
    for pid, recs in iaps.items():
        m = _RWY.match(pid)
        finals = {_c(r, 20) for r in recs if _c(r, 20) != "A"}
        if not m or not finals:
            continue
        kind = min((_kind(rt) for rt in finals), key=_RANK.__getitem__)
        cand = ((_RANK[kind], pid), kind, pid)
        if m.group(1) not in best or cand < best[m.group(1)]:
            best[m.group(1)] = cand
    approaches = {}
    missed: dict[str, list[Leg]] = {}   # not emitted; only its holds and fixes are kept
    for rwy, (_, kind, pid) in sorted(best.items()):
        trans: dict[str, list[Leg]] = {}
        final: list[Leg] = []
        for ln in iaps[pid]:
            leg = parse_leg(ln)
            if leg.route_type == "A":
                trans.setdefault(leg.transition, []).append(leg)
            elif _kind(leg.route_type) == kind:
                final.append(leg)
        for v in trans.values():
            v.sort(key=lambda g: g.seq)
        final.sort(key=lambda g: g.seq)
        # "final" runs to the missed-approach point (desc col 43 = M); the legs after it are the missed approach
        cut = next((i for i, g in enumerate(final) if g.desc[3:4] == "M"), len(final) - 1) + 1
        missed.setdefault(rwy, []).extend(final[cut:])
        approaches[rwy] = {"id": pid, "kind": kind, "transitions": trans, "final": final[:cut]}

    # Holds from kept legs and from the missed approach.
    holds, seen = [], set()
    def collect(proc: str, legs: list[Leg]) -> None:
        for g in legs:
            if g.pt in HOLD_PT and g.fix:
                h = {"fix": g.fix, "inbound_mag": g.course_mag, "turn": g.turn or "R",
                     "leg_min": g.time_min, "leg_nm": g.dist_nm, "alt_ft": g.alt1_ft,
                     "kind": HOLD_PT[g.pt], "proc": proc}
                k = (h["fix"], h["inbound_mag"], h["turn"], proc)
                if k not in seen:
                    seen.add(k)
                    holds.append(h)
    for s in star_out:
        for legs in (*s["enroute"].values(), s["common"], *s["runway"].values()):
            collect(s["id"], legs)
    for rwy, a in approaches.items():
        for legs in (*a["transitions"].values(), a["final"], missed.get(rwy, [])):
            collect(a["id"], legs)

    # Fixes used by kept legs (and by holds).
    def kept_legs():
        for s in star_out:
            yield from (*s["enroute"].values(), s["common"], *s["runway"].values())
        for rwy, a in approaches.items():
            yield from (*a["transitions"].values(), a["final"])
            yield [g for g in missed.get(rwy, []) if g.pt in HOLD_PT]   # a hold needs its fix's coordinates
    refs: dict[str, tuple[str, str]] = {}      # ident -> (region, section+subsection)
    for legs in kept_legs():
        for g in legs:
            for ident in (g.fix, g.center):
                if ident:
                    refs.setdefault(ident, ("", ""))
    # region+section come from the raw leg lines (cols 35-38)
    raw_ref: dict[str, str] = {}
    raw_lines = [ln for recs in (*stars.values(), *iaps.values()) for ln in recs]
    for ln in raw_lines:
        fx = _c(ln, 30, 34).strip()
        if fx in refs:
            raw_ref.setdefault(fx, _c(ln, 35, 38))
        if _c(ln, 48, 49) == "RF":
            ct = _c(ln, 107, 111).strip()
            if ct in refs:
                raw_ref.setdefault(ct, _c(ln, 113, 116))   # centre: ident 107-111, region 113-114, sec/sub 115-116
    runways = {}
    for ident, ln in pg.items():
        rwy = ident[2:] if ident.startswith("RW") else ident
        c = _coords(ln)
        hdg = _tenths(_c(ln, 28, 31))
        if c:
            runways[rwy] = {"lat": c[0], "lon": c[1], "heading_mag": hdg}
    fixes: dict[str, list[float] | None] = {}
    need_scan = {i for i in refs if raw_ref.get(i, "")[2:3] != "G"}
    found: dict[tuple[str, str, str], list[float]] = {}
    for ln in lines:
        if len(ln) < 51 or ln[4] not in "DEP":
            continue
        key = _fix_key(ln)
        if key is None or key[0] not in need_scan:
            continue
        c = _coords(ln)
        if c is None:
            continue
        found.setdefault(key, c)
        if key[2] == "PC" and _c(ln, 7, 10) == icao:
            found[(key[0], "@" + icao, "PC")] = c
    for ident in sorted(refs):
        reg_sec = raw_ref.get(ident, "")
        region, sec = reg_sec[:2], reg_sec[2:4]
        coord = None
        if sec == "PG":
            ln = pg.get(ident)
            coord = _coords(ln) if ln else None
        elif sec == "PA":   # the airport reference point used as a fix
            coord = _coords(pa) if ident == icao else None
        elif sec == "PC":
            coord = found.get((ident, "@" + icao, "PC")) or found.get((ident, region, "PC"))
        elif sec == "EA":
            coord = found.get((ident, region, "EA"))
        elif sec[:1] == "D":
            coord = found.get((ident, region, "DB" if sec == "DB" else "D"))
        if coord is None:
            log.warning("CIFP %s: fix %s (%s) has no record", icao, ident, reg_sec)
        fixes[ident] = coord

    mv = _c(pa, 52, 56)
    magvar = None
    if len(mv) == 5 and mv[0] in "EW" and mv[1:].isdigit():
        magvar = int(mv[1:]) / 10 * (1 if mv[0] == "E" else -1)
    elev = _c(pa, 57, 61).strip()
    lat, lon = _coords(pa) or (None, None)
    tier = "star+iap" if star_out and approaches else "iap" if approaches else "none"
    return {
        "header": {"source": "FAA CIFP", "cycle": cycle, "script": SCRIPT,
                   "fetched_at": fetched_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "tier": tier},
        "airport": {"icao": icao, "lat": lat, "lon": lon,
                    "elev_ft": int(elev) if elev.lstrip("-").isdigit() else None, "magvar_deg": magvar},
        "fixes": fixes,
        "runways": runways,
        "stars": [_ser(s) for s in star_out],
        "approaches": {r: _ser(a) for r, a in approaches.items()},
        "holds": holds,
    }


def _ser(o):
    if isinstance(o, Leg):
        return asdict(o)
    if isinstance(o, dict):
        return {k: _ser(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_ser(v) for v in o]
    return o
