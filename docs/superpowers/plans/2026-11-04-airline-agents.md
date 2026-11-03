# Airline Agents and Pilot UNABLE ("Final Approach") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every negotiated flight has someone speaking for it. One `AIRLINE:<code>` agent per airline bargains over its own flights against a fairness floor (the status quo), and one formula-only `PILOT:<callsign>` per aircraft says UNABLE to instructions that break its pilot-side rules.

**Architecture:** A static airline-grouping table maps operating carriers to airline agents (`backend/wave/airlines.py`). `utility.py` gains the airline utility, and `fairness.py` holds the floor, in two variants (firm and relaxing) behind one engine setting. `moves.py` gains the `AIRLINE` authority and `swap_own`. `unable.py` turns pilot-side rule failures into UNABLE messages. In `graph.py` the `pilot` node becomes an `airlines` node that loops the airline agents, and consensus runs over N voters. The rule engine (`rules.check`) is unchanged and is still the only judge of legality.

**Tech Stack:** Python 3.12, LangGraph, FastAPI, FastMCP, pytest; React 18 + TypeScript, vitest; Postgres (neondb) for replays and benchmark rows.

**Spec:** `docs/superpowers/specs/2026-11-04-airline-agents-design.md` (binding). Also read `docs/api-contract.md` and the top-level design `docs/superpowers/specs/2026-10-31-disruption-negotiator-design.md`.

## Global Constraints

