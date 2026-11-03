import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { TrajectorySet, WaveGeo } from "../api/types";
import { icao, icaoText } from "../callsign";
import { activeWait, waitTag, endOfLine, bearing, positionAt, projector, runwayCrossings, secondFloor, type TrackPoint, type Wait } from "../geo/track";
import type { Tone } from "./Radar";

// The airport surface, as on a ground-movement display: runways, taxiways and taxilanes, stands and runway
// holding positions drawn to scale from OpenStreetMap (WaveGeo.surface), and each aircraft on the ground at its
// place on the engine's taxi track at the replay clock. The picture refreshes once a second, like surface
// movement radar (an assumption, see spec §9). Taxi tracks are the engine's own routes; a synthesized one
// (no usable OSM graph for that runway end) is dashed. Positions are replayed from the plan, not surveillance.
// The fixed 2015 traffic (WaveGeo.ground_traffic, the same for every plan) moves in grey. A taxiing aircraft that
// is waiting shows an amber bar across its taxiway with the reason on hover (the fixed traffic's own waits are
// dim); a runway flashes while someone crosses it; the shown plan's failing ATC-TAXI-CONFLICT checks are red
// markers, and the informational ones (a fixed crosser or departure holding for a plan landing) are grey rings.
const ZOOMS = [1, 2, 4];
const TAXIWAY_M = 23;              // drawn width of a taxiway (ICAO code E/F), metres
const TAXILANE_M = 12;             // apron taxilanes are narrower
const RUNWAY_M = 45;               // when OSM has no width
const HOLD_BAR_M = 40;             // a runway holding position is drawn as a bar across its taxiway
const SNAP_M = 60;                 // a holding position takes its orientation from a taxiway within this distance
const LEAD = 18;                   // leader line, px
const PAD = 22;
const TOP = 84, BOTTOM = 64;       // bands kept clear of the field at the fitted zoom
const WAIT_BAR_M = 50;             // a hold-short / give-way wait is drawn as a bar across the taxiway, metres
const WAIT_AHEAD_M = 30;           // ... at the line the aircraft holds at, just ahead of its nose
const FLASH_BEFORE = 0.1, FLASH_AFTER = 0.15;   // a crossed runway flashes from 6 s before to 9 s after the centreline, min
const INFO_PAD = 1;                // an informational conflict shows from 1 min before to 1 min after its hold, min
const AIRFRAME = "M0 -6L1.2 -1.5L6 1.5L6 2.6L1.2 1.6L0.9 4.6L2.4 5.8L2.4 6.6L0 6L-2.4 6.6L-2.4 5.8L-0.9 4.6L-1.2 1.6L-6 2.6L-6 1.5L-1.2 -1.5Z";

type XY = [number, number];
type Surf = NonNullable<WaveGeo["surface"]>;
type Ground = TrajectorySet["ground"][number];
type Traffic = NonNullable<WaveGeo["ground_traffic"]>;
type Conflict = TrajectorySet["conflicts"][number];
type Hover = { key: string; text: string; at: XY } | null;

interface Props {
  geo: WaveGeo;
  traj: TrajectorySet | null;      // the engine's paths for the plan on screen; null = map only
  t: number;                       // replay clock, minutes vs T0 (shared with the radar)
  tone: (cs: string) => Tone;
  selected: string | null;
  onSelect: (cs: string | null) => void;
  focus?: string | null;           // the disrupted flight: the zoomed map centres on it when nothing is selected
  updating?: boolean;
  active?: boolean;                // false while its pane is hidden: it then skips rendering entirely
  rejected?: RejectedGround | null; // a move the engine just vetoed: its failing ground conflicts, shown briefly
}

/** The failing ATC-TAXI-CONFLICT entries of the plan a rejected move would have produced (POST /api/trajectories
 *  with that move), keyed by the stream message that carried the rejection. */
export interface RejectedGround { seq: number; label: string; conflicts: TrajectorySet["conflicts"] }

/** Everything static about the surface, in metres around the field's centre. Computed once per wave. */
function prepare(s: Surf) {
  const all = [...s.runways.flatMap((r) => r.line), ...s.taxiways.flatMap((w) => w.line)];
  // extents by a loop: spreading tens of thousands of points into Math.min can overflow the call stack
  const ext = (xs: number[][], i: number) => xs.reduce((m, p) => [Math.min(m[0], p[i]), Math.max(m[1], p[i])], [Infinity, -Infinity]);
  const [la0, la1] = ext(all, 0), [lo0, lo1] = ext(all, 1);
  const lat0 = all.length ? (la0 + la1) / 2 : s.stands[0]?.lat ?? 0;
  const lon0 = all.length ? (lo0 + lo1) / 2 : s.stands[0]?.lon ?? 0;
  const pj = projector(lat0, lon0);
  const m = (p: [number, number]) => pj(p[0], p[1]);
  const runways = s.runways.map((r) => ({ ...r, xy: r.line.map(m) }));
  const taxiways = s.taxiways.map((w) => ({ ...w, xy: w.line.map(m) }));
  const stands = s.stands.map((st) => ({ ...st, xy: pj(st.lat, st.lon) }));
  const pts = [...runways.flatMap((r) => r.xy), ...taxiways.flatMap((w) => w.xy)];
  if (!pts.length) pts.push(...stands.map((st) => st.xy));
  const [x0, x1] = ext(pts, 0), [y0, y1] = ext(pts, 1);
  const box = pts.length ? { x0, x1, y0, y1 } : { x0: -1000, x1: 1000, y0: -1000, y1: 1000 };
  // holding positions: a bar across the nearest taxiway segment (OSM gives only the point)
  const segs: [XY, XY][] = taxiways.flatMap((w) => w.xy.slice(1).map((b, i): [XY, XY] => [w.xy[i], b]));
  const holds = s.holding_positions.map((h) => {
    const p = pj(h.lat, h.lon);
    let best = SNAP_M, dir: XY | null = null;
    for (const [a, b] of segs) {
      const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
      if (!L2 || Math.abs(p[0] - a[0]) > 400 || Math.abs(p[1] - a[1]) > 400) continue;
      const u = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2));
      const d = Math.hypot(a[0] + u * dx - p[0], a[1] + u * dy - p[1]);
      if (d < best) { best = d; const L = Math.sqrt(L2); dir = [dx / L, dy / L]; }
    }
    return { ...h, xy: p, dir };
  });
  return { pj, runways, taxiways, stands, holds, box };
}

