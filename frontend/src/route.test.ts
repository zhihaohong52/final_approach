import { describe, expect, it } from "vitest";
import { parseRoute, waveHash } from "./route";

describe("parseRoute", () => {
  it("treats the bare wave routes as the home page", () => {
    expect(parseRoute("")).toEqual({ screen: "wave", page: "home", waveId: null });
    expect(parseRoute("#/wave")).toEqual({ screen: "wave", page: "home", waveId: null });
  });
  it("reads a wave id on the home page", () => {
    expect(parseRoute("#/wave/F2015-152758")).toEqual({ screen: "wave", page: "home", waveId: "F2015-152758" });
  });
  it("reads the details page, with and without a wave id", () => {
    expect(parseRoute("#/wave/details")).toEqual({ screen: "wave", page: "details", waveId: null });
    expect(parseRoute("#/wave/details/F2015-152758")).toEqual({ screen: "wave", page: "details", waveId: "F2015-152758" });
  });
  it("reads the benchmark", () => {
    expect(parseRoute("#/benchmark").screen).toBe("benchmark");
  });
  it("round-trips through waveHash", () => {
    for (const p of ["home", "details"] as const) expect(parseRoute(waveHash(p, "F2015-1"))).toEqual({ screen: "wave", page: p, waveId: "F2015-1" });
  });
});