- Execution is subagent-driven: one git worktree per task, on branch `fp/a<N>` (task N), cut from `redesign/console` once the previous task has merged. Commit as you go. Never push, and never merge to `main`.
- Every commit message ends with exactly these two trailer lines:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01CZQD1apQx15EEBy5x3LDJF`
- Contract changes (`docs/api-contract.md`, `frontend/src/api/types.ts`, `frontend/src/api/samples/`) go in commits prefixed `contract:`. All of `frontend/` is in scope for this branch.
- Secrets live in `.env`. Never read it, print it or commit it. Only A10 (live benchmark) and A11 (live replay recording), plus the A13 Postgres smoke, symlink the main checkout's `.env` into their worktree, for that run only, and remove the link before committing. Those runs make paid model calls and write to neondb, both approved. No other task makes a model call or touches a database.
- Offline suite, which must pass at the end of every task (A10 → A11 exception below): `ATM_STORE=files ATM_WARM=0 python3 -m pytest -q -m "not live_db"`. Frontend: `cd frontend && npm test && npx tsc --noEmit && npm run build`.
- Known red between A10 and A11: `tests/test_geo_api.py::test_replays_are_current_engine_and_show_laps` fails on `engine_version` from A10's version bump until A11 re-records the replays. No other test may be red.
- Engine version: `ENGINE_VERSION = "v4-airlines"` (bumped in A10, not earlier). Add `"v4-airlines"` to `benchmark.VERSION_ORDER`.
- Performance: a cold ORD `build_wave` in a fresh process stays ≤ 1000 ms (about 800 ms today). Peak RSS on the Postgres store stays under Render's 512 MB (308 MB today).
- Safety unchanged: no rule, limit or `RULE_IDS` entry changes (spec §6). No agent can accept a plan that fails CHECK.
- Pilot agents make no model calls. Airline agents use the same `Decider` chain as ATC and GROUND (model → next model → formula), and every fallback is logged as `MODEL_FALLBACK`.
- Fairness setting: `ATM_FAIRNESS=firm|relaxing`, read by `fairness.current()`. `negotiate_wave(..., fairness=None)` overrides it. A10 picks the shipped default.
- Turns: still at most 3, then the deterministic fallback (`fallback.solve`, unchanged; it keeps `model.agent_ids` as its search space).
- Pinned names: voters are `utility.voters(wave)` = `["ATC", *sorted AIRLINE ids, "GROUND"]` (A2), and from A5 on `negotiators(wave)` returns the same list; pilots are `pilots(wave)` = `[f"PILOT:{cs}" for each wave flight, in wave order]`. `NegotiationResult.agents` lists the voters only, so "one AGENT_TURN per agent per turn" still holds. Pilots go in the new `pilots` field.

## Review Focus

1. **Carrier with no table row or no airlines.csv name** (e.g. `OH`, or a made-up `XX`): it becomes its own airline agent, named by its code; nothing raises. Test in A1.
2. **Airline with a single flight in the wave:** it is offered no `swap_own`, but it still moves, votes and objects. Test in A3.
3. **Status quo infeasible:** the floor is the best feasible plan seen so far, scored by that airline's utility. Before any feasible plan has been seen there is no floor: no floor-REJECT and no objection. Neither stress wave exercises this. Test in A2 (`floor_source`) and A5.
4. **Old 3-agent run or replay** (PILOT:<focus> AGENT_TURNs, no `pilots`/`airlines`/UNABLE): it still streams and renders in the new UI. Test in A8 (vitest on the old shape) and A7 (server passes an old replay through with `paths_unavailable`).
5. **Zero-fuel status-quo floor** (every flight in both stress waves): under firm, any flight above 0.5 kg is a breach. Under relaxing, the additive slack admits fuel from turn 1 (a multiplicative slack would never move a 0 floor). Test in A2.

---

### Task A1: Airline mapping and `Wave.airlines`

**Files:**
- Create: `backend/wave/airlines.py`, `tests/test_airlines.py`
- Modify: `backend/wave/model.py` (`Wave.to_dict`, around lines 153-165)

**Interfaces:**
- Produces:
  - `airline_of(carrier: str, hub_iata: str, year: int) -> tuple[str, str]`, returning (airline code, basis text).
  - `groups(wave: Wave) -> dict[str, list[str]]`: airline code → callsigns, in wave order, codes sorted.
  - `airline_name(code: str) -> str`: airlines.csv name, else the code.
  - `SOURCE: str`.
  - `Wave.to_dict()["airlines"] = [{"code", "name", "callsigns", "basis"}]`. It is derived, never stored, so the `waves` dataset needs no reseed.

- [ ] **Step 1: Check the data first (spec §3.1).** Confirm that no marketing carrier is available: `data/challenge2/flights.csv` has `AIRLINE` only, `scripts/fetch_bts.py` keeps `Reporting_Airline` only, and no raw PREZIPs are kept. Write the finding as the module docstring. Optionally, a later fetch of BTS's marketing-carrier table could supply this; do not fetch it here.
- [ ] **Step 2: Write failing tests** `test_regional_maps_to_major`, `test_unknown_carrier_is_own_airline`, `test_multi_partner_by_hub`, `test_groups_stress_waves`, `test_wave_dict_has_airlines`:
```python
assert airline_of("OH", "CLT", 2024)[0] == "AA" and airline_of("MQ", "DFW", 2015)[0] == "AA"
assert airline_of("9E", "ATL", 2024)[0] == "DL"
assert airline_of("XX", "ATL", 2024) == ("XX", "no grouping row: operating carrier is its own airline")
assert airline_of("EV", "ATL", 2015)[0] == "DL" and airline_of("EV", "ORD", 2015)[0] == "EV"   # ORD has no single major
w = build.build_wave("CLT-2024-07-29-19"); assert list(groups(w)) == ["AA"] and len(groups(w)["AA"]) == 5
assert list(groups(build.build_wave("ATL-2024-07-18-21"))) == ["DL"]
d = make_wave().to_dict(); assert [a["code"] for a in d["airlines"]] == ["AA", "DL", "UA"]
```
- [ ] **Step 3: Run them.** `ATM_STORE=files python3 -m pytest tests/test_airlines.py -q`. Expected: FAIL (no module).
- [ ] **Step 4: Implement `airlines.py`** with two data tables, each row carrying a `basis` string:
  - `SINGLE`, for one-partner carriers: at least `MQ→AA`, `OH→AA` and `9E→DL`, plus any others found, each with the years it held.
  - `MULTI`, for carriers with several partners (`EV`, `OO`, `YX`, …): the set of partner majors per year.
  - `HUB_MAJOR`: `ATL DL, CLT AA, DFW AA, MIA AA, DEN UA` (ORD maps to nothing, since it is a hub for both AA and UA).

  The rule is: if a `SINGLE` row covers the year, use its major. Otherwise, if the hub's major is one of the carrier's partners that year, use the hub's major. Otherwise the carrier is its own airline. `hub_iata` is `wave.airport.iata` and `year` is the year of `window_start`. `SOURCE` states that this is a static table and cites its basis. `groups` reads `f.carrier`.
- [ ] **Step 5: Run the tests and the offline suite.** Expected: PASS.
- [ ] **Step 6: Commit** `contract: Wave.airlines derived from an operating-to-airline grouping table` (a new response field; A7 documents it).

### Task A2: Airline utility and fairness floor (both variants)

**Files:**
- Create: `backend/wave/fairness.py`, `tests/test_fairness.py`
- Modify: `backend/wave/utility.py` (`voters`, `pilots`, `agent_terms`)

**Interfaces:**
- Consumes: `airlines.groups`.
- Produces:
  - `utility.W_AIRLINE = {"fuel": 0.30, "delay": 0.25, "connections": 0.25, "next_departure": 0.10, "divert": 0.10}`.
  - `utility.FOCUS_WEIGHT = 1.0`: no extra weight for the disrupted flight (spec §7). The floor already protects each flight.
  - `utility.voters(wave) -> list[str]` and `utility.pilots(wave) -> list[str]`. `negotiators` keeps its 3-agent body until A5, so the graph keeps working in A2-A4.
  - `agent_terms(wave, plan, "AIRLINE:<code>")`: the weighted mean over its flights of per-flight terms. Fuel, delay, connections and divert are the PILOT formulas; `next_departure` is `next_dep_delay_min / NEXT_DEP_CAP_MIN`. The `PILOT:` branch stays, because the fallback and MCP use it.
  - `fairness.VARIANTS = ("firm", "relaxing")`, `FAIRNESS_DEFAULT = "firm"` (until A10), `current() -> str`.
  - `slack(variant: str, rnd: int) -> float`: 0 for firm; `1 - (0.9 - 0.12*(rnd-1))` for relaxing, i.e. 0.10, 0.22 and 0.34 in turns 1, 2 and 3.
  - `breaches(wave, plan, code, floor: JointPlan | None, variant, rnd) -> list[dict]`. Each entry is `{"callsign", "metric": "delay_min"|"fuel_kg", "floor", "value", "allowed"}`. `allowed = floor + slack*cap + eps`, where:
    - for delay, cap = `DELAY_CAP_MIN` and eps = 0.05;
    - for fuel, cap = `max(extra_min, 1) * hold_burn`, the PILOT fuel budget, and eps = 0.5.
  - `floor_source(wave, status_quo: JointPlan, sq_feasible: bool, best_seen: JointPlan | None) -> JointPlan | None`.

- [ ] **Step 1: Write failing tests.**
```python
def test_negotiators_and_pilots():
    w = make_wave()
    assert voters(w) == ["ATC", "AIRLINE:AA", "AIRLINE:DL", "AIRLINE:UA", "GROUND"]
    assert pilots(w) == ["PILOT:DL100", "PILOT:DL201", "PILOT:AA302", "PILOT:UA403"]
def test_airline_utility_is_own_flights_mean():   # one-flight airline == the old PILOT utility plus the next_departure term
    w, p = make_wave(), fcfs_plan(make_wave()); assert 0 <= agent_utility(w, p, "AIRLINE:DL") <= 1
    assert set(agent_terms(w, p, "AIRLINE:DL")) == set(W_AIRLINE)
