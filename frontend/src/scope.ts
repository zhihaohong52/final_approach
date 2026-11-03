// Pure rules behind the replay pictures, kept apart from the components so they are tested.

/** The Gantt axis start (minutes vs T0): 15-minute steps. A plan whose first event is after T+45 starts 15 min
 *  before it; an earlier one starts at T0 or before. Hold starts count as events, so a lap is on the scrubber. */
export function axisStart(times: number[], holdStarts: number[]): number {
  const first = times.length ? Math.min(...times, ...holdStarts) : 0;
  return first > 45 ? Math.floor((first - 15) / 15) * 15 : Math.min(0, Math.floor((Math.min(0, ...times, ...holdStarts) - 3) / 15) * 15);
}

/** The axis end: three hours at most, at least 75 min, 15-minute steps after the last event. */
export function axisEnd(start: number, last: number): number {
  return Math.min(start + 180, Math.max(start + 75, Math.ceil((last + 3) / 15) * 15));
}

export const RANGES = [10, 20, 40, 80];       // NM from centre to the short edge, as on a scope range knob
export const DEFAULT_RANGE = 20;

export interface Lim { l: number; r: number; t: number; b: number }

/** Auto range: 20 NM, or the smallest wider scope that holds every active hold point (12 % margin; the widest if none
 *  does). A hold close in never zooms below the default. */
export function autoRange(hold: [number, number][], limOf: (r: number) => Lim): number {
  if (!hold.length) return DEFAULT_RANGE;
  const wide = RANGES.filter((r) => r >= DEFAULT_RANGE);
  return wide.find((r) => { const q = limOf(r); return hold.every(([x, y]) => x * 1.12 >= q.l && x * 1.12 <= q.r && y * 1.12 >= q.t && y * 1.12 <= q.b); })
    ?? wide[wide.length - 1];
}

/** The header chip. A run that was streamed from a recording names the recorded model and mode; otherwise the picker. */
export function chipLabel(o: {
  fromReplay: boolean; result: { mode: string; model?: string | null } | null;
  models: { id: string; label: string }[]; mode: string; model: string; modeLabel: Record<string, string>;
}): string {
  const named = (id: string | null | undefined) => (id ? o.models.find((x) => x.id === id)?.label ?? id : null);
  if (o.fromReplay && o.result) return o.result.mode === "formulas" ? "Formulas" : named(o.result.model) ?? o.modeLabel[o.result.mode] ?? o.result.mode;
  return o.mode === "formulas" ? "Formulas" : named(o.model) ?? o.modeLabel[o.mode] ?? o.mode;
}
