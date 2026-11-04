import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { NegotiationMessage } from "../api/types";
import { Log } from "./Log";

const msg = (seq: number, sender: string, body: unknown): NegotiationMessage => ({ seq, round: 1, sender, body } as never);
const render = (messages: NegotiationMessage[], extra = {}) => renderToStaticMarkup(createElement(Log, { messages, running: false, ...extra }));

describe("Log", () => {
  it("renders UNABLE as its own article with the full line and the source", () => {
    const text = "UNABLE hold 12 min: fuel reserve would fall to 28 min, PLT-FUEL-RESERVE";
    const html = render([msg(1, "PILOT:AA2712", { type: "UNABLE", turn: 1, agent: "PILOT:AA2712", callsign: "AA2712", rule: "PLT-FUEL-RESERVE", source: "14 CFR 121.639", evidence: {}, detail: "", instruction: null, plan_id: "p", proposed_by: "ATC", text })]);
    expect(html).toContain("msg unable");
    expect(html).toContain(text.replace("UNABLE", '<b class="unable-word">UNABLE</b>'));
    expect(html).toContain("14 CFR 121.639");
  });
  it("airline turn lists objections with the floor source", () => {
    const turn = { type: "AGENT_TURN", turn: 1, agent: "AIRLINE:AA", moves: [], rejected: [], votes: [], proposal_utility: 0.5, proposal: null, utilities: {},
      objections: [{ callsign: "AA2712", metric: "delay_min", floor: 10, value: 30, allowed: 11, plan_id: "x", proposed_by: "ENGINE", floor_source: "best_seen" }] };
    const html = render([msg(1, "AIRLINE:AA", turn)]);
    expect(html).toContain("vs the best plan seen so far");
    expect(html).toContain("Airline AA");
  });
  it("an old 3-agent turn still renders", () => {
    const turn = { type: "AGENT_TURN", turn: 1, agent: "PILOT:DL100", moves: [], rejected: [], votes: [], proposal_utility: 0.5, proposal: null, utilities: {} };
    expect(render([msg(1, "PILOT:DL100", turn)])).toContain("Pilot");
  });
  it("empty log: role cards come from the roster, pilots collapsed", () => {
    const html = render([], { agents: ["ATC", "AIRLINE:AA", "GROUND"], pilots: ["PILOT:AA1", "PILOT:AA2"], airlines: [{ code: "AA", name: "American Airlines Inc.", callsigns: ["AA1", "AA2"], basis: "" }] });
    expect(html).toContain("Airline AA");
    expect(html).toContain("2 pilots");
  });
});

describe("Log UNABLE styling", () => {
  it("sets the word UNABLE apart and keeps the rest of the line", () => {
    const html = render([msg(1, "PILOT:AA1", { type: "UNABLE", turn: 1, agent: "PILOT:AA1", callsign: "AA1", rule: "PLT-FUEL-RESERVE", source: "s", evidence: {}, detail: "", instruction: null, plan_id: "p", proposed_by: "ATC", text: "UNABLE hold 5 min, PLT-FUEL-RESERVE" })]);
    expect(html).toContain('<b class="unable-word">UNABLE</b> hold 5 min, PLT-FUEL-RESERVE');
  });
});

describe("Log vote wording (from vote data, not the rationale)", () => {
  const br = { callsign: "WN130", metric: "delay_min", floor: 30.7, value: 31.2, allowed: 30.75, floor_source: "best_seen" };
  const turn = (votes: unknown[], extra = {}) => ({ type: "AGENT_TURN", turn: 2, agent: "AIRLINE:WN", role: "AIRLINE", moves: [], rejected: [], votes,
    proposal_utility: 0.56, proposal: null, rationale: "ignored; rejects the plan", objections: [], ...extra });
  const floorVote = { plan_id: "4b2c73fe11", vote: "REJECT", utility: 0.564, reservation: 0.441, reason: "r", decided_by: "floor", floor: [br] };
  const at = (b: unknown, details: boolean) => render([msg(1, "AIRLINE:WN", b)], { details });

  it("a floor REJECT names the breach and never shows a utility comparison (details on)", () => {
    const html = at(turn([floorVote]), true);
    expect(html).toContain("rejects 4b2c73fe11 on its fairness floor: SWA130 delay +0.5 min vs the best plan seen");
    expect(html).not.toContain("0.56 &lt; 0.44");
    expect(html).not.toMatch(/0\.56 (&lt;|≥) 0\.44/);
  });
  it("a floor REJECT is counted as a floor rejection, not 'below its minimum' (details off)", () => {
    const html = at(turn([floorVote]), false);
    expect(html).toContain("rejects 1 on its fairness floor");
    expect(html).not.toContain("below its minimum");
  });
  it("a floor REJECT against the original plan says so", () => {
    const html = at(turn([{ ...floorVote, floor: [{ ...br, floor_source: "status_quo" }, br] }]), true);
    expect(html).toContain("vs the original plan (+1 more)");
  });
  it("an escape ACCEPT says it accepts below the floor because no plan on the table meets it", () => {
    const v = { plan_id: "4b2c73fe11", vote: "ACCEPT", utility: 0.564, reservation: 0.373, reason: "r", decided_by: "escape", floor: [br] };
    expect(at(turn([v]), true)).toContain("accepts 4b2c73fe11 below its fairness floor (SWA130 delay +0.5 min vs the best plan seen): no plan on the table meets it");
    const off = at(turn([v]), false);
    expect(off).toContain("accepts one below its fairness floor: no plan on the table meets it");
    expect(off).not.toContain("rejects");
  });
  it("an escape vote recorded before the marker is read from the turn's objections", () => {
    const v = { plan_id: "4b2c73fe11", vote: "ACCEPT", utility: 0.564, reservation: 0.373, p_accept: 0.98, reason: "model" };
    const obj = { ...br, plan_id: "4b2c73fe11", proposed_by: "ATC" };
    expect(at(turn([v], { objections: [obj] }), true)).toContain("accepts 4b2c73fe11 below its fairness floor (SWA130 delay +0.5 min vs the best plan seen)");
    // a turn with a floor REJECT is not an escape turn: the ACCEPT on a non-objected plan stays a plain accept
    const other = { ...v, plan_id: "f5ab8b1b14" };
    const html = at(turn([other, floorVote], { objections: [{ ...obj }] }), true);
    expect(html).not.toContain("accepts f5ab8b1b14 below");
  });
  it("a utility REJECT keeps the comparison; a model REJECT above the minimum does not claim '<'", () => {
    const u = { plan_id: "aaaaaaaaaa", vote: "REJECT", utility: 0.2, reservation: 0.4, reason: "r" };
    const b = { ...turn([u]), agent: "ATC", role: "ATC" };
    expect(render([msg(1, "ATC", b)], { details: true })).toContain("rejects aaaaaaaaaa (0.20 &lt; 0.40)");
    expect(render([msg(1, "ATC", b)], { details: false })).toContain("rejects 1 below its minimum");
    const m = { ...b, votes: [{ ...u, utility: 0.6, p_accept: 0.1 }] };
    expect(render([msg(1, "ATC", m)], { details: true })).toContain("rejects aaaaaaaaaa (the model&#x27;s call: 0.60 ≥ 0.40)");
  });
});
