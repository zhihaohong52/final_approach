// Generated verbatim from docs/api-contract.md. Do not edit by hand; change the contract instead.

export type AgentId = "ATC" | "GROUND" | `PILOT:${string}`;
export type Sender = AgentId | "ENGINE" | "DECIDER";

export interface Provenance {
  layer: "flights" | "queue" | "runways" | "weather" | "aircraft" | "gates" | "fuel" | "crew" | "connections" | "gate_occupancy"
    | "procedures" | "surface" | "ground_traffic";   // v2.2: last three
  status: "real" | "simulated" | "generic";   // generic = template used because real data missing
  source: string;                            // e.g. "BTS On-Time 2024-07", "METAR KATL 151651Z", "FAA Registry"
  detail?: string | null;
}

export interface Config {
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
  store?: StoreStatus;                 // v2.1.1: where runs and decisions are kept
}
export interface StoreStatus {
  backend: "postgres" | "files";
  degraded: boolean;                   // true when Postgres is configured but unreachable (files in use)
  reason?: string | null;
  persistent: boolean;                 // false: runs and decisions are only in memory or on ephemeral disk
}
export type Mode = "formulas" | "decision" | "decision+claude";
export interface ModelInfo {
  id: string;                          // e.g. "liquid/d1-ultra", "typesafe/jev-1.14"
  provider: "openrouter" | "typesafe" | "formula";
  label: string;                       // "LiquidAI d1-ultra"
  available: boolean;
  benchmark_rank?: number;             // 1 = winner
}

