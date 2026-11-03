"""Fixed ground traffic (spec section 6.4): the wave's queue arrivals and, where the BTS 2015 movements exist, the real
departures in [T0 - 30, T0 + 120] min, scheduled once per wave on the OSM taxi graph, first come first served by
start time, into the reservation table that the wave flights give way to (v2's convention: fixed traffic is never
vetoed). Pure and deterministic; no model decides anything here.

- Queue arrival: lands at `slot_min` on its runway (a landing runway event), exits by `surface.choose_exit` after its
  ROT (`t_start` = exit time) and taxis to a seeded stand.
- Departure: pushback at WHEELS_OFF - TAXI_OUT (`t_start`), from a seeded stand (simulated) in the carrier's most-used
  pier, to the holding point of its departure runway; takeoff (a runway event) at WHEELS_OFF, or later if the taxi
  waits made it reach the holding point after that, or takeoff spacing (from takeoffs already settled, and 1 min from
  every landing on the strip) or a scheduled crossing holds it. It waits for its takeoff in the holding bay at the
  holding point until it lines up 30 s before takeoff (HOLD_UNTIL_TAKEOFF): off the taxiway, so a departure with an
  earlier takeoff passes it; at most BAY_CAPACITY at once, beyond which the next one waits upstream on the taxiway
  (visible to every later mover and to the wave flights); `hold_stats` reports the bay waits and the queue waits.
- Departure runway (assumption, `rules.LIMITS` "departure runway"): the airport is mixed-mode, and departures use the
  arrival flow's runway (the majority `moves.fcfs_runways` end), unless an open end parallel to it (heading within
  10 degrees, same direction, another strip) carries no arrivals; then every such free end is a departure runway.
  A provisional end (pushback + unimpeded taxi + queue wait at one takeoff a minute, TAKEOFF_SPACING_MIN, in
  WHEELS_OFF order) places the takeoff event before scheduling; then, in FCFS order, each departure is
  trial-scheduled to every candidate end against a copy-on-write view of the reservations and keeps the earliest
  expected takeoff (`_settle_runway`); ties go to the threshold nearest the stand, then the runway id.
"""
from __future__ import annotations

import math
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from backend.geo import store
from backend.wave import surface
from backend.wave.model import Provenance, Wave
from backend.wave.surface import GroundTrack, NoPath, Reservations, Scheduled, TaxiGraph, TaxiPath

WINDOW_BEFORE_MIN, WINDOW_AFTER_MIN = 30.0, 120.0   # departures with wheels-off in [T0 - 30, T0 + 120]
LOAD_EARLY_MIN = 360.0        # movements are filtered on schedule: read 6 h earlier so late departures still enter
LOAD_LATE_MIN = 15.0          # ... and 15 min later for early ones
PARALLEL_DEG = 10.0
# other free ends within this of the arrival flow also take departures (assumption, LIMITS)
DEPARTURE_SPREAD_DEG = 60.0
TAKEOFF_SPACING_MIN = 1.0     # expected takeoffs per departure runway end: one a minute (assumption, LIMITS)
# a takeoff stays this far from every landing on its strip (mixed mode; assumption, LIMITS)
LANDING_TAKEOFF_GAP_MIN = 1.0
HOLD_UNTIL_TAKEOFF = True     # a departure waits in the holding bay until it lines up (False: it leaves the holding
                              # point as it arrives; the takeoff time is unchanged)
SRC_REAL = "BTS 2015 movements"
SRC_PARTIAL = "challenge flights.csv subset"
_TOL = 1e-9


@dataclass
class FixedMover:
    """One fixed ground movement. For arrivals `t_start_min` is the runway exit time and `event_min` the landing slot;
    for departures `t_start_min` is the pushback time (WHEELS_OFF - TAXI_OUT) and `event_min` WHEELS_OFF (T0-relative).
    `path` is None when the movement cannot be routed on the OSM graph (`unrouted` says why); its runway event still
    counts."""
    callsign: str
    kind: Literal["arrival", "departure"]
    runway: str
    stand_node: int
    t_start_min: float
    path: TaxiPath | None
    event_min: float = 0.0
    strip: str = ""               # runway_events key: the OSM strip name (e.g. "10C/28C"), else rules.strip_label
    stand: str = ""
    unrouted: str | None = None
    label: str = ""               # reservation / runway-event / track callsign: `callsign`, or "<cs> (dep)" for a
                                  # departure whose callsign is also a wave or queue arrival's (own events are skipped
                                  # by callsign, so a shared one would hide a real conflict)

    # departures with several candidate runway ends: [(end, entry node, strip, distance stand -> threshold)]; the end
    # is settled by trial scheduling in FCFS order (`_compute`)
    options: list = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.label = self.label or self.callsign


@dataclass
class FixedTraffic:
    movers: list[FixedMover]
    reservations: Reservations
    scheduled: dict[str, Scheduled]
    tracks: list[GroundTrack]
    failures: list[tuple[str, str]]            # (callsign, reason) for every schedule() with ok=False
    mode: tuple[bool, str | None, list[str]]   # departure_mode(wave)
    real: bool                                 # BTS 2015 movements exist for this wave
    takeoffs_shifted: int = 0                  # takeoffs moved past WHEELS_OFF by taxi waits
    stats: dict = field(default_factory=dict)
    # strip -> (t_in, t_out, label, "crossing", hold node) of every scheduled fixed runway crossing (from leaving the
    # hold to past the zone) and (landing slot, exit clear, label, "arrival", exit node) of every fixed arrival on the
    # runway it lands on: the fixed traffic is scheduled before the plan exists, so the plan's pass checks its landings
    # against them
    occupancy: dict = field(default_factory=dict)

    def __deepcopy__(self, memo):
        return self                            # shared and read-only (pinned on waves, which tests deep-copy)


