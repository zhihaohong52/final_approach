# Airline agents and pilot "unable": design

Status: draft for review, 4 Nov 2026. Branch `redesign/console` (engine `v3-traffic` today).

## 1. Why

Today a wave negotiation has three agents: ATC, GROUND and one PILOT for the disrupted ("focus") flight
(`backend/wave/utility.py:79 negotiators`, `backend/wave/graph.py` docstring). The other 3-4 negotiated
flights have nobody speaking for them. ATC can push them later or onto another runway, and as long as
the plan stays legal nothing objects.

The 4 Nov stress test showed the cost. On the two most disrupted 2024 waves the agreed plan is worse
than the airline's original plan, and ATC's own utility is the only one that moved:

| Wave | Delay (original → agreed) | Fuel | What ATC did | Utilities (ATC / PILOT / GROUND) |
|---|---|---|---|---|
| CLT-2024-07-29-19 | 1,053 → 1,086 min | +1,376 kg | AA2712 and AA2762 to 18C, each +17 min | 0.73→0.92 / 0.45→0.45 / 0.65→0.65 |
| ATL-2024-07-18-21 | 1,216 → 1,225 min | +448 kg | DL2389 to 09L, +11 min | 0.74→0.94 / 0.45→0.45 / 0.65→0.65 |

The user's direction: the flights that lose out should object and negotiate their own delay.

## 2. What real operations do (the model we copy)

- **Pilots don't bargain over slots.** A pilot accepts an ATC instruction or says "unable", and says
  "unable" only for safety: not enough fuel, a runway or wind the aircraft can't take, weather below
  its minimums. A pilot never trades slots with another aircraft.
- **Airlines bargain over delay.** Operational control is shared by the captain and the airline's
  dispatcher (14 CFR 121.533). When arrivals must be delayed, the airline's operations centre decides
  which of its own flights absorbs it (FAA Collaborative Decision Making, slot substitution), because
  only it sees connections, crew duty limits and the aircraft's next departure.

## 3. The design

### 3.1 Agents

| Agent id | Who | Speaks | Can do |
|---|---|---|---|
| `ATC` | approach / tower | every turn | as today: sequence, runway, slot, hold, speed, ground delay |
| `GROUND` | ramp / gates | every turn | as today: gates, stands |
| `AIRLINE:<code>` | one per airline with a negotiated flight in the wave | every turn | bargain for its own flights (below) |
| `PILOT:<callsign>` | one per negotiated aircraft | only to refuse | say "unable" to an instruction for its own aircraft |

- **Airline grouping:** regional carriers count under the major whose name they fly, e.g. OH (PSA) →
  AA, MQ (Envoy) → AA, 9E (Endeavor) → DL, EV (ExpressJet) → DL or UA, OO (SkyWest) → by its
  marketing partner. The mapping is a data table with its source; where the data has no marketing
  carrier, the operating carrier is its own airline. *Builder:* find the marketing carrier in the
  data we have (BTS on-time has the operating carrier only; check whether flights2015 / the 2024
  BTS rows or the callsign prefix give the partner), else use a stated static table for 2015/2024.
- **Typical count:** 1-3 airline agents per wave (CLT 29 Jul: AA only; ATL 18 Jul: DL only).

### 3.2 Airline agent

- **Utility:** its own flights only. The terms the focus PILOT weighs today (delay, holding fuel,
  passenger connections, crew overtime, next departure), summed over its flights, so it can trade
  one of its flights' delay against another's.
- **Moves:** the moves the PILOT role has today (`speed_reduction`, `ground_delay`, `request_divert`,
  `accept`) for any of its flights, plus a new **`swap_own`**: swap the landing order / slots of two of
  its own flights on the same runway (slot substitution). The engine re-checks every move as today.
- **Counter-offers:** when a proposal on the table makes one of its flights worse, its AGENT_TURN says
  which flight, by how much, and what it proposes instead (its own moves, or rejecting the plan).
- **Decider:** like ATC and GROUND today: model chain, falling back to the local formula.

### 3.3 Pilot agent ("unable")

- One per negotiated aircraft, including the focus flight. The focus flight's bargaining moves to its
  airline agent.
- **Runs on formulas only** (no model calls).
- **What it refuses** (*my call*): only rules whose check `source` is the pilot side (`source="PILOT"`,
  e.g. `PLT-FUEL-RESERVE`) plus the aircraft-limit checks (crosswind/tailwind limit, runway length) for
  its own callsign. Not wake separation, gates or capacity: a real pilot doesn't refuse over those.
