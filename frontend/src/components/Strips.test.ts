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
    expect(pathLine(a(), air(), 5)).toBe("inbound, entry BFORD T+20");
    expect(pathLine(a(), air(), 13)).toBe("");
  });
  it("names the procedures' entry fix, with that fix's own time, as the radar does", () => {
    const pts = air().points;
    const two = air({ points: [pts[0], { ...pts[1], fix: "AAA", t_min: 15 }, { ...pts[1], fix: "BFORD" }, pts[2]] });
    expect(pathLine(a(), two, 5, undefined, new Map([["AAA", { kind: "star" }], ["BFORD", { kind: "entry" }]]))).toBe("inbound, entry BFORD T+20");
    expect(pathLine(a(), two, 5, undefined, new Map([["AAA", { kind: "star" }], ["BFORD", { kind: "faf" }]]))).toBe("inbound, entry AAA T+15");
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
  it("after touchdown shows the taxi: exit, stand and minutes; the exit goes once on blocks", () => {
    const m = { exit: "B10", taxi_min: 9.4, in_block_min: 40 };
    expect(pathLine(a(m), air(), 30)).toBe("TXI B10 → C1 9m");
    expect(pathLine(a(m), air(), 39.9)).toBe("TXI B10 → C1 9m");
    expect(pathLine(a(m), air(), 40)).toBe("TXI → C1 9m");
  });
  it("prefers the engine's ground track for exit, stand, taxi time and in-block", () => {
    const g: TrajectorySet["ground"][number] = { callsign: "UA1", kind: "arrival", wave: true, exit: "M4", stand: "C3", route_names: ["M", "C"],
      points: [{ t_min: 31, lat: 0, lon: 0 }, { t_min: 42.6, lat: 0, lon: 1 }], waits: [], unimpeded_min: 10.5, taxi_min: 11.6, src: "osm" };
    expect(pathLine(a({ exit: "B10", taxi_min: 9, in_block_min: 40 }), air(), 41, g)).toBe("TXI M4 → C3 12m");
    expect(pathLine(a(), air(), 43, g)).toBe("TXI → C3 12m");
    expect(pathLine(a(), air(), 41, { ...g, stand: "n9463243357" })).toBe("TXI M4 → C1 12m");
  });
  it("says nothing after touchdown without taxi data", () => expect(pathLine(a(), air(), 31)).toBe(""));
});
