# Autonomous SG 2026: Airbus Challenge #2 notes

Source: shared Drive folder `AUTONOMOUS SG 2026`. The Challenge #2 brief and the track's judging rubric are in `docs/airbus_challenge2.pdf`.
Large CSVs are not committed. Run `scripts/fetch_data.sh` to download them.

## Airbus track rubric (what we are scored on)

| Criterion | Weight | What wins |
|---|---|---|
| Technical soundness | 25% | Working end to end. **LLMs for reading/explaining; solvers, rules, stats for constraints and scheduling.** An LLM doing legality checks or arithmetic is marked down. |
| Problem realism | 20% | A named user (e.g. a duty manager), what they do today, and what it costs them. |
| Honest evaluation | 20% | A baseline, metrics, and at least one visible failure or refusal, with the reason. |
| Trust and traceability | 20% | Every output traces to a signal or rule. Visible uncertainty. A human stays in control. |
| Impact | 10% | A before/after number against the baseline. |
| Time discipline | 5% | Submit early. Pitch in 3 minutes or less. |

## Challenge 2 deep dive

**Data gap.** The brief says to pick a row with `WEATHER_DELAY > 15` or `LATE_AIRCRAFT_DELAY > 20` from the 2015 DOT data. The Drive folder has only the `airlines.csv` and `airports.csv` lookup tables. The main `flights.csv` (about 5.8M rows, 565 MB) is not there. The datasets link in the PDF (`13h0JZ...`) is not public. Options:
- Kaggle `usdot/flight-delays` contains `flights.csv` (these two lookup files come from the same dataset).
- Ask the organisers for the file.

**Build that scores well under the rubric:**
- Three agents, each with its own prompt, its own private constraints, and its own objective. Never one prompt playing all three roles.
- A typed message schema (e.g. pydantic): `Proposal{slot, runway, gate, eta, fuel_burn_kg, ...}`, `Accept`, `Reject{violated_constraint}`, `CounterProposal`.
- **A deterministic constraint engine** that checks every proposal before an agent can accept it: wake separation minima, minimum fuel reserve, gate free and compatible with the aircraft size, crew duty limits. The LLM proposes, the rules decide. This is the LLM / non-LLM split the rubric asks for.
- A hard limit of 3 rounds. If there is no consensus, a deterministic fallback solver (e.g. first-feasible or a small cost-weighted search) picks the answer and the log records the reason.
- A consensus log: every message, every constraint check, and the final agreement, with timestamps.
- **Evaluation:** run on N sampled disrupted flights, not just one scripted row. Report the consensus rate, the average number of turns, the share of runs where the fallback fired, constraint violations (should be 0), and delay/fuel against a naive "hold until slot" baseline. Show one failure case.
- **User:** the airline duty manager / hub control centre who handles these calls by phone today.
