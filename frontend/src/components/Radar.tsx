import { useEffect, useRef, useState } from "react";
import type { Assignment, JointPlan, Runway, TrajectorySet, WaveGeo, WaveScenario } from "../api/types";
import { icao } from "../callsign";
import { clock } from "../format";
import { entryFix, holdLap, levelLabel, matchHold, nmProjector, positionAt, scanFloor, scanTimes, segmentDashed, trend } from "../geo/track";

// An approach-radar picture of the plan at one moment. The scope is centred on the airport reference point,
// runways are drawn to scale from their end coordinates and range rings are true nautical miles.
// With the wave's procedures (/geo) and the engine's trajectories for the plan on screen, each arrival is
// sampled from its own 4D track once per radar scan: STAR legs, holding laps at its stack level, vectors and
// final, exactly the path the engine costed. Synthesized legs (vectors, generic holds, corner posts) are
// dashed. Without them the scope falls back to the v2 schematic: each arrival sits on the extended
// centreline of its runway end at a typical final-approach groundspeed, and says so.
// Positions are replayed from the plan, not from surveillance data.
const GS_NM_PER_MIN = 3;          // ~180 kt over the last 30 NM (schematic only)
const RANGES = [10, 20, 40, 80];       // NM from centre to the short edge, as on a scope range knob
const LEAD = 22;                   // leader line length, px
export const SCAN_MIN = 4.8 / 60;  // one radar update, minutes: a terminal antenna turns at 12.5 rpm
const TRAIL = 5;                   // history dots: where the target was on the last five scans
const AHEAD_MIN = 1;               // predicted track line: where it will be in a minute
const TREND_MIN = 0.25;            // the climb/descent arrow compares the level now with 15 s later
const MAX_FIX_LABELS = 8;          // sparse fix labels: the rest are symbols with their name on hover

export type Tone = "focus" | "flight" | "edited" | "alert" | "fixed";
type End = Runway & { le_lat: number; le_lon: number; he_lat: number; he_lon: number };
const hasGeo = (r: Runway): r is End => [r.le_lat, r.le_lon, r.he_lat, r.he_lon].every((v) => typeof v === "number");
type Air = TrajectorySet["air"][number];
type Pt = Air["points"][number];
type XY = [number, number];

interface Props {
  wave: WaveScenario;
  plan: JointPlan | null;
  t: number;                                   // minutes vs T0
  tone: (cs: string) => Tone;
  selected: string | null;
  onSelect: (cs: string | null) => void;
  geo?: WaveGeo | null;                        // procedures for the wave; null = no geometry
  paths?: TrajectorySet | null;                // the engine's trajectories for `plan`; null = none
  pathsNote?: string | null;                   // set when a recorded run has no paths: no targets are drawn
  updating?: boolean;                          // trajectories for an edited plan are being recomputed
  fill?: boolean;                              // fill the parent's height also when narrow (half of a side-by-side panel)
}

interface Blip { cs: string; x: number; y: number; tone: Tone; l1: string; l2: string; out: number | null; title: string; trail: [number, number][]; ahead: [number, number] }

