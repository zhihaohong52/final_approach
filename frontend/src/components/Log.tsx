import { useEffect, useRef } from "react";
import type { AgentId, AgentTurnBody, AirlineGroup, Assignment, DecisionMeta, JointPlan, NegotiationMessage, RuleId } from "../api/types";
import { objectionLine, roster, unableLine, voteSummary } from "../agents";
import { icaoText } from "../callsign";
import { agentClass, agentName as rawAgentName } from "../format";
import { rulesCount } from "../rules";

const agentName = (s: string) => icaoText(rawAgentName(s as never));

const ROUND = ["Triage and opening plan", "Turn 1", "Turn 2", "Turn 3", "Fallback"];

function summary(m: NegotiationMessage): string {
  const b = m.body;
  switch (b.type) {
    case "TRIAGE": return `Severity ${b.levels[b.probabilities.indexOf(Math.max(...b.probabilities))]}`;
    case "OPEN": return `Opens with the first-come plan, ${b.evaluation.feasible ? "which passes every rule" : `which breaks ${b.evaluation.checks.filter((c) => !c.passed).length} rule${b.evaluation.checks.filter((c) => !c.passed).length === 1 ? "" : "s"}`}`;
    case "AGENT_TURN": return b.moves.length ? b.moves.map((x) => x.move.label).join("; ") : "No move; keeps the plan on the table";
    case "CHECK": return b.passed ? `Plan ${b.plan_id}${b.proposed_by ? ` from ${agentName(b.proposed_by)}` : ""} passes every rule` : `Plan ${b.plan_id} vetoed: ${b.violations.map((v) => `${v.rule} ${v.callsign ?? ""}`).join(", ")}`;
    case "CONSENSUS": return `Consensus on plan ${b.plan_id} in turn ${b.turn}, accepted by all ${b.accepted_by.length} agents`;
    case "NO_CONSENSUS": return `No consensus in turn ${b.turn}`;
    case "FALLBACK": return `Deterministic solver picks plan ${b.plan_id} (${b.method})`;
    case "MODEL_FALLBACK": return `${b.from_model} failed (${b.error}); ${b.to_model} answers`;
    case "UNABLE": return unableLine(b);
    // superseded in v2.0, still readable for old runs
    case "MOVE": return b.move.label;
    case "MOVE_REJECTED": return `${b.move.label}: vetoed, ${b.violations[0]?.rule ?? "rule"}`;
    case "COUNTER": return `Counter-plan · delay ${b.evaluation.totals.delay_min} min`;
    case "VOTE": return b.votes.map((v) => `${v.decision === "ACCEPT" ? "Accepts" : "Rejects"} ${v.plan_id}`).join("; ");
  }
}

// ---- what each agent's turn changed, against the plan on the table before it spoke
interface StepDiff { flights: string[]; delayBefore: number; delayAfter: number; breachesBefore: number | null; breachesAfter: number | null }

const n1 = (v: number) => (Math.round(v * 10) / 10).toString();
const tmin = (v?: number | null) => (v == null ? "?" : `T${v >= 0 ? "+" : ""}${n1(v)}`);
const totalDelay = (p: JointPlan) => p.assignments.reduce((s, a) => s + (a.metrics?.delay_min ?? 0), 0);

