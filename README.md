# Final Approach: disruption negotiation for arrival waves (Autonomous SG 2026, Airbus Challenge 2)

Design: `docs/superpowers/specs/2026-10-31-disruption-negotiator-design.md` · API contract: `docs/api-contract.md`

**Live demo:** https://disruption-negotiator.onrender.com · **Deck (36 s):** [YouTube](https://youtu.be/JpgnufOeDQQ) · [MP4](deck/final-approach-deck.mp4)

[![Final Approach deck: click to play on YouTube](deck/poster.png)](https://youtu.be/JpgnufOeDQQ)

![Final Approach system architecture](docs/architecture.png)

## 1. Pitch

When weather or a late inbound disrupts an arrival, the airline duty manager coordinates by phone between the tower, the crew and ground handling. This prototype replaces those calls with software agents: ATC, GROUND and one agent per airline that has a flight in the wave (2 + N voters; see the next paragraph for why this is not the challenge's three). Pilots appear too, but only to refuse (UNABLE). The input is one real disrupted flight, a row of `data/challenge2/flights.csv` (DOT 2015, `WEATHER_DELAY > 15` or `LATE_AIRCRAFT_DELAY > 20`). The wave is built at its destination airport, with the other `flights.csv` arrivals in the same window as context and weather fetched on demand from IEM (cited). Each agent sees only its own domain and can only make moves inside its own authority. An airline agent speaks for its own flights and may not accept a plan that makes one of them worse than a reference plan, with one stated exception (section 3, firm fairness). A deterministic rule engine decides whether any move or plan is legal; no model ever does. Decision models choose among the options the engine offers and vote. Claude (Haiku 5.5) only narrates the finished outcome for the duty manager and decides nothing; every run from the UI gets a brief, with a deterministic template if Claude is unavailable. In the default mode (formulas, chosen by the benchmark) no model takes any decision; decision models run in `decision` and `decision+claude` modes. The output is a revised arrival (time, runway, gate, action, delay vs schedule) plus the full log, and the human approves or overrides. BTS Jun-Aug 2024 waves are still supported as a second source.

### Deviation from the workbook's `rules_of_play`

The workbook sheet `rules_of_play` says: "One turn = each of the three agents sends one structured message." This build does not follow that. A turn here is one message from each **voter**: ATC, one `AIRLINE:<code>` agent per airline with a flight in the wave, and GROUND (2 + N voters; over the 50 benchmark waves 3 to 6). On top of that there is one formula pilot per flight (`PILOT:<callsign>`). Pilots never vote, never propose and make no model calls. They only answer UNABLE when an instruction or plan breaks one of their own rules (`PLT-*` and `ATC-WIND-LIMITS`). This is the default mode, not a switch. Waves with one airline still have 3 voters, which is the case for the two stress waves below.

Why:

- **Realism.** A pilot does not bargain over slots; they accept an instruction or say "unable", for safety only. The airline's operations centre decides which of its own flights absorbs a delay (14 CFR 121.533 on shared operational control, FAA Collaborative Decision Making, slot substitution), because only it sees connections, crew duty and the next departure.
- **Evidence.** In the 4 Nov stress test on the two most disrupted 2024 waves (as waves were built then: by scheduled hour, see section 4), the old three-agent plans left the airline worse off and nobody objected. With airline agents (formulas mode, firm fairness) both waves now reach consensus in 2 turns on the status quo:

| Wave | Old three-agent plan (delay, fuel) | v4-airlines, firm (delay, fuel) | What happened |
|---|---|---|---|
| CLT-2024-07-29-19 | 1,086 min, +1,376 kg vs status quo (status quo 1,053 min) | 1,053.3 min, 0 kg | turn 1: the airline rejects ATC's plan (e.g. AA2712 +15.5 min); turn 2: consensus on the status quo |
| ATL-2024-07-18-21 | 1,225 min, +448 kg vs status quo (status quo 1,216 min) | 1,216.2 min, 0 kg | same pattern, consensus in turn 2 on the status quo |

Both waves give the same result under firm and relaxing. The benchmark in section 5 measures the cost: every agreed plan is legal, and the airlines' floor costs consensus and some rounds.

## 2. Live demo and local run

Live: https://disruption-negotiator.onrender.com

Render free tier: it sleeps when idle, so expect a cold start of about 1 minute.

Local:

```bash
pip install -r requirements.txt
uvicorn backend.server:app --port 8000        # API, MCP and (if built) the dashboard at http://localhost:8000
```

Frontend build (served at `/` from `frontend/dist` when present; without it the API still runs):

```bash
cd frontend && npm ci && npm run build
```

Docker (multi-stage: builds the frontend, then the Python API; listens on 7860):

```bash
docker build -t disruption-negotiator .
docker run -p 7860:7860 disruption-negotiator
```

Keys are optional and live in `.env` (gitignored): `OPENROUTER_API_KEY`, `TYPESAFE_API_KEY`, `ANTHROPIC_API_KEY`. With none set, negotiation runs on the local formulas and narration uses a deterministic template. The Docker image sets `ATM_REQUIRE_REAL=1`.

## 3. Architecture

Input: pick a wave from `GET /api/waves` (2024 busy hours, id `<IATA>-<date>-<hour>`, spread over the 30 hubs and filterable by `airport`), or a flight with `GET /api/flights`, id `F2015-<row_id>` (the challenge `flights.csv`).

### System

```mermaid
flowchart LR
  subgraph DATA["Inputs"]
    F["flights.csv row<br/>(DOT 2015, the disrupted flight)"]
    WB["challenge2_operational_constraints.xlsx<br/>(workbook limits)"]
    REF["Real reference data<br/>runways (OurAirports), METAR (IEM),<br/>gates (OpenStreetMap)"]
    SIM["Seeded simulation<br/>gate occupancy, fuel, crew, connections"]
  end

  BUILD["Wave builder<br/>flight + other arrivals that hour<br/>at the destination"]
  F --> BUILD
  REF --> BUILD
  SIM --> BUILD

  subgraph NEG["Negotiation (LangGraph, max 3 turns)"]
    direction TB
    ATC["ATC agent<br/>runway, slot, order"]
    AIR["AIRLINE agents (one per airline, N)<br/>own flights: speed, ground delay, swap own, divert<br/>fairness floor on their flights"]
    GND["GROUND agent<br/>gates, remote stand"]
    ATC --> AIR --> GND
    PLT["PILOT:callsign (formula, no model)<br/>UNABLE on PLT-* and wind rules"]
    PLT -.->|"UNABLE"| AIR
  end

  ENGINE["Rule engine (deterministic)<br/>14 rules, cites source and values<br/>lists legal moves, CHECKs every plan"]
  DEC["Decision layer: picks a move / votes<br/>default chain (decide.py, overridable by DECISION_CHAIN): jev-1.14 → d1-ultra → elephant-1 → formula, or formulas only;<br/>one model at a time, the next only on timeout or invalid answer"]
  FB["Fallback solver (deterministic)<br/>beam search + safety net<br/>if no consensus after 3 turns"]
  NAR["Claude Haiku 5.5<br/>writes the duty-manager brief<br/>(explains only, decides nothing)"]

  BUILD --> NEG
  WB --> ENGINE
  ENGINE <-->|"legal moves / CHECK"| NEG
  DEC <-->|"which move? accept?"| NEG
  NEG -->|"no consensus"| FB
  FB --> OUT
  NEG -->|"consensus"| OUT
  OUT["Result<br/>revised arrival time, runway, gate or diversion<br/>+ per-turn log + limits used"]
  OUT --> NAR

  subgraph SERVE["Serving"]
    API["FastAPI + SSE<br/>/api/flights, /api/wave, /api/negotiate-wave"]
    UI["React dashboard<br/>timeline, per-turn changes, Approve / Override"]
    MCP["MCP server /mcp/sse (read-only)"]
  end
  OUT --> API --> UI
  NAR --> API
  API --- MCP

  BENCH["Benchmark (50 seeded rows)<br/>arms: formulas, 3 models, status quo,<br/>centralised optimiser; firm vs relaxing fairness; outage test; engine-off ablation"]
  BUILD -.-> BENCH
  NEG -.-> BENCH
```

### Negotiation graph (exported from LangGraph)

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	triage(triage)
	open(open)
	atc(atc)
	airlines(airlines)
	ground(ground)
	decide_turn(decide_turn)
	fallback(fallback)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> triage;
	airlines --> ground;
	atc --> airlines;
	decide_turn -. &nbsp;next&nbsp; .-> atc;
	decide_turn -.-> fallback;
	decide_turn -.-> finalize;
	fallback --> finalize;
	ground --> decide_turn;
	open --> atc;
	triage --> open;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

Protocol (`backend/wave/graph.py`, LangGraph). Per turn each voter (ATC, then the airlines in alphabetical order inside the one `airlines` node, then GROUND) sends ONE `AGENT_TURN` message holding its moves, the moves the engine rejected with their violations, its proposal, its votes and a rationale. The engine runs a `CHECK` after every proposal. A pilot sends `UNABLE` (not an `AGENT_TURN`) when a move the engine rejected, or a plan on the table, breaks one of that flight's own pilot-side rules. UNABLE is a message in the log; the engine's `CHECK` still decides legality. Consensus means all voters `ACCEPT` the same plan that passed `CHECK`, within a turn. After at most 3 turns without consensus, a deterministic beam solver with a safety net produces the plan and the reason is logged.

Components (all under `backend/`):

- **Wave builder** (`wave/flights2015.py`, `wave/build.py`): builds the wave from a `flights.csv` row (or a BTS 2024 wave), real runways, METAR, FAA registry and OSM gates, plus seeded simulation of what no public source has. Deterministic per id.
- **Workbook limits** (`wave/constraints.py`): airport capacity, wake table, fuel and crew rules parsed from `data/challenge2/challenge2_operational_constraints.xlsx` (sheet `rules_of_play`), with hard-coded fallbacks equal to the workbook.
- **Rules engine** (`wave/rules.py`): every check is a formula, and every result cites rule, source and input values. Fourteen rules:
  - `ATC-RWY-OPEN`: runway exists and is open.
  - `ATC-WAKE-SEP`: workbook wake distance (3-8 nm) between leader and follower on the same physical runway, converted to time at 140 kt (x1.25 when wet or low visibility).
  - `ATC-RWY-CAPACITY`: arrivals per runway per rolling 60 min within the workbook cap.
  - `ATC-TAXI-ROUTE`: taxi route to the gate is usable.
  - `ATC-TAXI-CONFLICT`: the landing taxis to its stand against the fixed 2015 ground traffic and earlier wave flights (hold-short / give-way waits within the 30-min horizon) and has vacated its runway before the next landing or takeoff on it. Waits cost in-block time, they never fail it.
  - `ATC-HOLD-STACK`: at most 5 simultaneous holders per physical hold fix, 1,000 ft apart, bottom-first exit (a later entrant may not leave while an earlier one still holds).
  - `ATC-WIND-LIMITS`: crosswind and tailwind from real METAR against FAA Order 8400.9 section 6d and demonstrated crosswind by type.
  - `PLT-EARLIEST-ETA`: no landing before the flight can physically arrive.
  - `PLT-FUEL-RESERVE`: hold fits within extra fuel above alternate plus 30 min final reserve.
  - `PLT-CREW-DUTY`: in-block (or alternate arrival) within 13 h of crew report time; report = scheduled departure - 60 min. A bust that even the earliest landing cannot avoid is reported, not vetoed.
  - `GND-GATE-SIZE`: gate fits the aircraft.
  - `GND-GATE-FREE`: gate not occupied, including by other wave flights, with the airport turnaround time.
  - `GND-GATE-NOTICE`: gate changes need at least 20 min notice.
  - `GND-CREW-DUTY`: reports ground overtime past the 8 h shift. It never vetoes; overtime is a cost.
- **Moves and authority** (`wave/moves.py`): ATC changes runway, slot and order. An airline agent changes only its own flights (speed reduction, ground delay, divert, accept, and `swap_own`: swap the slots of two of its own flights on the same runway). GROUND changes gates only. Pilots have no moves.
- **Airlines** (`wave/airlines.py`): a static table groups carrier codes into airlines (a regional flies under its major, for example OH to AA). BTS data has the operating carrier only, so where the table has no row the operating carrier is its own airline; each wave lists its airlines and the basis of each grouping (`Wave.airlines`).
- **Pilots** (`wave/unable.py`): one per flight, formulas only. A pilot refuses only on the `PLT-*` rules and `ATC-WIND-LIMITS`.
- **Utilities** (`wave/utility.py`): ATC scores the wave, each airline the weighted mean over its own flights, GROUND gates and crew (overtime at 1.5x). Voting uses a reservation schedule that drops each turn.
- **Fairness floor** (`wave/fairness.py`, `ATM_FAIRNESS=firm|relaxing`): an airline objects to a plan that makes one of its flights worse than a reference plan on delay or fuel: the status quo when it is feasible, else the best CHECK-passed plan seen so far. **Firm** (shipped): an airline never accepts such a plan, except when no CHECK-passed plan on the table meets its floor (the escape clause, so a wave can still end; such a vote is marked `decided_by: "escape"` in the log). When the status quo is illegal, each airline's floor is the best legal plan seen *for that airline*, so two airlines' floors can come from different plans and conflict (section 5). **Relaxing**: the floor loosens each turn on the reservation schedule (a flight may be worse by up to 6.0 / 13.2 / 20.4 min of delay in turns 1 / 2 / 3, plus a fuel allowance on the same scale). Shipped as firm by the user's choice, see section 5.
- **Safety**: every move and every proposal is engine-checked, votes are cast only on `CHECK`-passed plans, the final plan's feasibility is re-checked, and models only choose among offered options.
- **Decision layer** (`decide.py`): per call, a chain of OpenRouter models, then TypeSafe (Jev), then local formulas. Each call has a 2 s timeout and one retry on 429/5xx. A circuit breaker skips an entry for 60 s after 3 consecutive failures. Every fallback is logged and shown in the UI.
- **Fallback solver** (`wave/fallback.py`): beam search over legal moves maximising the worst agent's utility, with a remote-stand, ground-delay or divert safety net. Legality is still decided only by the rules engine. It never raises.
- **Narration** (`wave/narrate.py`): Claude writes the brief from the structured outcome; deterministic template on any failure.
- **API and SSE** (`server.py`): REST under `/api` (`/api/flights`, `/api/waves`, `/api/wave/{id}`, `/api/runs/{id}`, `/api/benchmark`, `/api/config`) and `POST /api/negotiate-wave`, which streams messages as server-sent events. Output is `result.revision` plus the full log. Contract v2.0 is `docs/api-contract.md`.
- **MCP server** (`mcp_server.py`): read-only, at `/mcp/sse`. Six tools: `list_waves`, `get_wave`, `fcfs_plan`, `legal_moves`, `check_plan`, `negotiate` (formulas only).

### Limits applied (`rules.LIMITS`, `rules.limits_for`)

Every change and assumption is returned in `result.limits`. Summary:

| Limit | What we do | Source |
|---|---|---|
| Wake separation | Workbook distances (`min_separation_nm`, 3-8 nm) converted to time at a 140 kt approach speed (3 nm = 77 s, 5 nm = 129 s, 8 nm = 206 s); same physical runway | workbook (nm column); 140 kt is an assumption |
| Wet or low-visibility factor | x1.25 on wake times | assumption |
| B757 | Treated as Heavy | assumption |
| Runways and wind | Real runways and METAR instead of the workbook runway count (used only as the capacity divisor) | real data |
| Runway capacity | `max_runway_ops_per_hour / active_runways` per runway; counts arrivals only | workbook, arrivals only |
| Turnaround, gates, spare gates | Per airport from workbook (turnaround 45/35 min, spare gates); wave models `min(gates, 12)` gates plus a remote stand with 2 positions | workbook; remote stand is an assumption |
| Fuel burn, reserve, spare | 2400 / 6500 kg/h, 30 min final reserve, 45 min spare fuel | workbook |
| Contingency fuel 5% | Not credited: it covers en-route deviations, and the workbook's 45 min spare-on-arrival is what is checked at the destination. Not crediting it is the conservative reading | assumption |
| Flight crew 13 h duty | Enforced by `PLT-CREW-DUTY`; report time = scheduled departure - 60 min | workbook; report offset is an assumption |
| Ground shift | 8 h, overtime at 1.5x is a cost, not illegal | workbook |
| Gate change notice | At least 20 min; remote stand exempt | workbook |
| Speed reduction | Capped at 8 min | assumption |
| Taxi-in time (engine `v3-taxi`) | In-block = landing slot + runway occupancy time (roll to the first forward exit past the required roll) + unimpeded taxi on the OpenStreetMap taxiway graph from that exit to the gate's stand (gates bound to OSM stands; 20 kt, 10 kt on taxilanes and turns > 30°, + 30 s to park). Tiers: real (OSM route); generic (runway end missing from today's OSM, a wave below the 80 % surface quality gate, or an unbound gate: straight line runway midpoint to gate x1.4 at 15 kt); no geometry (no runway coordinates: 6 min near / 10 min far). Remote stand + 10 min bus; no taxiway closures. The time is unimpeded: no ramp, gate or queue waits until `ATC-TAXI-CONFLICT` schedules hold-shorts, so it sits below BTS `TAXI_IN`. Validation without fitting (benchmark focus flights on the real surface tier with a real `TAXI_IN`, ROT + routed taxi vs `TAXI_IN`): n 42, MAE 3.37 min, bias -2.78 min, per-airport table in `eval/BENCHMARK.md` (Taxi-in validation). Figures quoted here earlier were FCFS medians over all flights of the replay waves, not this validation | real data (OSM, ODbL); speeds, generic tier and 6/10 min fallback are assumptions |
| Time base (engine `v3-time`) | BTS scheduled arrival (STA) is a gate (in-block) time. Earliest landing = STA + attributed BTS delays (late aircraft, carrier, weather, NAS) - taxi-in reference (the row's real `TAXI_IN`, else the airport's 2015 median over the challenge's delayed-flight rows, else 6 min). For a 2015 row that is the real wheels-on whenever the attributed delays sum to `ARRIVAL_DELAY` (security delay is not attributed). Delay = max(0, in-block - STA), also for `delay_vs_schedule_min`. Fixed-traffic arrivals land at their real wheels-on (STA + `ARRIVAL_DELAY` - `TAXI_IN`) but are still picked by scheduled time, so a late one can land hours after the window. Crew floors and `PLT-CREW-DUTY`'s "unavoidable" test use ROT + unimpeded routed taxi to the gate, minimised over the open runways. In-block uses that unimpeded routed taxi (see Taxi-in time), so the FCFS delay of a flight sits below its real BTS delay by (taxi-in reference - ROT - unimpeded taxi) when that is positive | real data (BTS `ARRIVAL_DELAY`, `WHEELS_ON`, `TAXI_IN`); 6 min fallback is an assumption |

### Flight paths and the airport surface (engine `v3-traffic`)

The plan is no longer only a table of slots. Every landing now has a flown path, and the same plan drives the radar and the surface map on one replay clock.

- **Air.** Each flight flies a published route: the FAA CIFP 2611 STAR and approach (or a synthetic corner-post and downwind route where the airport has none, drawn dashed), at the terminal speed profile. Flights that lap enter a holding stack (1,000 ft levels, bottom-first exit) at the hold fix of its STAR (the first fix of the common route, else the last enroute-transition fix). The slot, the fuel burned in the hold and the lap count all come from that same path, so the radar draws what the engine used and nothing else.
- **Ground.** After touchdown the flight rolls to the first forward exit past the required roll and taxis the fastest route on the OpenStreetMap taxiway graph to its bound stand. The real movements of the same hour (queue arrivals and departures; BTS 2015, or BTS 2024 at the 30 hubs for June to August) are scheduled once as fixed traffic, and each wave flight waits at a node or a runway hold until the headway, crossing gap and runway-vacate rules are met. Those waits are the amber bars on the surface map and they add to in-block time.
- **Legality stays in the rule engine.** `ATC-HOLD-STACK` and `ATC-TAXI-CONFLICT` are deterministic rules like the other twelve (14 in all); no model decides whether a path is legal. Every point and segment carries `src: "cifp" | "osm" | "synth"` and synthesized parts are drawn dashed.
- **Offline.** Procedures, surface and movements are derived files in `data/ref/` (and seeded into Postgres); the runtime never fetches CIFP, OSM or BTS.

Screenshots (replay mode, no keys): the status-quo plan of `F2015-7053` with ASQ4125 (EV4125) in its hold at WATSN, and `F2015-75` at DFW with AAL2441 waiting to give way to AA2498.

![Radar: ASQ4125 holding at WATSN, ORD F2015-7053](docs/screens/radar-holding.png)

![Surface map: AAL2441 holds short (amber bar), DFW F2015-75](docs/screens/surface-hold-short.png)

## 4. Data and provenance

| Layer | Source | Status |
|---|---|---|
| Focus flight, delays, context arrivals | DOT 2015 `flights.csv` (challenge file) | real |
| Second source: flights, delays, queue | BTS On-Time, Jun-Aug 2024, arrivals to 30 hubs | real |
| Runways (ends, headings, length) | OurAirports | real |
| Weather at arrival hour (wind, visibility, ceiling, wet/dry) | Iowa Environmental Mesonet METAR archive, fetched on demand and cited | real |
| Airport capacity, turnaround, wake table, fuel and crew rules | challenge workbook (synthetic planning numbers) | given by the challenge |
| Tail to aircraft type, wake category, gate size | FAA aircraft registry | real (US-registered tails) |
| Gates and wide-body gates | OpenStreetMap, 10 hubs | real |
| Arrival procedures (STARs, approaches, hold fixes) | FAA CIFP, cycle 2611, pinned (`scripts/fetch_cifp.py`) | real; **Procedures: FAA CIFP 2611, not for navigation** |
| Airport surface (taxiways, runways, stands, holding positions) | OpenStreetMap via Overpass (`scripts/fetch_surface.py`), ODbL; where OSM fails the 80 % quality gate, the X-Plane Scenery Gateway's recommended layout (`scripts/fetch_surface_xplane.py`, 96 airports incl. IAH, listed in `data/ref/surface/XPLANE.md`), GPL v2 or later | real; **© OpenStreetMap contributors**; **X-Plane Scenery Gateway contributors (GPL)**; not for navigation |
| Real ground traffic of the wave hour (arrivals, departures, taxi times) | BTS On-Time 2015 (`data/ref/movements2015`, `scripts/fetch_bts.py --movements2015`) and BTS On-Time 2024, Jun-Aug, 30 hubs (`data/ref/movements2024`, `--movements2024`); both seeded into `geo.movements2015_day`, keyed by airport and date | real |
| Gate occupancy | seeded simulation | simulated, badged |
| Fuel on board | seeded simulation | simulated, badged |
| Crew shifts | seeded simulation, anchored to the flight's actual delayed ETA | simulated, badged |
| Passenger connections | seeded simulation | simulated, badged |
| Two wide gates per hub where OSM has none | simulated | simulated, badged |

**How a wave is built (5 Nov 2026).** A wave is the hour in which flights really touch down: scheduled gate arrival + `ARR_DELAY` - taxi-in, so a flight 5 hours late is in the hour it lands, among the traffic it actually meets. A 2024 wave `<IATA>-<date>-<hour>` negotiates the most delayed disrupted flight (weather delay > 15 min or late-aircraft delay > 20 min) and the four flights landing nearest to it, usually within a few minutes; every other arrival of that hour lands at its real time on the parallels of the best-wind runway, keeping one free for departures, and the hour's real departures taxi out. A 2015 wave `F2015-<row>` centres on that `flights.csv` row's real landing, with the rows landing nearest to it; `flights.csv` holds only flights 15+ min late, so 2015 waves have few background arrivals. Before 5 Nov waves were grouped by scheduled hour, so a disrupted flight often landed hours after the others and the negotiation had little to trade. The 2024 index (`data/waves/index.csv`, `scripts/fetch_bts.py --index`, 36,881 hours) lists hours busiest first by disrupted landings, with the 2,191 storm-backlog hours of more than 8 ranked last.

Layers missing for a flight fall back to a generic model and are badged in the UI. Procedures: FAA CIFP 2611, not for navigation. The derived procedure and surface files are for this simulation only and are not for navigation. Map data © OpenStreetMap contributors (ODbL). Surfaces listed in `data/ref/surface/XPLANE.md` are derived from the X-Plane Scenery Gateway and stay under the GNU GPL v2 or later (`data/ref/surface/COPYING.xplane`); the rest of the repository is MIT.

### Data: Postgres (Neon) with file fallback

Reference data, runs and decisions live in Neon Postgres. The files under `data/` stay in the repo and are the fallback: the app serves from them when no database is configured or the database is down.

| Variable | Used by | Meaning |
|---|---|---|
| `DATABASE_URL_POOLED` | app runtime (`PgStore` pool), Render, `scripts/db_timings.py` | Neon pooled (`-pooler`) endpoint, `sslmode=require`. The only database secret on Render. Unset: the app uses files. |
| `DATABASE_URL` | `scripts/db_migrate.py`, `scripts/db_seed.py`, `scripts/db_dump_live.py` | Neon direct endpoint, owner role. Dev machine only; never put it on Render. |
| `DATABASE_TEST_URL` | live DB tests | Direct owner URL of a separate `atm_test` database in the same Neon project. |
| `ATM_STORE` | app | `files` forces the file store; `pg` requires `DATABASE_URL_POOLED`. |
| `ATM_LIVE_DB` | tests | `1` enables the opt-in live tests. |
| `ATM_WARM` | app | Default `1`: a one-shot daemon thread loads the flights.csv index and builds the replay (demo) waves so the first page load is fast (started at boot with the file store, else by the first request, normally Render's `/api/config` health check). `0` turns it off (the test suite sets it). |
| `ATM_PLAN_CACHE` | app | Default `256`: plans whose 4D trajectories are kept in memory (about 125-170 KB each, so about 43 MB at most). Edit suggestion searches never add to it. A missing, non-numeric or < 1 value means 256. The offline benchmark raises it to 256 x its concurrency for the run (still one shared cache). |

Neon setup: project in region `aws-us-west-2` (Oregon, next to Render's default `oregon`; a region cannot be changed later). Fix the compute at 0.25 CU (min = max) in the Console; autoscaling would burn the free 100 CU-hours up to 8 times faster. Keep a `test` branch (or the `atm_test` database) for the live tests so they never touch the production data.

```bash
python3 scripts/db_migrate.py            # apply migrations/NNNN_*.sql as the owner (append-only, checksummed)
python3 scripts/db_seed.py               # load the reference data (idempotent, shadow-table swap, 750 MB guard)
python3 scripts/db_seed.py --check       # verify row counts and checksums against the files
ATM_LIVE_DB=1 python3 -m pytest -q -m live_db   # optional: live tests against DATABASE_TEST_URL
python3 scripts/db_dump_live.py          # back up live.runs and live.decisions to .cache/dump/*.jsonl
python3 scripts/db_timings.py            # p50/p95 per path against the design budgets (add --after-idle for a wake-up sample)
```

First production seed, in order:

1. `python3 scripts/db_migrate.py`
2. `python3 scripts/db_seed.py`. Careful: the default dataset list includes `runs_import`, which loads every run in your local `eval/runs/` folder into the production `live.runs`. Review or clean that folder first, or name the datasets explicitly and leave `runs_import` out (`python3 scripts/db_seed.py small workbook waves bts_arrivals metar_2024 iem_cache flights2015 disruptions_v1 replays benchmark v1_eval procedures surface movements2015`).
3. `python3 scripts/db_seed.py --check` (must report no mismatches)
4. In the Render dashboard set `DATABASE_URL_POOLED` (the pooler URL). Never set `DATABASE_URL` there.
5. Redeploy.
6. Run one negotiation and approve it; note the run id.
7. Redeploy again.
8. Confirm `GET /api/runs/<id>` still returns that run, and `GET /api/config` shows `"backend": "postgres"` and `"degraded": false`.
9. `python3 scripts/db_timings.py` from the dev machine.

Rollback: unset `DATABASE_URL_POOLED` in the Render dashboard and redeploy. The app is back on files (runs saved while it was on Postgres stay in the database; `scripts/db_dump_live.py` exports them).

`DATABASE_TEST_URL` is a separate `atm_test` database in the same Neon project (a Neon `test` branch also works); the live tests never touch the production database.

Measured sizes (1 GB free limit): `atm_test` with all reference data 152 MB; the raw datasets add an estimated ~158 MB (they load only when named). When the build is finished, free that space: from the owner connection to the production database (`DATABASE_URL`), run `drop database atm_test;`.

Statement timeout: migration `0006` sets `statement_timeout = '5s'` on the role that runs it (the owner, which `DATABASE_URL_POOLED` uses for now), in that database only, so a runaway query from the app is cancelled after 5 s. It is a role setting because `SET` does not survive the pooler. `db_seed.py`, `db_migrate.py` and `db_dump_live.py` lift it to 0 on their own connections so long loads and exports finish. The limit also applies to your own sessions as the owner (Neon SQL editor, psql): run `set statement_timeout = 0;` first for manual maintenance. Apply `0006` to `atm_test` too (`python3 scripts/db_migrate.py --url-env DATABASE_TEST_URL`).

Fallback behaviour: every read and write goes through `FallbackStore`. A failed or timed-out call is retried (except `record_decision` and `put_benchmark`, which are not idempotent), then served from files; three consecutive failures open a breaker for 60 s. `GET /api/config` never queries the database. Read its `store` object: `"backend": "postgres"` with `"degraded": false` is healthy. `"backend": "files"` with `"degraded": true` (and a `reason`) is the fallback: a database is configured but unreachable, and runs saved then are not persistent. `"backend": "files"` with `"degraded": false` means no database is configured. A write the database rejects (a constraint, bad data, a missing table or privilege) is kept in the files and shows as degraded, without opening the breaker; an empty (unseeded) table is served from files the same way.

What changed in the v3 build:

- **`raw.*` only when named.** The raw challenge tables (about 120 MB, never read by the app) are no longer in the default dataset list: `python3 scripts/db_seed.py raw` loads them, `--drop raw` removes them.
- **`replays` prunes.** Seeding `replays` deletes the `eval.replays` rows whose `(wave_id, engine_version)` is not in `data/replay/`, so only the recorded files stay.
- **Geometry is documents, not points.** The app reads one gzipped document per airport from `geo.procedures` and `geo.surface` (and `geo.movements2015_day` for the fixed traffic), seeded by the `procedures`, `surface` and `movements2015` datasets. Migration `0007` dropped the point tables (`geo.fixes`, `geo.stands`, `geo.osm_gates`, `geo.holding_positions`), `geo.coverage` and the PostGIS extension; nothing queries by location. The coverage report is the file `data/ref/GEO_COVERAGE.md` (`scripts/geo_coverage.py`).

Compute hours: the free plan gives 100 CU-hours a month, which is 400 active hours at 0.25 CU. Compute suspends 5 minutes after the last query. Do not add a keep-alive, a timer query or a database call to the health check: a database that is never idle uses about 183 CU-hours a month and the budget is gone in about 16 days. If the budget runs out, the site keeps running on files (degraded).

Optional, later: a least-privilege login for the runtime. Until then `DATABASE_URL_POOLED` uses the owner role and nothing depends on `atm_app`. Run this once as the owner (the Neon SQL editor or `psql`), choosing the password yourself:

```sql
create role atm_app login password '<password>' in role app_read, app_write;
alter role atm_app set statement_timeout = '5s';
alter role atm_app set search_path = ref, live, eval, geo, extensions;
```

The `statement_timeout` line gives `atm_app` the same 5 s limit that `0006` gives the owner (`0006` only covers the role that ran it). `atm_app` needs both groups: `app_write` alone has no `SELECT`, which `UPDATE ... WHERE` and `ON CONFLICT` need. Then point `DATABASE_URL_POOLED` (in `.env` and Render) at the pooler URL for `atm_app`.

## 5. Evaluation

### Headline: 50 busy hours from 2024

Run: `python3 -m backend.wave.benchmark --source bts --n 50 --arms openrouter:typesafe/jev-1.14,openrouter:liquid/d1-ultra,formula --concurrency 4 --seed 7 --outage-n 20` (5 Nov 2026, engine `v4-airlines`, firm fairness, live models, waves built around real landing times). 50 seeded hours from the US DOT 2024 arrivals (June to August, 30 hubs, sampled from the 10,000 top-ranked hours), each negotiating 5 flights that land together among every arrival of the hour and the hour's real departures. Every number below is from `eval/bts2024/benchmark.json`; full tables in `eval/bts2024/BENCHMARK.md`. The app's Benchmark page opens on this run and switches to the 2015 one.

| Arm | Consensus | Fallback to solver | Mean rounds | Rule violations in agreed plans | Delay, all arrivals (min) | Fuel (kg) | Pax protected | p50 / p95 ms | Errors | Same decision as formulas | Model calls per wave |
|---|---|---|---|---|---|---|---|---|---|---|---|
| typesafe/jev-1.14 | 92% | 8% | 1.88 | 0 | 457.5 | 409.0 | 46.4% | 445 / 608 | 0% | 68% | 21.7 |
| liquid/d1-ultra | 92% | 8% | 1.76 | 0 | 457.3 | 403.5 | 46.4% | 631 / 1,836 | 0.7% | 60% | 18.3 |
| formula | 94% | 6% | 1.86 | 0 | 457.8 | 427.2 | 46.3% | n/a | 0% | 100% | 0 |
| centralised optimiser (beam search, no negotiation) | - | - | - | 0 infeasible | 501.0 | 631.0 | 43.2% | - | - | - | - |
| status quo (no negotiation) | - | - | - | breaks a rule on 26/50 hours | 432.5 | 136.5 | 47.3% | - | - | - | - |

- **Legality is the gain.** When five late flights really land together, the uncoordinated status quo (first come, first served) breaks at least one rule on 26 of the 50 hours: runway capacity, a runway still occupied at the next landing, a gate still taken. Every agreed plan breaks none. With the rule engine switched off, 48% of the formula arm's plans would be illegal.
- **Delay is about neutral where it can be compared.** On the 24 hours whose status quo is legal: jev-1.14 474.1 min, d1-ultra 473.9 min, formulas 474.4 min, status quo 477.0 min. The all-50 means above favour the status quo only because it is illegal on 26 of them; making those legal costs minutes.
- **No flight left worse.** Under the firm floor no agreed plan leaves any flight worse than in the status quo on delay or fuel (0.0% of agreed hours with a legal status quo, every arm).
- **Fallbacks:** the solver decided 4 hours for each model (`SAN-2024-07-21-20`, `ATL-2024-06-17-21`, `MCO-2024-08-05-01`, `SAN-2024-08-11-20`) and 3 for formulas (the same without MCO).
- **Selection picks `typesafe/jev-1.14`**, the only model arm under the 1 s p95 bar (608 ms); d1-ultra's p95 is 1,836 ms and 0.7% of its decisions fell back after a failed call. jev-1.14's accept/reject calls agree less with the formula verdict (Brier 0.0069 against d1-ultra's 0.0011) and it makes about 3 more model calls a wave; outcomes are the same within a minute. n = 50 is indicative, not significant.
- **Outage test:** primary model replaced by a dead id on 20 hours; 20/20 resolved with a legal plan.
- **Cost, measured:** this run and the 2015 run below together (two model arms on 100 waves, plus two 20-wave outage tests) moved the OpenRouter usage counter by $0.29.
- The engine history table in `eval/bts2024/BENCHMARK.md` is copied from the archived engines, which ran on 2015 rows built the old way: do not compare it with this run.

### Challenge input: 50 rows of the 2015 `flights.csv`

Run: `python3 -m backend.wave.benchmark --source flights2015 --n 50 --arms openrouter:typesafe/jev-1.14,openrouter:liquid/d1-ultra,formula --concurrency 4 --seed 7 --outage-n 20` (5 Nov 2026, engine `v4-airlines`, firm fairness, live models, waves centred on each row's real landing). The same 50 seeded rows as before (stratified over 30 hubs). Every number below is from `eval/benchmark.json`; full tables in `eval/BENCHMARK.md`.

<!-- BENCHMARK -->
| Arm | Consensus | Fallback to solver | Mean rounds | Rule violations | Delay, all arrivals (min) | Fuel (kg) | Pax protected | Brier* | p50 / p95 ms | Errors | Same decision as formulas |
|---|---|---|---|---|---|---|---|---|---|---|---|
| typesafe/jev-1.14 | 96% | 4% | 1.88 | 0 | 417.0 | 174.0 | 31.6% | 0.0081 | 433 / 558 | 0% | 64% |
| liquid/d1-ultra | 94% | 6% | 1.86 | 0 | 417.4 | 154.8 | 31.4% | 0.0013 | 586 / 1,085 | 0% | 58% |
| formula | 98% | 2% | 1.98 | 0 | 417.2 | 204.2 | 31.6% | n/a | n/a | 0% | 100% |
| centralised optimiser (beam search, no negotiation) | - | - | - | 0 infeasible | 417.5 | 57.8 | 32.8% | - | - | - | - |
| status quo (no negotiation) | - | - | - | breaks a rule on 23/50 waves | 416.1 | 15.3 | 30.6% | - | - | - | - |

\* Brier is measured against the formula verdict (accept if utility >= reservation), not ground truth.

- **Legality is the gain here too:** the status quo breaks a rule on 23 of 50 waves, the agreed plans on none; 48% of the formula arm's plans are illegal with the rule engine off. On the 27 waves with a legal status quo, delay is 379.6 (jev-1.14), 379.4 (d1-ultra) and 379.6 (formulas) against 381.2 min; no agreed plan leaves a flight worse (0.0%).
- **Fallbacks:** jev-1.14 2 (`F2015-144520`, `F2015-208678`), d1-ultra 3 (the same and `F2015-53108`), formulas 1 (`F2015-208678`). **Outage test:** 20/20 resolved.
- **Selection picks `typesafe/jev-1.14`** here too (p95 558 ms, consensus 96%); d1-ultra misses the 1 s bar (1,085 ms). The 4 Nov run on the old waves picked d1-ultra (p95 957 ms); the shipped chain still starts with it.
- **Regressions against the archived engine: 106 wave-arm rows** in `eval/benchmark.json`. They are expected: the waves themselves changed on 5 Nov, so these rows compare different flights and are not engine regressions.
<!-- /BENCHMARK -->

Method (`python3 -m backend.wave.benchmark --n 50 --arms <models>,formula --concurrency 4 --seed 7`):

- 50 seeded `flights.csv` rows, stratified over the 30 busiest hubs (BTS 2024 waves with `--source bts`).
- Selection rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie goes to lower Brier.
- Arms: each candidate decision model (full chain behavior, same waves), the formula arm (no model), and the status quo (FCFS slots, scheduled gates, no coordination).
- Metrics:
  - consensus %: waves that reached agreement within 3 turns; fallback % is the rest, ending in the deterministic solver.
  - error rate: % of decisions answered after the arm's model failed (non-empty `fallback_from`).
  - latency p50/p95: over model-answered decisions only.
  - Brier: mean `(p_accept - y)^2` over model-answered votes, where `y` is the formula verdict (utility at or above reservation). It measures agreement with the formula, not with ground truth.
  - agreement with formula: share of decisions matching the formula arm.
  - outcome means (delay, fuel, passengers protected, crew overtime): over waves that succeeded in every arm.
  - status-quo comparison: the status quo may itself break rules, so regressions against it are reported only on waves where the status quo is feasible.
- Outage test: the primary model is replaced by an invalid id and the chain must still resolve every wave.
- Engine-off ablation: the share of formula-arm final plans that are infeasible under the real rules when the rules engine is disabled.

#### History: three models and two fairness variants (4 Nov 2026, waves by scheduled hour)

Measured before the waves were rebuilt around real landing times, with `typesafe/jev-1.14`, `liquid/d1-ultra` and `baseten/elephant-1`; the per-variant runs are in `eval/variants/`. Kept for the model and fairness choices it informed; do not compare its minutes with the tables above.

**Arms, firm (4 Nov):** consensus 94 / 96 / 94 / 98% (jev-1.14 / d1-ultra / elephant-1 / formula), p95 509 / 957 / 587 ms; the selection rule picked `liquid/d1-ultra`, which leads the default chain in `decide.py`.

**Fairness variants.****Fairness variants.** Same waves and arms under each variant. "Worse flight" = share of agreed waves with a feasible status quo where any flight is worse than in the status quo on delay (> 0.05 min) or fuel (> 0.5 kg). "Worse wave-total" = share of waves with a feasible status quo whose final plan (consensus or fallback) has a worse wave-total delay or fuel. v3-traffic recorded wave totals only.

| Variant | Arm | Consensus | v3-traffic consensus | Worse flight | Worse wave-total | v3-traffic worse wave-total | Model calls per wave | p95 ms |
|---|---|---|---|---|---|---|---|---|
| firm (shipped) | jev-1.14 | 94% | 98% | 0.0% | 0.0% | 65.4% | 20.7 | 509 |
| firm (shipped) | d1-ultra | 96% | 96% | 0.0% | 0.0% | 61.5% | 18.7 | 957 |
| firm (shipped) | elephant-1 | 94% | 96% | 0.0% | 0.0% | 61.5% | 20.5 | 587 |
| firm (shipped) | formula | 98% | 98% | 0.0% | 0.0% | 65.4% | 0 | n/a |
| relaxing | jev-1.14 | 100% | 98% | 61.5% | 57.7% | 65.4% | 15.3 | 521 |
| relaxing | d1-ultra | 100% | 96% | 57.7% | 53.8% | 61.5% | 14.5 | 1,306 |
| relaxing | elephant-1 | 98% | 96% | 57.7% | 53.8% | 61.5% | 15.1 | 680 |
| relaxing | formula | 100% | 98% | 61.5% | 57.7% | 65.4% | 0 | n/a |

- **The automatic pick was relaxing; firm is shipped by the user's choice (4 Nov 2026).** The pick rule: a variant passes if every arm's consensus is at least the v3-traffic arm's minus one wave (2 points at n = 50); among passing variants the lower worse-flight share wins. Firm fails it in jev-1.14 (94% against 98%, two waves lower; elephant-1 is one wave lower, inside the tolerance). The user chose firm because relaxing leaves 57.7 to 61.5% of agreed waves with a flight worse than its status quo, and firm leaves none.
- **What firm costs:** consensus 94 / 96 / 94 / 98% (jev-1.14 / d1-ultra / elephant-1 / formula) against 98 / 96 / 96 / 98% in v3-traffic. That is 2 more fallback waves in jev-1.14 and 1 more in elephant-1. Relaxing reaches 100 / 100 / 98 / 100%. Firm also takes about half a round more (mean rounds 1.88 to 2.00, against 1.46 to 1.50) and about 5 more model calls per wave than relaxing (jev-1.14 20.7 against 15.3). Firm burns less fuel than relaxing (132.5 to 154.7 kg against 210.2 to 223.7 kg per wave).
- **Regressions against v3-traffic: 66 wave-arm rows** (19 d1-ultra, 16 jev-1.14, 16 formula, 15 elephant-1; 57 on delay, 9 on fuel). Every row has the cause `negotiation`: `physics_delta` is 0.0 on every metric, and the status quo is identical (326.69 min, 1.36 kg, 26 feasible waves). They are not all small: the largest delay rise is 54.4 min (`F2015-163396`, formula, 185.6 to 240.0) and the largest fuel rise is 168 kg (`F2015-251827`, d1-ultra, 12 to 180). The first 20 rows are listed in `eval/BENCHMARK.md`, all 66 in `eval/benchmark.json`. We have not measured per wave why the airline floor moves the plan in each of them.
- **Consensus fallbacks** (firm, solver took over): jev-1.14 3 waves (`F2015-112396`, `-208692`, `-246479`), d1-ultra 2, elephant-1 3, formula 1. Mean delay is flat across variants (334.0 to 335.5 min in firm, 333.4 to 334.6 min in relaxing).
- **Firm loses consensus only on waves whose status quo is illegal.** When the status quo passes CHECK (26 of 50 waves), every airline's floor is that same plan, which meets every floor, and firm reaches 100% consensus in every arm. When the status quo is illegal (24 waves), each airline's floor is the best legal plan seen for that airline, ranked by its own utility. Different airlines can anchor on different plans, and then no plan on the table meets every floor. On those 24 waves firm reaches 87.5 / 91.7 / 87.5 / 95.8% (jev-1.14 / d1-ultra / elephant-1 / formula), against 100 / 100 / 95.8 / 100% for relaxing. All 9 firm fallbacks (4 distinct waves: `F2015-112396`, `-124231`, `-208692`, `-246479`) are on illegal-status-quo waves, so all of firm's consensus cost against relaxing sits there. Example, formula arm: in turn 3 of `F2015-246479` OO floor-rejects one plan (OO5523 +8.1 min) and AA floor-rejects the other (AA686 +7.1 min), so the solver decides. One shared floor for every airline would remove the conflict but could lock in a plan worse for one airline; we kept the per-airline floor and have not benchmarked the alternative.
- **Earlier engines:** the engine history table in `eval/BENCHMARK.md` runs from v2 to v4-airlines. The v3-traffic numbers are archived in `eval/archive/`. The 100-row and 1,000-row runs (`eval/BENCHMARK_100.md`, `eval/BENCHMARK_1000.md`) were made on earlier engines and were not re-run; do not compare them with the table above.

### Cost of model calls

**Measured on 5 Nov 2026:** a first run of the two 50-wave benchmarks with d1-ultra alone (about 19 model calls a wave, plus two 20-wave outage tests) cost $0.136 on the OpenRouter usage counter, about $0.001 a wave; the three-arm re-run above (jev-1.14 and d1-ultra) cost $0.29, and recording the 10 demo runs with jev-1.14 cost $0.012 (about $0.001 a wave). Earlier, on 31 Oct 2026, from the OpenRouter key's usage counter before and after 20 random live flights.csv waves in decision mode (jev-1.14): **191 model calls for $0.0097**, so about **$0.00005 per call** on the v2 call pattern (9.6 calls per wave). Airline agents add model decisions: the v4-airlines benchmark (firm) counts 18.7 to 20.7 model calls per wave (relaxing 14.5 to 15.3), about twice the v2 pattern. At the measured price per call that is roughly $0.001 per wave. This is an estimate: the benchmark does not measure cost, and the price per call was not re-measured on v4.

| Item | Per wave | Per 1,000 waves | Source |
|---|---|---|---|
| jev-1.14 decisions (OpenRouter) | ~$0.001 | ~$1 | estimate: 20.7 calls (benchmark, firm) x $0.00005 (measured on v2) |
| Claude Haiku 5.5 brief (one call, max 1,500 output tokens) | at most ~$0.0016 | at most ~$1.60 | Anthropic list price $0.10 / $0.50 per million input / output tokens; upper bound assuming 8k input tokens and the full output cap |
| Formulas mode | $0 | $0 | no model calls |
| **Total, decision mode with brief** | **about $0.003** | **about $3** | |

Replays cost nothing (no model calls). The path and surface layers add no model calls either: trajectories, taxi routes and the hold-short schedule are deterministic code, and `GET /api/wave/{id}/geo` and `POST /api/trajectories` are pure computation. The v3 and v4 benchmarks record no cost (`cost_usd_per_wave` is 0.0, output tokens free, input cost not measured), so the per-call price comes from the 31 Oct measurement on the v2 call pattern. Pilot agents make no model calls.

### Demo replays

The wave list opens on these: recorded on 5 Nov 2026 on the rebuilt waves (engine v4-airlines, decision mode, firm fairness, `typesafe/jev-1.14`, 0 model fallbacks, $0.012 to record), they play back with no model calls. All 10 reach consensus. Delay and fuel are wave totals of the agreed plan against the status quo (first come, first served).

Five 2024 hours, each with five flights landing within minutes of each other and the hour's real traffic. In every one the status quo breaks a rule, and the agreed plan is legal with no aircraft holding:
- `ATL-2024-06-07-20` (airlines DL and WN, 62 other arrivals; 1 turn): the status quo puts too many landings on 26L (ATC-RWY-CAPACITY) and holds DL385 for a lap. Delay 106.0 min unchanged, fuel 368 to 48 kg.
- `ORD-2024-06-04-21` (AA, OO and WN, 35 other arrivals; 1 turn): the status quo lands a flight while another is still on the runway (ATC-TAXI-CONFLICT). Delay 395.1 to 387.0 min, fuel 218 to 64 kg.
- `DFW-2024-06-01-15` (AA, 46 other arrivals; 3 turns): the status quo overloads one runway and holds AA2321 (the disrupted flight, 141 min late) for 4 laps and MQ3321 for 2. Delay 249.9 to 189.6 min, fuel 1,668 to 444 kg.
- `CLT-2024-06-06-19` (AA, 49 other arrivals; 2 turns): runway capacity; AA2712 and AA2704 lap once in the status quo. Delay 175.0 to 169.9 min, fuel 1,164 to 276 kg.
- `LGA-2024-06-14-22` (DL and YX, 24 other arrivals; 2 turns): runway capacity and a runway conflict on LaGuardia's single arrival runway; YX5826 and YX4575 lap once in the status quo. Delay 953.2 to 953.1 min, fuel 436 to 132 kg.

Five 2015 `flights.csv` rows (few background arrivals: the file holds only late flights):
- `F2015-292602` MIA (AA; 2 turns): AA943, 312 min late, on 08L, gate A4. The status quo is legal and is agreed (delay 869.1 min, fuel 0).
- `F2015-152758` ATL (DL and WN; 2 turns): DL2256, 320 min late, on 27L, gate T11. The status quo fails GND-GATE-FREE; the agreed plan fixes the gates. Delay 1,314.5 to 1,308.2 min, fuel 92 to 596 kg.
- `F2015-75` DFW (AA; 2 turns): AA1344, 119 min late, to the remote stand. The status quo fails GND-GATE-FREE. Delay 362.6 to 351.9 min, fuel 0 to 4 kg.
- `F2015-77162` DEN (B6, DL and WN; 2 turns): DL927, 133 min late, on 16L, gate C35. The status quo fails GND-GATE-FREE. Delay 547.9 to 555.4 min, fuel 0 to 408 kg.
- `F2015-7053` ORD (AA; 2 turns): MQ3269, 419 min late, with four other flights landing on 32R within 12 minutes. The status quo fails GND-GATE-FREE and holds MQ3541 for a lap; the agreed plan moves MQ3043 and MQ3541 to 27L and fixes the gates. Delay 1,105.9 to 1,130.8 min, fuel 264 to 816 kg. (Recorded with d1-ultra earlier the same day, no plan passed CHECK in 3 turns and the safety net diverted two flights.)

Failure case, live (no recording): `F2015-208678` DEN (F9, UA and WN). In all three turns no plan on the table passes the engine CHECK, in every benchmark arm, so the log shows three `NO_CONSENSUS` and a `FALLBACK`: the safety net lands four flights on runway 07, two of them at the remote stand, and diverts WN240. The system declines to invent a plan it cannot prove legal.

## 6. What went wrong and what we fixed

- **First real-data run: 24 to 52% consensus.** Simulated crew shifts ignored real delays, so about 70% of flights were infeasible before any agent moved. Crew shifts are now anchored to the actual delayed ETA.
- **VRB wind closed every runway.** It is now treated as calm.
- **No wide gates.** OSM had none at many hubs; we add two simulated wide gates where it has none, badged.
- **Divert delay ignored existing delay.** Fixed.
- Formula consensus rose from 47% to 97% on 30 waves.
- **First benchmark: 47 to 74% model error rate.** Concurrency 16 with a 2 s timeout caused timeouts under load; we reran at concurrency 4.
- **Spec update at 12:45.** The challenge now wants 3 agents and turns on `flights.csv` rows, so we moved from N-pilot waves to 3-agent turns.
- **Final review found two bugs.** Weather delay was dropped from the ETA, and pilots diverted just to clear Ground's gate conflict. Both fixed.
- **First `flights.csv` benchmark picked a model that fell back to the solver on 32% of waves.** We added the consensus >= 90% gate to the selection rule.
- **The stress test showed that the three-agent plans hurt flights nobody spoke for.** On the two most disrupted 2024 waves ATC pushed airline flights later and nothing objected. We added one agent per airline and a fairness floor, and pilots that can say UNABLE. The first automatic pick (relaxing) still left 57.7 to 61.5% of agreed waves with some flight worse than its status quo. We shipped firm instead (user's choice, 4 Nov 2026), and it costs consensus (94 to 98%, a few more fallback waves) and about half a round.

## 7. Limits

- The workbook limits are synthetic planning numbers, and we relax or assume several of them (table above).
- Gate occupancy, fuel, crew shifts and connections are simulated. Outcome gains depend partly on those assumptions.
- Brier is measured against the formula verdict, not ground truth.
- n = 50 waves and one seed; small differences between arms are not significant. One wave is 2 points of consensus, so the one-wave tolerance in the fairness pick rule is the resolution of this benchmark. Model arms are not deterministic.
- The airline grouping is a static table (carrier code to airline, regionals under their major by hand). BTS has no marketing-carrier field, so a carrier with no row is its own airline: OO at ORD and EV at DFW stay their own airlines, and the floor protects each of them separately.
- Pilots refuse only on the `PLT-*` rules (earliest ETA, fuel reserve, crew duty) and wind limits. There is no runway-length rule in the rule set, so a pilot cannot refuse a runway for being too short.
- The firm floor has an escape clause: an airline accepts a plan below its floor only if no CHECK-passed plan on the table meets that floor. Without it a wave could not end. It means firm is not an absolute guarantee. No agreed plan in the benchmark had a flight worse than its status quo, but that is measured only on the 26 waves with a legal status quo. On the other waves the floor is the best plan seen, and the escape clause can let a flight end above it: the shipped DEN replay (`F2015-77162`) ends with WN130 0.5 min over its floor. If a floor-meeting plan reaches the table after an airline has voted, a plan that breaches that airline's floor cannot win that turn (the floor-meeting plan is carried into the next turn), except on turn 3, where there is no next turn.
- When the status quo is illegal, each airline's floor is the best legal plan seen for that airline, so floors can conflict and no plan may meet them all. Every firm fallback in the benchmark is on such a wave (section 5). A shared floor is the alternative; it is not built or measured.
- This build has more voters than the workbook's three agents (see "Deviation from the workbook's `rules_of_play`").
- Firm fairness costs consensus (94 to 98% against 98 to 100% for relaxing) and more rounds and model calls. 66 per-wave regressions against v3-traffic, up to 54.4 min of delay on one wave, are listed in section 5.
- The delay utility caps at 60 minutes, so flights that are hours late gain little from negotiation.
- The airborne flag ignores time zones (BTS waves); about 8% of flights are misclassified.
- METAR is the latest observation at or before the wave start (flights.csv rows) or the end-of-hour observation (BTS waves).
- Alternate airports may be small airports.
- p95 model latency is under 1 s per call in the shipped firm run (957 ms at most), but it was 1,306 ms for d1-ultra under relaxing.
- Utility weights are design judgements, not calibrated to airline cost data. Wake separation is a time-based simplification.
- Taxi validation bias: our taxi-in time (rollout + unimpeded routed taxi) is compared with BTS 2015 `TAXI_IN` on the focus flights (`eval/BENCHMARK.md`, 42 flights, mean bias -2.78 min). It runs low, on purpose and not corrected: BTS `TAXI_IN` includes ramp and gate waits that the model does not have (`rules.LIMITS` "taxi route": no ramp or gate waits, so it can sit below BTS TAXI_IN), and the 2015 stands are bound to today's OSM. The waits against fixed traffic are not in that comparison, and where a fixed route meets a departure stream head-on (DFW) the modelled time is above BTS, because the arrival cannot reroute.
- Paths: ground traffic is fixed once per wave and not re-timed per plan; some runway ends open in 2015 are missing from today's OSM and use a straight-line generic taxi (`src: "synth"`).
- Render free tier sleeps when idle.

## 8. Repo map and tests

```
backend/             backend
  server.py        FastAPI app, SSE, mounts /mcp and the built frontend
  decide.py        decision layer: model chain, timeout, breaker
  jev.py           Jev (TypeSafe) client
  mcp_server.py    read-only MCP tools
  geo/             CIFP and OSM surface parsers, gate binding, loaders (via the datastore)
  datastore/       Postgres (Neon) store, file fallback, seed datasets
  wave/            build, flights2015 (flights.csv waves), constraints (workbook limits), rules, moves, utility,
                   airlines, unable, fairness, graph, fallback, baseline, benchmark, narrate, replay,
                   route, trajectory, holding, surface, ground_traffic, wavegeo (paths and taxi)
  (negotiation.py, plans.py, scenario.py, baseline.py, evaluate.py: earlier single-flight mode, kept for back-compat)
frontend/        React dashboard (owned by the frontend teammate)
data/            challenge2/ (flights.csv, workbook), waves index, replay/ demo runs
eval/            benchmark outputs (BENCHMARK.md, benchmark.json)
scripts/         data fetch/extract; split_flights2015.py, split_arrivals.py; make_samples.py (API samples);
                 db_migrate.py, db_seed.py, db_dump_live.py, db_timings.py (Postgres)
docs/            design spec, API contract, graph.mmd, screens/ (README screenshots)
tests/           pytest suite
Dockerfile, render.yaml   deployment
```

Tests (739 offline, plus the opt-in `live_db` ones):

```bash
python3 -m pytest -q -m "not live_db"
cd frontend && npm test && npx tsc --noEmit && npm run build
```

## License

MIT
