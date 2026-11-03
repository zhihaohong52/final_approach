# Benchmark (50 waves, seed 7, source flights2015)
Generated 2026-11-03T13:32:22+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 325.612 | 1.36 | 37.316 | 1.696 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 327.914 | 75.68 | 34.96 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 98.0 | 1.38 | 2.0 | 0 | 332.354 | 190.56 | 34.56 | 1.29 | 48.0 | 0.0098 | 432.0 | 544.2 | 0.0 |
| liquid/d1-ultra | 50/50 | 98.0 | 1.36 | 2.0 | 0 | 332.286 | 186.4 | 34.324 | 1.126 | 60.0 | 0.002 | 613.0 | 1587.7 | 0.0 |
| baseten/elephant-1 | 50/50 | 96.0 | 1.44 | 4.0 | 0 | 332.75 | 186.0 | 34.324 | 1.07 | 48.0 | 0.0275 | 531.0 | 620.2 | 0.0 |
| formula | 50/50 | 98.0 | 1.38 | 2.0 | 0 | 331.66 | 197.6 | 34.72 | 1.088 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 18 waves: F2015-102056, F2015-105396, F2015-112396, F2015-124231, F2015-144568, F2015-165543, F2015-205196, F2015-208678, F2015-208692, F2015-210010, F2015-246479, F2015-254935, F2015-282726, F2015-298859, F2015-335957, F2015-343919, F2015-60842, F2015-63104.

- typesafe/jev-1.14 vs status quo on 32 feasible-status-quo waves: delay 316.916 min vs 322.322 min
- liquid/d1-ultra vs status quo on 32 feasible-status-quo waves: delay 316.519 min vs 322.322 min
- baseten/elephant-1 vs status quo on 32 feasible-status-quo waves: delay 316.803 min vs 322.322 min
- formula vs status quo on 32 feasible-status-quo waves: delay 316.172 min vs 322.322 min

