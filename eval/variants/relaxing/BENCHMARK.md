# Benchmark (50 waves, seed 7, source flights2015)
Generated 2026-11-04T00:26:38+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 326.69 | 1.36 | 36.92 | 1.748 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 329.44 | 83.2 | 34.704 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 100.0 | 1.46 | 0.0 | 0 | 334.152 | 223.68 | 34.56 | 1.988 | 38.0 | 0.0087 | 421.5 | 520.5 | 0.0 |
| liquid/d1-ultra | 50/50 | 100.0 | 1.46 | 0.0 | 0 | 334.106 | 218.8 | 34.56 | 2.002 | 54.0 | 0.0022 | 556.0 | 1305.9 | 0.0 |
| baseten/elephant-1 | 50/50 | 98.0 | 1.48 | 2.0 | 0 | 334.562 | 217.76 | 34.24 | 1.876 | 38.0 | 0.0168 | 489.0 | 680.1 | 0.0 |
| formula | 50/50 | 100.0 | 1.5 | 0.0 | 0 | 333.4 | 210.16 | 34.278 | 1.966 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 24 waves: F2015-102056, F2015-105396, F2015-110532, F2015-112396, F2015-124231, F2015-144568, F2015-165543, F2015-205196, F2015-208678, F2015-208692, F2015-209722, F2015-210010, F2015-246479, F2015-251827, F2015-254935, F2015-267904, F2015-282726, F2015-298859, F2015-335957, F2015-343919, F2015-43962, F2015-60842, F2015-63104, F2015-63860.

- typesafe/jev-1.14 vs status quo on 26 feasible-status-quo waves: delay 318.362 min vs 324.665 min
- liquid/d1-ultra vs status quo on 26 feasible-status-quo waves: delay 317.973 min vs 324.665 min
- baseten/elephant-1 vs status quo on 26 feasible-status-quo waves: delay 318.196 min vs 324.665 min
- formula vs status quo on 26 feasible-status-quo waves: delay 317.988 min vs 324.665 min

