import type { RuleCheck, TrajectorySet } from "./api/types";

type Conflict = TrajectorySet["conflicts"][number];

/** What the surface map shows for a vetoed move. Only a veto that cites ATC-TAXI-CONFLICT has ground conflicts to
 *  show (null otherwise: no request is made and nothing is drawn, so the map never blames the ground for a veto on
 *  another rule). Given the moved plan's conflicts, it keeps the failing ones (no `kind`) of the flights that veto
 *  names (all failing ones when it names none). Without them, [] means "ask". */
export function vetoConflicts(violations: Pick<RuleCheck, "rule" | "callsign">[], conflicts: Conflict[] = []): Conflict[] | null {
  const taxi = violations.filter((v) => v.rule === "ATC-TAXI-CONFLICT");
  if (!taxi.length) return null;
  const named = new Set(taxi.map((v) => v.callsign).filter((cs): cs is string => !!cs));
  return conflicts.filter((c) => !c.kind && (!named.size || named.has(c.callsign)));
}
