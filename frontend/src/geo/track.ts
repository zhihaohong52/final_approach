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
