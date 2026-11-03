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
