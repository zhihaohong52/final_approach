import type { RuleCheck, RuleId } from "./api/types";

/** Evidence fields in which a failing check names the other flight(s) it is with (comma-separated callsigns).
 *  Kept equal to backend/wave/edit.py OTHER_FLIGHT_KEYS. */
export const OTHER_FLIGHT_KEYS = ["leader", "follower", "conflict", "overlapping", "earlier_holding", "blocked_by"] as const;

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Whether a check names flight `cs`: its own flight, a structured evidence key, or the whole callsign in the
 *  detail text (AA26 is not named by "AA260"). */
export function namesFlight(c: Pick<RuleCheck, "callsign" | "detail" | "evidence">, cs: string): boolean {
  if (c.callsign === cs) return true;
  const ev = c.evidence ?? {};
  if (OTHER_FLIGHT_KEYS.some((k) => String(ev[k] ?? "").split(",").some((x) => x.trim() === cs))) return true;
  return new RegExp(`(^|[^A-Za-z0-9])${esc(cs)}([^A-Za-z0-9]|$)`).test(c.detail ?? "");
}

/** "14 safety rules" from Config.rule_ids (the engine's own list); "the safety rules" until it is known. */
export function rulesCount(ids: RuleId[] | undefined): string {
  return ids?.length ? `${ids.length} safety rules` : "the safety rules";
}
