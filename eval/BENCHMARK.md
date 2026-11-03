# Benchmark (50 waves, seed 7, source flights2015)
Generated 2026-11-03T15:31:27+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 326.69 | 1.36 | 36.92 | 1.748 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 329.44 | 83.2 | 34.704 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 98.0 | 1.36 | 2.0 | 0 | 334.256 | 245.76 | 34.56 | 1.966 | 36.0 | 0.0084 | 429.0 | 532.8 | 0.0 |
| liquid/d1-ultra | 50/50 | 96.0 | 1.4 | 4.0 | 0 | 334.036 | 218.08 | 34.56 | 1.558 | 44.0 | 0.002 | 577.0 | 1227.5 | 0.0 |
| baseten/elephant-1 | 50/50 | 96.0 | 1.42 | 4.0 | 0 | 334.386 | 238.4 | 34.24 | 1.8 | 38.0 | 0.0281 | 516.0 | 629.0 | 0.0 |
| formula | 50/50 | 98.0 | 1.36 | 2.0 | 0 | 333.378 | 233.36 | 34.278 | 1.886 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 24 waves: F2015-102056, F2015-105396, F2015-110532, F2015-112396, F2015-124231, F2015-144568, F2015-165543, F2015-205196, F2015-208678, F2015-208692, F2015-209722, F2015-210010, F2015-246479, F2015-251827, F2015-254935, F2015-267904, F2015-282726, F2015-298859, F2015-335957, F2015-343919, F2015-43962, F2015-60842, F2015-63104, F2015-63860.

- typesafe/jev-1.14 vs status quo on 26 feasible-status-quo waves: delay 318.796 min vs 324.665 min
- liquid/d1-ultra vs status quo on 26 feasible-status-quo waves: delay 318.292 min vs 324.665 min
- baseten/elephant-1 vs status quo on 26 feasible-status-quo waves: delay 318.592 min vs 324.665 min
- formula vs status quo on 26 feasible-status-quo waves: delay 318.308 min vs 324.665 min

