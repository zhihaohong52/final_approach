"""Fixed ground traffic (spec section 6.4): the wave's queue arrivals and, where the BTS 2015 movements exist, the real
departures in [T0 - 30, T0 + 120] min, scheduled once per wave on the OSM taxi graph, first come first served by
start time, into the reservation table that the wave flights give way to (v2's convention: fixed traffic is never
vetoed). Pure and deterministic; no model decides anything here.

- Queue arrival: lands at `slot_min` on its runway (a landing runway event), exits by `surface.choose_exit` after its
  ROT (`t_start` = exit time) and taxis to a seeded stand.
- Departure: pushback at WHEELS_OFF - TAXI_OUT (`t_start`), from a seeded stand (simulated) in the carrier's most-used
  pier, to the holding point of its departure runway; takeoff (a runway event) at WHEELS_OFF, or later if the taxi
  waits made it reach the holding point after that.
- Departure runway (assumption, `rules.LIMITS` "departure runway"): the airport is mixed-mode, and departures use the
  arrival flow's runway (the majority `moves.fcfs_runways` end), unless an open end parallel to it (heading within
  10 degrees, same direction, another strip) carries no arrivals; then departures use the one of those ends whose
  threshold is nearest the departure stand.
"""
from __future__ import annotations

import math
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
    else the free parallel ends (the departure picks the one nearest its stand)."""
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
    return (False, maj, free) if free else (True, maj, [maj])


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
    """Stand -> holding point of `strip`: the reverse of the entry-rooted fastest path (one cached Dijkstra per runway
    entry, not per stand), timed in the forward direction, cut at the last node before the tail that is inside the
    strip's protected zone or on any runway's pavement. The final 30 s (PARK_S) is kept as a line-up buffer: the
    departure holds the holding-point node for 30 s, then lines up; it is not drawn queueing there until WHEELS_OFF
    (a long TAXI_OUT would block the node and reorder every later takeoff)."""
    nodes = list(reversed(surface.unimpeded(g, entry, stand_node).nodes))
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
    stats = {"no_taxi_out": 0, "callsign_collisions": []}
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
            if m.kind != "departure" or m.wheels_min_abs is None or m.callsign in seen:
                continue
            off = float(m.wheels_min_abs - t0)
            if -WINDOW_BEFORE_MIN - _TOL <= off <= WINDOW_AFTER_MIN + _TOL:
                seen.add(m.callsign)
                todo.append((off, "departure", m.callsign, m))
    todo.sort(key=lambda x: (x[0], x[2], x[1]))
    wave_cs = {f.callsign for f in wave.flights} | {q.callsign for q in wave.queue}
    hw = {x.runway: x.headwind_kt - x.tailwind_kt for x in wave.runway_wind}
    out: list[FixedMover] = []
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
        if cs in wave_cs:
            stats["callsign_collisions"].append(cs)
        mixed, arr_rw, cands = mode
        rwy = None
        if cands:
            if st is not None and len(cands) > 1:
                by_id = {r.id: r for r in wave.airport.runways}
                la, lo = st[1]["lat"], st[1]["lon"]

                def dist(rid: str) -> float:
                    r = by_id[rid]
                    if r.le_lat is None or r.le_lon is None:
                        return math.inf
                    kx = math.cos(math.radians(la))
                    return math.hypot((r.le_lon - lo) * kx, r.le_lat - la)
                rwy = min(cands, key=lambda r: (dist(r), r))
            else:
                rwy = cands[0]
        strip = _strip_key(g, rwy) if rwy else ""
        path, why = None, None
        if rwy is None:
            why = "no open runway"
        elif g is None:
            why = "no OSM surface graph"
        elif st is None:
            why = "no stand in the main taxi network"
        else:
            entry = _entry_node(g, rwy, main)
            if entry is None:
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
        out.append(FixedMover(cs, "departure", rwy or "", snode, ev - taxi, path, ev, strip, skey, why))
    out.sort(key=lambda m: (m.t_start_min, m.callsign))
    return out, mode, mv is not None, stats


def _compute(wave: Wave) -> FixedTraffic:
    g = surface.load_graph(wave.airport.icao)
    movers, mode, real, stats = _build_movers(wave, g)
    res = Reservations({}, {}, {})
    for m in movers:
        if m.strip:
            res.runway_events.setdefault(m.strip, []).append(
                (m.event_min, "landing" if m.kind == "arrival" else "takeoff", m.callsign))
    for lst in res.runway_events.values():
        lst.sort()
    scheduled: dict[str, Scheduled] = {}
    tracks: list[GroundTrack] = []
    failures: list[tuple[str, str]] = []
    shifted = 0
    for m in movers:
        if m.path is None or g is None:
            continue
        # schedule() does not skip the mover's own runway event: hide it (shared node/edge tables, filtered events)
        view = Reservations(res.node_busy, res.edge_dir,
                            {s: [e for e in lst if e[2] != m.callsign] for s, lst in res.runway_events.items()})
        s = surface.schedule(g, m.path, m.t_start_min, view, m.callsign)
        scheduled[m.callsign] = s
        if not s.ok:
            failures.append((m.callsign, s.reason or "not scheduled"))
        if m.kind == "departure":
            leave = s.points[-1]["t_min"]
            if leave > m.event_min + _TOL:
                lst = res.runway_events[m.strip]
                i = lst.index((m.event_min, "takeoff", m.callsign))
                lst[i] = (leave, "takeoff", m.callsign)
                lst.sort()
                shifted += 1
        if s.reason == "hold-short search did not converge":
            continue                     # nothing was reserved: no track to draw or verify
        names = [n for i, n in enumerate(m.path.names) if n and (i == 0 or m.path.names[i - 1] != n)]
        tracks.append(GroundTrack(m.callsign, m.kind, False, m.runway, m.stand, names, s.points, s.waits,
                                  m.path.unimpeded_min, s.points[-1]["t_min"] - m.t_start_min, m.path.src))
    # A takeoff moved later (above) was not seen by the movers scheduled before it: re-check every track's runway
    # gaps against the final events with the independent verifier (one track at a time: checks 3 and 4 only).
    # A broken crossing gap is a failure; an arrival still vacating at another fixed movement's takeoff or landing
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
    return FixedTraffic(movers, res, scheduled, tracks, failures, mode, real, shifted, stats)


_CACHE: dict[str, FixedTraffic] = {}
_CACHE_MAX = 16


def _get(wave: Wave) -> FixedTraffic:
    from backend.wave.route import wave_key
    k = wave_key(wave)
    hit = _CACHE.get(k)
    if hit is None:
        hit = _compute(wave)
        _put(k, hit)
    return hit


def _put(k: str, ft: FixedTraffic) -> None:
    while len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
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


reservations.cache_clear = _CACHE.clear        # type: ignore[attr-defined]


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
    parts.append(f"not scheduled (wait over the 30-min horizon, cannot hold short, or crossing gap broken by a "
                 f"delayed takeoff): {len(ft.failures)}"
                 + (f" ({', '.join(cs for cs, _ in ft.failures)})" if ft.failures else ""))
    vac = ft.stats.get("vacating") or []
    if vac:
        parts.append(f"fixed arrivals still on the runway at another fixed movement (data times): {len(vac)} "
                     f"({', '.join(sorted({v.split()[0] for v in vac}))})")
    if unrouted:
        parts.append(f"not routed on the OSM taxi graph (runway events only): {len(unrouted)} "
                     f"({', '.join(m.callsign for m in unrouted)})")
    if ft.takeoffs_shifted:
        parts.append(f"{ft.takeoffs_shifted} takeoffs after WHEELS_OFF because of taxi waits")
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
    _put(wave_key(wave), ft)
    return wave