def test_firm_zero_fuel_floor():                   # Review Focus 5
    # floor plan with 0 kg on DL201; candidate with 1 kg on DL201 -> one fuel breach with allowed == 0.5
def test_relaxing_admits_fuel_from_turn_1():
    assert slack("relaxing", 1) == pytest.approx(0.10) and slack("relaxing", 3) == pytest.approx(0.34) and slack("firm", 3) == 0
def test_floor_source():                           # Review Focus 3
    assert floor_source(w, sq, True, None) is sq and floor_source(w, sq, False, None) is None
    assert floor_source(w, sq, False, best) is best
```
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above. `current()` returns the override if set, else `ATM_FAIRNESS` when it is in `VARIANTS`, else `FAIRNESS_DEFAULT`. An invalid env value falls back to the default with a one-line warning.
- [ ] **Step 4: Run the tests and the offline suite.** Expected: PASS, with no existing test changed.
- [ ] **Step 5: Commit** `feat(utility): airline utility over its own flights; fairness floor firm/relaxing`.

### Task A3: `swap_own` and the AIRLINE authority

**Files:**
- Modify: `backend/wave/moves.py` (`AUTHORITY`, `_check_agent`, `_candidates`, `_validate`, `apply`), `backend/wave/model.py:260-261` (Move comments)
- Test: `tests/test_moves.py`

**Interfaces:**
- Consumes: `airlines.groups`.
- Produces:
  - `AUTHORITY["AIRLINE"] = {"speed_reduction", "ground_delay", "request_divert", "accept", "swap_own"}`.
  - `legal_moves(wave, plan, "AIRLINE:<code>")`.
  - Moves: `_move(agent, "swap_own", [c1, c2], {"runway": end}, "Slot swap AA2712 ↔ AA2762 on 18C (airline substitution)")`. Accept is `_move(agent, "accept", own_callsigns, {}, "AIRLINE:AA accepts current plan")`.

- [ ] **Step 1: Write failing tests.**
```python
def test_airline_moves_own_flights_only():
    ms = legal_moves(w, p, "AIRLINE:DL"); assert ms and all(set(m.callsigns) <= {"DL100", "DL201"} for m in ms)
    with pytest.raises(ValueError, match="own flights"): apply(w, p, _move("AIRLINE:DL", "speed_reduction", ["AA302"], {"minutes": 3.0}, "x"))
    with pytest.raises(ValueError, match="no authority"): apply(w, p, _move("AIRLINE:DL", "change_runway", ["DL100"], {"runway": "27L"}, "x"))
def test_swap_own_adjacent_same_runway():      # DL100/DL201 adjacent on one end in FCFS -> offered; result has swapped order
def test_single_flight_airline_no_swap_own():  # Review Focus 2: AIRLINE:AA (AA302 only) offers no swap_own, still offers accept
def test_unknown_airline_rejected():
    with pytest.raises(ValueError): legal_moves(w, p, "AIRLINE:ZZ")
```
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `_check_agent` returns `"AIRLINE"` iff the code has at least one flight in `groups(wave)`.
  - `_candidates("AIRLINE")` offers the PILOT move set (speed reduction, ground delay, divert) for each own flight, plus `swap_own` for each pair of own wave flights that are adjacent among the wave flights in `strip_sequence` and on the same runway end. That is the same adjacency rule `swap_order` uses, and it is the swap limit (spec §7). It also offers one `accept`.
  - `apply("swap_own")` reuses the `swap_order` re-slot path.
  - `_validate` rejects any callsign that is not one of the airline's own flights, with `f"{agent} may only move its own flights {own}, not {cs}"`. The accept needs at least one own callsign, and `swap_own` needs 2 on the same runway.
  - ATC's speed/ground-delay authority stays as today: every non-focus flight, never the focus flight. `PILOT` authority stays for the fallback solver and MCP.
- [ ] **Step 4: Run the tests and the offline suite.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(moves): AIRLINE authority and swap_own slot substitution`.

### Task A4: Pilot UNABLE (formula only)

**Files:**
- Create: `backend/wave/unable.py`, `tests/test_unable.py`

**Interfaces:**
- Consumes: `RuleCheck` (fields `rule`, `owner`, `source`, `evidence`, `detail`, `callsign`).
- Produces:
  - `PILOT_SIDE_ATC_RULES = ("ATC-WIND-LIMITS",)`.
  - `pilot_side(c: RuleCheck) -> bool`: true when the check failed, has a callsign, and either `owner == "PILOT"` or its rule is in `PILOT_SIDE_ATC_RULES`. In practice that is PLT-EARLIEST-ETA, PLT-FUEL-RESERVE, PLT-CREW-DUTY and ATC-WIND-LIMITS. The spec's "runway length" has no rule to refuse on.
  - `evidence_words(c: RuleCheck) -> str`.
  - `unable_body(turn: int, c: RuleCheck, instruction: Move | None, plan_id: str, proposed_by: str) -> dict`. It returns `{"type": "UNABLE", "turn", "agent": f"PILOT:{c.callsign}", "callsign", "rule", "source", "evidence", "detail", "instruction": move dict | None, "plan_id", "proposed_by", "text"}`, where `text = f"UNABLE {what}: {evidence_words(c)}, {c.rule}"`. `what` is the move label with its first letter lowercased, or `f"{proposed_by}'s plan"` when there is no move.

