import { describe, expect, it } from "vitest";
import { effectiveChoice, shownPanel } from "./panel";

const base = { choice: null, surface: "real" as const, canBoth: false, touchdown: 62, t: 60 };
describe("shownPanel", () => {
  it("without a surface the radar is the only picture, whatever was picked", () => {
    for (const choice of [null, "auto", "radar", "surface", "both"] as const)
      expect(shownPanel({ ...base, choice, surface: null, canBoth: true, t: 70 })).toBe("radar");
  });
  it("hands off from the radar to the surface at the focus touchdown", () => {
    expect(shownPanel({ ...base, t: 61.99 })).toBe("radar");
    expect(shownPanel({ ...base, t: 62 })).toBe("surface");
    expect(shownPanel({ ...base, touchdown: null, t: 99 })).toBe("radar");
  });
  it("a generic surface never takes over automatically, but can be picked", () => {
    expect(shownPanel({ ...base, surface: "generic", t: 70 })).toBe("radar");
    expect(shownPanel({ ...base, surface: "generic", choice: "surface" })).toBe("surface");
  });
  it("both side by side by default on a wide screen; a pick wins", () => {
    expect(shownPanel({ ...base, canBoth: true })).toBe("both");
    expect(shownPanel({ ...base, canBoth: true, choice: "radar", t: 70 })).toBe("radar");
    expect(shownPanel({ ...base, canBoth: true, choice: "auto", t: 70 })).toBe("surface");
  });
  it("both on a screen that became narrow falls back to the hand-off", () => {
    expect(effectiveChoice({ choice: "both", surface: "real", canBoth: false })).toBe("auto");
    expect(shownPanel({ ...base, choice: "both", t: 63 })).toBe("surface");
  });
});
