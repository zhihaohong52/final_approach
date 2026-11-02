import { useEffect, useRef, useState } from "react";
import type { Assignment, JointPlan, Runway, WaveScenario } from "../api/types";
import { icao } from "../callsign";
import { clock } from "../format";

// An approach-radar picture of the plan at one moment. The scope is centred on the airport reference point,
// runways are drawn to scale from their end coordinates, range rings are true nautical miles, and each
// arrival sits on the extended centreline of the runway end it lands on, as far out as it still has to fly
// at a typical final-approach groundspeed. Positions are replayed from the plan, not from surveillance data.
const GS_NM_PER_MIN = 3;          // ~180 kt over the last 30 NM
const RANGES = [10, 20, 40];           // NM from centre to the short edge, as on a scope range knob
const LEAD = 22;                   // leader line length, px
export const SCAN_MIN = 4.8 / 60;  // one radar update, minutes: a terminal antenna turns at 12.5 rpm
const TRAIL = 5;                   // history dots: where the target was on the last five scans
const AHEAD_MIN = 1;               // predicted track line: where it will be in a minute

export type Tone = "focus" | "flight" | "edited" | "alert" | "fixed";
type End = Runway & { le_lat: number; le_lon: number; he_lat: number; he_lon: number };
const hasGeo = (r: Runway): r is End => [r.le_lat, r.le_lon, r.he_lat, r.he_lon].every((v) => typeof v === "number");

interface Props {
  wave: WaveScenario;
  plan: JointPlan | null;
  t: number;                                   // minutes vs T0
  tone: (cs: string) => Tone;
  selected: string | null;
  onSelect: (cs: string | null) => void;
}

interface Blip { cs: string; x: number; y: number; tone: Tone; l1: string; l2: string; out: number | null; title: string; trail: [number, number][]; ahead: [number, number] }

