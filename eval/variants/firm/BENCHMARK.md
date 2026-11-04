# Benchmark (50 waves, seed 7, source flights2015)
Generated 2026-11-04T00:12:29+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 326.69 | 1.36 | 36.92 | 1.748 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 329.44 | 83.2 | 34.704 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 94.0 | 1.98 | 6.0 | 0 | 334.134 | 148.72 | 34.72 | 2.214 | 62.0 | 0.0083 | 416.0 | 508.6 | 0.0 |
| liquid/d1-ultra | 50/50 | 96.0 | 1.88 | 4.0 | 0 | 333.996 | 132.48 | 34.72 | 1.742 | 70.0 | 0.0012 | 545.0 | 956.5 | 0.0 |
| baseten/elephant-1 | 50/50 | 94.0 | 2.0 | 6.0 | 0 | 334.39 | 152.56 | 34.4 | 2.074 | 60.0 | 0.0137 | 484.0 | 586.6 | 0.0 |
| formula | 50/50 | 98.0 | 1.92 | 2.0 | 0 | 335.54 | 154.72 | 34.438 | 2.012 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 24 waves: F2015-102056, F2015-105396, F2015-110532, F2015-112396, F2015-124231, F2015-144568, F2015-165543, F2015-205196, F2015-208678, F2015-208692, F2015-209722, F2015-210010, F2015-246479, F2015-251827, F2015-254935, F2015-267904, F2015-282726, F2015-298859, F2015-335957, F2015-343919, F2015-43962, F2015-60842, F2015-63104, F2015-63860.

- typesafe/jev-1.14 vs status quo on 26 feasible-status-quo waves: delay 320.904 min vs 324.665 min
- liquid/d1-ultra vs status quo on 26 feasible-status-quo waves: delay 320.623 min vs 324.665 min
- baseten/elephant-1 vs status quo on 26 feasible-status-quo waves: delay 320.946 min vs 324.665 min
- formula vs status quo on 26 feasible-status-quo waves: delay 322.985 min vs 324.665 min

