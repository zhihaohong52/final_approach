# Benchmark (50 waves, seed 7, source flights2015)
Generated 2026-11-03T12:15:46+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 343.158 | 0.72 | 26.538 | 1.76 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 345.006 | 77.36 | 27.526 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 98.0 | 1.34 | 2.0 | 0 | 352.328 | 230.48 | 25.726 | 1.966 | 64.0 | 0.0081 | 430.0 | 509.4 | 0.0 |
| liquid/d1-ultra | 50/50 | 98.0 | 1.36 | 2.0 | 0 | 352.256 | 224.16 | 25.726 | 1.84 | 80.0 | 0.0025 | 576.5 | 910.2 | 0.0 |
| baseten/elephant-1 | 50/50 | 98.0 | 1.44 | 2.0 | 0 | 351.744 | 211.84 | 25.726 | 1.746 | 64.0 | 0.0285 | 495.0 | 570.0 | 0.0 |
| formula | 50/50 | 98.0 | 1.36 | 2.0 | 0 | 352.302 | 235.76 | 26.0 | 1.906 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 16 waves: F2015-102056, F2015-105396, F2015-112396, F2015-124231, F2015-144568, F2015-165543, F2015-208678, F2015-208692, F2015-210010, F2015-246479, F2015-254935, F2015-282726, F2015-298859, F2015-335957, F2015-343919, F2015-63104.

- typesafe/jev-1.14 vs status quo on 34 feasible-status-quo waves: delay 336.771 min vs 338.753 min
- liquid/d1-ultra vs status quo on 34 feasible-status-quo waves: delay 336.815 min vs 338.753 min
- baseten/elephant-1 vs status quo on 34 feasible-status-quo waves: delay 336.535 min vs 338.753 min
- formula vs status quo on 34 feasible-status-quo waves: delay 336.741 min vs 338.753 min

