# Final Approach engine v3: flight paths, holding and taxi traffic (design)

Date: 2 Nov 2026 · Branch at time of writing: `redesign/console` · Status: design, nothing built.
Builds on `docs/superpowers/specs/2026-10-31-disruption-negotiator-design.md` (v2 design) and
`docs/api-contract.md` (contract v2.1).

The user asked for "realistic flight paths (waypoints, holding patterns)" and "taxi patterns with traffic
simulation", for **engine and display**. The negotiation must use the real paths: holding laps burn fuel,
taxi times and ground conflicts change in-block times and become rules the agents respect. The radar and a
new airport-surface map show them, and the 50-wave benchmark is re-run. Goal: make the product as
impressive as possible as a portfolio piece. The user delegated every design decision; each is marked
**our call** below and collected in §14.

---

## 1. Intent

Today an arrival is a slot number. It is drawn on the extended centreline at 180 kt and taxis for exactly
6 or 10 minutes (`utility.taxi_min`: runway list and pier list are split into halves and paired). The
negotiation is right about separation, wind, fuel and gates, but the airport it negotiates over has no
shape.

v3 gives every flight a real path that the engine uses:

- **Airborne:** a 4D trajectory (lat, lon, altitude, time) from a published STAR entry fix, through the STAR
  and the instrument approach, to the runway threshold. Delay is absorbed as **whole holding laps** at a
  named fix in a vertically separated **stack**, plus a short path stretch (vectors). Each runway has its own
  route length, so changing runway now costs or saves real minutes and fuel.
- **Surface:** a taxi route on the real OpenStreetMap taxiway network from a chosen runway exit to the
  gate's stand, timed segment by segment, with the other traffic on the ground (fixed arrivals and real
  departures). Conflicts are resolved by deterministic hold-short waits. Those waits, and runway crossings
  that must fit gaps in the landing sequence, change in-block times. That flows into gate occupancy,
  connections, crew duty and every utility.
- **Display:** the radar flies blips along those exact trajectories (still scan-stepped), draws the
  racetracks where aircraft hold, and labels only the fixes in use. A new surface map shows taxiways,
  stands and aircraft moving on the same replay clock, with hold-shorts and conflicts highlighted.

The pitch line still holds: decision models decide, Claude explains, rules guarantee safety, the human
approves. v3 makes the thing they decide about physically real.

## 2. Goals, non-goals, anti-goals

**Goals**
1. Every wave flight and every fixed-traffic arrival has one engine-computed trajectory per plan. Metrics
   (hold minutes, laps, fuel, landing time, taxi time, in-block) are derived from that trajectory and
   nothing else.
2. Real data where it exists: FAA CIFP procedures, OSM surface, BTS 2015 taxi times and departures, METAR
   wind. Where it does not, a labelled synthetic fallback, so that **any** wave airport works.
3. New and upgraded rules (`ATC-HOLD-STACK`, `ATC-TAXI-CONFLICT`, real `ATC-TAXI-ROUTE`). Each cites a
   rule, a source and input values, like the current 12.
4. The 3-agent protocol is unchanged: ATC / PILOT:<focus> / GROUND, at most 3 turns, formula fallback,
   one `AGENT_TURN` per agent per turn. Agents gain one move kind (`hold`) and richer move labels.
5. An honest benchmark: the same 50 waves re-run after each milestone, the old results frozen, and every
   delta attributed to a physics change, a definition change or a negotiation change. The taxi model is
   validated against real 2015 BTS `TAXI_IN` and the errors are published.
6. The replay demo stays offline and model-free. Main stays deployable after every milestone.

**Non-goals**
- Full airborne conflict detection (lateral separation along STARs, departures in the air). Separation
  stays enforced where v2 enforces it: wake on final, runway capacity, plus the new stack levels.
- Winds aloft. Only the METAR surface wind is applied, to the final approach groundspeed.
- Departure runway sequencing. Departures appear only as fixed ground traffic, plus runway use for the
  crossing check.
- Agents choosing STARs, runway exits or taxi routes. The engine picks them deterministically (§6.7).
- Re-creating 2015 procedures. We use the current cycle and label it (§4.1).
- Certified navigation data. Everything is "not for navigation" (FAA CIFP disclaimer, §4.1).

**Anti-goals (failure even if it "works")**
- **Faking precision.** Synthesized geometry is never drawn or described as published. Every trajectory
  point and taxi segment carries `src: "cifp" | "osm" | "synth"`, and synthesized parts are drawn dashed.
- **Showing a path the engine did not use.** The API serves the same trajectory objects the metrics were
  computed from. A replay recorded under another engine version shows no paths, never paths recomputed
  after the fact (§8).
- A model deciding legality, or a rule without a source. Every new limit is a row in
  `rules.LIMITS`/`limits_for` with `source: workbook | real data | assumption`.
- Network access at runtime. The live service never fetches procedures, OSM or BTS.
- Dropping benchmark waves that newly fail. They are reported as failures.
- Fitting the taxi model to BTS taxi times so that it "validates". Speeds are fixed up front, and the
  validation measures the error, it does not tune it away.

## 3. Current state (verified by reading and running, 2 Nov 2026)

- `atm/wave/utility.py`: `TAXI_NEAR_MIN = 6`, `TAXI_FAR_MIN = 10`, `TYPICAL_TAXI_MIN = 6`. Taxi is the
  halves heuristic. Hold = `slot - eta_earliest - speed_reduction - ground_delay`. Fuel = hold x workbook
  burn (narrow 2400 kg/h, wide 6500 kg/h) + speed reduction x 20 %. `metrics(wave, a)` is per flight and
  independent of the other flights.
- `atm/wave/rules.py`: 12 rules. `ATC-TAXI-ROUTE` always passes ("no taxiway closures in data").
- `atm/wave/moves.py`: ATC `swap_order | change_runway | shift_slot` (±2 min) plus speed/ground-delay
  instructions for non-focus flights. PILOT and GROUND move sets as in contract v2.0. The earliest slot is
  runway-independent (`_min_slot`).
- Gates: `data/ref/gates.csv` holds OSM `aeroway=gate` refs for **10 hubs only** (ORD, ATL, DEN, DFW, LAX,
  JFK, CLT, MCO, LAS, PHX). They were fetched with `out tags`, so there are **no coordinates**.
  `flights2015._gates` uses OSM names where present, otherwise generic `A1..`, `W1/W2` and remote stand
  `RS`. MIA's replay wave runs on generic gates.
- Replays: `data/replay/` has 5 F2015 waves: ORD (F2015-270287), MIA (F2015-58926), ATL (F2015-69315), DFW
  (F2015-75) and DEN (F2015-77162). Each has 4 negotiated flights. The fixed queue has 1 to 13 arrivals,
  because `flights.csv` contains only the challenge's delayed-flight subset.
- Benchmark (`eval/benchmark.json`, generated 2 Nov 2026 09:08Z): 50 waves, seed 7, source `flights2015`,
  30 airports (LGA, LAX, FLL, PHX, MIA, DEN, EWR, ORD, BNA, SFO, BOS, AUS, IAD, DCA, TPA, SEA, BWI, ATL,
  MSP, IAH, JFK, SLC, MCO, DFW, MDW, LAS, DTW, PHL, CLT, SAN). Mean fuel ~108 kg/wave for every arm, so
  holding is small today. 16 of 50 status-quo plans are infeasible.