Optimiser reference (n=50, 50 feasible): mean delay 329.44 min, 95% CI [282.467, 376.413]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [287.524, 380.78]; gap to optimiser 4.712 min; share of optimiser gain over status quo 59.73% (n=22 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [287.466, 380.746]; gap to optimiser 4.666 min; share of optimiser gain over status quo 63.29% (n=22 waves with feasible status quo beaten by the optimiser).
- baseten/elephant-1: delay 95% CI [287.835, 381.289]; gap to optimiser 5.122 min; share of optimiser gain over status quo 61.25% (n=22 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [286.759, 380.041]; gap to optimiser 3.96 min; share of optimiser gain over status quo 65.73% (n=22 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** typesafe/jev-1.14 (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- typesafe/jev-1.14 is the best model arm: it meets all three thresholds (error, p95, consensus) and has the best outcome among the qualifying model arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 520.5 ms
- liquid/d1-ultra p95 all decisions incl. fallbacks: 1305.9 ms
- liquid/d1-ultra missed p95 < 1000 ms: 1305.9 ms
- baseten/elephant-1 p95 all decisions incl. fallbacks: 680.1 ms
- baseten/elephant-1 outcomes partly from solver fallback on 1/50 waves
- Mean delay: typesafe/jev-1.14 334.152 min vs formula 333.4 min vs status quo 326.69 min.

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"typesafe/jev-1.14-20261014": 222}

## Ablation: safety engine off

46.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-144520` (typesafe/jev-1.14): regression vs status quo: fuel 92.0 > 0.0 kg
- `F2015-156608` (typesafe/jev-1.14): regression vs status quo: fuel 220.0 > 0.0 kg
- `F2015-302254` (typesafe/jev-1.14): regression vs status quo: delay 311.7 > 307.7 min
- `F2015-302254` (typesafe/jev-1.14): regression vs status quo: fuel 52.0 > 0.0 kg
- `F2015-115532` (typesafe/jev-1.14): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-169005` (typesafe/jev-1.14): regression vs status quo: fuel 32.0 > 0.0 kg
- `F2015-161169` (typesafe/jev-1.14): regression vs status quo: fuel 12.0 > 0.0 kg
- `F2015-209820` (typesafe/jev-1.14): regression vs status quo: delay 256.2 > 253.8 min
- `F2015-209820` (typesafe/jev-1.14): regression vs status quo: fuel 100.0 > 12.0 kg
- `F2015-291336` (typesafe/jev-1.14): regression vs status quo: fuel 228.0 > 0.0 kg
- `F2015-112468` (typesafe/jev-1.14): regression vs status quo: fuel 236.0 > 0.0 kg
- `F2015-268625` (typesafe/jev-1.14): regression vs status quo: fuel 196.0 > 0.0 kg
- `F2015-272020` (typesafe/jev-1.14): regression vs status quo: delay 444.0 > 443.8 min
- `F2015-74915` (typesafe/jev-1.14): regression vs status quo: delay 334.7 > 334.5 min
- `F2015-74915` (typesafe/jev-1.14): regression vs status quo: fuel 76.0 > 0.0 kg
- `F2015-274783` (typesafe/jev-1.14): regression vs status quo: fuel 108.0 > 0.0 kg
- `F2015-131437` (typesafe/jev-1.14): regression vs status quo: fuel 104.0 > 0.0 kg
- `F2015-273632` (typesafe/jev-1.14): regression vs status quo: fuel 20.0 > 0.0 kg
- `F2015-144520` (liquid/d1-ultra): regression vs status quo: fuel 92.0 > 0.0 kg
- `F2015-156608` (liquid/d1-ultra): regression vs status quo: fuel 220.0 > 0.0 kg
- `F2015-302254` (liquid/d1-ultra): regression vs status quo: fuel 52.0 > 0.0 kg
- `F2015-115532` (liquid/d1-ultra): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-169005` (liquid/d1-ultra): regression vs status quo: fuel 32.0 > 0.0 kg
- `F2015-161169` (liquid/d1-ultra): regression vs status quo: fuel 12.0 > 0.0 kg
- `F2015-209820` (liquid/d1-ultra): regression vs status quo: delay 256.2 > 253.8 min
- `F2015-209820` (liquid/d1-ultra): regression vs status quo: fuel 100.0 > 12.0 kg
- `F2015-112468` (liquid/d1-ultra): regression vs status quo: fuel 236.0 > 0.0 kg
- `F2015-291336` (liquid/d1-ultra): regression vs status quo: fuel 228.0 > 0.0 kg
- `F2015-268625` (liquid/d1-ultra): regression vs status quo: fuel 196.0 > 0.0 kg
- `F2015-272020` (liquid/d1-ultra): regression vs status quo: delay 444.0 > 443.8 min
- `F2015-274783` (liquid/d1-ultra): regression vs status quo: fuel 108.0 > 0.0 kg
- `F2015-131437` (liquid/d1-ultra): regression vs status quo: fuel 92.0 > 0.0 kg
- `F2015-273632` (liquid/d1-ultra): regression vs status quo: fuel 20.0 > 0.0 kg
- `F2015-144520` (baseten/elephant-1): regression vs status quo: fuel 92.0 > 0.0 kg
- `F2015-156608` (baseten/elephant-1): regression vs status quo: fuel 220.0 > 0.0 kg
- `F2015-302254` (baseten/elephant-1): regression vs status quo: fuel 52.0 > 0.0 kg
- `F2015-115532` (baseten/elephant-1): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-169005` (baseten/elephant-1): regression vs status quo: fuel 32.0 > 0.0 kg
- `F2015-161169` (baseten/elephant-1): regression vs status quo: fuel 12.0 > 0.0 kg
- `F2015-209820` (baseten/elephant-1): regression vs status quo: delay 256.2 > 253.8 min
- `F2015-209820` (baseten/elephant-1): regression vs status quo: fuel 100.0 > 12.0 kg
- `F2015-291336` (baseten/elephant-1): regression vs status quo: fuel 228.0 > 0.0 kg
- `F2015-112468` (baseten/elephant-1): regression vs status quo: fuel 236.0 > 0.0 kg
- `F2015-268625` (baseten/elephant-1): regression vs status quo: fuel 196.0 > 0.0 kg
- `F2015-272020` (baseten/elephant-1): regression vs status quo: delay 444.0 > 443.8 min
- `F2015-274783` (baseten/elephant-1): regression vs status quo: fuel 108.0 > 0.0 kg
- `F2015-131437` (baseten/elephant-1): regression vs status quo: fuel 92.0 > 0.0 kg
- `F2015-124231` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-273632` (baseten/elephant-1): regression vs status quo: fuel 20.0 > 0.0 kg
- `F2015-144520` (formula): regression vs status quo: fuel 92.0 > 0.0 kg
- `F2015-156608` (formula): regression vs status quo: fuel 220.0 > 0.0 kg
- `F2015-302254` (formula): regression vs status quo: fuel 52.0 > 0.0 kg
- `F2015-115532` (formula): regression vs status quo: delay 239.4 > 236.5 min
- `F2015-169005` (formula): regression vs status quo: fuel 228.0 > 0.0 kg
- `F2015-161169` (formula): regression vs status quo: fuel 36.0 > 0.0 kg
- `F2015-209820` (formula): regression vs status quo: delay 262.8 > 253.8 min
- `F2015-209820` (formula): regression vs status quo: fuel 212.0 > 12.0 kg
- `F2015-291336` (formula): regression vs status quo: fuel 228.0 > 0.0 kg
- `F2015-112468` (formula): regression vs status quo: fuel 236.0 > 0.0 kg
- `F2015-268625` (formula): regression vs status quo: fuel 196.0 > 0.0 kg
- `F2015-272020` (formula): regression vs status quo: delay 444.7 > 443.8 min
- `F2015-272020` (formula): regression vs status quo: fuel 24.0 > 0.0 kg
- `F2015-131437` (formula): regression vs status quo: fuel 92.0 > 0.0 kg
- `F2015-274783` (formula): regression vs status quo: fuel 168.0 > 0.0 kg
- `F2015-163396` (formula): regression vs status quo: fuel 112.0 > 0.0 kg
- `F2015-273632` (formula): regression vs status quo: fuel 84.0 > 0.0 kg

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- typesafe/jev-1.14: 7,907 chars (within budget)
- liquid/d1-ultra: 7,909 chars (within budget)
- baseten/elephant-1: 7,907 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- typesafe/jev-1.14: PLT-FUEL-RESERVE 8
- liquid/d1-ultra: PLT-FUEL-RESERVE 7
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
| v4-airlines | baseline | 326.69 | 1.36 | 0.0 | 1.083 | 36.92 | 1.748 | n/a | n/a | 26 |
| v4-airlines | typesafe/jev-1.14 | 334.152 | 223.68 | 4.0 | 0.434 | 34.56 | 1.988 | 100.0 | 0 | 50 |
| v4-airlines | liquid/d1-ultra | 334.106 | 218.8 | 4.0 | 0.423 | 34.56 | 2.002 | 100.0 | 0 | 50 |
| v4-airlines | baseten/elephant-1 | 334.562 | 217.76 | 4.08 | 0.458 | 34.24 | 1.876 | 98.0 | 0 | 50 |
| v4-airlines | formula | 333.4 | 210.16 | 2.0 | 0.441 | 34.278 | 1.966 | 100.0 | 0 | 50 |

## Attribution

Engine v3-traffic to v4-airlines. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Physics (status quo under the new engine minus under the old): delay_min +0, fuel_kg +0, lap_share_pct +0, taxi_wait_min +0, pax_protected_pct +0, overtime_min +0. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- typesafe/jev-1.14: delay_min +7.462, fuel_kg +222.32, lap_share_pct +4, taxi_wait_min -0.649, pax_protected_pct -2.36, overtime_min +0.24
- liquid/d1-ultra: delay_min +7.416, fuel_kg +217.44, lap_share_pct +4, taxi_wait_min -0.66, pax_protected_pct -2.36, overtime_min +0.254
- baseten/elephant-1: delay_min +7.872, fuel_kg +216.4, lap_share_pct +4.08, taxi_wait_min -0.625, pax_protected_pct -2.68, overtime_min +0.128
- formula: delay_min +6.71, fuel_kg +208.8, lap_share_pct +2, taxi_wait_min -0.642, pax_protected_pct -2.642, overtime_min +0.218

Headline: under engine v4-airlines, negotiation (formula) adds 6.71 min of mean delay against the status quo.

Regressions (22):

- `F2015-208692` (typesafe/jev-1.14) delay_min 181.3 to 186.4; negotiation
- `F2015-343919` (typesafe/jev-1.14) delay_min 251.1 to 253.1; negotiation
- `F2015-60842` (typesafe/jev-1.14) delay_min 279.7 to 284.6; negotiation
- `F2015-60842` (typesafe/jev-1.14) fuel_kg 0.0 to 16.0; negotiation
- `F2015-208678` (liquid/d1-ultra) delay_min 698.3 to 700.3; negotiation
- `F2015-208678` (liquid/d1-ultra) fuel_kg 88.0 to 168.0; negotiation
- `F2015-208692` (liquid/d1-ultra) delay_min 175.3 to 182.1; negotiation
- `F2015-209722` (liquid/d1-ultra) delay_min 132.4 to 148.6; negotiation
- `F2015-209722` (liquid/d1-ultra) fuel_kg 272.0 to 1088.0; negotiation
- `F2015-251827` (liquid/d1-ultra) delay_min 240.8 to 244.9; negotiation
- `F2015-251827` (liquid/d1-ultra) fuel_kg 12.0 to 180.0; negotiation
- `F2015-165543` (baseten/elephant-1) fuel_kg 348.0 to 360.0; negotiation
- `F2015-208692` (baseten/elephant-1) delay_min 181.3 to 186.4; negotiation
- `F2015-246479` (baseten/elephant-1) delay_min 198.9 to 206.3; negotiation
- `F2015-246479` (baseten/elephant-1) fuel_kg 688.0 to 728.0; negotiation
- `F2015-268625` (baseten/elephant-1) delay_min 318.6 to 327.3; negotiation
- `F2015-298859` (baseten/elephant-1) delay_min 451.0 to 458.5; negotiation
- `F2015-298859` (baseten/elephant-1) fuel_kg 24.0 to 72.0; negotiation
- `F2015-343919` (baseten/elephant-1) delay_min 251.1 to 253.0; negotiation
- `F2015-165543` (formula) delay_min 200.9 to 208.4; negotiation
- ... and 2 more in benchmark.json

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

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 50 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
