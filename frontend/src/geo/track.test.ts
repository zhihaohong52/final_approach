import { describe, expect, it } from "vitest";
import { activeWait, bearing, runwayCrossings, endOfLine, entryFix, matchHold, holdLap, levelLabel, nmProjector, positionAt, projector, secondFloor, scanFloor, scanTimes, segmentDashed, trend } from "./track";
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

it("projector is metres east/north", () => {
  const p = projector(41.98, -87.9); const [x, y] = p(41.98 + 1 / 60, -87.9);
  expect(x).toBeCloseTo(0, 6); expect(y).toBeCloseTo(1852, 0);
});
it("projector scales longitude by cos(lat)", () => {
  const [x, y] = projector(60, 10)(60, 10 + 1 / 60);
  expect(x).toBeCloseTo(926, 0); expect(y).toBeCloseTo(0, 6);
});
describe("surface helpers", () => {
  it("secondFloor steps the surface picture once a second", () => {
    expect(secondFloor(10 + 30.9 / 60)).toBeCloseTo(10.5, 9);
    expect(secondFloor(10.5)).toBeCloseTo(10.5, 9);
  });
  it("bearing is true degrees from north", () => {
    expect(bearing([0, 0], [0, 1])).toBeCloseTo(0, 6);
    expect(bearing([0, 0], [1, 0])).toBeCloseTo(90, 6);
    expect(bearing([0, 0], [-1, 0])).toBeCloseTo(270, 6);
  });
  it("a runway designator sits at the end it is landed from", () => {
    const sw: [number, number] = [0, 0], ne: [number, number] = [700, 800];   // a 04/22 strip drawn SW -> NE
    expect(endOfLine("04R", sw, ne)).toBe(0);
    expect(endOfLine("22L", sw, ne)).toBe(1);
    expect(endOfLine("22L", ne, sw)).toBe(0);
    expect(endOfLine("36", [0, 0], [0, 100])).toBe(0);
  });
});
it("the radar's NM plane is the surface map's metre plane / 1852, and keeps the classic 60·cos(lat) NM", () => {
  const lat0 = 41.98, lon0 = -87.9, m = projector(lat0, lon0), n = nmProjector(lat0, lon0);
  for (const [la, lo] of [[42.3, -88.4], [41.5, -87.2], [41.98, -87.9]]) {
    const [e, no] = n(la, lo), [em, nm] = m(la, lo);
    expect(e).toBeCloseTo(em / 1852, 9); expect(no).toBeCloseTo(nm / 1852, 9);
    expect(e).toBeCloseTo((lo - lon0) * 60 * Math.cos((lat0 * Math.PI) / 180), 9);
    expect(no).toBeCloseTo((la - lat0) * 60, 9);
  }
});

describe("entryFix", () => {
  const air = { points: [{ t_min: 1 }, { t_min: 3, fix: "AAA" }, { t_min: 6, fix: "BBB" }, { t_min: 9, fix: "CCC" }] };
  const fixes = new Map([["AAA", { kind: "star" }], ["BBB", { kind: "entry" }], ["CCC", { kind: "faf" }]]);
  it("prefers a fix the procedures mark as an entry fix, with its own time", () => expect(entryFix(air, fixes)).toEqual({ fix: "BBB", t_min: 6 }));
  it("else the first named fix", () => expect(entryFix(air, new Map([["AAA", { kind: "star" }], ["CCC", { kind: "faf" }]]))).toEqual({ fix: "AAA", t_min: 3 }));
  it("without procedures it is the first named fix", () => expect(entryFix(air)).toEqual({ fix: "AAA", t_min: 3 }));
  it("ignores fixes the procedures do not know", () => expect(entryFix(air, new Map([["CCC", { kind: "faf" }]]))).toEqual({ fix: "CCC", t_min: 9 }));
  it("is null when no point is named", () => { expect(entryFix({ points: [{ t_min: 1 }] }, fixes)).toBeNull(); expect(entryFix({ points: [] })).toBeNull(); });
});

describe("matchHold", () => {
  const sq = (lat: number, lon: number): [number, number][] => [[lat - .01, lon - .01], [lat - .01, lon + .01], [lat + .01, lon + .01], [lat + .01, lon - .01]];
  const holds = [{ fix: "AAA", polygon: sq(41, -88) }, { fix: "BBB", polygon: sq(41, -87) }, { fix: "AAA", polygon: sq(42, -88) }];
  const h = { fix: "AAA", entry_min: 10, exit_min: 20 };
  const pt = (t_min: number, lat: number, lon: number, kind = "hold") => ({ t_min, lat, lon, kind });
  it("picks, among holds of the same name, the one the flight flew in", () => {
    expect(matchHold(holds, h, [pt(5, 0, 0, "fix"), pt(12, 42.005, -88), pt(15, 41.995, -88)])).toBe(2);
    expect(matchHold(holds, h, [pt(12, 41.005, -88)])).toBe(0);
  });
  it("only counts hold points between entry and exit", () => expect(matchHold(holds, h, [pt(2, 42, -88), pt(12, 41, -88), pt(15, 41, -88)])).toBe(0));
  it("without hold points uses the fix position, else the first of that name", () => {
    expect(matchHold(holds, h, [pt(1, 0, 0, "fix")], { lat: 42, lon: -88 })).toBe(2);
    expect(matchHold(holds, h, [])).toBe(0);
  });
  it("is no match farther than 5 NM, or for an unknown name", () => {
    expect(matchHold(holds, h, [pt(12, 41.5, -88)])).toBeNull();
    expect(matchHold(holds, { ...h, fix: "ZZZ" }, [pt(12, 41, -88)])).toBeNull();
  });
});
it("activeWait picks the wait covering t", () => {
  const w = [{ from_min: 1, to_min: 2, reason: "hold short 27L/09R for landing UA12", lat: 0, lon: 0 }];
  expect(activeWait(w, 1.5)?.reason).toContain("hold short"); expect(activeWait(w, 2.1)).toBeNull();
});
it("activeWait is null between waits and with none", () => {
  const w = [{ from_min: 1, to_min: 2, reason: "a", lat: 0, lon: 0 }, { from_min: 3, to_min: 4, reason: "give way to AA1", lat: 0, lon: 0 }];
  expect(activeWait(w, 2.5)).toBeNull(); expect(activeWait(w, 3)?.reason).toBe("give way to AA1"); expect(activeWait([], 1)).toBeNull();
});
describe("runwayCrossings", () => {
  const rwy: [number, number][][] = [[[-1000, 0], [1000, 0]], [[500, -2000], [500, 2000]]];
  it("times the crossing of a runway centreline", () => {
    const c = runwayCrossings([[10, 0, -200], [12, 0, 200]], rwy);
    expect(c).toHaveLength(1); expect(c[0].rw).toBe(0); expect(c[0].t).toBeCloseTo(11, 9);
  });
  it("does not count the runway the path starts or ends on", () => {
    expect(runwayCrossings([[0, 0, 0], [1, 0, -300], [2, 800, -300]], rwy).map((c) => c.rw)).toEqual([1]);
    expect(runwayCrossings([[0, 300, -300], [1, 300, 20]], rwy)).toEqual([]);
  });
});
