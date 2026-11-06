# Benchmark (50 waves, seed 7, source flights2015)
Generated 2026-11-06T13:28:27+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 415.712 | 14.08 | 30.202 | 2.976 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 417.108 | 61.6 | 33.21 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 96.0 | 1.84 | 4.0 | 0 | 414.726 | 141.44 | 31.162 | 3.674 | 60.0 | 0.0081 | 453.0 | 616.8 | 0.0 |
| liquid/d1-ultra | 50/50 | 98.0 | 1.96 | 2.0 | 0 | 414.91 | 192.4 | 31.116 | 3.666 | 100.0 | n/a | n/a | n/a | 100.0 |
| formula | 50/50 | 98.0 | 1.96 | 2.0 | 0 | 414.91 | 192.4 | 31.116 | 3.666 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 23 waves: F2015-144520, F2015-161169, F2015-163396, F2015-18435, F2015-205196, F2015-208678, F2015-208692, F2015-209820, F2015-271058, F2015-273632, F2015-274783, F2015-282726, F2015-302254, F2015-314303, F2015-317184, F2015-324426, F2015-335957, F2015-343919, F2015-53108, F2015-60842, F2015-63104, F2015-63860, F2015-84851.

- typesafe/jev-1.14 vs status quo on 27 feasible-status-quo waves: delay 370.981 min vs 372.956 min
- liquid/d1-ultra vs status quo on 27 feasible-status-quo waves: delay 370.7 min vs 372.956 min
- formula vs status quo on 27 feasible-status-quo waves: delay 370.7 min vs 372.956 min