type Prepared = ReturnType<typeof prepare>;

/** A wait with the direction of the track where it stands (unit vector in metres, east/north), so its bar can
 *  be drawn across the taxiway. */
type DirWait = Wait & { xy: XY; dir: XY | null };

/** The track's direction at a wait: along the first segment of non-zero length at or after the wait starts
 *  (the stop itself repeats a point), else the last one before it. */
function waitsWithDir(pj: Prepared["pj"], points: TrackPoint[], waits: Wait[]): DirWait[] {
  const xy = points.map((q) => pj(q.lat, q.lon));
  return waits.map((w) => {
    let i = 0;
    while (i < points.length - 1 && points[i + 1].t_min <= w.from_min) i++;
    const seg = (j: number): XY | null => {
      const a = xy[j], b = xy[j + 1];
      if (!a || !b) return null;
      const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      return L > 1 ? [(b[0] - a[0]) / L, (b[1] - a[1]) / L] : null;
    };
    let dir: XY | null = null;
    for (let j = i; j < xy.length - 1 && !dir; j++) dir = seg(j);
    for (let j = i - 1; j >= 0 && !dir; j--) dir = seg(j);
    return { ...w, xy: pj(w.lat, w.lon), dir };
  });
}

/** Runway crossings of a set of tracks, flattened in time order. */
function crossingsOf(g: Prepared, tracks: { callsign: string; points: TrackPoint[] }[], fixed: boolean) {
  const rws = g.runways.map((r) => r.xy);
  return tracks.flatMap((x) => runwayCrossings(x.points.map((q): [number, number, number] => { const [e, n] = g.pj(q.lat, q.lon); return [q.t_min, e, n]; }), rws)
    .map((c) => ({ ...c, cs: x.callsign, fixed })));
}

/** The fixed 2015 traffic, once per wave: tuples become track points and waits (with their direction), and its
 *  runway crossings are found. Per tick only the tracks whose time span covers the clock are sampled. */
function prepareTraffic(gt: Traffic, g: Prepared) {
  const tracks = gt.tracks.filter((x) => x.points.length > 0).map((x) => {
    const points: TrackPoint[] = x.points.map(([t_min, lat, lon]) => ({ t_min, lat, lon }));
    const waits = waitsWithDir(g.pj, points, x.waits.map(([from_min, to_min, lat, lon, reason]) => ({ from_min, to_min, lat, lon, reason })));
    // a wait can start before the first point (a departure held at its stand): the span covers it
    const t0 = Math.min(points[0].t_min, ...waits.map((w) => w.from_min));
    const t1 = Math.max(points[points.length - 1].t_min, ...waits.map((w) => w.to_min));
    return { callsign: x.callsign, kind: x.kind, runway: x.runway, stand: x.stand, points, waits, t0, t1 };
  });
  return { tracks, crossings: crossingsOf(g, tracks, true) };
}

/** The first crossing at or after t in a time-ordered list (binary search). */
function firstFrom(cs: { t: number }[], t: number): number {
  let lo = 0, hi = cs.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (cs[mid].t < t) lo = mid + 1; else hi = mid; }
  return lo;
}

type Box = [number, number, number, number];   // x0, y0, x1, y1 in screen px
const overlap = (a: Box, b: Box) => Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
const MONO_PX = 7.2;               // advance of a 12 px IBM Plex Mono character
const rwEndBox = (x: number, y: number, id: string): Box => [x - id.length * 3.4 - 2, y - 6, x + id.length * 3.4 + 2, y + 8];

/** Runway-end labels on screen: each designator beyond the end it is landed from, 14 px out along the runway;
 *  one that would overlap a label already placed (close parallels: 18R/18L) steps further out, 16 px at a time,
 *  and is dropped after three tries, as is a second label of the same name next to the first (a runway OSM maps
 *  twice). Labels off the map or under the key (right of keyX, above top) are left out.
 *  `xy` is the text's centre; shared by the airfield layer and the data-block placement, so they agree. */