Optimiser reference (n=50, 50 feasible): mean delay 329.44 min, 95% CI [282.467, 376.413]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [287.546, 380.966]; gap to optimiser 4.816 min; share of optimiser gain over status quo 55.75% (n=22 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [287.316, 380.756]; gap to optimiser 4.596 min; share of optimiser gain over status quo 60.37% (n=22 waves with feasible status quo beaten by the optimiser).
- baseten/elephant-1: delay 95% CI [287.648, 381.124]; gap to optimiser 4.946 min; share of optimiser gain over status quo 57.62% (n=22 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [286.651, 380.105]; gap to optimiser 3.938 min; share of optimiser gain over status quo 62.8% (n=22 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** typesafe/jev-1.14 (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- typesafe/jev-1.14 is the best model arm: it meets all three thresholds (error, p95, consensus) and has the best outcome among the qualifying model arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 532.8 ms
- typesafe/jev-1.14 outcomes partly from solver fallback on 1/50 waves
- liquid/d1-ultra p95 all decisions incl. fallbacks: 1227.5 ms
- liquid/d1-ultra missed p95 < 1000 ms: 1227.5 ms
- liquid/d1-ultra outcomes partly from solver fallback on 2/50 waves
- baseten/elephant-1 p95 all decisions incl. fallbacks: 629.0 ms
- baseten/elephant-1 outcomes partly from solver fallback on 2/50 waves
- Mean delay: typesafe/jev-1.14 334.256 min vs formula 333.378 min vs status quo 326.69 min.

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"typesafe/jev-1.14-20261014": 171}

## Ablation: safety engine off

46.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-317184` (typesafe/jev-1.14): regression vs status quo: delay 385.3 > 380.6 min
- `F2015-115532` (typesafe/jev-1.14): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-302254` (typesafe/jev-1.14): regression vs status quo: delay 311.7 > 307.7 min
- `F2015-209820` (typesafe/jev-1.14): regression vs status quo: delay 256.2 > 253.8 min
- `F2015-271058` (typesafe/jev-1.14): regression vs status quo: delay 634.7 > 628.1 min
- `F2015-272020` (typesafe/jev-1.14): regression vs status quo: delay 444.0 > 443.8 min
- `F2015-74915` (typesafe/jev-1.14): regression vs status quo: delay 334.7 > 334.5 min
- `F2015-102056` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-317184` (liquid/d1-ultra): regression vs status quo: delay 385.3 > 380.6 min
- `F2015-105396` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-115532` (liquid/d1-ultra): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-209820` (liquid/d1-ultra): regression vs status quo: delay 256.2 > 253.8 min
- `F2015-271058` (liquid/d1-ultra): regression vs status quo: delay 631.7 > 628.1 min
- `F2015-272020` (liquid/d1-ultra): regression vs status quo: delay 444.0 > 443.8 min
- `F2015-102056` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-317184` (baseten/elephant-1): regression vs status quo: delay 385.3 > 380.6 min
- `F2015-115532` (baseten/elephant-1): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-209820` (baseten/elephant-1): regression vs status quo: delay 256.2 > 253.8 min
- `F2015-271058` (baseten/elephant-1): regression vs status quo: delay 631.7 > 628.1 min
- `F2015-272020` (baseten/elephant-1): regression vs status quo: delay 444.0 > 443.8 min
- `F2015-124231` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-102056` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-317184` (formula): regression vs status quo: delay 385.3 > 380.6 min
- `F2015-115532` (formula): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-209820` (formula): regression vs status quo: delay 262.8 > 253.8 min
- `F2015-271058` (formula): regression vs status quo: delay 631.7 > 628.1 min
- `F2015-272020` (formula): regression vs status quo: delay 444.7 > 443.8 min
- `F2015-102056` (formula): no consensus: deterministic solver fallback

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- typesafe/jev-1.14: 6,759 chars (within budget)
- liquid/d1-ultra: 6,759 chars (within budget)
- baseten/elephant-1: 6,759 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- typesafe/jev-1.14: PLT-FUEL-RESERVE 8
- liquid/d1-ultra: PLT-FUEL-RESERVE 6
- baseten/elephant-1: PLT-FUEL-RESERVE 5
- formula: PLT-FUEL-RESERVE 10

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
| v3-traffic | baseline | 326.69 | 1.36 | 0.0 | 1.083 | 36.92 | 1.748 | n/a | n/a | 26 |
| v3-traffic | typesafe/jev-1.14 | 334.256 | 245.76 | 4.08 | 0.414 | 34.56 | 1.966 | 98.0 | 0 | 50 |
| v3-traffic | liquid/d1-ultra | 334.036 | 218.08 | 4.17 | 0.44 | 34.56 | 1.558 | 96.0 | 0 | 50 |
| v3-traffic | baseten/elephant-1 | 334.386 | 238.4 | 4.17 | 0.414 | 34.24 | 1.8 | 96.0 | 0 | 50 |
| v3-traffic | formula | 333.378 | 233.36 | 2.04 | 0.432 | 34.278 | 1.886 | 98.0 | 0 | 50 |

## Attribution

Engine v3-taxi to v3-traffic. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Physics (status quo under the new engine minus under the old): delay_min +1.078, fuel_kg +0, lap_share_pct +0, taxi_wait_min +1.083, pax_protected_pct -0.396, overtime_min +0.052. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- typesafe/jev-1.14: delay_min +7.566, fuel_kg +244.4, lap_share_pct +4.08, taxi_wait_min -0.669, pax_protected_pct -2.36, overtime_min +0.218
- liquid/d1-ultra: delay_min +7.346, fuel_kg +216.72, lap_share_pct +4.17, taxi_wait_min -0.643, pax_protected_pct -2.36, overtime_min -0.19
- baseten/elephant-1: delay_min +7.696, fuel_kg +237.04, lap_share_pct +4.17, taxi_wait_min -0.669, pax_protected_pct -2.68, overtime_min +0.052
- formula: delay_min +6.688, fuel_kg +232, lap_share_pct +2.04, taxi_wait_min -0.651, pax_protected_pct -2.642, overtime_min +0.138

Headline: under engine v3-traffic, negotiation (formula) adds 6.688 min of mean delay against the status quo.

Regressions (104):

- `F2015-144568` (typesafe/jev-1.14) delay_min 505.1 to 507.8; negotiation
- `F2015-144568` (typesafe/jev-1.14) fuel_kg 144.0 to 380.0; negotiation
- `F2015-161169` (typesafe/jev-1.14) delay_min 277.9 to 278.0; physics
- `F2015-163396` (typesafe/jev-1.14) delay_min 180.1 to 185.0; negotiation
- `F2015-165543` (typesafe/jev-1.14) delay_min 204.0 to 212.4; negotiation
- `F2015-165543` (typesafe/jev-1.14) fuel_kg 288.0 to 348.0; negotiation
- `F2015-208678` (typesafe/jev-1.14) delay_min 692.4 to 698.2; negotiation
- `F2015-208678` (typesafe/jev-1.14) fuel_kg 96.0 to 156.0; negotiation
- `F2015-209722` (typesafe/jev-1.14) delay_min 126.3 to 148.6; negotiation
- `F2015-209722` (typesafe/jev-1.14) fuel_kg 0.0 to 1088.0; negotiation
- `F2015-209820` (typesafe/jev-1.14) delay_min 252.5 to 256.2; negotiation
- `F2015-209820` (typesafe/jev-1.14) fuel_kg 36.0 to 100.0; negotiation
- `F2015-210010` (typesafe/jev-1.14) delay_min 430.5 to 431.2; physics
- `F2015-251827` (typesafe/jev-1.14) delay_min 244.6 to 244.9; physics
- `F2015-267904` (typesafe/jev-1.14) delay_min 339.9 to 346.3; physics
- `F2015-267904` (typesafe/jev-1.14) fuel_kg 0.0 to 148.0; negotiation
- `F2015-271058` (typesafe/jev-1.14) delay_min 631.7 to 634.7; negotiation
- `F2015-271058` (typesafe/jev-1.14) fuel_kg 428.0 to 452.0; negotiation
- `F2015-272020` (typesafe/jev-1.14) delay_min 441.6 to 444.0; physics
- `F2015-274783` (typesafe/jev-1.14) delay_min 221.4 to 222.9; physics
- ... and 84 more in benchmark.json

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

## v3-traffic: fixed 2015 ground traffic, hold-short waits, ATC-TAXI-CONFLICT

- The v3-taxi → v3-traffic change is a physics change on the ground: the real 2015 movements (queue arrivals and departures, pushback = WHEELS_OFF − TAXI_OUT) are scheduled once per wave as fixed traffic, and each wave flight taxis its fixed route in runway-exit order, waiting at a node or a runway hold until the node headway (30 s), the runway crossing gap and the runway-vacate rule are met. The waits are taxi_wait_min and add to in-block. ATC-TAXI-CONFLICT fails a plan when a wave flight is still on its runway at another landing or takeoff on that strip, or when its total wait passes the 30-min horizon.
- Status quo (baseline) moved by the traffic model alone, nothing renegotiated: delay 325.6 → 326.7 min, mean taxi wait 0 → 1.08 min per wave, pax protected 37.3 → 36.9 %, overtime 1.70 → 1.75 min, fuel unchanged at 1.36 kg. Feasible status-quo waves fell 32 → 26: the 6 waves F2015-209722, -251827, -43962, -63860, -267904 and -110532 now fail only ATC-TAXI-CONFLICT, each because a wave arrival is still vacating its runway when a fixed 2015 departure takes off on that strip, by 0.01 to 0.98 min.
- Mean taxi wait per wave (sum over the plan's flights), agreed plans: jev-1.14 0.41, d1-ultra 0.44, elephant-1 0.41, formula 0.43 min, against 1.08 min in the status quo. Negotiation lowers the taxi waits (negotiation_delta about −0.65 min in every arm), a likely cause (not measured per wave) is that slot and runway moves take arrivals off runways or routes shared with the departure stream.
- Delay min (v3-taxi → v3-traffic): formula 331.7 → 333.4, jev-1.14 332.4 → 334.3, d1-ultra 332.3 → 334.0, elephant-1 332.8 → 334.4. About 1.1 min of each rise is the physics (status-quo taxi waits); the rest is the plans that clear the 6 newly infeasible waves (slot or runway moves) and F2015-335957 below.
- Fuel kg: formula 197.6 → 233.4, jev-1.14 190.6 → 245.8, d1-ultra 186.4 → 218.1, elephant-1 186.0 → 238.4. Two waves carry most of it. F2015-209722 (status quo fails runway-vacate on 10R/28L against fixed takeoffs, DL2265 and WN559): formula, jev-1.14 and elephant-1 move flights to a longer-route runway, +1,088 kg route extra and +22.3 min (d1-ultra +272 kg). F2015-335957: all three model arms now agree a plan with a 5-lap hold (28.6 min, +1,132 kg, +25 min delay; the status quo fails ATC-TAXI-CONFLICT for UA461 on 12/30 and GND-GATE-FREE); formula keeps 0 laps there.
- Lap share %: model arms about 2.0 → 4.1 (the F2015-335957 hold added to the existing 3-lap hold on F2015-208692), formula unchanged at 2.04. Pax protected %: formula 34.7 → 34.3, jev-1.14 34.6 → 34.6, d1-ultra 34.3 → 34.6, elephant-1 34.3 → 34.2. Overtime min: formula 1.09 → 1.89, jev-1.14 1.29 → 1.97, d1-ultra 1.13 → 1.56, elephant-1 1.07 → 1.80 (a likely cause, not measured per wave: later in-blocks from the taxi waits).
- Engine veto counts: ATC-HOLD-STACK 0 and ATC-TAXI-CONFLICT 0 in every arm; only PLT-FUEL-RESERVE vetoes remain (jev-1.14 8, d1-ultra 6, elephant-1 5, formula 10; v3-taxi 9 / 7 / 6 / 11). The counts are failed checks in proposals the engine rejected; no agent proposal was rejected on a stack or taxi conflict in this run. ATC-TAXI-CONFLICT still decides legality: it is why 6 more status-quo waves are infeasible, and every agreed plan passes it (rule violations in agreed plans 0 in every arm).
- Consensus: formula and jev-1.14 98 %, d1-ultra 96 % (was 98), elephant-1 96 %. No-consensus fallbacks: F2015-102056 in every arm, plus F2015-105396 (d1-ultra) and F2015-124231 (elephant-1). Failures 31 → 28 (22 regressions vs the status quo, 6 no-consensus fallbacks); the regressions-vs-status-quo count fell because 6 status quo waves are no longer feasible, so they no longer count as a baseline to beat.
- Regressions vs v3-taxi per wave: 104 (77 negotiation, 27 physics; 71 delay, 33 fuel). The physics ones are delay rises of 0.1 to 6.4 min from taxi waits (largest F2015-267904, +6.4 min in every arm). Among the negotiation ones, F2015-63860 rises +12.8 min in every arm: the agreed plan leaves the focus flight a 12.8 min taxi wait.
- Winner changed from formula to jev-1.14, the best model arm that meets all three thresholds (d1-ultra missed p95 < 1 s at 1,227 ms). Formula has the lowest mean delay (333.4 vs 334.3 min); the arms are within 1.1 min of each other (n = 50): not a meaningful ranking change. Optimiser reference 327.9 → 329.4 min, fuel 75.7 → 83.2 kg. Outage test 20 / 20 resolved. Ablation (engine off) unsafe 36 → 46 % of waves: a likely cause (not measured per wave) is that the new conflict rule fails more of the unchecked plans.
- Taxi-in validation vs BTS 2015 is unchanged (n 42, MAE 3.37 min, bias −2.78 min): it compares the routed unimpeded taxi + ROT, not the waits. Coverage unchanged: procedures star+iap 49, none 1; surface real 44, generic 6; ground traffic real 50.

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 50 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
- Fixed crossers are not re-timed per plan: the 2015 ground traffic is scheduled once per wave, before any plan exists. A plan landing inside a fixed crossing's gap window is landing priority (informational fixed_holds, the crosser would be held), and a fixed takeoff less than 1 min before a plan landing is informational too (fixed_departure_holds); neither the fixed track nor its knock-on effects are re-timed. The 6 newly infeasible status-quo waves fail by 0.01 to 0.98 min against fixed takeoffs that a controller would simply hold.
- Departure holding bay (assumption, controller ruling T31): 3 aircraft per holding point, resequenced by takeoff time, off the taxiway; a fourth waits upstream on the taxiway. Real bay capacity varies per airport.
- Head-on waits on fixed routes: wave flights never reroute, so an arrival whose route meets a departure stream head-on waits for a gap (DFW replay F2015-75: AA260, AA2441 and MQ2833 wait 7.6, 13.6 and 21.0 min at node 107 in the status quo, above their own BTS TAXI_IN). The taxi wait is therefore an upper bound where a controller would issue another route.
- Routed taxi + waits still has no ramp or gate waits, and the taxi-in validation (bias −2.78 min over 42 focus flights) measures the unimpeded part only.
- Gate-relief laps (airborne holds that exist only to clear GND-GATE-FREE, 9 across arms in v3-air; see eval/archive/engine-v3-air/BENCHMARK.md) were not re-counted on the v3-traffic plans; the F2015-208692 and F2015-335957 laps were not classified.