# ---------------------------------------------------------------- inputs

def _t0(wave: Wave) -> tuple[str, int]:
    """(local date, T0 in minutes since local midnight) from window_start."""
    d, t = wave.window_start.split("T")
    hh, mm = t.split(":")[:2]
    return d, int(hh) * 60 + int(mm)


def movements(wave: Wave) -> list[store.Movement] | None:
    """The airport's 2015 movements around the wave (scheduled in [T0 - 30 - 360, T0 + 120 + 15) local min), or None
    when there is no movements data for that airport and day."""
    date, t0 = _t0(wave)
    return store.load_movements(wave.airport.iata, date, int(t0 - WINDOW_BEFORE_MIN - LOAD_EARLY_MIN),
                                int(t0 + WINDOW_AFTER_MIN + LOAD_LATE_MIN))


def _hdg_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def departure_mode(wave: Wave) -> tuple[bool, str | None, list[str]]:
    """(mixed_mode, arrival runway, departure runway candidates). The arrival runway is the majority
    `moves.fcfs_runways` end (the queue's when the wave has no runway choice; ties by id). The airport is
    mixed-mode iff no open end parallel to it (heading within 10 degrees, same direction, another strip) is free of
    arrivals (wave flights' FCFS ends and the queue's, by strip); then the candidates are the arrival runway alone,
    else all the free parallel ends (each departure takes the earliest expected takeoff among them)."""
    from backend.wave import moves                 # lazy: moves -> utility -> ... import cycle
    from backend.wave.rules import strip_of
    fc = [r for r in moves.fcfs_runways(wave).values() if r] or [q.runway for q in wave.queue]
    if not fc:
        return True, None, []
    cnt = Counter(fc)
    maj = min(cnt, key=lambda r: (-cnt[r], r))
    m = next((r for r in wave.airport.runways if r.id == maj), None)
    if m is None:
        return True, maj, [maj]
    used = {strip_of(r) for r in fc} | {strip_of(q.runway) for q in wave.queue}
    par = [r.id for r in wave.airport.runways if r.open and strip_of(r.id) != strip_of(maj)
           and _hdg_diff(r.heading_true, m.heading_true) <= PARALLEL_DEG]
    free = sorted(r for r in par if strip_of(r) not in used)
    # other free ends within DEPARTURE_SPREAD_DEG of the flow, no tailwind and wind within the airport's crosswind /
    # tailwind limits, whose strip crosses no arrival strip and no strip already chosen (crossing-runway operations
    # are not modelled), by angle then id
    by_id = {r.id: r for r in wave.airport.runways}
    arr_rw = [r for r in wave.airport.runways if strip_of(r.id) in used]
    wind = {x.runway: x for x in wave.runway_wind}
    chosen = [by_id[e] for e in free]
    extra = []
    for r in sorted((r for r in wave.airport.runways if r.open and r.id not in free and strip_of(r.id) not in used
                     and _hdg_diff(r.heading_true, m.heading_true) <= DEPARTURE_SPREAD_DEG
                     and (r.id not in wind or (wind[r.id].tailwind_kt <= 0.0 and wind[r.id].within_limits))),
                    key=lambda r: (_hdg_diff(r.heading_true, m.heading_true), r.id)):
        if any(strip_of(c.id) == strip_of(r.id) for c in chosen):
            continue
        if any(_strips_cross(r, x) for x in arr_rw + chosen):
            continue
        chosen.append(r)
        extra.append(r.id)
    cands = free + sorted(extra)
    return (False, maj, cands) if cands else (True, maj, [maj])


def _strips_cross(a, b) -> bool:
    """The two runways' centrelines (threshold to threshold) intersect (local flat projection); False without
    coordinates."""
    pts = [a.le_lat, a.le_lon, a.he_lat, a.he_lon, b.le_lat, b.le_lon, b.he_lat, b.he_lon]
    if any(v is None for v in pts):
        return False
    k = math.cos(math.radians(a.le_lat))
    p1, p2 = (a.le_lon * k, a.le_lat), (a.he_lon * k, a.he_lat)
    q1, q2 = (b.le_lon * k, b.le_lat), (b.he_lon * k, b.he_lat)

    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(p1, p2, q1) * o(p1, p2, q2) < 0 and o(q1, q2, p1) * o(q1, q2, p2) < 0


# ---------------------------------------------------------------- graph helpers

def _strip_key(g: TaxiGraph | None, end: str) -> str:
    """The runway_events key of a runway end: the OSM strip whose name holds the end, else rules.strip_label."""
    if g is not None:
        for st in surface.runway_strips(g):
            if end in st.split("/"):
                return st
    from backend.wave.rules import strip_label
    return strip_label(end)


def _main_comp(g: TaxiGraph) -> int | None:
    """The taxi-network component holding the most stands."""
    comps = g.stand_components()
    comp = g._trees["comp"]
    n = Counter(comp[s["node"]] for s in g.stands.values() if comp.get(s["node"]) in comps)
    return min(n, key=lambda c: (-n[c], c)) if n else None


def _entry_node(g: TaxiGraph, end: str, main: int | None) -> int | None:
    """Where a departure enters runway `end`: its exit nearest the threshold, else the reciprocal's farthest exit."""
    from backend.wave.rules import reciprocal
    comp = g._trees["comp"]
    ex = [e for e in g.exits.get(end) or () if comp.get(e["node"]) == main]
    if ex:
        return min(ex, key=lambda e: (e["dist_m"], e["node"]))["node"]
    ex = [e for e in g.exits.get(reciprocal(end) or "") or () if comp.get(e["node"]) == main]
    if ex:
        return max(ex, key=lambda e: (e["dist_m"], -e["node"]))["node"]
    return None


