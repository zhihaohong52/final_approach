import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Assignment, FlightMetrics, JointPlan, Move, RuleCheck, TrajectorySet, WaveScenario } from "../api/types";
import { icao } from "../callsign";
import { clock } from "../format";
import type { Tone } from "./Radar";
import { axisEnd, axisStart } from "../scope";

// pointer capture throws when the pointer is already gone (lifted before the handler ran); the drag still works without it
const capture = (el: Element | null, id: number) => { try { el?.setPointerCapture(id); } catch { /* pointer already released */ } };

const LABEL = 72, RIGHT = 12, LANE = 28, BAR = 16, AXIS = 34, GAP = 24, NOTE = 22;
// On the home page the Gantt shares the screen with the scope: its card is GANTT_SHARE of the page height (the same
// 45% as .ops.home .plan-card in styles.css) and the drawing shrinks uniformly, down to MIN_K, to fit it; only past that
// does the card scroll.
export const GANTT_SHARE = 0.45;
const MIN_K = 0.75;
const FIT_MQ = "(min-width: 1100px) and (min-height: 700px)";
const WAKE_QUICK_MIN = 2;            // quick in-browser check while dragging; the server applies the real wake table

export interface Rejection { seq: number; move: Move; violations: RuleCheck[]; runway?: string }
export type View = "before" | "compare" | "after";
export interface Patch { runway?: string; slot_min?: number; gate?: string }
/** The server's re-check of a bar while it is dragged (POST /api/evaluate with include_trajectories). */
export interface DragPreview { cs: string; patch: Patch; metrics: FlightMetrics | null; set: TrajectorySet | null }
const samePatch = (a: Patch, b: Patch) => a.runway === b.runway && a.slot_min === b.slot_min && a.gate === b.gate;

type Air = TrajectorySet["air"][number];
type Ground = TrajectorySet["ground"][number];
type Span = { from: number; to: number; title: string };

/** The holding laps before the slot, one span per lap: from the engine's track when it has one, else from the
 *  plan's metrics (hold minutes ending at the slot, split into its laps). */
export function holdSpans(a: Assignment, air?: Air | null): Span[] {
  const h = air?.hold;
  if (h && h.laps >= 1 && h.exit_min > h.entry_min) {
    const lap = h.lap_min > 0 ? h.lap_min : (h.exit_min - h.entry_min) / h.laps;
    return Array.from({ length: h.laps }, (_, i) => ({ from: h.entry_min + i * lap, to: Math.min(h.exit_min, h.entry_min + (i + 1) * lap),
      title: `holds at ${h.fix}, lap ${i + 1} of ${h.laps} at ${Math.round(h.level_ft / 100) * 100} ft · ${lap.toFixed(1)} min a lap` }));
  }
  const m = a.metrics, slot = a.slot_min ?? 0;
  if (!(m.hold_min > 0)) return [];
  const laps = m.hold_laps && m.hold_laps >= 1 ? m.hold_laps : 1, lap = m.hold_min / laps;
  return Array.from({ length: laps }, (_, i) => ({ from: slot - m.hold_min + i * lap, to: slot - m.hold_min + (i + 1) * lap,
    title: laps > 1 || m.hold_fix ? `holds${m.hold_fix ? ` at ${m.hold_fix}` : ""}, lap ${i + 1} of ${laps} · ${m.hold_min} min in all` : `holds ${m.hold_min} min` }));
}

interface Props {
  wave: WaveScenario;
  plan: JointPlan | null;              // what is drawn solid
  baseline?: JointPlan | null;         // status quo: dashed ghosts in Compare
  agreed?: JointPlan | null;           // the agents' plan, ghosted where the duty manager moved a flight
  view: View;
  turn: number;                        // gate occupancy after in-block (GND-GATE-FREE evidence)
  rejection: Rejection | null;
  tone: (cs: string) => Tone;
  involved?: Set<string>;              // flights named in a failing check: outlined red
  editable: boolean;
  onEdit?: (cs: string, p: Patch) => void;
  t: number | null;                    // time cursor shared with the radar
  onTime?: (t: number) => void;
  paths?: TrajectorySet | null;        // the engine's paths for `plan`: hold laps and taxi tracks
  onDrag?: (cs: string, p: Patch | null) => void;   // where a dragged bar is now (null when the drag ends)
  preview?: DragPreview | null;        // the server's answer for a drag position
  previewFailed?: boolean;             // the last drag re-check failed: the bar shows its local estimate
  selected: string | null;
  onSelect: (cs: string | null) => void;
}

function rows<T extends { from: number; to: number }>(items: T[]): (T & { row: number })[] {
  const ends: number[] = [];
  return [...items].sort((a, b) => a.from - b.from).map((it) => {
    let r = ends.findIndex((e) => e <= it.from);
    if (r < 0) { r = ends.length; ends.push(0); }
    ends[r] = it.to;
    return { ...it, row: r };
  });
}

type Drag = { cs: string; kind: "land" | "gate"; x0: number; y0: number; slot0: number; lane0: string; slot: number; lane: string; moved: boolean };

const TONE_FILL: Record<Tone, string> = { focus: "var(--hue)", flight: "var(--flight)", edited: "var(--human)", alert: "var(--negative)", fixed: "var(--queue)" };
const TONE_INK: Record<Tone, string> = { focus: "#ffffff", flight: "#0e0d11", edited: "#1b1404", alert: "#ffffff", fixed: "#0e0d11" };
const TONE_TEXT: Record<Tone, string> = { focus: "var(--hue)", flight: "var(--text)", edited: "var(--human)", alert: "var(--negative)", fixed: "var(--soft)" };

