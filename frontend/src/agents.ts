import type { AgentId, AgentTurnBody, AirlineGroup, FloorBreach, Objection, UnableBody, Vote } from "./api/types";
import { icaoText } from "./callsign";
import { agentClass, agentName, signed } from "./format";

export interface RosterEntry { id: string; cls: "atc" | "airline" | "ground" | "pilot"; name: string; what: string }

const ATC = "Sees the runways, the queue and the wind. Sequences every flight: landing order, runway, slot, speed control or ground delay.";
const GROUND = "Sees gates, stands and crews. Reassigns a gate, sends a flight to a remote stand, swaps gates.";
const PILOT = "Flies one flight: its fuel, ETA and crew hours. A rule-based check, not a model: it only speaks to say UNABLE.";

function airlineWhat(code: string, groups: AirlineGroup[]): string {
  const g = groups.find((x) => x.code === code);
  const own = g?.callsigns.length ? ` Its flights: ${g.callsigns.join(", ")}.` : "";
  return `Speaks for ${(g?.name ?? `airline ${code}`).replace(/\.+$/, "")}.${own} Moves its own flights between slots and gates, and rejects any plan that leaves one of them clearly worse off than it started.`;
}

/** Everyone who takes part: the voters (`agents`), then the pilots (formula-only, never vote). Old runs list the focus pilot among the voters. */
export function roster(agents: AgentId[], pilots: string[] = [], airlines: AirlineGroup[] = []): RosterEntry[] {
  const mk = (id: string): RosterEntry => {
    const cls = agentClass(id as AgentId) as RosterEntry["cls"];
    const what = cls === "atc" ? ATC : cls === "ground" ? GROUND : cls === "airline" ? airlineWhat(id.slice(8), airlines) : PILOT;
    return { id, cls, name: agentName(id as AgentId), what };
  };
  const seen = new Set<string>(agents);
  return [...agents, ...pilots.filter((p) => !seen.has(p))].map(mk);
}

export const unableLine = (b: UnableBody): string => b.text;

const fromWhom = (p: Objection["proposed_by"]) => `objecting to ${p === "ENGINE" ? "the opening plan" : `${agentName(p)}'s plan`}`;

/** One breach, e.g. "AA2712 delay +15.5 min vs the original plan (ATC's plan)". The wording follows floor_source. */
export function objectionLine(o: Objection): string {
  const d = o.value - o.floor;
  const what = o.metric === "delay_min" ? `delay ${signed(d)} min` : `fuel ${signed(d)} kg`;
  const vs = o.floor_source === "best_seen" ? "the best plan seen so far" : "the original plan";
  return `${o.callsign} ${what} vs ${vs} \u2014 ${fromWhom(o.proposed_by)}`;
}

/** Who is in the room, from the best source at hand: a finished run, else the OPEN message, else the wave itself (before negotiating). */
export function participants(wave: { airlines?: AirlineGroup[]; flights: { callsign: string }[]; focus?: string | null } | null | undefined,
  result: { agents: AgentId[]; pilots?: AgentId[]; airlines?: AirlineGroup[] } | null | undefined,
  open: { agents: AgentId[]; pilots?: AgentId[]; airlines?: AirlineGroup[] } | null | undefined) {
  const src = result ?? open;
  if (src) return { agents: src.agents, pilots: src.pilots as string[] | undefined, airlines: src.airlines };
  if (wave?.airlines?.length) {
    return { agents: ["ATC", ...wave.airlines.map((a) => `AIRLINE:${a.code}` as AgentId), "GROUND"] as AgentId[],
      pilots: wave.flights.map((f) => `PILOT:${f.callsign}`), airlines: wave.airlines };
  }
  return { agents: ["ATC", ...(wave?.focus ? [`PILOT:${wave.focus}` as AgentId] : []), "GROUND"] as AgentId[], pilots: undefined, airlines: undefined };
}

export type VoteKind = "floor" | "escape" | "utility";

/** Why a vote went the way it did, from the vote data only (never the rationale text). "escape": an airline's
 * utility vote on a plan that breaches its fairness floor while no plan on the table meets it. Runs recorded before
 * the `decided_by: "escape"` marker are read from the turn's objections: a non-floor airline vote on an objected plan,
 * in a turn with no floor REJECT, is an escape vote. */
export function voteKind(b: Pick<AgentTurnBody, "agent" | "votes" | "objections">, v: Vote): VoteKind {
  if (v.decided_by === "floor") return "floor";
  if (v.decided_by === "escape") return "escape";
  if (agentClass(b.agent) !== "airline" || b.votes.some((x) => x.decided_by === "floor")) return "utility";
  return b.objections?.some((o) => o.plan_id === v.plan_id) ? "escape" : "utility";
}

/** The breaches behind a floor or escape vote: its own `floor`, else the turn's objections to that plan. */
export function voteBreaches(b: Pick<AgentTurnBody, "objections">, v: Vote): FloorBreach[] {
  return v.floor?.length ? v.floor : (b.objections ?? []).filter((o) => o.plan_id === v.plan_id);
}

/** "AA2712 delay +15.5 min over the best plan seen (+1 more)". */
export function breachText(br: FloorBreach[]): string {
  const x = br[0];
  if (!x) return "a flight over its floor";
  const what = x.metric === "delay_min" ? `delay ${signed(x.value - x.floor)} min` : `fuel ${signed(x.value - x.floor)} kg`;
  const vs = x.floor_source === "best_seen" ? "the best plan seen" : "the original plan";
  return icaoText(`${x.callsign} ${what} vs ${vs}`) + (br.length > 1 ? ` (+${br.length - 1} more)` : "");
}

/** The vote part of a turn line. Details on: one clause per notable vote, with ids; off: counts by reason. A floor
 * REJECT is never worded as a utility comparison. */
export function voteSummary(b: Pick<AgentTurnBody, "agent" | "votes" | "objections">, details: boolean): string {
  const kinds = b.votes.map((v) => [v, voteKind(b, v)] as const);
  const cmp = (v: Vote) => `${v.utility.toFixed(2)} ${v.utility < v.reservation ? "<" : "≥"} ${v.reservation.toFixed(2)}`;
  if (details) {
    return kinds.map(([v, k]) => {
      if (k === "floor") return ` · rejects ${v.plan_id} on its fairness floor: ${breachText(voteBreaches(b, v))}`;
      if (k === "escape" && v.vote === "ACCEPT")
        return ` · accepts ${v.plan_id} below its fairness floor (${breachText(voteBreaches(b, v))}): no plan on the table meets it`;
      if (v.vote === "REJECT") return ` · rejects ${v.plan_id} (${v.utility < v.reservation ? "" : "the model's call: "}${cmp(v)})`;
      return "";
    }).join("");
  }
  const n = (f: (v: Vote, k: VoteKind) => boolean) => kinds.filter(([v, k]) => f(v, k)).length;
  const fl = n((_, k) => k === "floor");
  const ut = n((v, k) => k !== "floor" && v.vote === "REJECT");
  const es = n((v, k) => k === "escape" && v.vote === "ACCEPT");
  return (fl ? ` · rejects ${fl} on its fairness floor` : "") + (ut ? ` · rejects ${ut} below its minimum` : "")
    + (es ? ` · accepts ${es === 1 ? "one" : es} below its fairness floor: no plan on the table meets it` : "");
}