Optimiser reference (n=50, 50 feasible): mean delay 345.006 min, 95% CI [298.578, 391.434]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [306.029, 398.627]; gap to optimiser 7.322 min; share of optimiser gain over status quo 26.17% (n=28 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [305.817, 398.695]; gap to optimiser 7.25 min; share of optimiser gain over status quo 25.07% (n=28 waves with feasible status quo beaten by the optimiser).
- baseten/elephant-1: delay 95% CI [305.457, 398.031]; gap to optimiser 6.738 min; share of optimiser gain over status quo 26.02% (n=28 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [306.17, 398.434]; gap to optimiser 7.296 min; share of optimiser gain over status quo 25.86% (n=28 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** baseten/elephant-1 (n = 50 waves: indicative, not statistically significant)

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- baseten/elephant-1 meets all three thresholds (error, p95, consensus) and has the best outcome among qualifying arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 509.4 ms
- typesafe/jev-1.14 outcomes partly from solver fallback on 1/50 waves
- liquid/d1-ultra p95 all decisions incl. fallbacks: 910.2 ms
- liquid/d1-ultra outcomes partly from solver fallback on 1/50 waves
- baseten/elephant-1 p95 all decisions incl. fallbacks: 570.0 ms
- baseten/elephant-1 outcomes partly from solver fallback on 1/50 waves
- Mean delay: baseten/elephant-1 351.744 min vs formula 352.302 min vs status quo 343.158 min.

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"baseten/elephant-1-20261021": 179}

## Ablation: safety engine off

32.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-144520` (typesafe/jev-1.14): regression vs status quo: delay 643.2 > 642.0 min
- `F2015-60842` (typesafe/jev-1.14): regression vs status quo: delay 295.8 > 295.0 min
- `F2015-302254` (typesafe/jev-1.14): regression vs status quo: delay 346.4 > 344.0 min
- `F2015-115532` (typesafe/jev-1.14): regression vs status quo: delay 251.3 > 246.0 min
- `F2015-161169` (typesafe/jev-1.14): regression vs status quo: delay 287.3 > 287.0 min
- `F2015-169005` (typesafe/jev-1.14): regression vs status quo: delay 215.8 > 215.0 min
- `F2015-209820` (typesafe/jev-1.14): regression vs status quo: delay 300.2 > 300.0 min
- `F2015-291336` (typesafe/jev-1.14): regression vs status quo: delay 189.3 > 187.0 min
- `F2015-205196` (typesafe/jev-1.14): regression vs status quo: delay 313.3 > 308.0 min
- `F2015-131437` (typesafe/jev-1.14): regression vs status quo: delay 488.2 > 486.0 min
- `F2015-163396` (typesafe/jev-1.14): regression vs status quo: delay 243.6 > 238.0 min
- `F2015-334031` (typesafe/jev-1.14): regression vs status quo: delay 188.2 > 188.0 min
- `F2015-267904` (typesafe/jev-1.14): regression vs status quo: delay 379.4 > 374.0 min
- `F2015-273632` (typesafe/jev-1.14): regression vs status quo: delay 236.0 > 226.0 min
- `F2015-102056` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-144520` (liquid/d1-ultra): regression vs status quo: delay 643.2 > 642.0 min
- `F2015-60842` (liquid/d1-ultra): regression vs status quo: delay 295.8 > 295.0 min
- `F2015-115532` (liquid/d1-ultra): regression vs status quo: delay 251.3 > 246.0 min
- `F2015-302254` (liquid/d1-ultra): regression vs status quo: delay 344.4 > 344.0 min
- `F2015-169005` (liquid/d1-ultra): regression vs status quo: delay 215.8 > 215.0 min
- `F2015-161169` (liquid/d1-ultra): regression vs status quo: delay 287.3 > 287.0 min
- `F2015-209820` (liquid/d1-ultra): regression vs status quo: delay 300.2 > 300.0 min
- `F2015-205196` (liquid/d1-ultra): regression vs status quo: delay 313.3 > 308.0 min
- `F2015-291336` (liquid/d1-ultra): regression vs status quo: delay 189.3 > 187.0 min
- `F2015-131437` (liquid/d1-ultra): regression vs status quo: delay 486.7 > 486.0 min
- `F2015-163396` (liquid/d1-ultra): regression vs status quo: delay 243.6 > 238.0 min
- `F2015-334031` (liquid/d1-ultra): regression vs status quo: delay 188.2 > 188.0 min
- `F2015-267904` (liquid/d1-ultra): regression vs status quo: delay 379.4 > 374.0 min
- `F2015-102056` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-273632` (liquid/d1-ultra): regression vs status quo: delay 236.0 > 226.0 min
- `F2015-144520` (baseten/elephant-1): regression vs status quo: delay 643.2 > 642.0 min
- `F2015-60842` (baseten/elephant-1): regression vs status quo: delay 295.8 > 295.0 min
- `F2015-302254` (baseten/elephant-1): regression vs status quo: delay 346.4 > 344.0 min
- `F2015-169005` (baseten/elephant-1): regression vs status quo: delay 215.8 > 215.0 min
- `F2015-161169` (baseten/elephant-1): regression vs status quo: delay 287.3 > 287.0 min
- `F2015-209820` (baseten/elephant-1): regression vs status quo: delay 300.2 > 300.0 min
- `F2015-291336` (baseten/elephant-1): regression vs status quo: delay 189.3 > 187.0 min
- `F2015-205196` (baseten/elephant-1): regression vs status quo: delay 310.1 > 308.0 min
- `F2015-131437` (baseten/elephant-1): regression vs status quo: delay 486.7 > 486.0 min
- `F2015-163396` (baseten/elephant-1): regression vs status quo: delay 243.6 > 238.0 min
- `F2015-334031` (baseten/elephant-1): regression vs status quo: delay 188.2 > 188.0 min
- `F2015-267904` (baseten/elephant-1): regression vs status quo: delay 379.4 > 374.0 min
- `F2015-273632` (baseten/elephant-1): regression vs status quo: delay 227.2 > 226.0 min
- `F2015-102056` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-144520` (formula): regression vs status quo: delay 643.2 > 642.0 min
- `F2015-60842` (formula): regression vs status quo: delay 295.8 > 295.0 min
- `F2015-302254` (formula): regression vs status quo: delay 344.4 > 344.0 min
- `F2015-115532` (formula): regression vs status quo: delay 251.3 > 246.0 min
- `F2015-53108` (formula): regression vs status quo: delay 277.7 > 277.0 min
- `F2015-169005` (formula): regression vs status quo: delay 215.8 > 215.0 min
- `F2015-161169` (formula): regression vs status quo: delay 287.3 > 287.0 min
- `F2015-291336` (formula): regression vs status quo: delay 189.3 > 187.0 min
- `F2015-205196` (formula): regression vs status quo: delay 313.3 > 308.0 min
- `F2015-131437` (formula): regression vs status quo: delay 486.7 > 486.0 min
- `F2015-163396` (formula): regression vs status quo: delay 243.6 > 238.0 min
- `F2015-334031` (formula): regression vs status quo: delay 188.2 > 188.0 min
- `F2015-267904` (formula): regression vs status quo: delay 379.4 > 374.0 min
- `F2015-273632` (formula): regression vs status quo: delay 236.0 > 226.0 min
- `F2015-102056` (formula): no consensus: deterministic solver fallback

## Prompt budget (largest prompt of any kind)

Largest prompt sent by any Decider call (choice, score, noul, noul_batch: state + instructions + options) per arm, against the 32,000-char (8k-token) budget:

- typesafe/jev-1.14: 6,407 chars (within budget)
- liquid/d1-ultra: 6,394 chars (within budget)
- baseten/elephant-1: 6,394 chars (within budget)
- formula: 0 chars (within budget)

## Engine veto counts

- typesafe/jev-1.14: PLT-FUEL-RESERVE 7
- liquid/d1-ultra: PLT-FUEL-RESERVE 6
- baseten/elephant-1: PLT-FUEL-RESERVE 5
- formula: PLT-FUEL-RESERVE 8

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

## Attribution

Engine v2 to v3-air. Better is: delay_min lower, fuel_kg lower, lap_share_pct lower, taxi_wait_min lower, pax_protected_pct higher, overtime_min lower.

Physics (status quo under the new engine minus under the old): delay_min +0, fuel_kg +0, pax_protected_pct +0, overtime_min +0. The status quo is never renegotiated, so this only re-scores the unchanged status-quo plans; engine effects on negotiated plans (e.g. routed touchdown times) land in the Negotiation deltas below.

Negotiation (arm minus status quo, same engine); headline claims come from these deltas:

- typesafe/jev-1.14: delay_min +9.17, fuel_kg +229.76, lap_share_pct +4.08, taxi_wait_min +0, pax_protected_pct -0.812, overtime_min +0.206
- liquid/d1-ultra: delay_min +9.098, fuel_kg +223.44, lap_share_pct +4.08, taxi_wait_min +0, pax_protected_pct -0.812, overtime_min +0.08
- baseten/elephant-1: delay_min +8.586, fuel_kg +211.12, lap_share_pct +4.08, taxi_wait_min +0, pax_protected_pct -0.812, overtime_min -0.014
- formula: delay_min +9.144, fuel_kg +235.04, lap_share_pct +6.12, taxi_wait_min +0, pax_protected_pct -0.538, overtime_min +0.146

Headline: under engine v3-air, negotiation (baseten/elephant-1) adds 8.586 min of mean delay against the status quo.

No per-wave data in v2; regressions start next run.

Regressions vs v2 (0): empty by construction, not evidence of no regressions (v2 has no per-wave data to compare against).

- none

## v2 → v3-air notes

- Aggregate change vs v2, same 50 waves and seed (v2 → v3-air). Fuel kg: formula 108.1 → 235.8, jev-1.14 107.8 → 230.5, d1-ultra 111.3 → 224.2, elephant-1 111.3 → 211.8. Delay min: formula 351.2 → 352.3, jev-1.14 351.2 → 352.3, d1-ultra 351.4 → 352.3, elephant-1 351.4 → 351.7. Overtime min: 1.2 → 1.75-1.97 in every arm. Failures: 10 in total (2-3 per arm) → 59 (14-15 per arm), mostly small regressions vs the status quo (0.2-10 min of delay).
- Pax protected % is the one gain, and only in two arms: formula 25.50 → 26.00 and d1-ultra 25.50 → 25.73. jev-1.14 and elephant-1 fall slightly, 25.80 → 25.73.
- Formula fuel split vs v2 (+127.7 kg per wave), from a flight-by-flight rerun of the v2 engine on the same waves. +121.3 is route extra: v3 lands each flight at its routed earliest touchdown on the assigned runway, and the minutes past the nominal route are charged at holding burn (+84.3 on the same runway as in v2, +37.0 where the runway changed). +6.5 is holding (v3 laps 37.2 + vector 10.8 vs v2 hold 41.5). Speed reduction adds 0.
- Asymmetry: negative route extra (a route shorter than nominal) is floored at -hold, so with no hold a shorter route earns 0 kg credit while a longer one is charged in full. This biases fuel upward.
- Optimiser comparison: the arms' share of the optimiser's gain over the status quo fell from ~95% (v2) to ~26%, and the gap to the optimiser grew from ~1.6-1.8 min to ~6.7-7.3 min.

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 50 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
- 9 lapped holds across the four arms' agreed plans (3 formula, 2 per model arm; F2015-208692 F9771, F2015-335957 UA208, F2015-165543 DL2657) exist only to relieve GND-GATE-FREE: each flight could land at its earliest touchdown with no ATC/PLT failure. They are created by ATC swap_order / shift_slot / ground_delay (never a hold move, which legal_moves filters) and were already present in v2's plans as plain hold minutes.
