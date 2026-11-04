import { describe, expect, it } from "vitest";
import { autoRange, axisEnd, axisStart, chipLabel, type Lim } from "./scope";

// a 690x560 scope, centred, with a 64 px top band and 60 px bottom band, as the radar computes it
const limOf = (r: number): Lim => { const k = (560 / 2 - 18) / r; return { l: -(345 - 16) / k, r: (345 - 16) / k, t: -(280 - 64) / k, b: (280 - 60) / k }; };

describe("axisStart", () => {
  it("no hold: 15 min before the first slot after T+45", () => expect(axisStart([82, 90], [])).toBe(60));
  it("a hold earlier than the first slot pulls the axis back", () => expect(axisStart([82, 90], [51.3])).toBe(30));
  it("a later hold changes nothing", () => expect(axisStart([82, 90], [120])).toBe(60));
  it("early plans start 15 min before T0, or earlier for a hold at t < 0", () => {
    expect(axisStart([10, 20], [])).toBe(-15);
    expect(axisStart([10, 20], [-20])).toBe(-30);
  });
  it("no events at all", () => expect(axisStart([], [])).toBe(-15));
});

describe("axisEnd", () => {
  it("at least 75 min", () => expect(axisEnd(60, 70)).toBe(135));
  it("capped at start + 180", () => expect(axisEnd(30, 600)).toBe(210));
  it("rounds up to 15 min past the last event", () => expect(axisEnd(0, 100)).toBe(105));
  it("reaches the disrupted flight past three hours", () => expect(axisEnd(0, 330, 310)).toBe(315));
  it("the disrupted flight's reach only lifts the cap", () => expect(axisEnd(0, 100, 310)).toBe(105));
  it("never beyond 12 hours", () => expect(axisEnd(0, 2000, 2000)).toBe(720));
});

describe("autoRange", () => {
  it("no hold: 20", () => expect(autoRange([], limOf)).toBe(20));
  it("a hold within 10 NM never zooms in", () => expect(autoRange([[4, -3]], limOf)).toBe(20));
  it("a hold at 60 NM: 80", () => expect(autoRange([[40, 44]], limOf)).toBe(80));
  it("a hold at 25 NM: 40", () => expect(autoRange([[18, 17]], limOf)).toBe(40));
  it("a hold beyond 80 NM: still 80", () => expect(autoRange([[150, 20]], limOf)).toBe(80));
});

describe("chipLabel", () => {
  const models = [{ id: "liquid/d1-ultra", label: "Liquid d1-ultra" }];
  const modeLabel = { formulas: "Formulas", decision: "Decision models" };
  const base = { models, mode: "formulas", model: "typesafe/jev-1.14", modeLabel };
  it("a replayed run names its recorded model", () =>
    expect(chipLabel({ ...base, fromReplay: true, result: { mode: "decision", model: "liquid/d1-ultra" } })).toBe("Liquid d1-ultra"));
  it("an unknown recorded model shows its id", () =>
    expect(chipLabel({ ...base, fromReplay: true, result: { mode: "decision", model: "x/y" } })).toBe("x/y"));
  it("an empty recorded model falls through to the mode", () =>
    expect(chipLabel({ ...base, fromReplay: true, result: { mode: "decision", model: "" } })).toBe("Decision models"));
  it("a recorded formulas run", () =>
    expect(chipLabel({ ...base, fromReplay: true, result: { mode: "formulas", model: null } })).toBe("Formulas"));
  it("a live run (not from a replay) shows the picker", () =>
    expect(chipLabel({ ...base, mode: "decision", fromReplay: false, result: { mode: "decision", model: "liquid/d1-ultra" } })).toBe("typesafe/jev-1.14"));
  it("no result shows the picker", () => expect(chipLabel({ ...base, fromReplay: true, result: null })).toBe("Formulas"));
});
