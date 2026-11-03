import type { Assignment, JointPlan, TrajectorySet, WaveScenario } from "../api/types";
import { icao } from "../callsign";
import { clock } from "../format";
import type { Tone } from "./Radar";

// Flight progress strips, as in a tower or approach position: one strip per arrival, racked by where it is at
// the replay clock (on final, sequenced, later, down). Each strip carries what the controller reads at a glance:
// callsign, type and origin; runway and landing time; gate; and on the second line what changed and who changed it.
// A third line carries the path when the engine has one: the entry fix and time before the target is on the
// scope, the hold (fix, laps, level), and the route and vector time the runway choice adds.
const FINAL_MIN = 10;              // ~30 NM out at 180 kt: established on the approach
const SEQ_MIN = 45;                // inside the wave: already given a runway and a time

interface Props {
  wave: WaveScenario;
  plan: JointPlan | null;          // the plan on screen (Before, or After with the duty manager's edits)
  agreed: JointPlan | null;        // what the agents agreed, before any edit
  baseline: JointPlan | null;      // the status quo: first come, first served
  edited: (cs: string) => boolean;
  t: number;
  tone: (cs: string) => Tone;
  selected: string | null;
  onSelect: (cs: string | null) => void;
  paths?: TrajectorySet | null;    // the engine's trajectories for `plan`, when it has them
}

type Strip = { cs: string; rem: number; tone: Tone; type: string; from: string; rw: string; time: string; gate: string; note: string; who: "you" | "agents" | null; path: string };

const tPlus = (m: number) => `T${m < 0 ? "−" : "+"}${Math.abs(Math.round(m))}`;
const signedMin = (m: number) => `${m < 0 ? "−" : "+"}${Math.abs(m).toFixed(1)}′`;

/** The strip's path line: "inbound, entry BFORD T+12" before the target appears, "HLD BENKY 2L 120" for a
 *  flight that holds, and the route time its runway adds over the nominal one ("rte +2.1′") plus any vector
 *  stretch ("vec +1.5′"). Empty when there is nothing to say. */
export function pathLine(a: Assignment | undefined, air: TrajectorySet["air"][number] | undefined, t: number): string {
  const parts: string[] = [];
  if (air && air.points.length && t < air.appears_min) {
    const fix = air.points.find((p) => p.fix)?.fix;
    parts.push(`inbound${fix ? `, entry ${fix}` : ""} ${tPlus(air.appears_min)}`);
  }
  const m = a?.metrics;
  const hold = air?.hold && air.hold.laps >= 1 ? { fix: air.hold.fix, laps: air.hold.laps, level: air.hold.level_ft }
    : m?.hold_laps && m.hold_laps >= 1 && m.hold_fix ? { fix: m.hold_fix, laps: m.hold_laps, level: m.hold_level_ft ?? null } : null;
  if (hold) parts.push(`HLD ${hold.fix} ${hold.laps}L${hold.level != null ? ` ${String(Math.round(hold.level / 100)).padStart(3, "0")}` : ""}`);
  if (m?.route_extra_min != null && Math.abs(m.route_extra_min) >= 0.1) parts.push(`rte ${signedMin(m.route_extra_min)}`);
  if (m?.vector_min != null && m.vector_min >= 0.1) parts.push(`vec ${signedMin(m.vector_min)}`);
  return parts.join(" · ");
}

