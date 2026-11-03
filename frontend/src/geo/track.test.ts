import { describe, expect, it } from "vitest";
import { holdLap, levelLabel, positionAt, scanFloor, scanTimes, segmentDashed, trend } from "./track";
const pts = [{ t_min: 0, lat: 0, lon: 0 }, { t_min: 2, lat: 0, lon: 2 }, { t_min: 3, lat: 1, lon: 2 }];
describe("positionAt", () => {
  it("interpolates linearly within a segment", () => expect(positionAt(pts, 1)).toMatchObject({ lat: 0, lon: 1, seg: 0 }));
  it("is null outside the track", () => { expect(positionAt(pts, -0.1)).toBeNull(); expect(positionAt(pts, 3.1)).toBeNull(); });
  it("hits points exactly", () => expect(positionAt(pts, 3)).toMatchObject({ lat: 1, lon: 2 }));
  it("interpolates altitude when both ends carry one", () => {
    const a = [{ t_min: 0, lat: 0, lon: 0, alt_ft: 12000 }, { t_min: 4, lat: 0, lon: 4, alt_ft: 8000 }];
    expect(positionAt(a, 1)).toMatchObject({ lon: 1, alt_ft: 11000, seg: 0 });
    expect(positionAt(pts, 1)?.alt_ft).toBeUndefined();
  });
  it("is null for an empty track and handles a single point", () => {
    expect(positionAt([], 0)).toBeNull();
    expect(positionAt([{ t_min: 5, lat: 1, lon: 1 }], 5)).toMatchObject({ lat: 1, lon: 1, seg: 0 });
  });
  it("tolerates two points at the same time", () => {
    const d = [{ t_min: 0, lat: 0, lon: 0 }, { t_min: 1, lat: 0, lon: 1 }, { t_min: 1, lat: 0, lon: 1 }, { t_min: 2, lat: 0, lon: 2 }];
    expect(positionAt(d, 1)).toMatchObject({ lon: 1 });
    expect(positionAt(d, 1.5)).toMatchObject({ lon: 1.5, seg: 2 });
  });
});
it("scan steps are 4.8 s back from t", () => expect(scanTimes(10, 3, 4.8 / 60).map((x) => +x.toFixed(4))).toEqual([10, 9.92, 9.84]));
it("scanFloor snaps to the last radar update", () => {
  expect(scanFloor(10.1, 0.08)).toBeCloseTo(10.08, 9);
  expect(scanFloor(10.08, 0.08)).toBeCloseTo(10.08, 9);
});

describe("holdLap", () => {
  const track = { hold: { fix: "BENKY", laps: 3, level_ft: 12000, entry_min: 10, exit_min: 22, lap_min: 4 } };
  it("is null outside the hold and without one", () => {
    expect(holdLap(track, 9.9)).toBeNull();
    expect(holdLap(track, 22)).toBeNull();
    expect(holdLap({}, 12)).toBeNull();
  });
  it("counts laps from one", () => {
    expect(holdLap(track, 10)).toEqual({ lap: 1, of: 3 });
    expect(holdLap(track, 14.5)).toEqual({ lap: 2, of: 3 });
    expect(holdLap(track, 21.99)).toEqual({ lap: 3, of: 3 });
  });
  it("never reports more laps than planned", () => {
    expect(holdLap({ hold: { ...track.hold, exit_min: 23 } }, 22.5)).toEqual({ lap: 3, of: 3 });
  });
});

describe("display helpers", () => {
  it("trend arrow from altitude change", () => {
    expect(trend(12000, 11800)).toBe("↓");
    expect(trend(8000, 8200)).toBe("↑");
    expect(trend(12000, 12000)).toBe("");
    expect(trend(undefined, 11000)).toBe("");
  });
  it("level label in hundreds of feet, three digits", () => {
    expect(levelLabel(12000)).toBe("120");
    expect(levelLabel(2449)).toBe("024");
    expect(levelLabel(undefined)).toBe("");
  });
  it("a segment is dashed when either end is synthesized (a vector that joins a published fix is still a vector)", () => {
    expect(segmentDashed({ src: "cifp" }, { src: "synth" })).toBe(true);
    expect(segmentDashed({ src: "synth" }, { src: "cifp" })).toBe(true);
    expect(segmentDashed({ src: "cifp" }, { src: "cifp" })).toBe(false);
  });
});
