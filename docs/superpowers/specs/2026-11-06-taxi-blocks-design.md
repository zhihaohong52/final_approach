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

- `surface.stop_points(g, path)`: the path indices where an aircraft may wait. Index 0 (stand, or the start of the
  runway vacate), OSM holding positions, and runway hold points (`_crossing_zones` h) always; otherwise a node that is not
  a junction, whose nose stays `JUNCTION_CLEAR_M` short of the next junction ahead on the path, whose tail
  (`TAXI_AIRCRAFT_LEN_M` + `JUNCTION_CLEAR_M`) is clear of the previous junction, and that is at least
  `TAXI_AIRCRAFT_LEN_M + TAXI_QUEUE_GAP_M` past the previous stop point. Never inside a runway protected zone or the
  vacate stretch.
- Junction: a node with three or more distinct non-runway neighbours, not counting dead-end stubs (stand leads). Whether
  taxilane-only nodes count is settled by measurement (stop points per route at ATL and ORD) and recorded here.
- `_crossing_zones`' `hold[i]` becomes the last stop point before `i` instead of `i - 1`. The scheduler already drives a
  non-stop stretch (the runway zones use it), so the rest of `schedule` is unchanged. A queue now holds one aircraft per
  stop point, i.e. per ~60 m of taxiway.
- Direction locks (my call: not added). The per-edge opposite-direction reservation plus the node headway already
  exclude two aircraft meeting head-on on a single-lane segment of a time-exact schedule (they would have to share an
  edge or a node at the same time); measured head-on waits are 0–0.8%. A railway direction lock adds safety only when
  trains run off their timetable, which this model does not simulate.
- `surface.verify_stops(tracks, g)`: independent check that no track dwells at a junction node (OSM holding positions
  and the first point excepted).
- LIMITS: "taxi stop points" (assumption), with the three constants.

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