function flightDiff(a: Assignment, b: Assignment): string | null {
  const parts: string[] = [];
  if (a.action !== b.action) parts.push(b.action === "divert" ? "diverts" : "lands instead of diverting");
  if (b.action === "land") {
    if (a.runway !== b.runway) parts.push(`runway ${a.runway ?? "-"} → ${b.runway}`);
    if (Math.abs((a.slot_min ?? 0) - (b.slot_min ?? 0)) >= 0.1) parts.push(`lands ${tmin(a.slot_min)} → ${tmin(b.slot_min)}`);
    if (a.gate !== b.gate) parts.push(`gate ${a.gate ?? "-"} → ${b.gate === "RS" ? "remote stand" : b.gate}`);
    if (a.speed_reduction_min !== b.speed_reduction_min) parts.push(`slows ${n1(a.speed_reduction_min)} → ${n1(b.speed_reduction_min)} min en route`);
    if (a.ground_delay_min !== b.ground_delay_min) parts.push(`ground delay ${n1(a.ground_delay_min)} → ${n1(b.ground_delay_min)} min`);
    if (Math.abs(a.metrics.hold_min - b.metrics.hold_min) >= 0.1) parts.push(`holding ${n1(a.metrics.hold_min)} → ${n1(b.metrics.hold_min)} min`);
    if (a.metrics.pax_protected !== b.metrics.pax_protected) parts.push(`connecting pax ${a.metrics.pax_protected} → ${b.metrics.pax_protected} of ${b.metrics.pax_total}`);
  }
  if (!parts.length) return null;
  if (Math.abs(a.metrics.delay_min - b.metrics.delay_min) >= 0.1) parts.push(`delay ${n1(a.metrics.delay_min)} → ${n1(b.metrics.delay_min)} min`);
  return `${b.callsign}  ${parts.join(" · ")}`;
}

function stepDiffs(messages: NegotiationMessage[]): Map<number, StepDiff> {
  const out = new Map<number, StepDiff>();
  let prev: JointPlan | null = null;
  let prevBreaches: number | null = null;
  messages.forEach((m, i) => {
    const b = m.body;
    if (b.type === "OPEN") { prev = b.plan; prevBreaches = b.evaluation.checks.filter((c) => !c.passed).length; return; }
    if (b.type !== "AGENT_TURN" || !b.proposal) return;
    const cur = b.proposal;
    const check = messages.slice(i + 1).find((x) => x.body.type === "CHECK" && x.body.plan_id === cur.id)?.body;
    const before: JointPlan | null = prev;
    const breaches = check && check.type === "CHECK" ? check.violations.length : before && before.id === cur.id ? prevBreaches : null;
    const byCs = new Map((before?.assignments ?? []).map((a) => [a.callsign, a]));
    const flights = before ? cur.assignments.map((a) => { const o = byCs.get(a.callsign); return o ? flightDiff(o, a) : null; }).filter((x): x is string => !!x) : [];
    out.set(m.seq, { flights, delayBefore: before ? totalDelay(before) : totalDelay(cur), delayAfter: totalDelay(cur), breachesBefore: prevBreaches, breachesAfter: breaches });
    prev = cur; prevBreaches = breaches;
  });
  return out;
}

function Changes({ d }: { d: StepDiff }) {
  const dd = d.delayAfter - d.delayBefore;
  const br = d.breachesBefore != null && d.breachesAfter != null && d.breachesBefore !== d.breachesAfter
    ? ` · rule breaches ${d.breachesBefore} → ${d.breachesAfter}`
    : d.breachesAfter ? ` · ${d.breachesAfter} rule breach${d.breachesAfter === 1 ? "" : "es"} remain` : "";
  if (!d.flights.length) return <div className="msg-diff muted">No change to the plan{br}</div>;
  return (
    <div className="msg-diff">
      {d.flights.map((f) => <div key={f}>{icaoText(f)}</div>)}
      <div className="msg-diff-total">
        Wave total delay {n1(d.delayBefore)} → {n1(d.delayAfter)} min
        {Math.abs(dd) >= 0.1 && <span className={dd < 0 ? "pos" : "neg"}> ({dd > 0 ? "+" : ""}{n1(dd)})</span>}
        {br}
      </div>
    </div>
  );
}

const meta = (d: DecisionMeta) => `${d.kind === "formula" ? "formula" : d.model}${d.confidence != null ? ` · p ${d.confidence.toFixed(2)}` : ""} · ${d.latency_ms} ms`;