export function Strips({ wave, plan, agreed, baseline, edited, t, tone, selected, onSelect, paths = null }: Props) {
  const airBy = new Map((paths?.air ?? []).map((x) => [x.callsign, x]));
  const at = (p: JointPlan | null, cs: string): Assignment | undefined => p?.assignments.find((a) => a.callsign === cs);
  const hhmm = (m: number) => clock(wave.window_start, m);

  const strips: Strip[] = wave.flights.map((f) => {
    const a = at(plan, f.callsign);
    const base = { cs: f.callsign, tone: tone(f.callsign), type: f.aircraft_type ?? "", from: f.origin };
    if (!a) return { ...base, rem: f.sched_min - t, rw: "", time: hhmm(f.sched_min), gate: f.scheduled_gate, note: "no runway yet", who: null, path: "" };
    if (a.action === "divert") return { ...base, rem: Infinity, rw: "DIV", time: "", gate: "", note: `diverts to ${f.fuel.alternate}`, who: null, path: "" };
    const slot = a.slot_min ?? f.sched_min;
    // what changed, against the plan it came from: the duty manager's edit against the agents', the agents' against the status quo
    const ref = edited(f.callsign) ? at(agreed, f.callsign) : at(baseline, f.callsign);
    const moved = ref && ref.action === "land" && (ref.runway !== a.runway || ref.slot_min !== a.slot_min || ref.gate !== a.gate);
    // one change fits the strip: the runway if it moved, else the time, else the gate (the full story is on the timeline)
    const was = !moved ? "" : ref.runway !== a.runway ? `${ref.runway}` : ref.slot_min !== a.slot_min ? hhmm(ref.slot_min ?? 0) : `gate ${ref.gate}`;
    const note = moved ? `was ${was}` : a.metrics.hold_min >= 1 ? `holds ${Math.round(a.metrics.hold_min)} min` : `due ${hhmm(f.sched_min)}`;
    return { ...base, rem: slot - t, rw: a.runway ?? "", time: hhmm(slot), gate: a.gate === "RS" ? "RS" : a.gate ?? "", note, who: moved ? (edited(f.callsign) ? "you" : "agents") : null, path: pathLine(a, airBy.get(f.callsign), t) };
  });
  const fixed = wave.queue.filter((q) => q.slot_min - t >= 0 && q.slot_min - t <= SEQ_MIN).sort((p, q) => p.slot_min - q.slot_min);

  const bays: [string, Strip[]][] = [
    ["On final", strips.filter((s) => s.rem >= 0 && s.rem <= FINAL_MIN)],
    ["Sequenced", strips.filter((s) => s.rem > FINAL_MIN && s.rem <= SEQ_MIN)],
    ["Later", strips.filter((s) => s.rem > SEQ_MIN)],
    ["Landed", strips.filter((s) => s.rem < 0)],
  ];

  return (
    <section className="strips" aria-label="Flight strips">
      <div className="strips-head"><h2>Flight strips</h2><span className="meta">{wave.flights.length} in the plan</span></div>
      <div className="strips-body">
        {bays.map(([name, list]) => list.length > 0 && (
          <div className="bay" key={name}>
            <h3>{name}<span>{list.length}</span></h3>
            {list.sort((p, q) => p.rem - q.rem).map((s) => (
              <button key={s.cs} className={`strip ${s.tone}${selected === s.cs ? " sel" : ""}${name === "Landed" ? " down" : ""}`}
                aria-pressed={selected === s.cs} onClick={() => onSelect(selected === s.cs ? null : s.cs)}
                title={`${icao(s.cs)} (${s.cs}) · ${s.type ? `${s.type} ` : ""}from ${s.from}${s.who ? ` · ${s.note}, changed by ${s.who === "you" ? "you" : "the agents"}` : ""}`}>
                <span className="s-id"><b>{icao(s.cs)}</b><small>{[s.type, s.from].filter(Boolean).join(" · ")}</small></span>
                <span className="s-slot"><b>{s.rw} <i>{s.time}</i></b><small className={s.who ?? ""}>{s.note}</small></span>
                <span className="s-gate">{s.gate}</span>
                {s.path && <small className="s-path">{s.path}</small>}
              </button>
            ))}
          </div>
        ))}
        {fixed.length > 0 && (
          <div className="bay">
            <h3>Other traffic<span>{fixed.length}</span></h3>
            {fixed.slice(0, 6).map((q) => (
              <div key={q.callsign} className="strip fixed" title={`${icao(q.callsign)} · fixed traffic, not part of the negotiation`}>
                <span className="s-id"><b>{icao(q.callsign)}</b><small>{q.aircraft_type ?? ""}</small></span>
                <span className="s-slot"><b>{q.runway} <i>{hhmm(q.slot_min)}</i></b><small>fixed</small></span>
                <span className="s-gate" />
              </div>
            ))}
            {fixed.length > 6 && <p className="more">+{fixed.length - 6} more in the next {SEQ_MIN} min</p>}
          </div>
        )}
      </div>
    </section>
  );
}
