"""Arrival routes per (flight, runway end): entry -> STAR -> approach -> threshold (spec §6.2, §6.3).

Built once per wave at wave-build time from the CIFP procedures document (`store.load_procedures`), with
synthetic fallbacks: vectors (downwind 5 NM abeam, base, 30 deg intercept) when a STAR does not end at an
approach IAF/IF, a corner-post entry (40 NM at 045/135/225/315 true) when the airport has no STAR, and a
10 NM 3 deg straight-in when the runway end has no approach. Every point carries `src` ("cifp" or "synth")
and `alt_src` ("constraint" or "interp"). Times come from the terminal speed profile (global constraints)
converted to TAS with `holding.tas_kt`. Pure geometry: nothing here decides legality, and the rules,
utility and negotiation layers read routes from the cache, never the store.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
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
ENTRY_CAP_FT = 18000.0                 # unconstrained entry altitude cap (US transition altitude)
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


def _current_id(wave: Wave, runway: str) -> str:
    """The runway end's designator today (the CIFP cycle's), undoing `renamed` events after the wave date:
    the wave keeps its dated designator (e.g. KAUS 17L in 2015), CIFP 2611 uses the current one (18L)."""
    from atm.wave import build   # lazy: build imports the wave modules
    date = wave.window_start[:10]
    cur = runway
    for h in sorted(build._runway_history().get(wave.airport.iata, []), key=lambda h: h["date"]):
        if h["event"] == "renamed" and date < h["date"] and _norm_rwy(h["old_id"]) == _norm_rwy(cur):
            cur = h["runway"]
    return cur


def _approach(proc: dict | None, runway: str) -> dict | None:
    apps = (proc or {}).get("approaches") or {}
    r = _norm_rwy(runway)
    hit = next((k for k in sorted(apps) if _norm_rwy(k) == r), None)
    if hit is None:
        hit = next((k for k in sorted(apps) if _rwy_key_matches(k, runway) and _norm_rwy(k) != "ALL"), None)
    return apps.get(hit) if hit is not None else None


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
        self.pid = _current_id(wave, rw.id) if proc else rw.id   # designator in the procedures document
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
            if pt == "RF" and self.pts:
                self._arc(g, c, kind)
            alt = _leg_alt(g)
            self._add(_P(c[0], c[1], kind, fix, "cifp", alt, "constraint" if alt is not None else "interp",
                         g.get("speed_kt"), faf=(kind == "final" and _desc(g, 3) == "F")))
        return ends_vectors

    def _arc(self, g: dict, end: tuple[float, float], kind: str) -> None:
        ctr = _coord(self.fixes, g.get("center"))
        if ctr is None:
            return   # no centre fix: fly the chord
        prev = self.pts[-1]
        b0 = _bearing(ctr[0], ctr[1], prev.lat, prev.lon)
        b1 = _bearing(ctr[0], ctr[1], *end)
        r = (_nm(ctr[0], ctr[1], prev.lat, prev.lon) + _nm(ctr[0], ctr[1], *end)) / 2.0
        sweep = (b1 - b0) % 360.0 if (g.get("turn") or "R") == "R" else -((b0 - b1) % 360.0)
        n = max(1, int(math.ceil(abs(sweep) / RF_STEP_DEG)))
        for k in range(1, n):
            lat, lon = _dest(ctr[0], ctr[1], b0 + sweep * k / n, r)
            self._add(_P(lat, lon, kind, None, "cifp"))

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
        app = _approach(proc, self.pid)
        sel = select_star(proc, origin, self.pid) if proc and proc.get("stars") else None
        star_id = trans = hold_fix = None
        ends_vectors = False
        if sel is not None:
            star_id, trans = sel
            star = next(s for s in proc["stars"] if s["id"] == star_id)
            enr = (star.get("enroute") or {}).get(trans, []) if trans else []
            rkey = _pick_key(star.get("runway") or {}, self.pid)
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
            idx = next((i for i, g in enumerate(final) if g.get("fix") == last and _desc(g, 3) in IF_CODES), None)
            if idx is not None:
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

        # hold: fix, base level, geometry
        hi = next((i for i, p in enumerate(pts) if p.fix == hold_fix), 0) if hold_fix else 0
        hp = pts[hi]
        i_app = self.i_app if self.i_app is not None else n - 1
        if hp.alt is not None and hp.alt_src == "constraint":
            base = hp.alt
        else:
            iaf_alt = next((p.alt for p in pts[i_app:] if p.alt is not None and p.alt_src == "constraint"
                            and p.kind != "threshold"), None)
            base = iaf_alt if iaf_alt is not None else round(self.elev + HOLD_AGL_FT, -2)
            if hp.src == "synth" and hp.alt is None:   # the corner post sits at its hold base
                hp.alt = base
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

        # altitudes: constraints, unconstrained entry on a 3 deg profile (capped), linear in between
        if pts[0].alt is None:
            k = next(i for i in range(n) if pts[i].alt is not None)
            pts[0].alt = max(pts[k].alt, min(ENTRY_CAP_FT, self.elev + DESCENT_FT_PER_NM * cum[-1]))
        known = [i for i in range(n) if pts[i].alt is not None]
        for a, b in zip(known, known[1:]):
            for i in range(a + 1, b):
                w = (cum[i] - cum[a]) / (cum[b] - cum[a]) if cum[b] > cum[a] else 0.0
                pts[i].alt = pts[a].alt + w * (pts[b].alt - pts[a].alt)

        # speeds: phase profile, capped by CIFP speed limits, TAS, headwind on final only
        i_if = self.i_if if self.i_if is not None else n - 1
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
                     points=out, t_route_min=t[-1], hold=hold, hold_index=hi, tier=tier)


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
    """Routes keyed by (callsign, runway end) for every wave and queue flight over every open runway end."""
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
