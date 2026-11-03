# Benchmark (50 waves, seed 7, source flights2015)
Engine version: v2 (frozen)
Generated 2026-11-02T09:08:32+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 343.158 | 0.72 | 26.538 | 1.76 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 349.626 | 64.24 | 26.118 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 98.0 | 1.32 | 2.0 | 0 | 351.192 | 107.84 | 25.798 | 1.2 | 66.0 | 0.0063 | 446.0 | 598.0 | 0.0 |
| liquid/d1-ultra | 50/50 | 96.0 | 1.36 | 4.0 | 0 | 351.438 | 111.28 | 25.502 | 1.2 | 84.0 | 0.0016 | 568.0 | 668.1 | 0.0 |
| baseten/elephant-1 | 50/50 | 98.0 | 1.34 | 2.0 | 0 | 351.438 | 111.28 | 25.798 | 1.2 | 74.0 | 0.0155 | 493.0 | 579.0 | 0.0 |
| formula | 50/50 | 98.0 | 1.32 | 2.0 | 0 | 351.198 | 108.08 | 25.502 | 1.2 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 16 waves: F2015-102056, F2015-105396, F2015-112396, F2015-124231, F2015-144568, F2015-165543, F2015-208678, F2015-208692, F2015-210010, F2015-246479, F2015-254935, F2015-282726, F2015-298859, F2015-335957, F2015-343919, F2015-63104.

- typesafe/jev-1.14 vs status quo on 34 feasible-status-quo waves: delay 336.794 min vs 338.753 min
- liquid/d1-ultra vs status quo on 34 feasible-status-quo waves: delay 336.735 min vs 338.753 min
- baseten/elephant-1 vs status quo on 34 feasible-status-quo waves: delay 336.853 min vs 338.753 min
- formula vs status quo on 34 feasible-status-quo waves: delay 336.5 min vs 338.753 min

Optimiser reference (n=50, 50 feasible): mean delay 349.626 min, 95% CI [302.856, 396.396]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [304.811, 397.573]; gap to optimiser 1.566 min; share of optimiser gain over status quo 95.04% (n=12 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [305.081, 397.795]; gap to optimiser 1.812 min; share of optimiser gain over status quo 95.04% (n=12 waves with feasible status quo beaten by the optimiser).
- baseten/elephant-1: delay 95% CI [305.119, 397.757]; gap to optimiser 1.812 min; share of optimiser gain over status quo 95.04% (n=12 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [304.82, 397.576]; gap to optimiser 1.572 min; share of optimiser gain over status quo 104.96% (n=12 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** typesafe/jev-1.14

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- typesafe/jev-1.14 meets all three thresholds (error, p95, consensus) and has the best outcome among qualifying arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 598.0 ms
- typesafe/jev-1.14 outcomes partly from solver fallback on 1/50 waves
- liquid/d1-ultra p95 all decisions incl. fallbacks: 668.1 ms
- liquid/d1-ultra outcomes partly from solver fallback on 2/50 waves
- baseten/elephant-1 p95 all decisions incl. fallbacks: 579.0 ms
- baseten/elephant-1 outcomes partly from solver fallback on 1/50 waves
- Mean delay: typesafe/jev-1.14 351.192 min vs formula 351.198 min vs status quo 343.158 min.

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"typesafe/jev-1.14-20261014": 166}

## Ablation: safety engine off

32.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-302254` (typesafe/jev-1.14): regression vs status quo: delay 346.0 > 344.0 min
- `F2015-274783` (typesafe/jev-1.14): regression vs status quo: delay 243.0 > 235.0 min
- `F2015-102056` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-105396` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-274783` (liquid/d1-ultra): regression vs status quo: delay 243.0 > 235.0 min
- `F2015-102056` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-274783` (baseten/elephant-1): regression vs status quo: delay 247.0 > 235.0 min
- `F2015-102056` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-274783` (formula): regression vs status quo: delay 243.0 > 235.0 min
- `F2015-102056` (formula): no consensus: deterministic solver fallback

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 50 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
