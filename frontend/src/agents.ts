import type { AgentId, AirlineGroup, Objection, UnableBody } from "./api/types";
import { agentClass, agentName, signed } from "./format";

export interface RosterEntry { id: string; cls: "atc" | "airline" | "ground" | "pilot"; name: string; what: string }

const ATC = "Sees the runways, the queue and the wind. Sequences every flight: landing order, runway, slot, speed control or ground delay.";
const GROUND = "Sees gates, stands and crews. Reassigns a gate, sends a flight to a remote stand, swaps gates.";
const PILOT = "Flies one flight: its fuel, ETA and crew hours. A rule-based check, not a model: it only speaks to say UNABLE.";

function airlineWhat(code: string, groups: AirlineGroup[]): string {
  const g = groups.find((x) => x.code === code);
  const own = g?.callsigns.length ? ` Its flights: ${g.callsigns.join(", ")}.` : "";
  return `Speaks for ${g?.name ?? `airline ${code}`}.${own} Moves its own flights between slots and gates, and rejects any plan that leaves one of them clearly worse off than it started.`;
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

const fromWhom = (p: Objection["proposed_by"]) => (p === "ENGINE" ? "the opening plan" : `${agentName(p)}'s plan`);

/** One breach, e.g. "AA2712 delay +15.5 min vs the original plan (ATC's plan)". The wording follows floor_source. */
export function objectionLine(o: Objection): string {
  const d = o.value - o.floor;
  const what = o.metric === "delay_min" ? `delay ${signed(d)} min` : `fuel ${signed(d)} kg`;
  const vs = o.floor_source === "best_seen" ? "the best plan seen" : "the original plan";
  return `${o.callsign} ${what} vs ${vs} (${fromWhom(o.proposed_by)})`;
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