export function Radar({ wave, plan, t, tone, selected, onSelect }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [[W, boxH], setSize] = useState([960, 0]);
  const [range, setRange] = useState(10);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize([Math.round(e.contentRect.width), Math.round(e.contentRect.height)]));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const wide = W >= 700;
  const H = wide ? Math.max(460, boxH) : Math.round(Math.min(560, Math.max(360, W * 1.05)));

  const ends = wave.airport.runways.filter(hasGeo);
  if (!ends.length) return <div ref={box} className="radar empty-radar"><p className="empty">No runway coordinates for {wave.airport.iata}, so the scope can't be drawn.</p></div>;

  // local flat projection in nautical miles around the airport reference point
  const lat0 = wave.airport.lat ?? ends[0].le_lat, lon0 = wave.airport.lon ?? ends[0].le_lon;
  const k = Math.cos((lat0 * Math.PI) / 180);
  const nm = (lat: number, lon: number): [number, number] => [(lon - lon0) * 60 * k, -(lat - lat0) * 60];
  const cx = W / 2, cy = H / 2;
  const S = (Math.min(W, H) / 2 - 18) / range;         // px per NM: the chosen range fits the short side
  const step = range / 5;
  const rings = Array.from({ length: Math.ceil(Math.hypot(W, H) / 2 / S / step) }, (_, i) => (i + 1) * step);
  const P = (p: [number, number]): [number, number] => [cx + p[0] * S, cy + p[1] * S];
  // visible extent in NM from the centre; the top band is kept clear for the clock and range controls
  const keyed = window.innerWidth > 760;                 // styles.css shows the colour key and overlays the header above 760px
  const TOP = keyed ? 64 : 12, BOTTOM = keyed ? 60 : 30;
  const lim = { l: -(cx - 16) / S, r: (W - cx - 16) / S, t: -(cy - TOP) / S, b: (H - cy - BOTTOM) / S };

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

  const blips: Blip[] = [];
  const awaiting = plan ? [] : [...wave.flights].sort((p, q) => Number(q.callsign === wave.focus) - Number(p.callsign === wave.focus) || p.sched_min - q.sched_min);
  const beyond: { cs: string; rw: string; rem: number; tone: Tone }[] = [];   // inbound but past the scope edge
  const add = (cs: string, rw: string, slot: number, tn: Tone, l2: string, title: string) => {
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
  for (const q of wave.queue) add(q.callsign, q.runway, q.slot_min, "fixed", `${q.runway} ${clock(wave.window_start, q.slot_min)}`, `${icao(q.callsign)} · fixed traffic · ${q.aircraft_type ?? ""} ${q.wake} · runway ${q.runway} ${clock(wave.window_start, q.slot_min)}`);
  for (const a of land) {
    const tn = tone(a.callsign);
    add(a.callsign, a.runway!, a.slot_min ?? 0, tn, `${a.runway} ${a.gate === "RS" ? "RS" : a.gate} ${clock(wave.window_start, a.slot_min ?? 0)}`,
      `${icao(a.callsign)} (${a.callsign}) · runway ${a.runway} at ${clock(wave.window_start, a.slot_min ?? 0)} · gate ${a.gate}${a.metrics.hold_min ? ` · holds ${a.metrics.hold_min} min` : ""}`);
  }
  // draw the negotiated flights last, so they sit on top of fixed traffic
  blips.sort((a, b) => (a.tone === "fixed" ? 0 : 1) - (b.tone === "fixed" ? 0 : 1));

  // data blocks: try four corners, keep the one that collides least with blocks already placed
  const placed: [number, number, number, number][] = [];
  const blocks = blips.map((b) => {
    const w = Math.max(b.l1.length, b.l2.length) * 7.4 + 6, h = b.tone === "fixed" ? 16 : 30;
    const opts: [number, number][] = [[1, -1], [-1, -1], [1, 1], [-1, 1]];
    let best = opts[0], score = Infinity;
    for (const [sx, sy] of opts) {
      const lx = b.x + sx * LEAD * 0.7, ly = b.y + sy * LEAD * 0.7;
      const r: [number, number, number, number] = [sx > 0 ? lx + 2 : lx - w - 2, sy < 0 ? ly - h : ly, w, h];
      const off = r[0] < 2 || r[0] + r[2] > W - 2 || r[1] < TOP - 8 || r[1] + r[3] > H - BOTTOM + 6 ? 1000 : 0;
      const hit = placed.reduce((s, p) => s + Math.max(0, Math.min(r[0] + r[2], p[0] + p[2]) - Math.max(r[0], p[0])) * Math.max(0, Math.min(r[1] + r[3], p[1] + p[3]) - Math.max(r[1], p[1])), 0);
      if (hit + off < score) { score = hit + off; best = [sx, sy]; }
    }
    const lx = b.x + best[0] * LEAD * 0.7, ly = b.y + best[1] * LEAD * 0.7;
    const r: [number, number, number, number] = [best[0] > 0 ? lx + 2 : lx - w - 2, best[1] < 0 ? ly - h : ly, w, h];
    placed.push(r);
    return { b, lx, ly, r };
  });

  const m = wave.metar, calm = !m || !m.wind_kt, dir = m?.wind_dir_deg;
  const wr = dir != null ? ((dir - 90) * Math.PI) / 180 : 0;
  // ring numbers go on the diagonal furthest from every approach in use, so they never sit under traffic
  const approachDirs = [...usedEnds].map((id) => approach(id, 1)?.u).filter(Boolean) as [number, number][];
  const diag = ([[1, -1], [-1, -1], [1, 1], [-1, 1]] as [number, number][]).map(([dx, dy]) => [dx * 0.707, dy * 0.707] as [number, number])
    .sort((p, q) => Math.max(...approachDirs.map((u) => p[0] * u[0] + p[1] * u[1]), -1) - Math.max(...approachDirs.map((u) => q[0] * u[0] + q[1] * u[1]), -1))[0];
  const ringLabel = (r: number) => { const x = cx + r * S * diag[0], y = cy + r * S * diag[1]; const underWind = x > W - 140 && y > H - 116;   // the wind compass box, bottom right
    return x > 24 && x < W - 24 && y > TOP && y < H - BOTTOM && !underWind; };

  return (
    <div ref={box} className={`radar${wide ? " fill" : ""}`}>
      <div className="range seg" role="group" aria-label="Scope range">
        {RANGES.map((r) => <button key={r} aria-pressed={range === r} onClick={() => setRange(r)}>{r} NM</button>)}
      </div>
      {(beyond.length > 0 || awaiting.length > 0) && (
        <ul className="beyond" aria-label={`Inbound flights not drawn on the ${range} NM scope`}>
          {/* before a plan exists the negotiated flights have no runway, so they are listed, not drawn */}
          {awaiting.map((f) => (
            <li key={f.callsign} className={`blip ${tone(f.callsign)}`}><button onClick={() => onSelect(f.callsign)}>{icao(f.callsign)}</button> due {clock(wave.window_start, f.sched_min)} · no runway yet</li>
          ))}
          {beyond.sort((p, q) => p.rem - q.rem).slice(0, 5).map((b) => (
            <li key={b.cs} className={`blip ${b.tone}`}><button onClick={() => b.tone !== "fixed" && onSelect(b.cs)}>{icao(b.cs)}</button> {b.rw} in {b.rem}′</li>
          ))}
          {beyond.length > 5 && <li className="more">+{beyond.length - 5} more</li>}
        </ul>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" onClick={() => onSelect(null)}
        aria-label={`${wave.airport.iata} approach picture at ${clock(wave.window_start, t)}: ${blips.length} arrivals inbound`}>
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

        {/* extended centrelines of the runway ends in use, out to the scope edge */}
        {[...usedEnds].map((id) => {
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

        <g transform={`translate(${W - 74} ${H - 76})`}>
          <circle r={22} fill="none" stroke="#2a2d33" />
          {calm ? <text className="ring" textAnchor="middle" y={4}>calm</text>
            : dir == null ? <text className="ring" textAnchor="middle" y={4}>VRB</text>
            : <line x1={Math.cos(wr) * 18} y1={Math.sin(wr) * 18} x2={-Math.cos(wr) * 16} y2={-Math.sin(wr) * 16} stroke="var(--soft)" strokeWidth={1.8} markerEnd="url(#wind-tip)" />}
          <text className="wind" textAnchor="middle" y={42}>{calm ? "wind calm" : `${dir != null ? String(Math.round(dir)).padStart(3, "0") : "VRB"}° ${m!.wind_kt}${m!.gust_kt ? `G${m!.gust_kt}` : ""} kt`}</text>
        </g>
        <text className="scope-note" x={14} y={H - 12}>{wave.airport.icao} · rings {step} NM{wide ? ` · replayed from the plan at ${GS_NM_PER_MIN * 60} kt · updates every ${SCAN_MIN * 60} s` : ` · ${GS_NM_PER_MIN * 60} kt · ${SCAN_MIN * 60} s scan`}</text>
      </svg>
    </div>
  );
}

export const assignmentAt = (p: JointPlan | null, cs: string): Assignment | undefined => p?.assignments.find((a) => a.callsign === cs);