export function Gantt({ wave, plan, baseline, agreed, view, turn, rejection, tone, involved, editable, onEdit, t, onTime, selected, onSelect, paths = null, onDrag, preview = null, previewFailed = false }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [cw, setW] = useState(1100);   // the drawing is scaled by k to fit the card, so its own width is the container's over k
  const [k, setK] = useState(1);
  const [room, setRoom] = useState(Infinity);
  const W = cw / k;
  const [drag, setDrag] = useState<Drag | null>(null);
  const [scrub, setScrub] = useState(false);
  const dragged = useRef(false);
  const [settle, setSettle] = useState(false);
  const [scrollX, setScrollX] = useState(0);                // lane notes follow the visible left edge on narrow screens
  // the height the card can give the drawing on the home page (Infinity: the page scrolls instead)
  useLayoutEffect(() => {
    const el = wrap.current, main = el?.closest<HTMLElement>(".ops.home"), card = el?.closest<HTMLElement>(".plan-card");
    if (!el || !main || !card) { setRoom(Infinity); return; }
    const mq = matchMedia(FIT_MQ);
    const measure = () => {
      const cs = getComputedStyle(main);   // a percentage max-height is of the content box
      const inner = main.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
      setRoom(mq.matches ? Math.floor(GANTT_SHARE * inner - (card.offsetHeight - el.offsetHeight)) : Infinity);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(main); ro.observe(card);
    mq.addEventListener("change", measure);
    return () => { ro.disconnect(); mq.removeEventListener("change", measure); };
  }, []);
  // measured before the first paint, so bars never start at a guessed width and glide into place
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    setW(Math.max(760, Math.round(el.clientWidth)));
    const ro = new ResizeObserver(([e]) => setW(Math.max(760, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const land = (p: JointPlan | null | undefined) => (p?.assignments ?? []).filter((a) => a.action === "land");
  const plans = [plan, baseline, agreed].filter(Boolean) as JointPlan[];
  const usedRwy = new Set([...wave.queue.map((q) => q.runway), ...plans.flatMap((p) => land(p).map((a) => a.runway ?? ""))]);
  // closed runways stay visible (they are why the plan changed); open ones only when used, plus every open one while dragging
  // while a landing mark is dragged the other open runways join below, so no lane moves under the pointer
  const baseRwy = wave.airport.runways.filter((r) => !r.open || usedRwy.has(r.id));
  const runways = drag?.kind === "land" ? [...baseRwy, ...wave.airport.runways.filter((r) => r.open && !usedRwy.has(r.id))] : baseRwy;
  const hiddenRwy = wave.airport.runways.length - runways.length;
  const usedGate = new Set([...wave.flights.map((f) => f.scheduled_gate), ...plans.flatMap((p) => land(p).map((a) => a.gate ?? ""))]);
  const baseGates = wave.gates.filter((g) => usedGate.has(g.id));
  const gatesShown = drag?.kind === "gate" ? [...baseGates, ...wave.gates.filter((g) => !usedGate.has(g.id) && !g.remote && g.size === sizeOf(drag.cs))] : baseGates;
  const hiddenGates = wave.gates.length - gatesShown.length;
  function sizeOf(cs: string) { return wave.flights.find((f) => f.callsign === cs)?.gate_size ?? "narrow"; }

  const times = plans.flatMap((p) => land(p).flatMap((a) => [a.slot_min ?? 0, a.metrics.in_block_min ?? 0]));
  const lastPlan = plans.flatMap((p) => land(p).map((a) => (a.metrics.in_block_min ?? a.slot_min ?? 0) + turn));
  const lastQueue = wave.queue.map((q) => q.slot_min + 2);
  // the axis also covers the start of every hold in the shown plan, so a lap is on the scrubber
  const holdStart = (paths?.air ?? []).flatMap((a) => a.points.filter((p) => p.kind === "hold").slice(0, 1).map((p) => p.t_min));
  const firstPlan = times.length ? Math.min(...times, ...holdStart) : 0;
  // never narrower than it has been for this wave, so the axis does not jump when paths load or a tab changes
  const startSeen = useRef<{ id: string; v: number }>({ id: wave.id, v: Infinity });
  const computedStart = axisStart(times, holdStart);
  if (startSeen.current.id !== wave.id) startSeen.current = { id: wave.id, v: Infinity };
  const START = Math.min(computedStart, startSeen.current.v);
  startSeen.current.v = START;
  const lastAll = Math.max(START, ...lastPlan, ...(firstPlan > 45 ? [] : lastQueue));
  // the disrupted flight is always on the axis, landing and gate, however late it lands
  const focusEnd = plans.flatMap((p) => land(p).filter((a) => a.callsign === wave.focus).map((a) => (a.metrics.in_block_min ?? a.slot_min ?? 0) + turn));
  const END = axisEnd(START, lastAll, Math.max(-Infinity, ...focusEnd));
  const inView = (from: number, to: number) => to >= START && from <= END;
  // flights landing after the window are named in a gutter on the right, clear of every bar
  // fixed for the length of a drag, so the time scale never shifts under the pointer
  const wantGutter = [plan, agreed].some((p) => land(p).some((a) => (a.slot_min ?? 0) > END));
  const gutterRef = useRef(wantGutter);
  if (!drag) gutterRef.current = wantGutter;
  const GUTTER = gutterRef.current ? 118 : RIGHT;
  const pxPerMin = (W - LABEL - GUTTER) / (END - START);
  const x = (m: number) => LABEL + (Math.max(START, Math.min(END, m)) - START) * pxPerMin;

  const yRwy = (id: string) => AXIS + runways.findIndex((r) => r.id === id) * LANE;
  const rwyEnd = AXIS + runways.length * LANE + (hiddenRwy ? NOTE : 0);
  const gatesTop = rwyEnd + GAP;

  // gate lanes: plan bars (with the dragged one at its preview gate) and other aircraft
  const patchOf = (d: Drag): Patch => d.kind === "land" ? { runway: d.lane, slot_min: d.slot }
    : { gate: d.lane, ...(d.slot !== d.slot0 ? { runway: land(plan).find((o) => o.callsign === d.cs)?.runway, slot_min: d.slot } : {}) };
  // the server's answer for exactly where the bar is now; a stale one still says how the hold and taxi look
  const fresh = !!drag && !!preview && preview.cs === drag.cs && samePatch(preview.patch, patchOf(drag));
  const live = (a: Assignment): Assignment => {
    if (!drag || drag.cs !== a.callsign) return a;
    // either bar moves the flight in time (it reaches the gate a taxi after landing); each changes its own lane
    const dt = drag.slot - (a.slot_min ?? 0);
    const metrics = fresh && preview!.metrics ? preview!.metrics : { ...a.metrics, in_block_min: (a.metrics.in_block_min ?? a.slot_min ?? 0) + dt };
    const moved = { ...a, slot_min: drag.slot, metrics };
    return drag.kind === "land" ? { ...moved, runway: drag.lane } : { ...moved, gate: drag.lane };
  };
  const airBy = new Map((paths?.air ?? []).map((x) => [x.callsign, x]));
  const groundBy = new Map((paths?.ground ?? []).filter((x) => x.kind === "arrival").map((x) => [x.callsign, x]));
  // hold laps and taxi of a flight as drawn: while its bar is dragged, from the server's re-check of the drag
  // position (shifted with the bar until the answer for the new position arrives); otherwise from the plan's paths
  const pathOf = (a: Assignment): { air: Air | null; ground: Ground | null; shift: number; stale: boolean } => {
    if (drag?.moved && drag.cs === a.callsign) {
      if (!preview || preview.cs !== a.callsign) return { air: null, ground: null, shift: 0, stale: true };
      const ps = preview.patch.slot_min ?? drag.slot0;
      return { air: preview.set?.air.find((x) => x.callsign === a.callsign) ?? null, ground: preview.set?.ground.find((x) => x.callsign === a.callsign && x.kind === "arrival") ?? null,
        shift: fresh ? 0 : drag.slot - ps, stale: !fresh };
    }
    return { air: airBy.get(a.callsign) ?? null, ground: groundBy.get(a.callsign) ?? null, shift: 0, stale: false };
  };
  const shownPlan = land(plan).map(live);
  const lanes = gatesShown.map((g) => {
    const flights = shownPlan.filter((a) => a.gate === g.id).map((a) => {
      const from = a.metrics.in_block_min ?? a.slot_min ?? 0;
      return { kind: "wave" as const, label: a.callsign, from, to: from + turn };
    });
    const busy = g.busy.filter((b) => inView(b.from_min, b.to_min)).map((b) => ({ kind: "busy" as const, label: b.label, from: b.from_min, to: b.to_min }));
    const items = rows([...busy, ...flights]);
    return { g, items, n: Math.max(1, ...items.map((i) => i.row + 1)) };
  });
  const gateY: Record<string, number> = {};
  let y = gatesTop;
  for (const l of lanes) { gateY[l.g.id] = y; y += l.n * LANE; }
  const H = y + (hiddenGates ? NOTE : 0) + 6;
  useLayoutEffect(() => { setK(Math.max(MIN_K, Math.min(1, room / H))); }, [room, H]);
  const ticks: number[] = [];
  for (let m = START; m <= END; m += 15) ticks.push(m);
  const tickPx = 15 * pxPerMin;

  let rejY: number | null = null;
  if (rejection) {
    const p = rejection.move.params, cs = rejection.move.callsigns[0];
    const rwy = typeof p.runway === "string" ? p.runway : rejection.runway ?? plan?.assignments.find((a) => a.callsign === cs)?.runway;
    if (typeof p.gate === "string" && gateY[p.gate] != null) rejY = gateY[p.gate];
    else if (rwy && runways.some((r) => r.id === rwy)) rejY = yRwy(rwy);
  }
  const real = (label: string) => !/simulated/i.test(label);
  const noteX = Math.max(x(START), scrollX + LABEL) + 8;
  const offAxis: JSX.Element[] = [];   // flights landing after the window: labels on the right edge
  const offRow = new Map<number, number>();   // labels already on a row, so a second one sits below, not on top
  const offDy = (y: number) => { const n = offRow.get(y) ?? 0; offRow.set(y, n + 1); return n * 12; };

  // on a narrow screen the chart opens on the disrupted flight's landing, the subject of the page
  const focusSlot = plan?.assignments.find((a) => a.callsign === wave.focus)?.slot_min;
  const firstX = focusSlot != null ? x(focusSlot) : times.length ? x(Math.min(...times)) : 0;
  useEffect(() => {
    const el = wrap.current;
    if (el && el.scrollWidth > el.clientWidth + 8) el.scrollLeft = Math.max(0, (firstX - LABEL - (el.clientWidth / k - LABEL) / 3) * k);
  }, [firstX]);

  // ---------- pointer: drag a bar, or scrub the time cursor on the axis ----------
  const local = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / k, (e.clientY - r.top) / k] as const;
  };
  const laneAt = (kind: "land" | "gate", py: number, fallback: string) => {
    if (kind === "land") {
      const i = Math.floor((py - AXIS) / LANE);
      return runways[Math.max(0, Math.min(runways.length - 1, i))]?.id ?? fallback;
    }
    const hit = lanes.find((l) => py >= gateY[l.g.id] && py < gateY[l.g.id] + l.n * LANE) ?? (py < gatesTop ? lanes[0] : lanes[lanes.length - 1]);
    return hit?.g.id ?? fallback;
  };
  const startDrag = (e: React.PointerEvent, a: Assignment, kind: "land" | "gate") => {
    onSelect(a.callsign);
    if (!editable || !onEdit) return;
    e.stopPropagation();
    e.preventDefault();                                     // no text selection while dragging
    dragged.current = false;
    capture(svg.current, e.pointerId);
    const [px, py] = local(e);
    const lane = kind === "land" ? a.runway ?? "" : a.gate ?? "";
    setDrag({ cs: a.callsign, kind, x0: px, y0: py, slot0: a.slot_min ?? 0, lane0: lane, slot: a.slot_min ?? 0, lane, moved: false });
  };
  // the scrubber, drawn in the axis of the drawing and again in the pinned axis that covers it once the rows scroll
  const scrubber = (
    <rect x={LABEL} y={0} width={W - LABEL - GUTTER} height={AXIS - 8} fill="transparent"
      style={{ cursor: onTime ? "ew-resize" : undefined, pointerEvents: onTime ? "auto" : "none" }}
      onPointerDown={(e) => { if (!onTime) return; e.stopPropagation(); capture(e.target as Element, e.pointerId); setScrub(true); const [px] = local(e); onTime(Math.round(START + (px - LABEL) / pxPerMin)); }} />
  );
  const onMove = (e: React.PointerEvent) => {
    if (scrub && onTime) { const [px] = local(e); onTime(Math.round(START + (px - LABEL) / pxPerMin)); return; }
    if (!drag) return;
    const [px, py] = local(e);
    const moved = drag.moved || Math.hypot(px - drag.x0, py - drag.y0) > 3;
    const slot = Math.round(drag.slot0 + (px - drag.x0) / pxPerMin);
    const next = { ...drag, moved, slot, lane: laneAt(drag.kind, py, drag.lane0) };
    if (moved && onDrag && (!drag.moved || next.slot !== drag.slot || next.lane !== drag.lane)) onDrag(drag.cs, patchOf(next));
    setDrag(next);
  };
  // Escape drops a drag where it started; the click that ends it is swallowed
  useEffect(() => {
    if (!drag) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); dragged.current = drag.moved; setDrag(null); onDrag?.(drag.cs, null); } };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [drag]);

  const onUp = () => {
    setScrub(false);
    if (!drag) return;
    const d = drag;
    setDrag(null);
    if (d.moved) onDrag?.(d.cs, null);
    if (d.moved) { setSettle(true); setTimeout(() => setSettle(false), 80); }
    if (!d.moved || !onEdit) return;
    dragged.current = true;
    const verdict = quick(d);
    if (verdict.block) return;                               // a closed runway refuses the drop
    if (d.kind === "land" && (d.slot !== d.slot0 || d.lane !== d.lane0)) onEdit(d.cs, { runway: d.lane, slot_min: d.slot });
    if (d.kind === "gate" && (d.slot !== d.slot0 || d.lane !== d.lane0)) {
      const a = land(plan).find((o) => o.callsign === d.cs);
      onEdit(d.cs, { gate: d.lane, ...(d.slot !== d.slot0 ? { runway: a?.runway, slot_min: d.slot } : {}) });
    }
  };

  // ---------- quick checks while dragging (the server's re-check is the one that counts) ----------
  const twin = (id: string) => {
    const r = wave.airport.runways.find((x) => x.id === id);
    if (!r || r.le_lat == null) return new Set([id]);
    return new Set(wave.airport.runways.filter((o) => o.le_lat != null && ((Math.abs(o.le_lat - r.le_lat!) < 1e-4 && Math.abs(o.le_lon! - r.le_lon!) < 1e-4) || (Math.abs(o.le_lat - r.he_lat!) < 1e-4 && Math.abs(o.le_lon! - r.he_lon!) < 1e-4))).map((o) => o.id));
  };
  function quick(d: Drag): { block: boolean; text: string; bad: boolean; gap?: { from: number; to: number } } {
    if (d.kind === "land") {
      const r = wave.airport.runways.find((x) => x.id === d.lane);
      if (r && !r.open) return { block: true, bad: true, text: `can't land on ${d.lane}: closed` };
      const strip = twin(d.lane);
      const others = [
        ...land(plan).filter((a) => a.callsign !== d.cs && strip.has(a.runway ?? "")).map((a) => ({ cs: a.callsign, s: a.slot_min ?? 0 })),
        ...wave.queue.filter((q) => strip.has(q.runway)).map((q) => ({ cs: q.callsign, s: q.slot_min })),
      ];
      const near = others.sort((a, b) => Math.abs(a.s - d.slot) - Math.abs(b.s - d.slot))[0];
      if (!near) return { block: false, bad: false, text: `${d.lane} ${clock(wave.window_start, d.slot)} · runway free` };
      const g = Math.abs(near.s - d.slot);
      return { block: false, bad: g < WAKE_QUICK_MIN, gap: { from: Math.min(near.s, d.slot), to: Math.max(near.s, d.slot) },
        text: `${g < 0.05 ? "same minute as" : `${g.toFixed(1)} min ${near.s <= d.slot ? "behind" : "ahead of"}`} ${icao(near.cs)}${g < WAKE_QUICK_MIN ? ` · wake gap needs ~${WAKE_QUICK_MIN} min` : ""}` };
    }
    const g = wave.gates.find((x) => x.id === d.lane);
    const a = land(plan).find((x) => x.callsign === d.cs);
    if (!g || !a) return { block: false, bad: false, text: "" };
    // moved in time from the gate bar: the landing moves too, so its runway gap is checked first
    if (d.slot !== d.slot0) { const rw = quick({ ...d, kind: "land", lane: a.runway ?? "" }); if (rw.bad || rw.block) return { ...rw, gap: undefined, text: `lands ${clock(wave.window_start, d.slot)} · ${rw.text}` }; }
    const from = (a.metrics.in_block_min ?? a.slot_min ?? 0) + (d.slot - d.slot0), to = from + turn;
    if (g.size === "narrow" && sizeOf(d.cs) === "wide") return { block: false, bad: true, text: `${d.lane} is a narrow-body gate` };
    const clash = g.busy.some((b) => b.from_min < to && b.to_min > from) || land(plan).some((o) => o.callsign !== d.cs && o.gate === d.lane && (o.metrics.in_block_min ?? 0) < to && (o.metrics.in_block_min ?? 0) + turn > from);
    return { block: false, bad: clash, text: clash ? `${d.lane} is occupied at ${clock(wave.window_start, from)}` : `${d.lane} free from ${clock(wave.window_start, from)}` };
  }
  const q = drag?.moved ? quick(drag) : null;

  // ---------- keyboard: arrows move the selected bar ----------
  const onKey = (e: React.KeyboardEvent, a: Assignment, kind: "land" | "gate") => {
    if (!editable || !onEdit) return;
    const step = e.shiftKey ? 5 : 1;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault(); onEdit(a.callsign, { runway: a.runway, slot_min: (a.slot_min ?? 0) + (e.key === "ArrowRight" ? step : -step) });
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const ids = kind === "land" ? wave.airport.runways.filter((r) => r.open).map((r) => r.id) : wave.gates.filter((g) => !g.remote && g.size === sizeOf(a.callsign)).map((g) => g.id);
      const cur = kind === "land" ? a.runway ?? "" : a.gate ?? "";
      const i = ids.indexOf(cur), n = ids[(Math.max(0, i) + (e.key === "ArrowDown" ? 1 : ids.length - 1)) % ids.length];
      if (n && n !== cur) onEdit(a.callsign, kind === "land" ? { runway: n, slot_min: a.slot_min } : { gate: n });
    }
  };

  const compare = view === "compare";
  const byCs = (p: JointPlan | null | undefined, cs: string) => p?.assignments.find((a) => a.callsign === cs);
  const gatePos = (a: Assignment) => {
    const lane = lanes.find((l) => l.g.id === a.gate);
    const item = lane?.items.find((i) => i.kind === "wave" && i.label === a.callsign);
    return (gateY[a.gate ?? ""] ?? -100) + (item?.row ?? 0) * LANE;
  };
  // a ghost: where a flight was in another plan, drawn as a dashed outline with an arrow to where it is now
  const ghost = (from: Assignment | undefined, to: Assignment, label: string | null, stroke: string, key: string) => {
    if (!from || from.action !== "land") return null;
    const out: React.ReactNode[] = [];
    const moved = from.runway !== to.runway || Math.abs((from.slot_min ?? 0) - (to.slot_min ?? 0)) >= 0.5;
    if (moved && runways.some((r) => r.id === from.runway) && (from.slot_min ?? 0) <= END) {
      const gx = x(from.slot_min ?? 0), gy = yRwy(from.runway ?? ""), tx = x(to.slot_min ?? 0), ty = yRwy(to.runway ?? "");
      out.push(<rect key={`${key}r`} x={gx - 3} y={gy + 5} width={7} height={BAR} rx={1} fill="none" stroke={stroke} strokeDasharray="3 2" />);
      // name the ghost only when it sits clear of the bar it moved to
      if (label && (from.runway !== to.runway || Math.abs((from.slot_min ?? 0) - (to.slot_min ?? 0)) * pxPerMin > 90))
        out.push(<text key={`${key}l`} className="ghost-label" x={gx + 8} y={gy + 17}>{label}</text>);
      if (to.slot_min != null && to.slot_min <= END) {
        const mx = (gx + tx) / 2, my = Math.min(gy, ty) + (gy === ty ? -10 : 0);
        out.push(<path key={`${key}a`} d={`M${gx} ${gy + (ty < gy ? 5 : ty > gy ? 21 : 5)} Q ${mx} ${my} ${tx} ${ty + (ty < gy ? 21 : ty > gy ? 5 : 5)}`} fill="none" stroke={stroke} strokeOpacity=".7" markerEnd="url(#arrow)" />);
      }
    }
    if (from.gate !== to.gate || Math.abs((from.metrics.in_block_min ?? 0) - (to.metrics.in_block_min ?? 0)) >= 0.5) {
      const gy = gateY[from.gate ?? ""];
      const fx = x(from.metrics.in_block_min ?? from.slot_min ?? 0);
      if (gy != null && (from.metrics.in_block_min ?? 0) <= END)
      {
        const gw = Math.max(6, x((from.metrics.in_block_min ?? 0) + turn) - fx);
        out.push(<rect key={`${key}g`} x={fx} y={gy + 5} width={gw} height={BAR} rx={2} fill="none" stroke={stroke} strokeDasharray="3 2" />);
        const who = label?.split(":")[0];
        if (who && gw > 90) out.push(<text key={`${key}gl`} className="ghost-label" x={fx + 7} y={gy + 17}>{`${who}: ${from.gate} ${clock(wave.window_start, from.metrics.in_block_min ?? 0)}`}</text>);
      }
    }
    return <g key={key} className="ghost" pointerEvents="none">{out}</g>;
  };

  return (
    <div className={`gantt-wrap${drag?.moved ? " dragging" : ""}`} ref={wrap} onScroll={(e) => setScrollX(e.currentTarget.scrollLeft)} tabIndex={-1} role="region" aria-label="Runway and gate timeline, scrolls sideways on small screens">
      <svg className="gantt gantt-labels" viewBox={`0 0 ${LABEL - 4} ${H}`} width={(LABEL - 4) * k} height={H * k} aria-hidden="true">
        <text className="group" x={0} y={AXIS - 6}>Runways</text>
        {runways.map((r) => <text key={r.id} className="lane-label" x={0} y={yRwy(r.id) + 18} style={r.open ? undefined : { fill: "var(--muted)" }}>{r.id}</text>)}
        <text className="group" x={0} y={gatesTop - 6}>Gates</text>
        {lanes.map(({ g }) => <text key={g.id} className="lane-label" x={0} y={gateY[g.id] + 18}>{g.id === "RS" ? "Remote" : g.id}</text>)}
      </svg>
      <svg ref={svg} className="gantt" viewBox={`0 0 ${W} ${H}`} width={cw} height={H * k} role="group"
        aria-label={`Runway and gate timeline, ${clock(wave.window_start, START)} to ${clock(wave.window_start, END)}${editable ? ". Drag a bar, or select it and use the arrow keys, to move a flight." : ""}`}
        onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={() => { if (drag?.moved) onDrag?.(drag.cs, null); setDrag(null); setScrub(false); }} onClick={() => { if (dragged.current) dragged.current = false; else onSelect(null); }}>
        <defs>
          <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--hatch)" strokeWidth="2.5" />
          </pattern>
          {/* holding laps: hatched in the flight's own colour */}
          {(Object.keys(TONE_FILL) as Tone[]).map((k) => (
            <pattern key={k} id={`hold-${k}`} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="5" stroke={TONE_FILL[k]} strokeWidth="1.6" />
            </pattern>
          ))}
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 1L9 5L0 9" fill="none" stroke="context-stroke" strokeWidth="1.6" /></marker>
          <filter id="lift" x="-80%" y="-80%" width="260%" height="260%"><feDropShadow dx="0" dy="5" stdDeviation="4" floodColor="#000" floodOpacity=".75" /></filter>
        </defs>

        {/* axis: also the time scrubber */}
        {scrubber}
        {ticks.map((m) => (
          <g key={m} pointerEvents="none">
            <line x1={x(m)} x2={x(m)} y1={AXIS - 6} y2={H} stroke={m === 0 || m === 60 ? "var(--control)" : "var(--gridline)"} />
            {(tickPx >= 60 || (m !== START && m !== END)) && (tickPx >= 44 || ((m - START) / 15) % 2 === 0) && (
              <text className="tick" x={x(m)} y={16} textAnchor={m === START ? "start" : m === END ? "end" : "middle"}>{clock(wave.window_start, m)}</text>
            )}
          </g>
        ))}

        {runways.map((r) => {
          const ry = yRwy(r.id);
          const target = drag?.moved && drag.kind === "land" && drag.lane === r.id;
          return (
            <g key={r.id}>
              {target && <rect x={LABEL} y={ry + 2} width={W - LABEL - GUTTER} height={LANE - 4} rx={3} fill={r.open ? "rgba(232,176,75,.07)" : "rgba(229,86,111,.1)"} />}
              {!r.open && (
                <>
                  <rect x={x(START)} y={ry + 6} width={x(END) - x(START)} height={BAR} fill="url(#hatch)" rx={2} />
                  <text className="closed-label" x={noteX} y={ry + 18}>Closed{r.closed_reason ? ` · ${r.closed_reason}` : ""}</text>
                </>
              )}
              {wave.queue.filter((qq) => qq.runway === r.id && inView(qq.slot_min, qq.slot_min)).map((qq) => (
                <rect key={qq.callsign} x={x(qq.slot_min) - 1} y={ry + 7} width={2.5} height={BAR - 2} rx={1} fill="var(--fixed)">
                  <title>{`${icao(qq.callsign)} ${qq.aircraft_type ?? ""} ${qq.wake} · ${clock(wave.window_start, qq.slot_min)} · fixed traffic, not negotiated`}</title>
                </rect>
              ))}
            </g>
          );
        })}
        {hiddenRwy > 0 && <text x={noteX - 8} y={rwyEnd - 6} style={{ fontSize: 13 }}>{hiddenRwy} other runway{hiddenRwy === 1 ? "" : "s"} open, not used in this wave{editable ? " · they appear while you drag" : ""}</text>}

        {lanes.map(({ g, items }) => {
          const target = drag?.moved && drag.kind === "gate" && drag.lane === g.id;
          return (
            <g key={g.id}>
              {target && <rect x={LABEL} y={gateY[g.id] + 2} width={W - LABEL - GUTTER} height={LANE - 4} rx={3} fill={q?.bad ? "rgba(229,86,111,.1)" : "rgba(232,176,75,.07)"} />}
              {items.filter((i) => i.kind === "busy").map((b, i) => (
                <g key={i}>
                  <rect x={x(b.from)} y={gateY[g.id] + b.row * LANE + 6} width={Math.max(2, x(b.to) - x(b.from))} height={BAR} rx={2} fill="var(--busy)"><title>{b.label}</title></rect>
                  {real(b.label) && x(b.to) - x(b.from) > 110 && <text className="q-label" x={x(b.from) + 6} y={gateY[g.id] + b.row * LANE + 18}>{b.label}</text>}
                </g>
              ))}
            </g>
          );
        })}
        {hiddenGates > 0 && <text x={noteX - 8} y={H - 10} style={{ fontSize: 13 }}>{hiddenGates} other gate{hiddenGates === 1 ? "" : "s"} not used by these flights</text>}

        {/* ghosts first, so the plan draws over them */}
        {compare && plan && land(plan).map((a) => { const b = byCs(baseline, a.callsign);
          return ghost(b, a, b ? `before: ${b.runway} ${clock(wave.window_start, b.slot_min ?? 0)}` : null, "var(--soft)", `b-${a.callsign}`); })}
        {plan && agreed && view !== "before" && land(plan).map((a) => {
          const g = byCs(agreed, a.callsign);
          const changed = g && (g.runway !== a.runway || g.slot_min !== a.slot_min || g.gate !== a.gate);
          return changed ? ghost(g, a, `agents: ${g!.runway} ${clock(wave.window_start, g!.slot_min ?? 0)}`, "var(--flight)", `a-${a.callsign}`) : null;
        })}

        {shownPlan.map((a) => {
          const slot = a.slot_min ?? 0, inb = a.metrics.in_block_min ?? slot;
          const ry = yRwy(a.runway ?? "");
          const onLane = runways.some((r) => r.id === a.runway);
          const gy = gatePos(a);
          const tn = tone(a.callsign);
          // orange only on what the duty manager changed: a gate move leaves the runway mark in its own colour
          const ag = byCs(agreed, a.callsign), plain: Tone = a.callsign === wave.focus ? "focus" : "flight";
          const rwTone: Tone = tn === "edited" && ag && ag.runway === a.runway && Math.abs((ag.slot_min ?? 0) - slot) < 0.5 ? plain : tn;
          const gtTone: Tone = tn === "edited" && ag && ag.gate === a.gate && Math.abs((ag.metrics.in_block_min ?? 0) - inb) < 0.5 ? plain : tn;
          const fill = TONE_FILL[rwTone], gfill = TONE_FILL[gtTone];
          const ring = involved?.has(a.callsign) && tn !== "alert" ? "var(--negative)" : undefined;
          const dragging = drag?.moved && drag.cs === a.callsign;
          const sel = selected === a.callsign;
          const pth = pathOf(a);
          // a track's hold is at its own times (shifted with a bar whose answer is pending); the metrics fallback already ends at the live slot
          const holds = pth.air?.hold ? holdSpans(a, pth.air).map((h) => ({ ...h, from: h.from + pth.shift, to: h.to + pth.shift })) : holdSpans(a);
          const waits = (pth.ground?.waits ?? []).map((w) => ({ from: w.from_min + pth.shift, to: w.to_min + pth.shift, reason: w.reason }));
          const taxiFrom = slot, taxiMin = pth.ground?.taxi_min ?? a.metrics.taxi_min ?? null;
          const near = (o: Assignment) => o !== a && o.runway === a.runway && Math.abs((o.slot_min ?? 0) - slot) < 7;
          // name to the right unless a neighbour sits there; then to the left; hidden if boxed in on both sides
          const os = (o: Assignment) => o.slot_min ?? 0;
          const after = shownPlan.some((o) => near(o) && (os(o) > slot || (os(o) === slot && o.callsign > a.callsign)));
          const before = shownPlan.some((o) => near(o) && (os(o) < slot || (os(o) === slot && o.callsign < a.callsign)));
          const name = icao(a.callsign);
          const tip = `${name} (${a.callsign}): runway ${a.runway} ${clock(wave.window_start, slot)}, gate ${a.gate} ${clock(wave.window_start, inb)}–${clock(wave.window_start, inb + turn)}${editable ? ". Drag to move; arrow keys move it by a minute or a lane." : ""}`;
          if (slot > END && !dragging) {
            // drawn in a layer above the bars (below), so a bar running to the edge can't hide it
            const rdy = onLane ? offDy(ry) : 0, gdy = gy > 0 ? offDy(gy) : 0;
            offAxis.push(
              <g key={a.callsign}>
                {onLane && ring && <circle cx={x(END) + 8} cy={ry + 14 + rdy} r={3.5} fill={ring} />}
                {onLane && <text x={x(END) + (ring ? 16 : 8)} y={ry + 18 + rdy} className="off-axis" style={{ fill: TONE_TEXT[rwTone] }}>{`${name} → ${clock(wave.window_start, slot)}`}</text>}
                {gy > 0 && ring && <circle cx={x(END) + 8} cy={gy + 14 + gdy} r={3.5} fill={ring} />}
                {gy > 0 && <text x={x(END) + (ring ? 16 : 8)} y={gy + 18 + gdy} className="off-axis" style={{ fill: TONE_TEXT[gtTone] }}>{`${name} → ${clock(wave.window_start, inb)}`}</text>}
                <title>{tip}</title>
              </g>);
            return null;
          }
          return (
            <g key={a.callsign} className={`bar-group${sel ? " sel" : ""}`}>
              {onLane && <g className={drag?.moved || settle ? "" : "wave"} style={{ transform: `translate(${x(slot)}px, ${ry}px)` }}
                tabIndex={editable ? 0 : -1} role={editable ? "button" : undefined} aria-label={tip}
                onPointerDown={(e) => startDrag(e, a, "land")} onKeyDown={(e) => onKey(e, a, "land")} onClick={(e) => e.stopPropagation()}
                filter={dragging ? "url(#lift)" : undefined}>
                {/* the holding pattern before the slot: one hatched segment per lap */}
                {holds.map((h, i) => (
                  <rect key={i} className="hold-lap" x={x(h.from) - x(slot) + 0.5} y={9} width={Math.max(1, x(h.to) - x(h.from) - 1)} height={BAR - 6} rx={1}
                    fill={`url(#hold-${rwTone})`} stroke={fill} strokeOpacity={pth.stale ? 0.3 : 0.6} opacity={pth.stale ? 0.5 : 1}><title>{`${name} ${h.title}`}</title></rect>
                ))}
                <rect className="hit" x={-8} y={0} width={20} height={LANE} fill="transparent" style={{ cursor: editable ? (dragging ? "grabbing" : "grab") : "pointer" }} />
                {(sel || dragging) && <rect x={-6} y={2} width={18} height={BAR + 8} rx={3} fill="none" stroke={fill} />}
                <rect x={-3} y={6} width={7} height={BAR} rx={1} fill={fill} stroke={ring} strokeWidth={ring ? 2 : undefined} />
                {!(after && before) && !dragging && <text x={after ? -8 : (sel ? 14 : 10)} y={18} textAnchor={after ? "end" : "start"} className="bar-name" style={{ fill: TONE_TEXT[rwTone] }}>{name}</text>}
              </g>}
              {gy > 0 && (
                <g className={drag?.moved || settle ? "" : "wave"} style={{ transform: `translate(${x(inb)}px, ${gy}px)` }}
                  tabIndex={editable ? 0 : -1} role={editable ? "button" : undefined} aria-label={`${name} at gate ${a.gate}. ${editable ? "Drag up or down, or use the arrow keys, to change the gate." : ""}`}
                  onPointerDown={(e) => startDrag(e, a, "gate")} onKeyDown={(e) => onKey(e, a, "gate")} onClick={(e) => e.stopPropagation()}>
                  {/* taxi: a thin segment from touchdown to in-block in front of the gate bar, any ground wait in amber */}
                  {inb > taxiFrom && (
                    <g className="taxi-seg" pointerEvents="none" opacity={pth.stale && dragging ? 0.5 : 1}>
                      <rect x={x(taxiFrom) - x(inb)} y={6 + BAR / 2 - 1.5} width={x(inb) - x(taxiFrom)} height={3} rx={1.5} fill={gfill} opacity={0.7}>
                        <title>{`${name} taxis ${clock(wave.window_start, taxiFrom)}–${clock(wave.window_start, inb)}${pth.ground?.exit ?? a.metrics.exit ? ` from ${pth.ground?.exit ?? a.metrics.exit}` : ""}${pth.ground?.route_names.length ? ` via ${pth.ground.route_names.join(" ")}` : ""}${taxiMin != null ? ` · ${taxiMin.toFixed(1)} min` : ""}`}</title>
                      </rect>
                      {waits.filter((w) => w.to > w.from).map((w, i) => (
                        <rect key={i} x={x(w.from) - x(inb)} y={6 + BAR / 2 - 3} width={Math.max(2, x(w.to) - x(w.from))} height={6} rx={1} fill="var(--warning)"><title>{`${name} waits ${((w.to - w.from) * 60).toFixed(0)} s · ${w.reason}`}</title></rect>
                      ))}
                    </g>
                  )}
                  <rect x={0} y={6} width={Math.max(6, x(inb + turn) - x(inb))} height={BAR} rx={2} fill={gfill} stroke={ring} strokeWidth={ring ? 2 : undefined} opacity={dragging && drag?.kind === "land" ? 0.55 : 1}
                    style={{ cursor: editable ? (dragging ? "grabbing" : "grab") : "pointer" }} />
                  {sel && <rect x={-2} y={4} width={Math.max(6, x(inb + turn) - x(inb)) + 4} height={BAR + 4} rx={3} fill="none" stroke={gfill} />}
                  <text className="bar-label" x={7} y={18} style={{ fill: TONE_INK[gtTone] }} pointerEvents="none">{name} {clock(wave.window_start, inb)}</text>
                </g>
              )}
            </g>
          );
        })}
        {offAxis}

        {/* drag feedback: guide, time pill, origin, quick check */}
        {drag?.moved && q && (() => {
          const a = land(plan).find((o) => o.callsign === drag.cs);
          if (!a) return null;
          const col = q.bad || q.block ? "var(--negative)" : "var(--human)";
          const gx = x(drag.slot);
          const ly = drag.kind === "land" ? yRwy(drag.lane) : gateY[drag.lane] ?? 0;
          // the note sits past the end of the dragged bar, or before its start when there is no room
          const la = live(a), lin = la.metrics.in_block_min ?? 0;
          const endX = drag.kind === "land" ? gx + 4 : x(lin + turn);
          const startX = drag.kind === "land" ? gx - 4 : x(lin);
          const msg = `${icao(drag.cs)} → ${drag.lane}${drag.kind === "land" ? ` ${clock(wave.window_start, drag.slot)}` : drag.slot !== drag.slot0 ? ` · lands ${clock(wave.window_start, drag.slot)}` : ""} · ${q.text.replace(/^lands \d\d:\d\d · /, "")}${previewFailed ? " · preview unavailable" : ""}`;
          const noteW = msg.length * 7.3 + 16;
          const right = endX + 8 + noteW < W - 4;
          return (
            <g pointerEvents="none">
              {(drag.kind === "land" || drag.slot !== drag.slot0) && <>
                <line x1={gx} x2={gx} y1={AXIS - 6} y2={H} stroke={col} strokeDasharray="2 4" />
                <rect x={gx - 26} y={2} width={52} height={20} rx={10} fill={col} />
                <text x={gx} y={16} textAnchor="middle" className="pill-text">{clock(wave.window_start, drag.slot)}</text>
              </>}
              {drag.kind === "land" && <>
                {runways.some((r) => r.id === drag.lane0) && <rect x={x(drag.slot0) - 3} y={yRwy(drag.lane0) + 6} width={7} height={BAR} rx={1} fill="none" stroke="var(--muted)" strokeDasharray="3 2" />}
                {q.gap && <path d={`M${x(q.gap.from)} ${ly + LANE - 1} v-4 M${x(q.gap.from)} ${ly + LANE - 3} H${x(q.gap.to)} M${x(q.gap.to)} ${ly + LANE - 1} v-4`} stroke={q.bad ? "var(--negative)" : "var(--fixed)"} fill="none" />}
              </>}
{(() => {
                const w = noteW, nx = right ? endX + 8 : Math.max(LABEL, startX - 8 - w);
                return <g>
                  <rect x={nx} y={ly + 4} width={w} height={20} rx={4} fill="var(--raised)" stroke={col} strokeOpacity=".5" />
                  <text x={nx + 8} y={ly + 18} className="drag-note" style={{ fill: col }}>{msg}</text>
                </g>;
              })()}
            </g>
          );
        })()}

        {rejection && rejY !== null && (
          <rect key={rejection.seq} className="flash" x={LABEL - 6} y={rejY + 1} width={W - LABEL + 4} height={LANE - 2} rx={4}
            fill="none" stroke="var(--negative)" strokeWidth={1.5} />
        )}

        {t != null && t >= START && t <= END && (
          <g pointerEvents="none" className="cursor">
            <line x1={x(t)} x2={x(t)} y1={AXIS - 6} y2={H} stroke="var(--text)" strokeOpacity=".55" />
            <path d={`M${x(t) - 5} ${AXIS - 12} h10 l-5 6z`} fill="var(--text)" />
          </g>
        )}
      </svg>
      {/* the time axis stays on screen when the rows scroll under it, with its own scrubber (the drawing's is under it) */}
      <svg className="gantt gantt-axis" viewBox={`0 0 ${W} ${AXIS}`} width={cw} height={AXIS * k} aria-hidden="true"
        onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={() => setScrub(false)}>
        <rect width={W} height={AXIS} fill="var(--surface)" />
        <text className="group" x={0} y={AXIS - 6}>Runways</text>
        {ticks.map((m) => (
          <g key={m}>
            <line x1={x(m)} x2={x(m)} y1={AXIS - 6} y2={AXIS} stroke={m === 0 || m === 60 ? "var(--control)" : "var(--gridline)"} />
            {(tickPx >= 60 || (m !== START && m !== END)) && (tickPx >= 44 || ((m - START) / 15) % 2 === 0) && (
              <text className="tick" x={x(m)} y={16} textAnchor={m === START ? "start" : m === END ? "end" : "middle"}>{clock(wave.window_start, m)}</text>
            )}
          </g>
        ))}
        {t != null && t >= START && t <= END && <path d={`M${x(t) - 5} ${AXIS - 12} h10 l-5 6z`} fill="var(--text)" />}
        {scrubber}
      </svg>
    </div>
  );
}

