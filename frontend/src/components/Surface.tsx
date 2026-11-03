import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { TrajectorySet, WaveGeo } from "../api/types";
import { icao } from "../callsign";
import { endOfLine, bearing, positionAt, projector, secondFloor } from "../geo/track";
import type { Tone } from "./Radar";

// The airport surface, as on a ground-movement display: runways, taxiways and taxilanes, stands and runway
// holding positions drawn to scale from OpenStreetMap (WaveGeo.surface), and each aircraft on the ground at its
// place on the engine's taxi track at the replay clock. The picture refreshes once a second, like surface
// movement radar (an assumption, see spec §9). Taxi tracks are the engine's own routes; a synthesized one
// (no usable OSM graph for that runway end) is dashed. Positions are replayed from the plan, not surveillance.
const ZOOMS = [1, 2, 4];
const TAXIWAY_M = 23;              // drawn width of a taxiway (ICAO code E/F), metres
const TAXILANE_M = 12;             // apron taxilanes are narrower
const RUNWAY_M = 45;               // when OSM has no width
const HOLD_BAR_M = 40;             // a runway holding position is drawn as a bar across its taxiway
const SNAP_M = 60;                 // a holding position takes its orientation from a taxiway within this distance
const LEAD = 18;                   // leader line, px
const PAD = 22;
const TOP = 84, BOTTOM = 64;       // bands kept clear of the field at the fitted zoom

type XY = [number, number];
type Surf = NonNullable<WaveGeo["surface"]>;
type Ground = TrajectorySet["ground"][number];

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
}

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

/** Screen position of a point in metres: the view is the scale (px per metre) and the centre (metres). */
const screen = (W: number, H: number, S: number, cx: number, cy: number) => (q: XY): XY =>
  [W / 2 + (q[0] - cx) * S, TOP + (H - TOP - BOTTOM) / 2 - (q[1] - cy) * S];
const polyline = (P: (q: XY) => XY, xy: XY[]) => xy.map((q) => P(q).map((v) => v.toFixed(1)).join(",")).join(" ");

/** The airfield itself: taxiways, runways, holding positions and stands. It depends only on the surface and the
 *  view, so the once-a-second clock re-renders just the aircraft and their routes. */
