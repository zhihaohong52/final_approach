# Benchmark (50 waves, seed 7, source bts)
Generated 2026-11-04T19:20:05+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 560.05 | 11.68 | 19.528 | 3.442 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 577.548 | 229.84 | 19.432 | - | - | - | - | - | - |
| liquid/d1-ultra | 50/50 | 92.0 | 1.56 | 8.0 | 0 | 571.462 | 244.88 | 19.172 | 0.176 | 62.0 | 0.0011 | 578.0 | 1194.0 | 0.0 |
| formula | 50/50 | 94.0 | 1.56 | 6.0 | 0 | 561.57 | 194.08 | 19.606 | 0.864 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 32 waves: ATL-2024-06-15-18, ATL-2024-07-24-19, AUS-2024-06-09-18, AUS-2024-07-28-20, BNA-2024-07-07-21, BNA-2024-07-15-18, BOS-2024-07-12-20, BWI-2024-07-12-19, BWI-2024-07-19-12, CLT-2024-08-14-15, DCA-2024-06-11-21, DCA-2024-06-22-16, DEN-2024-07-03-19, FLL-2024-07-10-20, FLL-2024-07-17-22, IAD-2024-07-10-21, IAH-2024-06-28-20, JFK-2024-07-06-16, LAS-2024-07-17-17, LAS-2024-07-27-19, LGA-2024-06-27-19, LGA-2024-08-07-22, MCO-2024-07-23-16, MDW-2024-06-06-00, MDW-2024-08-25-19, PHL-2024-06-11-19, SAN-2024-07-09-22, SAN-2024-07-24-19, SEA-2024-06-03-20, SEA-2024-06-27-16, SFO-2024-08-19-23, TPA-2024-08-03-20.

- liquid/d1-ultra vs status quo on 18 feasible-status-quo waves: delay 763.828 min vs 767.378 min
- formula vs status quo on 18 feasible-status-quo waves: delay 763.728 min vs 767.378 min

Optimiser reference (n=50, 50 feasible): mean delay 577.548 min, 95% CI [494.207, 660.889]; no models, no negotiation.
- liquid/d1-ultra: delay 95% CI [488.108, 654.816]; gap to optimiser -6.086 min; share of optimiser gain over status quo 48.23% (n=14 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [479.084, 644.056]; gap to optimiser -15.978 min; share of optimiser gain over status quo 49.58% (n=14 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** formula (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- Formula selected: the best model arm, liquid/d1-ultra, is worse than formula on delay (571.462 > 561.57 min) and not better on pax protected.
- No arm met error < 2%, p95 < 1000 ms and consensus >= 90%: liquid/d1-ultra p95 1194.0 ms / error 0.0% / consensus 92.0%. Picked best outcome among eligible model arms: liquid/d1-ultra.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- liquid/d1-ultra p95 all decisions incl. fallbacks: 1194.0 ms
- liquid/d1-ultra missed p95 < 1000 ms: 1194.0 ms
- liquid/d1-ultra outcomes partly from solver fallback on 4/50 waves
- Mean delay: liquid/d1-ultra 571.462 min vs formula 561.57 min vs status quo 560.05 min.
- Outage test ran with the best eligible model arm liquid/d1-ultra (overall winner is formula).

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"liquid/d1-ultra-20261015": 202}

## Ablation: safety engine off

54.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `SAN-2024-07-09-22` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `DEN-2024-07-03-19` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `ATL-2024-07-24-19` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `BNA-2024-07-15-18` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `SAN-2024-07-09-22` (formula): no consensus: deterministic solver fallback
- `ATL-2024-07-24-19` (formula): no consensus: deterministic solver fallback
- `BNA-2024-07-15-18` (formula): no consensus: deterministic solver fallback

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- liquid/d1-ultra: 9,234 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- liquid/d1-ultra: none
- formula: none

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
| v4-airlines | baseline | 560.05 | 11.68 | 0.0 | 12.671 | 19.528 | 3.442 | n/a | n/a | 18 |
| v4-airlines | liquid/d1-ultra | 571.462 | 244.88 | 2.17 | 1.439 | 19.172 | 0.176 | 92.0 | 0 | 50 |
| v4-airlines | formula | 561.57 | 194.08 | 2.13 | 1.143 | 19.606 | 0.864 | 94.0 | 0 | 50 |

## Attribution

Engine v3-traffic to v4-airlines. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Note: wave sets differ (0 shared of 50 previous / 50 current): the physics delta mixes waves.
Physics (status quo under the new engine minus under the old): delay_min +233.36, fuel_kg +10.32, lap_share_pct +0, taxi_wait_min +11.588, pax_protected_pct -17.392, overtime_min +1.694. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- liquid/d1-ultra: delay_min +11.412, fuel_kg +233.2, lap_share_pct +2.17, taxi_wait_min -11.232, pax_protected_pct -0.356, overtime_min -3.266
- formula: delay_min +1.52, fuel_kg +182.4, lap_share_pct +2.13, taxi_wait_min -11.528, pax_protected_pct +0.078, overtime_min -2.578

Headline: under engine v4-airlines, negotiation (formula) adds 1.52 min of mean delay against the status quo.

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