function runwayEnds(g: Prepared, P: (q: XY) => XY, W: number, H: number, top: number, keyX: number) {
  const out: { key: string; id: string; xy: XY; box: Box }[] = [];
  g.runways.forEach((r, i) => {
    if (r.xy.length < 2) return;
    const first = r.xy[0], last = r.xy[r.xy.length - 1];
    for (const id of r.ids) {
      const end = endOfLine(id, first, last) === 0 ? first : last, other = end === first ? last : first;
      const a = P(end), b = P(other), L = Math.hypot(a[0] - b[0], a[1] - b[1]) || 1;
      for (const off of [14, 30, 46]) {
        const xy: XY = [a[0] + ((a[0] - b[0]) / L) * off, a[1] + ((a[1] - b[1]) / L) * off];
        if (xy[0] < -40 || xy[0] > W + 40 || xy[1] < -40 || xy[1] > H + 40 || (xy[0] + 16 > keyX && xy[1] < top)) break;
        if (out.some((o) => o.id === id && Math.hypot(o.xy[0] - xy[0], o.xy[1] - xy[1]) < 40)) break;   // OSM maps some runways twice
        const box = rwEndBox(xy[0], xy[1] + 4, id);
        if (out.some((o) => overlap(o.box, box) > 0)) continue;
        out.push({ key: `${i}-${id}`, id, xy, box });
        break;
      }
    }
  });
  return out;
}

/** A data block's place: the first of NE, NW, SE, SW (leader `LEAD` px) that overlaps nothing already placed and
 *  stays on the map; failing that, the one that overlaps least. Returns the leader end, text anchor and box. */
function placeBlock(x: number, y: number, w: number, taken: Box[], view: Box) {
  const opts = [[1, -1], [-1, -1], [1, 1], [-1, 1]].map(([ex, ny]) => {
    // north: the leader ends at the first line's baseline; south: at the block's top edge
    const lx = x + ex * LEAD, ey = y + ny * LEAD, ly = ny < 0 ? ey : ey + 14;
    const x0 = ex > 0 ? lx : lx - w - 4;
    const b: Box = [x0, ly - 14, x0 + w + 4, ly + 16];
    const out = (b[2] - b[0]) * (b[3] - b[1]) - overlap(b, view);
    return { lx, ly, ey, east: ex > 0, box: b, cost: taken.reduce((m, o) => m + overlap(b, o), 0) + 2 * out };
  });
  return opts.find((o) => o.cost === 0) ?? opts.reduce((m, o) => (o.cost < m.cost ? o : m));
}


/** Screen position of a point in metres: the view is the scale (px per metre) and the centre (metres). */
const screen = (W: number, H: number, S: number, cx: number, cy: number, top: number) => (q: XY): XY =>
  [W / 2 + (q[0] - cx) * S, top + (H - top - BOTTOM) / 2 - (q[1] - cy) * S];
const polyline = (P: (q: XY) => XY, xy: XY[]) => xy.map((q) => P(q).map((v) => v.toFixed(1)).join(",")).join(" ");

/** The airfield itself: taxiways, runways, holding positions and stands. It depends only on the surface and the
 *  view, so the once-a-second clock re-renders just the aircraft and their routes. */
const Airfield = memo(function Airfield({ g, W, H, S, cx, cy, top, keyX }: { g: Prepared; W: number; H: number; S: number; cx: number; cy: number; top: number; keyX: number }) {
  const P = screen(W, H, S, cx, cy, top);
  const tw = (m: number, min: number) => Math.max(min, m * S);
  const inView = ([x, y]: XY) => x > -40 && x < W + 40 && y > -40 && y < H + 40;
  // runway end labels: the designator at the end it is landed from, never under the key or on another label
  const ends = runwayEnds(g, P, W, H, top, keyX);
  const r = Math.max(1.5, Math.min(4, 9 * S));
  return (
    <>
      <g className="sf-taxi">
        {g.taxiways.map((w, i) => (
          <polyline key={i} className={w.kind} points={polyline(P, w.xy)} strokeWidth={w.kind === "taxilane" ? tw(TAXILANE_M, 0.8) : tw(TAXIWAY_M, 1.4)}>
            {w.name && <title>{`${w.kind === "taxilane" ? "Taxilane" : "Taxiway"} ${w.name}`}</title>}
          </polyline>
        ))}
      </g>
      <g className="sf-rwy">
        {g.runways.map((rw, i) => (
          <g key={i}>
            <polyline points={polyline(P, rw.xy)} strokeWidth={tw(rw.width_m ?? RUNWAY_M, 3)}><title>{`Runway ${rw.ids.join("/")}${rw.width_m ? ` · ${Math.round(rw.width_m)} m wide` : ""}`}</title></polyline>
            {(rw.width_m ?? RUNWAY_M) * S >= 5 && <polyline className="cl" points={polyline(P, rw.xy)} />}
          </g>
        ))}
        {ends.map((e) => <text key={e.key} className="rw-end" x={e.xy[0]} y={e.xy[1] + 4} textAnchor="middle">{e.id}</text>)}
      </g>
      <g className="sf-hold">
        {g.holds.map((h) => {
          const [x, y] = P(h.xy);
          if (!inView([x, y])) return null;
          const half = Math.max(3, (HOLD_BAR_M * S) / 2);
          const d = h.dir ? [h.dir[1], h.dir[0]] : null;          // across the taxiway (screen y is flipped)
          return (
            <g key={h.id}>
              <title>{`Runway holding position${h.runway ? ` · ${h.runway}` : ""}`}</title>
              {d ? <line x1={x - d[0] * half} y1={y - d[1] * half} x2={x + d[0] * half} y2={y + d[1] * half} /> : <circle cx={x} cy={y} r={1.6} />}
            </g>
          );
        })}
      </g>
      <g className="sf-stand">
        {g.stands.map((st) => {
          const [x, y] = P(st.xy);
          if (!inView([x, y])) return null;
          return <rect key={st.id} x={x - r} y={y - r} width={2 * r} height={2 * r}><title>{`Stand ${st.gate ?? st.id}${st.geo_src ? ` · ${st.geo_src}` : ""}`}</title></rect>;
        })}
      </g>
    </>
  );
});

