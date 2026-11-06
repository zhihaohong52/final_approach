# Benchmark (50 waves, seed 7, source bts)
Generated 2026-11-06T13:20:37+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 437.272 | 148.504 | 47.296 | 3.062 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 507.202 | 636.314 | 41.724 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 88.0 | 1.76 | 12.0 | 0 | 466.952 | 435.514 | 44.05 | 0.34 | 56.0 | 0.0066 | 461.0 | 624.4 | 0.0 |
| liquid/d1-ultra | 50/50 | 88.0 | 1.78 | 12.0 | 0 | 471.852 | 467.994 | 44.72 | 0.218 | 50.0 | 0.0016 | 686.0 | 1157.2 | 0.0 |
| formula | 50/50 | 94.0 | 1.78 | 6.0 | 0 | 454.65 | 391.754 | 45.81 | 0.494 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 29 waves: ATL-2024-06-17-21, ATL-2024-07-22-02, AUS-2024-07-26-17, BNA-2024-06-27-16, BNA-2024-07-07-22, CLT-2024-07-25-18, DCA-2024-08-08-15, DEN-2024-06-09-13, DEN-2024-08-16-22, DFW-2024-08-21-23, DTW-2024-06-03-10, EWR-2024-08-31-00, IAD-2024-07-18-22, JFK-2024-08-30-08, LAS-2024-08-03-22, LGA-2024-06-14-22, MCO-2024-07-18-16, MCO-2024-08-05-01, MSP-2024-07-23-20, ORD-2024-06-07-20, PHL-2024-07-20-22, PHL-2024-08-20-18, PHX-2024-06-28-21, SAN-2024-07-21-20, SAN-2024-08-11-20, SFO-2024-07-28-23, SLC-2024-08-09-23, SLC-2024-08-23-20, TPA-2024-08-05-19.

- typesafe/jev-1.14 vs status quo on 21 feasible-status-quo waves: delay 479.038 min vs 485.595 min
- liquid/d1-ultra vs status quo on 21 feasible-status-quo waves: delay 479.243 min vs 485.595 min
- formula vs status quo on 21 feasible-status-quo waves: delay 479.914 min vs 485.595 min