// The engine writes for a log file: plan hashes, "(s)" plurals, role tags, move chains. For the controller each
// line is rewritten to what changed; the hashes and scores stay available behind "Show model, probability and latency".
function plain(raw: string, details: boolean): string {
  let s = icaoText(raw);
  s = s.replace(/(\d+) (rule violation|plan|flight|move|check)\(s\)/g, (_, n: string, w: string) => `${n} ${w}${n === "1" ? "" : "s"}`);
  s = s.replace(/\bPILOT:/g, "Pilot ").replace(/\bGROUND\b/g, "Ground");
  // "Move X from A to runway B; Move X from B to runway C" -> net change per flight
  if (s.includes("; ")) {
    const parts = s.split(/;\s*/).map((p) => p.trim()).filter(Boolean);
    const net = new Map<string, [string, string, number]>();   // flight -> first from, last to, position
    const out: (string | null)[] = [];
    for (const p of parts) {
      const m = p.match(/^Move (\S+) from (\S+) to runway (\S+)$/);
      if (!m) { out.push(p); continue; }
      const prev = net.get(m[1]);
      if (prev) net.set(m[1], [prev[0], m[3], prev[2]]);
      else { net.set(m[1], [m[2], m[3], out.length]); out.push(null); }
    }
    for (const [cs, [a, b, i]] of net) out[i] = a === b ? "" : `Move ${cs} from ${a} to runway ${b}`;
    s = out.filter(Boolean).join("; ");
  }
  if (details) return s;
  return s
    .replace(/\bPlan [0-9a-f]{10} from (\w+)/g, "$1's plan")
    .replace(/ \(?[0-9a-f]{10}\)? \((\d\.\d+) < (\d\.\d+)\)/g, " a plan below its minimum")
    .replace(/\b[Pp]lan [0-9a-f]{10}\b/g, (m) => (m[0] === "P" ? "The plan" : "the plan"))
    .replace(/\b[0-9a-f]{10}\b/g, "the plan")
    .replace(/; max-min utility [\d.]+ among (\d+) unanimous plans?/, "")
    .replace(/ among 1 unanimous plan\(s\)/, "")
    .replace(/the plan\(s\)/g, "the plan")
    .replace(/no CHECK-passed plan/g, "no rule-clean plan")
    .replace(/ \(the plan: [^)]*\)/g, "")
    .replace(/^turn (\d)/, "Turn $1");
}

function Turn({ b, details, diff }: { b: AgentTurnBody; details: boolean; diff?: StepDiff }) {
  const acc = b.votes.filter((v) => v.vote === "ACCEPT").length;
  return (
    <>
      {diff && <Changes d={diff} />}
      {b.rejected.map((r) => (
        <div key={r.move.id} className="msg-why breach">Vetoed: {plain(r.move.label, details)}. {plain(r.violations[0]?.detail ?? r.reason, details)}</div>
      ))}
      {b.objections?.length ? (
        <div className="msg-why breach">
          Objects to {new Set(b.objections.map((o) => o.plan_id)).size === 1 ? "the plan" : "plans"} on the table:
          <ul className="msg-obj">
            {b.objections.slice(0, 6).map((o, i) => <li key={i}>{icaoText(objectionLine(o))}</li>)}
            {b.objections.length > 6 && <li>and {b.objections.length - 6} more</li>}
          </ul>
        </div>
      ) : null}
      <div className="msg-why">
        Own plan scores {b.proposal_utility.toFixed(2)} · accepts {acc} of {b.votes.length} plan{b.votes.length === 1 ? "" : "s"} on the table
        {voteSummary(b, details)}
      </div>
      {details && b.moves.map((x) => <div key={x.move.id} className="msg-dec">{x.move.label.split(" ").slice(0, 2).join(" ")} · {meta(x.decision)}</div>)}
      {details && b.decision && <div className="msg-dec">vote · {meta(b.decision)}{b.decision.fallback_from?.length ? <span style={{ color: "var(--warning)" }}> · after {b.decision.fallback_from.join(", ")} failed</span> : null}</div>}
      {!details && b.decision?.fallback_from?.length ? <div className="msg-dec" style={{ color: "var(--warning)" }}>Model fallback: {b.decision.fallback_from.join(", ")} failed</div> : null}
    </>
  );
}