/** Hidden behind the radar it does not re-render at all; shown, only the aircraft layer follows the clock. */
export const Surface = memo(SurfaceMap, (a, b) => a.active === false && b.active === false);

function SurfaceMap({ geo, traj, t, tone, selected, onSelect, focus = null, updating = false, rejected = null }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [[W, H], setSize] = useState([720, 480]);
  const [zoom, setZoom] = useState(1);
  const centre = useRef<{ who: string | null; xy: XY } | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => { if (e.contentRect.width > 0) setSize([Math.max(280, Math.round(e.contentRect.width)), Math.max(320, Math.round(e.contentRect.height))]); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const surf = geo.surface;
  const g = useMemo(() => (surf && surf.status === "real" ? prepare(surf) : null), [surf]);
  const gt = geo.ground_traffic ?? null;
  const traffic = useMemo(() => (g && gt ? prepareTraffic(gt, g) : null), [g, gt]);
  // the plan's own tracks: waits with their direction and runway crossings, once per trajectory set
  const plan = useMemo(() => {
    if (!g || !traj) return null;
    const ground = traj.ground.filter((x) => x.points.length > 0);
    return {
      waits: new Map(ground.map((x) => [x.callsign, waitsWithDir(g.pj, x.points, x.waits)])),
      crossings: crossingsOf(g, ground.filter((x) => x.src !== "synth"), false),   // a synthesized straight line crosses nothing real (spec §6.4)
      conflicts: traj.conflicts.map((c) => ({ ...c, xy: g.pj(c.lat, c.lon) })),
    };
  }, [g, traj]);
  // every crossing, the plan's and the fixed traffic's, in time order: a tick finds its window by binary search
  const crossings = useMemo(() => [...(traffic?.crossings ?? []), ...(plan?.crossings ?? [])].sort((a, b) => a.t - b.t), [traffic, plan]);
  const [hover, setHover] = useState<Hover>(null);
  const attribution = surf?.attribution || "© OpenStreetMap contributors";

  if (!surf) return <div ref={box} className="surface-map empty-surface"><p className="empty">Surface not available for this wave.</p></div>;
  if (!g) {
    return (
      <div ref={box} className="surface-map empty-surface">
        <p className="empty">surface not modelled for this airport</p>
        <p className="empty">Taxi times use a straight line from the runway to the gate.</p>
        <p className="surface-attr">{attribution}</p>
      </div>
    );
  }

  // ---------- the aircraft on the ground at this second ----------
  const ts = secondFloor(t);
  const tracks: Ground[] = (traj?.ground ?? []).filter((x) => x.points.length > 0);
  const toneOf = (x: Ground): Tone => (x.wave && x.kind === "arrival" ? tone(x.callsign) : "fixed");
  type Ac = { x: Ground; p: XY; hdg: number; seg: number; tone: Tone };
  const acs: Ac[] = [];
  for (const x of tracks) {
    const now = positionAt(x.points, ts);
    if (!now) continue;                               // still airborne, or on blocks
    acs.push({ x, p: g.pj(now.lat, now.lon), hdg: headingAt(g.pj, x.points, now.seg), seg: now.seg, tone: toneOf(x) });
  }
  // the fixed 2015 traffic at this second: only the tracks whose span covers it are sampled
  type Fx = { x: NonNullable<typeof traffic>["tracks"][number]; p: XY; hdg: number; wait: DirWait | null };
  const fixedNow: Fx[] = [];
  for (const x of traffic?.tracks ?? []) {
    if (ts < x.t0 || ts > x.t1) continue;
    const wait = activeWait(x.waits, ts);
    // waiting before its first point (or after its last) it stands at that end of its track
    const now = positionAt(x.points, ts) ?? (wait ? { ...x.points[ts < x.points[0].t_min ? 0 : x.points.length - 1], seg: ts < x.points[0].t_min ? 0 : x.points.length - 1 } : null);
    if (now) fixedNow.push({ x, p: g.pj(now.lat, now.lon), hdg: headingAt(g.pj, x.points, now.seg), wait });
  }
  // waits in progress: the plan's (amber) and the fixed traffic's (dim)
  const waitsNow = [
    ...fixedNow.flatMap((f) => (f.wait ? [{ w: f.wait, cs: f.x.callsign, fixed: true }] : [])),
    ...acs.flatMap((a) => { const w = a.tone === "fixed" ? null : activeWait(plan?.waits.get(a.x.callsign) ?? [], ts); return w ? [{ w, cs: a.x.callsign, fixed: false }] : []; }),
  ];
  // runways being crossed now: one flash per runway, the plan's crossers outrank the fixed traffic
  const flashing = new Map<number, { fixed: boolean; who: string[] }>();
  for (let k = firstFrom(crossings, ts - FLASH_AFTER); k < crossings.length && crossings[k].t <= ts + FLASH_BEFORE; k++) {
    const c = crossings[k], f = flashing.get(c.rw);
    flashing.set(c.rw, { fixed: (f?.fixed ?? true) && c.fixed, who: [...(f?.who ?? []), c.cs] });
  }
  const blinkOn = Math.round(ts * 60) % 2 === 0;           // whole-second ticks: the flash alternates each second
  // the plan's ATC-TAXI-CONFLICT entries: failing ones always (they are why it was rejected), informational near their time
  const failing = (plan?.conflicts ?? []).filter((c) => !c.kind);
  const info = (plan?.conflicts ?? []).filter((c) => c.kind && ts >= c.at_min - INFO_PAD && ts <= c.at_min + (c.hold_min ?? 0) + INFO_PAD);
  // a hover label stays only while what it describes is on the map
  const shownKeys = new Set([...waitsNow.map(({ w, cs }) => `w-${cs}-${w.from_min}`), ...failing.map(hoverKey), ...info.map(hoverKey)]);

  // ---------- the key, top right: a row for what is on the map now; the band is sized for every row this plan
  // can show, so the field does not jump when one appears ----------
  const anySynth = acs.some((a) => a.tone !== "fixed" && a.x.src === "synth");
  const KEYS = [
    { id: "synth", text: "dashed = synthesized taxi route", can: tracks.some((x) => x.wave && x.src === "synth"), on: anySynth },
    { id: "wait", text: "amber bar = waiting, reason on hover", can: [...(plan?.waits.values() ?? [])].some((ws) => ws.length > 0), on: waitsNow.some((w) => !w.fixed) },
    { id: "cross", text: "bright runway = being crossed", can: crossings.length > 0, on: flashing.size > 0 },
    { id: "info", text: "grey ring = fixed traffic holds for a landing", can: (plan?.conflicts ?? []).some((c) => c.kind), on: info.length > 0 },
    { id: "conflict", text: "red = taxi conflict, plan rejected", can: failing.length > 0, on: failing.length > 0 },
  ];
  const keyRows = KEYS.filter((k) => k.can);
  const keys = KEYS.filter((k) => k.on).map((k) => ({ ...k, w: k.text.length * 6.6 + 8 }));
  const top = TOP + 16 * Math.max(0, keyRows.length - 1);
  const keyX = W - 16 - Math.max(0, ...keyRows.map((k) => k.text.length * 6.6 + 8 + 26));   // left edge of the key

  // ---------- view: fit the field, or zoom on the selected (else the disrupted) aircraft ----------
  const bw = Math.max(200, g.box.x1 - g.box.x0), bh = Math.max(200, g.box.y1 - g.box.y0);
  // the field fits between the top band (clock, toggles, zoom, key) and the bottom band (key, scale, notes)
  const S = Math.min((W - 2 * PAD) / bw, (H - top - BOTTOM) / bh) * zoom;     // px per metre
  const who = selected ?? focus;
  const target = zoom > 1 ? acs.find((a) => a.x.callsign === who)?.p ?? standXY(who) : null;
  function standXY(cs: string | null): XY | null {
    const x = tracks.find((tr) => tr.callsign === cs);
    const st = x && g!.stands.find((s) => s.id === x.stand || s.gate === x.stand);
    return st ? st.xy : null;
  }
  // zoomed in, the view re-centres only when the followed aircraft nears the edge (as a ground display pages),
  // so the airfield layer is redrawn on those pages, not every second
  if (!target) centre.current = null;
  else {
    const cur = centre.current;
    const off = cur && cur.who === who ? Math.max(Math.abs(target[0] - cur.xy[0]) * S / (W / 2), Math.abs(target[1] - cur.xy[1]) * S / ((H - top - BOTTOM) / 2)) : Infinity;
    if (off > 0.6) centre.current = { who, xy: target };
  }
  const c: XY = centre.current?.xy ?? [(g.box.x0 + g.box.x1) / 2, (g.box.y0 + g.box.y1) / 2];
  const P = screen(W, H, S, c[0], c[1], top);
  const line = (xy: XY[]) => polyline(P, xy);

  // ---------- what the tracks on screen use: stands and taxiway names get labels, the rest stay quiet ----------
  const shownTracks = tracks.filter((x) => x.wave && x.kind === "arrival");
  const standUsed = new Map<string, Ground>();
  for (const x of shownTracks) standUsed.set(x.stand, x);
  const usedStands = g.stands.flatMap((st) => { const x = standUsed.get(st.id) ?? (st.gate ? standUsed.get(st.gate) : undefined); return x ? [{ st, x, tn: toneOf(x) }] : []; });
  const labelTrack = shownTracks.find((x) => x.callsign === who) ?? null;
  const twLabels: { name: string; at: XY }[] = [];
  if (labelTrack) {
    const route = labelTrack.points.map((q) => g.pj(q.lat, q.lon));
    for (const name of [...new Set(labelTrack.route_names)].slice(0, 8)) {
      let best = Infinity, at: XY | null = null;
      for (const w of g.taxiways) if (w.name === name) for (const v of w.xy) for (const r of route) {
        const d = Math.hypot(v[0] - r[0], v[1] - r[1]);
        if (d < best) { best = d; at = v; }
      }
      if (at && best < 80) twLabels.push({ name, at });
    }
  }

  // remaining route of each negotiated aircraft on the ground; the selected one's is brighter
  const routes = acs.filter((a) => a.tone !== "fixed").map((a) => {
    const rest = a.x.points.slice(a.seg + 1).map((q) => g.pj(q.lat, q.lon));
    return { cs: a.x.callsign, tone: a.tone, dashed: a.x.src === "synth", xy: [a.p, ...rest] };
  });

  // a scale bar: the largest of 100/250/500/1000 m that fits in ~120 px
  const bar = [1000, 500, 250, 100].find((mm) => mm * S <= 130) ?? 100;
  const nOnGround = acs.filter((a) => a.tone !== "fixed").length;
  const sr = Math.max(1.5, Math.min(4, 9 * S));

  // ---------- labels that must not collide: runway ends, the selected and focus flights' stands, data blocks ----------
  const view: Box = [0, top, W, H - BOTTOM];
  const taken: Box[] = runwayEnds(g, P, W, H, top, keyX).map((e) => e.box);
  // only the selected and focus flights' stands are labelled; the others show their label on hover
  const standLabels = new Map(usedStands.filter(({ x }) => x.callsign === selected || x.callsign === focus).map(({ st, x }) => {
    const [px, py] = P(st.xy), name = st.gate ?? st.id;
    return [st.id, { cs: x.callsign, box: [px + sr + 2, py - 6, px + sr + 4 + name.length * 6.6, py + 8] as Box }];
  }));
  taken.push(...[...standLabels.values()].map((l) => l.box));
  // data blocks in priority order: selected, focus, then the rest; each takes the first free corner
  const rank = (cs: string) => (cs === selected ? 0 : cs === focus ? 1 : 2);
  const blocks = new Map<string, { lx: number; ly: number; ey: number; east: boolean; l2: string }>();
  for (const a of acs.filter((q) => q.tone !== "fixed").sort((p, q) => rank(p.x.callsign) - rank(q.x.callsign) || p.x.callsign.localeCompare(q.x.callsign))) {
    const cs = a.x.callsign, [x, y] = P(a.p);
    const wt = waitsNow.find((w) => w.cs === cs && !w.fixed)?.w ?? null;
    // line 2: the wait (a second channel beside the amber bar) while there is one, else exit and stand
    const l2 = wt ? `${waitTag(wt.reason)} ${Math.max(0, wt.to_min - ts).toFixed(1)}` : `${a.x.exit ? `${a.x.exit} ` : ""}→ ${a.x.stand}`;
    const o = placeBlock(x, y, Math.max(icao(cs).length, l2.length) * MONO_PX, taken, view);
    taken.push(o.box);
    blocks.set(cs, { lx: o.lx, ly: o.ly, ey: o.ey, east: o.east, l2 });
    // a stand label under its own flight's data block gives way to it
    for (const [id, l] of standLabels) if (l.cs === cs && overlap(l.box, o.box) > 0) standLabels.delete(id);
  }

  return (
    <div ref={box} className="surface-map">
      <div className="range seg" role="group" aria-label="Surface zoom">
        {ZOOMS.map((z) => <button key={z} aria-pressed={zoom === z} onClick={() => setZoom(z)} title={z === 1 ? "The whole field" : `×${z}, centred on ${who ? icao(who) : "the field"}`}>{z === 1 ? "Field" : `×${z}`}</button>)}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" onClick={() => onSelect(null)}
        aria-label={`Airport surface: ${nOnGround} negotiated aircraft taxiing, ${fixedNow.length} other aircraft moving${failing.length ? `, ${failing.length} taxi conflict${failing.length > 1 ? "s" : ""}` : ""}`}>
        <rect width={W} height={H} fill="var(--scope)" />
        <Airfield g={g} W={W} H={H} S={S} cx={c[0]} cy={c[1]} top={top} keyX={keyX} />
        {/* the stands the plan's taxiing aircraft are bound for */}
        <g className="sf-stand">
          {usedStands.map(({ st, tn }) => {
            const [x, y] = P(st.xy);
            return (
              <g key={st.id} className={`used blip ${tn}`}>
                <title>{`Stand ${st.gate ?? st.id}${st.geo_src ? ` · ${st.geo_src}` : ""}`}</title>
                <rect x={x - sr} y={y - sr} width={2 * sr} height={2 * sr} />
                <text className={`stand-label${standLabels.has(st.id) ? "" : " quiet"}`} x={x + sr + 3} y={y + 4}>{st.gate ?? st.id}</text>
              </g>
            );
          })}
        </g>
        {/* a runway being crossed flashes (the fixed traffic's crossings more faintly) */}
        <g className="sf-cross">
          {[...flashing].map(([rw, f]) => (
            <polyline key={rw} className={f.fixed ? "fixed" : undefined} points={line(g.runways[rw].xy)} strokeOpacity={blinkOn ? undefined : 0.35}
              strokeWidth={Math.max(3, (g.runways[rw].width_m ?? RUNWAY_M) * S) + 4}>
              <title>{`Runway ${g.runways[rw].ids.join("/")} crossed by ${f.who.map(icao).join(", ")}`}</title>
            </polyline>
          ))}
        </g>
        {twLabels.map((l) => { const [x, y] = P(l.at); return <text key={l.name} className="tw-label" x={x} y={y - 5} textAnchor="middle">{l.name}</text>; })}

        <g className="sf-routes">
          {routes.map((r) => <polyline key={r.cs} className={`path blip ${r.tone}${selected === r.cs ? " sel" : ""}`} points={line(r.xy)} strokeDasharray={r.dashed ? "5 4" : undefined} />)}
        </g>

        {/* the fixed 2015 traffic and departures (grey, no data block) */}
        <g className="gnd-fixed">
          {fixedNow.map((f) => {
            const [x, y] = P(f.p);
            const sc = Math.max(0.8, Math.min(1.6, 40 * S / 10));
            return (
              <path key={`${f.x.callsign}-${f.x.t0}`} className="gnd blip fixed" transform={`translate(${x} ${y}) rotate(${f.hdg}) scale(${sc})`} d={AIRFRAME} fill="currentColor">
                <title>{`${icao(f.x.callsign)} · 2015 ${f.x.kind}, runway ${f.x.runway}, stand ${f.x.stand}${f.wait ? ` · ${f.wait.reason}` : ""}`}</title>
              </path>
            );
          })}
        </g>

        {/* fixed traffic and departures first (grey, no data block), negotiated aircraft on top */}
        {[...acs].sort((a, b) => Number(a.tone !== "fixed") - Number(b.tone !== "fixed")).map((a) => {
          const [x, y] = P(a.p);
          const cs = a.x.callsign, fixed = a.tone === "fixed", sel = selected === cs;
          const wt = fixed ? null : waitsNow.find((w) => w.cs === cs && !w.fixed)?.w ?? null;
          const sc = Math.max(0.8, Math.min(1.6, 40 * S / 10));           // a ~40 m airframe, never smaller than a glyph
          const l1 = icao(cs), b = blocks.get(cs);
          const l2 = b?.l2 ?? "", flip = !(b?.east ?? true);
          const lx = b?.lx ?? x + LEAD, ly = b?.ly ?? y - LEAD, ey = b?.ey ?? ly;
          return (
            <g key={cs} className={`gnd blip ${a.tone}${sel ? " sel" : ""}`} onClick={(e) => { e.stopPropagation(); if (!fixed) onSelect(cs); }} style={{ cursor: fixed ? "default" : "pointer" }}
              onMouseEnter={wt ? () => setHover({ key: `w-${cs}-${wt.from_min}`, text: waitText(cs, wt), at: [x, y] }) : undefined} onMouseLeave={wt ? () => setHover(null) : undefined}>
              <title>{`${l1}${fixed ? ` · ${a.x.kind === "departure" ? "departure" : "other traffic"}` : ""} · ${a.x.kind === "arrival" ? `from ${a.x.exit ?? "the runway"} to ${a.x.stand}` : `from ${a.x.stand}`} via ${a.x.route_names.join(" ") || "—"} · ${a.x.taxi_min.toFixed(1)} min taxi (${a.x.unimpeded_min.toFixed(1)} unimpeded) · ${a.x.src === "osm" ? "OSM route" : "synthesized, straight line"}${wt ? ` · ${wt.reason}` : ""}`}</title>
              {sel && <circle cx={x} cy={y} r={10} fill="none" stroke="currentColor" strokeOpacity=".6" />}
              <path transform={`translate(${x} ${y}) rotate(${a.hdg}) scale(${sc})`} d={AIRFRAME}
                fill={fixed ? "currentColor" : a.tone === "focus" ? "currentColor" : "var(--scope)"} stroke="currentColor" strokeWidth={fixed ? 0 : 1.2 / sc} />
              {!fixed && <>
                <line x1={x} y1={y} x2={lx} y2={ey} stroke="currentColor" strokeOpacity=".8" />
                <text className="db1" x={lx + (flip ? -2 : 2)} y={ly - 2} textAnchor={flip ? "end" : "start"}>{l1}</text>
                <text className="db2" x={lx + (flip ? -2 : 2)} y={ly + 12} textAnchor={flip ? "end" : "start"}>{l2}</text>
              </>}
            </g>
          );
        })}

        {/* waits in progress: a bar across the taxiway where the aircraft holds, the reason on hover */}
        <g className="sf-wait">
          {waitsNow.map(({ w, cs, fixed }) => {
            // the bar is the line it holds at, just ahead of the nose (screen y is flipped)
            const ahead = Math.max(fixed ? 5 : 9, WAIT_AHEAD_M * S), f = w.dir ? [w.dir[0], -w.dir[1]] : [0, 0];
            const [x, y] = P(w.xy).map((v, i) => v + f[i] * ahead);
            const half = Math.max(fixed ? 3 : 6, (WAIT_BAR_M * S) / 2);
            const d = w.dir ? [w.dir[1], w.dir[0]] : [1, 0];      // across the track
            const text = waitText(cs, w);
            return (
              <g key={`${cs}-${w.from_min}`} className={fixed ? "fixed" : undefined} onMouseEnter={() => setHover({ key: `w-${cs}-${w.from_min}`, text, at: [x, y] })} onMouseLeave={() => setHover(null)}>
                <title>{text}</title>
                <line x1={x - d[0] * half} y1={y - d[1] * half} x2={x + d[0] * half} y2={y + d[1] * half} />
                <line className="hit" x1={x - d[0] * half} y1={y - d[1] * half} x2={x + d[0] * half} y2={y + d[1] * half} />
              </g>
            );
          })}
        </g>

        {/* the plan's ATC-TAXI-CONFLICT entries: informational ones grey (a fixed aircraft holds for a plan landing,
            never a violation), failing ones red, the ground counterpart of the timeline's rejection flash */}
        <g className="sf-conflict">
          {info.map((c) => <ConflictMark key={hoverKey(c)} c={c} P={P} info onHover={setHover} />)}
          {failing.map((c) => <ConflictMark key={hoverKey(c)} c={c} P={P} info={false} onHover={setHover} />)}
        </g>
        {/* a vetoed move's ground conflicts flash red and fade, like the timeline's rejection flash (keyed on the
            message, so each rejection replays it) */}
        {rejected && rejected.conflicts.length > 0 && (
          <g key={rejected.seq} className="sf-conflict sf-reject">
            {rejected.conflicts.map((c) => <ConflictMark key={hoverKey(c)} c={{ ...c, xy: g.pj(c.lat, c.lon) }} P={P} info={false} onHover={() => {}} />)}
            <text className="sf-reject-note" x={16} y={top - 8}>{`Vetoed: ${icaoText(rejected.label)} · taxi conflict`}</text>
          </g>
        )}
        {hover && shownKeys.has(hover.key) && (() => {
          // kept on the map: shortened to its width, then slid left or right to fit
          const max = Math.floor((W - 16) / MONO_PX), text = hover.text.length > max ? `${hover.text.slice(0, max - 1)}…` : hover.text;
          const w = text.length * MONO_PX, x0 = hover.at[0] > W / 2 ? hover.at[0] - 10 - w : hover.at[0] + 10;
          return <text className="sf-hover" x={Math.max(8, Math.min(W - 8 - w, x0))} y={hover.at[1] - 10}>{text}</text>;
        })()}

        {/* scrims keep the controls, key and notes legible over the map when zoomed in */}
        <defs>
          <linearGradient id="sf-top" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--scope)" stopOpacity=".92" /><stop offset="1" stopColor="var(--scope)" stopOpacity="0" /></linearGradient>
          <linearGradient id="sf-bot" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stopColor="var(--scope)" stopOpacity=".92" /><stop offset="1" stopColor="var(--scope)" stopOpacity="0" /></linearGradient>
        </defs>
        <rect width={W} height={top} fill="url(#sf-top)" pointerEvents="none" />
        <rect y={H - 72} width={W} height={72} fill="url(#sf-bot)" pointerEvents="none" />
        <text className="ring" x={W / 2} y={14} textAnchor="middle">N</text>
        <g className="sf-scale" transform={`translate(${W - 16 - bar * S} ${H - 46})`}>
          <path d={`M0 -4V0H${bar * S}V-4`} />
          <text x={bar * S / 2} y={-7} textAnchor="middle">{bar >= 1000 ? `${bar / 1000} km` : `${bar} m`}</text>
        </g>
        {keys.map((k, i) => (
          <g key={k.id} className={`legend-synth ${k.id}`} transform={`translate(${W - 16} ${64 + 16 * i})`}>
            {k.id === "info" ? <circle cx={-k.w - 13} cy={-4} r={5} />
              : k.id === "conflict" ? <circle cx={-k.w - 13} cy={-4} r={5} />
              : <line x1={-k.w - 26} y1={-4} x2={-k.w} y2={-4} strokeDasharray={k.id === "synth" ? "5 4" : undefined} />}
            <text x={0} y={0} textAnchor="end">{k.text}</text>
          </g>
        ))}
        {/* notes bottom right: the colour key sits bottom left across the whole panel */}
        <text className="scope-note" x={W - 16} y={H - 26} textAnchor="end">{traj ? `the engine's taxi routes · updates every 1 s${updating ? " · updating…" : ""}` : "no taxi routes for this plan"}</text>
        <text className="scope-note attr" x={W - 16} y={H - 12} textAnchor="end">{attribution} · not for navigation</text>
      </svg>
    </div>
  );
}