Optimiser reference (n=50, 50 feasible): mean delay 327.914 min, 95% CI [280.968, 374.86]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [285.537, 379.171]; gap to optimiser 4.44 min; share of optimiser gain over status quo 55.02% (n=26 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [285.321, 379.251]; gap to optimiser 4.372 min; share of optimiser gain over status quo 58.92% (n=26 waves with feasible status quo beaten by the optimiser).
- baseten/elephant-1: delay 95% CI [285.886, 379.614]; gap to optimiser 4.836 min; share of optimiser gain over status quo 56.13% (n=26 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [284.895, 378.425]; gap to optimiser 3.746 min; share of optimiser gain over status quo 62.33% (n=26 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** formula (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- Formula selected: the best model arm, typesafe/jev-1.14, is worse than formula on delay (332.354 > 331.66 min) and not better on pax protected.
- typesafe/jev-1.14 is the best model arm but not the overall winner: it meets all three thresholds (error, p95, consensus) and has the best outcome among the qualifying model arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 544.2 ms
- typesafe/jev-1.14 outcomes partly from solver fallback on 1/50 waves
- liquid/d1-ultra p95 all decisions incl. fallbacks: 1587.7 ms
- liquid/d1-ultra missed p95 < 1000 ms: 1587.7 ms
- liquid/d1-ultra outcomes partly from solver fallback on 1/50 waves
- baseten/elephant-1 p95 all decisions incl. fallbacks: 620.2 ms
- baseten/elephant-1 outcomes partly from solver fallback on 2/50 waves
- Mean delay: typesafe/jev-1.14 332.354 min vs formula 331.66 min vs status quo 325.612 min.
- Outage test ran with the best eligible model arm typesafe/jev-1.14 (overall winner is formula).

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"typesafe/jev-1.14-20261014": 172}

## Ablation: safety engine off

36.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-317184` (typesafe/jev-1.14): regression vs status quo: delay 380.3 > 379.7 min
- `F2015-115532` (typesafe/jev-1.14): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-302254` (typesafe/jev-1.14): regression vs status quo: delay 311.7 > 307.7 min
- `F2015-161169` (typesafe/jev-1.14): regression vs status quo: delay 277.9 > 276.0 min
- `F2015-251827` (typesafe/jev-1.14): regression vs status quo: delay 244.6 > 241.9 min
- `F2015-271058` (typesafe/jev-1.14): regression vs status quo: delay 631.7 > 628.1 min
- `F2015-272020` (typesafe/jev-1.14): regression vs status quo: delay 441.6 > 437.4 min
- `F2015-74915` (typesafe/jev-1.14): regression vs status quo: delay 334.7 > 334.5 min
- `F2015-102056` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-317184` (liquid/d1-ultra): regression vs status quo: delay 380.3 > 379.7 min
- `F2015-115532` (liquid/d1-ultra): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-161169` (liquid/d1-ultra): regression vs status quo: delay 277.9 > 276.0 min
- `F2015-251827` (liquid/d1-ultra): regression vs status quo: delay 244.6 > 241.9 min
- `F2015-271058` (liquid/d1-ultra): regression vs status quo: delay 631.7 > 628.1 min
- `F2015-272020` (liquid/d1-ultra): regression vs status quo: delay 441.6 > 437.4 min
- `F2015-102056` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-317184` (baseten/elephant-1): regression vs status quo: delay 380.3 > 379.7 min
- `F2015-115532` (baseten/elephant-1): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-161169` (baseten/elephant-1): regression vs status quo: delay 277.9 > 276.0 min
- `F2015-251827` (baseten/elephant-1): regression vs status quo: delay 244.6 > 241.9 min
- `F2015-271058` (baseten/elephant-1): regression vs status quo: delay 631.7 > 628.1 min
- `F2015-272020` (baseten/elephant-1): regression vs status quo: delay 441.6 > 437.4 min
- `F2015-124231` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-102056` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-317184` (formula): regression vs status quo: delay 380.3 > 379.7 min
- `F2015-115532` (formula): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-161169` (formula): regression vs status quo: delay 277.9 > 276.0 min
- `F2015-251827` (formula): regression vs status quo: delay 244.6 > 241.9 min
- `F2015-271058` (formula): regression vs status quo: delay 631.7 > 628.1 min
- `F2015-272020` (formula): regression vs status quo: delay 441.6 > 437.4 min
- `F2015-102056` (formula): no consensus: deterministic solver fallback

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- typesafe/jev-1.14: 6,710 chars (within budget)
- liquid/d1-ultra: 6,709 chars (within budget)
- baseten/elephant-1: 6,709 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- typesafe/jev-1.14: PLT-FUEL-RESERVE 9
- liquid/d1-ultra: PLT-FUEL-RESERVE 7
- baseten/elephant-1: PLT-FUEL-RESERVE 6
- formula: PLT-FUEL-RESERVE 11

## Engine history

| engine | arm | delay min | fuel kg | lap share % | taxi wait min | pax prot % | overtime min | consensus % | violations | n feasible |
|---|---|---|---|---|---|---|---|---|---|---|
| v2 | baseline | 343.158 | 0.72 | n/a | n/a | 26.538 | 1.76 | n/a | n/a | 34 |
| v2 | typesafe/jev-1.14 | 351.192 | 107.84 | n/a | n/a | 25.798 | 1.2 | 98.0 | 0 | 50 |
| v2 | liquid/d1-ultra | 351.438 | 111.28 | n/a | n/a | 25.502 | 1.2 | 96.0 | 0 | 50 |
| v2 | baseten/elephant-1 | 351.438 | 111.28 | n/a | n/a | 25.798 | 1.2 | 98.0 | 0 | 50 |
| v2 | formula | 351.198 | 108.08 | n/a | n/a | 25.502 | 1.2 | 98.0 | 0 | 50 |
| v3-air | baseline | 343.158 | 0.72 | 0.0 | 0.0 | 26.538 | 1.76 | n/a | n/a | 34 |
| v3-air | typesafe/jev-1.14 | 352.328 | 230.48 | 4.08 | 0.0 | 25.726 | 1.966 | 98.0 | 0 | 50 |
| v3-air | liquid/d1-ultra | 352.256 | 224.16 | 4.08 | 0.0 | 25.726 | 1.84 | 98.0 | 0 | 50 |
| v3-air | baseten/elephant-1 | 351.744 | 211.84 | 4.08 | 0.0 | 25.726 | 1.746 | 98.0 | 0 | 50 |
| v3-air | formula | 352.302 | 235.76 | 6.12 | 0.0 | 26.0 | 1.906 | 98.0 | 0 | 50 |
| v3-time | baseline | 335.474 | 1.36 | 0.0 | 0.0 | 34.098 | 1.856 | n/a | n/a | 33 |
| v3-time | typesafe/jev-1.14 | 344.85 | 218.24 | 4.08 | 0.0 | 33.564 | 1.88 | 98.0 | 0 | 50 |
| v3-time | liquid/d1-ultra | 344.902 | 221.68 | 4.17 | 0.0 | 33.518 | 1.766 | 96.0 | 0 | 50 |
| v3-time | baseten/elephant-1 | 344.278 | 199.76 | 4.08 | 0.0 | 33.564 | 1.554 | 98.0 | 0 | 50 |
| v3-time | formula | 344.59 | 221.2 | 4.08 | 0.0 | 34.48 | 1.728 | 98.0 | 0 | 50 |
| v3-taxi | baseline | 325.612 | 1.36 | 0.0 | 0.0 | 37.316 | 1.696 | n/a | n/a | 32 |
| v3-taxi | typesafe/jev-1.14 | 332.354 | 190.56 | 2.04 | 0.0 | 34.56 | 1.29 | 98.0 | 0 | 50 |
| v3-taxi | liquid/d1-ultra | 332.286 | 186.4 | 2.04 | 0.0 | 34.324 | 1.126 | 98.0 | 0 | 50 |
| v3-taxi | baseten/elephant-1 | 332.75 | 186.0 | 2.08 | 0.0 | 34.324 | 1.07 | 96.0 | 0 | 50 |
| v3-taxi | formula | 331.66 | 197.6 | 2.04 | 0.0 | 34.72 | 1.088 | 98.0 | 0 | 50 |

## Attribution

Engine v3-time to v3-taxi. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Physics (status quo under the new engine minus under the old): delay_min -9.862, fuel_kg +0, lap_share_pct +0, taxi_wait_min +0, pax_protected_pct +3.218, overtime_min -0.16. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- typesafe/jev-1.14: delay_min +6.742, fuel_kg +189.2, lap_share_pct +2.04, taxi_wait_min +0, pax_protected_pct -2.756, overtime_min -0.406
- liquid/d1-ultra: delay_min +6.674, fuel_kg +185.04, lap_share_pct +2.04, taxi_wait_min +0, pax_protected_pct -2.992, overtime_min -0.57
- baseten/elephant-1: delay_min +7.138, fuel_kg +184.64, lap_share_pct +2.08, taxi_wait_min +0, pax_protected_pct -2.992, overtime_min -0.626
- formula: delay_min +6.048, fuel_kg +196.24, lap_share_pct +2.04, taxi_wait_min +0, pax_protected_pct -2.596, overtime_min -0.608

Headline: under engine v3-taxi, negotiation (formula) adds 6.048 min of mean delay against the status quo.

Regressions (92):

- `F2015-105396` (typesafe/jev-1.14) delay_min 142.1 to 143.5; negotiation
- `F2015-105396` (typesafe/jev-1.14) fuel_kg 0.0 to 24.0; negotiation
- `F2015-112396` (typesafe/jev-1.14) fuel_kg 36.0 to 260.0; negotiation
- `F2015-112468` (typesafe/jev-1.14) fuel_kg 0.0 to 236.0; negotiation
- `F2015-131437` (typesafe/jev-1.14) fuel_kg 88.0 to 104.0; negotiation
- `F2015-144520` (typesafe/jev-1.14) fuel_kg 48.0 to 92.0; negotiation
- `F2015-156608` (typesafe/jev-1.14) fuel_kg 152.0 to 220.0; negotiation
- `F2015-205196` (typesafe/jev-1.14) fuel_kg 212.0 to 464.0; negotiation
- `F2015-208692` (typesafe/jev-1.14) fuel_kg 452.0 to 1332.0; negotiation
- `F2015-209820` (typesafe/jev-1.14) fuel_kg 8.0 to 36.0; negotiation
- `F2015-268625` (typesafe/jev-1.14) delay_min 323.4 to 327.3; physics
- `F2015-268625` (typesafe/jev-1.14) fuel_kg 0.0 to 196.0; negotiation
- `F2015-271058` (typesafe/jev-1.14) delay_min 617.3 to 631.7; negotiation
- `F2015-271058` (typesafe/jev-1.14) fuel_kg 0.0 to 428.0; negotiation
- `F2015-274783` (typesafe/jev-1.14) delay_min 219.4 to 221.4; physics
- `F2015-274783` (typesafe/jev-1.14) fuel_kg 0.0 to 120.0; negotiation
- `F2015-282726` (typesafe/jev-1.14) fuel_kg 16.0 to 100.0; negotiation
- `F2015-291336` (typesafe/jev-1.14) fuel_kg 92.0 to 228.0; negotiation
- `F2015-343919` (typesafe/jev-1.14) fuel_kg 4.0 to 8.0; negotiation
- `F2015-60842` (typesafe/jev-1.14) fuel_kg 0.0 to 16.0; negotiation
- ... and 72 more in benchmark.json

## Taxi-in validation vs BTS 2015

Focus flight of each benchmark wave on the real surface tier (OSM route) with a real BTS TAXI_IN, under the FCFS plan. Routed = ROT + unimpeded routed taxi (wheels-on to in-block, the span TAXI_IN measures). No fitting: speeds are fixed up front. BTS TAXI_IN includes gate waits and ramp delays, so routed < real (bias < 0) is expected; the bias is stated, not corrected.

n = 42, MAE 3.37 min, bias (routed - real) -2.78 min.

| airport | n | routed median min | BTS 2015 median min |
|---|---|---|---|
| ATL | 1 | 4.25 | 8.0 |
| BNA | 2 | 4.4 | 6.0 |
| BOS | 2 | 7.44 | 6.0 |
| BWI | 2 | 4.05 | 6.0 |
| DCA | 2 | 2.44 | 5.0 |
| DEN | 2 | 6.9 | 7.0 |
| DFW | 1 | 9.57 | 10.0 |
| DTW | 1 | 8.37 | 8.0 |
| EWR | 2 | 5.98 | 9.0 |
| FLL | 2 | 3.37 | 5.0 |
| IAD | 2 | 6.1 | 6.0 |
| JFK | 1 | 6.23 | 8.0 |
| LAX | 2 | 3.87 | 9.0 |
| LGA | 2 | 3.19 | 7.0 |
| MCO | 1 | 5.0 | 7.0 |
| MDW | 1 | 4.85 | 6.0 |
| MIA | 2 | 3.61 | 7.0 |
| MSP | 2 | 5.57 | 5.0 |
| ORD | 1 | 9.23 | 12.0 |
| PHL | 1 | 4.46 | 6.0 |
| PHX | 2 | 4.22 | 6.0 |
| SAN | 1 | 4.78 | 4.0 |
| SEA | 2 | 3.83 | 7.0 |
| SFO | 2 | 5.14 | 5.0 |
| SLC | 1 | 7.44 | 5.0 |
| TPA | 2 | 4.51 | 4.0 |

Largest gaps (cause is a heuristic):

- `F2015-334031` WN2338: routed 3.8 min, real 24.0 min; BTS includes gate wait
- `F2015-317184` AA1465: routed 2.7 min, real 13.0 min; BTS includes gate wait
- `F2015-115532` UA1623: routed 4.0 min, real 12.0 min; -
- `F2015-251827` WN998: routed 3.4 min, real 11.0 min; -
- `F2015-106428` DL1212: routed 4.4 min, real 12.0 min; -

## Coverage

Tier counts over the 50 benchmark waves (waves that failed to build count as none):

- procedures: star+iap 49, none 1
- surface: real 44, generic 6
- ground_traffic: real 50

## v3-taxi: routed unimpeded taxi-in

- The v3-time → v3-taxi change is a physics change in the taxi-in model: in-block = landing slot + runway occupancy (roll to the first forward exit past the required roll) + unimpeded taxi routed on the OpenStreetMap taxiway graph to the gate's stand (20 kt, 10 kt on taxilanes and turns > 30°, + 30 s to park). It replaces the fixed 6 / 10 min near/far heuristic. No ramp, gate or queue waits are modelled yet (taxi_wait_min stays 0 until ATC-TAXI-CONFLICT).
- Status quo (baseline) moved by the taxi model alone, nothing renegotiated: delay 335.5 → 325.6 min, pax protected 34.1 → 37.3 %, overtime 1.86 → 1.70 min, fuel unchanged at 1.36 kg, feasible waves 33 → 32. Routed taxi-in is shorter than the 6 / 10 min heuristic on most waves, so flights reach the gate earlier. This is the physics_delta in the Attribution section.
- Delay min (v3-time → v3-taxi): formula 344.6 → 331.7, jev-1.14 344.9 → 332.4, d1-ultra 344.9 → 332.3, elephant-1 344.3 → 332.8.
- Fuel kg: formula 221.2 → 197.6, jev-1.14 218.2 → 190.6, d1-ultra 221.7 → 186.4, elephant-1 199.8 → 186.0. Lap share halved in every arm (about 4.1 → 2.0 %); a likely cause (not measured per wave) is that earlier in-block times leave fewer gate conflicts, so fewer slot moves push a flight into a hold.
- Pax protected %: formula 34.5 → 34.7, jev-1.14 33.6 → 34.6, d1-ultra 33.5 → 34.3, elephant-1 33.6 → 34.3. Overtime min: formula 1.7 → 1.1, jev-1.14 1.9 → 1.3, d1-ultra 1.8 → 1.1, elephant-1 1.6 → 1.1.
- Consensus: formula, jev-1.14 and d1-ultra 98 %, elephant-1 96 % (2 no-consensus fallbacks). Failures: 49 in v3-time → 31 in v3-taxi (26 regressions vs the status quo, 5 no-consensus fallbacks). Rule violations in agreed plans are 0 in every arm.
- Winner changed from elephant-1 to formula. The arms are within 1.1 min of mean delay of each other (n = 50): this is not a meaningful ranking change.
- Taxi-in validation vs BTS 2015 (no fitting): 42 focus flights on the real surface tier with a real TAXI_IN; MAE 3.37 min, bias (routed − real) −2.78 min. The negative bias is expected because BTS TAXI_IN includes gate waits and ramp delays, which the unimpeded routed taxi does not model. It is stated, not corrected.
- Coverage over the 50 waves: procedures star+iap 49, none 1; surface real 44, generic 6; ground traffic real (BTS 2015 movements) 50.

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 50 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
- Routed taxi is unimpeded: it sits below BTS TAXI_IN (bias −2.78 min over 42 focus flights) and the gap is not corrected; per-airport medians are in the taxi-in validation table.
- The taxi-in validation uses one focus flight per wave (n = 42, 1-2 per airport), so per-airport medians are indicative only; the cause column is a heuristic.
- Gate-relief laps (airborne holds that exist only to clear GND-GATE-FREE, 9 across arms in v3-air; see eval/archive/engine-v3-air/BENCHMARK.md) were not re-counted on the v3-taxi plans.