def _forward(g: TaxiGraph, nodes: list[int]) -> TaxiPath:
    """Taxi times for the node sequence in this direction (20 kt, 10 kt on taxilanes and turns > 30 deg), names,
    OSM-tagged plus geometric crossings, and the final 30 s line-up buffer."""
    seg, names, strips = [], [], []
    for i in range(len(nodes) - 1):
        u, v = nodes[i], nodes[i + 1]
        e = min((e for e in g.adj[u] if e[0] == v and e[2] != "runway"), key=lambda e: (e[1], e[3] or ""))
        slow = e[2] == "taxilane" or (i > 0 and surface._heading_turn(g, nodes[i - 1], u, v) > surface.TURN_SLOW_DEG)
        seg.append(e[1] / ((surface.SLOW_KT if slow else surface.TAXI_KT) * surface.KT))
        names.append(e[3] or "")
        strips.append(e[4])
    tagged = [(nodes[i], strips[i]) for i in range(1, len(nodes) - 1)
              if strips[i] is not None and strips[i - 1] == strips[i]]
    seg.append(surface.PARK_S)
    return TaxiPath(list(nodes), seg, names, surface.merge_crossings(g, list(nodes), tagged), sum(seg) / 60.0, "osm")


def departure_path(g: TaxiGraph, stand_node: int, entry: int, strip: str) -> TaxiPath:
    """Memoised on the graph per (stand, entry, strip): `_departure_path`. Shared and read-only."""
    memo = g._trees.setdefault("dep_paths", {})
    k = (stand_node, entry, strip)
    hit = memo.get(k)
    if hit is None:
        hit = memo[k] = _departure_path(g, stand_node, entry, strip)
    return hit


def _departure_path(g: TaxiGraph, stand_node: int, entry: int, strip: str) -> TaxiPath:
    """Stand -> holding point of `strip`: the reverse of the entry-rooted fastest path (one cached Dijkstra per runway
    entry, not per stand), timed in the forward direction, cut at the last node before the tail that is inside the
    strip's protected zone or on any runway's pavement. The final 30 s (PARK_S) is kept as a line-up buffer: the
    departure holds the holding-point node for 30 s, then lines up; it is not drawn queueing there until WHEELS_OFF
    (a long TAXI_OUT would block the node and reorder every later takeoff)."""
    tr = surface._tree(g, entry)                 # the entry-rooted Dijkstra (cached on the graph), nodes only
    if stand_node not in tr["best"]:
        raise NoPath(f"stand node {stand_node} is not reachable from runway entry {entry}")
    nodes, st = [], tr["best"][stand_node][1]
    while st is not None:                        # parent chain runs stand -> entry: already the departure order
        nodes.append(st[0])
        st = tr["parent"][st]
    strips = surface.runway_strips(g)
    k = len(nodes) - 1
    while k > 0 and (surface._in_zone(g, nodes[k], strip) or any(surface.on_runway(g, nodes[k], st) for st in strips)):
        k -= 1
    return _forward(g, nodes[:k + 1])


# ---------------------------------------------------------------- stands

def _stand_piers(wave: Wave, g: TaxiGraph, main: int | None) -> dict[str, list[tuple[str, dict]]]:
    """Pier (stand ref prefix) -> [(stand key, stand)] of stands in the main taxi component, minus the stands the
    wave's gates bind to. Stands without a lettered ref form pier "" (used only when no pier has a letter)."""
    from backend.geo.gates_geo import _split, bind_gates
    comp = g._trees["comp"]
    bound: set[int] = set()
    doc = store.load_surface(wave.airport.icao)
    if doc is not None:
        bound = {b["node"] for b in bind_gates([(gt.id, gt.remote) for gt in wave.gates], doc).values()}
    piers: dict[str, list[tuple[str, dict]]] = {}
    for key in sorted(g.stands):
        s = g.stands[key]
        if comp.get(s["node"]) != main or s["node"] in bound:
            continue
        ref = s.get("ref")
        piers.setdefault(_split(str(ref))[0] if ref not in (None, "") else "", []).append((key, s))
    named = {p: v for p, v in piers.items() if p}
    return named or piers


def _carrier_pier(wave: Wave, carrier: str, piers: dict) -> str:
    """The carrier's most-used pier among the wave's flights (by gate), else a seeded pier (simulated), weighted by its
    stand count."""
    from backend.wave.build import _rng
    gate_pier = {gt.id: gt.pier.upper() for gt in wave.gates}
    use = Counter(gate_pier.get(f.scheduled_gate) for f in wave.flights if f.carrier == carrier)
    hit = [p for p in sorted(use, key=lambda p: (-use[p], str(p))) if p in piers]
    if hit:
        return hit[0]
    keys = sorted(piers)
    rng = _rng(wave.airport.iata, carrier, "pier")
    x = rng.random() * sum(len(piers[p]) for p in keys)
    for p in keys:
        x -= len(piers[p])
        if x < 0:
            return p
    return keys[-1]


def _carrier(cs: str) -> str:
    return cs[:2]


# ---------------------------------------------------------------- fixed traffic