/** Heading, degrees true, of a track at segment `seg`: along the first segment of non-zero length at or before
 *  it (a stopped aircraft keeps the heading it stopped with), else the first one after. */
function headingAt(pj: Prepared["pj"], points: TrackPoint[], seg: number): number {
  const at = (j: number) => { const a = points[j], b = points[j + 1]; if (!a || !b) return null; const p = pj(a.lat, a.lon), q = pj(b.lat, b.lon); return Math.hypot(q[0] - p[0], q[1] - p[1]) > 0.5 ? bearing(p, q) : null; };
  const i = Math.min(seg, points.length - 2);
  for (let j = i; j >= 0; j--) { const h = at(j); if (h != null) return h; }
  for (let j = i + 1; j < points.length - 1; j++) { const h = at(j); if (h != null) return h; }
  return 0;
}

const waitText = (cs: string, w: Wait) => `${icao(cs)} · ${w.reason} · ${(w.to_min - w.from_min).toFixed(1)} min`;

const hoverKey = (c: Conflict) => `c-${c.kind ?? "fail"}-${c.callsign}-${c.fixed ?? ""}-${c.at_min}`;

/** One ATC-TAXI-CONFLICT entry: a failing one is a red ring with a cross, an informational one a quiet grey ring. */
function ConflictMark({ c, P, info, onHover }: { c: Conflict & { xy: XY }; P: (q: XY) => XY; info: boolean; onHover: (h: Hover) => void }) {
  const [x, y] = P(c.xy);
  const text = info
    ? `${c.fixed ? icao(c.fixed) : "fixed traffic"} ${c.kind === "fixed_departure_holds" ? "holds for takeoff" : "holds short"}${c.strip ? ` of ${c.strip}` : ""}${c.hold_min != null ? ` ${c.hold_min.toFixed(1)} min` : ""} for landing ${icao(c.callsign)} · informational`
    : `${c.rule} · ${c.detail}`;
  return (
    <g className={info ? "info" : "fail"} onMouseEnter={() => onHover({ key: hoverKey(c), text, at: [x, y] })} onMouseLeave={() => onHover(null)}>
      <title>{info ? `${text} · ${c.detail}` : text}</title>
      <circle cx={x} cy={y} r={info ? 6 : 9} />
      {!info && <path d={`M${x - 4} ${y - 4}L${x + 4} ${y + 4}M${x + 4} ${y - 4}L${x - 4} ${y + 4}`} />}
    </g>
  );
}
