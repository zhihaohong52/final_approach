// Sampling the engine's 4D trajectories for display. The engine emits points dense enough (arcs every 10°,
// racetracks every 15°) that linear interpolation between consecutive points is exact to < 0.1 NM, so the
// client only interpolates; it never invents geometry.

export interface TrackPoint { t_min: number; lat: number; lon: number; alt_ft?: number }
export interface Fix4D { lat: number; lon: number; alt_ft?: number; seg: number }
export interface Hold { fix: string; laps: number; level_ft: number; entry_min: number; exit_min: number; lap_min: number }

/** Where the aircraft is at t (minutes vs T0): null before the first point and after the last. `seg` is the
 *  index of the point that starts the segment it is on. */
export function positionAt(points: TrackPoint[], t: number): Fix4D | null {
  const n = points.length;
  if (!n || t < points[0].t_min || t > points[n - 1].t_min) return null;
  // binary search for the last point with t_min <= t
  let lo = 0, hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (points[mid].t_min <= t) lo = mid; else hi = mid - 1;
  }
  const a = points[lo];
  if (lo === n - 1) return { lat: a.lat, lon: a.lon, alt_ft: a.alt_ft, seg: Math.max(0, lo - 1) };
  const b = points[lo + 1];
  const span = b.t_min - a.t_min;
  const f = span > 0 ? (t - a.t_min) / span : 0;
  const alt_ft = a.alt_ft != null && b.alt_ft != null ? a.alt_ft + (b.alt_ft - a.alt_ft) * f : undefined;
  return { lat: a.lat + (b.lat - a.lat) * f, lon: a.lon + (b.lon - a.lon) * f, alt_ft, seg: lo };
}

/** The n most recent radar updates at or before t, newest first, `step` minutes apart. */
export const scanTimes = (t: number, n: number, step: number): number[] => Array.from({ length: n }, (_, i) => t - i * step);

/** The radar update that is on screen at t: the scope shows the last scan, not a glide between them. */
export const scanFloor = (t: number, step: number): number => Math.floor(t / step + 1e-9) * step;

/** Which lap of its hold the flight is flying at t, counting from 1; null when it is not in the hold. */
export function holdLap(track: { hold?: Hold | null }, t: number): { lap: number; of: number } | null {
  const h = track.hold;
  if (!h || h.laps < 1 || t < h.entry_min || t >= h.exit_min) return null;
  const lap = h.lap_min > 0 ? Math.floor((t - h.entry_min) / h.lap_min) + 1 : 1;
  return { lap: Math.min(h.laps, Math.max(1, lap)), of: h.laps };
}

/** Climb or descent arrow for a data block: compares the altitude now with the altitude a little later. */
export function trend(now: number | undefined, next: number | undefined, deadband = 50): "↑" | "↓" | "" {
  if (now == null || next == null) return "";
  return next < now - deadband ? "↓" : next > now + deadband ? "↑" : "";
}

/** Altitude in hundreds of feet, three digits, as a data block shows it (12,000 ft -> "120"). */
export const levelLabel = (alt_ft: number | undefined): string => (alt_ft == null ? "" : String(Math.max(0, Math.round(alt_ft / 100))).padStart(3, "0"));

/** A segment is drawn dashed when either end is synthesized: a vector that joins a published fix is still
 *  a vector, and the leg from the 150 NM entry boundary to the first fix is not a published one. */
export const segmentDashed = (a: { src: string }, b: { src: string }): boolean => a.src === "synth" || b.src === "synth";

/** Earth radius that makes one arc-minute of latitude exactly one nautical mile (1852 m). */
const R_M = (1852 * 60 * 180) / Math.PI;

/** A flat equirectangular projection around (lat0, lon0): metres east and north of it. Exact enough over an
 *  airport or a terminal area (< 0.1 % at 100 NM), and shared by the radar and the surface map. */
export function projector(lat0: number, lon0: number): (lat: number, lon: number) => [number, number] {
  const k = Math.cos((lat0 * Math.PI) / 180);
  const r = (Math.PI / 180) * R_M;
  return (lat, lon) => [(lon - lon0) * r * k, (lat - lat0) * r];
}

/** The surface map's refresh: the replay time floored to a whole second (surface radar updates about once a second). */
export const secondFloor = (t: number): number => Math.floor(t * 60 + 1e-9) / 60;

/** True heading, degrees, of the vector from a to b (both [east, north] metres). */
export const bearing = (a: [number, number], b: [number, number]): number => ((Math.atan2(b[0] - a[0], b[1] - a[1]) * 180) / Math.PI + 360) % 360;

/** Which end of a drawn runway line a designator belongs to: "04R" is landed on heading ~040°, so it sits at
 *  the end you fly away from. Returns 0 for the line's first point, 1 for its last. */
export function endOfLine(id: string, first: [number, number], last: [number, number]): 0 | 1 {
  const hdg = (parseInt(id, 10) || 36) * 10;
  const d = Math.abs(((bearing(first, last) - hdg + 540) % 360) - 180);
  return d <= 90 ? 0 : 1;
}

export const NM_M = 1852;

/** The radar's projection: `projector` in nautical miles (east, north). Same plane as the surface map. */
export function nmProjector(lat0: number, lon0: number): (lat: number, lon: number) => [number, number] {
  const p = projector(lat0, lon0);
  return (lat, lon) => { const [e, n] = p(lat, lon); return [e / NM_M, n / NM_M]; };
}

/** The fix a flight enters the terminal area at: the first of its path's named fixes that the wave's procedures
 *  mark as an "entry" fix, else the first named fix. `fixBy` is the procedures' fixes by id; when given, only
 *  fixes it knows count (the radar can only place those). Shared by the radar and the strips so they agree. */
