# Benchmark (50 waves, seed 7, source flights2015)
Generated 2026-11-05T12:06:00+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 416.068 | 15.28 | 30.614 | 3.002 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 417.48 | 57.76 | 32.778 | - | - | - | - | - | - |
| liquid/d1-ultra | 50/50 | 94.0 | 1.86 | 6.0 | 0 | 418.312 | 175.44 | 31.354 | 3.91 | 64.0 | 0.0012 | 576.0 | 1231.8 | 0.31 |
| formula | 50/50 | 98.0 | 1.98 | 2.0 | 0 | 417.244 | 204.24 | 31.56 | 3.802 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 23 waves: F2015-144520, F2015-161169, F2015-163396, F2015-18435, F2015-205196, F2015-208678, F2015-208692, F2015-271058, F2015-273632, F2015-274783, F2015-282726, F2015-302254, F2015-314303, F2015-317184, F2015-324426, F2015-335957, F2015-343919, F2015-53108, F2015-60842, F2015-63104, F2015-63860, F2015-74915, F2015-84851.

- liquid/d1-ultra vs status quo on 27 feasible-status-quo waves: delay 379.415 min vs 381.196 min
- formula vs status quo on 27 feasible-status-quo waves: delay 379.622 min vs 381.196 min

Optimiser reference (n=50, 50 feasible): mean delay 417.48 min, 95% CI [360.847, 474.113]; no models, no negotiation.
- liquid/d1-ultra: delay 95% CI [361.279, 475.345]; gap to optimiser 0.832 min; share of optimiser gain over status quo 23.59% (n=20 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [360.31, 474.178]; gap to optimiser -0.236 min; share of optimiser gain over status quo 20.84% (n=20 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** formula (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- Formula selected: the best model arm, liquid/d1-ultra, is worse than formula on delay (418.312 > 417.244 min) and not better on pax protected.
- No arm met error < 2%, p95 < 1000 ms and consensus >= 90%: liquid/d1-ultra p95 1231.8 ms / error 0.31% / consensus 94.0%. Picked best outcome among eligible model arms: liquid/d1-ultra.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- liquid/d1-ultra p95 all decisions incl. fallbacks: 1283.3 ms
- liquid/d1-ultra missed p95 < 1000 ms: 1231.8 ms
- liquid/d1-ultra outcomes partly from solver fallback on 3/50 waves
- Mean delay: liquid/d1-ultra 418.312 min vs formula 417.244 min vs status quo 416.068 min.
- Outage test ran with the best eligible model arm liquid/d1-ultra (overall winner is formula).

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"liquid/d1-ultra-20261015": 295}

## Ablation: safety engine off

48.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-317184` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-144520` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-208678` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-208678` (formula): no consensus: deterministic solver fallback

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- liquid/d1-ultra: 9,048 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- liquid/d1-ultra: ATC-TAXI-CONFLICT 4, GND-GATE-NOTICE 4, PLT-FUEL-RESERVE 4
- formula: ATC-TAXI-CONFLICT 4, GND-GATE-NOTICE 6, PLT-FUEL-RESERVE 4

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
| v4-airlines | liquid/d1-ultra | 418.312 | 175.44 | 6.38 | 2.736 | 31.354 | 3.91 | 94.0 | 0 | 50 |
| v4-airlines | formula | 417.244 | 204.24 | 4.08 | 2.423 | 31.56 | 3.802 | 98.0 | 0 | 50 |

## Attribution

Engine v3-traffic to v4-airlines. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Physics (status quo under the new engine minus under the old): delay_min +89.378, fuel_kg +13.92, lap_share_pct +0, taxi_wait_min +1.985, pax_protected_pct -6.306, overtime_min +1.254. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- liquid/d1-ultra: delay_min +2.244, fuel_kg +160.16, lap_share_pct +6.38, taxi_wait_min -0.332, pax_protected_pct +0.74, overtime_min +0.908
- formula: delay_min +1.176, fuel_kg +188.96, lap_share_pct +4.08, taxi_wait_min -0.645, pax_protected_pct +0.946, overtime_min +0.8

Headline: under engine v4-airlines, negotiation (formula) adds 1.176 min of mean delay against the status quo.

Regressions (106):

- `F2015-105396` (liquid/d1-ultra) delay_min 154.8 to 452.3; physics
- `F2015-106428` (liquid/d1-ultra) delay_min 43.4 to 148.7; physics
- `F2015-110532` (liquid/d1-ultra) delay_min 474.7 to 781.9; negotiation
- `F2015-110532` (liquid/d1-ultra) fuel_kg 0.0 to 132.0; physics
- `F2015-112468` (liquid/d1-ultra) delay_min 168.2 to 235.6; negotiation
- `F2015-124231` (liquid/d1-ultra) delay_min 348.0 to 585.0; negotiation
- `F2015-131437` (liquid/d1-ultra) delay_min 475.2 to 602.9; negotiation
- `F2015-144520` (liquid/d1-ultra) delay_min 629.8 to 841.5; negotiation
- `F2015-15580` (liquid/d1-ultra) delay_min 387.7 to 852.9; physics
- `F2015-161169` (liquid/d1-ultra) delay_min 278.0 to 757.7; negotiation
- `F2015-161169` (liquid/d1-ultra) fuel_kg 12.0 to 76.0; negotiation
- `F2015-163396` (liquid/d1-ultra) delay_min 185.0 to 372.6; negotiation
- `F2015-163396` (liquid/d1-ultra) fuel_kg 0.0 to 112.0; negotiation
- `F2015-165543` (liquid/d1-ultra) delay_min 209.6 to 517.8; physics
- `F2015-169005` (liquid/d1-ultra) delay_min 204.1 to 517.8; physics
- `F2015-18435` (liquid/d1-ultra) delay_min 377.5 to 388.0; physics
- `F2015-18435` (liquid/d1-ultra) fuel_kg 0.0 to 52.0; negotiation
- `F2015-205196` (liquid/d1-ultra) delay_min 310.8 to 493.3; physics
- `F2015-208678` (liquid/d1-ultra) delay_min 698.3 to 710.9; negotiation
- `F2015-208678` (liquid/d1-ultra) fuel_kg 88.0 to 760.0; negotiation
- ... and 86 more in benchmark.json

## Taxi-in validation vs BTS 2015

Focus flight of each benchmark wave on the real surface tier (OSM route) with a real BTS TAXI_IN, under the FCFS plan. Routed = ROT + unimpeded routed taxi (wheels-on to in-block, the span TAXI_IN measures). No fitting: speeds are fixed up front. BTS TAXI_IN includes gate waits and ramp delays, so routed < real (bias < 0) is expected; the bias is stated, not corrected.

n = 45, MAE 3.18 min, bias (routed - real) -2.57 min.

| airport | n | routed median min | BTS 2015 median min |
|---|---|---|---|
| ATL | 2 | 5.04 | 8.0 |
| BNA | 2 | 4.9 | 6.0 |
| BOS | 2 | 8.14 | 6.0 |
| BWI | 2 | 4.56 | 6.0 |
| DCA | 2 | 3.92 | 5.0 |
| DEN | 2 | 7.8 | 7.0 |
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
| MDW | 1 | 3.75 | 6.0 |
| MIA | 2 | 4.25 | 7.0 |
| MSP | 2 | 5.56 | 5.0 |
| ORD | 1 | 9.12 | 12.0 |
| PHL | 1 | 3.87 | 6.0 |
| PHX | 2 | 4.36 | 6.0 |
| SAN | 1 | 4.75 | 4.0 |
| SEA | 2 | 3.17 | 7.0 |
| SFO | 2 | 4.05 | 5.0 |
| SLC | 1 | 7.44 | 5.0 |
| TPA | 2 | 2.3 | 4.0 |

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