- Cost today: `rules.check` takes **0.11 ms** on the ORD replay wave. One formulas-mode negotiation calls
  `rules.check` **183 to 273 times** and finishes in 0.04 to 0.08 s. That is the budget baseline for §6.8.
- Frontend: `Radar.tsx` (straight centreline at 3 NM/min, 4.8 s scan, 5-dot trail, 1-min predictor),
  `Gantt.tsx`, `Strips.tsx`, `WaveScreen.tsx`. `components/AirportMap.tsx` still exists but **nothing
  imports it** (dead code; v3 replaces it).
- **Time base offset (existing):** BTS `SCHEDULED_ARRIVAL` / `ARRIVAL_TIME` are gate times and
  `WHEELS_ON` is touchdown. The engine treats `sched_min` and `eta_earliest_min = sched + BTS delays` as
  *landing* times and adds 6 min for scheduled in-block. Queue arrivals "land" at their scheduled gate
  time. §6.1 fixes this, attributed separately in the benchmark.
- **ICAO heuristic bug (existing):** `flights2015._icao()` builds ICAO by prefix. It produces PHGG for OGG
  (real PHOG), TJJU for SJU (TJSJ), KFCA for FCA (KGPI) and PHOA for KOA (PHKO). This affects METAR
  lookups today and would break CIFP/OSM joins. `data/challenge3/airports.dat` (OpenFlights) has the
  correct ICAO for all 320 destinations.
- `data/challenge2/flights.csv` has `WHEELS_ON`, `TAXI_IN`, `WHEELS_OFF` and `TAXI_OUT` per row (100 %
  filled). `scripts/split_flights2015.py` drops them.
- Deploy: Render free plan (`render.yaml`, Docker). `build.py` comments assume a 512 MB host. The
  Dockerfile copies `data/` wholesale. Repo licence: MIT.

## 4. Research findings

### 4.1 Procedures: FAA CIFP

- **Download:** the FAA CIFP page lists cycle **2611** (effective 29 Oct to 26 Nov 2026) and 2612 (from
  26 Nov). There is a direct zip URL, `https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip`
  (9.1 MB; a plain GET returned 200 with no click-through when tested). Contents: `FAACIFP18` (50.8 MB,
  397k fixed-width ARINC 424-18 records), `IN_CIFP.txt` (14,308 procedures), `Not_In_CIFP_2611.xlsx`, a
  readme and a disclaimer. The 28-day cycle is confirmed.