def _build_movers(wave: Wave, g: TaxiGraph | None) -> tuple[list[FixedMover], tuple, bool, dict]:
    from backend.wave.build import _rng
    from backend.wave.route import IAS_FINAL, MIN_GS_KT
    date, t0 = _t0(wave)
    mv = movements(wave)
    mode = departure_mode(wave)
    stats = {"no_taxi_out": 0, "duplicate_departures": [], "takeoffs_held": 0}
    main = _main_comp(g) if g is not None else None
    piers = _stand_piers(wave, g, main) if g is not None else {}
    used: set[int] = set()
    wet = wave.metar is not None and wave.metar.surface == "wet"

    def stand_for(cs: str) -> tuple[str, dict] | None:
        if not piers:
            return None
        cands = piers[_carrier_pier(wave, _carrier(cs), piers)]
        free = [c for c in cands if c[1]["node"] not in used] or cands
        pick = free[_rng(wave.id, cs, "stand").randrange(len(free))]
        used.add(pick[1]["node"])
        return pick

    # (event time, kind, callsign, payload) in event order, so stand choice is deterministic
    todo: list[tuple[float, str, str, object]] = [(float(q.slot_min), "arrival", q.callsign, q) for q in wave.queue]
    if mv:
        seen: set[str] = set()
        for m in sorted(mv, key=lambda m: (m.wheels_min_abs if m.wheels_min_abs is not None else 1e9, m.callsign)):
            if m.kind != "departure" or m.wheels_min_abs is None:
                continue
            if m.callsign in seen:
                stats["duplicate_departures"].append(m.callsign)
                continue
            off = float(m.wheels_min_abs - t0)
            if -WINDOW_BEFORE_MIN - _TOL <= off <= WINDOW_AFTER_MIN + _TOL:
                seen.add(m.callsign)
                todo.append((off, "departure", m.callsign, m))
    todo.sort(key=lambda x: (x[0], x[2], x[1]))
    wave_cs = {f.callsign for f in wave.flights} | {q.callsign for q in wave.queue}
    hw = {x.runway: x.headwind_kt - x.tailwind_kt for x in wave.runway_wind}
    out: list[FixedMover] = []
    last_to: dict[str, float] = {}       # runway end -> latest expected takeoff assigned there
    for ev, kind, cs, obj in todo:
        st = stand_for(cs) if g is not None else None
        skey, snode = (st[0], int(st[1]["node"])) if st else ("", -1)
        if kind == "arrival":
            q = obj
            path, why, t_start = None, None, ev
            if g is None:
                why = "no OSM surface graph"
            else:
                ex = surface.choose_exit(g, q.runway, "wide" if q.wake in ("HEAVY", "SUPER") else "narrow", wet)
                t_start = ev + surface.rot_s(ex, max(IAS_FINAL - hw.get(q.runway, 0.0), MIN_GS_KT)) / 60.0
                if ex["node"] is None:
                    why = f"runway end {q.runway} not in OSM"
                elif st is None:
                    why = "no stand in the main taxi network"
                else:
                    try:
                        path = surface.unimpeded(g, ex["node"], snode)
                    except NoPath as e:
                        why = f"no taxi path ({e})"
            out.append(FixedMover(cs, "arrival", q.runway, snode, t_start, path, ev, _strip_key(g, q.runway), skey,
                                  why))
            continue
        m = obj
        mixed, arr_rw, cands = mode
        rwy, entry, why = None, None, None
        by_id = {r.id: r for r in wave.airport.runways}

        def dist(rid: str) -> float:
            r = by_id.get(rid)
            if st is None or r is None or r.le_lat is None or r.le_lon is None:
                return math.inf
            la, lo = st[1]["lat"], st[1]["lon"]
            return math.hypot((r.le_lon - lo) * math.cos(math.radians(la)), r.le_lat - la)

        # departure runway: among the flow's candidates, the earliest expected takeoff = pushback + unimpeded taxi to
        # the end's runway entry + queue wait behind the takeoffs already assigned there (TAKEOFF_SPACING_MIN);
        # ties -> nearest threshold to the stand, then id
        rows = []
        opts = []
        for rid in cands:
            ent = _entry_node(g, rid, main) if g is not None and st is not None else None
            taxi_u = None
            if ent is not None:
                hit = surface._tree(g, ent)["best"].get(snode)
                taxi_u = hit[0] / 60.0 + surface.PARK_S / 60.0 if hit is not None else None
            if taxi_u is None:
                rows.append(((math.inf, dist(rid), rid), rid, ent, None))
                continue
            opts.append((rid, ent, _strip_key(g, rid), dist(rid)))
            push = ev - (float(m.taxi_min) if m.taxi_min is not None else taxi_u)
            ready = push + taxi_u
            wait = max(0.0, last_to.get(rid, -math.inf) + TAKEOFF_SPACING_MIN - ready)
            rows.append(((ready + wait, dist(rid), rid), rid, ent, ready + wait))
        if rows:
            _, rwy, entry, t_exp = min(rows, key=lambda r: r[0])
            if t_exp is not None:
                last_to[rwy] = max(ev, t_exp)        # takeoff is never before WHEELS_OFF
        strip = _strip_key(g, rwy) if rwy else ""
        path = None
        if rwy is None:
            why = "no open runway"
        elif g is None:
            why = "no OSM surface graph"
        elif st is None:
            why = "no stand in the main taxi network"
        elif entry is None:
            why = f"runway end {rwy} not in OSM"
        else:
            try:
                path = departure_path(g, snode, entry, strip)
            except NoPath as e:
                why = f"no taxi path ({e})"
        if m.taxi_min is not None:
            taxi = float(m.taxi_min)
        else:
            stats["no_taxi_out"] += 1
            taxi = path.unimpeded_min if path is not None else 0.0
        label = f"{cs} (dep)" if cs in wave_cs else cs
        out.append(FixedMover(cs, "departure", rwy or "", snode, ev - taxi, path, ev, strip, skey, why, label,
                              opts if path is not None and len(opts) > 1 else []))
    out.sort(key=lambda m: (m.t_start_min, m.callsign))
    return out, mode, mv is not None, stats