- **When it speaks:** (a) on a plan on the table that gives its aircraft such an instruction, and (b) on
  a move the engine rejects because of such a rule for its aircraft (today these appear only as the
  agent's `rejected` list). Case (b) is the common one, because the engine already rejects moves that
  add a violation; logging it as the pilot's refusal is what makes UNABLE visible.
- **Message:** an `UNABLE` entry naming the instruction, the rule and the evidence ("UNABLE hold
  3 laps at WATSN: reserve 18 min < 30 min, PLT-FUEL-RESERVE"). A plan with an UNABLE can't win.
- It doesn't vote on plans otherwise and never proposes moves.
- *Note:* the rules engine already rejects these plans; the pilot agent makes the refusal visible in
  the log, attributed to the aircraft, as a real pilot's would be.

### 3.4 Agreement

- **Consensus:** one CHECK-passed plan ACCEPTed in the same turn by ATC, GROUND and every airline agent,
  with no UNABLE from any pilot. Best max-min utility if several, as today.
- **Fairness floor, the point of the change.** "The original plan" is `baseline.status_quo` (the plan the
  result reports as the baseline). An airline agent compares each of **its own flights' delay and fuel**
  directly against that plan, not only its composite utility. When `status_quo` is infeasible, the floor
  is the best feasible plan seen so far in the negotiation (*my call*).
  - **Two variants, the benchmark picks (user's decision):**
    - *Firm:* the airline rejects any plan where one of its flights' delay or fuel is worse than the floor,
      unless no CHECK-passed plan on the table meets it.
    - *Relaxing:* the floor loosens each turn on the same schedule as today's reservations
      (graph.py `reservation`: x0.9 on turn 1, -0.12 per turn).
  - Build both behind one engine setting. Run the benchmark with each. Ship the variant that most reduces
    agreed plans worse than `status_quo` (delay or fuel) without the consensus rate falling below
    v3-traffic's; on a tie, the firm one. Both sets of numbers go in BENCHMARK.md and the README.
- **Turns and fallback:** unchanged: at most 3 turns, then the deterministic fallback, which logs who
  held out and why.
- **Turn order:** ATC → airline agents (alphabetical) → GROUND, pilots speaking right after the agent
  whose proposal they refuse. *Builder:* this is a suggestion; keep the chaining rule (each agent starts
  from the previous proposal) and choose what reads best in the log.

### 3.5 Visible to the user

- The negotiation log shows each airline agent's turn and each UNABLE, with the flight named.
- The frontend lists the agents of the run (the API's `agents` list already carries them), lays out the
  extra airline turns in the log, and shows UNABLE lines distinctly. Strips/radar labels need no change.
- **Frontend is in scope:** the user put all of `frontend/` in scope for this branch (2 Nov 2026), so
  the plan builds the UI part too, not only the contract and samples.

## 4. Constraints

- **Challenge rule deviation, stated:** the workbook's `rules_of_play` sheet says "One turn = each of the
  three agents sends one structured message." This design has more than three agents. The README and
  the demo say so plainly, with the reason (realism: §2) and the stress-test evidence (§1).
  *My call:* this is the default mode, not a switch. The user can ask for a switch.
- **Safety unchanged:** the deterministic rules engine remains the only judge of legality. No agent can
  argue a plan past a failing rule.
- **Cost:** pilot agents make no model calls. Airline agents add one model decision per airline per
  turn (1-3 airlines, so 1.3-2x today's calls per turn). The benchmark reports calls and latency.
- **Contract:** additive, in a `contract:` commit: new agent ids in `agents`, the `UNABLE` entry, the
  `swap_own` move kind, `Wave.airlines` (or equivalent). Old replays keep working (they carry their own
  agents list).
- **Engine version bump** (e.g. `v4-airlines`): replays and the benchmark are re-recorded; the new
  row is compared with v3-traffic.
- **MCP server** exposes the new agent roles the same way as the existing ones.

## 5. Success

- CLT-2024-07-29-19 and ATL-2024-07-18-21 no longer end with any airline's flight worse in delay or
  fuel than `baseline.status_quo`, or, if they do, the airline objected in the log and the fallback says
  why. Wave totals are reported alongside.
- On the benchmark, the share of agreed plans worse than the status quo (delay or fuel) drops versus
  v3-traffic; 0 rule violations in agreed plans in every arm, as now.
- The demo replays show at least one airline counter-offer; UNABLE appears wherever a pilot-side rule
  rejects a move for that aircraft (it may be absent from a replay where none does).
- Model calls per wave and p95 latency are reported; the cold ORD build stays ≤ 1000 ms.

## 6. Non-goals and anti-goals

- No pilot-to-pilot bargaining; no pilot agents with model calls.
- No change to how waves are built (one disrupted flight in 2015 waves; up to 5 in 2024 waves).
- No change to the rules or their limits.
- Anti-goal: agreement becoming rare. If airline agents make consensus collapse (most waves ending in
  fallback), that is a failure even if each objection is fair; report the consensus rate per arm.

## 7. Left to the builder

- The airline-mapping source (§3.1), the exact airline utility weights, the `swap_own` limits (how far
  apart the two slots may be), the turn order, and how the frontend lays out more agents in the log.
- Whether the focus flight's airline gets extra weight for the disrupted flight.
