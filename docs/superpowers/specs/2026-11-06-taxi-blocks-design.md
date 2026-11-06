# Taxi model: runway configuration, gate holds, block stop points, crossing batching

Approved in chat on 6 Nov 2026 ("all 4 points, test each so that it works"), after a measurement of the hold-short
scheduler on 10 reference waves (1,052 fixed taxi movements). Branch `taxi/blocks`. Results change, so the engine
version changes.

## Why

The taxi scheduler (`surface.schedule`) is a sound timetable: head-on conflicts cause 0–0.8% of wait time and runway
crossings already work like railway blocks (hold at the stop bar until the whole protected zone is clear). What was
wrong was where aircraft wait and why:

- 75–92% of wait minutes are aircraft queued behind another waiting aircraft, one aircraft per OSM node, so a queue's
  length depends on how densely OSM places nodes; 63% of waits are inside junctions.
- Departures make a median of 6 stops while taxiing (p90 24, max 41). At ORD on 4 Jun 2024 (a ground-stop evening, real
  pushback-to-wheels-off up to 161 min) the model pushes them back at the real time and stacks them along the route:
  7,481 min of waits, 115 failed schedules, 97 of 129 takeoffs later than the real wheels-off.
- ATL lands queue arrivals on 26L/26R/27L (ties by runway id), so all 165 departures squeeze onto 27R and 28.

Baseline figures: `harness.py` (job tmp) on main `f6ecdd3`: 19,305 min of fixed-traffic taxi waits, 204 failures,
344 of 1,035 takeoffs late, one separation breach (ORD, AA2881, pre-existing).

## Goals

1. Runway configuration: arrivals on the outboard parallels, departures inboard.
2. Gate holds: a departure leaves its stand just in time for its real takeoff, instead of queueing on the taxiways.
3. Block stop points: aircraft stop only where a real aircraft can hold (clear of junctions, nose-to-tail spacing), and
   the stretch between two stop points is driven non-stop (enter only when it is clear: route setting with overlap).
4. Runway crossing batching: a crosser joins a crossing of the same runway that starts within a minute, so the runway
   is closed to takeoffs once for the group instead of once per aircraft.

