"""Arrival routes per (flight, runway end): entry -> STAR -> approach -> threshold (spec §6.2, §6.3).

Built once per wave at wave-build time from the CIFP procedures document (`store.load_procedures`), with
synthetic fallbacks: vectors (downwind 5 NM abeam, base, 30 deg intercept) when a STAR does not end at an
approach IAF/IF, a corner-post entry (40 NM at 045/135/225/315 true) when the airport has no STAR, and a
10 NM 3 deg straight-in when the runway end has no approach. Every point carries `src` ("cifp" or "synth")
and `alt_src` ("constraint" or "interp"). Times come from the terminal speed profile (global constraints)
converted to TAS with `holding.tas_kt`. CIFP altitude constraints are treated as exact altitudes
("+"/"-" at-or-above/below use the published value; "B" between uses the mean). Pure geometry: nothing here decides legality, and the rules,
utility and negotiation layers read routes from the cache, never the store.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Literal

from atm.geo import store
from atm.wave import holding
from atm.wave.holding import HoldSpec
from atm.wave.model import Runway, Wave

NM_FT = 6076.12
EARTH_NM = 3440.065
CORNER_NM = 40.0                       # synthetic corner post distance from the threshold
CORNERS = (45.0, 135.0, 225.0, 315.0)  # corner-post bearings, true
DOWNWIND_NM = 5.0                      # downwind abeam distance
INTERCEPT_DEG = 30.0                   # intercept angle onto the final course
INTERCEPT_OFFSET_NM = 2.5              # lateral offset where base turns onto the intercept heading (assumption)
FAF_JOIN_NM = 2.0                      # join 2 NM before the FAF when the approach has no IF
STRAIGHT_IN_NM = 10.0
GLIDE_DEG = 3.0
TCH_FT = 50.0                          # threshold crossing height when CIFP has no runway fix altitude
HOLD_AGL_FT = 6000.0                   # default hold base above elevation
ENTRY_PROFILE_CAP_FT = 18000.0         # cap on the 3 deg profile for an unconstrained entry (US transition
                                       # altitude); a higher downstream constraint still wins (e.g. FL340)
CIFP_CENTRELINE_XTRACK_NM = 0.1        # a CIFP runway end must sit on the wave end's centreline,
CIFP_CENTRELINE_OUT_NM = 1.0           # at most this far out from the wave threshold,
CIFP_HDG_TOL_DEG = 30.0                # and point within this of the wave heading, else it is not used
DESCENT_FT_PER_NM = math.tan(math.radians(GLIDE_DEG)) * NM_FT   # ~318 ft/NM
IAS_HIGH, IAS_LOW, IAS_IAF, IAS_FAF, IAS_FINAL = 280.0, 250.0, 210.0, 170.0, 140.0
MIN_GS_KT = 60.0
RF_STEP_DEG = 10.0
CACHE_MAX = 16

HOLD_PT = ("HF", "HM", "HA")
VECTOR_PT = ("FM", "VM")
IAF_CODES = "ACD"    # waypoint description col 43: A/C/D initial approach fix
IF_CODES = "IB"      # I intermediate fix, B intermediate fix (combined code)

Kind = Literal["entry", "fix", "vector", "final", "threshold"]


@dataclass
class RoutePoint:
    lat: float
    lon: float
    alt_ft: float
    kind: str                         # entry|fix|vector|final|threshold
    fix: str | None
    src: Literal["cifp", "synth"]
    alt_src: Literal["constraint", "interp"]
    t_min: float                      # time from entry


@dataclass
class Route:
    callsign: str
    runway: str
    star: str | None
    transition: str | None
    approach: str | None
    entry_fix: str
    points: list[RoutePoint]
    t_route_min: float
    hold: HoldSpec
    hold_index: int
    tier: Literal["star+iap", "iap", "straight-in"]
    notes: list[str] = field(default_factory=list)   # why a fallback was used (runway mismatch, chorded RF)


# --------------------------------------------------------------------------- geometry

def _nm(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_NM * math.asin(min(1.0, math.sqrt(a)))


def _bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def _dest(lat, lon, brg, dist_nm) -> tuple[float, float]:
    d = dist_nm / EARTH_NM
    th, p1, l1 = math.radians(brg), math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(th))
    l2 = l1 + math.atan2(math.sin(th) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def _angdiff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class _Frame:
    """Local flat frame at a runway threshold: x = NM out along the extended centreline (positive on the
    approach side), y = NM to the right of the landing direction."""

    def __init__(self, lat, lon, course_true):
        self.lat, self.lon = lat, lon
        self.k = 60.0 * max(math.cos(math.radians(lat)), 1e-6)
        c = math.radians(course_true)
        self.u = (math.sin(c), math.cos(c))     # landing direction (east, north)
        self.n = (math.cos(c), -math.sin(c))    # right of the landing direction

    def xy(self, lat, lon) -> tuple[float, float]:
        e, n = (lon - self.lon) * self.k, (lat - self.lat) * 60.0
        return -(e * self.u[0] + n * self.u[1]), e * self.n[0] + n * self.n[1]

    def ll(self, x, y) -> tuple[float, float]:
        e = -x * self.u[0] + y * self.n[0]
        n = -x * self.u[1] + y * self.n[1]
        return self.lat + n / 60.0, self.lon + e / self.k


# --------------------------------------------------------------------------- procedure helpers

def _norm_rwy(s: str) -> str:
    s = str(s).strip().upper()
    if s.startswith("RW"):
        s = s[2:]
    if s[:1].isdigit() and not s[1:2].isdigit():
        s = "0" + s
    return s


def _rwy_key_matches(key: str, runway: str) -> bool:
    """A STAR runway-transition or approach key serves `runway`: exact, "ALL", or "NNB" (both/all parallels)."""
    k, r = _norm_rwy(key), _norm_rwy(runway)
    if k in ("ALL", r):
        return True
    return len(k) == 3 and k.endswith("B") and k[:2] == r[:2]


def _pick_key(keys, runway: str) -> str | None:
    """Exact runway match first, then "NNB", then "ALL"."""
    keys = sorted(keys)
    r = _norm_rwy(runway)
    for pred in (lambda k: _norm_rwy(k) == r,
                 lambda k: _norm_rwy(k) != "ALL" and _rwy_key_matches(k, runway),
                 lambda k: _norm_rwy(k) == "ALL"):
        hit = next((k for k in keys if pred(k)), None)
        if hit is not None:
            return hit
    return None


def _coord(fixes: dict, ident: str | None) -> tuple[float, float] | None:
    c = fixes.get(ident) if ident else None
    return (float(c[0]), float(c[1])) if c else None


def _first_fix(legs: list[dict], fixes: dict) -> tuple[str, tuple[float, float]] | None:
    for g in legs:
        c = _coord(fixes, g.get("fix"))
        if c is not None:
            return g["fix"], c
    return None


def _leg_alt(g: dict) -> float | None:
    a1, a2 = g.get("alt1_ft"), g.get("alt2_ft")
    if a1 is None:
        return None
    if g.get("alt_desc") == "B" and a2 is not None:
        return (a1 + a2) / 2.0
    return float(a1)


def _iaf(g: dict) -> bool:
    return (g.get("desc") or "    ")[3:4] in IAF_CODES


def _desc(g: dict, i: int) -> str:
    return (g.get("desc") or "    ")[i:i + 1]


def _star_ref(proc: dict) -> tuple[float, float] | None:
    a = proc.get("airport") or {}
    if a.get("lat") is not None and a.get("lon") is not None:
        return float(a["lat"]), float(a["lon"])
    rw = next(iter((proc.get("runways") or {}).values()), None)
    return (float(rw["lat"]), float(rw["lon"])) if rw else None


def select_star(proc: dict, origin_latlon: tuple[float, float] | None, runway: str) -> tuple[str, str] | None:
    """(star_id, enroute transition) for an arrival from `origin_latlon` to `runway`, or None (no STARs).

    Candidates are the STARs with a runway transition for `runway` plus those without runway transitions
    (their common route serves every runway); all STARs if none qualify. With an origin, the transition
    whose first fix lies closest in bearing (from the airport) to the origin wins; ties go to the lower
    STAR id, then the lower transition name. Without one, the first STAR by id with a transition for the
    runway is used, with its first transition by name. The transition is "" when a STAR has none."""
    stars = sorted((proc or {}).get("stars") or [], key=lambda s: s["id"])
    if not stars:
        return None
    fixes = proc.get("fixes") or {}
    explicit = [s for s in stars if _pick_key(s.get("runway") or {}, runway) is not None]
    open_rw = [s for s in stars if not s.get("runway")]

    def entries(s):
        out = []
        for t in sorted(s.get("enroute") or {}):
            ff = _first_fix(s["enroute"][t], fixes)
            if ff is not None:
                out.append((t, ff[1]))
        if not out:
            ff = _first_fix(s.get("common") or [], fixes)
            if ff is not None:
                out.append(("", ff[1]))
        return out

    ref = _star_ref(proc)
    if origin_latlon is None or ref is None:
        for s in explicit or open_rw or stars:
            e = entries(s)
            return s["id"], (e[0][0] if e else "")
    pool = (explicit + open_rw) or stars
    want = _bearing(ref[0], ref[1], *origin_latlon)
    best = None
    for s in pool:
        for t, c in entries(s):
            key = (round(_angdiff(_bearing(ref[0], ref[1], *c), want), 6), s["id"], t)
            if best is None or key < best:
                best = key
    if best is None:
        s = pool[0]
        return s["id"], ""
    return best[1], best[2]


def _current_id(wave: Wave, runway: str) -> str | None:
    """The runway end's designator today (the CIFP cycle's), applying `renamed` events after the wave date:
    the wave keeps its dated designator (e.g. KAUS 17L in 2015), CIFP 2611 uses the current one (18L).
    None when the dated end was closed later, or its name was later given to another runway (KMDW 13L in
    2015 is closed today; today's 13L is the old 13C): today's procedures for that name are not its own."""
    from atm.wave import build   # lazy: build imports the wave modules
    date = wave.window_start[:10]
    later = sorted((h for h in build._runway_history().get(wave.airport.iata, []) if date < h["date"]),
                   key=lambda h: h["date"])
    cur = runway
    for h in later:
        if h["event"] == "closed" and _norm_rwy(h["runway"]) == _norm_rwy(cur):
            return None
        if h["event"] == "renamed":
            if _norm_rwy(h["old_id"]) == _norm_rwy(cur):
                cur = h["runway"]
            elif _norm_rwy(h["runway"]) == _norm_rwy(cur):
                return None
    return cur


def _cifp_end_check(proc: dict, pid: str | None, rw: Runway) -> str | None:
    """None if CIFP runway end `pid` is the wave's physical end `rw`, else the reason it is not used.

    The CIFP threshold must lie on the wave end's centreline (<= 0.1 NM cross-track; measured over 1,226
    current ends: p99 0.021 NM, while ORD 10L/10C thresholds are 0.2 NM apart), between the far end and
    1 NM out (a displaced threshold, or a runway extended since the wave date: KORD 09R is 0.59 NM out in
    2015), with a true heading (heading_mag + magvar) within 30 deg."""
    if pid is None:
        return f"runway {rw.id}: closed or renamed after the wave date; today's procedures do not apply"
    c = next((v for k, v in (proc.get("runways") or {}).items() if _norm_rwy(k) == _norm_rwy(pid)), None)
    if c is None or c.get("lat") is None or c.get("lon") is None:
        return f"runway {rw.id}: CIFP has no runway end {pid}"
    x, y = _Frame(float(rw.le_lat), float(rw.le_lon), rw.heading_true).xy(float(c["lat"]), float(c["lon"]))
    length_nm = (rw.length_ft or 0) / NM_FT
    if abs(y) > CIFP_CENTRELINE_XTRACK_NM or not (-length_nm <= x <= CIFP_CENTRELINE_OUT_NM):
        d = _nm(float(c["lat"]), float(c["lon"]), float(rw.le_lat), float(rw.le_lon))
        return (f"runway {rw.id}: CIFP {pid} threshold is {d:.2f} NM from the wave threshold "
                f"({x:+.2f} NM along, {y:+.2f} NM across the centreline)")
    if c.get("heading_mag") is not None:
        mv = float(((proc.get("airport") or {}).get("magvar_deg")) or 0.0)
        dh = _angdiff(float(c["heading_mag"]) + mv, rw.heading_true)
        if dh > CIFP_HDG_TOL_DEG:
            return f"runway {rw.id}: CIFP {pid} heading differs by {dh:.0f} deg"
    return None


def _approach(proc: dict | None, runway: str) -> dict | None:
    apps = (proc or {}).get("approaches") or {}
    r = _norm_rwy(runway)
    hit = next((k for k in sorted(apps) if _norm_rwy(k) == r), None)
    if hit is None:
        hit = next((k for k in sorted(apps) if _rwy_key_matches(k, runway) and _norm_rwy(k) != "ALL"), None)
    return apps.get(hit) if hit is not None else None


def rf_arc(start: tuple[float, float], end: tuple[float, float], center: tuple[float, float] | None,
           end_course_true: float | None, turn: str) -> list[tuple[float, float]] | None:
    """Intermediate points (every 10 deg) of an RF arc from `start` to `end` turning `turn` ("R"/"L").

    The centre is the CIFP centre fix when known; otherwise it is derived from the course at the end of the
    leg (the arc's tangent there): the centre lies on the perpendicular to that course at `end`, on the turn
    side, at the radius that puts `start` on the same circle. None when neither is possible."""
    if center is None:
        if end_course_true is None:
            return None
        f = _Frame(end[0], end[1], 0.0)            # x = south NM, y = east NM at `end`
        e_x, e_y = f.xy(*start)
        px, py = e_y, -e_x                          # start relative to end: (east, north)
        side = math.radians(end_course_true + (90.0 if turn == "R" else -90.0))
        nx, ny = math.sin(side), math.cos(side)     # unit vector from end towards the centre
        dot = nx * px + ny * py
        if dot <= 1e-6:
            return None
        r = (px * px + py * py) / (2.0 * dot)
        if not (0.05 < r < 50.0):
            return None
        center = _dest(end[0], end[1], math.degrees(side) % 360.0, r)
    b0 = _bearing(center[0], center[1], *start)
    b1 = _bearing(center[0], center[1], *end)
    r = (_nm(center[0], center[1], *start) + _nm(center[0], center[1], *end)) / 2.0
    sweep = (b1 - b0) % 360.0 if turn == "R" else -((b0 - b1) % 360.0)
    n = max(1, int(math.ceil(abs(sweep) / RF_STEP_DEG - 0.01)))   # tolerate a derived centre's rounding
    return [_dest(center[0], center[1], b0 + sweep * k / n, r) for k in range(1, n)]


# --------------------------------------------------------------------------- route assembly

@dataclass
class _P:
    lat: float
    lon: float
    kind: str
    fix: str | None
    src: str
    alt: float | None = None
    alt_src: str = "interp"
    spd: float | None = None
    faf: bool = False


class _Builder:
    def __init__(self, wave: Wave, callsign: str, rw: Runway, proc: dict | None):
        self.wave, self.cs, self.rw, self.proc = wave, callsign, rw, proc
        self.notes: list[str] = []
        self.pid: str | None = None   # designator in the procedures document; None: no approach/runway transition
        if proc:
            pid = _current_id(wave, rw.id)
            why = _cifp_end_check(proc, pid, rw)
            if why is None:
                self.pid = pid
            else:
                self.notes.append(why + " (synthetic straight-in)")
        ap = (proc or {}).get("airport") or {}
        self.elev = float(ap.get("elev_ft") or 0.0)
        self.magvar = float(ap.get("magvar_deg") or 0.0)
        self.fixes = (proc or {}).get("fixes") or {}
        self.thr = (float(rw.le_lat), float(rw.le_lon))
        self.frame = _Frame(self.thr[0], self.thr[1], rw.heading_true)
        self.pts: list[_P] = []
        self.i_app: int | None = None
        self.i_if: int | None = None

    # -- point helpers
    def _add(self, p: _P) -> None:
        if self.pts:
            q = self.pts[-1]
            if _nm(q.lat, q.lon, p.lat, p.lon) < 0.01:   # same place: merge constraints, keep the first point
                q.alt, q.alt_src = (q.alt, q.alt_src) if q.alt is not None else (p.alt, p.alt_src)
                q.spd = q.spd if q.spd is not None else p.spd
                q.faf = q.faf or p.faf
                if p.kind == "threshold":
                    q.kind, q.lat, q.lon, q.fix = p.kind, p.lat, p.lon, p.fix
                return
        self.pts.append(p)

    def _mark_app(self) -> None:
        if self.i_app is None:
            self.i_app = len(self.pts)

    def _mark_if(self) -> None:
        self._mark_app()
        if self.i_if is None:
            self.i_if = len(self.pts)

    def _legs(self, legs: list[dict], kind: str) -> bool:
        """Append fix points for CIFP legs; RF legs become chords every 10 deg. Returns True if the legs
        end in a vector leg (FM/VM)."""
        ends_vectors = False
        for g in legs:
            pt = g.get("pt") or ""
            if pt in VECTOR_PT:
                ends_vectors = True
                continue
            ends_vectors = False
            if not pt or pt in HOLD_PT:
                continue
            c = _coord(self.fixes, g.get("fix"))
            if c is None:
                continue
            fix = g["fix"]
            if kind == "final" and fix.startswith("RW"):
                self._add(_P(self.thr[0], self.thr[1], "threshold", fix, "cifp",
                             _leg_alt(g) if _leg_alt(g) is not None else self.elev + TCH_FT,
                             "constraint" if _leg_alt(g) is not None else "interp", g.get("speed_kt")))
                return False
            src = "cifp"
            if pt == "RF" and self.pts:
                src = self._arc(g, c, kind)
            alt = _leg_alt(g)
            self._add(_P(c[0], c[1], kind, fix, src, alt, "constraint" if alt is not None else "interp",
                         g.get("speed_kt"), faf=(kind == "final" and _desc(g, 3) == "F")))
        return ends_vectors

    def _arc(self, g: dict, end: tuple[float, float], kind: str) -> str:
        """Chord points of an RF arc into `end`; returns the src of the end point: "cifp" for an arc,
        "synth" when the arc cannot be built and a straight chord is flown instead (recorded in notes)."""
        prev = self.pts[-1]
        crs = g.get("course_mag")
        mids = rf_arc((prev.lat, prev.lon), end, _coord(self.fixes, g.get("center")),
                      None if crs is None else (float(crs) + self.magvar) % 360.0, g.get("turn") or "R")
        if mids is None:
            self.notes.append(f"RF leg to {g.get('fix')}: no centre fix and no usable end course; flown as a chord")
            return "synth"
        for lat, lon in mids:
            self._add(_P(lat, lon, kind, None, "cifp"))
        return "cifp"

    def _vectors_to(self, j: tuple[float, float]) -> None:
        """Synthetic vectors from the last point to join point `j` on the final course: downwind 5 NM abeam,
        base, then a 30 deg intercept. Direct when the last point is already inside the intercept cone."""
        f = self.frame
        last = self.pts[-1]
        xl, yl = f.xy(last.lat, last.lon)
        xj, _ = f.xy(*j)
        if xl > xj and abs(yl) <= (xl - xj) * math.tan(math.radians(INTERCEPT_DEG)):
            return
        s = 1.0 if yl >= 0 else -1.0
        xb = xj + INTERCEPT_OFFSET_NM / math.tan(math.radians(INTERCEPT_DEG))
        self._mark_app()
        xd = min(max(xl, 0.0), xb)
        if xb - xd > 0.1:
            self._add(_P(*f.ll(xd, s * DOWNWIND_NM), "vector", None, "synth"))
        self._add(_P(*f.ll(xb, s * DOWNWIND_NM), "vector", None, "synth"))
        self._add(_P(*f.ll(xb, s * INTERCEPT_OFFSET_NM), "vector", None, "synth"))

    def _origin_ll(self) -> tuple[float, float] | None:
        w = self.wave
        o = next((f.origin for f in w.flights if f.callsign == self.cs), None)
        if o is None:
            o = next((q.origin for q in w.queue if q.callsign == self.cs), None)
        if not o:
            return None
        from atm.wave import build   # lazy: build imports the wave modules
        return build._airport_coords(o)

    # -- main
    def build(self) -> Route:
        proc, rw, f = self.proc, self.rw, self.frame
        origin = self._origin_ll()
        app = _approach(proc, self.pid) if self.pid else None
        sel = select_star(proc, origin, self.pid or "") if proc and proc.get("stars") else None
        star_id = trans = hold_fix = None
        ends_vectors = False
        if sel is not None:
            star_id, trans = sel
            star = next(s for s in proc["stars"] if s["id"] == star_id)
            enr = (star.get("enroute") or {}).get(trans, []) if trans else []
            rkey = _pick_key(star.get("runway") or {}, self.pid) if self.pid else None
            rlegs = star["runway"][rkey] if rkey is not None else []
            common = star.get("common") or []
            ends_vectors = self._legs([*enr, *common, *rlegs], "fix")
            hf = _first_fix(common, self.fixes)
            if hf is None:
                last = [g for g in enr if _coord(self.fixes, g.get("fix"))]
                hf = (last[-1]["fix"], None) if last else None
            hold_fix = hf[0] if hf else None
        if not self.pts:   # no STAR (or no STAR fix with coordinates): corner post
            star_id = trans = None
            want = (_bearing(*self.thr, *origin) if origin is not None else (rw.heading_true + 180.0) % 360.0)
            cb = min(CORNERS, key=lambda b: (_angdiff(b, want), b))
            lat, lon = _dest(*self.thr, cb, CORNER_NM)
            hold_fix = f"CP{int(cb):03d}"
            self._add(_P(lat, lon, "entry", hold_fix, "synth"))
        self.pts[0].kind = "entry"
        corner = star_id is None

        if app is not None:
            self._join_approach(app, corner, ends_vectors)
        else:
            j = f.ll(STRAIGHT_IN_NM, 0.0)
            self._vectors_to(j)
            self._mark_if()
            self._add(_P(*j, "final", None, "synth",
                         self.elev + TCH_FT + STRAIGHT_IN_NM * math.tan(math.radians(GLIDE_DEG)) * NM_FT,
                         faf=True))
        if self.pts[-1].kind != "threshold":
            self._add(_P(*self.thr, "threshold", None, "synth", self.elev + TCH_FT))
        tier = ("star+iap" if not corner else "iap") if app is not None else "straight-in"
        return self._finish(star_id, trans, app, hold_fix, tier)

    def _join_approach(self, app: dict, corner: bool, ends_vectors: bool) -> None:
        trans = app.get("transitions") or {}
        final = app.get("final") or []
        last = self.pts[-1].fix
        if not corner and not ends_vectors and last:
            for t in sorted(trans):
                legs = trans[t]
                idx = next((i for i, g in enumerate(legs) if g.get("fix") == last), None)
                if idx is not None and (_iaf(legs[idx]) or _desc(legs[idx], 3) in IF_CODES or idx == 0):
                    self._mark_app()
                    self._legs(legs[idx + 1:], "fix")
                    self._final_from(final, 0)
                    return
            i_faf = next((i for i, g in enumerate(final) if _desc(g, 3) == "F"), len(final) - 1)
            idx = next((i for i, g in enumerate(final[:i_faf + 1])
                        if g.get("fix") == last and not str(last).startswith("RW")), None)
            if idx is not None:   # the STAR ends on the final (IF, a step-down fix, or the FAF): join there
                q = self.pts[-1]
                q.kind, q.faf = "final", q.faf or _desc(final[idx], 3) == "F"
                if q.alt is None and _leg_alt(final[idx]) is not None:
                    q.alt, q.alt_src = _leg_alt(final[idx]), "constraint"
                self._mark_app()
                if self.i_if is None:
                    self.i_if = len(self.pts) - 1
                self._final_from(final, idx + 1)
                return
        if corner and trans:
            best = None
            for t in sorted(trans):
                legs = trans[t]
                cands = [i for i, g in enumerate(legs) if _iaf(g) and _coord(self.fixes, g.get("fix"))]
                cands = cands or [i for i, g in enumerate(legs) if _coord(self.fixes, g.get("fix"))]
                if not cands:
                    continue
                i = cands[0]
                c = _coord(self.fixes, legs[i]["fix"])
                d = _nm(self.pts[-1].lat, self.pts[-1].lon, *c)
                if best is None or (d, t) < best[:2]:
                    best = (d, t, legs, i)
            if best is not None:
                _, _, legs, i = best
                self._mark_app()
                self._legs(legs[i:], "fix")
                self._final_from(final, 0)
                return
        # vectors to the IF, or to 2 NM before the FAF
        i_if = next((i for i, g in enumerate(final) if _desc(g, 3) in IF_CODES and _coord(self.fixes, g.get("fix"))),
                    None)
        i_faf = next((i for i, g in enumerate(final) if _desc(g, 3) == "F" and _coord(self.fixes, g.get("fix"))),
                     None)
        if i_if is not None and (i_faf is None or i_if < i_faf):
            self._vectors_to(_coord(self.fixes, final[i_if]["fix"]))
            self._final_from(final, i_if)
        elif i_faf is not None:
            fc = _coord(self.fixes, final[i_faf]["fix"])
            xf, _ = self.frame.xy(*fc)
            j = self.frame.ll(xf + FAF_JOIN_NM, 0.0)
            self._vectors_to(j)
            self._mark_if()
            self._add(_P(*j, "final", None, "synth", _leg_alt(final[i_faf])))
            self._final_from(final, i_faf)
        else:
            ff = _first_fix(final, self.fixes)
            if ff is not None:
                self._vectors_to(ff[1])
            self._final_from(final, 0)

    def _final_from(self, final: list[dict], i: int) -> None:
        first = _first_fix(final[i:], self.fixes)
        if self.i_if is None and first is not None and self.pts and self.pts[-1].fix == first[0]:
            self.i_if = len(self.pts) - 1   # the final's first fix closes the transition (merged point)
            self._mark_app()
        self._mark_if()
        self._legs(final[i:], "final")

    def _finish(self, star_id, trans, app, hold_fix, tier) -> Route:
        pts = self.pts
        n = len(pts)
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + _nm(a.lat, a.lon, b.lat, b.lon))

        # hold fix: the chosen fix must lie before the IF; otherwise the last named route fix before it
        i_if = self.i_if if self.i_if is not None else n - 1
        hi = next((i for i, p in enumerate(pts) if p.fix == hold_fix), 0) if hold_fix else 0
        if hi >= i_if:
            hi = next((i for i in range(i_if - 1, -1, -1) if pts[i].fix is not None), 0)
            self.notes.append(f"hold fix {hold_fix} is on the final approach; holding at {pts[hi].fix} instead")
        hp = pts[hi]
        i_app = self.i_app if self.i_app is not None else n - 1
        own = hp.alt if (hp.alt is not None and hp.alt_src == "constraint") else None
        if own is None and hp.src == "synth" and hp.alt is None:
            # corner post: the IAF (approach transition, before the IF) altitude, else 6,000 ft AGL; the post
            # sits at its hold base
            iaf_alt = next((p.alt for p in pts[i_app:i_if] if p.alt is not None and p.alt_src == "constraint"),
                           None)
            hp.alt = own = iaf_alt if iaf_alt is not None else round(self.elev + HOLD_AGL_FT, -2)

        # altitudes: constraints, unconstrained entry on a 3 deg profile (capped), linear in between
        if pts[0].alt is None:
            k = next(i for i in range(n) if pts[i].alt is not None)
            pts[0].alt = max(pts[k].alt, min(ENTRY_PROFILE_CAP_FT, self.elev + DESCENT_FT_PER_NM * cum[-1]))
        known = [i for i in range(n) if pts[i].alt is not None]
        for a, b in zip(known, known[1:]):
            for i in range(a + 1, b):
                w = (cum[i] - cum[a]) / (cum[b] - cum[a]) if cum[b] > cum[a] else 0.0
                pts[i].alt = pts[a].alt + w * (pts[b].alt - pts[a].alt)

        # hold base: the fix's constraint (or the corner post's level above), else the route's interpolated
        # altitude at the fix rounded to the nearest 1,000 ft (a published fix without a coded altitude)
        base = own if own is not None else float(round(hp.alt / 1000.0) * 1000)
        coded = next((h for h in (self.proc or {}).get("holds") or []
                      if h.get("fix") == hp.fix and h.get("kind") in ("HF", "HM") and h.get("inbound_mag") is not None),
                     None)
        if coded is not None:
            hold = holding.make_hold(hp.fix, hp.lat, hp.lon, (coded["inbound_mag"] + self.magvar) % 360.0,
                                     coded.get("turn") or "R", coded.get("leg_min"), base, "cifp")
        else:
            if hi > 0:
                inb = _bearing(pts[hi - 1].lat, pts[hi - 1].lon, hp.lat, hp.lon)
            else:
                inb = _bearing(hp.lat, hp.lon, pts[1].lat, pts[1].lon)
            hold = holding.make_hold(hp.fix or "HOLD", hp.lat, hp.lon, inb, "R", None, base, "synth")

        # speeds: phase profile, capped by CIFP speed limits, TAS, headwind on final only
        i_faf = next((i for i in range(i_if, n) if pts[i].faf), i_if)
        rwind = next((x for x in self.wave.runway_wind if x.runway == self.rw.id), None)
        hw = (rwind.headwind_kt - rwind.tailwind_kt) if rwind is not None else 0.0
        cap = None
        t = [0.0]
        for i in range(n - 1):
            if pts[i].spd:
                cap = float(pts[i].spd)
            alt = (pts[i].alt + pts[i + 1].alt) / 2.0
            if i >= i_faf:
                ias = IAS_FINAL
            elif i >= i_if:
                ias = IAS_FAF
            elif i >= i_app:
                ias = IAS_IAF
            else:
                ias = IAS_HIGH if alt > 10000 else IAS_LOW
            if cap is not None:
                ias = min(ias, cap)
            gs = holding.tas_kt(ias, alt) - (hw if i >= i_faf else 0.0)
            t.append(t[-1] + (cum[i + 1] - cum[i]) / max(gs, MIN_GS_KT) * 60.0)

        out = [RoutePoint(lat=p.lat, lon=p.lon, alt_ft=float(p.alt), kind=p.kind, fix=p.fix, src=p.src,
                          alt_src=p.alt_src, t_min=t[i]) for i, p in enumerate(pts)]
        return Route(callsign=self.cs, runway=self.rw.id, star=star_id, transition=trans or None,
                     approach=app["id"] if app is not None else None, entry_fix=pts[0].fix or "ENTRY",
                     points=out, t_route_min=t[-1], hold=hold, hold_index=hi, tier=tier, notes=list(self.notes))


def build_route(wave: Wave, callsign: str, runway_end: str) -> Route | None:
    """Route for `callsign` to `runway_end`, or None when there is no geometry (runway end without
    coordinates, or the airport at (0, 0))."""
    a = wave.airport
    rw = next((r for r in a.runways if r.id == runway_end), None)
    if rw is None or None in (rw.le_lat, rw.le_lon, rw.he_lat, rw.he_lon) or (a.lat == 0 and a.lon == 0):
        return None
    return _Builder(wave, callsign, rw, store.load_procedures(a.icao)).build()


# --------------------------------------------------------------------------- wave cache

def wave_key(wave: Wave) -> str:
    """Content fingerprint of a wave (sha1 of its contract JSON), memoised on the object."""
    k = wave.__dict__.get("_key")
    if k is None:
        k = hashlib.sha1(json.dumps(wave.to_dict(), sort_keys=True, default=str).encode()).hexdigest()
        wave.__dict__["_key"] = k
    return k


_CACHE: dict[str, dict[tuple[str, str], Route]] = {}


def routes_for_wave(wave: Wave) -> dict[tuple[str, str], Route]:
    """Routes keyed by (callsign, runway end) for every wave and queue flight over every open runway end.

    The returned dict and its Route objects are the shared cache entry: treat them as read-only."""
    k = wave_key(wave)
    hit = _CACHE.get(k)
    if hit is not None:
        return hit
    out: dict[tuple[str, str], Route] = {}
    callsigns = [f.callsign for f in wave.flights] + [q.callsign for q in wave.queue]
    for cs in dict.fromkeys(callsigns):
        for rw in wave.airport.runways:
            if rw.open:
                r = build_route(wave, cs, rw.id)
                if r is not None:
                    out[(cs, rw.id)] = r
    while len(_CACHE) >= CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[k] = out
    return out


routes_for_wave.cache_clear = _CACHE.clear