class _CowTable(dict):
    """Copy-on-write view of a reservation table for trial scheduling: reads fall through to `base`, and the first
    write to a key copies its list into the view, so `base` and its lists are never mutated."""

    def __init__(self, base: dict):
        super().__init__()
        self.base = base

    def _own(self, k) -> bool:
        return dict.__contains__(self, k)

    def __contains__(self, k) -> bool:
        return self._own(k) or k in self.base

    def __getitem__(self, k):
        return dict.__getitem__(self, k) if self._own(k) else self.base[k]

    def get(self, k, d=None):
        return dict.__getitem__(self, k) if self._own(k) else self.base.get(k, d)

    def setdefault(self, k, d=None):
        if not self._own(k):
            dict.__setitem__(self, k, list(self.base.get(k, ())))
        return dict.__getitem__(self, k)


def _takeoff_time(events, spans, t: float) -> float:
    """The departure's takeoff: the earliest time >= t that is TAKEOFF_SPACING_MIN from every other takeoff on the
    strip, LANDING_TAKEOFF_GAP_MIN from every landing on it (mixed mode: the arrival has vacated, or the departure is
    airborne before the arrival crosses the threshold) and outside the crossing window of every scheduled crossing of
    it (a takeoff in [t_in - a, t_out + b] of a crossing span, GAP_MIN["takeoff"] = (a, b), would break that crossing's
    gap). The departure waits at the holding point: the crosser and the landing came first (FCFS). t only grows, so
    this ends."""
    a, b = surface.GAP_MIN["takeoff"]
    tos = [e[0] for e in events if e[1] == "takeoff"]
    lds = [e[0] for e in events if e[1] == "landing"]
    moved = True
    while moved:
        moved = False
        for x in tos:
            if t - TAKEOFF_SPACING_MIN + _TOL < x < t + TAKEOFF_SPACING_MIN - _TOL:
                t, moved = x + TAKEOFF_SPACING_MIN, True
        for x in lds:
            if t - LANDING_TAKEOFF_GAP_MIN + _TOL < x < t + LANDING_TAKEOFF_GAP_MIN - _TOL:
                t, moved = x + LANDING_TAKEOFF_GAP_MIN, True
        for t_in, t_out in spans:
            if t_in - a - _TOL <= t <= t_out + b + _TOL:
                t, moved = t_out + b + 2 * _TOL, True   # just past the span's tolerant edge, so the loop ends
    return t


def _spacing_events(events, settled: set[str] | None) -> list:
    """The runway events a takeoff is spaced from: every landing, and the takeoffs already settled (FCFS by pushback).
    A departure not yet scheduled holds a provisional takeoff event at its WHEELS_OFF (so earlier crossers keep clear
    of it), but that event does not block an earlier departure's takeoff: it moves once its own departure is
    scheduled. `settled` None: every takeoff counts."""
    return [e for e in events if e[1] != "takeoff" or settled is None or e[2] in settled]


# holding bay at each departure holding point (assumption, LIMITS): departures waiting for their takeoff stand off
# the taxiway, so one with an earlier takeoff passes them; at most this many at once, else the queue backs up upstream
BAY_CAPACITY = 3
HOLD_QUEUE_MAX_RETRY = 50     # re-schedules of one departure waiting upstream for room in the bay


def _bay_free_at(bay, t_in: float, t_out: float) -> tuple[float, str] | None:
    """None when a departure can wait in the holding bay over [t_in, t_out] (fewer than BAY_CAPACITY others in it at
    every moment); else (the earliest time >= t_in at which one of the occupants leaves and there is room, its label):
    the departure must reach the holding point no earlier (the queue backs up upstream)."""
    occ = [b for b in bay if b[0] < t_out - _TOL and t_in < b[1] - _TOL]
    pts = sorted({t_in} | {b[0] for b in occ if b[0] > t_in})
    if all(sum(1 for b in occ if b[0] <= t + _TOL and t < b[1] - _TOL) < BAY_CAPACITY for t in pts):
        return None
    for b in sorted(occ, key=lambda b: (b[1], b[2])):
        t = b[1]
        if t <= t_in + _TOL:
            continue
        if sum(1 for x in bay if x[0] <= t + _TOL and t < x[1] - _TOL) < BAY_CAPACITY:
            return (t, b[2])
    return (max(b[1] for b in occ), max(occ, key=lambda b: b[1])[2])


def _unreserve(res: Reservations, nodes: list[int], label: str) -> None:
    """Drop every node and edge reservation of `label` along `nodes` (a schedule to be redone)."""
    for n in set(nodes):
        if n in res.node_busy:
            res.node_busy[n] = [b for b in res.node_busy[n] if b[2] != label]
    for u, v in zip(nodes, nodes[1:]):
        if (u, v) in res.edge_dir:
            res.edge_dir[(u, v)] = [b for b in res.edge_dir[(u, v)] if b[2] != label]


def _crossing_spans(g: TaxiGraph, path: TaxiPath, s: Scheduled) -> list[tuple[str, float, float]]:
    """(strip, from leaving the hold, to reaching the first node past the zone) for each runway crossing of a
    scheduled path, read back from its points (one arrival point per node, plus a departure point after a wait)."""
    nodes, pts = path.nodes, s.points
    n = len(nodes)
    arr, dep = [0.0] * n, [0.0] * n
    k = 0
    for i in range(n):
        arr[i] = dep[i] = pts[k]["t_min"]
        if i < n - 1 and k + 1 < len(pts) and pts[k + 1]["node"] == nodes[i] and nodes[i + 1] != nodes[i]:
            dep[i] = pts[k + 1]["t_min"]
            k += 1
        k += 1
    _, zones, _ = surface._crossing_zones(g, nodes, path.crossings)
    return [(strip, dep[h], arr[ze], nodes[h]) for _, strip, h, ze, _ in zones]


