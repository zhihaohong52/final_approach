# Benchmark (50 waves, seed 7, source flights2015)
Generated 2026-11-03T13:01:27+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 335.474 | 1.36 | 34.098 | 1.856 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 337.62 | 80.8 | 35.156 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 98.0 | 1.36 | 2.0 | 0 | 344.85 | 218.24 | 33.564 | 1.88 | 64.0 | 0.0087 | 457.0 | 597.2 | 0.0 |
| liquid/d1-ultra | 50/50 | 96.0 | 1.36 | 4.0 | 0 | 344.902 | 221.68 | 33.518 | 1.766 | 74.0 | 0.0026 | 573.0 | 1215.3 | 0.0 |
| baseten/elephant-1 | 50/50 | 98.0 | 1.46 | 2.0 | 0 | 344.278 | 199.76 | 33.564 | 1.554 | 66.0 | 0.031 | 513.0 | 599.9 | 0.0 |
| formula | 50/50 | 98.0 | 1.38 | 2.0 | 0 | 344.59 | 221.2 | 34.48 | 1.728 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 17 waves: F2015-102056, F2015-105396, F2015-112396, F2015-124231, F2015-144568, F2015-165543, F2015-208678, F2015-208692, F2015-210010, F2015-246479, F2015-254935, F2015-282726, F2015-298859, F2015-335957, F2015-343919, F2015-60842, F2015-63104.

- typesafe/jev-1.14 vs status quo on 33 feasible-status-quo waves: delay 329.179 min vs 330.93 min
- liquid/d1-ultra vs status quo on 33 feasible-status-quo waves: delay 329.045 min vs 330.93 min
- baseten/elephant-1 vs status quo on 33 feasible-status-quo waves: delay 328.879 min vs 330.93 min
- formula vs status quo on 33 feasible-status-quo waves: delay 328.897 min vs 330.93 min