export interface WaveSummary {
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

// v2.0: GET /api/flights -> { flights: FlightRow[]; total: number }  (rows of the challenge flights.csv)
export interface FlightRow {
  row_id: number;
  wave_id: string;                     // "F2015-<row_id>", accepted by GET /api/wave/{id} and POST /api/negotiate-wave
  date: string;
  carrier: string; flight: string;
  origin: string; dest: string;
  sched_arr: string;                   // "HH:MM" local
  arr_delay: number; weather_delay: number; late_aircraft_delay: number;
}
export interface FlightsResponse { flights: FlightRow[]; total: number }

export interface WaveScenario {
  id: string;
  airport: Airport;
  window_start: string;                // ISO; T0 = this instant
  metar: Metar | null;
  runway_wind: RunwayWind[];           // one per runway end
  queue: QueueArrival[];               // fixed real traffic in the window (not negotiated)
  flights: WaveFlight[];               // negotiated flights
  gates: Gate[];
  severity?: Triage | null;                 // filled once a negotiation has triaged it
  focus: string | null;                // v2.0: callsign of the disruption flight (its pilot is the PILOT agent)
  provenance: Provenance[];            // per layer for the whole wave
  engine_version?: string;             // v2.2
  geo_available?: boolean;             // v2.2: runway ends have coordinates and the airport is placed
  status_quo?: { plan: JointPlan; evaluation: PlanEvaluation };  // v2.1: first come, first served and its rule check, shown before negotiating
}

export interface Airport {
  iata: string; icao: string; name: string; lat: number; lon: number;
  runways: Runway[];
}
export interface Runway {
  id: string;                          // runway end, e.g. "08L"
  heading_true: number;                // degrees
  length_ft: number;
  open: boolean;
  closed_reason?: string | null;            // "crosswind 24 kt exceeds FAA 8400.9 §6d (wet, 15 kt)"
  // for the stretch map:
  le_lat?: number; le_lon?: number; he_lat?: number; he_lon?: number;
}
export interface Metar {
  raw: string;                         // full METAR text
  station: string; observed_at: string;
  wind_dir_deg: number | null; wind_kt: number; gust_kt: number | null;
  visibility_sm: number | null; ceiling_ft: number | null;
  wx_codes: string[];                  // ["TSRA"]
  surface: "dry" | "wet";
}
export interface RunwayWind {
  runway: string;
  headwind_kt: number; crosswind_kt: number; tailwind_kt: number;  // gust-inclusive crosswind
  limit_crosswind_kt: number; limit_tailwind_kt: number;
  limit_source: string;                // "FAA Order 8400.9 §6d (wet)" or "A320 demonstrated 38 kt"
  within_limits: boolean;
}
export interface QueueArrival {
  callsign: string; aircraft_type: string | null; wake: Wake;
  runway: string; slot_min: number;
  origin?: string | null;               // v2.2
}
export type Wake = "SUPER" | "HEAVY" | "B757" | "LARGE" | "SMALL";

export interface WaveFlight {
  callsign: string;                    // "DL1234"
  carrier: string; airline: string; flight_no: number; tail: string | null;
  aircraft_type: string | null;        // "A321", null if unknown
  wake: Wake; gate_size: "narrow" | "wide";
  origin: string;
  sched_arr: string;                   // ISO local
  sched_min: number;                   // scheduled arrival vs T0
  eta_earliest_min: number;
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
export interface Gate {
  id: string; size: "narrow" | "wide"; pier: string; remote: boolean;
  busy: { from_min: number; to_min: number; label: string }[];   // occupancy by non-wave aircraft
  lat?: number | null; lon?: number | null;   // v2.2
  geo_src?: "osm-ref" | "osm-near" | "osm-gate" | "osm-generic" | "synth" | null;
}

export interface JointPlan {
  id: string;                          // stable hash of assignments
  label: string;                       // "DL1234 R08L T+6 A12 · AA88 R09R T+9 RS · …"
  assignments: Assignment[];           // one per WaveFlight
}
export interface Assignment {
  callsign: string;
  action: "land" | "divert";
  runway?: string; slot_min?: number;  // landing slot vs T0
  gate?: string;                       // "RS" = remote stand
  speed_reduction_min: number;         // 0-8, absorbed en route
  ground_delay_min: number;            // >0 only if not airborne
  metrics: FlightMetrics;
}
export interface FlightMetrics {
  hold_min: number; fuel_kg: number; delay_min: number; in_block_min: number | null;
  pax_protected: number; pax_total: number; overtime_min: number; next_dep_delay_min: number;
  // v2.2 path fields (all optional for v2.1 payloads)
  hold_laps?: number; hold_fix?: string | null; hold_level_ft?: number | null; lap_min?: number | null;
  vector_min?: number; route_min?: number | null; route_extra_min?: number;
  exit?: string | null; rot_s?: number | null;
  taxi_unimpeded_min?: number | null; taxi_wait_min?: number; taxi_min?: number | null;
  hold_short_strip?: string | null;    // v3-traffic: strip of the longest hold-short wait (null: none)
}
export interface RuleCheck {
  rule: RuleId;
  owner: "ATC" | "PILOT" | "GROUND";
  callsign?: string;                   // flight the check is about
  passed: boolean;
  detail: string;                      // human-readable, cites values
  source: string;                      // "FAA Order 8400.9 §6d", "ICAO wake separation (time-based)", "ops rule"
  evidence: Record<string, string | number>;   // e.g. { metar: "...", crosswind_kt: 24, limit_kt: 15 }
}
export type RuleId = "ATC-RWY-OPEN" | "ATC-WAKE-SEP" | "ATC-TAXI-ROUTE" | "ATC-WIND-LIMITS"
  | "ATC-RWY-CAPACITY"                 // v2.0: landings per physical runway in the rolling 60 min <= workbook cap
  | "PLT-EARLIEST-ETA" | "PLT-FUEL-RESERVE" | "GND-GATE-SIZE" | "GND-GATE-FREE" | "GND-CREW-DUTY"
  | "GND-GATE-NOTICE"                  // v2.0: gate change only if arrival >= T+20 (remote stand exempt)
  | "PLT-CREW-DUTY"                   // in-block (or alternate arrival) within the flight crew's duty end
  | "ATC-HOLD-STACK" | "ATC-TAXI-CONFLICT";   // v2.2

export interface PlanEvaluation {
  plan_id: string;
  feasible: boolean;
  checks: RuleCheck[];                 // all rules, all flights
  utilities: Record<AgentId, number>;  // 0..1 for the 3 negotiating agents (formula decision model)
  totals: { delay_min: number; fuel_kg: number; hold_min: number; pax_protected_pct: number;
            overtime_min: number; diversions: number };
}
export interface EvaluateRequest { wave_id: string; assignments: Assignment[];
  include_trajectories?: boolean }     // v2.2
export interface EvaluateResponse {
  plan: JointPlan;
  evaluation: PlanEvaluation;
  suggestions: Record<string, number>; // callsign -> nearest legal slot_min on the same runway (never before its route-earliest)
  trajectories?: TrajectorySet;        // v2.2: when include_trajectories is true
}
// v2.2: POST /api/trajectories -> TrajectorySet (409 engine_mismatch for a run without paths)
export type TrajectoriesRequest =
  | { wave_id: string; assignments: Assignment[] }
  | { run_id: string };
export interface Move {
  id: string;
  agent: AgentId;
  kind: "swap_order" | "change_runway" | "shift_slot"                         // ATC
      | "speed_reduction" | "ground_delay" | "request_divert" | "accept"      // PILOT (own flight); ATC: speed/ground delay of non-focus flights
      | "reassign_gate" | "remote_stand" | "swap_gates"     // GROUND
      | "hold";                                                                  // v2.2: ATC holds a flight in the stack
  callsigns: string[];
  params: Record<string, string | number>;   // { runway: "09R" } | { minutes: 6 } | { gate: "B4" }
  label: string;                       // "Swap landing order DL1234 ↔ AA88"
}

export interface NegotiateRequest {
  wave_id: string;
  mode: Mode;
  model?: string;                      // primary decision model; default = Config.default_model
  replay?: boolean;                    // true = stream a pre-recorded run, no model calls
  stream?: boolean;                    // default true
}

export type NegotiationEvent =
  | { event: "start";   data: { run_id: string; wave_id: string; mode: Mode; model_chain: string[]; started_at: string } }
  | { event: "message"; data: NegotiationMessage }
  | { event: "result";  data: NegotiationResult }
  | { event: "error";   data: { code: string; message: string } };

export interface NegotiationMessage {
  seq: number;                         // 0,1,2,… in order
  round: number;                       // the turn: 0 = triage/opening, 1..3
  ts: string;
  sender: Sender;
  type: MessageType;
  body: MessageBody;                   // discriminated by `type`, see below
  rationale: string;                   // one line, shown under the message
  decision?: DecisionMeta;             // present when a model/formula made a decision
}
export type MessageType = "TRIAGE" | "OPEN" | "AGENT_TURN" | "CHECK" | "CONSENSUS" | "NO_CONSENSUS"
  | "FALLBACK" | "MODEL_FALLBACK"
  | "MOVE" | "MOVE_REJECTED" | "COUNTER" | "VOTE";   // superseded in v2.0 (never emitted), kept for old runs

export interface DecisionMeta {
  kind: "choice" | "noul" | "score" | "formula";
  model: string;                       // model that actually answered
  provider: "openrouter" | "typesafe" | "formula";
  latency_ms: number;
  probabilities?: Record<string, number>;   // choice/score distribution
  confidence?: number;
  fallback_from?: string[];            // models that failed before this one answered
}

export type MessageBody =
  | { type: "TRIAGE"; score: number; levels: string[]; probabilities: number[]; confidence?: number }
  | { type: "OPEN"; plan: JointPlan; evaluation: PlanEvaluation;          // ENGINE: FCFS opening plan (incumbent of turn 1)
      agents: AgentId[]; focus: string }
  | AgentTurnBody                                                          // v2.0: the ONE message per agent per turn
  | { type: "CHECK"; plan_id: string; passed: boolean; violations: RuleCheck[]; proposed_by?: AgentId }
  | { type: "CONSENSUS"; plan_id: string; accepted_by: AgentId[]; turn: number }
  | { type: "NO_CONSENSUS"; valid_plan_ids: string[]; next_reservation: Record<AgentId, number>;
      turn: number; reason: string }
  | { type: "FALLBACK"; plan_id: string; method: string; reason: string }  // reason: why no consensus (who rejected what)
  | { type: "MODEL_FALLBACK"; agent: AgentId; from_model: string; to_model: string; error: string }
  // superseded in v2.0, never emitted by new runs:
  | { type: "MOVE"; move: Move; plan: JointPlan; options_count: number }
  | { type: "MOVE_REJECTED"; move: Move; violations: RuleCheck[] }
  | { type: "COUNTER"; plan: JointPlan; evaluation: PlanEvaluation }
  | { type: "VOTE"; votes: LegacyVote[] };

// v2.0: one structured message per agent per turn (sender = agent, round = turn)
export interface AgentTurnBody {
  type: "AGENT_TURN";
  turn: number;                        // 1..3
  agent: AgentId;
  role: "ATC" | "PILOT" | "GROUND";
  moves: AppliedMove[];                // moves applied this turn, in order (0-3), each engine-checked
  rejected: RejectedMove[];            // moves the engine vetoed this turn (agent re-picked)
  proposal: JointPlan;                 // the agent's plan for the table after its moves (metrics filled)
  proposal_utility: number;            // the agent's own utility for its proposal
  votes: Vote[];                       // on every CHECK-passed plan on the table when it speaks
  rationale: string;
  decision?: DecisionMeta;             // the vote decision (moves carry their own)
}
export interface AppliedMove { move: Move; utility_before: number; utility_after: number; decision: DecisionMeta }
export interface RejectedMove { move: Move; violations: RuleCheck[]; reason: string; decision: DecisionMeta }
export interface Vote {
  plan_id: string;
  vote: "ACCEPT" | "REJECT";
  p_accept?: number;                   // absent in formulas mode
  utility: number; reservation: number;
  reason: string;
}
export interface LegacyVote { plan_id: string; decision: "ACCEPT" | "REJECT"; p_accept?: number;
                              utility: number; reservation: number; reason: string }

export interface NegotiationResult {
  run_id: string; wave_id: string; mode: Mode; model: string;
  outcome: "consensus" | "fallback";
  rounds: number;                      // = turns (kept for v1.x clients)
  turns: number;                       // v2.0: 1..3
  agents: AgentId[];                   // v2.0: ["ATC", "PILOT:<focus>", "GROUND"]
  focus: string;                       // v2.0: callsign of the disruption flight
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
  paths_unavailable?: boolean;         // v2.2: replay recorded on another engine version (engine_version names it)
}

export interface Revision {
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
export interface Limit {
  limit: string;
  value: string;
  source: "workbook" | "real data" | "assumption";
  note: string;
}

export interface DecisionRequest { run_id: string; decision: "APPROVE" | "OVERRIDE"; note?: string;  // note required for OVERRIDE
  edits?: Assignment[] }   // flights the duty manager moved before approving (logged with the decision)

export interface RunListItem {
  run_id: string; wave_id: string; mode: Mode; model: string | null; outcome: string; created_at: string;
  decided: boolean;                    // a duty manager decision was recorded for this run
}

export interface DecisionResponse { ok: true; recorded_at: string; persisted?: boolean }   // v2.1.1

export interface BenchmarkReport {
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
    };
    delay_min_vs_feasible_status_quo?: number;   // arm mean delay on waves where status quo is legal
    n_ok?: number; failed_ids?: string[]; p95_all_ms?: number | null; model_decisions?: number;
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
}

export interface Triage { score: number; levels: string[]; probabilities: number[]; confidence?: number }

// v2.2 (additive)
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
  conflicts: { rule: RuleId; callsign: string; at_min: number; lat: number; lon: number; detail: string }[];   // failing checks, mapped
}