const Airfield = memo(function Airfield({ g, W, H, S, cx, cy }: { g: Prepared; W: number; H: number; S: number; cx: number; cy: number }) {
  const P = screen(W, H, S, cx, cy);
  const tw = (m: number, min: number) => Math.max(min, m * S);
  const inView = ([x, y]: XY) => x > -40 && x < W + 40 && y > -40 && y < H + 40;
  // runway end labels: the designator at the end it is landed from
  const ends = g.runways.flatMap((r) => {
    if (r.xy.length < 2) return [];
    const first = r.xy[0], last = r.xy[r.xy.length - 1];
    return r.ids.map((id) => {
      const at = endOfLine(id, first, last) === 0 ? first : last, other = at === first ? last : first;
      const L = Math.hypot(at[0] - other[0], at[1] - other[1]) || 1;
      return { id, xy: [at[0] + ((at[0] - other[0]) / L) * 14 / S, at[1] + ((at[1] - other[1]) / L) * 14 / S] as XY };
    });
  });
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
        {ends.filter((e) => inView(P(e.xy))).map((e) => { const [x, y] = P(e.xy); return <text key={e.id} className="rw-end" x={x} y={y + 4} textAnchor="middle">{e.id}</text>; })}
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

function SurfaceMap({ geo, traj, t, tone, selected, onSelect, focus = null, updating = false }: Props) {
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
    const p = g.pj(now.lat, now.lon);
    // heading: along the segment it is on; at the last point, along the one before
    const i = Math.min(now.seg, x.points.length - 2);
    const a = x.points[Math.max(0, i)], b = x.points[Math.max(0, i) + 1] ?? a;
    const hdg = bearing(g.pj(a.lat, a.lon), g.pj(b.lat, b.lon));
    acs.push({ x, p, hdg, seg: now.seg, tone: toneOf(x) });
  }

  // ---------- view: fit the field, or zoom on the selected (else the disrupted) aircraft ----------
  const bw = Math.max(200, g.box.x1 - g.box.x0), bh = Math.max(200, g.box.y1 - g.box.y0);
  // the field fits between the top band (clock, toggles, zoom) and the bottom band (key, scale, notes)
  const S = Math.min((W - 2 * PAD) / bw, (H - TOP - BOTTOM) / bh) * zoom;     // px per metre
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
    const off = cur && cur.who === who ? Math.max(Math.abs(target[0] - cur.xy[0]) * S / (W / 2), Math.abs(target[1] - cur.xy[1]) * S / ((H - TOP - BOTTOM) / 2)) : Infinity;
    if (off > 0.6) centre.current = { who, xy: target };
  }
  const c: XY = centre.current?.xy ?? [(g.box.x0 + g.box.x1) / 2, (g.box.y0 + g.box.y1) / 2];
  const P = screen(W, H, S, c[0], c[1]);
  const line = (xy: XY[]) => polyline(P, xy);

  // ---------- what the tracks on screen use: stands and taxiway names get labels, the rest stay quiet ----------
  const shownTracks = tracks.filter((x) => x.wave && x.kind === "arrival");
  const standUsed = new Map<string, Tone>();
  for (const x of shownTracks) standUsed.set(x.stand, toneOf(x));
  const usedStands = g.stands.flatMap((st) => { const tn = standUsed.get(st.id) ?? (st.gate ? standUsed.get(st.gate) : undefined); return tn ? [{ st, tn }] : []; });
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
  const anySynth = routes.some((r) => r.dashed);

  // a scale bar: the largest of 100/250/500/1000 m that fits in ~120 px
  const bar = [1000, 500, 250, 100].find((mm) => mm * S <= 130) ?? 100;
  const nOnGround = acs.filter((a) => a.tone !== "fixed").length;
  const sr = Math.max(1.5, Math.min(4, 9 * S));

  return (
    <div ref={box} className="surface-map">
      <div className="range seg" role="group" aria-label="Surface zoom">
        {ZOOMS.map((z) => <button key={z} aria-pressed={zoom === z} onClick={() => setZoom(z)} title={z === 1 ? "The whole field" : `×${z}, centred on ${who ? icao(who) : "the field"}`}>{z === 1 ? "Field" : `×${z}`}</button>)}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" onClick={() => onSelect(null)}
        aria-label={`Airport surface: ${nOnGround} negotiated aircraft taxiing`}>
        <rect width={W} height={H} fill="var(--scope)" />
        <Airfield g={g} W={W} H={H} S={S} cx={c[0]} cy={c[1]} />
        {/* the stands the plan's taxiing aircraft are bound for */}
        <g className="sf-stand">
          {usedStands.map(({ st, tn }) => {
            const [x, y] = P(st.xy);
            return (
              <g key={st.id} className={`used blip ${tn}`}>
                <title>{`Stand ${st.gate ?? st.id}${st.geo_src ? ` · ${st.geo_src}` : ""}`}</title>
                <rect x={x - sr} y={y - sr} width={2 * sr} height={2 * sr} />
                <text className="stand-label" x={x + sr + 3} y={y + 4}>{st.gate ?? st.id}</text>
              </g>
            );
          })}
        </g>
        {twLabels.map((l) => { const [x, y] = P(l.at); return <text key={l.name} className="tw-label" x={x} y={y - 5} textAnchor="middle">{l.name}</text>; })}

        <g className="sf-routes">
          {routes.map((r) => <polyline key={r.cs} className={`path blip ${r.tone}${selected === r.cs ? " sel" : ""}`} points={line(r.xy)} strokeDasharray={r.dashed ? "5 4" : undefined} />)}
        </g>

        {/* fixed traffic and departures first (grey, no data block), negotiated aircraft on top */}
        {[...acs].sort((a, b) => Number(a.tone !== "fixed") - Number(b.tone !== "fixed")).map((a) => {
          const [x, y] = P(a.p);
          const cs = a.x.callsign, fixed = a.tone === "fixed", sel = selected === cs;
          const sc = Math.max(0.8, Math.min(1.6, 40 * S / 10));           // a ~40 m airframe, never smaller than a glyph
          const l1 = icao(cs), l2 = `${a.x.exit ? `${a.x.exit} ` : ""}→ ${a.x.stand}`;
          const flip = x > W - 120;
          const lx = x + (flip ? -LEAD : LEAD), ly = y - LEAD;
          return (
            <g key={cs} className={`gnd blip ${a.tone}${sel ? " sel" : ""}`} onClick={(e) => { e.stopPropagation(); if (!fixed) onSelect(cs); }} style={{ cursor: fixed ? "default" : "pointer" }}>
              <title>{`${l1}${fixed ? ` · ${a.x.kind === "departure" ? "departure" : "other traffic"}` : ""} · ${a.x.kind === "arrival" ? `from ${a.x.exit ?? "the runway"} to ${a.x.stand}` : `from ${a.x.stand}`} via ${a.x.route_names.join(" ") || "—"} · ${a.x.taxi_min.toFixed(1)} min taxi (${a.x.unimpeded_min.toFixed(1)} unimpeded) · ${a.x.src === "osm" ? "OSM route" : "synthesized, straight line"}`}</title>
              {sel && <circle cx={x} cy={y} r={10} fill="none" stroke="currentColor" strokeOpacity=".6" />}
              <path transform={`translate(${x} ${y}) rotate(${a.hdg}) scale(${sc})`} d="M0 -6L1.2 -1.5L6 1.5L6 2.6L1.2 1.6L0.9 4.6L2.4 5.8L2.4 6.6L0 6L-2.4 6.6L-2.4 5.8L-0.9 4.6L-1.2 1.6L-6 2.6L-6 1.5L-1.2 -1.5Z"
                fill={fixed ? "currentColor" : a.tone === "focus" ? "currentColor" : "var(--scope)"} stroke="currentColor" strokeWidth={fixed ? 0 : 1.2 / sc} />
              {!fixed && <>
                <line x1={x} y1={y} x2={lx} y2={ly} stroke="currentColor" strokeOpacity=".8" />
                <text className="db1" x={lx + (flip ? -2 : 2)} y={ly - 2} textAnchor={flip ? "end" : "start"}>{l1}</text>
                <text className="db2" x={lx + (flip ? -2 : 2)} y={ly + 12} textAnchor={flip ? "end" : "start"}>{l2}</text>
              </>}
            </g>
          );
        })}

        {/* scrims keep the controls, key and notes legible over the map when zoomed in */}
        <defs>
          <linearGradient id="sf-top" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--scope)" stopOpacity=".92" /><stop offset="1" stopColor="var(--scope)" stopOpacity="0" /></linearGradient>
          <linearGradient id="sf-bot" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stopColor="var(--scope)" stopOpacity=".92" /><stop offset="1" stopColor="var(--scope)" stopOpacity="0" /></linearGradient>
        </defs>
        <rect width={W} height={84} fill="url(#sf-top)" pointerEvents="none" />
        <rect y={H - 72} width={W} height={72} fill="url(#sf-bot)" pointerEvents="none" />
        <text className="ring" x={W / 2} y={14} textAnchor="middle">N</text>
        <g className="sf-scale" transform={`translate(${W - 16 - bar * S} ${H - 46})`}>
          <path d={`M0 -4V0H${bar * S}V-4`} />
          <text x={bar * S / 2} y={-7} textAnchor="middle">{bar >= 1000 ? `${bar / 1000} km` : `${bar} m`}</text>
        </g>
        {anySynth && (
          <g className="legend-synth" transform={`translate(${W - 16} 64)`}>
            <line x1={-232} y1={-4} x2={-206} y2={-4} strokeDasharray="5 4" />
            <text x={0} y={0} textAnchor="end">dashed = synthesized taxi route</text>
          </g>
        )}
        {/* notes bottom right: the colour key sits bottom left across the whole panel */}
        <text className="scope-note" x={W - 16} y={H - 26} textAnchor="end">{traj ? `the engine's taxi routes · updates every 1 s${updating ? " · updating…" : ""}` : "no taxi routes for this plan"}</text>
        <text className="scope-note attr" x={W - 16} y={H - 12} textAnchor="end">{attribution} · not for navigation</text>
      </svg>
    </div>
  );
}
