import type { JointPlan, NegotiationResult, PlanEvaluation, RuleId, WaveScenario } from "../api/types";
import { icao, icaoText } from "../callsign";

// Two levels, as on a controller's display: red is a safety problem that must be fixed before the plan can be
// approved (a rule the plan breaks); amber is advice the duty manager should see and acknowledge.
export interface Alert { id: string; level: "red" | "amber"; callsign?: string; title: string; detail: string }

export const RULE_NAME: Record<RuleId, string> = {
  "ATC-RWY-OPEN": "Runway closed", "ATC-WAKE-SEP": "Wake separation", "ATC-TAXI-ROUTE": "Taxi route",
  "ATC-WIND-LIMITS": "Wind limits", "ATC-RWY-CAPACITY": "Runway capacity", "PLT-EARLIEST-ETA": "Earlier than it can arrive",
  "PLT-FUEL-RESERVE": "Fuel reserve", "PLT-CREW-DUTY": "Flight crew out of hours", "GND-GATE-SIZE": "Gate too small", "GND-GATE-FREE": "Gate occupied",
  "GND-GATE-NOTICE": "Gate change too late", "GND-CREW-DUTY": "Ground crew overtime",
};

export function deriveAlerts(wave: WaveScenario, r: NegotiationResult | null, plan: JointPlan | null, ev: PlanEvaluation | null): Alert[] {
  const out: Alert[] = [];
  for (const c of ev?.checks ?? []) {
    if (c.passed) continue;
    out.push({ id: `rule:${c.rule}:${c.callsign ?? ""}`, level: "red", callsign: c.callsign ?? undefined,
      title: `${RULE_NAME[c.rule] ?? c.rule}${c.callsign ? ` · ${icao(c.callsign)}` : ""}`, detail: icaoText(c.detail) });
  }
  for (const a of plan?.assignments ?? []) {
    const f = wave.flights.find((x) => x.callsign === a.callsign);
    if (a.action === "divert") {
      out.push({ id: `divert:${a.callsign}`, level: "amber", callsign: a.callsign, title: `Diversion · ${icao(a.callsign)}`, detail: `Diverts to ${f?.fuel.alternate ?? "its alternate"}.` });
      continue;
    }
    if (f && a.metrics.hold_min > 0) {
      const left = f.fuel.extra_min - a.metrics.hold_min;
      if (left < 15) out.push({ id: `fuel:${a.callsign}`, level: "amber", callsign: a.callsign, title: `Fuel · ${icao(a.callsign)}`,
        detail: `Holds ${a.metrics.hold_min} min; ${Math.max(0, Math.round(left))} min of extra fuel left above alternate and reserve.` });
    }
    const was = r?.baseline.plan.assignments.find((b) => b.callsign === a.callsign);
    // only when the plan strands more connecting passengers than the status quo would
    if (a.metrics.pax_total > 0 && was && a.metrics.pax_protected < was.metrics.pax_protected) {
      const miss = a.metrics.pax_total - a.metrics.pax_protected;
      out.push({ id: `pax:${a.callsign}:${miss}`, level: "amber", callsign: a.callsign, title: `Connections · ${icao(a.callsign)}`,
        detail: `${miss} of ${a.metrics.pax_total} connecting passengers miss their flights; ${was.metrics.pax_protected - a.metrics.pax_protected} more than the status quo.` });
    }
  }
  if (r && r.outcome !== "consensus") out.push({ id: "fallback", level: "amber", title: "No agreement", detail: "The agents did not agree in 3 turns; the deterministic solver chose this plan." });
  if (r && r.stats.fallbacks > 0) out.push({ id: "model-fallback", level: "amber", title: "Model fallback", detail: `${r.stats.fallbacks} decision${r.stats.fallbacks === 1 ? "" : "s"} fell back to the next model in the chain.` });
  return out;
}

export function AlertList({ alerts, seen, onSeen, onFocus }: { alerts: Alert[]; seen: Set<string>; onSeen: (id: string) => void; onFocus: (cs: string) => void }) {
  if (!alerts.length) return null;
  const order = [...alerts].sort((a, b) => (a.level === b.level ? 0 : a.level === "red" ? -1 : 1) || (seen.has(a.id) ? 1 : 0) - (seen.has(b.id) ? 1 : 0));
  return (
    <ul className="alerts" aria-label="Alerts">
      {order.map((a) => (
        <li key={a.id} className={`alert ${a.level}${seen.has(a.id) ? " seen" : ""}`}>
          <button className="alert-body" onClick={() => a.callsign && onFocus(a.callsign)} disabled={!a.callsign}>
            <b>{a.title}</b><span>{a.detail}</span>
          </button>
          {a.level === "amber" && !seen.has(a.id) && <button className="ack" onClick={() => onSeen(a.id)}>Seen</button>}
        </li>
      ))}
    </ul>
  );
}