- [ ] **Step 1: Write failing tests** on the fixture wave. Push AA302 (16 min extra fuel) 20 min past its earliest landing, then run `rules.check`.
```python
def test_fuel_unable_text():
    c = next(c for c in rules.check(w, late) if c.rule == "PLT-FUEL-RESERVE" and c.callsign == "AA302")
    b = unable_body(1, c, None, late.id, "ATC")
    assert pilot_side(c) and b["agent"] == "PILOT:AA302" and b["text"].startswith("UNABLE ATC's plan: ")
    assert b["text"].endswith(", PLT-FUEL-RESERVE") and "16" in b["text"] and b["source"] == c.source
def test_not_pilot_side():   # GND-GATE-FREE / ATC-WAKE-SEP failures are not pilot-side
def test_divert_check_words():   # PLT-FUEL-RESERVE from rules._divert_check (no need_min in evidence) does not raise
def test_wind_is_pilot_side():   # _hard_wave(): a failed ATC-WIND-LIMITS for a callsign is pilot-side; words cite crosswind and limit kt
```
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** `evidence_words` cites only `c.evidence` values, falling back to `c.detail[:120]`:
  - PLT-FUEL-RESERVE: `needs {need_min} min holding > {extra_min} min extra fuel`.
  - PLT-EARLIEST-ETA: `slot T{slot} before earliest T{earliest_min}`.
  - ATC-WIND-LIMITS: `crosswind {x}/{lim} kt, tailwind {t}/{lim} kt`.
  - PLT-CREW-DUTY: its detail.
- [ ] **Step 4: Run the tests and the offline suite.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(unable): pilot-side rule refusals as UNABLE bodies`.

### Task A5: Graph with N voters, airline turns, floor votes, objections and UNABLE

**Files:**
- Modify: `backend/wave/graph.py` (docstring, `_role`, `OBJECTIVES`, `_counted`, `_divert_justified`, `_view`, `_Run`, `_open`, `_agent_moves`, `_votes`, `_speak`, nodes, `_no_consensus_reason`, `_decide_turn`, `build_graph`, `negotiate_wave`)
- Modify: `backend/wave/utility.py` (`negotiators` returns `voters(wave)`, so `evaluate()["utilities"]` is keyed by the voters)
- Test: `tests/test_graph.py` (`_assert_valid`, line 230), `tests/test_rules.py:173-174` (assert `list(utilities) == negotiators(w)` instead of `len == 3`), `tests/test_moves.py:192-194`, `tests/test_perf.py` (negotiator loops), new `tests/test_graph_airlines.py`

**Interfaces:**
- Consumes: A1 to A4.
- Produces:
  - Graph: `triage → open → atc → airlines → ground → decide_turn`. The `airlines` node calls `_speak` for each airline voter in alphabetical order inside one node and merges the state deltas; `_GRAPH` stays a singleton.
  - `negotiate_wave(wave, mode, chain=None, on_message=None, run_id=None, narrate=False, fairness: str | None = None)`. The result adds `"pilots"`, `"airlines"` (as `Wave.to_dict`) and `"fairness"`.
  - The OPEN body adds `pilots` and `airlines`.
  - AGENT_TURN body: `role` can be `"AIRLINE"`. Airline turns add `objections: [{**breach, "plan_id", "proposed_by"}]`.
  - Vote adds `decided_by: "floor"` and `floor: [breach]` when the floor decided it.
  - New message `UNABLE` (sender `PILOT:<cs>`, body from `unable_body`).

- [ ] **Step 1: Update `_assert_valid` and write failing tests in `tests/test_graph_airlines.py`.**
```python
# _assert_valid: voters = r["agents"]; voters[0] == "ATC", voters[-1] == "GROUND", voters[1:-1] == sorted AIRLINE ids
# per turn: [AGENT_TURN senders] == voters; len(turns) == len(voters) * r["turns"]; r["pilots"] == pilots(wave)
def test_fixture_roster():
    r = negotiate_wave(make_wave(), "formulas")
    assert r["agents"] == ["ATC", "AIRLINE:AA", "AIRLINE:DL", "AIRLINE:UA", "GROUND"]   # replaces line 230
def test_unable_after_rejected_pilot_side_move():
    # every rejected move whose violations include a pilot_side check for callsign cs is followed, before the
    # next AGENT_TURN, by an UNABLE from PILOT:cs with instruction.id == move.id; checked on make_wave, _hard_wave,
    # CLT-2024-07-29-19, ATL-2024-07-18-21 (property), plus one forced case: monkeypatch moves.legal_moves so ATC
    # offers only a slot push that breaks AA302's PLT-FUEL-RESERVE -> exactly one UNABLE from PILOT:AA302
