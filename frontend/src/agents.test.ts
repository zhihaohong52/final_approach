import { describe, expect, it } from "vitest";
import type { UnableBody } from "./api/types";
import { agentClass, agentName } from "./format";
import { objectionLine, roster, unableLine } from "./agents";

const sampleUnable: UnableBody = {
  type: "UNABLE", turn: 1, agent: "PILOT:AA2712", callsign: "AA2712", rule: "PLT-FUEL-RESERVE", source: "14 CFR 121.639",
  evidence: { need_min: 61 }, detail: "needs 61 min of fuel", instruction: null, plan_id: "p1", proposed_by: "ATC",
  text: "UNABLE hold 12 min: fuel reserve would fall to 28 min, PLT-FUEL-RESERVE",
};
const aa = { code: "AA", name: "American Airlines Inc.", callsigns: ["AA2712"], basis: "" };
const o = { callsign: "AA2712", metric: "delay_min" as const, floor: 209, value: 224.5, allowed: 209.05, plan_id: "x", proposed_by: "ATC" as const, floor_source: "status_quo" as const };

describe("agent names", () => {
  it("airline agents", () => {
    expect(agentClass("AIRLINE:AA")).toBe("airline");
    expect(agentName("AIRLINE:AA")).toBe("Airline AA");
  });
  it("maps DECIDER to Triage and shows any other unknown sender raw", () => {
    expect(agentName("DECIDER")).toBe("Triage");
    expect(agentName("MYSTERY" as never)).toBe("MYSTERY");
  });
});
describe("roster", () => {
  it("v2.3 run: voters, then pilots", () => {
    const r = roster(["ATC", "AIRLINE:AA", "GROUND"], ["PILOT:AA2712"], [aa]);
    expect(r.map((x) => x.cls)).toEqual(["atc", "airline", "ground", "pilot"]);
    expect(r[1].name).toBe("Airline AA");
    expect(r[1].what).toContain("American Airlines Inc.");
    expect(r[1].what).toContain("AA2712");
  });
  it("old 3-agent run keeps its pilot among the voters", () => {
    expect(roster(["ATC", "PILOT:DL100", "GROUND"]).map((x) => x.cls)).toEqual(["atc", "pilot", "ground"]);
  });
});
describe("lines", () => {
  it("unableLine", () => expect(unableLine(sampleUnable)).toMatch(/^UNABLE .+, PLT-FUEL-RESERVE$/));
  it("objectionLine names the floor source", () => {
    expect(objectionLine(o)).toBe("AA2712 delay +15.5 min vs the original plan (ATC's plan)");
    expect(objectionLine({ ...o, floor_source: "best_seen" })).toBe("AA2712 delay +15.5 min vs the best plan seen (ATC's plan)");
    expect(objectionLine({ ...o, metric: "fuel_kg", floor: 100, value: 160, proposed_by: "ENGINE" })).toBe("AA2712 fuel +60 kg vs the original plan (the opening plan)");
  });
});