Optimiser reference (n=50, 50 feasible): mean delay 507.202 min, 95% CI [418.226, 596.178]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [377.982, 555.922]; gap to optimiser -40.25 min; share of optimiser gain over status quo 67.93% (n=19 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [382.902, 560.802]; gap to optimiser -35.35 min; share of optimiser gain over status quo 65.81% (n=19 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [365.281, 544.019]; gap to optimiser -52.552 min; share of optimiser gain over status quo 58.86% (n=19 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** formula (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- Formula selected: the best model arm, typesafe/jev-1.14, is worse than formula on delay (466.952 > 454.65 min) and not better on pax protected.
- No arm met error < 2%, p95 < 1000 ms and consensus >= 90%: typesafe/jev-1.14 p95 624.4 ms / error 0.0% / consensus 88.0%, liquid/d1-ultra p95 1157.2 ms / error 0.0% / consensus 88.0%. Picked best outcome among eligible model arms: typesafe/jev-1.14.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 624.4 ms
- typesafe/jev-1.14 missed consensus >= 90%: 88.0%
- typesafe/jev-1.14 outcomes partly from solver fallback on 6/50 waves
- liquid/d1-ultra p95 all decisions incl. fallbacks: 1157.2 ms
- liquid/d1-ultra missed p95 < 1000 ms: 1157.2 ms
- liquid/d1-ultra missed consensus >= 90%: 88.0%
- liquid/d1-ultra outcomes partly from solver fallback on 6/50 waves
- Mean delay: typesafe/jev-1.14 466.952 min vs formula 454.65 min vs status quo 437.272 min.
- Outage test ran with the best eligible model arm typesafe/jev-1.14 (overall winner is formula).

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"typesafe/jev-1.14-20261014": 273}

## Ablation: safety engine off

56.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `SAN-2024-07-21-20` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `ATL-2024-06-17-21` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `SFO-2024-07-28-23` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `MSP-2024-07-23-20` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `MCO-2024-08-05-01` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `SAN-2024-08-11-20` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `SAN-2024-07-21-20` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `ATL-2024-06-17-21` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `LAS-2024-08-03-22` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `SFO-2024-07-28-23` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `PHX-2024-06-28-21` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `SAN-2024-08-11-20` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `SAN-2024-07-21-20` (formula): no consensus: deterministic solver fallback
- `SFO-2024-07-28-23` (formula): no consensus: deterministic solver fallback
- `SAN-2024-08-11-20` (formula): no consensus: deterministic solver fallback

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- typesafe/jev-1.14: 8,433 chars (within budget)
- liquid/d1-ultra: 8,433 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- typesafe/jev-1.14: ATC-TAXI-CONFLICT 2, ATC-TAXI-ROUTE 3, GND-GATE-FREE 8, GND-GATE-NOTICE 6
- liquid/d1-ultra: ATC-TAXI-CONFLICT 1, GND-GATE-FREE 6, GND-GATE-NOTICE 3
- formula: ATC-TAXI-CONFLICT 3, GND-GATE-FREE 10, GND-GATE-NOTICE 2

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
| v5-taxi | baseline | 437.272 | 148.504 | 6.0 | 16.148 | 47.296 | 3.062 | n/a | n/a | 21 |
| v5-taxi | typesafe/jev-1.14 | 466.952 | 435.514 | 0.0 | 2.752 | 44.05 | 0.34 | 88.0 | 0 | 50 |
| v5-taxi | liquid/d1-ultra | 471.852 | 467.994 | 2.27 | 3.254 | 44.72 | 0.218 | 88.0 | 0 | 50 |
| v5-taxi | formula | 454.65 | 391.754 | 2.13 | 3.388 | 45.81 | 0.494 | 94.0 | 0 | 50 |

## Attribution

Engine v4-airlines to v5-taxi. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Note: wave sets differ (0 shared of 50 previous / 50 current): the physics delta mixes waves.
Physics (status quo under the new engine minus under the old): delay_min +21.204, fuel_kg +133.224, lap_share_pct +6, taxi_wait_min +13.08, pax_protected_pct +16.682, overtime_min +0.06. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- typesafe/jev-1.14: delay_min +29.68, fuel_kg +287.01, lap_share_pct -6, taxi_wait_min -13.396, pax_protected_pct -3.246, overtime_min -2.722
- liquid/d1-ultra: delay_min +34.58, fuel_kg +319.49, lap_share_pct -3.73, taxi_wait_min -12.894, pax_protected_pct -2.576, overtime_min -2.844
- formula: delay_min +17.378, fuel_kg +243.25, lap_share_pct -3.87, taxi_wait_min -12.76, pax_protected_pct -1.486, overtime_min -2.568

Headline: under engine v5-taxi, negotiation (formula) adds 17.378 min of mean delay against the status quo.

Regressions (0):

- none

## Taxi-in validation vs BTS 2015

Focus flight of each benchmark wave on the real surface tier (OSM route) with a real BTS TAXI_IN, under the FCFS plan. Routed = ROT + unimpeded routed taxi (wheels-on to in-block, the span TAXI_IN measures). No fitting: speeds are fixed up front. BTS TAXI_IN includes gate waits and ramp delays, so routed < real (bias < 0) is expected; the bias is stated, not corrected.

n = 0, MAE n/a min, bias (routed - real) n/a min.

| airport | n | routed median min | BTS 2015 median min |
|---|---|---|---|

Largest gaps (cause is a heuristic):

- none

## Coverage

Tier counts over the 50 benchmark waves (waves that failed to build count as none):

- procedures: star+iap 49, none 1
- surface: real 47, generic 3
- ground_traffic: real 50

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 50 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