def test_unable_plan_cannot_win():      # a plan id in run.unable_plans is never CONSENSUS.plan_id
def test_floor_reject_names_flight():   # firm, CLT: any AIRLINE REJECT with decided_by == "floor" has floor[0]["callsign"] in its reason
def test_no_floor_before_feasible():    # Review Focus 3: status quo made infeasible and nothing feasible seen -> no vote has decided_by "floor"
def test_pilots_make_no_model_calls():  # decision mode with a stub Decider: no Decider is built for a PILOT: id
def test_consensus_accepted_by_all_voters():   # CONSENSUS.accepted_by == r["agents"]; rationale says f"all {n} agents"
```
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `_Run`:
    - `agents = negotiators(wave)`, `airlines = agents[1:-1]`, `pilots = pilots(wave)`.
    - `fairness = fairness or fairness_mod.current()`.
    - `sq = baseline.status_quo(wave)`, `sq_ok = rules.feasible(...)`.
    - `floor_best: dict[code, JointPlan]`, updated in `see()` with the feasible plan that is best by that airline's utility.
    - `unable_plans: set[str]`, `unable_seen: set[tuple]`.
  - `_role("AIRLINE:x") == "AIRLINE"`. `OBJECTIVES["AIRLINE"] =` "Protect each of your flights: delay, holding fuel, connections and next departure; no flight worse than the original plan." `_counted` for AIRLINE counts failed checks naming any of its own flights.
  - Divert guard: replace `_divert_justified` with `_divert_ok(w, mv, checks)`. It is per move: a divert is allowed only if that callsign fails PLT-FUEL-RESERVE or no runway is open. The PILOT check keeps the same semantics.
  - `_view(AIRLINE)` is `{"airline", "flights": [per-flight PILOT view]}`. Test: its JSON is ≤ `benchmark.PROMPT_BUDGET_CHARS` on CLT.
  - Airline move formula: options are ranked by `(known_violations, len(breaches at this turn's slack), -utility, key)`. The airline moves only on a strict lexicographic improvement over the current plan. The model options add `"fairness"` words, e.g. "keeps every flight within its floor" or "AA2712 +15.5 min over its floor".
  - `_votes` for AIRLINE:
    - `floor = fairness.floor_source(...)`.
    - If some CHECK-passed plan on the table (the `valid` list of `_speak`) has no breaches, then every plan with breaches gets REJECT with `decided_by: "floor"` and `floor: breaches`. Its reason is `f"{cs} {metric} {value} > {allowed} ({variant} fairness floor {floor})"`, and that plan is not sent to the model.
    - All other plans are voted as today (utility vs reservation, model with the confidence band).
  - `_speak` for AIRLINE: `objections` lists the breaches of each valid plan proposed by another agent. The rationale adds `"objects: <cs> <metric> +<value-floor> vs <floor source>; counter-offer: <own moves> | rejects the plan"`.
  - UNABLE emission, through one helper `_unables(run, rnd, proposer, rejected, plan, viol)`:
    - after OPEN (round 0), for the opening plan's pilot-side failures;
    - after each AGENT_TURN and before its CHECK, for each rejected move's pilot-side violations and for the new proposal's pilot-side violations.
    - Dedupe on `(plan_id or move id, callsign, rule)`. Add the plan ids to `unable_plans`.
  - `_decide_turn`:
    - unanimous = valid plans not in `unable_plans` that every voter ACCEPTed;
    - `_maxmin` runs over the voters;
    - texts say `f"all {len(agents)} agents"`.
  - Incumbent for turn t+1 (`_decide_turn`): pick by `(fewest floor breaches summed over the airlines at turn t+1's slack, *_maxmin over voters, id)`, not `_maxmin` alone. Otherwise, on CLT, ATC's plan (two AA flights +17 min) wins the mean, the status quo leaves the table, AA's authority cannot move a flight back to 18L, and the firm escape clause ("no CHECK-passed plan in `valid` meets the floor") fires and accepts the worse plan. With this rule, a floor-meeting plan stays in `valid` while one exists. Test `test_floor_plan_stays_incumbent`: on CLT under firm, turn 2's table is the status-quo plan id.
  - `_no_consensus_reason` names the floor-REJECTs with their breach. That text feeds the FALLBACK reason unchanged.
  - Rewrite the module docstring to the new protocol, including the deviation from rules_of_play.
- [ ] **Step 4: Run the tests and the offline suite.** Expected: PASS. Then record the formulas wall time of the 5 replay waves before and after, from `stats.elapsed_ms`, in the commit body.
- [ ] **Step 5: Commit** `contract: airline agents, fairness-floor votes, objections and pilot UNABLE in the negotiation` (new message type and result fields; A7 documents them).

### Task A6: Stress waves CLT-2024-07-29-19 and ATL-2024-07-18-21 (offline, formulas)

**Files:**
- Create: `tests/test_stress_airlines.py`

**Interfaces:**
- Consumes: `negotiate_wave(..., fairness=...)`, `baseline.status_quo`, `airlines.groups`.

- [ ] **Step 1: Write the tests.** They are parametrised over both waves × `("firm", "relaxing")`, in formulas mode.
```python
r = negotiate_wave(build.build_wave(wid), "formulas", fairness=v)
sq = {a["callsign"]: a["metrics"] for a in r["baseline"]["plan"]["assignments"]}   # metrics filled by evaluate()
worse = [(cs, k) for a in r["final_plan"]["assignments"] for cs in [a["callsign"]] for k, eps in (("delay_min", .05), ("fuel_kg", .5))
         if a["metrics"][k] > sq[cs][k] + eps]
if worse:   # spec §5: else the airline objected in the log and the fallback says why
    assert any(m["type"] == "AGENT_TURN" and m["sender"].startswith("AIRLINE:") and
               any(o["callsign"] == cs for o in m["body"].get("objections", [])) for cs, _ in worse for m in r["log"])
    if r["outcome"] == "fallback": assert any(cs in fb["body"]["reason"] for cs, _ in worse for fb in r["log"] if fb["type"] == "FALLBACK")
assert r["evaluation"]["feasible"] and r["agents"] == ["ATC", f"AIRLINE:{'AA' if wid.startswith('CLT') else 'DL'}", "GROUND"]
# firm only, the expected case (status quo legal, ATC's reservation falls to 0.92 x 0.78 ~ 0.72 < its 0.73 on it by turn 2):
if v == "firm": assert not worse
```
- [ ] **Step 2: Run them.** If the firm `not worse` assertion fails, find the root cause; do not loosen the test. Expected: PASS.
- [ ] **Step 3: Print the wave totals** (delay, fuel, outcome, turns) for the 4 cases into the commit body. A12 quotes them.
- [ ] **Step 4: Commit** `test(airlines): CLT and ATL stress waves keep every airline flight at its status quo`.

### Task A7: Contract, types, server, stub and MCP

**Files:**
- Modify: `docs/api-contract.md` (status line, Conventions §AgentId at line 19, Moves, MessageType/MessageBody, AgentTurnBody, Vote, NegotiationResult, message order at line 438, BenchmarkReport), `frontend/src/api/types.ts`, `backend/wave/stub.py`, `backend/mcp_server.py` (docstrings), `backend/server.py` only if a field needs passing through
- Test: `tests/test_api.py`, `tests/test_mcp.py`

**Interfaces (v2.3, additive):**
- `AgentId` adds `` `AIRLINE:${string}` ``. `AgentTurnBody.role` adds `"AIRLINE"`.
- `FloorBreach {callsign; metric: "delay_min" | "fuel_kg"; floor; value; allowed}`; `Objection = FloorBreach & {plan_id; proposed_by: AgentId}`; `AgentTurnBody.objections?: Objection[]`.
- `Vote.decided_by?: "formula" | "floor"` (`formula` was already emitted but untyped); `Vote.floor?: FloorBreach[]`.
- `MessageType` adds `"UNABLE"`. `UnableBody {type: "UNABLE"; turn; agent: `` `PILOT:${string}` ``; callsign; rule: RuleId; source; evidence; detail; instruction: Move | null; plan_id; proposed_by: AgentId | "ENGINE"; text}`.
- `Move.kind` adds `"swap_own"`.
- `AirlineGroup {code; name; callsigns: string[]; basis}`. `WaveScenario.airlines?`, `NegotiationResult.airlines?` and `pilots?: string[]`, `fairness?: "firm" | "relaxing"`, and the OPEN body's `pilots?` and `airlines?`.
- `BenchmarkReport`: `fairness?`, `arms[].summary.worse_than_status_quo_pct?` and `model_calls_per_wave?`, `fairness_variants?`.

- [ ] **Step 1: Write failing tests.**
  - `test_mcp_legal_moves_airline`: MCP `legal_moves(wave_id, "AIRLINE:DL")` returns only DL moves.
  - `test_mcp_negotiate_roster`: MCP `negotiate` returns `agents`, `pilots` and `airlines`.
  - `test_stub_stream_has_airline_and_unable`: the stub emits one AIRLINE AGENT_TURN and one UNABLE.
  - `test_wave_has_airlines`: GET `/api/wave/{id}` includes `airlines`.
  - `test_old_replay_passes_through`: a stored v3-traffic replay with 3 agents still streams with `paths_unavailable: true` (Review Focus 4).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The stub roster is `["ATC", "AIRLINE:<focus carrier>", "GROUND"]` plus one UNABLE.
  - The MCP `legal_moves` docstring names `AIRLINE:<code>` and says `PILOT:<cs>` lists the per-flight action space the fallback solver searches. Pilots never propose in a negotiation.
  - Contract prose:
    - replace "exactly three agents" with the voter/pilot roster;
    - add the new message order `ATC → (UNABLE*) → CHECK → AIRLINE:* each → (UNABLE*) → CHECK → GROUND → CHECK → CONSENSUS|NO_CONSENSUS`;
    - add one line that old runs carry their own `agents`.
- [ ] **Step 4: Run the offline suite and the frontend checks** (tsc must accept the existing samples). Expected: PASS.
- [ ] **Step 5: Commit** `contract: v2.3 airline agents, UNABLE, swap_own, Wave.airlines, fairness fields`.

### Task A8: Frontend roster, airline turns and UNABLE lines

**Files:**
- Create: `frontend/src/agents.ts`, `frontend/src/agents.test.ts`
- Modify: `frontend/src/format.ts` (`agentClass`, `agentName`), `frontend/src/components/Log.tsx` (`summary`, `Turn`, the empty-log roles list, the UNABLE article), `frontend/src/WaveScreen.tsx` (the `3 agents` meta at line 651, `lastRadio` at 709-716, passing `airlines`/`pilots` to `Log`), `frontend/src/styles.css` (`.who.airline`, `.msg.unable`)

**Interfaces:**
- Consumes: the A7 types.
- Produces:
  - `roster(agents: AgentId[], pilots?: string[], airlines?: AirlineGroup[]) -> {id: string; cls: "atc" | "airline" | "ground" | "pilot"; name: string; what: string}[]`;
  - `unableLine(b: UnableBody) -> string`;
  - `objectionLine(o: Objection) -> string`.

- [ ] **Step 1: Write failing vitest tests.**
```ts
expect(agentClass("AIRLINE:AA")).toBe("airline"); expect(agentName("AIRLINE:AA")).toBe("Airline AA");
expect(roster(["ATC", "AIRLINE:AA", "GROUND"], ["PILOT:AA2712"], [{ code: "AA", name: "American Airlines Inc.", callsigns: ["AA2712"], basis: "" }])
  .map((r) => r.cls)).toEqual(["atc", "airline", "ground", "pilot"]);
expect(roster(["ATC", "PILOT:DL100", "GROUND"]).map((r) => r.cls)).toEqual(["atc", "pilot", "ground"]);   // old 3-agent run (Review Focus 4)
expect(unableLine(sampleUnable)).toMatch(/^UNABLE .+, PLT-FUEL-RESERVE$/);
expect(objectionLine({ callsign: "AA2712", metric: "delay_min", floor: 209, value: 224.5, allowed: 209.05, plan_id: "x", proposed_by: "ATC" }))
  .toBe("AA2712 delay +15.5 min vs the original plan (ATC's plan)");
```
- [ ] **Step 2: Run them.** `cd frontend && npx vitest run src/agents.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `agentName` never returns "Triage" for an unknown sender: unknown senders show the raw id.
  - `Log` renders UNABLE as its own article with class `msg unable`: the pilot name, then `unableLine`, then the source.
  - `Turn` lists `objectionLine` rows for airline turns.
  - The empty-log role cards are built from `roster`; when the wave is not yet negotiated, they come from `wave.airlines` and `wave.flights`.
  - The WaveScreen meta reads `${roster.length} agents`.
  - Strips and radar labels are unchanged (spec §3.5).
- [ ] **Step 4: Run all frontend checks** (`npm test && npx tsc --noEmit && npm run build`). Expected: PASS. Then run `npm run dev` on the stub or a formulas run and check the log by eye.
- [ ] **Step 5: Commit** `feat(frontend): airline agents, objections and UNABLE in the negotiation log`.

### Task A9: Benchmark code: worse-than-status-quo, model calls, `--fairness`, variant pick

**Files:**
- Modify: `backend/wave/benchmark.py` (`HISTORY_KEYS`, `VERSION_ORDER`, `run_arm`, `summarize`, `collect_failures`, `_rows`, `markdown`, `main`), `scripts/benchmark_notes.py`
- Test: `tests/test_benchmark_small.py`

**Interfaces:**
- Produces:
  - `worse_than_sq_pct(arm_pw: dict, base_pw: dict, infeasible_ids: list[str]) -> float | None`: the % of waves with a feasible status quo whose final plan (consensus or fallback) has `delay_min > sq + 0.05` or `fuel_kg > sq + 0.5`. `None` with no such waves.
  - `summarize` adds `worse_than_status_quo_pct` and `model_calls_per_wave` (the mean of `stats.model_calls`). `HISTORY_KEYS` adds `"worse_than_status_quo_pct"`, which `_rows` computes from an archived report's `per_wave` when the key is missing.
  - `run_arm(..., fairness: str | None = None)` and `--fairness {firm,relaxing}`; `report["fairness"]`.
  - `pick_fairness(firm: dict, relaxing: dict, prev: dict) -> dict` returns `{"variant": "firm" | "relaxing" | None, "rule": str, "reasons": list[str], "rows": list[dict]}`:
    - A variant is eligible iff every arm's `consensus_rate_pct` is ≥ that of the same-label arm in `prev` (v3-traffic). Arms that are missing in `prev` are skipped.
    - Score = the mean `worse_than_status_quo_pct` over the arms. The lowest eligible score wins.
    - |Δ| < 0.05 is a tie, and firm wins it. If neither variant is eligible, the variant is `None`.
  - `collect_failures` flags a regression vs the status quo on delay or fuel.
  - `markdown` adds a `## Fairness variants` section when `fairness_variants` is present: the per-arm consensus, worse-than-SQ %, delay, fuel, model calls/wave and p95, plus the pick and its reasons.
  - `benchmark_notes.py --variants FIRM.json RELAXING.json` attaches `fairness_variants` to `eval/benchmark.json` and re-renders.

- [ ] **Step 1: Write failing tests.**
  - `test_worse_than_sq_pct`: hand-built per-wave dicts, including one fuel-only regression and one infeasible-SQ wave that is excluded.
  - `test_pick_fairness_rule`, three cases: relaxing better and eligible → relaxing; a tie → firm; relaxing consensus below prev → firm; both below → `None`.
  - `test_fixture_benchmark_has_new_keys`: `main(["--fixtures", "--n", "2", "--fairness", "relaxing", "--out", tmp, "--no-ablation"])` has the new summary keys and `fairness == "relaxing"`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run the tests and the offline suite.** Expected: PASS.
- [ ] **Step 5: Commit** `eval: worse-than-status-quo share, model calls per wave, fairness variants in the benchmark`.

### Task A10: Live benchmark on both variants, pick, ship, archive v3-traffic

**Files:**
- Modify: `backend/wave/version.py`, `tests/test_version.py`, `backend/wave/fairness.py` (`FAIRNESS_DEFAULT`), `eval/benchmark.json`, `eval/BENCHMARK.md`, `eval/variants/{firm,relaxing}/*`
- Create: `eval/archive/engine-v3-traffic/`

- [ ] **Step 1: Freeze the old report.** In the worktree, link `.env` (state this in the task report), then run `python3 scripts/benchmark_notes.py --archive`. Expected: `archived to eval/archive/engine-v3-traffic`.
- [ ] **Step 2: Bump the version.** Set `ENGINE_VERSION = "v4-airlines"` and update `test_version.py`. The only expected red is the replay test (Global Constraints).
- [ ] **Step 3: Run both variants** with the README arms and the same 50 ids:
  `python3 -m backend.wave.benchmark --source flights2015 --n 50 --arms openrouter:typesafe/jev-1.14,openrouter:liquid/d1-ultra,openrouter:baseten/elephant-1,formula --concurrency 4 --seed 7 --outage-n 20 --fairness firm --out eval/variants/firm`, then the same command with `relaxing`.
  Expected: 0 exceptions and no `WARNING` line.
- [ ] **Step 4: Pick.** Run `python3 -c` → `pick_fairness(firm, relaxing, eval/archive/engine-v3-traffic/benchmark.json)`.
  - If `variant is None` (consensus fell below v3-traffic in both, the spec §6 anti-goal): STOP and report both tables to the controller.
  - Otherwise set `FAIRNESS_DEFAULT` to the winner, then copy that run's `benchmark.json` and `BENCHMARK.md` to `eval/`.
- [ ] **Step 5: Attach the variants and notes.**
  - Run `python3 scripts/benchmark_notes.py --variants eval/variants/firm/benchmark.json eval/variants/relaxing/benchmark.json`.
  - Then run `python3 scripts/benchmark_notes.py eval/runs/a10-notes.json`. The notes file stays uncommitted. It covers:
    - title "Airline agents (v4-airlines)";
    - the worse-than-SQ share vs v3-traffic for each arm;
    - consensus per arm and variant;
    - model calls per wave and p95;
    - the A6 stress-wave totals;
    - regressions, each with a measured cause or "a likely cause (not measured per wave)";
    - limits (static airline table, ORD has no single major, pilots refuse only on PLT-* and wind).
  - Check `rule_violations_in_agreed_plans == 0` in every arm and variant.
- [ ] **Step 6: Seed.** Run `python3 scripts/db_seed.py benchmark`, then `python3 scripts/db_seed.py --check benchmark`. Expected: up to date. Remove the `.env` link.
- [ ] **Step 7: Commit** `eval: v4-airlines benchmark, firm vs relaxing, <winner> shipped; v3-traffic archived`.

### Task A11: Replays re-recorded, samples, db_seed

**Files:**
- Modify: `data/replay/*.json` (same 5 ids), `frontend/src/api/samples/*`, `frontend/src/api/samples.typecheck.ts` only if the new types need widening

- [ ] **Step 1: Record.** Link `.env`, then run `python3 -c "from backend.wave import replay; replay.record(['F2015-7053','F2015-292602','F2015-152758','F2015-75','F2015-77162'], 'decision')"` with the default chain. Expected: no `WARNING` line, and every replay recorded on `v4-airlines` with the shipped `fairness`.
- [ ] **Step 2: Check the demo conditions.**
  - At least one airline counter-offer across the 5, i.e. an AIRLINE AGENT_TURN with non-empty `objections`. DEN F2015-77162 (WN×2, NK, DL) and ORD F2015-7053 (EV×2) are the likely ones.
  - `pytest tests/test_geo_api.py::test_replays_are_current_engine_and_show_laps` must pass (≥3/5 with a lap).
  - Risk: MIA's agreed-plan lap (AA1202, 44.7 min) will probably be objected away, which leaves ORD-7053 and ATL-152758 (status-quo laps) at 2/5. If laps fall below 3, or no counter-offer appears, STOP. Run `PYTHONPATH=. python3 scripts/pick_replays.py <hubs> --scan 300` offline, propose swaps to the controller, and do not swap ids or relax the test without a ruling.
  - Count UNABLE lines per replay, for the README. It may be 0 (spec §5).
- [ ] **Step 3: Regenerate the samples.** Run `PYTHONPATH=. ATM_STORE=files python3 scripts/make_samples.py`, then the frontend checks. Expected: PASS, and `result.json` carries `pilots`, `airlines` and `fairness`.
- [ ] **Step 4: Seed.**
  - Run `python3 scripts/db_seed.py replays`, then `--check replays benchmark waves`. Expected: up to date.
  - If the staleness hash skips the seed, call `scripts.db_seed._load_replays` once directly, so that `eval.replays` holds exactly the 5 v4-airlines rows. This is the T33 precedent.
  - Remove the `.env` link. Run the offline suite: all green.
- [ ] **Step 5: Commit.** First `eval: replays re-recorded on v4-airlines`, then `contract: samples regenerated for v4-airlines`.

### Task A12: README, BENCHMARK and graph docs

**Files:**
- Modify: `README.md` (Pitch line 13, the architecture mermaid around line 66, the graph mermaid around lines 100-130, the protocol paragraph at line 135, the moves/utilities bullets at lines 156-157, §5 Evaluation between the `<!-- BENCHMARK -->` markers, Cost per wave, Demo replays, §6, §7 Limits), `docs/graph.mmd`

- [ ] **Step 1: Regenerate the graph.** Run `python3 -m backend.wave.graph --mermaid docs/graph.mmd` and paste the result into the README. Expected: `airlines` sits between `atc` and `ground`.
- [ ] **Step 2: Write the deviation statement (spec §4).**
  - Quote rules_of_play: "One turn = each of the three agents sends one structured message."
  - Say plainly that this build has 2 + N airline voters plus formula-only pilots, and that this is the default mode, not a switch.
  - Reason: real operations (14 CFR 121.533, FAA CDM slot substitution).
  - Evidence: the §1 stress-test table next to the A6 results, firm and relaxing.
- [ ] **Step 3: Evaluation.**
  - Both variants' tables from `fairness_variants`, with the pick and its rule.
  - Worse-than-SQ share vs v3-traffic, consensus per arm, 0 violations in every arm, model calls per wave and p95.
  - Every number comes from `eval/benchmark.json`. Nothing is rounded into a better claim, and regressions are listed.
- [ ] **Step 4: Replays.** Rewrite each bullet from the A11 recordings: who objected, the counter-offer, UNABLE (if any) and the totals. Then grep for leftovers: `rg -n "three agents|3 agents|exactly three" README.md docs/api-contract.md frontend/src backend` must only return deliberate history lines.
- [ ] **Step 5: Commit** `docs: airline agents, pilot UNABLE, the three-agent deviation and v4-airlines numbers`.

### Task A13: Final verification

- [ ] **Step 1: Offline checks.**
  - Run `ATM_STORE=files ATM_WARM=0 python3 -m pytest -q -m "not live_db"`: all pass.
  - Run `cd frontend && npm test && npx tsc --noEmit && npm run build`: all pass.
- [ ] **Step 2: Cold build, fresh process.** Run `ATM_STORE=files ATM_WARM=0 python3 -c "from backend.wave import build; import time; t=time.perf_counter(); build.build_wave('F2015-7053'); print(round((time.perf_counter()-t)*1000))"` 3 times. Expected: each ≤ 1000. Also run `python3 -m pytest tests/test_perf.py -q`: `evaluate` p95 stays within its 3 ms budget even with the extra voters.
- [ ] **Step 3: Postgres smoke.**
  - Link `.env`, then start `ATM_WARM=0 python3 -m uvicorn backend.server:app --port 8766`.
  - For each replay id: `/api/config`, `/geo`, and POST `/api/negotiate-wave` with replay and stream false. Each must return `engine_version` v4-airlines, `pilots` and `airlines`.
  - Then one formulas run on CLT-2024-07-29-19.
  - Python RSS must stay < 512 MB. Note it against the 308 MB baseline.
  - Stop the server, then remove the link.
- [ ] **Step 4: Spec §5 checklist,** each item with its evidence:
  - CLT/ATL (A6);
  - the worse-than-SQ drop and 0 violations (A10);
  - ≥1 counter-offer and the UNABLE count (A11);
  - model calls per wave, p95, and the cold ORD build.
  Anything unmet is reported, not hidden.
- [ ] **Step 5: Commit** any fixes found, each under its own message. No empty commit.