def _settle_runway(g: TaxiGraph, m: FixedMover, res: Reservations,
                   spans: dict[str, list[tuple[float, float]]], settled: set[str] | None = None) -> None:
    """Trial-schedule the departure to each candidate end against a copy-on-write view of the current reservations
    and keep the end with the earliest takeoff, ok=True first; ties to the threshold nearest the stand, then the
    runway id. The takeoff is the one `_compute` will record: `_takeoff_time` from the time it leaves the holding
    point (never before WHEELS_OFF). Moves its pre-inserted takeoff event to the chosen strip."""
    events = {s: [e for e in lst if e[2] != m.label] for s, lst in res.runway_events.items()}
    spacing = {}

    def takeoff(strip: str, t: float) -> float:
        if strip not in spacing:
            spacing[strip] = _spacing_events(events.get(strip, ()), settled)
        return _takeoff_time(spacing[strip], spans.get(strip, ()), t)

    # Pruning (same result as trying every candidate): `_takeoff_time` is non-decreasing in its start time and a
    # schedule never reaches the holding point before the unimpeded travel time (waits only add), so a candidate whose
    # lower-bound key is already worse than the best ok key cannot win; candidates are tried in lower-bound order.
    lb0 = sorted(((takeoff(strip, m.event_min), d, rid), rid, entry, strip, d) for rid, entry, strip, d in m.options)
    best = None
    for (t0, _, _), rid, entry, strip, d in lb0:
        if best is not None and not best[0][0] and (False, t0, d, rid) > best[0]:
            continue
        try:
            path = m.path if rid == m.runway else departure_path(g, m.stand_node, entry, strip)
        except NoPath:
            continue
        travel = sum(path.seg_s[:-1]) / 60.0
        if best is not None and not best[0][0] and \
                (False, takeoff(strip, max(m.event_min, m.t_start_min + travel)), d, rid) > best[0]:
            continue
        view = Reservations(_CowTable(res.node_busy), _CowTable(res.edge_dir), events)
        s = surface.schedule(g, path, m.t_start_min, view, m.label)
        t = takeoff(strip, max(m.event_min, s.points[-1]["t_min"]))
        key = (not s.ok, t, d, rid)
        if best is None or key < best[0]:
            best = (key, rid, strip, path)
    if best is None or best[1] == m.runway:
        return
    old = res.runway_events[m.strip]
    old.remove((m.event_min, "takeoff", m.label))
    lst = res.runway_events.setdefault(best[2], [])
    lst.append((m.event_min, "takeoff", m.label))
    lst.sort()
    m.runway, m.strip, m.path = best[1], best[2], best[3]


