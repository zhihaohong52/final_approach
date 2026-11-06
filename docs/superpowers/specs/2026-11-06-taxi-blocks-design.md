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

## 4. Runway crossing batching

- Fixed traffic only (`ground_traffic._compute`): when a crosser's zone traversal on a strip does not overlap any
  scheduled crossing of that strip but one starts within `CROSSING_BATCH_MIN` after it would start, it waits to cross
  with that one (the node headway still separates them). Passed to `schedule` as `Reservations.crossings` (default
  empty, so wave flights and every other caller are unchanged: for a wave flight batching only adds wait, because the
  fixed takeoffs it would help are already placed).
- LIMITS: "runway crossing batching" (assumption).

## Testing

Each change: unit tests for its rule, the full suite, and a before/after row of the harness (waits, failures, late
takeoffs, stops per departure, waits in junctions, fragility, `verify_separation` breaches, wave-flight taxi against
the real BTS TAXI_IN). `verify_separation` must not gain breaches. Scenario pins that depend on engine results
(`test_stress_airlines`) are re-picked at the end by their stated criteria.

## After merge (not on this branch without the user)

`ENGINE_VERSION` changes, so the recorded replays lose their path drawings until re-recorded, and the model-arm
benchmark (paid) should be rerun. The formulas-mode figures are recomputed on the branch.