Optimiser reference (n=50, 50 feasible): mean delay 337.62 min, 95% CI [291.166, 384.074]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [298.28, 391.42]; gap to optimiser 7.23 min; share of optimiser gain over status quo 23.53% (n=27 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [298.327, 391.477]; gap to optimiser 7.282 min; share of optimiser gain over status quo 24.3% (n=27 waves with feasible status quo beaten by the optimiser).
- baseten/elephant-1: delay 95% CI [297.746, 390.81]; gap to optimiser 6.658 min; share of optimiser gain over status quo 23.21% (n=27 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [298.173, 391.007]; gap to optimiser 6.97 min; share of optimiser gain over status quo 25.87% (n=27 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** baseten/elephant-1 (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- baseten/elephant-1 meets all three thresholds (error, p95, consensus) and has the best outcome among qualifying arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 597.2 ms
- typesafe/jev-1.14 outcomes partly from solver fallback on 1/50 waves
- liquid/d1-ultra p95 all decisions incl. fallbacks: 1215.3 ms
- liquid/d1-ultra missed p95 < 1000 ms: 1215.3 ms
- liquid/d1-ultra outcomes partly from solver fallback on 2/50 waves
- baseten/elephant-1 p95 all decisions incl. fallbacks: 599.9 ms
- baseten/elephant-1 outcomes partly from solver fallback on 1/50 waves
- Mean delay: baseten/elephant-1 344.278 min vs formula 344.59 min vs status quo 335.474 min.

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"baseten/elephant-1-20261021": 182}

## Ablation: safety engine off

34.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-144520` (typesafe/jev-1.14): regression vs status quo: delay 641.2 > 640.0 min
- `F2015-302254` (typesafe/jev-1.14): regression vs status quo: delay 314.4 > 312.0 min
- `F2015-115532` (typesafe/jev-1.14): regression vs status quo: delay 248.3 > 243.0 min
- `F2015-169005` (typesafe/jev-1.14): regression vs status quo: delay 217.8 > 217.0 min
- `F2015-161169` (typesafe/jev-1.14): regression vs status quo: delay 287.3 > 287.0 min
- `F2015-291336` (typesafe/jev-1.14): regression vs status quo: delay 188.3 > 186.0 min
- `F2015-205196` (typesafe/jev-1.14): regression vs status quo: delay 309.3 > 304.0 min
- `F2015-131437` (typesafe/jev-1.14): regression vs status quo: delay 482.2 > 480.0 min
- `F2015-163396` (typesafe/jev-1.14): regression vs status quo: delay 236.6 > 231.0 min
- `F2015-267904` (typesafe/jev-1.14): regression vs status quo: delay 353.4 > 348.0 min
- `F2015-273632` (typesafe/jev-1.14): regression vs status quo: delay 227.0 > 217.0 min
- `F2015-102056` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-144520` (liquid/d1-ultra): regression vs status quo: delay 641.2 > 640.0 min
- `F2015-105396` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-209722` (liquid/d1-ultra): regression vs status quo: delay 154.0 > 148.0 min
- `F2015-302254` (liquid/d1-ultra): regression vs status quo: delay 312.4 > 312.0 min
- `F2015-115532` (liquid/d1-ultra): regression vs status quo: delay 248.3 > 243.0 min
- `F2015-169005` (liquid/d1-ultra): regression vs status quo: delay 217.8 > 217.0 min
- `F2015-161169` (liquid/d1-ultra): regression vs status quo: delay 287.3 > 287.0 min
- `F2015-291336` (liquid/d1-ultra): regression vs status quo: delay 188.3 > 186.0 min
- `F2015-205196` (liquid/d1-ultra): regression vs status quo: delay 309.3 > 304.0 min
- `F2015-131437` (liquid/d1-ultra): regression vs status quo: delay 480.7 > 480.0 min
- `F2015-163396` (liquid/d1-ultra): regression vs status quo: delay 236.6 > 231.0 min
- `F2015-267904` (liquid/d1-ultra): regression vs status quo: delay 353.4 > 348.0 min
- `F2015-102056` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-273632` (liquid/d1-ultra): regression vs status quo: delay 227.0 > 217.0 min
- `F2015-144520` (baseten/elephant-1): regression vs status quo: delay 641.2 > 640.0 min
- `F2015-169005` (baseten/elephant-1): regression vs status quo: delay 217.8 > 217.0 min
- `F2015-161169` (baseten/elephant-1): regression vs status quo: delay 287.3 > 287.0 min
- `F2015-291336` (baseten/elephant-1): regression vs status quo: delay 188.3 > 186.0 min
- `F2015-205196` (baseten/elephant-1): regression vs status quo: delay 306.1 > 304.0 min
- `F2015-131437` (baseten/elephant-1): regression vs status quo: delay 480.7 > 480.0 min
- `F2015-163396` (baseten/elephant-1): regression vs status quo: delay 236.6 > 231.0 min
- `F2015-267904` (baseten/elephant-1): regression vs status quo: delay 353.4 > 348.0 min
- `F2015-273632` (baseten/elephant-1): regression vs status quo: delay 227.0 > 217.0 min
- `F2015-102056` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-144520` (formula): regression vs status quo: delay 641.2 > 640.0 min
- `F2015-53108` (formula): regression vs status quo: delay 216.7 > 216.0 min
- `F2015-302254` (formula): regression vs status quo: delay 312.4 > 312.0 min
- `F2015-115532` (formula): regression vs status quo: delay 248.3 > 243.0 min
- `F2015-169005` (formula): regression vs status quo: delay 217.8 > 217.0 min
- `F2015-161169` (formula): regression vs status quo: delay 287.3 > 287.0 min
- `F2015-291336` (formula): regression vs status quo: delay 188.3 > 186.0 min
- `F2015-205196` (formula): regression vs status quo: delay 309.3 > 304.0 min
- `F2015-131437` (formula): regression vs status quo: delay 480.7 > 480.0 min
- `F2015-163396` (formula): regression vs status quo: delay 236.6 > 231.0 min
- `F2015-267904` (formula): regression vs status quo: delay 353.4 > 348.0 min
- `F2015-273632` (formula): regression vs status quo: delay 227.0 > 217.0 min
- `F2015-102056` (formula): no consensus: deterministic solver fallback

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- typesafe/jev-1.14: 6,409 chars (within budget)
- liquid/d1-ultra: 6,396 chars (within budget)
- baseten/elephant-1: 6,396 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- typesafe/jev-1.14: PLT-FUEL-RESERVE 7
- liquid/d1-ultra: PLT-FUEL-RESERVE 6
- baseten/elephant-1: PLT-FUEL-RESERVE 5
- formula: PLT-FUEL-RESERVE 9

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

## Attribution

Engine v3-air to v3-time. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Physics (status quo under the new engine minus under the old): delay_min -7.684, fuel_kg +0.64, lap_share_pct +0, taxi_wait_min +0, pax_protected_pct +7.56, overtime_min +0.096. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- typesafe/jev-1.14: delay_min +9.376, fuel_kg +216.88, lap_share_pct +4.08, taxi_wait_min +0, pax_protected_pct -0.534, overtime_min +0.024
- liquid/d1-ultra: delay_min +9.428, fuel_kg +220.32, lap_share_pct +4.17, taxi_wait_min +0, pax_protected_pct -0.58, overtime_min -0.09
- baseten/elephant-1: delay_min +8.804, fuel_kg +198.4, lap_share_pct +4.08, taxi_wait_min +0, pax_protected_pct -0.534, overtime_min -0.302
- formula: delay_min +9.116, fuel_kg +219.84, lap_share_pct +4.08, taxi_wait_min +0, pax_protected_pct +0.382, overtime_min -0.128

Headline: under engine v3-time, negotiation (baseten/elephant-1) adds 8.804 min of mean delay against the status quo.

Regressions (53):

Note: this version is a definition change, so the "physics" cause below means the new definition moved the status quo, not flight physics.

- `F2015-110532` (typesafe/jev-1.14) delay_min 494.9 to 502.0; negotiation
- `F2015-112396` (typesafe/jev-1.14) delay_min 384.6 to 390.6; physics
- `F2015-124231` (typesafe/jev-1.14) delay_min 360.1 to 369.1; physics
- `F2015-15580` (typesafe/jev-1.14) delay_min 383.0 to 393.0; physics
- `F2015-165543` (typesafe/jev-1.14) delay_min 217.1 to 223.1; negotiation
- `F2015-169005` (typesafe/jev-1.14) delay_min 215.8 to 217.8; physics
- `F2015-209722` (typesafe/jev-1.14) delay_min 146.0 to 148.0; physics
- `F2015-210010` (typesafe/jev-1.14) delay_min 429.9 to 434.9; physics
- `F2015-254935` (typesafe/jev-1.14) delay_min 319.5 to 321.5; physics
- `F2015-272020` (typesafe/jev-1.14) delay_min 442.6 to 442.8; negotiation
- `F2015-324426` (typesafe/jev-1.14) delay_min 86.2 to 87.2; physics
- `F2015-63104` (typesafe/jev-1.14) delay_min 396.0 to 396.2; physics
- `F2015-63104` (typesafe/jev-1.14) fuel_kg 680.0 to 728.0; negotiation
- `F2015-105396` (liquid/d1-ultra) delay_min 146.1 to 153.0; negotiation
- `F2015-110532` (liquid/d1-ultra) delay_min 497.0 to 502.0; physics
- `F2015-112396` (liquid/d1-ultra) delay_min 384.6 to 390.6; physics
- `F2015-124231` (liquid/d1-ultra) delay_min 363.2 to 372.2; physics
- `F2015-15580` (liquid/d1-ultra) delay_min 383.0 to 393.0; physics
- `F2015-165543` (liquid/d1-ultra) delay_min 212.3 to 219.1; negotiation
- `F2015-165543` (liquid/d1-ultra) fuel_kg 276.0 to 468.0; negotiation
- ... and 33 more in benchmark.json

## v3-time: definition change, not physics

- The v3-air → v3-time delta below is a definition change, not a change in the physics of the airport. In v3-air the BTS scheduled arrival was treated as the wheels-on time. In v3-time it is a gate (in-block) time, and the earliest possible landing is the wheels-on time (scheduled arrival + arrival delay - taxi-in). Delay, pax and fuel are therefore measured against a different reference, on the same 50 waves and seed 7.
- Status quo (baseline) moved by the definition alone, with nothing renegotiated: delay 343.2 → 335.5 min, pax protected 26.5 → 34.1 %, fuel 0.72 → 1.36 kg, overtime 1.76 → 1.86 min, feasible waves 34 → 33. That is the physics_delta in the Attribution section; read it as the definition effect.
- Delay min (v3-air → v3-time): formula 352.3 → 344.6, jev-1.14 352.3 → 344.9, d1-ultra 352.3 → 344.9, elephant-1 351.7 → 344.3.
- Fuel kg: formula 235.8 → 221.2, jev-1.14 230.5 → 218.2, d1-ultra 224.2 → 221.7, elephant-1 211.8 → 199.8.
- Pax protected %: formula 26.0 → 34.5, jev-1.14 25.7 → 33.6, d1-ultra 25.7 → 33.5, elephant-1 25.7 → 33.6.
- Overtime min: formula 1.9 → 1.7, jev-1.14 2.0 → 1.9, d1-ultra 1.8 → 1.8, elephant-1 1.7 → 1.6.
- Consensus rate is unchanged in formula, jev-1.14 and elephant-1 (98 %) and fell from 98 % to 96 % in d1-ultra. Lap share: formula 6.12 → 4.08 %, d1-ultra 4.08 → 4.17 %, the others unchanged at 4.08 %.
- Failures: 59 in total in v3-air → 49 in v3-time (formula 13, jev-1.14 12, d1-ultra 14, elephant-1 10); 44 are regressions vs the status quo and 5 are no-consensus fallbacks. The regression list is dominated by 'physics' because the baseline moved with the definition; compare negotiation_delta, not absolute values, across versions.
- Rule violations in agreed plans are 0 in every arm.
- Formula lap share fell 6.12 -> 4.08 %: that is a negotiation outcome (a different set of plans was agreed on the new time base), not part of the definition change.

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 50 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
- The v3-air → v3-time comparison changes what is measured, so absolute delay, pax and fuel are not comparable across the two versions; use the baseline row of each version as the reference.
- Gate-relief laps (airborne holds that exist only to clear GND-GATE-FREE, 9 across arms in v3-air; see eval/archive/engine-v3-air/BENCHMARK.md) were not re-counted on the v3-time plans: per-wave results do not store laps, so whether they persist is unmeasured.