def _compute(wave: Wave) -> FixedTraffic:
    g = surface.load_graph(wave.airport.icao)
    movers, mode, real, stats = _build_movers(wave, g)
    res = Reservations({}, {}, {})
    for m in movers:
        if m.strip:
            res.runway_events.setdefault(m.strip, []).append(
                (m.event_min, "landing" if m.kind == "arrival" else "takeoff", m.label))
    for lst in res.runway_events.values():
        lst.sort()
    scheduled: dict[str, Scheduled] = {}
    tracks: list[GroundTrack] = []
    failures: list[tuple[str, str]] = []
    shifted = 0
    spans: dict[str, list[tuple[float, float]]] = {}      # strip -> crossing windows of scheduled movers
    occupancy: dict[str, list[tuple[float, float, str, str]]] = {}
    stats["hold_point_min"], stats["queue_wait_min"] = [], []
    # takeoffs whose time is final: unrouted departures keep WHEELS_OFF; the others settle as they are scheduled
    settled: set[str] = {m.label for m in movers if m.kind == "departure" and (m.path is None or g is None)}
    bays: dict[int, list[tuple[float, float, str]]] = {}
    for m in movers:
        if m.path is None or g is None:
            continue
        if m.options:
            _settle_runway(g, m, res, spans, settled)
        # schedule() does not skip the mover's own runway event: hide it (shared node/edge tables, filtered events)
        view = Reservations(res.node_busy, res.edge_dir,
                            {s: [e for e in lst if e[2] != m.label] for s, lst in res.runway_events.items()})
        last = m.path.nodes[-1]
        fifo_ok, blocks = False, []
        for _attempt in range(HOLD_QUEUE_MAX_RETRY):
            s = surface.schedule(g, m.path, m.t_start_min, view, m.label)
            if m.kind != "departure" or not HOLD_UNTIL_TAKEOFF or s.reason == "hold-short search did not converge":
                fifo_ok = True
                break
            # holding bay: its wait there (from reaching the holding point to line-up) needs room in the bay, else it
            # reaches the holding point only when an occupant leaves (a temporary node reservation under that
            # occupant's label, so the wait names it) and the queue backs up upstream
            leave = max(m.event_min, s.points[-1]["t_min"])
            t_to = _takeoff_time(_spacing_events([e for e in res.runway_events[m.strip] if e[2] != m.label],
                                                 settled), spans.get(m.strip, ()), leave)
            at_hold = next(q["t_min"] for q in s.points if q["node"] == last)
            free = _bay_free_at(bays.get(last, ()), s.points[-1]["t_min"], t_to - surface.PARK_S / 60.0)
            if free is None:
                fifo_ok = True
                break
            _unreserve(res, m.path.nodes, m.label)
            blk = (at_hold, free[0], free[1])
            res.node_busy[last].append(blk)
            res.node_busy[last].sort()
            blocks.append(blk)
        for blk in blocks:
            res.node_busy[last].remove(blk)
        if not fifo_ok:
            # retries exhausted (its reservations were dropped): schedule once without the queue blocks, keep it,
            # and report the unresolved order as a fixed failure
            s = surface.schedule(g, m.path, m.t_start_min, view, m.label)
        scheduled[m.label] = s
        if not s.ok:
            failures.append((m.label, s.reason or "not scheduled"))
        elif not fifo_ok:
            failures.append((m.label, "no room in the holding bay within the retries"))
        points, waits = s.points, s.waits
        if m.kind == "departure":
            leave = max(m.event_min, s.points[-1]["t_min"])
            lst = res.runway_events[m.strip]
            t_to = _takeoff_time(_spacing_events([e for e in lst if e[2] != m.label], settled),
                                 spans.get(m.strip, ()), leave)
            settled.add(m.label)
            if leave > m.event_min + _TOL:
                shifted += 1
            if t_to > leave + _TOL:
                stats["takeoffs_held"] += 1
            if t_to > m.event_min + _TOL:
                i = lst.index((m.event_min, "takeoff", m.label))
                lst[i] = (t_to, "takeoff", m.label)
                lst.sort()
            at_hold = next(p["t_min"] for p in s.points if p["node"] == last)
            stats["hold_point_min"].append((t_to - at_hold, m.label, last))
            stats["queue_wait_min"].append(sum(w["to_min"] - w["from_min"] for w in s.waits))
            stats["late"] = stats.get("late", 0) + (t_to > m.event_min + _TOL)
            end = s.points[-1]["t_min"]
            line_up = t_to - surface.PARK_S / 60.0     # it leaves the holding point 30 s before takeoff (line-up)
            if HOLD_UNTIL_TAKEOFF and s.reason != "hold-short search did not converge" and line_up > end + _TOL:
                # it waits in the holding bay until it lines up: off the taxiway (no node reservation, so a departure
                # with an earlier takeoff passes it and the wave flights do not see it), at most BAY_CAPACITY at once
                bays.setdefault(last, []).append((end, line_up, m.label))
                p = s.points[-1]
                points = s.points + [{"t_min": line_up, "lat": p["lat"], "lon": p["lon"], "node": None}]
                waits = waits + [{"node": None, "lat": p["lat"], "lon": p["lon"], "from_min": end,
                                  "to_min": line_up, "reason": f"holding bay, takeoff {m.runway}", "strip": None,
                                  "kind": None, "for": None}]
        if s.reason == "hold-short search did not converge":
            continue                     # nothing was reserved: no track to draw or verify
        cs_spans = _crossing_spans(g, m.path, s)
        for strip, a, b, hn in cs_spans:
            spans.setdefault(strip, []).append((a, b))
            occupancy.setdefault(strip, []).append((a, b, m.label, "crossing", hn))
        if m.kind == "arrival":
            for strip in surface.runway_strips_at(g, m.path.nodes[0]):
                occupancy.setdefault(strip, []).append((m.event_min, s.exit_clear_min, m.label, "arrival",
                                                        m.path.nodes[0]))
        names = [n for i, n in enumerate(m.path.names) if n and (i == 0 or m.path.names[i - 1] != n)]
        tracks.append(GroundTrack(m.label, m.kind, False, m.runway, m.stand, names, points, waits,
                                  m.path.unimpeded_min, points[-1]["t_min"] - m.t_start_min, m.path.src))
    # Safety net: re-check every track's runway gaps against the final events with the independent verifier (one
    # track at a time: checks 3 and 4 only). Takeoffs are held clear of scheduled crossings above, so a broken
    # crossing gap should not occur; if one does it is a failure; an arrival still vacating at another fixed
    # movement's takeoff or landing
    # comes from the data's own times (queue slots, real WHEELS_OFF) and is counted, never hidden.
    stats["vacating"] = []
    if g is not None:
        failed = {cs for cs, _ in failures}
        for tr in tracks:
            for msg in surface.verify_separation([tr], runway_events=res.runway_events, g=g):
                if "protected zone" in msg and tr.callsign not in failed:
                    failures.append((tr.callsign, f"crossing gap broken by a later takeoff: {msg}"))
                    failed.add(tr.callsign)
                elif "still vacating" in msg:
                    stats["vacating"].append(msg)
    return FixedTraffic(movers, res, scheduled, tracks, failures, mode, real, shifted, stats, occupancy)


_CACHE: dict[str, FixedTraffic] = {}
_CACHE_MAX = 32               # as build_wave's LRU
_LOCK = threading.Lock()      # eviction from the benchmark's worker threads (as route / trajectory)


_PIN = "_ground_traffic"      # wave.__dict__ slot: (wave_key, generation, FixedTraffic) pinned by `annotate`
_GEN = [0]                    # bumped by reservations.cache_clear(), which also invalidates the pins


def _get(wave: Wave) -> FixedTraffic:
    """The wave's fixed traffic: pinned on the object at build time (so the hot path never recomputes it, nor reads
    the store, after a cache eviction), else the shared cache, else computed (a variant wave; reads the store)."""
    from backend.wave.route import wave_key
    k = wave_key(wave)
    pin = wave.__dict__.get(_PIN)
    if pin is not None and pin[0] == k and pin[1] == _GEN[0]:
        return pin[2]
    hit = _CACHE.get(k)
    if hit is None:
        hit = _compute(wave)
        _put(k, hit)
    return hit


def _put(k: str, ft: FixedTraffic) -> None:
    with _LOCK:
        while len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[k] = ft


def fixed_traffic(wave: Wave) -> list[FixedMover]:
    """Queue arrivals plus real 2015 departures with wheels-off in [T0 - 30, T0 + 120], in FCFS (t_start) order."""
    return list(_get(wave).movers)


def fixed_tracks(wave: Wave) -> list[GroundTrack]:
    """Ground tracks of the scheduled fixed movers (`wave=False`), for drawing and for verify_separation."""
    return list(_get(wave).tracks)


