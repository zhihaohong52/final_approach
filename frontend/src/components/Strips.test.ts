import { describe, expect, it } from "vitest";
import type { Assignment, TrajectorySet } from "../api/types";
import { pathLine } from "./Strips";

const metrics = { hold_min: 0, fuel_kg: 0, delay_min: 0, in_block_min: null, pax_protected: 0, pax_total: 0, overtime_min: 0, next_dep_delay_min: 0 };
const a = (m: Partial<Assignment["metrics"]> = {}): Assignment => ({ callsign: "UA1", action: "land", runway: "28C", slot_min: 30, gate: "C1", speed_reduction_min: 0, ground_delay_min: 0, metrics: { ...metrics, ...m } });
const air = (over: Partial<TrajectorySet["air"][number]> = {}): TrajectorySet["air"][number] => ({
  callsign: "UA1", wave: true, runway: "28C", appears_min: 12.4,
  points: [
    { t_min: 12.4, lat: 0, lon: 0, alt_ft: 18000, kind: "entry", src: "synth" },
    { t_min: 20, lat: 0, lon: 1, alt_ft: 12000, kind: "fix", fix: "BFORD", src: "cifp" },
    { t_min: 30, lat: 0, lon: 2, alt_ft: 0, kind: "threshold", src: "cifp" },
  ],
  ...over,
});

describe("pathLine", () => {
  it("lists the entry fix and time before the target appears", () => {
    expect(pathLine(a(), air(), 5)).toBe("inbound, entry BFORD T+12");
    expect(pathLine(a(), air(), 13)).toBe("");
  });
  it("shows the hold as fix, laps and level", () => {
    const hold = { fix: "BENKY", laps: 2, level_ft: 12000, entry_min: 20, exit_min: 28, lap_min: 4 };
    expect(pathLine(a(), air({ hold }), 20)).toBe("HLD BENKY 2L 120");
  });
  it("falls back to the plan's metrics without trajectories", () => {
    expect(pathLine(a({ hold_laps: 3, hold_fix: "BENKY", hold_level_ft: 13000 }), undefined, 0)).toBe("HLD BENKY 3L 130");
  });
  it("adds the route and vector time", () => {
    expect(pathLine(a({ route_extra_min: 2.14, vector_min: 1.5 }), undefined, 0)).toBe("rte +2.1′ · vec +1.5′");
    expect(pathLine(a({ route_extra_min: -0.6, vector_min: 0 }), undefined, 0)).toBe("rte −0.6′");
    expect(pathLine(a({ route_extra_min: 0.04 }), undefined, 0)).toBe("");
  });
});
