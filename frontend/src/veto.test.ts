import { describe, expect, it } from "vitest";
import type { RuleId } from "./api/types";
import { vetoConflicts } from "./veto";
const conflict = (callsign: string, kind?: "fixed_holds_for_landing") => ({ rule: "ATC-TAXI-CONFLICT" as const, callsign, at_min: 1, lat: 0, lon: 0, detail: callsign, ...(kind ? { kind } : {}) });
const v = (rule: RuleId, callsign?: string) => ({ rule, callsign });
describe("vetoConflicts", () => {
  it("is null unless the veto cites ATC-TAXI-CONFLICT (no request, nothing drawn)", () => {
    expect(vetoConflicts([v("ATC-WAKE-SEP", "AA1")], [conflict("AA1")])).toBeNull();
    expect(vetoConflicts([], [conflict("AA1")])).toBeNull();
  });
  it("keeps only failing conflicts of the flights the taxi veto names", () => {
    const got = vetoConflicts([v("ATC-TAXI-CONFLICT", "AA1"), v("ATC-WAKE-SEP", "BB2")], [conflict("AA1"), conflict("BB2"), conflict("AA1", "fixed_holds_for_landing")]);
    expect(got?.map((c) => c.callsign)).toEqual(["AA1"]);
  });
  it("without the conflicts (no request made yet) it still says whether to ask", () => {
    expect(vetoConflicts([v("ATC-TAXI-CONFLICT", "AA1")])).toEqual([]);
  });
});