def reservations(wave: Wave) -> Reservations:
    """The fixed traffic's reservation table, cached on `route.wave_key(wave)`. Shared: copy it before scheduling wave
    flights against it."""
    return _get(wave).reservations


def _cache_clear() -> None:
    with _LOCK:
        _CACHE.clear()
        _GEN[0] += 1


reservations.cache_clear = _cache_clear        # type: ignore[attr-defined]


def hold_stats(ft: FixedTraffic) -> dict:
    """Departures' dwell on the holding-point node (arrival there to takeoff) and their taxi waits upstream (the
    queue), in minutes: n, median, p95, max, count over the 30-min horizon, the most of those at one node."""
    hp = sorted(h for h, _, _ in ft.stats.get("hold_point_min") or ())
    qw = sorted(ft.stats.get("queue_wait_min") or ())
    over = Counter(node for h, _, node in ft.stats.get("hold_point_min") or () if h > surface.HORIZON_MIN)

    def q(xs, f):
        return xs[min(len(xs) - 1, int(f * len(xs)))] if xs else 0.0
    return {"n": len(hp), "median": q(hp, 0.5), "p95": q(hp, 0.95), "max": hp[-1] if hp else 0.0,
            "over_30": sum(1 for h in hp if h > surface.HORIZON_MIN),
            "over_30_one_node": max(over.values()) if over else 0,
            "queue_median": q(qw, 0.5), "queue_max": qw[-1] if qw else 0.0}


def provenance(ft: FixedTraffic, partial_source: str) -> Provenance:
    deps = [m for m in ft.movers if m.kind == "departure"]
    arrs = [m for m in ft.movers if m.kind == "arrival"]
    mixed, arr_rw, cands = ft.mode
    parts = []
    if ft.real:
        dep_rw = sorted({m.runway for m in deps if m.runway})
        parts.append(f"{len(deps)} real departures (wheels-off T-30..T+120 min; stands simulated, seeded in the "
                     f"carrier's most-used pier) and {len(arrs)} queue arrivals")
        if arr_rw:
            parts.append(f"departure runway {'/'.join(dep_rw) or '-'}: "
                         + (f"mixed mode on {arr_rw}" if mixed else f"parallel to arrival runway {arr_rw}"))
    else:
        parts.append(f"partial: {len(arrs)} queue arrivals only, no departures (no BTS 2015 movements for this "
                     f"airport and day)")
    unrouted = [m for m in ft.movers if m.path is None]
    parts.append(f"scheduled over the 30-min horizon or with an unresolved conflict (still reserved): "
                 f"{len(ft.failures)}" + (f" ({', '.join(cs for cs, _ in ft.failures)})" if ft.failures else ""))
    vac = ft.stats.get("vacating") or []
    if vac:
        parts.append(f"fixed arrivals still on the runway at another fixed movement (data times): {len(vac)} "
                     f"({', '.join(sorted({v.split()[0] for v in vac}))})")
    if unrouted:
        parts.append(f"not routed on the OSM taxi graph (runway events only): {len(unrouted)} "
                     f"({', '.join(m.label for m in unrouted)})")
    relabel = [m.label for m in ft.movers if m.label != m.callsign]
    if relabel:
        parts.append(f"departures sharing a wave or queue arrival's callsign, labelled: {', '.join(relabel)}")
    if ft.takeoffs_shifted:
        parts.append(f"{ft.takeoffs_shifted} takeoffs after WHEELS_OFF because of taxi waits")
    deps_n = sum(1 for m in ft.movers if m.kind == "departure")
    if deps_n:
        parts.append(f"takeoffs later than WHEELS_OFF: {ft.stats.get('late', 0)} of {deps_n}")
    if ft.stats.get("takeoffs_held"):
        parts.append(f"{ft.stats['takeoffs_held']} takeoffs held at the holding point for takeoff spacing, a "
                     f"landing or a crossing")
    hs = hold_stats(ft)
    if hs["n"]:
        parts.append(f"departures waiting for takeoff in the holding bay (at most {BAY_CAPACITY}, then queued "
                     f"upstream): median "
                     f"{hs['median']:.1f}, p95 {hs['p95']:.1f}, max {hs['max']:.1f} min, {hs['over_30']} over 30 min "
                     f"(at most {hs['over_30_one_node']} at one node); upstream queue waits median "
                     f"{hs['queue_median']:.1f}, max {hs['queue_max']:.1f} min")
    dup = ft.stats.get("duplicate_departures") or []
    if dup:
        parts.append(f"repeated departure callsigns in the window, first kept: {len(dup)} ({', '.join(dup)})")
    if ft.stats.get("no_taxi_out"):
        parts.append(f"departures without BTS TAXI_OUT (pushback from the unimpeded taxi): {ft.stats['no_taxi_out']}")
    detail = "; ".join(parts)
    if ft.real:
        return Provenance("ground_traffic", "real", SRC_REAL, detail)
    return Provenance("ground_traffic", "generic", partial_source, detail)


def annotate(wave: Wave, partial_source: str = SRC_PARTIAL) -> Wave:
    """Schedule the wave's fixed traffic, append the `ground_traffic` provenance (counts, and every mover that is not
    scheduled or not routed, never dropped silently) and cache the result under the wave's final content key."""
    from backend.wave.route import wave_key
    ft = _compute(wave)
    wave.provenance.append(provenance(ft, partial_source))
    wave.__dict__.pop("_key", None)    # the key hashes the provenance: recompute it now
    k = wave_key(wave)
    _put(k, ft)
    wave.__dict__[_PIN] = (k, _GEN[0], ft)
    return wave