export function Radar({ wave, plan, t, tone, selected, onSelect, geo = null, paths = null, pathsNote = null, updating = false, fill = false }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [[W, boxH], setSize] = useState([960, 0]);
  const [userRange, setRange] = useState<number | null>(null);   // null = Auto: 20 NM, wider while a hold is on the scope
  const [hoverFix, setHoverFix] = useState<string | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize([Math.round(e.contentRect.width), Math.round(e.contentRect.height)]));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const wide = W >= 700;
  const stack = W < 900;                                 // below this the left and right notes would collide on one line: the procedures note goes above
  const H = wide || fill ? Math.max(460, boxH) : Math.round(Math.min(560, Math.max(360, W * 1.05)));

  const ends = wave.airport.runways.filter(hasGeo);
  if (!ends.length) return <div ref={box} className="radar empty-radar"><p className="empty">No runway coordinates for {wave.airport.iata}, so the scope can't be drawn.</p></div>;

  // local flat projection in nautical miles around the airport reference point
  const lat0 = wave.airport.lat ?? ends[0].le_lat, lon0 = wave.airport.lon ?? ends[0].le_lon;
  const toNm = nmProjector(lat0, lon0);                  // the same plane as the surface map, in NM
  const nm = (lat: number, lon: number): [number, number] => { const [e, n] = toNm(lat, lon); return [e, -n]; };   // screen y points south
  const cx = W / 2, cy = H / 2;
  // visible extent in NM from the centre; the top band is kept clear for the clock and range controls
  const keyed = window.innerWidth > 760;                 // styles.css shows the colour key and overlays the header above 760px
  const TOP = keyed ? 64 : 12, BOTTOM = keyed ? (stack ? 76 : 60) : 30;   // stacked notes: the colour key sits above two note lines
  const scaleOf = (r: number) => (Math.min(W, H) / 2 - 18) / r;   // px per NM: the range fits the short side
  const limOf = (r: number) => { const k = scaleOf(r); return { l: -(cx - 16) / k, r: (W - cx - 16) / k, t: -(cy - TOP) / k, b: (H - cy - BOTTOM) / k }; };
  // Auto range: the smallest scope that holds every hold pattern flown at this moment (with a margin), else 20 NM
  const holdNow: XY[] = [];
  if (geo && paths && !pathsNote) {
    const tsA = scanFloor(t, SCAN_MIN);
    for (const a of paths.air) {
      const hs = a.points.filter((p) => p.kind === "hold");
      if (hs.length && tsA >= hs[0].t_min - 1 && tsA <= hs[hs.length - 1].t_min + 0.5)
        for (const p of hs) holdNow.push(nm(p.lat, p.lon));
    }
  }
  const autoRange = holdNow.length
    ? RANGES.find((r) => { const q = limOf(r); return holdNow.every(([x, y]) => x * 1.12 >= q.l && x * 1.12 <= q.r && y * 1.12 >= q.t && y * 1.12 <= q.b); }) ?? RANGES[RANGES.length - 1]
    : 20;
  const range = userRange ?? autoRange;
  const S = scaleOf(range);
  const step = range / 5;
  const rings = Array.from({ length: Math.ceil(Math.hypot(W, H) / 2 / S / step) }, (_, i) => (i + 1) * step);
  const P = (p: [number, number]): [number, number] => [cx + p[0] * S, cy + p[1] * S];
  const lim = limOf(range);

  // one strip per physical runway (the two ends share coordinates, swapped)
  const key = (e: End) => [e.le_lat, e.le_lon, e.he_lat, e.he_lon].map((v) => v.toFixed(4));
  const strips: End[][] = [];
  for (const e of ends) {
    const [a, b, c, d] = key(e);
    const twin = strips.find((g) => { const [p, q, r, s] = key(g[0]); return (p === a && q === b && r === c && s === d) || (p === c && q === d && r === a && s === b); });
    if (twin) twin.push(e); else strips.push([e]);
  }
  const land = (plan?.assignments ?? []).filter((a) => a.action === "land" && a.runway);
  const usedEnds = new Set([...land.map((a) => a.runway!), ...wave.queue.map((q) => q.runway)]);
  const endBy = new Map(ends.map((e) => [e.id, e]));

  // where an arrival is: d NM out on the extended centreline of its runway end
  const approach = (rw: string, d: number) => {
    const e = endBy.get(rw);
    if (!e) return null;
    const th = nm(e.le_lat, e.le_lon), far = nm(e.he_lat, e.he_lon);
    const len = Math.hypot(th[0] - far[0], th[1] - far[1]) || 1;
    const u: [number, number] = [(th[0] - far[0]) / len, (th[1] - far[1]) / len];   // outward, away from the runway
    // how far along the centreline before it leaves the scope; further arrivals wait at the edge
    const reach = Math.min(
      Math.abs(u[0]) > 1e-6 ? ((u[0] > 0 ? lim.r : lim.l) - th[0]) / u[0] : Infinity,
      Math.abs(u[1]) > 1e-6 ? ((u[1] > 0 ? lim.b : lim.t) - th[1]) / u[1] : Infinity);
    const out = d > reach;
    const dd = Math.max(0, Math.min(d, reach));
    const p: [number, number] = [th[0] + u[0] * dd, th[1] + u[1] * dd];
    return { p, u, out };
  };

  // Three pictures: the engine's paths (fly), the v2 schematic with a caption, or a recorded run whose
  // paths do not exist (runways only, no targets: drawing the schematic there would pass it off as the run).
  const fly = !!geo && !!paths && !pathsNote;
  const noTargets = !!pathsNote;
  const inScope = ([x, y]: XY) => x >= lim.l && x <= lim.r && y >= lim.t && y <= lim.b;

  const blips: Blip[] = [];
  const awaiting = plan || noTargets ? [] : [...wave.flights].sort((p, q) => Number(q.callsign === wave.focus) - Number(p.callsign === wave.focus) || p.sched_min - q.sched_min);
  const beyond: { cs: string; rw: string; rem: number; tone: Tone }[] = [];   // inbound but past the scope edge
  const titleOf = new Map<string, string>();
  for (const q of wave.queue) titleOf.set(q.callsign, `${icao(q.callsign)} · fixed traffic · ${q.aircraft_type ?? ""} ${q.wake} · runway ${q.runway} ${clock(wave.window_start, q.slot_min)}`);
  for (const a of land) titleOf.set(a.callsign, `${icao(a.callsign)} (${a.callsign}) · runway ${a.runway} at ${clock(wave.window_start, a.slot_min ?? 0)} · gate ${a.gate}${a.metrics.hold_min ? ` · holds ${a.metrics.hold_min} min` : ""}`);

  // ---------- the engine's paths ----------
  const ts = scanFloor(t, SCAN_MIN);                    // the picture is the last scan, also when scrubbing
  const air: Air[] = fly ? paths!.air.filter((a) => a.points.length > 0) : [];
  if (fly) for (const a of air) usedEnds.add(a.runway);
  // Holds someone in the shown plan flies, with the levels they use. Geo holds may share a fix name, so each
  // flight is matched to the pattern of that name nearest to where its own hold points lie (name AND position).
  const holds = fly ? geo!.procedures.holds : [];
  const holdOf = new Map<string, number>();                      // callsign -> index into geo holds (when one matches)
  const holdsUsed = new Map<number, { levels: Set<number>; n: number }>();   // by geo hold: the racetrack's levels
  const usedHoldFixes = new Set<string>();                       // every fix a flight holds at, matched or not
  let nHolding = 0;
  for (const a of air) if (a.hold && a.hold.laps >= 1) {
    nHolding++; usedHoldFixes.add(a.hold.fix);
    const fx = geo!.procedures.fixes.find((f) => f.id === a.hold!.fix);
    const i = matchHold(holds, a.hold, a.points, fx);
    if (i == null) continue;                                     // counted and labelled, but no racetrack to draw
    holdOf.set(a.callsign, i);
    const h = holdsUsed.get(i) ?? { levels: new Set<number>(), n: 0 };
    h.levels.add(a.hold.level_ft); h.n++; holdsUsed.set(i, h);
  }
  const racetracks = holds.map((h, i) => ({ h, i })).filter(({ h, i }) => holdsUsed.has(i) && h.polygon.length > 2);
  const drawnHold = new Set(racetracks.map(({ i }) => i));
  const drawnHoldFixes = new Set(racetracks.map(({ h }) => h.fix));
  // what is still ahead of each target, split into runs of solid (published) and dashed (synthesized) legs;
  // laps of a hold that has its racetrack drawn are left to the racetrack
  type Run = { cs: string; tone: Tone; dashed: boolean; pts: XY[] };
  const runs: Run[] = [];
  let anySynth = racetracks.some(({ h }) => h.src === "synth");
  const aheadFixes = new Set<string>();                 // fixes still to be flown by a target on the scope
  if (fly) for (const a of air) {
    const tn = a.wave ? tone(a.callsign) : "fixed";
    const now = ts < a.appears_min ? null : positionAt(a.points, ts);
    if (!now) continue;
    let cur: Run | null = null;
    let prev: { xy: XY; p: Pt } = { xy: nm(now.lat, now.lon), p: a.points[now.seg] };
    for (let i = now.seg + 1; i < a.points.length; i++) {
      const p = a.points[i], xy = nm(p.lat, p.lon);
      if (p.fix) aheadFixes.add(p.fix);
      const skip = prev.p.kind === "hold" && p.kind === "hold" && drawnHold.has(holdOf.get(a.callsign) ?? -1);
      const dashed = segmentDashed(prev.p, p);
      if (skip) cur = null;
      else {
        if (dashed) anySynth = true;
        if (!cur || cur.dashed !== dashed) { cur = { cs: a.callsign, tone: tn, dashed, pts: [prev.xy] }; runs.push(cur); }
        cur.pts.push(xy);
      }
      prev = { xy, p };
    }
  }

  // sparse fix labels: hold fixes, then the FAFs of runways in use, then entry fixes; at most eight on screen.
  // A hold fix with its racetrack drawn is named by the racetrack's label, which counts toward the eight.
  const fixBy = new Map((geo?.procedures.fixes ?? []).map((f) => [f.id, f]));
  const onPath = new Set<string>();
  const entries: string[] = [];
  for (const a of air) {
    const named = a.points.filter((p) => p.fix && fixBy.has(p.fix)).map((p) => p.fix!);
    named.forEach((f) => onPath.add(f));
    const entry = entryFix(a, fixBy)?.fix;
    if (entry) entries.push(entry);
  }
  const fafs = [...onPath].filter((f) => fixBy.get(f)!.kind === "faf");
  const fixLabels: { id: string; x: number; y: number; hold: boolean }[] = [];
  for (const id of [...new Set([...usedHoldFixes, ...fafs, ...entries])]) {
    const f = fixBy.get(id);
    if (!f || fixLabels.length >= MAX_FIX_LABELS) continue;
    const p = nm(f.lat, f.lon);
    if (!inScope(p)) continue;
    const [x, y] = P(p);
    if (fixLabels.some((l) => Math.abs(l.x - x) < 40 && Math.abs(l.y - y) < 12)) continue;
    fixLabels.push({ id, x, y, hold: drawnHoldFixes.has(id) });
  }
  // fix symbols: what the targets on the scope still fly, plus the labelled ones; the rest of the procedure stays off
  const fixSyms = [...onPath].filter((id) => aheadFixes.has(id) || fixLabels.some((l) => l.id === id))
    .map((id) => fixBy.get(id)!).filter((f) => inScope(nm(f.lat, f.lon)));

  if (fly) for (const a of air) {
    const tn: Tone = a.wave ? tone(a.callsign) : "fixed";
    if (ts < a.appears_min) continue;                     // not yet on frequency: listed in the strips
    const now = positionAt(a.points, ts);
    if (!now) continue;                                   // landed
    const p = nm(now.lat, now.lon);
    const last = a.points[a.points.length - 1];
    if (!inScope(p)) { beyond.push({ cs: a.callsign, rw: a.runway, rem: Math.round(last.t_min - ts), tone: tn }); continue; }
    const [x, y] = P(p);
    const trail = scanTimes(ts, TRAIL + 1, SCAN_MIN).slice(1)
      .map((s) => positionAt(a.points, s)).filter((h): h is NonNullable<typeof h> => !!h)
      .map((h) => nm(h.lat, h.lon)).filter(inScope).map(P);
    const nx = positionAt(a.points, ts + AHEAD_MIN) ?? last;
    const lap = holdLap(a, ts);
    const alt = `${levelLabel(now.alt_ft)}${lap ? "" : trend(now.alt_ft, positionAt(a.points, ts + TREND_MIN)?.alt_ft ?? last.alt_ft)}`;
    const l2 = lap ? `${alt} HLD ${lap.lap}/${lap.of}` : `${alt} ${a.runway}`;
    blips.push({ cs: a.callsign, x, y, tone: tn, l1: icao(a.callsign), l2, out: null,
      title: `${titleOf.get(a.callsign) ?? icao(a.callsign)} · ${Math.round(now.alt_ft ?? 0).toLocaleString("en-US")} ft${lap ? ` · holding at ${a.hold!.fix}, lap ${lap.lap} of ${lap.of}` : ""}`,
      trail, ahead: P(nm(nx.lat, nx.lon)) });
  }

  // ---------- the v2 schematic ----------
  const add = (cs: string, rw: string, slot: number, tn: Tone, l2: string, title: string) => {
    if (fly || noTargets) return;
    const rem = slot - t;
    if (rem < 0 || rem > 60) return;                    // landed, or not yet inbound
    const a = approach(rw, rem * GS_NM_PER_MIN);
    if (!a) return;
    const [x, y] = P(a.p);
    if (a.out) { beyond.push({ cs, rw, rem: Math.round(rem), tone: tn }); return; }
    // the trail is the target's own past returns, one per scan; the line ahead is a minute of its track
    const trail: [number, number][] = [];
    for (let k = 1; k <= TRAIL; k++) { const h = approach(rw, (rem + k * SCAN_MIN) * GS_NM_PER_MIN); if (h && !h.out) trail.push(P(h.p)); }
    const ahead = P(approach(rw, Math.max(0, rem - AHEAD_MIN) * GS_NM_PER_MIN)!.p);
    blips.push({ cs, x, y, tone: tn, l1: icao(cs), l2, out: null, title, trail, ahead });
  };
  for (const q of wave.queue) add(q.callsign, q.runway, q.slot_min, "fixed", `${q.runway} ${clock(wave.window_start, q.slot_min)}`, titleOf.get(q.callsign)!);
  for (const a of land) {
    const tn = tone(a.callsign);
    add(a.callsign, a.runway!, a.slot_min ?? 0, tn, `${a.runway} ${a.gate === "RS" ? "RS" : a.gate} ${clock(wave.window_start, a.slot_min ?? 0)}`, titleOf.get(a.callsign)!);
  }
  // draw the negotiated flights last, so they sit on top of fixed traffic
  blips.sort((a, b) => (a.tone === "fixed" ? 0 : 1) - (b.tone === "fixed" ? 0 : 1));

  // racetracks in screen space, with their "{fix} ×{levels} · {lo}-{hi}k" label above the pattern
  const holdDraw0 = racetracks.map(({ h, i }) => {
    const pts = h.polygon.map(([la, lo]) => P(nm(la, lo)));
    const u = holdsUsed.get(i)!;
    const lv = [...u.levels].sort((a, b) => a - b);
    const lo = Math.round(lv[0] / 1000), hi = Math.round(lv[lv.length - 1] / 1000);
    const top = pts.reduce((m, p) => (p[1] < m[1] ? p : m), pts[0]);
    const f = fixBy.get(h.fix);
    return { h, i, pts, lv, n: u.n, x: (f ? P(nm(f.lat, f.lon)) : top)[0], y: top[1] - 6, text: `${h.fix} ×${lv.length} · ${lo === hi ? lo : `${lo}-${hi}`}k` };
  });

  // the wind compass sits bottom right unless a target, a fix or a racetrack would be under it; then the next free spot
  type Rect = [number, number, number, number];
  const hit = (a: Rect, b: Rect) => Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]));
  const solids: XY[] = [...blips.map((b): XY => [b.x, b.y]), ...fixSyms.map((f): XY => P(nm(f.lat, f.lon))), ...holdDraw0.flatMap((d) => d.pts)];
  const windSpots: XY[] = [[W - 74, H - (stack ? 118 : 76)], [W - 74, H - (stack ? 118 : 76) - 120], [W - 74, cy], [74, cy + 70]];
  const windBox = (c: XY): Rect => [c[0] - 50, c[1] - 26, 100, 76];
  const wind = windSpots.find((c) => { const r = windBox(c); return !solids.some(([x, y]) => x > r[0] - 12 && x < r[0] + r[2] + 12 && y > r[1] - 12 && y < r[1] + r[3] + 12); }) ?? windSpots[0];
  const fixedOverlays: Rect[] = [windBox(wind), [cx - 10, 2, 20, 16]];   // the wind compass and the N mark
  // hold labels: above the pattern, else below it, whichever clears the overlays
  const holdDraw = holdDraw0.map((d) => {
    const w = d.text.length * 6.8, top = d.y;
    const at = (y: number): Rect => [d.x - w / 2, y - 11, w, 14];
    const bottom = Math.max(...d.pts.map((p) => p[1])) + 16;
    const y = [top, bottom].find((yy) => !fixedOverlays.some((o) => hit(at(yy), o) > 0)) ?? top;
    return { ...d, y };
  });

  // data blocks: try four corners, keep the one that collides least with blocks and labels already placed
  const placed: [number, number, number, number][] = [
    ...fixedOverlays,
    ...holdDraw.map((d): [number, number, number, number] => [d.x - d.text.length * 3.4, d.y - 11, d.text.length * 6.8, 14]),
    ...fixLabels.filter((l) => !l.hold).map((l): [number, number, number, number] => [l.x + 6, l.y - 15, l.id.length * 6.8, 13]),
  ];
  const blocks = blips.map((b) => {
    const w = Math.max(b.l1.length, b.l2.length) * 7.4 + 6, h = b.tone === "fixed" ? 16 : 30;
    const opts: [number, number][] = [[1, -1], [-1, -1], [1, 1], [-1, 1]];
    let best = opts[0], score = Infinity;
    for (const [sx, sy] of opts) {
      const lx = b.x + sx * LEAD * 0.7, ly = b.y + sy * LEAD * 0.7;
      const r: [number, number, number, number] = [sx > 0 ? lx + 2 : lx - w - 2, sy < 0 ? ly - h : ly, w, h];
      const off = r[0] < 2 || r[0] + r[2] > W - 2 || r[1] < TOP - 8 || r[1] + r[3] > H - BOTTOM + 6 ? 1000 : 0;
      const over = placed.reduce((s, p) => s + hit(r, p), 0);
      if (over + off < score) { score = over + off; best = [sx, sy]; }
    }
    const lx = b.x + best[0] * LEAD * 0.7, ly = b.y + best[1] * LEAD * 0.7;
    const r: [number, number, number, number] = [best[0] > 0 ? lx + 2 : lx - w - 2, best[1] < 0 ? ly - h : ly, w, h];
    placed.push(r);
    return { b, lx, ly, r };
  });

  // what the picture is, for the label: the caption state and a short hold summary
  const state = noTargets ? "paths not available" : !fly ? "schematic, not the engine's path" : updating ? "updating paths" : "";
  const holdSummary = !fly ? "" : nHolding ? `${nHolding} holding at ${[...usedHoldFixes].join(", ")}` : "no holds";

  const m = wave.metar, calm = !m || !m.wind_kt, dir = m?.wind_dir_deg;
  const wr = dir != null ? ((dir - 90) * Math.PI) / 180 : 0;
  // ring numbers go on the diagonal furthest from every approach in use, so they never sit under traffic
  const approachDirs = [...usedEnds].map((id) => approach(id, 1)?.u).filter(Boolean) as [number, number][];
  const diag = ([[1, -1], [-1, -1], [1, 1], [-1, 1]] as [number, number][]).map(([dx, dy]) => [dx * 0.707, dy * 0.707] as [number, number])
    .sort((p, q) => Math.max(...approachDirs.map((u) => p[0] * u[0] + p[1] * u[1]), -1) - Math.max(...approachDirs.map((u) => q[0] * u[0] + q[1] * u[1]), -1))[0];
  const ringLabel = (r: number) => { const x = cx + r * S * diag[0], y = cy + r * S * diag[1];
    const box: Rect = [diag[0] < 0 ? x - 26 : x, diag[1] < 0 ? y - 14 : y, 26, 14];   // the number, with its offset
    return x > 24 && x < W - 24 && y > TOP && y < H - BOTTOM && !placed.some((p) => hit(box, p) > 0); };

  return (
    <div ref={box} className={`radar${wide || fill ? " fill" : ""}`}>
      <div className="range seg" role="group" aria-label="Scope range">
        {RANGES.map((r) => <button key={r} aria-pressed={range === r} onClick={() => setRange(r)}>{r} NM</button>)}
      </div>
      {(beyond.length > 0 || awaiting.length > 0) && (
        <ul className="beyond" aria-label={`Inbound flights not drawn on the ${range} NM scope`}>
          {/* before a plan exists the negotiated flights have no runway, so they are listed, not drawn */}
          {awaiting.map((f) => (
            <li key={f.callsign} className={`blip ${tone(f.callsign)}`}><button onClick={() => onSelect(f.callsign)}>{icao(f.callsign)}</button> STA {clock(wave.window_start, f.sched_min)} (gate) · no runway yet</li>
          ))}
          {beyond.sort((p, q) => p.rem - q.rem).slice(0, 5).map((b) => (
            <li key={b.cs} className={`blip ${b.tone}`}><button onClick={() => b.tone !== "fixed" && onSelect(b.cs)}>{icao(b.cs)}</button> {b.rw} in {b.rem}′</li>
          ))}
          {beyond.length > 5 && <li className="more">+{beyond.length - 5} more</li>}
        </ul>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" onClick={() => onSelect(null)}
        aria-label={`${wave.airport.iata} approach picture at ${clock(wave.window_start, t)}: ${blips.length} arrivals inbound${state ? `, ${state}` : ""}${holdSummary ? `, ${holdSummary}` : ""}`}>
        <defs>
          <radialGradient id="scope-bg" cx="50%" cy="50%" r="60%">
            <stop offset="0" stopColor="#10161b" /><stop offset="1" stopColor="#0b0c0e" />
          </radialGradient>
          <pattern id="rw-closed" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="5" stroke="var(--negative)" strokeWidth="2" strokeOpacity=".7" />
          </pattern>
          <marker id="wind-tip" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--soft)" /></marker>
        </defs>
        <rect width={W} height={H} fill="url(#scope-bg)" />
        {rings.map((r) => (
          <g key={r}>
            <circle cx={cx} cy={cy} r={r * S} fill="none" stroke="#1b232a" />
            {ringLabel(r) && <text className="ring" x={cx + r * S * diag[0] + 3 * Math.sign(diag[0])} y={cy + r * S * diag[1] + (diag[1] < 0 ? -3 : 12)} textAnchor={diag[0] < 0 ? "end" : "start"}>{r}</text>}
          </g>
        ))}
        <text className="ring" x={cx} y={14} textAnchor="middle">N</text>

        {fly && (
          <g className="paths">
            {/* holding racetracks in use, at the published (or synthesized) pattern, with the levels stacked there */}
            {holdDraw.map(({ h, i, pts, lv, n, x, y, text }) => (
              <g key={i} className={`racetrack${h.src === "synth" ? " synth" : ""}`}>
                <title>{`${h.fix} hold · ${h.turn === "R" ? "right" : "left"} turns · inbound ${String(Math.round(h.inbound_true)).padStart(3, "0")}°T · ${h.leg_min} min legs · ${n} holding at ${lv.map((l) => levelLabel(l)).join(", ")} · ${h.src === "synth" ? "synthesized, not published" : "published (CIFP)"}`}</title>
                <polygon points={pts.map((p) => p.join(",")).join(" ")} strokeDasharray={h.src === "synth" ? "5 4" : undefined} />
                <text className="hold-label" x={x} y={y} textAnchor="middle">{text}</text>
              </g>
            ))}
            {/* the path still ahead of each target; the selected flight's is brighter */}
            {runs.map((r, i) => (
              <polyline key={i} className={`path blip ${r.tone}${selected === r.cs ? " sel" : ""}`} points={r.pts.map((p) => P(p).join(",")).join(" ")}
                strokeDasharray={r.dashed ? "5 4" : undefined} />
            ))}
            {fixSyms.map((f) => {
              const [x, y] = P(nm(f.lat, f.lon));
              const labelled = fixLabels.some((l) => l.id === f.id && !l.hold);
              return (
                <g key={f.id} className="fix" onMouseEnter={() => setHoverFix(f.id)} onMouseLeave={() => setHoverFix((h) => (h === f.id ? null : h))}>
                  <title>{`${f.id} · ${f.kind.toUpperCase()} · ${f.src === "synth" ? "synthesized" : "CIFP"}`}</title>
                  <path d={`M${x} ${y - 3.5}l3.5 6h-7z`} strokeDasharray={f.src === "synth" ? "2 2" : undefined} />
                  <circle cx={x} cy={y} r={7} fill="transparent" stroke="none" />
                  {(labelled || hoverFix === f.id) && <text className="fix-label" x={x + 6} y={y - 5}>{f.id}</text>}
                </g>
              );
            })}
          </g>
        )}

        {/* extended centrelines of the runway ends in use, out to the scope edge (schematic only: in the
            engine's picture the final is part of each path) */}
        {!fly && [...usedEnds].map((id) => {
          const a = approach(id, 200);
          const e = endBy.get(id);
          if (!a || !e) return null;
          const [x1, y1] = P(nm(e.le_lat, e.le_lon)), [x2, y2] = P(a.p);
          return <line key={id} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#24323d" strokeDasharray="3 6" />;
        })}

        {strips.map((g) => {
          const e = g[0], open = g.some((x) => x.open), used = g.some((x) => usedEnds.has(x.id));
          const [x1, y1] = P(nm(e.le_lat, e.le_lon)), [x2, y2] = P(nm(e.he_lat, e.he_lon));
          const w = Math.max(3, Math.min(6, (e.length_ft ?? 9000) / 2200));
          return (
            <line key={g.map((x) => x.id).join("/")} x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={open ? w : w + 2}
              stroke={!open ? "url(#rw-closed)" : used ? "#d8dadf" : "#4a4c52"}>
              <title>{g.map((x) => `${x.id}: ${x.open ? "open" : `closed, ${x.closed_reason ?? ""}`}`).join("\n")}</title>
            </line>
          );
        })}
        {(() => {
          // end labels for runways in use only (closed ones are dim bars here and named in the header); a label that would touch one already placed is dropped
          const taken: [number, number][] = [];
          return ends.filter((e) => usedEnds.has(e.id)).sort((a, b) => Number(usedEnds.has(b.id)) - Number(usedEnds.has(a.id))).map((e) => {
            const th = P(nm(e.le_lat, e.le_lon)), far = P(nm(e.he_lat, e.he_lon));
            const len = Math.hypot(th[0] - far[0], th[1] - far[1]) || 1;
            const ux = (th[0] - far[0]) / len, uy = (th[1] - far[1]) / len;
            const lx = th[0] + ux * 13, ly = th[1] + uy * 13 + 4;
            if (taken.some(([tx, ty]) => Math.abs(tx - lx) < 26 && Math.abs(ty - ly) < 13)) return null;
            taken.push([lx, ly]);
            return <text key={e.id} className={`rw-end${e.open ? "" : " closed"}`} x={lx} y={ly} textAnchor="middle">{e.id}</text>;
          });
        })()}

        {blocks.map(({ b, lx, ly, r }) => {
          const sel = selected === b.cs;
          const cls = `blip ${b.tone}${sel ? " sel" : ""}`;
          return (
            <g key={b.cs} className={cls} onClick={(ev) => { ev.stopPropagation(); if (b.tone !== "fixed") onSelect(b.cs); }}
              style={{ cursor: b.tone === "fixed" ? "default" : "pointer" }}>
              <title>{b.title}{b.out != null ? ` · ${b.out} min out` : ""}</title>
              {b.tone === "alert" && <circle className="halo" cx={b.x} cy={b.y} r={13} />}
              {sel && <circle cx={b.x} cy={b.y} r={10} fill="none" stroke="currentColor" strokeOpacity=".6" />}
              {/* history: the last five returns, fading with age; predicted track: one minute ahead */}
              {b.trail.map(([hx, hy], i) => Math.hypot(hx - b.x, hy - b.y) < 7 ? null : <circle key={i} cx={hx} cy={hy} r={1.6} fill="currentColor" opacity={0.6 - i * 0.1} />)}
              <line x1={b.x} y1={b.y} x2={b.ahead[0]} y2={b.ahead[1]} stroke="currentColor" strokeOpacity=".45" />
              {b.tone === "fixed"
                ? <circle cx={b.x} cy={b.y} r={3} fill="currentColor" />
                : <path d={`M${b.x} ${b.y - 5}l5 5l-5 5l-5 -5z`} fill={b.tone === "focus" ? "currentColor" : "var(--scope)"} stroke="currentColor" strokeWidth={1.6} />}
              <line x1={b.x} y1={b.y} x2={lx} y2={ly} stroke="currentColor" strokeOpacity=".8" />
              <text className="db1" x={r[0] + (lx > b.x ? 0 : r[2])} y={r[1] + 12} textAnchor={lx > b.x ? "start" : "end"}>{b.l1}{b.out != null ? <tspan className="db-out"> +{b.out}′</tspan> : null}</text>
              {b.tone !== "fixed" && <text className="db2" x={r[0] + (lx > b.x ? 0 : r[2])} y={r[1] + 26} textAnchor={lx > b.x ? "start" : "end"}>{b.l2}</text>}
            </g>
          );
        })}

        <g transform={`translate(${wind[0]} ${wind[1]})`}>{/* bottom right (lifted when narrow) unless something is under it */}
          <circle r={22} fill="none" stroke="#2a2d33" />
          {calm ? <text className="ring" textAnchor="middle" y={4}>calm</text>
            : dir == null ? <text className="ring" textAnchor="middle" y={4}>VRB</text>
            : <line x1={Math.cos(wr) * 18} y1={Math.sin(wr) * 18} x2={-Math.cos(wr) * 16} y2={-Math.sin(wr) * 16} stroke="var(--soft)" strokeWidth={1.8} markerEnd="url(#wind-tip)" />}
          <text className="wind" textAnchor="middle" y={42}>{calm ? "wind calm" : `${dir != null ? String(Math.round(dir)).padStart(3, "0") : "VRB"}° ${m!.wind_kt}${m!.gust_kt ? `G${m!.gust_kt}` : ""} kt`}</text>
        </g>
        {fly ? (
          <>
            <text className="scope-note" x={14} y={H - 12}>{wave.airport.icao} · rings {step} NM{wide ? ` · the engine's paths for this plan · updates every ${SCAN_MIN * 60} s` : ` · ${SCAN_MIN * 60} s scan`}{updating ? " · updating paths…" : ""}</text>
            {anySynth && (
              <g className="legend-synth" transform={`translate(${W - 16} ${TOP + 4})`}>
                <line x1={-252} y1={-4} x2={-226} y2={-4} strokeDasharray="5 4" />
                <text x={0} y={0} textAnchor="end">dashed = synthesized, not published</text>
              </g>
            )}
            <text className="scope-note" x={stack ? 14 : W - 16} y={stack ? H - 26 : H - 12} textAnchor={stack ? "start" : "end"}>
              {geo!.procedures.status === "real" ? `Procedures: FAA CIFP ${geo!.procedures.cycle}, not for navigation` : "Procedures: synthesized (no published procedures), not for navigation"}
            </text>
          </>
        ) : (
          <>
            <text className="scope-note" x={14} y={H - 12}>{wave.airport.icao} · rings {step} NM{noTargets ? "" : wide ? ` · replayed from the plan at ${GS_NM_PER_MIN * 60} kt · updates every ${SCAN_MIN * 60} s` : ` · ${GS_NM_PER_MIN * 60} kt · ${SCAN_MIN * 60} s scan`}</text>
            <text className="scope-caption" x={W - 16} y={TOP + 4} textAnchor="end">{pathsNote ?? "schematic, not the engine's path"}</text>
          </>
        )}
      </svg>
    </div>
  );
}

export const assignmentAt = (p: JointPlan | null, cs: string): Assignment | undefined => p?.assignments.find((a) => a.callsign === cs);