Optimiser reference (n=50, 50 feasible): mean delay 417.108 min, 95% CI [360.306, 473.91]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [357.652, 471.8]; gap to optimiser -2.382 min; share of optimiser gain over status quo 24.73% (n=22 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [357.514, 472.306]; gap to optimiser -2.198 min; share of optimiser gain over status quo 28.26% (n=22 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [357.514, 472.306]; gap to optimiser -2.198 min; share of optimiser gain over status quo 28.26% (n=22 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** typesafe/jev-1.14 (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- typesafe/jev-1.14 is the best model arm: it meets all three thresholds (error, p95, consensus) and has the best outcome among the qualifying model arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 616.8 ms
- typesafe/jev-1.14 outcomes partly from solver fallback on 2/50 waves
- liquid/d1-ultra disqualified: zero model-answered decisions
- Mean delay: typesafe/jev-1.14 414.726 min vs formula 414.91 min vs status quo 415.712 min.

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"typesafe/jev-1.14-20261014": 291}

## Ablation: safety engine off

50.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-53108` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-208678` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-208678` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-208678` (formula): no consensus: deterministic solver fallback

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- typesafe/jev-1.14: 8,867 chars (within budget)
- liquid/d1-ultra: 8,989 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- typesafe/jev-1.14: ATC-TAXI-CONFLICT 6, GND-GATE-FREE 1, GND-GATE-NOTICE 6, PLT-FUEL-RESERVE 4
- liquid/d1-ultra: ATC-TAXI-CONFLICT 6, GND-GATE-FREE 1, GND-GATE-NOTICE 6, PLT-FUEL-RESERVE 4
- formula: ATC-TAXI-CONFLICT 6, GND-GATE-FREE 1, GND-GATE-NOTICE 6, PLT-FUEL-RESERVE 4

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
| v4-airlines | baseline | 416.068 | 15.28 | 0.0 | 3.068 | 30.614 | 3.002 | n/a | n/a | 27 |
| v4-airlines | typesafe/jev-1.14 | 416.984 | 174.0 | 6.25 | 2.484 | 31.606 | 3.938 | 96.0 | 0 | 50 |
| v4-airlines | liquid/d1-ultra | 417.438 | 154.8 | 6.38 | 2.459 | 31.354 | 3.734 | 94.0 | 0 | 50 |
| v4-airlines | formula | 417.244 | 204.24 | 4.08 | 2.423 | 31.56 | 3.802 | 98.0 | 0 | 50 |
| v5-taxi | baseline | 415.712 | 14.08 | 0.0 | 2.067 | 30.202 | 2.976 | n/a | n/a | 27 |
| v5-taxi | typesafe/jev-1.14 | 414.726 | 141.44 | 4.17 | 1.124 | 31.162 | 3.674 | 96.0 | 0 | 50 |
| v5-taxi | liquid/d1-ultra | 414.91 | 192.4 | 6.12 | 1.033 | 31.116 | 3.666 | 98.0 | 0 | 50 |
| v5-taxi | formula | 414.91 | 192.4 | 6.12 | 1.033 | 31.116 | 3.666 | 98.0 | 0 | 50 |

## Attribution

Engine v4-airlines to v5-taxi. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Physics (status quo under the new engine minus under the old): delay_min -0.356, fuel_kg -1.2, lap_share_pct +0, taxi_wait_min -1.001, pax_protected_pct -0.412, overtime_min -0.026. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- typesafe/jev-1.14: delay_min -0.986, fuel_kg +127.36, lap_share_pct +4.17, taxi_wait_min -0.943, pax_protected_pct +0.96, overtime_min +0.698
- liquid/d1-ultra: delay_min -0.802, fuel_kg +178.32, lap_share_pct +6.12, taxi_wait_min -1.034, pax_protected_pct +0.914, overtime_min +0.69
- formula: delay_min -0.802, fuel_kg +178.32, lap_share_pct +6.12, taxi_wait_min -1.034, pax_protected_pct +0.914, overtime_min +0.69

Headline: under engine v5-taxi, negotiation (typesafe/jev-1.14) cuts 0.986 min of mean delay against the status quo.

Regressions (57):

- `F2015-102056` (typesafe/jev-1.14) delay_min 507.8 to 517.0; physics
- `F2015-106428` (typesafe/jev-1.14) delay_min 154.3 to 156.5; physics
- `F2015-144520` (typesafe/jev-1.14) delay_min 841.5 to 844.1; negotiation
- `F2015-144520` (typesafe/jev-1.14) fuel_kg 24.0 to 64.0; negotiation
- `F2015-205196` (typesafe/jev-1.14) delay_min 493.5 to 493.7; negotiation
- `F2015-208678` (typesafe/jev-1.14) delay_min 710.9 to 723.9; physics
- `F2015-209722` (typesafe/jev-1.14) delay_min 194.3 to 201.5; physics
- `F2015-210010` (typesafe/jev-1.14) delay_min 267.3 to 273.7; physics
- `F2015-267904` (typesafe/jev-1.14) delay_min 313.1 to 313.5; physics
- `F2015-271058` (typesafe/jev-1.14) delay_min 697.8 to 698.8; negotiation
- `F2015-271058` (typesafe/jev-1.14) fuel_kg 0.0 to 148.0; negotiation
- `F2015-291336` (typesafe/jev-1.14) delay_min 226.6 to 227.2; physics
- `F2015-298859` (typesafe/jev-1.14) delay_min 190.4 to 195.1; negotiation
- `F2015-314303` (typesafe/jev-1.14) delay_min 858.8 to 858.9; negotiation
- `F2015-102056` (liquid/d1-ultra) delay_min 507.8 to 517.0; physics
- `F2015-106428` (liquid/d1-ultra) delay_min 148.7 to 156.5; negotiation
- `F2015-144520` (liquid/d1-ultra) delay_min 841.5 to 842.0; negotiation
- `F2015-144520` (liquid/d1-ultra) fuel_kg 24.0 to 64.0; negotiation
- `F2015-205196` (liquid/d1-ultra) delay_min 493.3 to 495.4; negotiation
- `F2015-205196` (liquid/d1-ultra) fuel_kg 232.0 to 256.0; negotiation
- ... and 37 more in benchmark.json

## Taxi-in validation vs BTS 2015

Focus flight of each benchmark wave on the real surface tier (OSM route) with a real BTS TAXI_IN, under the FCFS plan. Routed = ROT + unimpeded routed taxi (wheels-on to in-block, the span TAXI_IN measures). No fitting: speeds are fixed up front. BTS TAXI_IN includes gate waits and ramp delays, so routed < real (bias < 0) is expected; the bias is stated, not corrected.

n = 45, MAE 3.22 min, bias (routed - real) -2.43 min.

| airport | n | routed median min | BTS 2015 median min |
|---|---|---|---|
| ATL | 2 | 6.99 | 8.0 |
| BNA | 2 | 4.08 | 6.0 |
| BOS | 2 | 7.14 | 6.0 |
| BWI | 2 | 4.56 | 6.0 |
| DCA | 2 | 3.92 | 5.0 |
| DEN | 2 | 9.02 | 7.0 |
| DFW | 1 | 9.76 | 10.0 |
| DTW | 1 | 5.25 | 8.0 |
| EWR | 2 | 6.02 | 9.0 |
| FLL | 2 | 3.81 | 5.0 |
| IAD | 2 | 6.07 | 6.0 |
| IAH | 2 | 8.68 | 8.0 |
| JFK | 1 | 4.79 | 8.0 |
| LAX | 2 | 4.05 | 9.0 |
| LGA | 2 | 4.6 | 7.0 |
| MCO | 1 | 5.06 | 7.0 |
| MDW | 1 | 4.09 | 6.0 |
| MIA | 2 | 4.25 | 7.0 |
| MSP | 2 | 5.56 | 5.0 |
| ORD | 1 | 9.12 | 12.0 |
| PHL | 1 | 3.87 | 6.0 |
| PHX | 2 | 5.17 | 6.0 |
| SAN | 1 | 4.75 | 4.0 |
| SEA | 2 | 3.17 | 7.0 |
| SFO | 2 | 4.33 | 5.0 |
| SLC | 1 | 7.44 | 5.0 |
| TPA | 2 | 2.96 | 4.0 |

Largest gaps (cause is a heuristic):

- `F2015-334031` WN2338: routed 3.8 min, real 24.0 min; BTS includes gate wait
- `F2015-317184` AA1465: routed 3.7 min, real 13.0 min; -
- `F2015-115532` UA1623: routed 4.0 min, real 12.0 min; -
- `F2015-106428` DL1212: routed 4.4 min, real 12.0 min; -
- `F2015-251827` WN998: routed 4.4 min, real 11.0 min; -

## Coverage

Tier counts over the 50 benchmark waves (waves that failed to build count as none):

- procedures: star+iap 50
- surface: real 47, generic 3
- ground_traffic: real 50

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 50 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
