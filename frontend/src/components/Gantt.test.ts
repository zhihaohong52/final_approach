import { describe, expect, it } from "vitest";
import type { Assignment } from "../api/types";
import { holdSpans } from "./Gantt";

const metrics = { hold_min: 0, fuel_kg: 0, delay_min: 0, in_block_min: null, pax_protected: 0, pax_total: 0, overtime_min: 0, next_dep_delay_min: 0 };
const a = (m: Partial<Assignment["metrics"]> = {}): Assignment => ({ callsign: "UA1", action: "land", runway: "28C", slot_min: 30, gate: "C1", speed_reduction_min: 0, ground_delay_min: 0, metrics: { ...metrics, ...m } });

describe("holdSpans", () => {
  it("one segment per lap at the engine's hold times", () => {
    const air = { callsign: "UA1", wave: true, runway: "28C", appears_min: 0, points: [],
      hold: { fix: "BENKY", laps: 3, level_ft: 12000, entry_min: 10, exit_min: 22, lap_min: 4 } };
    expect(holdSpans(a({ hold_min: 12 }), air).map((s) => [s.from, s.to])).toEqual([[10, 14], [14, 18], [18, 22]]);
    expect(holdSpans(a(), air)[1].title).toContain("lap 2 of 3");
  });
  it("falls back to the plan's hold minutes ending at the slot, split into its laps", () => {
    expect(holdSpans(a({ hold_min: 8, hold_laps: 2, hold_fix: "BENKY" })).map((s) => [s.from, s.to])).toEqual([[22, 26], [26, 30]]);
    expect(holdSpans(a({ hold_min: 3 })).map((s) => [s.from, s.to])).toEqual([[27, 30]]);
  });
  it("nothing without a hold", () => expect(holdSpans(a())).toEqual([]));
});