const Mark = ({ children, w = 18 }: { children: React.ReactNode; w?: number }) => <svg width={w} height={12} aria-hidden="true">{children}</svg>;
export function GanttLegend({ view, focus, edited, simulated, negotiated = true, holds = false, taxi = false }: { view: View; focus?: string; edited: boolean; simulated?: boolean; negotiated?: boolean; holds?: boolean; taxi?: boolean }) {
  return (
    <div className="legend">
      {focus && <span><Mark><rect y={2} width={18} height={8} rx={1.5} fill="var(--hue)" /></Mark>Disrupted <span className="code">{icao(focus)}</span></span>}
      <span><Mark><rect y={2} width={18} height={8} rx={1.5} fill="var(--flight)" /></Mark>{view === "before" || !negotiated ? "Status quo" : "Agents' plan"}</span>
      {edited && view !== "before" && <span><Mark><rect y={2} width={18} height={8} rx={1.5} fill="var(--human)" /></Mark>Your edit</span>}
      {view === "compare" && <span><Mark><rect x={1} y={2} width={16} height={8} rx={1.5} fill="none" stroke="var(--soft)" strokeDasharray="3 2" /></Mark>Before</span>}
      {edited && view === "after" && <span><Mark><rect x={1} y={2} width={16} height={8} rx={1.5} fill="none" stroke="var(--flight)" strokeDasharray="3 2" /></Mark>Agents' plan, before your edit</span>}
      {holds && <span><Mark><rect x={0.5} y={3} width={8} height={6} rx={1} fill="url(#lg-hold)" stroke="var(--flight)" strokeOpacity=".6" /><rect x={9.5} y={3} width={8} height={6} rx={1} fill="url(#lg-hold)" stroke="var(--flight)" strokeOpacity=".6" />
        <defs><pattern id="lg-hold" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="4" stroke="var(--flight)" strokeWidth="1.4" /></pattern></defs></Mark>Holding, a segment per lap</span>}
      {taxi && <span><Mark><rect y={4.5} width={18} height={3} rx={1.5} fill="var(--flight)" opacity={0.7} /></Mark>Taxi</span>}
      <span><Mark w={6}><rect x={2} y={1} width={2.5} height={10} rx={1} fill="var(--fixed)" /></Mark>Other traffic</span>
      <span><Mark><rect y={2} width={18} height={8} rx={1.5} fill="var(--busy)" /></Mark>Gate held{simulated ? " (simulated)" : ""}</span>
    </div>
  );
}