Optimiser reference (n=50, 50 feasible): mean delay 329.44 min, 95% CI [282.467, 376.413]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [287.4, 380.868]; gap to optimiser 4.694 min; share of optimiser gain over status quo 34.49% (n=22 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [287.155, 380.837]; gap to optimiser 4.556 min; share of optimiser gain over status quo 37.06% (n=22 waves with feasible status quo beaten by the optimiser).
- baseten/elephant-1: delay 95% CI [287.585, 381.195]; gap to optimiser 4.95 min; share of optimiser gain over status quo 34.1% (n=22 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [289.252, 381.828]; gap to optimiser 6.1 min; share of optimiser gain over status quo 15.41% (n=22 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** liquid/d1-ultra (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- liquid/d1-ultra is the best model arm: it meets all three thresholds (error, p95, consensus) and has the best outcome among the qualifying model arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 508.6 ms
- typesafe/jev-1.14 outcomes partly from solver fallback on 3/50 waves
- liquid/d1-ultra p95 all decisions incl. fallbacks: 956.5 ms
- liquid/d1-ultra outcomes partly from solver fallback on 2/50 waves
- baseten/elephant-1 p95 all decisions incl. fallbacks: 586.6 ms
- baseten/elephant-1 outcomes partly from solver fallback on 3/50 waves
- Mean delay: liquid/d1-ultra 333.996 min vs formula 335.54 min vs status quo 326.69 min.

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"liquid/d1-ultra-20261015": 246}

## Ablation: safety engine off

48.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-112396` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-208692` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-246479` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-208692` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-246479` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-208692` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-246479` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-124231` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-246479` (formula): no consensus: deterministic solver fallback

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- typesafe/jev-1.14: 8,173 chars (within budget)
- liquid/d1-ultra: 8,329 chars (within budget)
- baseten/elephant-1: 8,329 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- typesafe/jev-1.14: PLT-FUEL-RESERVE 7
- liquid/d1-ultra: PLT-FUEL-RESERVE 8
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
| v4-airlines | typesafe/jev-1.14 | 334.134 | 148.72 | 2.13 | 0.783 | 34.72 | 2.214 | 94.0 | 0 | 50 |
| v4-airlines | liquid/d1-ultra | 333.996 | 132.48 | 2.08 | 0.944 | 34.72 | 1.742 | 96.0 | 0 | 50 |
| v4-airlines | baseten/elephant-1 | 334.39 | 152.56 | 2.13 | 0.771 | 34.4 | 2.074 | 94.0 | 0 | 50 |
| v4-airlines | formula | 335.54 | 154.72 | 2.04 | 0.846 | 34.438 | 2.012 | 98.0 | 0 | 50 |

## Attribution

Engine v3-traffic to v4-airlines. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Physics (status quo under the new engine minus under the old): delay_min +0, fuel_kg +0, lap_share_pct +0, taxi_wait_min +0, pax_protected_pct +0, overtime_min +0. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- typesafe/jev-1.14: delay_min +7.444, fuel_kg +147.36, lap_share_pct +2.13, taxi_wait_min -0.3, pax_protected_pct -2.2, overtime_min +0.466
- liquid/d1-ultra: delay_min +7.306, fuel_kg +131.12, lap_share_pct +2.08, taxi_wait_min -0.139, pax_protected_pct -2.2, overtime_min -0.006
- baseten/elephant-1: delay_min +7.7, fuel_kg +151.2, lap_share_pct +2.13, taxi_wait_min -0.312, pax_protected_pct -2.52, overtime_min +0.326
- formula: delay_min +8.85, fuel_kg +153.36, lap_share_pct +2.04, taxi_wait_min -0.237, pax_protected_pct -2.482, overtime_min +0.264

Headline: under engine v4-airlines, negotiation (liquid/d1-ultra) adds 7.306 min of mean delay against the status quo.

Regressions (66):

- `F2015-112468` (typesafe/jev-1.14) delay_min 168.2 to 179.6; negotiation
- `F2015-124231` (typesafe/jev-1.14) delay_min 348.0 to 349.9; negotiation
- `F2015-131437` (typesafe/jev-1.14) delay_min 475.4 to 475.9; negotiation
- `F2015-144520` (typesafe/jev-1.14) delay_min 629.8 to 630.7; negotiation
- `F2015-144568` (typesafe/jev-1.14) delay_min 507.8 to 510.1; negotiation
- `F2015-156608` (typesafe/jev-1.14) delay_min 335.3 to 341.2; negotiation
- `F2015-161169` (typesafe/jev-1.14) delay_min 278.0 to 282.8; negotiation
- `F2015-169005` (typesafe/jev-1.14) delay_min 204.1 to 216.9; negotiation
- `F2015-268625` (typesafe/jev-1.14) delay_min 327.3 to 331.5; negotiation
- `F2015-273632` (typesafe/jev-1.14) delay_min 200.9 to 216.9; negotiation
- `F2015-274783` (typesafe/jev-1.14) delay_min 222.9 to 236.9; negotiation
- `F2015-291336` (typesafe/jev-1.14) delay_min 187.6 to 191.9; negotiation
- `F2015-334031` (typesafe/jev-1.14) delay_min 159.8 to 160.8; negotiation
- `F2015-334031` (typesafe/jev-1.14) fuel_kg 8.0 to 28.0; negotiation
- `F2015-60842` (typesafe/jev-1.14) delay_min 279.7 to 284.6; negotiation
- `F2015-60842` (typesafe/jev-1.14) fuel_kg 0.0 to 16.0; negotiation
- `F2015-112468` (liquid/d1-ultra) delay_min 168.2 to 179.6; negotiation
- `F2015-124231` (liquid/d1-ultra) delay_min 348.0 to 349.9; negotiation
- `F2015-131437` (liquid/d1-ultra) delay_min 475.2 to 475.9; negotiation
- `F2015-144520` (liquid/d1-ultra) delay_min 629.8 to 630.7; negotiation
- ... and 46 more in benchmark.json

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
