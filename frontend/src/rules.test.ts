import { describe, expect, it } from "vitest";
import { namesFlight, rulesCount } from "./rules";
const chk = (detail: string, evidence: Record<string, string | number> = {}) => ({ callsign: "X1", detail, evidence });
describe("namesFlight", () => {
  it("matches a whole callsign in the detail, never a prefix of a longer one", () => {
    expect(namesFlight(chk("AA260 behind DL5"), "AA26")).toBe(false);
    expect(namesFlight(chk("AA26 lands 1.2 min behind AA260"), "AA26")).toBe(true);
    expect(namesFlight(chk("behind AA26, gap 1.0 min"), "AA26")).toBe(true);
  });
  it("reads the structured evidence keys the engine uses for the other flight", () => {
    expect(namesFlight(chk("too close", { leader: "AA26" }), "AA26")).toBe(true);
    expect(namesFlight(chk("too close", { blocked_by: "DL5, AA26" }), "AA26")).toBe(true);
    expect(namesFlight(chk("too close", { blocked_by: "AA260" }), "AA26")).toBe(false);
  });
  it("the check's own flight is always named", () => {
    expect(namesFlight(chk("no detail"), "X1")).toBe(true);
  });
});
describe("rulesCount", () => {
  it("says how many rules from the config and never guesses a number", () => {
    expect(rulesCount(["ATC-RWY-OPEN", "ATC-WAKE-SEP"])).toBe("2 safety rules");
    expect(rulesCount(undefined)).toBe("the safety rules");
    expect(rulesCount([])).toBe("the safety rules");
  });
});