export function Log({ messages, running, focus, details = false, ruleIds, agents, pilots, airlines }: { messages: NegotiationMessage[]; running: boolean; focus?: string | null; details?: boolean; ruleIds?: RuleId[]; agents?: AgentId[]; pilots?: string[]; airlines?: AirlineGroup[] }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (running && box.current) box.current.scrollTop = box.current.scrollHeight; }, [messages.length, running]);
  if (!messages.length) {
    const people = roster(agents ?? ["ATC", ...(focus ? [`PILOT:${focus}` as AgentId] : []), "GROUND"], pilots, airlines);
    const pilotCards = people.filter((r) => r.cls === "pilot");
    const roles: [string, string, string][] = [
      ...people.filter((r) => r.cls !== "pilot" || pilotCards.length === 1).map((r): [string, string, string] => [icaoText(r.name), r.cls, r.what]),
      ...(pilotCards.length > 1 ? [[`${pilotCards.length} pilots`, "pilot", pilotCards[0].what] as [string, string, string]] : []),
      ["Engine", "engine", `Deterministic code, not a model. Checks ${rulesCount(ruleIds)} on every plan and vetoes any that breaks one.`],
    ];
    return (
      <div>
        <p className="empty" style={{ marginBottom: 12 }}>No messages yet. Each agent speaks once per turn, for at most three turns; the engine checks every plan in between.</p>
        {roles.map(([name, cls, what]) => (
          <article key={cls} className="msg" style={{ animation: "none" }}>
            <div className="msg-top"><span className={`who ${cls}`}>{name}</span><div className="msg-body muted">{what}</div></div>
          </article>
        ))}
      </div>
    );
  }
  const rounds = [...new Set(messages.map((m) => m.round))];
  const diffs = stepDiffs(messages);
  return (
    <div className="log" ref={box} tabIndex={0} role="region" aria-label="Negotiation log">
      {rounds.map((r) => (
        <section key={r}>
          <h3 className="round-h">{ROUND[r] ?? `Turn ${r}`}</h3>
          {messages.filter((m) => m.round === r).map((m) => {
            const b = m.body;
            if (b.type === "UNABLE") return (
              <article key={m.seq} className="msg unable">
                <div className="msg-top">
                  <span className="who pilot">{agentName(m.sender)}</span>
                  <div className="msg-body"><b className="unable-word">UNABLE</b>{plain(unableLine(b), details).replace(/^UNABLE/, "")}</div>
                </div>
                <div className="msg-why">{b.source}</div>
              </article>
            );
            const rej = b.type === "MOVE_REJECTED" || (b.type === "CHECK" && !b.passed);
            const d = b.type === "AGENT_TURN" ? undefined : m.decision;
            return (
              <article key={m.seq} className={`msg${rej ? " rej" : ""}${b.type === "AGENT_TURN" ? " turn" : ""}`}>
                <div className="msg-top">
                  <span className={`who ${agentClass(m.sender)}`}>{agentName(m.sender)}</span>
                  <div className="msg-body">{plain(summary(m), details)}</div>
                </div>
                {b.type === "AGENT_TURN" && <Turn b={b} details={details} diff={diffs.get(m.seq)} />}
                {(b.type === "NO_CONSENSUS" || b.type === "FALLBACK") && <div className="msg-why">{plain(b.reason, details)}</div>}
                {b.type === "MOVE_REJECTED" && b.violations[0] && <div className="msg-why breach">{plain(b.violations[0].detail, details)} · {b.violations[0].source}</div>}
                {b.type !== "AGENT_TURN" && b.type !== "TRIAGE" && m.rationale && <div className="msg-why">{plain(m.rationale, details)}</div>}
                {d && (details || d.fallback_from?.length) && <div className="msg-dec">{meta(d)}{d.fallback_from?.length ? <span style={{ color: "var(--warning)" }}> · after {d.fallback_from.join(", ")} failed</span> : null}</div>}
              </article>
            );
          })}
        </section>
      ))}
    </div>
  );
}
