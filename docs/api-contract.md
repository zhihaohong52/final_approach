# API contract: Disruption Negotiator v2 (wave mode)

Status: **v2.3.1, 4 Nov 2026** (additive over v2.2.1: airline agents, fairness floor, pilot UNABLE; v2.3.1 marks escape-clause votes and the user override of the fairness pick; see the v2.3 and v2.3.1 notes at the end of this paragraph; the engine is `v5-taxi` (6 Nov 2026: taxi stop lines, gate holds, arrival runways outboard, crossing gap; no field changes) and the samples in `frontend/src/api/samples/` are regenerated from it; every v2.3 field is optional, so older recordings stay valid). Earlier history: **v2.0, 31 Oct 2026 13:40** (challenge rules_of_play: exactly 3 agents ATC / PILOT:<focus> / GROUND,
one `AGENT_TURN` message per agent per turn, max 3 turns; `MOVE`/`MOVE_REJECTED`/`COUNTER`/`VOTE` superseded;
result gains `revision`, `limits`, `turns`, `agents`, `focus`; `Wave.focus`; `GET /api/flights`; new rules
`ATC-RWY-CAPACITY`, `GND-GATE-NOTICE`; workbook limits). v1.2 (12:55): added `Triage`; `/api/eval` marked v1;
benchmark brier/latency/ablation nullable. Frozen at 12:30 for the frontend; any later change is
announced in the commit message with the prefix `contract:`. v2.1 (2 Nov 2026): `GET /api/wave/{id}` adds optional `status_quo` (the first-come-first-served plan and its rule check), so a wave shows a plan before any negotiation. v2.1.1 (3 Nov 2026, additive): `Config.store`, `persisted?` on the `POST /api/decision` response, and `GET /api/runs?wave_id=` (past runs of a wave). v2.2 (3 Nov 2026, additive, part 1 of the flight-paths-and-taxi build): `FlightMetrics` path fields, `Gate.lat/lon/geo_src`, `QueueArrival.origin`, `WaveScenario.engine_version` and `geo_available`, `NegotiationResult.engine_version` and `trajectories`, `BenchmarkReport.engine_version/coverage/taxi_validation`, `RuleId` `ATC-HOLD-STACK`/`ATC-TAXI-CONFLICT`, `Move.kind` `hold`, `Provenance.layer` `procedures|surface|ground_traffic`, and the `WaveGeo`/`TrajectorySet` types (endpoints `GET /api/wave/{id}/geo` and `POST /api/trajectories` land in later parts). v2.2 part 2 (3 Nov 2026, additive): `GET /api/wave/{id}/geo` (`WaveGeo`, procedures; `surface: null` until the surface milestone), `POST /api/trajectories` (`{wave_id, assignments}` or `{run_id}`), `EvaluateRequest.include_trajectories?` / `EvaluateResponse.trajectories?`, `NegotiationResult.paths_unavailable?` (replay engine-version guard), and `alt_src?` on `TrajectorySet` air points. v2.2 time base (engine `v3-time`): scheduled arrival is the BTS gate time (see Conventions), `delay_min` is measured at in-block against it, and `WaveFlight.taxi_in_ref_min?` is added. v2.2 surface (engine `v3-taxi`, additive): `in_block_min` = slot + ROT + unimpeded taxi routed on the OpenStreetMap taxiway graph (see Conventions); the `FlightMetrics` taxi fields (`exit`, `rot_s`, `taxi_unimpeded_min`, `taxi_wait_min` = 0, `taxi_min`) are filled; `Gate.lat/lon/geo_src` are filled from the OSM stand binding; `TrajectorySet.ground` holds one unimpeded taxi-in per landing wave flight (`waits` empty until `ATC-TAXI-CONFLICT`); `WaveScenario.provenance` gains the `surface` layer; `ATC-TAXI-ROUTE` can fail. `GET /api/wave/{id}/geo` fills `WaveGeo.surface` (taxiways, runways, stands with `gate`, holding_positions, attribution). v2.2 ground traffic (engine `v3-traffic`, additive): the plan's landing wave flights are scheduled on the taxi graph against the fixed traffic (queue arrivals and real 2015 departures) with hold-short / give-way waits; `taxi_wait_min` is filled, `taxi_min` = unimpeded + waits, `FlightMetrics.hold_short_strip?` is added; `TrajectorySet.ground` holds the wave tracks with their `waits` (wave flights only); the plan-independent fixed traffic is served once per wave in `WaveGeo.ground_traffic` (thinned tracks and runway events, cached with the `/geo` ETag); `ATC-TAXI-CONFLICT` is checked (14 rules), its failures fill `TrajectorySet.conflicts`, and informational `kind: "fixed_holds_for_landing"` / `"fixed_departure_holds"` entries say which fixed crosser or fixed departure (a takeoff less than 1 min before a plan landing on its strip) holds for a plan landing (landing priority, never a violation). v2.2 rejection paths (additive): `POST /api/trajectories {wave_id, assignments}` accepts an optional `move` (a `Move`, e.g. a `RejectedMove.move`) and returns the paths of the plan that move produces (`moves.apply`), so the surface map can show a rejected move's failing ground conflicts. v2.2.1 (4 Nov 2026, additive): `Config.rule_ids` (every rule the engine checks, `rules.RULE_IDS`), and `WaveSummary.window_end` of flights.csv waves is the window end (window_start + 1 h; it repeated window_start). Bad numbers in an `Assignment` (a JSON integer too large for a float, e.g. 10^400) or a callsign longer than 16 characters are `422 invalid_request`, never a 500. `POST /api/decision` v2 requires `run_id` to be a string of 1-64 characters and `edits` (optional) a list of at most 64 objects (64 KB of JSON); otherwise `422 invalid_request`. Spec: `docs/superpowers/specs/2026-11-02-flight-paths-and-taxi-design.md` §7. **v2.3 (4 Nov 2026, additive; airline agents):** `AgentId` adds `AIRLINE:<code>` (one voter per airline in the wave) and `AgentTurnBody.role` adds `"AIRLINE"`; every wave flight now has a pilot agent `PILOT:<callsign>` that never proposes or votes and only says `UNABLE` (new `MessageType`, `UnableBody`); `NegotiationResult.agents` lists the voters only, the pilots go in the new `NegotiationResult.pilots`; new `airlines` (`AirlineGroup[]`) on `WaveScenario`, `NegotiationResult` and the `OPEN` body; `NegotiationResult.fairness`; `Move.kind` `swap_own`; the fairness floor adds `FloorBreach`, `Objection`, `AgentTurnBody.objections?`, `Vote.decided_by?` / `Vote.floor?` and `RejectedMove.plan_id?`; `BenchmarkReport` gains `fairness?`, `arms[].summary.worse_than_status_quo_pct?` / `model_calls_per_wave?`, `fairness_variants?`. Runs recorded before v2.3 carry their own three-agent `agents` and none of the new fields; clients must accept both. **v2.3.1 (4 Nov 2026, additive):** `Vote.decided_by` adds `"escape"` (an airline's utility vote on a plan that breaches its floor when no plan on the table meets it), and such a vote also carries `floor`; `fairness_variants.pick` adds `auto_variant` (what the rule picked) and `override?: "user"` (set when `variant` was chosen by the user over the rule). Runs recorded earlier have no `"escape"` marker: an AIRLINE vote that is not `"floor"` on a plan listed in the same turn's `objections` is an escape vote. Spec: `docs/superpowers/specs/2026-11-04-airline-agents-design.md`. Design: `docs/superpowers/specs/2026-10-31-disruption-negotiator-design.md`.

The frontend (Vite + React + TS, `frontend/`) talks only to these endpoints. In dev, Vite proxies
`/api` to `http://localhost:8000`. In production the same FastAPI service serves `frontend/dist` at `/`.
Once the backend lands, the exact types live in `frontend/src/api/types.ts` and one real sample
response per endpoint in `frontend/src/api/samples/*.json`. Until then, build against this doc.

## Conventions

- JSON, `snake_case` keys. Times on the wave timeline are **minutes relative to the wave window
  start `T0`** (`*_min`, may be negative); every entity also carries ISO strings for display.
- `AgentId`: `"ATC" | "GROUND" | "AIRLINE:<code>" | "PILOT:<callsign>"`. Since v2.3 the **voters** are `ATC`, one
  `AIRLINE:<code>` per airline that has a flight in the wave (sorted by code) and `GROUND`
  (`NegotiationResult.agents`; `["ATC", "AIRLINE:AA", "GROUND"]` when one airline flies the wave). Consensus needs
  every voter. An airline agent controls its own flights (its `callsigns` in `WaveScenario.airlines`): own-flight
  moves and a swap of two of its own flights. Every wave flight also has a **pilot agent** `PILOT:<callsign>`
  (`NegotiationResult.pilots`, wave order): pilots never propose, move or vote, they only say `UNABLE` when a plan
  or instruction fails one of their rules (see `UnableBody`). The deterministic rule engine still decides legality;
  ATC sequences, GROUND assigns gates, and the engine checks all rules for all flights. Runs recorded before v2.3
  carry their own `agents` (`["ATC", "PILOT:<focus>", "GROUND"]`, the pilot of the focus flight voting) and no
  `pilots`/`airlines`: read `agents` from the run, never assume a roster. Non-agent senders: `"ENGINE"` (safety
  rules / protocol) and `"DECIDER"` (wave triage).
- Errors: non-2xx status with `{ "error": { "code": string, "message": string } }`.
  `429` adds `retry_after_s`. Rate limits: 10 live negotiations per 10 min per IP, and 500 a UTC day across all visitors (`ATM_DAILY_LIVE_MAX`); both answer `429 rate_limited`, the daily one with `retry_after_s` to the next UTC day. Recorded replays count against neither. The v1 `POST /api/negotiate` shares both limits (`429` with a `detail` and a `Retry-After` header).
- Every real-world fact carries a `Provenance` so the UI can badge real vs simulated.
- Time base (engine `v3-time`): BTS scheduled arrival times are gate (in-block) times (STA). `sched_min` is
  the scheduled in-block; `eta_earliest_min` is the earliest touchdown, `sched_min` + attributed BTS
  delays (late aircraft, carrier, weather, NAS) − the taxi-in reference (the flight's real `TAXI_IN`, else
  the airport's 2015 median, else 6 min). For a 2015 row that is the real wheels-on whenever the attributed
  delays sum to `ARRIVAL_DELAY` (security delay is not attributed). `delay_min` = max(0, in-block −
  `sched_min`). Fixed-traffic `QueueArrival.slot_min` is the real wheels-on, `sched + ARRIVAL_DELAY −
  TAXI_IN` (exact by the BTS definitions), else `sched − taxi-in reference`; the queue is still chosen by
  scheduled time, so a late fixed arrival may land well after the window.
- Taxi (engine `v3-taxi`): `in_block_min` = `slot_min` + ROT (`rot_s`, roll to the first forward exit past the required roll) + `taxi_min` (fastest route on the OSM taxiway graph from that exit to the gate's stand, 20 kt, 10 kt on taxilanes and turns > 30°, + 30 s to park). Tiers: `real` (OSM route, ground `src: "osm"`); `generic` (runway end missing from today's OSM, a wave below the surface quality gate, or a `synth` gate: straight line runway midpoint → gate × 1.4 at 15 kt, `src: "synth"`); no runway coordinates: the v2 6/10 min near/far taxi, no ROT, no ground track. `ATC-TAXI-ROUTE` evidence: `runway, exit, gate, stand, route, crossings, unimpeded_min, tier` (+ `rot_s`); it fails iff the tier is `real` and no exit of the runway end reaches the stand. Ground point `node` ids are strings.
- Ground traffic (engine `v3-traffic`): the wave's fixed traffic (queue arrivals; real BTS 2015 departures from a simulated stand, pushback at `WHEELS_OFF − TAXI_OUT`, then waiting in the holding bay at its holding point until line-up 30 s before takeoff: off the taxiway, resequenced by takeoff time, at most 3 per holding point, beyond which the queue backs up upstream; the bay wait is the track's last wait, its last point has no node) is scheduled once per wave and never yields. Per plan, the landing wave flights with a routed taxi move in runway-exit order along their fixed route and wait (node headway 30 s, no head-on edge use, runway crossing gap: no landing from 1.5 min before to 1 min after the time in the strip's protected zone, no takeoff from 1 min before it to 15 s after it) against the fixed traffic, the earlier wave flights and every landing of the plan. `taxi_min` = `taxi_unimpeded_min` + `taxi_wait_min`; `in_block_min` = slot + ROT + `taxi_min`. `ATC-TAXI-CONFLICT` (source "FAA JO 7110.65 §3-7-2 …") evidence: `waits, wait_min, crossings, exit_clear_min, next_landing_min` (the next landing or takeoff on the exit strip, either kind; `next_event` names it) + `tier`, and when relevant `hold_short_strip`, `blocking`, `landing_conflicts`, `reason`. It fails iff the total wait exceeds the 30-min horizon (or the route starts inside another strip's zone during a runway event), or the flight is still on the runway it exits (until `exit_clear_min`) at another flight's landing or takeoff on that strip (no margin, JO 7110.65 §3-10-3), or it lands while a fixed arrival is still on that runway (`landing_conflicts`). A fixed crosser whose scheduled crossing falls in a plan landing's gap window holds for it (landings have priority; the fixed 2015 traffic is not re-timed per plan): evidence `fixed_holds`, an informational conflict, never a failure. Waits alone never fail it. Without a routed taxi (generic or no-geometry tier) it passes with "ground conflicts not modelled (tier)". `TrajectorySet.conflicts` = the failing checks, located at the exit node (vacate / scheduler failures) or the touchdown point, plus the informational `fixed_holds_for_landing` entries at the crosser's hold and `fixed_departure_holds` entries at the departure's holding point. `WaveGeo.ground_traffic` (null without a real surface tier): `tracks` (fixed movers, `[t_min, lat, lon]` points, waits `[from, to, lat, lon, reason]`) and `runway_events` (strip → `[t_min, kind, callsign]`).

```ts
type AgentId = "ATC" | "GROUND" | `AIRLINE:${string}` | `PILOT:${string}`;
type Sender = AgentId | "ENGINE" | "DECIDER";

interface Provenance {
  layer: "flights" | "queue" | "runways" | "weather" | "aircraft" | "gates" | "fuel" | "crew" | "connections" | "gate_occupancy"
    | "procedures" | "surface" | "ground_traffic";   // v2.2: last three
  status: "real" | "simulated" | "generic";   // generic = template used because real data missing
  source: string;                            // e.g. "BTS On-Time 2024-07", "METAR KATL 151651Z", "FAA Registry"
  detail?: string | null;
}
```

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/config` | `Config` |
| GET | `/api/waves?airport=ATL&q=DL123&limit=50` | `WaveSummary[]` |
| GET | `/api/waves/random?airport=ATL` | `WaveSummary` |
| GET | `/api/flights?airport=ATL&q=DL123&limit=50&offset=0` | `{ flights: FlightRow[]; total: number }` (rows of the 2015 challenge flights.csv) |
| GET | `/api/wave/{wave_id}` | `WaveScenario` (`wave_id` may be a BTS wave id or `"F2015-<row_id>"`) |
| GET | `/api/wave/{wave_id}/geo` | `WaveGeo` (v2.2; gzip, `ETag` = sha1 of the body; 404 `geo_unavailable` / `unknown_wave`) |
| POST | `/api/trajectories` | `TrajectorySet` (v2.2; body `{wave_id, assignments}` or `{run_id}`; 409 `engine_mismatch`) |
| POST | `/api/evaluate` | `EvaluateResponse` (duty-manager edit re-check) |
| POST | `/api/negotiate-wave` | SSE stream of `NegotiationEvent` (or `NegotiationResult` with `stream:false`) |
| GET | `/api/runs?wave_id=ATL-2024-07-15-16&limit=20` | `{ runs: RunListItem[] }` (v2.1.1; newest first, `limit` 1..100, 422 `invalid_request` without `wave_id`) |
| GET | `/api/runs/{run_id}` | `NegotiationResult` (with full `log`) |
| POST | `/api/decision` | `{ ok: true, recorded_at: string, persisted?: boolean }` |
| GET | `/api/benchmark` | `BenchmarkReport` (404 until generated) |
| GET | `/api/eval` | v1 single-flight eval (legacy shape; not used by the React app) |
| GET | `/mcp` | MCP server (not used by the frontend) |

v1 single-flight endpoints (`/api/negotiate`, `/api/random`, …) stay as they are for `web/index.html`; the React app does not use them.

### GET /api/config

```ts
interface Config {
  version: string;
  jev_available?: boolean;            // legacy v1 flag, still sent
  model?: string;                     // narration model id (legacy v1 field)
  policies?: string[]; n_disruptions?: number;   // legacy v1 fields
  modes: Mode[];                       // available modes
  default_mode: Mode;
  models: ModelInfo[];                 // decision models, benchmark winner first
  default_model: string;
  llm_available: boolean;              // Claude narration available
  tracing: boolean;                    // LangSmith on
  replay_available: boolean;           // pre-recorded waves exist
  default_wave?: string | null;        // v2.3: the wave the app opens first (a recorded 2024 hour)
  store?: StoreStatus;                 // v2.1.1: where runs and decisions are kept
  rule_ids?: RuleId[];                 // v2.2.1: every rule the engine checks (rules.RULE_IDS)
}
interface StoreStatus {
  backend: "postgres" | "files";
  degraded: boolean;                   // true when Postgres is configured but unreachable (files in use)
  reason?: string | null;
  persistent: boolean;                 // false: runs and decisions are only in memory or on ephemeral disk
}
type Mode = "formulas" | "decision" | "decision+claude";
interface ModelInfo {
  id: string;                          // e.g. "liquid/d1-ultra", "typesafe/jev-1.14"
  provider: "openrouter" | "typesafe" | "formula";
  label: string;                       // "LiquidAI d1-ultra"
  available: boolean;
  benchmark_rank?: number;             // 1 = winner
}
```

### GET /api/waves, /api/waves/random

```ts
interface WaveSummary {
  id: string;                          // "ATL-2024-07-15-16"
  airport: { iata: string; icao: string; name: string };
  window_start: string;                // ISO local time
  window_end: string;
  n_flights: number;                   // negotiated flights (3-5)
  n_queue: number;                     // other real arrivals in the window
  callsigns: string[];
  disrupted_callsigns: string[];
  weather_summary: string;             // "21024G33KT 3SM TSRA, wet"
  replay_available: boolean;
}
```

### GET /api/flights

Rows of the challenge `flights.csv` (2015, cancelled rows skipped), filtered by destination airport
and free text (callsign, tail, origin, dest, wave id). Picking a row = the challenge input: its
`wave_id` `"F2015-<row_id>"` works everywhere a wave id is accepted (`GET /api/wave/{id}`,
`POST /api/negotiate-wave`); that row's flight becomes `WaveScenario.focus`.

```ts
interface FlightRow {
  row_id: number;
  wave_id: string;                     // "F2015-<row_id>"
  date: string;                        // "2015-01-01"
  carrier: string; flight: string;     // "AA", "AA1234"
  origin: string; dest: string;
  sched_arr: string;                   // "HH:MM" local
  arr_delay: number; weather_delay: number; late_aircraft_delay: number;   // minutes
}
```

### GET /api/wave/{wave_id}

```ts
interface WaveScenario {
  id: string;
  airport: Airport;
  window_start: string;                // ISO; T0 = this instant
  metar: Metar | null;
  runway_wind: RunwayWind[];           // one per runway end
  queue: QueueArrival[];               // fixed real traffic in the window (not negotiated)
  flights: WaveFlight[];               // negotiated flights
  gates: Gate[];
  severity?: Triage | null;                 // filled once a negotiation has triaged it
  focus: string | null;                // v2.0: callsign of the disruption flight;
                                       // null => the engine uses the most-delayed disrupted flight
  provenance: Provenance[];            // per layer for the whole wave
  status_quo?: { plan: JointPlan; evaluation: PlanEvaluation };  // v2.1: first come, first served and its rule check, shown before negotiating
  engine_version?: string;             // v2.2
  geo_available?: boolean;             // v2.2: runway ends have coordinates and the airport is placed
  airlines?: AirlineGroup[];           // v2.3: the airline agents' groups (Wave.to_dict)
}

// v2.3: one airline agent AIRLINE:<code> controls these wave flights. Several operating carriers can share an
// airline (e.g. OH flies for AA), so `callsigns` is the truth, not the callsign prefix.
interface AirlineGroup { code: string; name: string; callsigns: string[];
  basis: string }                      // why these callsigns are grouped, e.g. "no grouping row: operating carrier is its own airline"

interface Triage { score: number; levels: string[]; probabilities: number[]; confidence?: number }  // 0-based score

interface Airport {
  iata: string; icao: string; name: string; lat: number; lon: number;
  runways: Runway[];
}
interface Runway {
  id: string;                          // runway end, e.g. "08L"
  heading_true: number;                // degrees
  length_ft: number;
  open: boolean;
  closed_reason?: string | null;            // "crosswind 24 kt exceeds FAA 8400.9 §6d (wet, 15 kt)"
  // for the stretch map:
  le_lat?: number; le_lon?: number; he_lat?: number; he_lon?: number;
}
interface Metar {
  raw: string;                         // full METAR text
  station: string; observed_at: string;
  wind_dir_deg: number | null; wind_kt: number; gust_kt: number | null;
  visibility_sm: number | null; ceiling_ft: number | null;
  wx_codes: string[];                  // ["TSRA"]
  surface: "dry" | "wet";
}
interface RunwayWind {
  runway: string;
  headwind_kt: number; crosswind_kt: number; tailwind_kt: number;  // gust-inclusive crosswind
  limit_crosswind_kt: number; limit_tailwind_kt: number;
  limit_source: string;                // "FAA Order 8400.9 §6d (wet)" or "A320 demonstrated 38 kt"
  within_limits: boolean;
}
interface QueueArrival {
  callsign: string; aircraft_type: string | null; wake: Wake;
  runway: string; slot_min: number;
  origin?: string | null;               // v2.2
}
type Wake = "SUPER" | "HEAVY" | "B757" | "LARGE" | "SMALL";

interface WaveFlight {
  callsign: string;                    // "DL1234"
  carrier: string; airline: string; flight_no: number; tail: string | null;
  aircraft_type: string | null;        // "A321", null if unknown
  wake: Wake; gate_size: "narrow" | "wide";
  origin: string;
  sched_arr: string;                   // ISO local
  sched_min: number;                   // scheduled arrival vs T0 (BTS gate / in-block time)
  eta_earliest_min: number;            // earliest touchdown: sched + attributed delays - taxi-in reference
  airborne: boolean;                   // false => ground delay at origin possible
  disrupted: boolean;
  delays: { weather: number; late_aircraft: number; nas: number; carrier: number; arr_delay: number }; // real BTS minutes
  fuel: { extra_min: number; alternate: string; alternate_min: number; final_reserve_min: number };
  connections: { flight: string; pax: number; departs_min: number }[];
  scheduled_gate: string;
  crew_shift_end_min: number;
  next_departure_min: number;
  provenance: Provenance[];
  taxi_in_ref_min?: number | null;     // v2.2: real TAXI_IN, else airport 2015 median; absent = generic 6 min
}
interface Gate {
  id: string; size: "narrow" | "wide"; pier: string; remote: boolean;
  busy: { from_min: number; to_min: number; label: string }[];   // occupancy by non-wave aircraft
  lat?: number | null; lon?: number | null;   // v2.2
  geo_src?: "osm-ref" | "osm-near" | "osm-gate" | "osm-generic" | "synth" | null;
}
```

### Plans, moves, rules (shared by stream and results)

```ts
interface JointPlan {
  id: string;                          // stable hash of assignments
  label: string;                       // "DL1234 R08L T+6 A12 · AA88 R09R T+9 RS · …"
  assignments: Assignment[];           // one per WaveFlight
}
interface Assignment {
  callsign: string;
  action: "land" | "divert";
  runway?: string; slot_min?: number;  // landing slot vs T0
  gate?: string;                       // "RS" = remote stand
  speed_reduction_min: number;         // 0-8, absorbed en route
  ground_delay_min: number;            // >0 only if not airborne
  metrics: FlightMetrics;
}
interface FlightMetrics {
  hold_min: number; fuel_kg: number; delay_min: number; in_block_min: number | null;
  pax_protected: number; pax_total: number; overtime_min: number; next_dep_delay_min: number;
  // v2.2 path fields (all optional for v2.1 payloads)
  hold_laps?: number; hold_fix?: string | null; hold_level_ft?: number | null; lap_min?: number | null;
  vector_min?: number; route_min?: number | null; route_extra_min?: number;
  exit?: string | null; rot_s?: number | null;
  taxi_unimpeded_min?: number | null; taxi_wait_min?: number; taxi_min?: number | null;
  hold_short_strip?: string | null;    // v3-traffic: strip of the longest hold-short wait (null: none)
}
interface RuleCheck {
  rule: RuleId;
  owner: "ATC" | "PILOT" | "GROUND";
  callsign?: string;                   // flight the check is about
  passed: boolean;
  detail: string;                      // human-readable, cites values
  source: string;                      // "FAA Order 8400.9 §6d", "challenge2_operational_constraints.xlsx wake_separation ...", "ops rule"
  evidence: Record<string, string | number>;   // e.g. { metar: "...", crosswind_kt: 24, limit_kt: 15 }
}
type RuleId = "ATC-RWY-OPEN" | "ATC-WAKE-SEP" | "ATC-TAXI-ROUTE" | "ATC-WIND-LIMITS"
  | "ATC-RWY-CAPACITY"                 // v2.0: landings per physical runway in the rolling 60 min <= workbook cap
  | "PLT-EARLIEST-ETA" | "PLT-FUEL-RESERVE" | "GND-GATE-SIZE" | "GND-GATE-FREE" | "GND-CREW-DUTY"
  | "GND-GATE-NOTICE"                  // v2.0: gate change only if arrival >= T+20 (remote stand exempt)
  | "PLT-CREW-DUTY"                   // in-block (or alternate arrival) within the flight crew's duty end
  | "ATC-HOLD-STACK" | "ATC-TAXI-CONFLICT";   // v2.2
// v2.0: GND-CREW-DUTY never fails any more (workbook: overtime after the 8 h shift is allowed at 1.5x cost);
// it reports overtime. PLT-CREW-DUTY can veto; crew report time is assumed (scheduled departure - 60 min, see `limits`).

interface PlanEvaluation {
  plan_id: string;
  feasible: boolean;
  checks: RuleCheck[];                 // all rules, all flights
  utilities: Record<AgentId, number>;  // 0..1 per voter (v2.3: ATC, each AIRLINE:<code>, GROUND; formula decision model)
  totals: { delay_min: number; fuel_kg: number; hold_min: number; pax_protected_pct: number;
            overtime_min: number; diversions: number };
}
interface Move {
  id: string;
  agent: AgentId;
  kind: "swap_order" | "change_runway" | "shift_slot"                         // ATC
      | "speed_reduction" | "ground_delay" | "request_divert" | "accept"      // own flight (v2.3: AIRLINE; pilots do not move);
                                                                             // v2.0: ATC may also issue speed_reduction /
                                                                             // ground_delay for non-focus flights
      | "reassign_gate" | "remote_stand" | "swap_gates"     // GROUND
      | "hold"                                                                   // v2.2: ATC holds a flight in the stack
      | "swap_own";                                                              // v2.3: AIRLINE swaps the slots of two of its own flights
  callsigns: string[];
  params: Record<string, string | number>;   // { runway: "09R" } | { minutes: 6 } | { gate: "B4" }
  label: string;                       // "Swap landing order DL1234 ↔ AA88"
}
```

### POST /api/negotiate-wave

Request:

```ts
interface NegotiateRequest {
  wave_id: string;
  mode: Mode;
  model?: string;                      // primary decision model; default = Config.default_model
  replay?: boolean;                    // true = stream a pre-recorded run, no model calls; waves without a recording run live
  stream?: boolean;                    // default true
}
```

Response (`stream` true): `Content-Type: text/event-stream`. It's a POST, so use `fetch` with a
`ReadableStream` reader (or `@microsoft/fetch-event-source`), not `EventSource`. Each SSE frame:

```
event: <start|message|result|error>
data: <JSON>
```

```ts
type NegotiationEvent =
  | { event: "start";   data: { run_id: string; wave_id: string; mode: Mode; model_chain: string[]; started_at: string } }
  | { event: "message"; data: NegotiationMessage }
  | { event: "result";  data: NegotiationResult }
  | { event: "error";   data: { code: string; message: string } };

interface NegotiationMessage {
  seq: number;                         // 0,1,2,… in order
  round: number;                       // the turn: 0 = triage/opening, 1..3
  ts: string;
  sender: Sender;
  type: MessageType;
  body: MessageBody;                   // discriminated by `type`, see below
  rationale: string;                   // one line, shown under the message
  decision?: DecisionMeta;             // present when a model/formula made a decision
}
type MessageType = "TRIAGE" | "OPEN" | "AGENT_TURN" | "CHECK" | "CONSENSUS" | "NO_CONSENSUS"
  | "FALLBACK" | "MODEL_FALLBACK"
  | "UNABLE"                                                   // v2.3: a pilot refuses a plan or instruction
  | "MOVE" | "MOVE_REJECTED" | "COUNTER" | "VOTE";   // superseded in v2.0 (never emitted), kept for old runs

interface DecisionMeta {
  kind: "choice" | "noul" | "score" | "formula";
  model: string;                       // model that actually answered
  provider: "openrouter" | "typesafe" | "formula";
  latency_ms: number;
  probabilities?: Record<string, number>;   // choice/score distribution
  confidence?: number;
  fallback_from?: string[];            // models that failed before this one answered
}

type MessageBody =
  | { type: "TRIAGE"; score: number; levels: string[]; probabilities: number[]; confidence?: number }
  | { type: "OPEN"; plan: JointPlan; evaluation: PlanEvaluation;          // ENGINE: FCFS opening plan (incumbent of turn 1)
      agents: AgentId[]; focus: string;                                       // agents = voters
      pilots?: AgentId[]; airlines?: AirlineGroup[] }                         // v2.3
  | AgentTurnBody                                                          // v2.0: the ONE message per agent per turn
  | { type: "CHECK"; plan_id: string; passed: boolean; violations: RuleCheck[]; proposed_by?: AgentId }
  | { type: "CONSENSUS"; plan_id: string; accepted_by: AgentId[]; turn: number }
  | { type: "NO_CONSENSUS"; valid_plan_ids: string[]; next_reservation: Record<AgentId, number>;
      turn: number; reason: string }
  | { type: "FALLBACK"; plan_id: string; method: string; reason: string }  // reason: why no consensus (who rejected what)
  | { type: "MODEL_FALLBACK"; agent: AgentId; from_model: string; to_model: string; error: string }
  | UnableBody                                                             // v2.3
  // superseded in v2.0, never emitted by new runs:
  | { type: "MOVE"; move: Move; plan: JointPlan; options_count: number }
  | { type: "MOVE_REJECTED"; move: Move; violations: RuleCheck[] }
  | { type: "COUNTER"; plan: JointPlan; evaluation: PlanEvaluation }
  | { type: "VOTE"; votes: LegacyVote[] };

// v2.0: one structured message per agent per turn (sender = agent, round = turn)
interface AgentTurnBody {
  type: "AGENT_TURN";
  turn: number;                        // 1..3
  agent: AgentId;
  role: "ATC" | "AIRLINE" | "GROUND" | "PILOT";   // v2.3: AIRLINE; PILOT only in runs recorded before v2.3
  moves: AppliedMove[];                // moves applied this turn, in order (0-3), each engine-checked
  rejected: RejectedMove[];            // moves the engine vetoed this turn (agent re-picked)
  proposal: JointPlan;                 // the agent's plan for the table after its moves (metrics filled)
  proposal_utility: number;            // the agent's own utility for its proposal
  votes: Vote[];                       // on every CHECK-passed plan on the table when it speaks
  rationale: string;
  decision?: DecisionMeta;             // the vote decision (moves carry their own)
  objections?: Objection[];            // v2.3: AIRLINE turns only (empty when none): see below
}
interface AppliedMove { move: Move; utility_before: number; utility_after: number; decision: DecisionMeta }
interface RejectedMove { move: Move; violations: RuleCheck[]; reason: string; decision: DecisionMeta;
  plan_id?: string }                   // v2.3: id of the plan the vetoed move would have produced (the UNABLE `plan_id`)
interface Vote {
  plan_id: string;
  vote: "ACCEPT" | "REJECT";
  p_accept?: number;                   // absent in formulas mode
  utility: number; reservation: number;
  reason: string;
  decided_by?: "formula" | "floor" | "escape";
                                       // "formula": a model's P(accept) was uncertain (0.3-0.7), so the formula decided;
                                       // "floor" (v2.3, AIRLINE only): the fairness floor REJECTed it, no model was asked;
                                       // "escape" (v2.3.1, AIRLINE only): the plan breaches the airline's floor but no
                                       // CHECK-passed plan on the table meets it, so the airline voted on utility (the
                                       // escape clause); `vote` is ACCEPT or REJECT. Takes precedence over "formula"
                                       // (`reason` then still says the formula decided)
  floor?: FloorBreach[];               // v2.3: the breaches behind a `decided_by: "floor"` REJECT or (v2.3.1) a
                                       // `decided_by: "escape"` vote (>= 1)
}
// v2.3 fairness floor. An airline compares each of its flights' delay and fuel in a plan with the floor plan: the
// status quo (first come, first served) when it is feasible, else the best feasible plan seen so far. A flight
// breaches when `value > allowed`; `allowed` = floor + slack x the flight's cap + a small epsilon (delay 0.05 min,
// fuel 0.5 kg); slack is 0 for `fairness: "firm"` and 0.10 / 0.22 / 0.34 on turns 1 / 2 / 3 for `"relaxing"`. When some CHECK-passed plan on the table has no breach for the airline, it REJECTs every
// plan that has one (`Vote.decided_by: "floor"`); otherwise it votes on utility (the escape clause) and each vote on a
// breaching plan carries `decided_by: "escape"` and its `floor` breaches (v2.3.1). The AGENT_TURN rationale words that
// vote: "rejects the plan" only for a REJECT, "accepts it: no plan on the table meets its floor (escape clause)" for an
// escape ACCEPT.
interface FloorBreach {
  callsign: string;                    // an own flight of the airline
  metric: "delay_min" | "fuel_kg";
  floor: number;                       // the floor plan's value for that flight
  value: number;                       // the judged plan's value
  allowed: number;                     // the most the airline accepts this turn; breach iff value > allowed
  floor_source: "status_quo" | "best_seen";   // which plan `floor` comes from (best_seen when the status quo is infeasible)
}
// What an AIRLINE turn objects to: the breaches of every CHECK-passed plan another voter (or the engine) put on the
// table. Plans the airline proposed itself are not listed. Absent from ATC/GROUND turns.
interface Objection extends FloorBreach {
  plan_id: string;                     // the plan with the breach
  proposed_by: AgentId | "ENGINE";     // "ENGINE" = the opening plan
}
// v2.3: a pilot says it is UNABLE to fly a plan or an instruction. Pure formula (no model call), one per
// pilot-side failed rule (PLT-EARLIEST-ETA, PLT-FUEL-RESERVE, PLT-CREW-DUTY, ATC-WIND-LIMITS) of a move the engine
// vetoed, or of a plan that failed CHECK; deduped per (plan_id, instruction, callsign, rule). sender = `agent`,
// round = `turn`. A plan with an UNABLE can never win (it also fails CHECK); this is the refusal in the pilot's words.
interface UnableBody {
  type: "UNABLE";
  turn: number;                        // 0 = on the opening plan, else 1..3
  agent: `PILOT:${string}`;
  callsign: string;                    // the pilot's flight
  rule: RuleId; source: string;        // the failed check's rule and citation
  evidence: Record<string, string | number>;
  detail: string;                      // the failed check's detail
  instruction: Move | null;            // the vetoed move; null when the whole plan is refused
  plan_id: string;                     // the plan after the instruction (or the refused plan)
  proposed_by: AgentId | "ENGINE";     // who gave the instruction or proposed the plan
  text: string;                        // "UNABLE <instruction>: <evidence in words>, <RULE-ID>"
}
interface LegacyVote { plan_id: string; decision: "ACCEPT" | "REJECT"; p_accept?: number;
                       utility: number; reservation: number; reason: string }

interface NegotiationResult {
  run_id: string; wave_id: string; mode: Mode; model: string;
  outcome: "consensus" | "fallback";
  rounds: number;                      // = turns (kept for v1.x clients)
  turns: number;                       // v2.0: 1..3
  agents: AgentId[];                   // the voters, v2.3: ["ATC", "AIRLINE:<code>"..., "GROUND"] (old runs: ["ATC", "PILOT:<focus>", "GROUND"])
  pilots?: AgentId[];                  // v2.3: ["PILOT:<callsign>", ...] one per wave flight, wave order; they never vote
  airlines?: AirlineGroup[];           // v2.3: the airline grouping (same as WaveScenario.airlines)
  fairness?: "firm" | "relaxing";      // v2.3: the fairness-floor variant this run used
  focus: string;                       // v2.0: callsign of the disruption flight (its pilot is `PILOT:<focus>`)
  revision: Revision;                  // v2.0: the challenge output for the focus flight
  limits: Limit[];                     // v2.0: every limit used, kept or changed, with its source
  final_plan: JointPlan;
  evaluation: PlanEvaluation;          // all checks pass by construction
  baseline: { plan: JointPlan; evaluation: PlanEvaluation };  // status quo FCFS
  narration: { source: "claude" | "template"; model?: string; brief: string; lines: string[] } | null;
  model_errors: string[];
  stats: { model_calls: number; fallbacks: number; elapsed_ms: number };
  log?: NegotiationMessage[];          // included when stream=false and in GET /api/runs/{id}
  engine_version?: string;             // v2.2
  trajectories?: { final: TrajectorySet; baseline: TrajectorySet };   // v2.2; absent when paths_unavailable
  paths_unavailable?: boolean;         // v2.2: true on a replay recorded on another engine version
                                       // (engine_version then names that version, "v2" if unversioned)
}

interface Revision {
  callsign: string;
  action: "land" | "hold" | "ground_delay" | "divert";   // hold = airborne holding > 0
  revised_arrival: string | null;      // "HH:MM" local (window_start + slot); null when diverting
  revised_arrival_min: number | null;  // landing slot vs T0
  revised_arrival_t?: string;          // "T+12"
  runway: string | null; gate: string | null;
  scheduled_arrival: string;           // "HH:MM" local
  scheduled_gate: string;
  speed_reduction_min?: number; ground_delay_min?: number; hold_min?: number;
  alternate?: string | null;           // divert only
  delay_vs_schedule_min: number;       // max(0, in-block - scheduled arrival (BTS gate time)) >= 0; divert: estimated delay
  summary: string;                     // one line for the UI
}
interface Limit {
  limit: string;                       // "wake separation (same runway)"
  value: string;                       // "Heavy->any 120 s, ..."
  source: "workbook" | "real data" | "assumption";
  note: string;                        // what we changed / why
}
```

Message order (v2.3). Round 0: `TRIAGE`, `OPEN`, `UNABLE*` (pilots that cannot fly the opening plan). Each turn 1..3:
`AGENT_TURN(ATC)` → `UNABLE*` → `CHECK` (if its proposal is new) → `AGENT_TURN(AIRLINE:<code>)` → `UNABLE*` →
`CHECK` (each airline in turn, sorted by code, if its proposal is new) → `AGENT_TURN(GROUND)` → `UNABLE*` → `CHECK` → `CONSENSUS` or
`NO_CONSENSUS`. In short `ATC → (UNABLE*) → CHECK → AIRLINE:* each → (UNABLE*) → CHECK → GROUND → (UNABLE*) →
CHECK → CONSENSUS|NO_CONSENSUS`; an `UNABLE` follows an `AGENT_TURN` whose vetoed moves or new proposal failed a
pilot-side rule, and a gate move by GROUND can trip `PLT-CREW-DUTY`, so it can follow GROUND too. Exactly one
`AGENT_TURN` per voter (`agents`) per turn; pilots never take a turn. `FALLBACK` only after `NO_CONSENSUS` of turn
3. `MODEL_FALLBACK` can appear anywhere. Runs recorded before v2.3 keep their old order (`ATC → CHECK →
PILOT:<focus> → CHECK → GROUND → CHECK`, no `UNABLE`) and their own `agents`.

Protocol rules: in turn t ATC starts from the incumbent plan, each airline from the previous proposal in
order (ATC's, then the earlier airlines'), GROUND from the last airline's. Each voter votes on every CHECK-passed plan on the table when it speaks (incumbent, proposals
earlier in this turn, its own proposal). Consensus = one CHECK-passed plan with no pilot `UNABLE` ACCEPTed by all voters in
the same turn. Otherwise the best CHECK-passed plan of the turn becomes the next incumbent; after turn
3 the deterministic non-AI solver decides and `FALLBACK.reason` says why. Limits come from the
challenge workbook (`data/challenge2/challenge2_operational_constraints.xlsx`); every limit kept,
changed or added is listed in `NegotiationResult.limits`.

### GET /api/runs?wave_id=

```ts
interface RunListItem {
  run_id: string; wave_id: string; mode: Mode; model: string | null; outcome: string; created_at: string;
  decided: boolean;                    // a duty manager decision was recorded for this run
}
// response: { runs: RunListItem[] }
```

### POST /api/decision

```ts
interface DecisionRequest { run_id: string; decision: "APPROVE" | "OVERRIDE"; note?: string;  // note required for OVERRIDE
  edits?: Assignment[] }   // flights the duty manager moved before approving (logged with the decision)
// response: { ok: true; recorded_at: string; persisted?: boolean }   // v2.1.1; false when the decision was not saved durably
```

### POST /api/evaluate

The duty manager's edit: re-check a whole plan against the same 14 rules and scores the negotiation uses.

```ts
interface EvaluateRequest { wave_id: string; assignments: Assignment[];   // metrics ignored
  include_trajectories?: boolean }     // v2.2
interface EvaluateResponse {
  plan: JointPlan;                     // label "edited by the duty manager"
  evaluation: PlanEvaluation;
  suggestions: Record<string, number>; // callsign -> nearest slot_min (same runway, ±30 min) that makes the plan pass;
                                       // v2.2: never before the flight's earliest touchdown on that runway (its route)
  trajectories?: TrajectorySet;        // v2.2: the edited plan's paths, when include_trajectories is true
}
```
Errors: 422 `invalid_request` (empty or malformed assignments), 404 `unknown_wave`.

v2.2 input bounds (`POST /api/evaluate` and `POST /api/trajectories {wave_id, assignments}`), each a 422
`invalid_request` with a message naming the field: numbers must be finite (no NaN / ±Infinity); at most one
assignment per wave flight (no more rows than `flights`, no duplicate callsign); every callsign must be a
wave flight; `runway`, when given, must be one of the airport's runway ids; `slot_min` must lie in
[min(0, every flight's `sched_min` / `eta_earliest_min` and queue `slot_min`) − 60,
 max(60, the same times) + 240] (minutes vs T0; the window is 60 min); `speed_reduction_min` and
`ground_delay_min` must lie in [0, 240].

### GET /api/wave/{wave_id}/geo (v2.2)

`WaveGeo` (see "v2.2 types"): the procedures the engine flies for this wave, built from its own routes, so
the map never shows a path the engine did not use. `procedures.routes` are the distinct
(runway, STAR, transition, approach, named fixes) over every wave and queue flight and every open runway end;
`procedures.fixes` are their named fixes plus every hold fix (kind by priority
hold > corner > faf > if > iaf > entry > star; `star` also covers uncoded approach step-down fixes);
`procedures.holds` are the wave's shared holds, one per physical fix, with the racetrack `polygon`
(`[lat, lon]` pairs) the laps are drawn on. `cycle`/`source` come from the CIFP header (`"2611"`,
`"FAA CIFP"`), `""`/`"synthetic"` when the airport has no procedures; `status` is `"real"` when some route
flies a CIFP STAR and approach. `surface` is the OSM airport surface (`null` when the airport has none): `taxiways` / `runways` as drawn lines
(`[lat, lon]`), every `stands` entry labelled as `TrajectorySet.ground[].stand` is (OSM `ref`, else the OSM id; a
ref shared by several stands labels the gate-bound one, the others keep their OSM id) with the wave `gate` bound
to it and its `geo_src` (`osm` when no wave gate uses it), `holding_positions` (`HP<node>`, `runway` = the OSM
strip when known), `status` = the wave's taxi tier (`real`/`generic`), `source` = `OpenStreetMap <date>`,
`attribution` = "© OpenStreetMap contributors". A `generic`-tier airport (quality gate failed) returns `status: "generic"` with `stands: []`; `surface` is `null` only when the airport has no OSM surface. A ref shared by several stands is made unique as `<ref>/<osm id>` for all but one stand, identically in `stands[].id` and `ground[].stand`. ORD: about 330 KB JSON, about 77 KB gzipped.

`ETag` is the sha1 of the JSON body, so it changes with the engine version and with any procedures/route
data change: `"<sha1>"` for the identity body, `"<sha1>-gz"` for the gzipped one (`Cache-Control: no-cache`:
clients revalidate). `If-None-Match` uses the weak comparison (`W/` ignored) and `*` matches; a match is a
304. The body is gzipped when `Accept-Encoding` lists `gzip` (or `*`) with q > 0 (`Content-Encoding: gzip`,
`Vary: Accept-Encoding`); `POST /api/trajectories` and `GET /api/runs/{run_id}` are gzipped the same way. Errors: 404 `unknown_wave`; 404 `geo_unavailable` when the wave's
`geo_available` is false.

### POST /api/trajectories (v2.2)

Deterministic and model-free. Two request forms:

```ts
type TrajectoriesRequest =
  | { wave_id: string; assignments: Assignment[];     // any plan: an intermediate proposal or a duty-manager edit
      move?: Move }                                   // v2.2: apply this move first (e.g. a RejectedMove's `move`)
  | { run_id: string };                               // that run's agreed plan (= result.trajectories.final)
// response: TrajectorySet
```
With `move`, the response is the paths of the plan the engine's own `moves.apply` makes from `assignments` and
that move (the plan a vetoed move would have produced). The UI asks only for a veto that cites
`ATC-TAXI-CONFLICT` and draws only the failing `conflicts` of the flights that veto names. Its `assignments` are
the plan on screen when the rejection arrived, an approximation of the engine's working plan: from turn 2 the
engine starts from the incumbent (not the last `AGENT_TURN` proposal), and a turn's earlier applied moves are not
included. The move is bounded like the other inputs: `kind`, `agent` and `id` strings of ≤ 64 chars, `label` ≤ 200;
`callsigns` 1-2 strings of ≤ 16 chars; `params` at most 4 entries with keys of ≤ 64 chars, each value a string of
≤ 64 chars or a finite number, and every number (or numeric string) within ±1440; the agent must have authority
for the kind and the move must apply to the plan (the negotiation's own checks), and the moved plan must satisfy
the input bounds below; otherwise 422 `invalid_request` (the message never echoes the input).
Errors: 422 `invalid_request` (neither form, empty or malformed assignments, a bad `move`), 404 `unknown_wave`,
404 `unknown_run`, 409 `engine_mismatch` (the run has `paths_unavailable`, or was recorded on another engine
version, or carries no trajectories). A hold is drawn with at most 12 laps (`hold.laps` ≤ 12); any
remaining delay is drawn as vector stretch, so the threshold is still crossed at `slot_min`.

### GET /api/benchmark

```ts
interface BenchmarkReport {
  generated_at: string; n_waves: number; seed: number;
  n_common?: number; wave_ids?: string[]; source?: string;   // waves every arm finished; sampled ids; bts | flights2015
  arms: {
    model: string; provider: string; label: string;
    summary: {
      consensus_rate_pct: number; mean_rounds: number; fallback_rate_pct: number;
      rule_violations_in_agreed_plans: number;
      delay_min: number; fuel_kg: number; pax_protected_pct: number; overtime_min: number;
      agreement_with_formula_pct: number; brier: number | null;
      latency_p50_ms: number | null; latency_p95_ms: number | null; error_rate_pct: number; cost_usd_per_wave: number;
      worse_than_status_quo_pct?: number | null;   // v2.3: WAVE-TOTAL share: % of waves with a feasible status quo whose final plan (consensus or fallback) has a worse wave-total delay (> +0.05 min) or fuel (> +0.5 kg); null: no such wave. The only share v3-traffic archives hold
      worse_flight_pct?: number | null;            // v2.3: PER-FLIGHT share: % of agreed (consensus) waves with a feasible status quo where any flight's delay or fuel is worse than in the status quo (same tolerances); null: no such wave. The fairness pick ranks on it
      model_calls_per_wave?: number;               // v2.3: mean of stats.model_calls
    };
    delay_min_vs_feasible_status_quo?: number;   // arm mean delay on waves where status quo is legal
    n_ok?: number; failed_ids?: string[]; p95_all_ms?: number | null; model_decisions?: number;
    worse_than_sq_consensus_pct?: number | null;   // v2.3: the wave-total share (worse_than_status_quo_pct) over consensus plans only
    infeasible_sq_waves?: number; consensus_pct_infeasible_sq?: number | null;   // v2.3: waves whose status quo is infeasible, and the consensus rate on them
  }[];
  baseline: { delay_min: number; fuel_kg: number; pax_protected_pct: number; overtime_min: number; diversions: number;
    n_feasible?: number; infeasible_ids?: string[];   // status quo legal on n_feasible waves
    feasible_only?: { delay_min: number; fuel_kg: number; pax_protected_pct: number; overtime_min: number; diversions: number } };
  ablation_no_engine_unsafe_pct: number | null;   // null = not run
  outage_test: { primary_disabled: string; waves: number; resolved: number; fallbacks_used: Record<string, number> };
  selection: { winner: string; rule: string; reasons: string[] };
  failures: { wave_id: string; model: string; reason: string }[];   // clickable: open the wave
  engine_version?: string;             // v2.2
  coverage?: { procedures: Record<string, number>; surface: Record<string, number>;
    ground_traffic: Record<string, number> };   // v2.2: per layer, tier -> wave count (sums to n_waves)
  taxi_validation?: Record<string, unknown>;   // v2.2: filled by the taxi validation step
  fairness?: "firm" | "relaxing";      // v2.3: the fairness-floor variant the arms ran with
  fairness_variants?: {                // v2.3: the firm vs relaxing comparison and the pick (benchmark_notes.py --variants)
    firm: FairnessVariantReport; relaxing: FairnessVariantReport;
    pick: { variant: "firm" | "relaxing" | null; rule: string; reasons: string[]; rows: Record<string, unknown>[]; shortfall: boolean;
            auto_variant?: "firm" | "relaxing"; override?: "user" };   // shortfall: neither variant kept consensus within one wave of v3-traffic, so the higher mean consensus won; variant is null only in reports written before this field. v2.3.1: auto_variant = the rule's pick; override "user" = variant (the shipped one) was chosen by the user over the rule
  };
}
interface FairnessVariantReport {      // v2.3
  arms: { label: string; summary: BenchmarkReport["arms"][number]["summary"] }[];   // the per-arm summary (a subset of keys may be present)
}
```

`GET /api/eval` is the frozen v1 single-flight evaluation; the React app uses `/api/benchmark` only.

## Screens the contract is meant to support (from the design)

- **Hero:** runway + gate timeline. Lanes = runways (from `Airport.runways` that are open) and gates
  (`Gate[]`); bars = `queue` (grey, fixed) and wave flights (`Assignment.slot_min`, `in_block_min`);
  animate between consecutive `AGENT_TURN.proposal` plans; flash red on each `AGENT_TURN.rejected[]`
  item with `violations[0].detail`.
- Flight picker (`/api/flights`) and wave picker, mode + model selector, triage card, revision card
  (`revision.summary`), consensus log by turn (one card per `AGENT_TURN`; colour per agent;
  `decision` shows model, probability, latency, and `fallback_from` as a warning), per-flight
  provenance badges, wind table (`runway_wind`), decision panel (final plan per flight, utilities,
  full `checks` with `source` + `evidence`, before/after vs `baseline`, regressions in red), Approve /
  Override, Claude brief, Benchmark tab.
- Stretch: airport map from runway end coordinates + METAR wind arrow.

## v2.2 types (additive)

```ts
export interface WaveGeo {
  engine_version: string;
  procedures: { cycle: string; source: string; status: "real" | "generic";
    fixes: { id: string; lat: number; lon: number; kind: "entry" | "star" | "iaf" | "if" | "faf" | "hold" | "corner"; src: "cifp" | "synth" }[];
    routes: { runway: string; star?: string; transition?: string; approach?: string; fixes: string[] }[];
    holds: { fix: string; inbound_true: number; turn: "R" | "L"; leg_min: number; base_ft: number; levels_ft: number[]; lap_min: number; src: "cifp" | "synth"; polygon: [number, number][] }[] };
  surface: { source: string; status: "real" | "generic"; attribution: string;
    taxiways: { name: string | null; kind: "taxiway" | "taxilane"; line: [number, number][] }[];
    runways: { ids: string[]; line: [number, number][]; width_m: number | null }[];
    stands: { id: string; gate?: string; lat: number; lon: number; geo_src: string }[];
    holding_positions: { id: string; lat: number; lon: number; runway?: string }[] } | null;
  // v3-traffic: the wave's fixed ground traffic (plan-independent, drawn grey), null without a real surface tier
  ground_traffic?: { source: string;
    tracks: { callsign: string; kind: "arrival" | "departure"; runway: string; stand: string;
      points: [number, number, number][];                       // [t_min, lat, lon]
      waits: [number, number, number, number, string][] }[];    // [from_min, to_min, lat, lon, reason]
    runway_events: Record<string, [number, "landing" | "takeoff", string][]> } | null;   // strip -> [t_min, kind, callsign]
}
export interface TrajectorySet {
  plan_id: string; engine_version: string;
  air: { callsign: string; wave: boolean; runway: string; appears_min: number;
    points: { t_min: number; lat: number; lon: number; alt_ft: number; kind: "entry" | "fix" | "hold" | "vector" | "final" | "threshold"; fix?: string; src: "cifp" | "synth";
      alt_src?: "constraint" | "interp" }[];   // v2.2 part 2: published altitude, or interpolated/capped
    hold?: { fix: string; laps: number; level_ft: number; entry_min: number; exit_min: number; lap_min: number } }[];
  ground: { callsign: string; kind: "arrival" | "departure"; wave: boolean; exit?: string; stand: string; route_names: string[];
    points: { t_min: number; lat: number; lon: number; node?: string }[];
    waits: { lat: number; lon: number; from_min: number; to_min: number; reason: string }[];
    unimpeded_min: number; taxi_min: number; src: "osm" | "synth" }[];
  conflicts: { rule: RuleId; callsign: string; at_min: number; lat: number; lon: number; detail: string;
    // v3-traffic: absent = a failing check; otherwise informational, never a violation (the landing has priority):
    // "fixed_holds_for_landing" = the fixed crosser `fixed` would hold short of `strip` for `hold_min`;
    // "fixed_departure_holds" = the fixed departure `fixed` would hold for takeoff on `strip` for `hold_min`
    kind?: "fixed_holds_for_landing" | "fixed_departure_holds"; fixed?: string; strip?: string; hold_min?: number }[];
}
```