- **Terms:** the readme says that downloading means agreeing to the Customer Agreement for Error
  Notification (the website's "I Agree"). In short: pass any reported CIFP errors on to your users, and
  report errors you find to the FAA. The disclaimer says CIFP "is not copyrighted under Title 17 U.S.C.",
  is provided "as is", and the FAA "is not responsible for products derived from the CIFP". Derived
  products must carry the warranty disclaimer. **Our call:** commit derived extracts with
  that disclaimer in a `README` next to them, show "Procedures: FAA CIFP 2611, not for navigation" in the
  UI, and treat the error-notification duty as satisfied by pinning one cycle and noting it.
- **Record types we need (verified in the file):** PA (airport reference point, mag var, e.g. KORD
  `W003`, elevation 680 ft, transition altitude 18,000), PG (runway thresholds), PC (terminal waypoints),
  EA (enroute waypoints), D/DB (VHF/NDB navaids), PE (STAR legs), PF (approach legs) and PI (localizer).
  Leg fields used: fix ident (cols 30-34), waypoint description code (cols 40-43, where col 42 `F` marks
  the FAF, and `A`/`I` mark the IAF/IF), turn direction (col 44), path terminator (cols 48-49), course
  (cols 71-74, magnetic, tenths), time or distance (cols 75-78/79-82, e.g. `T010` = 1.0 min), altitude
  description and altitudes (col 83, cols 85-94). Route type (col 20) separates enroute transitions,
  common route and runway transitions on STARs (`BENKY6` at KORD has transitions `BFORD`, `BYLAW` and
  others, and runway transitions `RW04B`, `RW27C`, …).
- **Holding (important):** the CIFP readme states: "Waypoint description code H will not be used for
  arrival, SID, or STAR holding. Holding records (EP) are not available in the database." Confirmed: 0 EP
  records, and 0 hold legs on any STAR. Coded holds exist only inside approaches: **HF** (hold in lieu of
  procedure turn, 6,676 legs) and **HM** (missed-approach hold, 10,145 legs). These carry fix, inbound
  course, turn direction and leg time, e.g. `KORD I10C … CGT … R HM 3360 T010 +04000`. So **arrival
  holding geometry at STAR fixes is not in CIFP**. It must be synthesized from AIM rules and labelled as
  such (§6.3).
- **Coverage over the 320 flights2015 destinations** (correct ICAO from OpenFlights): **111** have STARs,
  **315** have instrument approaches, and 2 are absent from the file (PBI, ISN; verified by grep). All 30
  benchmark airports and all 5 replay airports have STARs and ILS/RNAV approaches. Per-airport legs:
  KORD 846 STAR / 663 approach, KMIA 533 / 325, KATL 486 / 422, KDFW 649 / 374, KDEN 786 / 766.
- **Parsers:** `cifparse` (PyPI 2.0.9, GitHub misterrodg/cifparse) targets CIFP directly but is
  **GPL-3.0**. `arinc424` (PyPI 0.3.0, jack-laverty/arinc424) is **MIT** but works record by record and is
  print-oriented. `pyarinc424` and PyARINC424 have no stated licence or docs. **Our call:** write our own
  fixed-column extractor (~250 lines) for the 9 record types above, in an offline script. It avoids a GPL
  dependency in an MIT repo, and it is all we need. Its output is cross-checked in a dev-only test against
  `arinc424` decodes of ~200 sample records.
- **Anachronism:** procedures are 2026; traffic is 2015 (and 2024 for BTS waves). **Our call:** use cycle
  2611 for every wave. Drop any STAR runway transition or approach whose runway is not in the wave's
  dated runway list (`build._runways(iata, date)` already winds back runways using
  `runway_history.csv`, e.g. ORD 9C/27C opened 2020). Badge the layer as
  `Provenance("procedures", "real", "FAA CIFP 2611 (current procedures flown over 2015 traffic)")`.

### 4.2 Airport surface: OpenStreetMap

Feature counts were fetched with Overpass on 2 Nov 2026 (`out count`), in the order taxiway ways /
taxilane ways / parking_position / gate nodes / runway ways / holding_position nodes:

| Airport | taxiway | taxilane | stands | gates | runways | hold pos. |
|---|---|---|---|---|---|---|
| KORD | 783 | 224 | 364 | 219 | 8 | 312 |
| KATL | 409 | 28 | 576 | 201 | 6 | 232 |
| KDFW | 267 | 120 | 377 | 168 | 8 | 161 |
| KDEN | 393 | 0 | 263 | 177 | 6 | 85 |
| KMIA (by `iata`) | 407 | n/q | 496 | 129 | 7 | n/q |
| KBNA | 263 | 0 | 87 | 51 | 10 | 85 |
| KABE (small) | 86 | 0 | 0 | 8 | 3 | 27 |

(n/q = not queried.) Every replay hub is mapped in detail. Small airports have taxiways but often no
stands. `aeroway=holding_position` nodes exist, so hold-short points can be real. Robustness: the
`area[icao=…]` query **returned HTML errors for KMIA and KGPI**, while `area[iata=MIA]` worked. The fetch
script therefore tries ICAO, then IATA, then a 6 km bbox around the airport reference point, sleeps
between calls and caches the raw JSON. OSM data is ODbL: attribution "© OpenStreetMap contributors"
goes in the UI and README, and the derived files are ODbL (the repo already commits OSM-derived
`gates.csv`, so there is precedent).

### 4.3 BTS taxi and movement data

- Real taxi-in in the challenge file (2015 delayed subset), median / p90 / mean in minutes: ORD 12 / 28 /
  15.6, DFW 10 / 21 / 12.2, ATL 8 / 14 / 9.3, JFK 8 / 15 / 9.5, MIA 7 / 14 / 8.5, DEN 7 / 12 / 8.4, LGA
  7 / 17 / 9.6, BNA 6 / 9 / 6.4, ABE 4 / 6 / 4.1. The 6/10-minute heuristic is badly wrong at ORD and DFW.
- `TAXI_IN` is wheels-on to gate-in, so it **includes gate waits and ramp delays**. A routed unimpeded taxi
  time should come out below it. We report that bias, we do not remove it.
- The full 2015 BTS On-Time file per month is available as a PREZIP (verified:
  `…On_Time_Performance_1987_present_2015_1.zip`, 23 MB). It contains every arrival **and departure**, with
  `WheelsOff`, `TaxiOut`, `WheelsOn` and `TaxiIn`. `scripts/fetch_bts.py` already handles the PREZIP format.

### 4.4 Realism numbers (with sources)

| Quantity | Value used | Source / status |
|---|---|---|
| Holding pattern turn direction | right (standard); left if published | AIM 5-3-8 ("Standard Pattern. Right turns") |
| Inbound leg | 1 min at or below 14,000 ft MSL; 1.5 min above | AIM 5-3-8 Timing |
| Max holding speed | 200 KIAS to 6,000 ft; 230 KIAS 6,001-14,000 ft; 265 KIAS above | AIM 5-3-8 Airspeeds |
| Turn performance in hold | 3°/s, 30° bank, or 25° bank with flight director, whichever needs least bank | AIM 5-3-8 |
| Resulting lap time | ~4.6 min (200 KIAS, 5,000 ft) to ~5.3 min (230 KIAS, 12,000 ft); computed per hold, not a constant | derived (TAS ≈ IAS x (1 + 2 %/1,000 ft), assumption) |
| Stack vertical separation | 1,000 ft (up to FL410) | FAA JO 7110.65 §4-5-1 |
| Stack exit order | bottom first, i.e. first come, first served | JO 7110.65 §2-1-4 ("first come, first served as circumstances permit") + §4-5-1 (cannot descend through occupied levels). Ops rule, labelled |
| Stack size | 5 levels from the hold's base altitude | assumption |
| Holding fuel | narrow 2,400 kg/h, wide 6,500 kg/h → ~210 / ~575 kg per lap | challenge workbook (unchanged) |
| Speed below 10,000 ft | ≤ 250 KIAS | 14 CFR 91.117(a) |
| Terminal speed profile | 280 KIAS above 10,000 ft, 250 below, 210 from IAF, 170 at FAF, 140 kt final | assumption (140 kt matches the workbook's wake conversion) |
| Approach category | Cat A < 91 kt … Cat D 141-165 kt | 14 CFR 97.3 |
| Final glidepath | CIFP vertical angle, else 3° | CIFP PF final leg / assumption |
| Taxi speed | 20 kt on straight taxiway, 10 kt in turns > 30° and on taxilanes | Sadono et al., ITB (simulation paper, weak source; flagged as assumption and validated against BTS, §10) |
| Exit types | right-angle and high-speed (30°) exits; acute exits unusable in the opposite direction | FAA AC 150/5300-13B Chg 1 §4.8 and definitions 38 |
| Exit choice | first exit beyond a required roll distance by size class (narrow 1,500 m, wide 2,000 m, wet x1.15) | assumption informed by AC 150/5300-13B Fig. 4-18 (cumulative exit probability by AAC; graph only); recorded in `limits` |
| Hold-short / crossing clearances | ground control issues routes and hold-short instructions | JO 7110.65 §3-7-2 |
| Runway crossing gap | no landing on that strip in [t − 60 s, t + 90 s], no takeoff in [t − 60 s, t + 60 s] | assumption, labelled |
| Departure stand dwell / taxi-out | real `WHEELS_OFF − TAXI_OUT` pushback time | BTS 2015 (real) |

## 5. Data pipeline

All fetching is offline. Each script writes raw files into `.cache/` (gitignored and dockerignored) and
derived compact files into `data/ref/…` (committed). The live service only reads committed files. This is
the existing rule from the v2 design (§4 there).

| Script (new or changed) | Input | Output (committed) | Est. size |
|---|---|---|---|
| `scripts/fetch_cifp.py` (new) | pinned `CIFP_261029.zip`, SHA-256 recorded in the script | `data/ref/procedures/{ICAO}.json.gz`, one per destination airport that has CIFP data: fixes used, STARs (transitions, common route, runway transitions as fix lists with altitude/speed constraints, leg types), one preferred approach per runway end (ILS, then RNAV, then other) from IAF/IF through FAF to threshold, HF/HM holds, mag var, elevation | ~1-3 MB total (scaled from ~160k procedure legs nationally, keeping only STARs and final approaches) |
| `scripts/fetch_surface.py` (new) | Overpass per airport (taxiway, taxilane, runway, parking_position, gate, holding_position, apron, terminal; `out geom`) | `data/ref/surface/{ICAO}.json.gz`: simplified directed graph (nodes at intersections and turn points, edges with length, name, kind, turn angle, runway-crossing flag), runway exits per runway end, stands (id, ref, lat/lon, wide hint), holding positions, drawing polylines | ORD ~80-150 KB gz; ~5-12 MB for 320 airports (estimate, measured in M0) |
| `scripts/fetch_bts.py` (extend) | 2015 PREZIP months | `data/ref/movements2015/{IATA}.csv.gz` for the **top 30 airports by 2015 arrivals**: every arrival and departure with tail, sched/actual, wheels on/off, taxi in/out | ~30-45 MB est.; hard cap 50 MB, otherwise keep only ±2 h around each hour that holds a flights.csv row |
| `scripts/split_flights2015.py` (extend) | `flights.csv` | add `wheels_on, taxi_in, wheels_off, taxi_out` to `data/challenge2/flights/{DEST}.csv.gz` | +~2 MB |
| `scripts/fetch_ref.py gates` (retire) | – | gates now come from the surface file. `gates.csv` stays as the id source for the 10 hubs, so scheduled-gate seeding is unchanged | – |
| `atm/wave/icao.py` (new, tiny) | `data/challenge3/airports.dat` | IATA→ICAO lookup replacing `flights2015._icao` | – |

The **derived files are the contract with the engine**. Each starts with a header holding the source,
fetch timestamp, cycle or OSM timestamp, script version and quality tier (§6.9). A `data/ref/GEO_COVERAGE.md`
table is generated in M0: per airport, the tiers and the counts behind them.

**Gate → stand mapping (our call):** keep v2's gate selection exactly (same ids, same seeds), so every wave
keeps its gates and scheduled gates. Attach coordinates in this order:
(1) a `parking_position` whose `ref` equals the gate ref;
(2) the same after normalisation (`B12` ↔ `12` inside terminal/pier B);
(3) the parking_position nearest the `aeroway=gate` node within 150 m;
(4) the gate node itself, snapped to the nearest taxilane node.
Generic ids (`A1..`, `W1/W2`) are bound to real OSM stands nearest the main `aeroway=terminal` in
deterministic order. `RS` goes to the stand farthest from the terminal on a connected apron. Each gate
carries `geo_src: "osm-ref" | "osm-near" | "osm-generic" | "synth"`, shown in the UI.

**Render constraints:** the image grows by ~40-60 MB (within reason, since `data/` is already 192 MB). At
runtime, procedures and surface are loaded per airport through `functools.lru_cache(maxsize=4)` like
`_arrivals`. The ORD graph is ~15k raw nodes, simplified to a few thousand: well under 30 MB resident.
M0 measures peak RSS on ORD and adds a test that fails above 60 MB per airport.

## 6. Engine model

### 6.1 Time base (definition fix, attributed separately)

**Our call:** fix the existing offset (§3), as its own step and its own benchmark row, so it is never
mixed up with physics changes.

- Scheduled in-block = BTS `sched_arr` (`sched_min`). Delay = `in_block − sched_min`. The `+6` goes.
- Earliest landing (`eta_earliest_min`, keeping the field name and meaning "earliest touchdown on the
  nominal runway") = `sched_min + attributed BTS delays − taxi_in_ref`. Here `taxi_in_ref` is the
  flight's real `TAXI_IN` when known (flights2015 rows), otherwise the airport's 2015 median. For a real
  row this lands at the real `WHEELS_ON`, consistent with v2's "delays already baked in" assumption.
- Fixed-traffic arrivals land at their real `WHEELS_ON` when known, otherwise at `sched − taxi_in_ref`.
- Crew and turnaround floors that use `TYPICAL_TAXI_MIN` (`build.py:462`, `flights2015.py:320`) switch to
  the flight's unimpeded routed taxi time, so they stay feasible by construction.

### 6.2 Airborne 4D trajectory (`atm/wave/trajectory.py`, new)

For each flight and each open runway end, a **route** is built once per wave and cached:

1. **Entry and STAR (our call):** choose the STAR enroute transition whose first fix lies closest in
   bearing to the great-circle inbound course from the flight's origin airport. Ties go to the lower
   procedure id. The route then follows the transition, the common route, and the runway transition for
   the assigned runway, or the "all runways" transition (`RW…B`) if there is one.
2. **STAR to approach:** if the last STAR fix is an IAF/IF of the runway's preferred approach, the route
   joins it there. Otherwise, and also when the STAR ends in vector legs (`VM`/`FM`), the route
   **synthesizes vectors**. These are a downwind parallel to the runway, 5 NM abeam, then base, then a join
   at the approach's IF (or 2 NM before the FAF) at a 30° intercept. Marked `src: "synth"`.
3. **Approach:** IF → FAF → threshold from the PF legs, using the PF final's vertical angle (else 3°).
   `RF` arcs become chord segments every 10°. `TF/CF/DF/IF` legs are fix to fix. Missed approach is
   ignored.
4. **No STAR (209 of 320 airports):** a synthetic **corner-post** arrival. The entry point is 40 NM out on
   the nearest of 4 corner bearings (045/135/225/315° true) to the origin direction. From there, direct
   to the approach IAF (or vectors as in step 2). The hold is at the corner post. If there is no approach
   either, a 3° straight-in final from 10 NM is used. All `synth`.
5. **Speeds and altitudes:** the §4.4 speed profile, converted to TAS, gives the time per segment.
   Headwind from the METAR applies on final only. Altitudes interpolate between CIFP altitude constraints
   and follow the glidepath inside the FAF. Each point stores `alt_ft` and `alt_src: "constraint" |
   "interp"`.
6. **Route time** `T_route(flight, rwy)` is the time from the entry fix to the threshold. **Entry time**
   comes from the flight: `t_entry = eta_earliest_min − T_route(flight, nominal_rwy)`, where `nominal_rwy`
   is the runway the FCFS plan gives it. In M1 the FCFS plan therefore lands exactly when v2's did. M2a
   (§6.1) then shifts every landing earlier by `taxi_in_ref`, and that shift is attributed separately.

**Earliest landing becomes runway-dependent:** `earliest(f, r) = t_entry + SR + GD + T_route(f, r)`,
with SR the speed reduction (absorbed before entry; the blip appears later) and GD the ground delay.
Moving a flight to the far-side runway can now cost several minutes, and moving it to a runway its STAR
feeds directly can save them. This is the most consequential realism gain for the negotiation.

**Delay absorption in the terminal area (our call):** `D = slot − earliest(f, r) ≥ 0` (otherwise
`PLT-EARLIEST-ETA` fails, as today). It is split into
`laps = floor(D / lap_min(hold))` whole laps at the hold fix, plus `vector_min = D − laps x lap_min`
(0 ≤ vector < one lap) of path stretch, drawn as an extended downwind (`synth`, dashed). Any slot the
engine accepts today stays expressible, so no new quantisation veto appears. Laps, the fix and the level
are now explicit, drawn and costed. `hold_min = laps x lap_min + vector_min` keeps its v2 meaning.

**Fuel (our call):** `fuel_kg = (hold_min + route_extra_min) x burn + SR x burn x 0.20`, where
`route_extra_min = T_route(f, r) − T_route(f, nominal_rwy)` (may be negative; floored at −hold so fuel
never goes below zero). Terminal-area burn uses the workbook holding burn as a proxy (assumption,
recorded in `limits`). `PLT-FUEL-RESERVE` checks `hold_min + max(0, route_extra_min) ≤ extra_min`.

**Expect laps to be rare (measured, stated up front).** Across the 100 agreed plans recorded in
`data/replay/` and `eval/runs/` (397 flights), only **6 flights hold at all and 4 hold ≥ 4.6 min**
(one lap). Mean hold is 0.41 min per flight; max is 40.3. The reason is that the BTS-attributed delay is
already baked into `eta_earliest`, so the sequence rarely has to absorb more. In most waves, then, v3's
airborne change shows up as runway-dependent route time and vector stretch, not racetracks. We do not
inflate holding to make the picture busier. Instead:
(a) the benchmark reports the share of agreed plans with ≥ 1 lap;
(b) the 5 demo replays are re-chosen in M1, keeping one per hub where possible, so that at least 3 of them
show a full lap in the agreed or status-quo plan (a search over flights2015 rows at those hubs, ranked by
FCFS hold);
(c) the radar's empty-hold case stays honest, with no racetrack drawn.

**Fixed traffic** (queue arrivals) gets the same treatment with zero laps. It flies the nominal route
timed to land at its fixed time, so it shows on the radar.

### 6.3 Holding and the stack

- **Hold fix (our call):** the last fix of the STAR common route, which in US practice is typically the
  meter fix, where STAR charts depict holding. If that fix carries a coded `HF`/`HM` hold in the airport's
  approaches, that published geometry is used (inbound course, turn, leg time: `src: "cifp"`). Otherwise a
  standard AIM hold is synthesized: inbound course = route course into the fix, right turns, leg time by
  altitude (`src: "synth"`, labelled "standard hold synthesized at published fix; STAR holds are not
  coded in CIFP"). Base level = the fix's CIFP altitude constraint (e.g. BENKY 12,000 ft at KORD), else
  the corner-post/IAF altitude, else 6,000 ft AGL.
- **Lap geometry and time** follow §4.4: legs and turn radius come from TAS and bank. This gives the
  racetrack polygon drawn on the radar (~10 x 5 NM at 12,000 ft).
- **Stack:** only flights with `laps ≥ 1` enter the stack; vector-only flights do not. Each holding aircraft at a fix gets the lowest free level, in steps of 1,000 ft, when it
  arrives at the fix. It steps down as lower levels empty. It leaves from the bottom.
  `ATC-HOLD-STACK` (§6.6) checks level count and exit order.

### 6.4 Surface (`atm/wave/surface.py`, new)

- **Graph:** a directed graph from the derived surface file; both directions for every taxiway. Edge time
  = length / speed, where speed is 20 kt, or 10 kt when the turn into the edge exceeds 30° or the edge
  is a taxilane, plus 30 s to park at the stand.
- **Exit choice (deterministic):** on the landing runway end, take the first exit whose distance from the
  threshold is at least the required roll (§4.4). Acute exits that point backwards are skipped. Runway
  occupancy time = time to roll to the exit
  (`2 x d / (v_td + v_exit)`, v_td = final groundspeed, v_exit 25 kt for high-speed exits and 12 kt for
  right-angle exits; assumption).
- **Route:** the unimpeded shortest-time path from the exit node to the stand node. It is precomputed
  per airport with one Dijkstra per exit and cached; ties break by node id. Route names (`B, A, A10`)
  come from the OSM `ref`.
- **Ground traffic (fixed, plan-independent):** queue arrivals, and, where §5 movements data exists,
  **real 2015 departures** in the window (pushback at `WHEELS_OFF − TAXI_OUT`, takeoff at `WHEELS_OFF`).
  Departures leave from a seeded stand in the carrier's most-used pier area (stand `simulated`, badged)
  and go to the holding point of the departure runway (assumption: the arrival flow's runway on mixed-mode
  airports, else the parallel departure runway in the same direction). Fixed traffic is scheduled **once
  per wave**, first-come-first-served by start time, and becomes a reservation table. This matches v2's
  convention that fixed traffic is never vetoed and wave flights yield to it.
- **Wave flights per plan:** in order of runway-exit time, each wave flight moves along its fixed route.
  At each node it waits (hold-short at the previous node, or at the OSM `holding_position` before a
  runway) until:
  (a) the node is free of every earlier reservation by ≥ 30 s headway;
  (b) the next edge is not in use in the opposite direction;
  (c) for a runway crossing, the crossing gap of §4.4 is met against the plan's landings and the
  departures.
  This is the single-path case of Safe Interval Path Planning (Phillips & Likhachev 2011). It is
  deterministic, linear in path length x reservations, and needs no rerouting. **Our call:** no
  rerouting, which is also how a ground controller works (standard route plus hold-short instructions).
  Each wait records `{node, from_min, to_min, reason: "hold short 27L/09R for landing UA12" | "give way to
  AA301"}`.
- **Outcome:** `taxi_min = unimpeded + waits`, and `in_block = landing + ROT + taxi_min`. A wait longer
  than the 30-min search horizon, or a wave flight that cannot leave the runway (exit node blocked
  beyond the next landing's slot on that strip), is a **rule failure**, not a long wait (§6.6).
- **Tiers when OSM is poor (§6.9):** with no usable graph, taxi is a straight line from the runway's
  midpoint to the gate point x 1.4 tortuosity at 15 kt (`synth`). There are no ground conflicts, the
  surface map shows "surface not modelled for this airport", and `ATC-TAXI-CONFLICT` reports "not
  modelled" (pass, informational), like `ATC-WIND-LIMITS` without a METAR today.

### 6.5 Metrics (contract `FlightMetrics`, additive)

Joint, no longer per flight. `utility._metrics_map(wave, plan)` becomes the only entry point, because the
stack and taxi waits depend on the other flights. **`metrics(wave, a)` is removed.** A per-flight
wrapper cannot return correct values once waits and levels depend on other flights. The call sites that
must move to the joint map:
- `moves.py` (lines 19, 228, 242);
- `graph.py` (lines 54, 86, 167, 178, 712);
- `rules.check` / `rules._duty_check` (already take `mm`; their default `mm` construction changes);
- `edit.py` and `/api/evaluate` (already go through `evaluate`).

New fields: `hold_laps`, `hold_fix`, `hold_level_ft`, `lap_min`,
`vector_min`, `route_min`, `route_extra_min`, `exit`, `rot_s`, `taxi_unimpeded_min`, `taxi_wait_min`,
`taxi_min`. Changed meaning: `in_block_min = slot + ROT + taxi`, and `delay_min` per §6.1. `fuel_kg` now
includes route extra. ATC's `taxi` utility term becomes
`(taxi_min − best unimpeded taxi for that flight over all open runways and its gate) / 10 min cap`.
Weights are unchanged, so a benchmark difference comes from the physics, not from retuning.

### 6.6 Rules (12 → 14)

| Rule | Status | Check | Source |
|---|---|---|---|
| `PLT-EARLIEST-ETA` | changed | slot ≥ `earliest(f, r)` (runway-dependent); evidence adds `route_min`, `entry_fix`, `t_entry` | ops rule + CIFP route |
| `PLT-FUEL-RESERVE` | changed | `hold_min + max(0, route_extra) ≤ extra_min`; evidence adds laps, lap_min, fix | workbook + 14 CFR 121.639 (as today) |
| `ATC-HOLD-STACK` | **new** | at each hold fix: simultaneous holders ≤ 5 levels; holders leave bottom first (a later arrival at the fix may not leave before an earlier one still holding); level ≥ base altitude | JO 7110.65 §4-5-1 (1,000 ft), §2-1-4 (FCFS); 5 levels = assumption |
| `ATC-TAXI-ROUTE` | upgraded, can fail | a connected route exists from an exit of the assigned runway end to the gate's stand; lists route names, runway crossings and unimpeded minutes | OSM surface (real) / synth tier |
| `ATC-TAXI-CONFLICT` | **new** | after deterministic hold-short scheduling: every wave flight reaches its stand within the 30-min horizon, vacates the runway before the next landing on that strip, and every crossing meets the gap; reports the waits | JO 7110.65 §3-7-2; headway and gap values = assumption |
| `GND-GATE-FREE`, `GND-GATE-NOTICE`, `PLT-CREW-DUTY`, `GND-CREW-DUTY` | inputs change | use the new `in_block`; `PLT-CREW-DUTY`'s "unavoidable bust" test uses the earliest in-block **minimised over open runways** (earliest is now runway-dependent) | unchanged |
| others | unchanged | | |

**Naming (our call):** the user floated `GND-TAXI-CONFLICT`. In this product, `GROUND` is ground handling
(gates and crews). Taxi movement and hold-shorts are tower ground control's authority (JO 7110.65 Ch. 3),
so the new rule is `ATC-TAXI-CONFLICT`, owner ATC. Waits on the ground are not violations; they cost time
in `in_block` and every agent feels that through its utility. Only an unresolvable situation vetoes.

### 6.7 Moves, agents, fallback

- **New ATC move `hold`** `{callsign, laps: 1..4}`: re-slot the flight to the earliest wake-legal slot ≥
  `earliest + laps x lap_min` on its runway. Its label states what it costs, e.g.
  `Hold DL1234 2 laps at BENKY (FL120, 10.6 min, ~420 kg)`. `shift_slot ±2` stays, and its label now reads
  "vector +2 min (path stretch)".
- `change_runway` uses the runway-dependent earliest slot (`moves._min_slot` takes the runway). Its label
  includes the route delta, e.g. `DL1234 → 27R (+3.2 min route, crosses 27L on taxi, taxi 14 min)`.
- `reassign_gate` / `swap_gates` labels include the taxi minutes per option.
- **Not moves (our call):** STAR choice, runway exit and taxi route. They are deterministic engine
  outputs. Reasons: they are tactical controller choices, not part of the challenge's "revised arrival
  time, gate assignment or flight-plan adjustment"; they would multiply move lists past what elephant-1's
  16k context and the 12-move cap allow; and the formula fallback stays exactly as it is.
- **Agent views** (`graph._view`) gain one line per flight, for example
  `STAR BENKY6.BFORD · hold fix BENKY base 12,000 · lap 5.3 min = 213 kg · taxi 27R→B21 9.5 min (+1.5 hold-short 27L)`.
  Budget: each move label ≤ 120 characters, and the largest prompt in the 50-wave benchmark is measured
  before and after; it must stay ≤ 8k tokens (half of elephant-1's context). Tested.
- `fcfs_plan`: unchanged logic, but floors use `earliest(f, r)` and the default runway comes from the queue
  (unchanged). The deterministic fallback solver is unchanged; it just scores the new metrics.

### 6.8 One source of truth, and the compute budget

- `trajectory.build(wave, plan) → TrajectorySet` and `surface.simulate(wave, plan, trajs) → TaxiSet` are
  pure functions. `utility` derives every metric from them. The API serialises the **same objects**. A
  test asserts, for every plan in the benchmark, that trajectory touchdown = `slot_min`, last taxi point =
  `in_block_min`, `laps x lap_min + vector_min = hold_min`, and that time is monotonic.
- **Precomputed per wave (cached by wave id):** routes and `T_route` per (flight, open runway end), hold
  geometry, the exit table, the unimpeded route table, and the fixed-traffic reservation table.
- **Per plan:** assemble ≤ 5 wave trajectories, run the stack, and run the hold-short pass for ≤ 5 wave
  flights. Results are memoised by `(wave_id, plan.id)` (plan ids already hash the assignments).
- **Budget:** `utility.evaluate` p95 ≤ 3 ms on the ORD replay wave, up from 0.11 ms. That means ≤ ~1 s
  added per negotiation at ~270 checks, and ≤ ~5 min CPU for a 4-arm, 50-wave benchmark. It is enforced by
  a perf test. If it is exceeded, the build fails. A rule is never skipped silently.

### 6.9 Coverage tiers and provenance

`Provenance.layer` gains `procedures`, `surface` and `ground_traffic` (contract change). Statuses:

| Layer | real | generic (synth) |
|---|---|---|
| procedures | CIFP STAR + approach (111 airports) | approach only + corner-post arrival; or straight-in only (PBI, ISN) |
| surface | OSM graph that passes the quality gate: every open runway end reaches ≥ 80 % of wave stands | straight-line taxi, no conflicts |
| gates (geo) | `osm-ref` / `osm-near` | `osm-generic` / `synth` |
| ground_traffic | BTS 2015 full movements (top 30) | challenge-file subset only ("partial"), badged |

## 7. API contract v2.2 (additive; commit prefixed `contract:`)

All additions are optional fields or new endpoints. v2.1 clients keep working.

- `Provenance.layer` += `"procedures" | "surface" | "ground_traffic"`.
- `RuleId` += `"ATC-HOLD-STACK" | "ATC-TAXI-CONFLICT"`. `Move.kind` += `"hold"`.
- `FlightMetrics` += the §6.5 fields, all optional.
- `Gate` += `lat?, lon?, geo_src?`.
- `WaveScenario` += `engine_version: string`, `geo_available: boolean`.
- `NegotiationResult` += `engine_version`,
  `trajectories?: { final: TrajectorySet; baseline: TrajectorySet }` (embedded, so the final plan never
  needs recomputation).
- `BenchmarkReport` += `engine_version`, `coverage` (tier counts), and `taxi_validation` (§10).
- `GET /api/wave/{id}/geo` → `WaveGeo` (static per wave and data version, gzip, ETag):
  ```ts
  interface WaveGeo {
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
  ```
- `POST /api/trajectories` `{ wave_id, assignments: Assignment[] }` → `TrajectorySet`. It is
  deterministic and model-free, and used for intermediate proposals and duty-manager edits.
  `POST /api/evaluate` gains `include_trajectories?: boolean`.
  ```ts
  interface TrajectorySet {
    plan_id: string; engine_version: string;
    air: { callsign: string; wave: boolean; runway: string; appears_min: number;
      points: { t_min: number; lat: number; lon: number; alt_ft: number; kind: "entry" | "fix" | "hold" | "vector" | "final" | "threshold"; fix?: string; src: "cifp" | "synth" }[];
      hold?: { fix: string; laps: number; level_ft: number; entry_min: number; exit_min: number; lap_min: number } }[];
    ground: { callsign: string; kind: "arrival" | "departure"; wave: boolean; exit?: string; stand: string; route_names: string[];
      points: { t_min: number; lat: number; lon: number; node?: string }[];
      waits: { lat: number; lon: number; from_min: number; to_min: number; reason: string }[];
      unimpeded_min: number; taxi_min: number; src: "osm" | "synth" }[];
    conflicts: { rule: RuleId; callsign: string; at_min: number; lat: number; lon: number; detail: string }[];   // failing checks, mapped
  }
  ```
  Points are piecewise-linear at constant speed between consecutive points. The engine adds enough
  points (arcs every 10°, racetracks every 15°) that linear interpolation is exact to < 0.1 NM. The client
  interpolates; it never invents geometry.
- **Samples:** `scripts/make_samples.py` regenerates `frontend/src/api/samples/*.json` (adding `geo.json`
  and `trajectories.json`) from the ORD replay wave. `frontend/src/api/types.ts` is updated in the same
  `contract:` commit.

## 8. Replays and recorded runs

- Each replay file gains `engine_version` (`"v3-air"`, `"v3-taxi"`, `"v3-traffic"` by milestone; v2 files
  have none, which means `"v2"`).
- **Our call:** re-record all 5 replays at every milestone that changes metrics. Use `decision` mode with
  the default chain (cost ~$0.002 per wave per the README's measured numbers: jev-1.14 ~$0.0005 plus the Haiku brief ≤ ~$0.0016). The recording script falls back to
  formulas if no keys are present and labels the file accordingly. The live replay path stays model-free.
- If a replay's `engine_version` differs from the server's, `negotiate-wave?replay=true` still streams the
  recorded messages, but `result.trajectories` is absent and `/api/trajectories` returns
  `409 engine_mismatch` for that run. The UI shows "recorded on engine v2; paths not available" and draws
  no paths. This is the anti-goal guard. Unit-tested.
- `eval/runs/*.json` (untracked run logs) are left alone; they are not served as replays.

## 9. Display (frontend spec; see §15 on who builds it)

- **Radar:**
  - Blips sample the engine's air trajectory at 4.8 s scan steps (`SCAN_MIN` unchanged). The history trail
    is the last 5 scan positions; the predictor is the trajectory 1 min ahead (no longer straight).
  - Range options become 10/20/40/80 NM, so STAR entries (often 40-100 NM out) can be shown.
  - Holding racetracks are drawn only for holds that someone uses in the shown plan, with the stack level
    count beside them (`BENKY ×3 · 12-14k`).
  - Fix labels are sparse: entry fixes, hold fixes and the FAF of runways in use, at most ~8 labels,
    with others shown on hover.
  - `synth` segments (vectors, synthesized holds and corner posts) are dashed, and a legend says "dashed
    = synthesized, not published".
  - The data block line 2 shows altitude (`120↓`) and, while holding, `HLD 2/3` (lap 2 of 3).
  - Aircraft appear at `appears_min`. Before that they are listed in strips as "inbound, entry BFORD
    T+12".
- **Surface map (new; replaces the unused `AirportMap.tsx`):**
  - It draws taxiways, runways, stands and holding positions to scale from `WaveGeo.surface`, and runs
    on the **same replay clock** `t` as the radar (one shared time source in `WaveScreen`).
  - Aircraft symbols move along `ground.points` at 1 s steps (surface-radar-like refresh; assumption). Wave
    flights are coloured by tone as on the radar; fixed traffic and departures are grey.
  - An active wait shows an amber bar across the taxiway at the hold-short point, with its `reason`
    on hover.
  - Failing `conflicts` are drawn red at their location for rejected proposals. This is the ground
    counterpart of the Gantt's red rejection flash.
  - Runway crossings flash the crossed runway briefly.
  - Attribution "© OpenStreetMap contributors" is always visible. For generic surface, the map is replaced
    by a short note.
- **Layout (our call):** the radar and surface map share one panel with an automatic hand-off. The radar
  shows until the focus flight's touchdown and the surface map after it, with a manual toggle. Both are
  available side by side on wide screens.
- **Gantt:**
  - The runway lane is unchanged, plus a hatched "holding" bar before the slot that shows laps (each lap a
    segment).
  - The gate lane bar starts at in-block as today. A thin "taxi" segment from touchdown to in-block is
    drawn in front of it, with any ground wait in amber.
  - Dragging a slot later updates laps and fuel live through `/api/evaluate?include_trajectories`.
- **Strips:** add the STAR/entry fix, `HLD BENKY 2L 120`, the exit, and `TXI 9.5 (+1.5)`.
- **Panels / rule checklist:** the two new rules and their evidence (levels, waits, crossings) appear in
  the existing checklist without UI changes beyond the new rule ids.

## 10. Benchmark re-run plan

1. **Freeze** the current results before any engine change. Copy `eval/benchmark.json` and
   `eval/BENCHMARK.md` to `eval/archive/engine-v2/`, and add `engine_version: "v2"` to the frozen copy.
2. **Same inputs:** the same 50 wave ids, seed 7, the same 4 arms (jev-1.14, d1-ultra, elephant-1,
   formulas), the same selection rule. A wave that no longer builds or negotiates is listed in `failures`,
   never dropped.
3. **Re-run after each milestone** (M1, M2, M3, and M2a for the time-base fix). Each re-run costs
   < $1. `BENCHMARK.md` gains an **engine history** table: one row per engine version, showing baseline
   (FCFS) and each arm for delay, fuel, holding laps, taxi wait, pax protected, overtime, consensus, rule
   violations in agreed plans (must stay 0), and status-quo feasible count.
4. **Attribution:** for each step, report
   (a) the **physics delta**: the FCFS baseline under engine N vs N−1, i.e. how much the world changed;
   (b) the **negotiation delta**: arm minus baseline within the same engine, i.e. what the agents add;
   (c) per-rule veto counts for the new rules.
   Only (b) is comparable across engines as "agent quality". Headline claims use (b).
5. **Taxi validation (no fitting):** for every flights2015 benchmark focus flight on a real surface tier,
   compare routed `taxi_min` under the FCFS plan with that flight's real BTS `TAXI_IN`. Publish MAE, bias
   and per-airport medians against the 2015 BTS medians (§4.3), plus the 5 worst cases with a likely
   cause. Expected bias: routed < real, because BTS includes gate waits. That is stated, not corrected.
6. **Coverage:** the share of waves per tier (procedures, surface, ground traffic) appears in the report and
   the UI Benchmark tab.
7. **Regressions** (agreed-plan delay or fuel worse than under v2 for the same wave) are listed per wave
   with the cause, e.g. "far runway now costs 3.2 min route".

## 11. Phasing (each milestone leaves `main` deployable)

| Milestone | Scope | Exit criteria |
|---|---|---|
| **M0 Data + coverage** | `icao.py` fix; `fetch_cifp.py` + extractor + tests; `fetch_surface.py` + graph builder + quality gate; movements extract; `GEO_COVERAGE.md`; nothing changes at runtime except the ICAO fix (its METAR effect is noted) | derived files committed; sizes measured against §5 caps; coverage table; all 129 existing tests pass |
| **M1 Airborne (`v3-air`)** | `trajectory.py`, runway-dependent earliest, laps/vectors/stack, route fuel, `ATC-HOLD-STACK`, `hold` move, labels, `/geo` (procedures part), `/api/trajectories` (air), contract v2.2 part 1, radar on trajectories, strips; replays re-recorded; benchmark re-run | invariant tests; perf ≤ 3 ms; prompt ≤ 8k tokens; benchmark row v3-air with attribution; **either the radar draws `TrajectorySet` or the straight-line scope is hidden or relabelled "schematic, not the engine's path"** (otherwise the old radar would break the anti-goal) |
| **M2a Time base** | §6.1 only; separate benchmark row | delay definition documented in README and contract |
| **M2 Surface (`v3-taxi`)** | surface graph, exits, routed unimpeded taxi replaces 6/10, real `ATC-TAXI-ROUTE`, gate geo, surface map (static traffic, no waits), Gantt taxi segment; taxi validation published | validation table; re-recorded replays; benchmark row |
| **M3 Ground traffic (`v3-traffic`)** | fixed-traffic reservations incl. real departures, hold-short pass, crossings, `ATC-TAXI-CONFLICT`, surface map waits/conflicts | conflict-free invariant tests; benchmark row; failure cases visible in UI |
| **M4 Polish** | README/deck story ("BENKY stack ×3, 2 laps, 420 kg"), Claude brief template mentions laps and hold-shorts, MCP tool `get_trajectory`, screenshots | demo walkthrough on all 5 replays offline |

Rough size: M0 2-3 days, M1 3-4, M2a 0.5, M2 3, M3 2-3, M4 1 (one builder plus the frontend work).

## 12. Test strategy

- **Parser:** golden tests on ~30 verbatim CIFP lines committed under `tests/fixtures/cifp/` (public
  domain). Covered: KORD BENKY6 legs, I10C including the HM at CGT, an RF leg, a VM-ended STAR, and the PA
  mag var. Plus a dev-only cross-check against `arinc424` decodes.
- **Geometry:** lap time at 230/200/265 KIAS within ±5 % of the hand calculation (§4.4); racetrack closes;
  corner-post fallback for an airport with no STAR (e.g. ABE); straight-in only for PBI.
- **Trajectory invariants** (property tests over all 50 benchmark waves x FCFS + agreed plans): touchdown
  = slot; time is monotonic; laps are integers; `hold_min` identity; every point is within 150 NM of the
  airport reference point; `src` is set everywhere.
- **Surface:** graph connectivity per quality tier; exit choice is deterministic; route time on a
  hand-built 6-node graph; hold-short scheduling yields no two aircraft at a node within headway and no
  opposite-direction edge overlap (checked by an independent brute-force verifier, not the scheduler
  itself); crossing gap respected.
- **Rules:** one passing and one failing fixture for each new or changed rule, asserting `source` and
  `evidence` keys (same style as the existing rule tests).
- **Contract:** samples regenerate; `types.ts` typechecks (`npm run build`); v2.1 sample JSON still parses
  with the v2.2 types.
- **Replay guard:** a v2 replay streams, has no trajectories, and returns 409 from `/api/trajectories`.
- **Performance and memory:** `evaluate` p95 ≤ 3 ms (ORD); per-airport RSS ≤ 60 MB; a formulas-mode
  negotiation of all 5 replay waves completes in < 2 s each.
- **End-to-end:** all 50 benchmark waves build and negotiate in formulas mode with no exceptions (CI);
  Playwright screenshots of radar and surface map on the ORD replay.

## 13. Risks

| Risk | Mitigation |
|---|---|
| OSM graph defects (taxiways not sharing nodes with runways, gaps) | snap within 15 m during build; quality gate; synth tier; coverage table published |
| CIFP edge cases (RF/AF arcs, VM/FM endings, multi-runway transitions) | handle TF/CF/DF/IF/RF; everything else becomes synthesized vectors; tests on real lines |
| Synthesized holds look authoritative | `src` on every point, dashed drawing, legend, provenance badge |
| Metric shifts read as agent regressions | per-milestone rows; physics vs negotiation attribution; v2 frozen |
| Compute in move enumeration | precompute per wave, memoise per plan, perf test gate |
| Prompt growth breaks elephant-1 | label budget, measured prompt cap, test |
| Overpass rate limits / outages during fetch | cached raw, retries, IATA/bbox fallback; never at runtime |
| Image size / 512 MB RAM on Render | size caps in §5, LRU per airport, RSS test |
| ODbL / CIFP terms | attribution, derived-data README, disclaimer in UI |
| Departure stands simulated | badged `simulated`; departures affect only ground traffic |
| Frontend ownership (CLAUDE.md) | §15 question |

## 14. Calls made (all "our call", delegated by the user)

1. Use pinned CIFP cycle 2611 for all waves. Drop procedures whose runway did not exist on the wave date.
   Label the layer as current procedures flown over 2015 traffic.
2. Write our own fixed-column CIFP extractor. `cifparse` is GPL-3.0 and the repo is MIT; `arinc424`
   (MIT) is used only as a dev-time cross-check.
3. Holds go at the last STAR common-route fix. Use the published HF/HM geometry if coded there, otherwise
   a synthesized AIM standard hold, labelled. CIFP does not code STAR holds (readme, verified).
4. Use a corner-post synthetic arrival where there is no STAR (209 of 320 airports), and a straight-in
   where there is no approach.
5. Delay = whole laps + < 1 lap of vectors. No new quantisation veto.
6. Earliest landing and fuel depend on the runway through the route length. Terminal burn = workbook
   holding burn (assumption).
7. Stack: 1,000 ft levels, 5 levels, bottom-first exit. New rule `ATC-HOLD-STACK`.
8. Taxi: fixed route (unimpeded shortest time) plus deterministic hold-short waits, no rerouting. Fixed
   traffic has priority and is scheduled once per wave.
9. The new rule is `ATC-TAXI-CONFLICT` (owner ATC), not `GND-…`. It vetoes only unresolvable or
   runway-blocking situations. Waits cost time, not legality.
10. `ATC-TAXI-ROUTE` becomes a real check.
11. Background ground traffic: real 2015 departures and arrivals for the top 30 airports (benchmark
    coverage); elsewhere, the challenge-file subset, badged partial.
12. Fix the BTS time base (scheduled = gate time) in its own milestone and benchmark row.
13. Fix the IATA→ICAO heuristic using OpenFlights.
14. Gate ids and seeding stay as in v2; coordinates are attached by ref, then by nearest stand, then
    generically.
15. One new move (`hold`). STAR, exit and taxi route are engine outputs, not moves.
16. Metrics become joint. Utility weights are unchanged.
17. Trajectories are served from the same objects as the metrics. The final and baseline trajectories
    are embedded in the result. Replays from another engine version show no paths.
18. Contract v2.2 is additive, with new `/geo` and `/trajectories` endpoints.
19. Re-record the 5 replays at every metric-changing milestone. Re-run the benchmark per milestone, with
    v2 frozen.
20. Taxi speeds are fixed up front (20/10 kt) and validated against BTS `TAXI_IN` without fitting.
21. Radar and surface map share a panel with an automatic hand-off at the focus flight's touchdown.
22. Holding is rare in the data (6 of 397 recorded flights hold). We never inflate it. Instead, we report
    the lap share and re-choose the demo replays so that at least 3 of 5 show a real lap.

## 15. Open questions for the user

1. **Who builds the display work?** `CLAUDE.md` says `frontend/` belongs to the teammate, except
   `frontend/src/api/types.ts` and `samples/`. Recent commits on this branch changed frontend components
   directly. Is that rule still in force for the radar, surface map, Gantt and strips changes, or may
   the backend builder implement them? This decides who executes the display rows of M1-M3. Everything
   else is decided above.

## 16. Sources

- FAA CIFP download page: https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/download/
  and the cycle 2611 zip https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip (inside: `CIFP Readme 2611.pdf`, `FAA CIFP Disclaimer.pdf`, `IN_CIFP.txt`, `Not_In_CIFP_2611.xlsx`, `FAACIFP18`).
- cifparse (GPL-3.0): https://github.com/misterrodg/cifparse · https://pypi.org/project/cifparse/
- arinc424 (MIT): https://github.com/jack-laverty/arinc424 · https://pypi.org/project/arinc424
- PyARINC424: https://pypi.org/project/pyarinc424/0.3.0/
- FAA AIM Chapter 5 Section 3 (para 5-3-8 Holding): https://www.faa.gov/air_traffic/publications/atpubs/aim_html/chap5_section_3.html
- FAA JO 7110.65, §2-1-4 Operational Priority: https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap2_section_1.html
- FAA JO 7110.65, §3-7 Taxi and Ground Movement: https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap3_section_7.html
- FAA JO 7110.65, §4-5-1 Vertical Separation Minima: https://www.faa.gov/air_traffic/publications/atpubs/atc_html/chap4_section_5.html
- 14 CFR 91.117 Aircraft speed: https://www.ecfr.gov/current/title-14/part-91/section-91.117
- 14 CFR 97.3 (approach categories): https://www.ecfr.gov/current/title-14/part-97/section-97.3
- FAA AC 150/5300-13B Chg 1, Airport Design (exit taxiways §4.8, Fig. 4-18): https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC-150-5300-13B-Airport-Design-Chg1-w-errata.pdf
- Taxi speed assumption (weak source, flagged): Sadono, Putra, Harahap, ITB, simulation of Soekarno-Hatta taxiways: https://avia.ftmd.itb.ac.id/index.php/jav/article/download/13/14
- Safe Interval Path Planning: M. Phillips, M. Likhachev, "SIPP: Safe Interval Path Planning for Dynamic Environments", ICRA 2011.
- OpenStreetMap / Overpass API: https://overpass-api.de/ · aeroway tags: https://wiki.openstreetmap.org/wiki/Key:aeroway · ODbL: https://opendatacommons.org/licenses/odbl/
- BTS On-Time PREZIP (2015 months): https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2015_1.zip
- Challenge workbook: `data/challenge2/challenge2_operational_constraints.xlsx` (holding burn, wake, capacity, turnaround, crew).