export function entryFix(air: { points: { t_min: number; fix?: string }[] }, fixBy?: ReadonlyMap<string, { kind: string }>): { fix: string; t_min: number } | null {
  const named = air.points.filter((p): p is { t_min: number; fix: string } => !!p.fix && (!fixBy || fixBy.has(p.fix)));
  const p = (fixBy && named.find((q) => fixBy.get(q.fix)!.kind === "entry")) || named[0];
  return p ? { fix: p.fix, t_min: p.t_min } : null;
}

export const HOLD_MATCH_NM = 5;

/** Which of the procedures' holds a flight's hold is flown in. Geo holds may share a fix name, so among those
 *  named `hold.fix` the nearest to the flight's own hold points (entry..exit) wins; with no hold points, the
 *  nearest to the fix's position (`fixAt`), else the first of that name. A pattern farther than `maxNm` from
 *  where the flight flew is no match (null), as is no hold of that name. Returns an index into `holds`. */
export function matchHold(
  holds: { fix: string; polygon: [number, number][] }[],
  hold: { fix: string; entry_min: number; exit_min: number },
  points: { t_min: number; lat: number; lon: number; kind: string }[],
  fixAt?: { lat: number; lon: number } | null,
  maxNm = HOLD_MATCH_NM,
): number | null {
  const named = holds.map((h, i) => i).filter((i) => holds[i].fix === hold.fix);
  if (!named.length) return null;
  const flown = points.filter((p) => p.kind === "hold" && p.t_min >= hold.entry_min - 1e-6 && p.t_min <= hold.exit_min + 1e-6);
  const where = flown.length ? { lat: flown.reduce((m, p) => m + p.lat, 0) / flown.length, lon: flown.reduce((m, p) => m + p.lon, 0) / flown.length } : fixAt ?? null;
  if (!where) return named[0];
  const to = nmProjector(where.lat, where.lon);
  const dist = (i: number): number => {
    const poly = holds[i].polygon;
    if (!poly.length) return Infinity;
    const c = poly.map(([la, lo]) => to(la, lo));
    return Math.hypot(c.reduce((m, q) => m + q[0], 0) / c.length, c.reduce((m, q) => m + q[1], 0) / c.length);
  };
  const best = named.map((i) => ({ i, d: dist(i) })).sort((p, q) => p.d - q.d || p.i - q.i)[0];
  return best.d <= maxNm ? best.i : null;
}

export interface Wait { from_min: number; to_min: number; reason: string; lat: number; lon: number }

/** The wait a taxiing aircraft is in at t (minutes vs T0), or null: a wait covers [from_min, to_min). */
export function activeWait<W extends { from_min: number; to_min: number }>(waits: W[], t: number): W | null {
  for (const w of waits) if (t >= w.from_min && t < w.to_min) return w;
  return null;
}

/** A wait as a data block shows it: `HS 17R`, `GW AA1359`, `HT 09R` (hold for takeoff), else the reason's start. */
export function waitTag(reason: string): string {
  const m = reason.match(/^hold short (?:of )?(\S+)/i);
  if (m) return `HS ${m[1]}`;
  const gw = reason.match(/^give way to (\S+)/i);
  if (gw) return `GW ${gw[1]}`;
  const ht = reason.match(/^hold for takeoff (\S+)/i);
  if (ht) return `HT ${ht[1]}`;
  return reason.split(" ").slice(0, 2).join(" ").toUpperCase();
}

/** Where a timed path on the plane (metres, `[t_min, x, y]`) crosses runway centrelines: the runway's index and
 *  the time it is on the centreline, in time order. A runway the path starts or ends on (within `clearM` of its
 *  centreline: the strip an arrival vacates, or a departure's line-up) is its own and is not a crossing. */
export function runwayCrossings(path: [number, number, number][], runways: [number, number][][], clearM = 60): { rw: number; t: number }[] {
  const out: { rw: number; t: number }[] = [];
  if (path.length < 2) return out;
  const near = (p: [number, number, number], xy: [number, number][]) => xy.slice(1).some((b, i) => {
    const a = xy[i], dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
    const u = L2 ? Math.max(0, Math.min(1, ((p[1] - a[0]) * dx + (p[2] - a[1]) * dy) / L2)) : 0;
    return Math.hypot(a[0] + u * dx - p[1], a[1] + u * dy - p[2]) <= clearM;
  });
  const first = path[0], last = path[path.length - 1];
  runways.forEach((xy, rw) => {
    if (xy.length < 2 || near(first, xy) || near(last, xy)) return;
    for (let k = 1; k < path.length; k++) {
      const [t0, x0, y0] = path[k - 1], [t1, x1, y1] = path[k];
      for (let i = 1; i < xy.length; i++) {
        const [ax, ay] = xy[i - 1], [bx, by] = xy[i];
        const d = (x1 - x0) * (by - ay) - (y1 - y0) * (bx - ax);
        if (!d) continue;
        const u = ((ax - x0) * (by - ay) - (ay - y0) * (bx - ax)) / d;     // along the path segment
        const v = ((ax - x0) * (y1 - y0) - (ay - y0) * (x1 - x0)) / d;     // along the runway segment
        // half-open on both lines, so a crossing at a shared vertex counts once; the runway line's last point is closed
        if (u >= 0 && u < 1 && v >= 0 && (v < 1 || (i === xy.length - 1 && v <= 1))) out.push({ rw, t: t0 + (t1 - t0) * u });
      }
    }
  });
  // rounding can still put one crossing on both sides of a vertex: one runway, the same instant, counts once
  out.sort((a, b) => a.t - b.t);
  return out.filter((c, k) => !out.slice(0, k).some((d) => d.rw === c.rw && c.t - d.t < 1e-4));
}