Non-goals: rerouting around queues (routes stay the fastest unimpeded route); re-timing fixed traffic per plan; an
event-driven simulation. The output is still a timetable: plans are not made robust to aircraft running late. The
fragility figure (share of movements whose 30 s lateness breaks the plan's times, 82% at baseline) is reported, not
promised.

## 1. Runway configuration (my call: the mechanism)

Approved as "keep departure runways parallel to the arrival runway available". The cause is the arrival assignment, so
the fix is there:

- `build._queue`: the parallels of the best-wind end are ranked by distance from the terminal (median of the OSM stands,
  `_terminal_ll`), farthest first; the queue lands on the first `active_runways - 1` of them as before. Without stands or
  runway end coordinates, the old order (headwind, then id).
- `moves._wind_rank`: after the wind limits and the opposite-flow test, an end the queue lands on comes before an end it
  does not, so wave flights join the arrival flow instead of taking a departure runway with the same wind.
- `ground_traffic.departure_mode` is unchanged; it now finds the inboard ends free.
- LIMITS: "arrival runways" (assumption). Matches the configurations I am confident of (ATL west flow, DTW, SEA, JFK,
  PHX); airports whose terminal is not between its parallels (DEN) are wrong before and after; LAX still lands on three
  runways because of the active-runways-minus-one count, which is unchanged.

## 2. Gate holds

- `FixedMover.t_push_min`: the metered pushback, `max(real pushback, WHEELS_OFF - unimpeded taxi to the holding point -
  GATE_HOLD_BUFFER_MIN)` for the path actually flown; `t_start_min` stays the real BTS gate-out time. Arrivals: equal to
  `t_start_min`.
- Trial scheduling for the runway choice (`_settle_runway`) and the schedule use the metered time of each candidate path.
- The track gets a leading wait at the stand ("gate hold until …") from the real to the metered pushback; taxi time is
  measured from the metered pushback (BTS TAXI_OUT starts at gate-out, which the hold moves). Stats: `gate_hold_min`.
- Gates: fixed movers use distinct OSM stands outside the wave's bound gates, and a stand is not reserved before
  pushback in either version, so holding does not change any gate rule.
- LIMITS: "gate hold" (assumption; the TSAT = TTOT - EXOT idea of A-CDM / surface metering, with the real wheels-off as
  the target).

## 3. Block stop points

Decided after measurement (6 Nov, "make it as realistic as possible to aircraft operations"): stop lines on the
taxiway, not node-only stop points. 35-62% of route nodes are junctions (ATL 62%, ORD 35-40%), so "stop only on a
node that is not a junction" leaves long stretches with nowhere to stop, and OSM node spacing (5-200 m) would still
set queue capacity.

- `surface.stop_lines(g)`: per directed taxi edge (u, v), fixed positions on the edge: the line nearest v is
  `JUNCTION_CLEAR_M` (40 m) short of v when v is a junction, else one queue spacing (`TAXI_AIRCRAFT_LEN_M` 45 +
  `TAXI_QUEUE_GAP_M` 15 = 60 m); the rest every 60 m back, keeping that spacing from u (85 m, the tail clear, when u
  is a junction). None within `RUNWAY_HOLD_CLEARANCE_M` of a runway centreline. Built once per graph at load (before
  it is shared between threads), deterministic ids appended to `g.nodes` (empty adjacency), so every geometry lookup
  works on them and they pickle with the graph.
- `surface.refine(g, path)`: the route with its edges' stop lines inserted; each edge's time split by distance.
- `_stop_holds`: where to wait before entering each index. Inside a runway zone and the vacate stretch, unchanged.
  Elsewhere the last stop point at least 40 m short of the index when it is a junction, else 60 m behind it. Stop
  points: the first index, each zone's hold, the end of the vacate stretch, OSM holding positions, and every other
  position that is not a junction, not in a zone, 40 m short of the next junction and 85 m past the last one. Past the
  vacate stretch the hold never goes back onto the runway.
- Scheduler checks: (a) node headway now applies to stop lines too (nose-to-tail queues); (b) head-on is checked on
  the whole graph edge, stop-line waits included, and a wait on a stop line rescans from the edge's first node;
  (d, new) no overtaking: on every step, entry and exit order match every other traversal in the same direction (it
  also catches the turn-speed difference that let a fast aircraft pass a slow one on a long edge before).
- `verify_separation`: (2) on whole graph edges, (5, new) overtaking on any step. No separate `verify_stops`: the
  test `test_fixed_traffic_never_waits_in_a_junction` checks the stop rule on ATL and ORD.
- Direction locks: not added, as planned (head-on is exact on whole edges).
- LIMITS: "taxi stop lines" (assumption).

Measured (10 reference waves, after gate holds): waits in junctions 35-58% of wait time -> 0 apart from OSM holding
positions; fixed-traffic waits 2,731 -> 2,944 min; takeoffs after the real wheels-off 119 -> 130; no new separation
breach or overtaking. Queues are now 60 m per aircraft, so the ~125 m between two parallel runways' protected zones
holds about two crossers: at ATL east side and ORD 09R/27L, crossers of the departure runway back up onto the runway
exit (ATL-2024-06-07-20 still-vacating flags 11 -> 20) and ORD wave flights wait 87 -> 137 min. That is the crossing
bottleneck made visible; section 4 addresses it.

## 4. Runway crossing batching (built twice, parked twice)

1. Join a scheduled crossing starting within `CROSSING_BATCH_MIN` (branch `taxi/crossing-batching`): fires 1-3 times
   a wave and changes nothing (waits 2,731 -> 2,750 min, late takeoffs 119 -> 118). Sequential FCFS scheduling lets a
   crosser join only a crossing of a mover scheduled before it; the common case needs the earlier crosser held.
2. Crossing windows, as a tower does it (branch `taxi/crossing-windows`): a first pass finds when each crosser reaches
   its hold, batches open windows, a second pass keeps takeoffs clear of them. Six waves: waits 2,782 -> 2,336 min,
   failures 3 -> 0, but takeoffs after the real wheels-off 88 -> 212; more passes do not converge. The day looks less
   like the real one, so it is not merged.

The lever is the crossing gap assumption (LIMITS "runway crossing gap": no takeoff within 1 min either side of the
time in the zone), which makes every crossing cost ~2.5 min of departure runway. With 1 min before entry and 0.25
min after exit, waits drop about a third with no windows (verifier window not updated in that experiment, so its
failure count is not valid). Changing it is the user's call: it is the rule engine's assumption too.

Decided 6 Nov ("1 min before, 15 s after"): `GAP_MIN["takeoff"] = (1.0, 0.25)`: a crosser goes 1 min after a takeoff
started (the departure has rolled past the crossing point), the next takeoff starts 15 s after the crossers leave the
zone. The verifier's window is derived from `GAP_MIN`. Ten waves: fixed waits 2,944 -> 2,049 min, failures 3 -> 0,
late takeoffs 130 -> 116, wave-flight FCFS waits ATL-2024-06-07-20 32 -> 17 min, ORD 137 -> 123, no new separation
breach. Crossing windows on top of it still trade (waits 1,737, late takeoffs 215): they stay parked.

## Testing

Each change: unit tests for its rule, the full suite, and a before/after row of the harness (waits, failures, late
takeoffs, stops per departure, waits in junctions, fragility, `verify_separation` breaches, wave-flight taxi against
the real BTS TAXI_IN). `verify_separation` must not gain breaches. Scenario pins that depend on engine results
(`test_stress_airlines`) are re-picked at the end by their stated criteria.

## Results (engine v5-taxi, 6 Nov 2026)

Harness, 10 reference waves (fixed-traffic taxi waits excluding gate holds / failures / takeoffs after the real
wheels-off / share of waits in junctions):

| step | waits min | failures | late | in junctions |
|---|---|---|---|---|
| baseline (main f6ecdd3) | 19,305 | 204 | 344 / 1,035 | 63% |
| + arrival runways outboard | 18,949 | 210 | 271 | |
| + gate holds (5 min buffer) | 2,731 | 3 | 119 | 35-58% |
| + stop lines | 2,944 | 3 | 130 | ~0 (OSM holds only) |
| + crossing gap 1 min / 15 s | 2,049 | 0 | 116 | ~0 |

Separation breaches: 1 throughout (ORD AA2881, pre-existing), no overtaking. Wave-flight FCFS waits: ORD-2024-06-04-21
205 -> 123 min, ATL-2024-06-07-20 25 -> 17. Fragility (share of movements whose 30 s lateness touches another's
reservation): 82% at baseline, 41-78% per wave now; a timetable property, not robustness.

Formulas benchmark (BTS 2024, n 50, seed 7, firm, formula arm only; free): FCFS breaks a rule in 29 of 50 hours (26
at v4-airlines); agreed plans 0 violations; formula arm delay 457.8 -> 454.6 min, fuel 427 -> 392 kg; FCFS delay on
the hours where it is legal 477.0 -> 485.6 min (21 hours, was 24). The six newly illegal hours all fail
ATC-TAXI-CONFLICT with a wave flight still on its runway: ORD-2024-06-07-20, SLC-2024-08-09-23, LAS-2024-08-03-22
(fixed traffic never yields to wave flights; with 60 m queues it holds a wave arrival on its exit), BNA-2024-06-27-16,
BNA-2024-07-07-22 (the wave flights now land on 02L, whose chosen OSM exit runs along the runway pavement at 2.8 deg),
ATL-2024-07-22-02 (the wave flight now lands with the queue on 27L, landings 1 min apart, 0.28 min short of vacating).
BOS-2024-06-29-17, JFK-2024-07-08-20, SEA-2024-06-14-23 became legal.

## After merge (not on this branch without the user)

`ENGINE_VERSION` changes, so the recorded replays lose their path drawings until re-recorded, and the model-arm
benchmark (paid) should be rerun. The formulas-mode figures are recomputed on the branch.
