"""Deterministic safety/legality engine for joint wave plans.

No model ever decides legality: every check below is a formula over the plan and the wave's
data, and every RuleCheck cites its rule, source and the input values used.
"""
from __future__ import annotations

import functools
import math
import re
from collections import Counter

from backend.wave import ground_traffic, holding, route, surface, trajectory, wind
from backend.wave.model import FlightMetrics, JointPlan, RuleCheck, Wave
from backend.wave.utility import MAX_SPEED_REDUCTION_MIN, TAXI_FAR_MIN, fuel_info, metrics_map, turnaround_min

SRC_WB = "challenge2_operational_constraints.xlsx"
SRC_WAKE = f"{SRC_WB} wake_separation (min_separation_nm at 140 kt, simplified FAA JO 7110.65)"
SRC_FUEL = f"{SRC_WB} fuel_and_crew (final reserve 30 min) + 14 CFR 121.639 alternate"
SRC_OPS = "ops rule"
SRC_ETA = "ops rule + FAA CIFP 2611 route time T_route (synthesized where CIFP has none)"
SRC_STACK = "FAA JO 7110.65 §4-5-1 (1,000 ft) and §2-1-4 (first come, first served); 5 levels (assumption)"
SRC_CAP = f"{SRC_WB} airport_capacity (max_runway_ops_per_hour / active_runways)"
SRC_TURN = f"{SRC_WB} airport_capacity (min_turnaround_min)"
SRC_NOTICE = f"{SRC_WB} fuel_and_crew (gate change notice >= 20 min before arrival)"
SRC_DUTY = f"{SRC_WB} fuel_and_crew (flight crew max duty 13 h from report; report = sched dep - 60 min, assumption)"
SRC_CREW = f"{SRC_WB} fuel_and_crew (ground crew shift 8 h, overtime 1.5x)"
GATE_NOTICE_MIN = 20.0
EPS_MIN = 1e-6   # float tolerance (min) on slot >= earliest and hold <= extra fuel: moves._r rounds a slot to
                 # 0.1 min and can land ~5e-8 below trajectory.earliest; 1e-6 min = 60 microseconds
OVERTIME_MULT = 1.5
REMOTE_STAND_POSITIONS = 2   # assumption: aircraft on the remote stand (RS) at overlapping times
SRC_RWY = "airport runway status"
SRC_TAXI_OSM = surface.SRC_ROUTED
SRC_TAXI_CONFLICT = ("FAA JO 7110.65 §3-7-2 (ground control route and hold-short); headway 30 s, crossing gap, "
                     "30-min horizon (assumption)")
WET_VIS_SM = 3.0
WEATHER_FACTOR_BAD = 1.25
_RWY_RE = re.compile(r"^(\d{1,2})([LRC]?)$")


@functools.lru_cache(maxsize=4096)   # pure, called ~400k times a negotiation
def wake_gap_min(leader: str, follower: str) -> float:
    """Minimum time (min) between a leader and a follower landing on the same runway (dry), from the
    workbook wake_separation nm column converted at 140 kt (constraints.wake_s_arrival); B757 is
    treated as Heavy by constraints.py."""
    from backend.wave import constraints
    return constraints.wake_s_arrival(leader, follower) / 60.0


def capacity_per_runway_h(wave: Wave) -> tuple[float, object]:
    """Workbook arrival cap per runway per hour = max_runway_ops_per_hour / active_runways."""
    from backend.wave import constraints
    lim = constraints.airport(wave.airport.iata)
    return lim.max_runway_ops_per_hour / max(lim.active_runways, 1), lim


# Every rule check() can emit (the contract's RuleId). /api/config serves it as `rule_ids`.
RULE_IDS = ("ATC-RWY-OPEN", "ATC-WAKE-SEP", "ATC-RWY-CAPACITY", "ATC-TAXI-ROUTE", "ATC-TAXI-CONFLICT",
            "ATC-HOLD-STACK", "ATC-WIND-LIMITS", "PLT-EARLIEST-ETA", "PLT-FUEL-RESERVE", "PLT-CREW-DUTY",
            "GND-GATE-SIZE", "GND-GATE-FREE", "GND-GATE-NOTICE", "GND-CREW-DUTY")


# --------------------------------------------------------------- limits stated (rules_of_play)
LIMITS = [
    {"limit": "wake separation (same runway)", "value": "3-8 nm (min_separation_nm)", "source": "workbook",
     "note": "converted to time at 140 kt approach speed (assumption); x1.25 wet/low visibility (assumption); "
     "applied to arrivals on the same physical runway (both ends)"},
    {"limit": "B757 wake category", "value": "Heavy", "source": "assumption",
     "note": "workbook has no B757 row; treated as Heavy (conservative)"},
    {"limit": "wet-runway / low-visibility separation factor", "value": "x1.25", "source": "assumption",
     "note": "added on top of workbook wake times when METAR shows wet surface or visibility < 3 SM"},
    {"limit": "final fuel reserve", "value": "30 min", "source": "workbook", "note": "never planned below; "
     "hold allowed only up to the extra fuel above alternate + final reserve"},
    {"limit": "holding fuel burn", "value": "narrowbody 2400 kg/h, widebody 6500 kg/h", "source": "workbook",
     "note": "by gate size class of the aircraft"},
    {"limit": "contingency fuel 5% of trip", "value": "not modelled", "source": "assumption",
     "note": "no trip fuel in the data; holding fuel is checked against extra fuel instead"},
    {"limit": "spare fuel on arrival", "value": "45 min of holding (workbook default) unless the wave states it",
     "source": "workbook", "note": "fuel.extra_min of each flight as built by the wave builder"},
    {"limit": "flight crew max duty", "value": "13 h from report time", "source": "workbook",
     "note": "report = scheduled departure - 60 min (assumption); PLT-CREW-DUTY: in-block (landing slot + ROT + "
     "taxi-in, or the alternate arrival for a diversion) must be within the duty end. A flight whose earliest "
     "possible in-block (minimum over the open runways of the runway-dependent earliest landing + ROT + unimpeded "
     "routed taxi to the plan's gate, the scheduled gate for a diversion; replaces v2's ETA + taxi, and far taxi for "
     "diversions) is already past the duty end is an unavoidable duty bust (data): reported in the check detail, "
     "never a violation. Otherwise the rule fails iff the plan's arrival is past the duty end. Skipped when the "
     "departure time is unknown"},
    {"limit": "ground crew shift / overtime", "value": "8 h shift, overtime cost x1.5", "source": "workbook",
     "note": "overtime is allowed (GND-CREW-DUTY reports it, never vetoes) and costs 1.5x in Ground utility; "
     "changed from v1 where any overtime was illegal"},
    {"limit": "gate change notice", "value": ">= 20 min before arrival", "source": "workbook",
     "note": "decision time is T0: a flight landing before T+20 keeps its scheduled gate; the remote stand "
     "is exempt (assumption, always staffed)"},
    {"limit": "runway capacity", "value": "arrivals per runway per rolling 60 min <= max_runway_ops_per_hour / "
     "active_runways", "source": "workbook", "note": "applied to every real physical runway (strip); "
     "fixed queue arrivals count but only wave flights can be vetoed; arrivals only; departures not "
     "modelled (relaxation)"},
    {"limit": "runways and wind", "value": "real runway ids, headings and METAR", "source": "real data",
     "note": "real runways are used instead of the workbook active_runways count, which is only the divisor "
     "of the capacity cap; crosswind/tailwind limits from FAA Order 8400.9 / AFM"},
    {"limit": "remote stand", "value": "1 stand, 2 positions", "source": "assumption",
     "note": "not in workbook; spare_gates from workbook remain binding; exempt from gate notice"},
    {"limit": "taxi route", "value": "fastest route on the OpenStreetMap taxiway graph, runway exit to the gate's "
     "stand; in-block = landing slot + ROT + taxi", "source": "real data",
     "note": "taxiways, stands, runway exits and holding positions from OSM (ODbL), or the X-Plane Scenery Gateway (GPL) where OSM fails the quality gate; gates bound to stands (ref, "
             "then nearest within 150 m, else a generic stand); 20 kt, 10 kt on taxilanes and turns > 30 deg, +30 s to "
             "park (assumptions); exit = first forward exit past the required roll (narrow 1,500 m, wide 2,000 m, "
             "x1.15 wet), exits whose taxi network holds no stand skipped; ROT = 2d / (v_td + v_exit), v_exit 25 kt "
             "(exit <= 45 deg) or 12 kt; ATC-TAXI-ROUTE fails when no exit of the runway end reaches the stand. "
             "Quality gate per wave: every open runway end in OSM reaches >= 80 % of the wave's bound stands, else "
             "the wave uses the generic estimate (straight line runway midpoint -> gate x1.4 at 15 kt, generic exit at "
             "the required roll and 12 kt; assumption). Runways without coordinates keep v2's 6 min near / 10 min "
             "far. Remote stand: + 10 min bus. No taxiway closures modelled. taxi = unimpeded + the hold-short / "
             "give-way "
             "waits of the plan's hold-short pass (ATC-TAXI-CONFLICT); no ramp or gate waits, so it can sit below BTS "
             "TAXI_IN (FCFS medians on the replays with the 2015 movements, ROT + taxi + waits vs taxi-in reference: "
             "ORD 4.1 / 26.0, MIA 5.0 / 4.0, ATL 4.6 / 16.0, DFW 16.4 / 13.5, DEN 6.4 / 5.5 min; DFW is above "
             "the "
             "reference because three arrivals wait head-on against a 36R departure stream on their fixed route, no "
             "rerouting: see 'departure holding bay'); see the taxi validation (Task 27)"},
    {"limit": "ground node headway", "value": f"{surface.HEADWAY_S:g} s", "source": "assumption",
     "note": "ATC-TAXI-CONFLICT (FAA JO 7110.65 §3-7-2: ground control issues the route and hold-short "
             "instructions): wave flights move along their fixed route (no rerouting) in runway-exit order and wait "
             "at the previous node, or at the hold before a runway's zone, until every node is clear of the fixed "
             "traffic and earlier wave flights by this headway and no edge is in use head-on. Fixed traffic (queue "
             "arrivals, real 2015 departures) is scheduled once per wave and never yields"},
    {"limit": "runway crossing gap", "value": "no landing on the crossed strip from 1.5 min before to 1 min after "
     "the time in its zone; no takeoff within 1 min either side", "source": "assumption",
     "note": "the whole time inside the strip's protected zone (from leaving the hold to past the zone) is checked "
             "against the fixed traffic's landings and takeoffs and every landing of the plan, for the wave flights"},
    {"limit": "fixed crossers vs plan landings", "value": "landing priority; fixed 2015 crossers are not re-timed per "
     "plan", "source": "assumption",
     "note": "the fixed traffic (queue arrivals, real 2015 departures) is scheduled once per wave, before any plan "
             "exists. When a plan landing falls in the crossing gap window of a scheduled fixed crossing of that "
             "strip, a controller would hold the crosser, never the arrival: it does not fail ATC-TAXI-CONFLICT and is "
             "reported as an informational conflict (kind fixed_holds_for_landing: fixed callsign, strip, the hold it "
             "would need, until 1 min after the landing; evidence fixed_holds). The fixed track and its knock-on "
             "effects are not re-timed. A plan landing while a fixed arrival is still on that runway does fail "
             "(same-runway separation, evidence landing_conflicts)"},
    {"limit": "fixed departures vs plan landings", "value": "landing priority; a fixed takeoff less than "
     f"{ground_traffic.LANDING_TAKEOFF_GAP_MIN:g} min before a plan landing on its strip would be held",
     "source": "assumption",
     "note": "the fixed takeoffs are timed once when the wave is built (takeoff spacing, the queue arrivals' "
             "landings, scheduled crossings), never from any wave flight's landing, FCFS or plan. A plan landing "
             "less than the landing / takeoff gap after a fixed takeoff on the same strip does not fail "
             "ATC-TAXI-CONFLICT: a controller would hold the departure. It is an informational conflict (kind "
             "fixed_departure_holds: fixed callsign, strip, the hold until the gap after the landing; evidence "
             "fixed_departure_holds); the fixed departure is not re-timed for it. A fixed takeoff at or after the "
             "landing while the wave flight is still on the runway fails the runway-vacate rule"},
    {"limit": "taxi search horizon", "value": f"{surface.HORIZON_MIN:g} min", "source": "assumption",
     "note": "a wave flight whose total taxi wait exceeds it (or whose route starts inside another strip's zone "
             "during a landing or takeoff) fails ATC-TAXI-CONFLICT; shorter waits never fail, they add to in-block"},
    {"limit": "runway vacate", "value": "clear of the runway before the next landing or takeoff on that strip",
     "source": "assumption",
     "note": "FAA JO 7110.65 §3-10-3 (same-runway separation: the preceding aircraft has taxied off before the next "
             "crosses the threshold). ATC-TAXI-CONFLICT fails when a wave flight's exit clear time (first node past "
             "the exit runway's 90 m zone and off every runway's pavement) is after another flight's landing or "
             "takeoff on that strip, counted from its own landing. No pre-landing margin (unlike the crossing gap): "
             "the same-runway rule sets none, and the independent verifier's runway-vacate check uses the same "
             "window, so every verifier 'still vacating' message on a wave flight is a failing check"},
    {"limit": "landing / takeoff gap on one runway", "value": "1 min", "source": "assumption",
     "note": "mixed mode (fixed departures sharing the arrival runway): a fixed takeoff stays 1 min from "
             "every landing on its strip (the arrival has vacated, or the departure is airborne before the arrival "
             "crosses the threshold). A wave landing against fixed takeoffs on the same strip: a takeoff while the "
             "wave flight is still on the runway fails the runway-vacate rule; a takeoff less than 1 min before the "
             "landing is landing priority (informational fixed_departure_holds, see 'fixed departures vs plan "
             "landings')"},
    {"limit": "departure holding bay", "value": "3 aircraft per holding point, resequenced by takeoff time; "
     "beyond that the queue backs up upstream", "source": "assumption",
     "note": "real ATC resequences departures at the runway (holding bays let an aircraft with an earlier slot pass "
             "one that is waiting; controller ruling T31). A fixed departure that reaches its holding point before "
             "its takeoff waits in the bay until it lines up 30 s before takeoff: off the taxiway (no node "
             "reservation), so it blocks neither a later departure with an earlier takeoff nor the wave flights. "
             "At most 3 wait at once per holding point (bay capacity, assumption); the next one waits upstream on "
             "the taxiway until one leaves (re-scheduled behind it, visible to every later mover and to the wave "
             "flights). Takeoffs are spaced only from takeoffs already settled in pushback order. Replays with the "
             "2015 movements (bay wait median / p95 / max; upstream queue wait median / max; fixed movers over the "
             "30-min horizon): ORD 4.0 / 94.6 / 125.2, 27.8 / 91.7, 41; MIA 10.7 / 17.9 / 38.3, 0 / 8.6, 0; ATL "
             "5.0 / 17.0 / 22.0, 7.6 / 23.3, 0; DFW 5.0 / 18.0 / 39.0, 2.3 / 12.6, 0; DEN 9.3 / 22.4 / 23.2, "
             "0 / 7.0, 0. Known limitation: the ORD replay overloads the fixed traffic: it sends 88 departures to "
             "27R/28C/28R and 41 of them wait past the horizon (the count is in the wave's ground-traffic "
             "provenance). Pushback is WHEELS_OFF - TAXI_OUT, and BTS TAXI_OUT already contains the real queue, so "
             "the bay wait stands for that queue. Wave arrivals giving way to a departure on a shared taxiway (FCFS "
             "plans): DFW 3, other replays 0. Known limitation: the DFW waits (MQ2833 21.0, "
             "AA2441 13.6, AA260 7.6 min, all at node 107) are head-on waits on the arrival's fixed route against a "
             "stream of 36R departures (9-10 departures on its route edges, opposite direction, during each wait); "
             "with no rerouting the arrival waits for a gap, and its ROT + taxi + wait exceeds its own BTS TAXI_IN "
             "(19.3 / 15.0, 13.5 / 9.0, 26.7 / 16.0 min)"},
    {"limit": "runway ends missing from OSM", "value": "generic straight-line taxi", "source": "assumption",
     "note": "open in 2015 but not in today's OSM; taxi uses the straight-line generic estimate"},
    {"limit": "runway hold clearance", "value": f"{surface.RUNWAY_HOLD_CLEARANCE_M:g} m from a crossed runway's "
     f"centreline", "source": "assumption",
     "note": "taxiing aircraft never wait closer than this to a runway they cross, except at an OSM holding position "
             f"off the pavement; on the pavement = within {surface.RUNWAY_ON_PAVEMENT_M:g} m of the centreline"},
    {"limit": "speed reduction cap", "value": f"{MAX_SPEED_REDUCTION_MIN:g} min", "source": "assumption",
     "note": "maximum en-route speed reduction a pilot may use to absorb delay"},
    {"limit": "turnaround / gate occupancy", "value": "airport min_turnaround_min", "source": "workbook",
     "note": "45 min at large hubs, 35 min at medium/small hubs"},
    {"limit": "holding pattern geometry", "value": "AIM 5-3-8: 200 KIAS <= 6,000 ft, 230 KIAS to 14,000 ft, "
     "265 KIAS above; inbound leg 1.0 min <= 14,000 ft, 1.5 min above; right turns unless published",
     "source": "real data", "note": "published (CIFP HF/HM/HA) hold geometry where coded, else a standard hold "
     "at the STAR fix; turn rate = least bank of 3 deg/s, 30 deg, 25 deg; TAS = IAS x (1 + 0.02 x alt/1000); "
     "lap time derived from these; hold_min = laps x lap_min + vector stretch"},
    {"limit": "holding stack", "value": "5 levels, 1,000 ft apart, bottom-first exit", "source": "assumption",
     "note": "1,000 ft vertical spacing per FAA JO 7110.65 §4-5-1, first come first served per §2-1-4; the "
     "5-level depth is our assumption; only flights with >= 1 whole lap enter the stack; ATC-HOLD-STACK fails a "
     "6th simultaneous holder and a later entrant leaving while an earlier one still holds"},
    {"limit": "terminal burn proxy", "value": "workbook holding burn for route extra and holding",
     "source": "assumption", "note": "fuel_kg = (hold_min + route_extra_min) x holding burn (route extra may be "
     "negative, never below zero fuel); PLT-FUEL-RESERVE checks hold_min + max(0, route_extra_min) <= extra fuel"},
    {"limit": "terminal speed profile", "value": "280 KIAS above 10,000 ft, 250 below, 210 from the IAF, "
     "170 at the FAF, 140 kt final", "source": "assumption",
     "note": "converted to TAS for route time T_route; METAR headwind applied on final only; the runway-dependent "
     "earliest landing is t_entry + SR + GD + T_route(runway) with t_entry = ETA - T_route(FCFS runway)"},
    {"limit": "procedures cycle", "value": "FAA CIFP 2611", "source": "real data",
     "note": "current procedures (CIFP 2611) flown over 2015 traffic; not for navigation"},
    {"limit": "arrival runways", "value": "the outboard parallels of the best-wind end (farthest from the terminal), "
     "up to the airport's active runways minus one; wave flights join them", "source": "assumption",
     "note": "BTS has no runway: the queue arrivals land on the parallels of the best-wind end, ranked by distance "
             "from the median of the airport's OSM stands (farthest first), so the inboard parallels nearest the "
             "terminal stay free for departures, the segregated pattern of e.g. ATL west flow (land 26R/27L/28, depart "
             "26L/27R), DTW, SEA and JFK; by headwind then id without stands or runway coordinates. Airports whose "
             "terminal is not between its parallels (DEN) are not modelled well. A wave flight takes an end the queue "
             "lands on before an unused end with the same wind (moves.fcfs_runways)."},
    {"limit": "departure runway", "value": "the flow's free parallel ends plus free non-crossing ends within 60 deg "
     "(segregated), else the arrival runway (mixed mode); earliest expected takeoff", "source": "assumption",
     "note": "fixed ground traffic (real BTS 2015 departures, wheels-off T-30..T+120): the departure candidates are "
             "every open runway end free of arrivals that is parallel to the majority FCFS arrival runway (heading "
             "within 10 deg, same direction, another strip), plus every other free open end within 60 deg of it "
             "with no tailwind whose centreline crosses no arrival strip and no strip already chosen (nearest heading "
             "first; crossing-runway operations are not modelled). Mixed mode (departures share the arrival runway) "
             "iff there is none. Replays: ORD arrivals 32R, departures 27R/28C/28R; DFW arrivals 35C, departures "
             "31L/31R/36L/36R; MIA arrivals 08L, departures 09; ATL arrivals 08L, departures 09L/09R; DEN arrivals "
             "16L, departures 17L/17R. "
             "In pushback order each departure is trial-scheduled (hold-short scheduler, crossing "
             "waits and queues included) to every candidate and takes the earliest expected takeoff: leaving the "
             "holding point, never before WHEELS_OFF, spaced from the strip's other takeoffs (departure takeoff "
             "spacing); a schedule that is not ok, or a takeoff inside an already scheduled crossing's window, ranks "
             "last; ties to the threshold nearest its (seeded, simulated) stand, then runway id. Pushback = "
             "WHEELS_OFF - TAXI_OUT; the departure waits in the holding bay until it lines up 30 s before "
             "takeoff (departure holding bay); takeoff at WHEELS_OFF, later only if taxi waits, takeoff "
             "spacing, a landing on its strip or a scheduled crossing hold it. Known limitation: arrival runways "
             "are v2's FCFS choice "
             "(queue on the two best-wind ends), so e.g. ATL east flow lands on 08L/08R in this model, whereas real "
             "ATL lands on the outboard runways (08L, 09R, 10) and departs from 08R/09L"},
    {"limit": "gate hold", "value": "push back to reach the holding point 5 min before the real wheels-off "
     "(unimpeded taxi), never before the real gate-out", "source": "assumption",
     "note": "fixed departures: BTS TAXI_OUT includes queues and ground stops (up to 161 min at ORD on 4 Jun 2024); "
             "pushing back at the real gate-out stacked them on the taxiways. The metered pushback (the TSAT idea of "
             "A-CDM and surface metering, with the real wheels-off as the target) keeps that time at the stand, shown "
             "as a 'gate hold' wait on the track; the stand is not a wave gate and is not reserved before pushback "
             "either way. Buffer sweep on 10 reference waves (fixed-traffic taxi waits / takeoffs after the real "
             "wheels-off): 3 min 2,068 min / 137, 5 min 2,731 / 119, 8 min 3,984 / 94; without holds 18,949 / 271."},
    {"limit": "departure takeoff spacing", "value": "1 takeoff per minute per runway end", "source": "assumption",
     "note": "a departure's recorded takeoff is the earliest time >= max(WHEELS_OFF, leaving the holding point) "
             "that is 1 min from every other takeoff on the strip and outside every scheduled crossing's gap window "
             "(the departure waits at the holding point: FCFS, the crosser came first); the runway choice uses the "
             "same time; it is spaced only from takeoffs already settled in pushback order and from every landing on "
             "the strip (1 min). It matches the holding-point throughput of the ground scheduler (30 s node headway + "
             "30 s line-up). Replays with the 2015 movements, takeoffs later than WHEELS_OFF (> 0 / > 1 / > 5 min, "
             "max): ORD 39 / 38 / 31 of 88, 36.0; MIA 2 / 0 / 0 of 24, 1.0; ATL 38 / 33 / 18 of 110, 16.0; DFW "
             "4 / 4 / 0 of 119, 4.0; DEN 0 of 24"},
]


def limits_for(wave: Wave) -> list[dict]:
    """LIMITS plus the airport-specific workbook values used for this wave."""
    cap, lim = capacity_per_runway_h(wave)
    return LIMITS + [{"limit": f"{lim.iata} airport limits", "value":
                      f"{lim.size_class}: active_runways {lim.active_runways}, max_runway_ops_per_hour "
                      f"{lim.max_runway_ops_per_hour} -> {cap:g} arrivals/runway/h, min_turnaround "
                      f"{lim.min_turnaround_min} min, gates {lim.gates}, spare gates {lim.spare_gates}; wave models "
                      f"min(gates, 12) gates (+ remote stand)",
                      "source": "workbook",
                      "note": f"real runways at {wave.airport.icao}: "
                              f"{len({strip_of(r.id) for r in wave.airport.runways})} physical "
                              f"({', '.join(r.id for r in wave.airport.runways)})"}]


def weather_factor(wave: Wave) -> float:
    m = wave.metar
    if m is None:
        return 1.0
    if m.surface == "wet" or (m.visibility_sm is not None and m.visibility_sm < WET_VIS_SM):
        return WEATHER_FACTOR_BAD
    return 1.0


@functools.lru_cache(maxsize=4096)   # pure, called ~400k times a negotiation
def reciprocal(runway_id: str) -> str | None:
    """Opposite end of a runway designator: 08L -> 26R, 09C -> 27C, 36 -> 18. None if unparsable."""
    m = _RWY_RE.match(runway_id or "")
    if not m:
        return None
    num = (int(m.group(1)) + 18 - 1) % 36 + 1
    side = {"L": "R", "R": "L"}.get(m.group(2), m.group(2))
    return f"{num:02d}{side}"


@functools.lru_cache(maxsize=4096)   # pure, called ~400k times a negotiation
def strip_of(runway_id: str) -> tuple[str, ...]:
    """Physical runway (strip) key: sorted tuple of both ends, e.g. ('08L', '26R')."""
    rec = reciprocal(runway_id)
    return tuple(sorted({runway_id, rec})) if rec else (runway_id,)


def strip_label(runway_id: str) -> str:
    return "/".join(strip_of(runway_id))


def has_slot(a) -> bool:
    return a.slot_min is not None and isinstance(a.slot_min, (int, float)) and math.isfinite(a.slot_min)


def validate_structure(wave: Wave, plan: JointPlan) -> None:
    """Raise ValueError unless the plan has exactly one assignment per wave flight."""
    want = [f.callsign for f in wave.flights]
    got = [a.callsign for a in plan.assignments]
    cnt = Counter(got)
    missing = sorted(set(want) - set(got))
    unknown = sorted(set(got) - set(want))
    dup = sorted(c for c, n in cnt.items() if n > 1)
    if missing or unknown or dup:
        raise ValueError(f"plan callsigns must match wave flights exactly: missing={missing} "
                         f"unknown={unknown} dup={dup}")


def strip_sequence(wave: Wave, plan: JointPlan, runway: str) -> list[tuple[float, str, str, bool, str]]:
    """All landings on the strip of `runway` (both ends, queue included) as
    (slot, callsign, wake, is_wave_flight, runway_end), sorted by time (ties: queue first, callsign)."""
    key = strip_of(runway)
    seq = [(float(q.slot_min), q.callsign, q.wake, False, q.runway) for q in wave.queue
           if strip_of(q.runway) == key]
    wake = {f.callsign: f.wake for f in wave.flights}
    for a in plan.assignments:
        if a.action == "land" and a.runway and has_slot(a) and strip_of(a.runway) == key:
            seq.append((float(a.slot_min), a.callsign, wake.get(a.callsign, "LARGE"), True, a.runway))
    seq.sort(key=lambda x: (x[0], x[3], x[1]))
    return seq


def runway_sequence(wave: Wave, plan: JointPlan, runway: str) -> list[tuple[float, str, str, bool]]:
    """Landings on the strip of `runway` as (slot, callsign, wake, is_wave_flight)."""
    return [x[:4] for x in strip_sequence(wave, plan, runway)]


def direction_conflicts(wave: Wave, plan: JointPlan) -> dict[str, str]:
    """callsign -> reason for wave flights landing on the non-dominant end of a strip that is also
    used in the opposite direction. Dominant end: the queue's end if the (fixed) queue uses exactly
    one end of the strip; otherwise the end with the most landings (ties: lower runway id)."""
    by_strip: dict[tuple, list[tuple[str, str, bool]]] = {}
    for q in wave.queue:
        by_strip.setdefault(strip_of(q.runway), []).append((q.runway, q.callsign, False))
    for a in plan.assignments:
        if a.action == "land" and a.runway:
            by_strip.setdefault(strip_of(a.runway), []).append((a.runway, a.callsign, True))
    out: dict[str, str] = {}
    for key, uses in by_strip.items():
        ends = {u[0] for u in uses}
        if len(ends) < 2:
            continue
        q_ends = {u[0] for u in uses if not u[2]}
        if len(q_ends) == 1:
            dom = next(iter(q_ends))
        else:
            n = Counter(u[0] for u in uses)
            dom = min(ends, key=lambda e: (-n[e], e))
        ex = next(u for u in uses if u[0] == dom)
        who = f"{'wave' if ex[2] else 'queue'} landing {ex[1]} {dom}"
        for end, cs, is_wave in uses:
            if is_wave and end != dom:
                out[cs] = f"opposite-direction use of strip {'/'.join(key)} ({who})"
    return out


def _wake_checks(wave: Wave, plan: JointPlan) -> dict[str, RuleCheck]:
    fac = weather_factor(wave)
    out: dict[str, RuleCheck] = {}
    strips = {strip_of(a.runway): a.runway for a in plan.assignments
              if a.action == "land" and a.runway and has_slot(a)}
    for key, rw in strips.items():
        seq = strip_sequence(wave, plan, rw)
        sl = "/".join(key)
        for i, (t, cs, wk, is_wave, end) in enumerate(seq):
            if not is_wave:
                continue
            problems, ev = [], {"runway": end, "strip": sl, "slot_min": t, "wake": wk, "weather_factor": fac}
            if i > 0:
                lt, lcs, lwk, _, lend = seq[i - 1]
                need = round(wake_gap_min(lwk, wk) * fac, 2)
                gap = round(t - lt, 2)
                ev.update({"leader": lcs, "leader_wake": lwk, "leader_runway": lend,
                           "gap_behind_min": gap, "required_behind_min": need})
                if gap < need:
                    problems.append(f"{gap:g} min behind {lcs} ({lwk}, {lend}), need {need:g}")
            if i + 1 < len(seq) and not seq[i + 1][3]:
                # follower is a fixed queue arrival: this wave flight owns that gap
                ft, fcs, fwk, _, fend = seq[i + 1]
                need = round(wake_gap_min(wk, fwk) * fac, 2)
                gap = round(ft - t, 2)
                ev.update({"follower": fcs, "follower_wake": fwk, "follower_runway": fend,
                           "gap_ahead_min": gap, "required_ahead_min": need})
                if gap < need:
                    problems.append(f"{gap:g} min ahead of queue arrival {fcs} ({fwk}, {fend}), need {need:g}")
            where = f"{cs} on {end} (strip {sl}) at T{t:+g}"
            detail = (f"{where}: " + ("; ".join(problems) if problems else "wake separation met")
                      + f" (weather factor x{fac:g})")
            out[cs] = RuleCheck("ATC-WAKE-SEP", "ATC", not problems, detail, SRC_WAKE, ev, cs)
    return out


def _gate_free_checks(wave: Wave, plan: JointPlan, mm: dict[str, FlightMetrics]) -> dict[str, RuleCheck]:
    out: dict[str, RuleCheck] = {}
    turn = turnaround_min(wave)
    gates = {g.id: g for g in wave.gates}
    landing = [a for a in plan.assignments if a.action == "land"]
    for a in landing:
        g = gates.get(a.gate or "")
        ib = mm[a.callsign].in_block_min
        if g is None or ib is None or not math.isfinite(ib):
            out[a.callsign] = RuleCheck("GND-GATE-FREE", "GROUND", False,
                                        f"{a.callsign}: unknown gate {a.gate!r} or no in-block time", SRC_OPS,
                                        {"gate": str(a.gate)}, a.callsign)
            continue
        end = ib + turn
        ev = {"gate": g.id, "need_from_min": ib, "need_to_min": round(end, 1), "turnaround_min": turn}
        if g.remote:
            # RS has REMOTE_STAND_POSITIONS positions: same overlap test as a gate, and only the later
            # arrival of an over-subscribed set fails
            earlier = []
            for o in landing:
                if o is a or o.gate != g.id:
                    continue
                oib = mm[o.callsign].in_block_min
                if oib is not None and math.isfinite(oib) and oib < end and ib < oib + turn and \
                        (oib, o.callsign) < (ib, a.callsign):
                    earlier.append(o.callsign)
            ok = len(earlier) < REMOTE_STAND_POSITIONS
            ev.update({"positions": REMOTE_STAND_POSITIONS, "overlapping": ", ".join(earlier) or "none"})
            out[a.callsign] = RuleCheck(
                "GND-GATE-FREE", "GROUND", ok,
                f"Remote stand {g.id} ({REMOTE_STAND_POSITIONS} positions) "
                + (f"free for {a.callsign} T{ib:+g}..T{end:+g}" if ok else
                   f"full T{ib:+g}..T{end:+g}: {', '.join(earlier)} already on it"), SRC_OPS, ev, a.callsign)
            continue
        clash = None
        for b in g.busy:
            if b["from_min"] < end and ib < b["to_min"]:
                clash = (b.get("label", "occupied"), b["from_min"], b["to_min"])
                break
        if clash is None:
            for o in landing:
                if o is a or o.gate != g.id:
                    continue
                oib = mm[o.callsign].in_block_min
                if oib is not None and oib < end and ib < oib + turn:
                    clash = (o.callsign, oib, oib + turn)
                    break
        if clash:
            ev.update({"conflict": str(clash[0]), "conflict_from_min": clash[1], "conflict_to_min": round(clash[2], 1)})
            detail = (f"Gate {g.id} occupied by {clash[0]} T{clash[1]:+g}..T{clash[2]:+g}, "
                      f"{a.callsign} needs T{ib:+g}..T{end:+g}")
        else:
            detail = f"Gate {g.id} free T{ib:+g}..T{end:+g}"
        out[a.callsign] = RuleCheck("GND-GATE-FREE", "GROUND", clash is None, detail, SRC_OPS, ev, a.callsign)
    return out


def _capacity_checks(wave: Wave, plan: JointPlan) -> dict[str, RuleCheck]:
    """ATC-RWY-CAPACITY: landings on the flight's physical runway in the rolling 60 min ending at its
    slot (queue + wave, itself included) <= workbook cap. Only wave flights can fail; the fixed queue
    counts toward the load."""
    per_h, lim = capacity_per_runway_h(wave)
    allowed = int(math.floor(per_h + 1e-9))
    out: dict[str, RuleCheck] = {}
    seqs: dict[tuple, list] = {}
    for a in plan.assignments:
        if a.action != "land":
            continue
        if not (a.runway and has_slot(a)):
            out[a.callsign] = RuleCheck("ATC-RWY-CAPACITY", "ATC", False, f"{a.callsign}: no runway/slot",
                                        SRC_CAP, {"runway": str(a.runway), "slot_min": str(a.slot_min)},
                                        a.callsign)
            continue
        key = strip_of(a.runway)
        if key not in seqs:
            seqs[key] = strip_sequence(wave, plan, a.runway)
        t = float(a.slot_min)
        n = sum(1 for x in seqs[key] if t - 60.0 < x[0] <= t)
        ok = n <= allowed
        out[a.callsign] = RuleCheck(
            "ATC-RWY-CAPACITY", "ATC", ok,
            f"{a.callsign}: {n} landings on runway {'/'.join(key)} in the 60 min to T{t:+g} "
            f"{'<=' if ok else '>'} cap {allowed} ({lim.max_runway_ops_per_hour} ops/h / "
            f"{lim.active_runways} active runways, {lim.size_class})",
            SRC_CAP, {"runway": a.runway, "strip": "/".join(key), "landings_60min": n, "cap_per_runway_h": allowed,
                      "max_runway_ops_per_hour": lim.max_runway_ops_per_hour,
                      "active_runways": lim.active_runways}, a.callsign)
    return out


def _divert_check(f, cs: str) -> RuleCheck:
    extra, alt, alt_min, fin = fuel_info(f)
    ev = {"alternate": str(alt) if alt else "missing",
          "alternate_min": alt_min if alt_min is not None else "missing", "final_reserve_min": fin}
    if not alt:
        return RuleCheck("PLT-FUEL-RESERVE", "PILOT", False,
                         f"{cs} cannot divert: no alternate aerodrome in fuel data", SRC_FUEL, ev, cs)
    if alt_min is None or alt_min <= 0:
        return RuleCheck("PLT-FUEL-RESERVE", "PILOT", False,
                         f"{cs} cannot divert to {alt}: alternate fuel time "
                         f"{'missing' if alt_min is None else f'{alt_min:g} min'} is not a valid planned value",
                         SRC_FUEL, ev, cs)
    return RuleCheck("PLT-FUEL-RESERVE", "PILOT", True,
                     f"{cs} diverts to {alt} ({alt_min:g} min), covered by alternate fuel + {fin:g} min final reserve",
                     SRC_FUEL, ev, cs)


def stack_eval(holders: list[tuple[str, float, float, int]], base_ft: int) -> dict[str, dict]:
    """ATC-HOLD-STACK at one physical hold fix. `holders` are (callsign, entry_min, exit_min, level_ft) of
    the flights flying >= 1 lap there. Order of entry is (entry_min, callsign); an exit at the same minute
    as an entry happens first. Per callsign returns {ok, holders, earlier_holding, why}:
      * holders: flights in the stack when it enters (itself included); > 5 (holding.STACK_LEVELS) fails;
      * earlier_holding: earlier entrants still holding when it leaves (FIFO / bottom-first exit); any fails;
      * a level below the hold base fails."""
    order = sorted(holders, key=lambda h: (h[1], h[0]))
    out: dict[str, dict] = {}
    for i, (cs, t_in, t_out, level) in enumerate(order):
        earlier = order[:i]
        n = 1 + sum(1 for o in earlier if o[2] > t_in + 1e-9)
        still = [o[0] for o in earlier if o[2] > t_out + 1e-9]
        why = []
        if n > holding.STACK_LEVELS:
            why.append(f"{n} simultaneous holders > {holding.STACK_LEVELS} levels")
        if still:
            why.append(f"leaves at T{t_out:+.1f} while earlier entrant {', '.join(still)} still holds "
                       f"(not bottom-first)")
        if level < base_ft:
            why.append(f"level {level} ft below hold base {base_ft} ft")
        out[cs] = {"ok": not why, "holders": n, "earlier_holding": still, "why": why}
    return out


def _stack_checks(wave: Wave, plan: JointPlan) -> dict[str, RuleCheck]:
    """ATC-HOLD-STACK for every landing wave flight, grouped by PHYSICAL hold fix (`trajectory.stack_key` of
    the route's hold, never the name). Entry/exit/level come from the plan's trajectories; a fix the
    trajectory layer flagged (`stack_violations`) with no failing flight here fails all its holders (closed)."""
    ts = trajectory.build(wave, plan)
    rs = route.routes_for_wave(wave)
    holds = trajectory.wave_holds(wave)
    groups: dict[tuple, list] = {}
    for t in ts.air:
        if t.wave and t.laps >= 1:
            r = rs.get((t.callsign, t.runway))
            if r is not None:
                groups.setdefault(trajectory.stack_key(r.hold), []).append(t)
    # one key set for both layers (parked T14): the labels the trajectory set gave its stacks
    missing = set(groups) - set(ts.stack_labels)
    if missing:          # both layers group by stack_key over the same routes: a mismatch is a bug, never relabelled
        raise RuntimeError(f"stack keys {sorted(missing)} have no TrajectorySet stack label")
    labels = {k: ts.stack_labels[k] for k in groups}
    out: dict[str, RuleCheck] = {}
    for key, ts_ in groups.items():
        label = labels[key]
        spec = holds[key]
        rows = [(t.callsign, t.hold["entry_min"], t.hold["exit_min"], t.hold["level_ft"])
                for t in ts_ if t.hold is not None]
        res = stack_eval(rows, spec.base_ft)
        for t in ts_:
            if t.hold is None:
                res[t.callsign] = {"ok": False, "holders": 0, "earlier_holding": [],
                                   "why": [f"{t.laps} laps but no stack level assigned"]}
        flagged = ts.stack_violations.get(label)
        if flagged and all(v["ok"] for v in res.values()):
            for v in res.values():
                v["ok"] = False
                v["why"].append("trajectory stack violation: " + "; ".join(flagged))
        all_cs = ", ".join(c for c, *_ in sorted(rows, key=lambda h: (h[1], h[0])))
        for t in ts_:
            v, h = res[t.callsign], t.hold or {}
            lvl = h.get("level_ft")
            ev = {"hold_fix": label, "laps": t.laps, "level_ft": lvl if lvl is not None else "none",
                  "holders": v["holders"], "levels": holding.STACK_LEVELS, "base_ft": spec.base_ft,
                  "lap_min": round(t.lap_min, 2), "holders_at_fix": all_cs}
            if h:
                ev.update({"entry_min": round(h["entry_min"], 2), "exit_min": round(h["exit_min"], 2)})
            if v["earlier_holding"]:
                ev["earlier_holding"] = ", ".join(v["earlier_holding"])
            where = (f"{t.callsign}: {t.laps} laps at {label} ({t.lap_min:.2f} min/lap)"
                     + (f", level {lvl} ft, T{h['entry_min']:+.1f}..T{h['exit_min']:+.1f}" if h else ""))
            detail = (where + ": " + "; ".join(v["why"]) if not v["ok"] else
                      where + f": {v['holders']} of {holding.STACK_LEVELS} levels in use on entry, "
                      f"leaves bottom-first")
            out[t.callsign] = RuleCheck("ATC-HOLD-STACK", "ATC", v["ok"], detail, SRC_STACK, ev, t.callsign)
    for t in ts.air:
        if t.wave and t.callsign not in out:
            if t.lap_min is None:
                det = f"{t.callsign}: no route geometry for {t.runway}: holding not modelled, not in a stack"
            elif t.laps >= 1:
                det = f"{t.callsign}: {t.laps} laps but no hold fix route; not in a stack"
            else:
                det = (f"{t.callsign}: no whole holding lap"
                       + (f" (delay absorbed by {t.vector_min:.1f} min vector stretch)" if t.vector_min > 0 else "")
                       + ", not in a stack")
            out[t.callsign] = RuleCheck("ATC-HOLD-STACK", "ATC", t.laps < 1 or t.lap_min is None, det, SRC_STACK,
                                        {"hold_fix": "none", "laps": t.laps, "level_ft": "none", "holders": 0,
                                         "levels": holding.STACK_LEVELS}, t.callsign)
    return out


def _earliest_in_block(wave: Wave, f, gate: str | None) -> tuple[float, str | None, float]:
    """Earliest possible in-block (no speed reduction, no ground delay) minimised over open runways:
    min over open r of trajectory.earliest(f, r) + ROT + unimpeded taxi from r to `gate`
    (`surface.min_unimpeded_taxi`; v2 near/far taxi without runway geometry). Returns (in-block, runway, the
    minimum over open runways of ROT + unimpeded taxi). Without an open runway: ETA + far taxi. Plan-independent:
    memoised on the wave object (like its key and routes) on (flight, gate)."""
    memo = wave.__dict__.setdefault("_earliest_in_block", {})
    k = (f.callsign, f.eta_earliest_min, gate)
    hit = memo.get(k)
    if hit is None:
        hit = memo[k] = _earliest_in_block_calc(wave, f, gate)
    return hit


def _earliest_in_block_calc(wave: Wave, f, gate: str | None) -> tuple[float, str | None, float]:
    best = (f.eta_earliest_min + TAXI_FAR_MIN, None)
    taxi = math.inf
    for r in wave.airport.runways:
        if r.open:
            tx = surface.min_unimpeded_taxi(wave, f.callsign, r.id, gate)
            taxi = min(taxi, tx)
            t = trajectory.earliest(wave, f.callsign, r.id) + tx
            if best[1] is None or t < best[0]:
                best = (t, r.id)
    return best[0], best[1], (taxi if math.isfinite(taxi) else TAXI_FAR_MIN)


def _taxi_route_check(wave: Wave, a, f, g, cs: str) -> RuleCheck:
    """ATC-TAXI-ROUTE (spec §6.6): a connected taxi route from an exit of the assigned runway end to the gate's stand.
    Fails iff the surface tier is real and no exit of the end reaches the stand. Generic tier (end missing from OSM,
    generic wave, synth gate) and the no-geometry tier pass and say which estimate applies. Plan-independent: memoised
    on the wave object on (flight, runway, gate); the check is shared, treat it as read-only."""
    memo = wave.__dict__.setdefault("_taxi_route_check", {})
    k = (cs, a.runway, a.gate)
    hit = memo.get(k)
    if hit is None:
        hit = memo[k] = _taxi_route_calc(wave, a, f, g, cs)
    return hit


def _taxi_route_calc(wave: Wave, a, f, g, cs: str) -> RuleCheck:
    tx = surface.taxi_info(wave, a.runway, a.gate, f.gate_size)
    p = tx.path
    route_s = " ".join(dict.fromkeys(n for n in p.names if n)) if p is not None else ""
    cross = sorted(dict.fromkeys(st for _, st in p.crossings)) if p is not None else []
    ex = tx.exit_label or ("generic" if tx.tier == "generic" else
                           f"{a.runway} exit {tx.exit['dist_m']:.0f} m" if tx.tier == "real" and tx.exit else "none")
    ev = {"runway": str(a.runway), "exit": ex,
          "gate": str(a.gate), "stand": tx.stand, "route": route_s or "none",
          "crossings": ", ".join(cross) if cross else "none", "unimpeded_min": round(tx.taxi_min, 2),
          "tier": tx.tier}
    if tx.rot_s is not None:
        ev["rot_s"] = round(tx.rot_s, 1)
    dest = "remote stand" if g is not None and g.remote else f"gate {a.gate}"
    if tx.no_path:
        return RuleCheck("ATC-TAXI-ROUTE", "ATC", False, f"{cs}: {tx.no_path}", SRC_TAXI_OSM, ev, cs)
    if tx.tier == "real":
        det = (f"{cs}: taxi {a.runway} exit {tx.exit_label} -> {dest} (stand {tx.stand}) via {route_s or 'unnamed'}"
               f", {tx.taxi_min:.1f} min unimpeded" + (f", crosses {', '.join(cross)}" if cross else ""))
    elif tx.tier == "generic":
        g_ = surface._geo(wave).graph
        why = ("no OSM surface" if g_ is None else
               "runway end not in today's OSM" if a.runway not in g_.exits else
               "gate not bound to an OSM stand" if surface.stand_node_for(wave, a.gate) is None else
               "surface below the quality gate")
        det = f"{cs}: taxi {a.runway} -> {dest} {tx.taxi_min:.1f} min, straight-line estimate ({why})"
    else:
        det = f"{cs}: taxi route {a.runway} -> {dest} available ({tx.taxi_min:g} min); no runway geometry"
    return RuleCheck("ATC-TAXI-ROUTE", "ATC", True, det, tx.source, ev, cs)


def _taxi_conflict_check(ts, cs: str) -> RuleCheck:
    """ATC-TAXI-CONFLICT (spec §6.4, §6.6) from the plan's hold-short pass (`trajectory.GroundOutcome`). Fails iff
    the scheduler could not give the flight a conflict-free taxi within the 30-min horizon (`Scheduled.ok` false), or
    it is still on the runway it exits (exit_clear_min) at another flight's landing or takeoff on that strip, or it
    lands while a fixed arrival is still on that runway. Waits alone never fail it: they cost in-block time. A fixed
    crosser inside its landing's gap window holds for it (landing priority): informational, evidence `fixed_holds`.
    Not modelled (pass) without a routed OSM taxi."""
    o = ts.outcomes.get(cs)
    if o is None:
        return RuleCheck("ATC-TAXI-CONFLICT", "ATC", True, f"{cs}: no valid runway/slot, no taxi scheduled",
                         SRC_TAXI_CONFLICT, {"waits": 0, "wait_min": 0.0, "crossings": "none",
                                             "exit_clear_min": "none", "next_landing_min": "none"}, cs)
    nx = o.next_event
    ev = {"waits": len(o.waits), "wait_min": round(o.wait_min, 2),
          "crossings": ", ".join(o.crossings) if o.crossings else "none",
          "exit_clear_min": round(o.exit_clear_min, 2) if o.exit_clear_min is not None else "none",
          "next_landing_min": round(nx[0], 2) if nx else "none",
          "next_event": f"{nx[1]} {nx[2]}" if nx else "none", "tier": o.tier}
    if o.hold_short_strip:
        ev["hold_short_strip"] = o.hold_short_strip
    if o.blocking:
        ev["blocking"] = ", ".join(f"{k} {lab} T{t:+.2f}" for t, k, lab in o.blocking)
        ev["blocked_by"] = ", ".join(lab for _, _, lab in o.blocking)
    if o.landing_conflicts:
        ev["landing_conflicts"] = "; ".join(o.landing_conflicts)
    if o.fixed_holds:
        ev["fixed_holds"] = len(o.fixed_holds)
        ev["fixed_holds_for_landing"] = ", ".join(f"{h['fixed']} {h['hold_min']:.1f} min" for h in o.fixed_holds)
    if o.fixed_departures:
        ev["fixed_departure_holds"] = ", ".join(f"{h['fixed']} {h['hold_min']:.1f} min" for h in o.fixed_departures)
    if not o.ok:
        ev["reason"] = o.reason or "not scheduled"
    if not o.modelled and o.passed:
        ev["tier"] = o.tier
    return RuleCheck("ATC-TAXI-CONFLICT", "ATC", o.passed, trajectory.detail(o), SRC_TAXI_CONFLICT, ev, cs)


def _duty_check(wave: Wave, f, a, mm, cs: str) -> RuleCheck | None:
    """PLT-CREW-DUTY: in-block (landing slot + taxi-in) or alternate arrival within the flight crew duty end.
    Fails iff the plan's arrival exceeds the duty end AND the earliest possible in-block over the open
    runways (`_earliest_in_block`, at the plan's gate; the scheduled gate for a diversion) was still within it
    (the plan caused the bust). If even that busts the duty it is an unavoidable bust in the data: reported,
    never a violation. None when the duty end is unknown."""
    end = getattr(f, "crew_duty_end_min", None)
    if end is None:
        return None
    if a.action == "divert":
        _, alt, alt_min, _ = fuel_info(f)
        arr = f.eta_earliest_min + float(alt_min or 0.0)
        what = f"diverts, alternate {alt or '?'} arrival T{arr:+g}"
    else:
        arr = mm[cs].in_block_min
        if arr is None or not math.isfinite(arr) or not has_slot(a):
            return RuleCheck("PLT-CREW-DUTY", "PILOT", False, f"{cs}: no in-block time to check crew duty",
                             SRC_DUTY, {"crew_duty_end_min": end}, cs)
        what = f"in-block T{arr:+g}"
    earliest, e_rwy, taxi = _earliest_in_block(wave, f, a.gate if a.action == "land" else f.scheduled_gate)
    unavoidable = earliest > end + 1e-9
    ok = arr <= end + 1e-9 or unavoidable
    if arr <= end + 1e-9:
        tail = f"<= crew duty end T{end:+g}"
    elif unavoidable:
        tail = (f"> crew duty end T{end:+g}: unavoidable duty bust (data), earliest landing in-block "
                f"T{earliest:+.1f} (on {e_rwy or 'any runway'}) is already past it")
    else:
        tail = (f"> crew duty end T{end:+g} (earliest possible in-block T{earliest:+.1f} on "
                f"{e_rwy or 'any runway'} would fit)")
    return RuleCheck("PLT-CREW-DUTY", "PILOT", ok, f"{cs}: {what} {tail}", SRC_DUTY,
                     {"arrival_min": round(arr, 1), "crew_duty_end_min": round(end, 1),
                      "earliest_arrival_min": round(earliest, 1), "earliest_runway": e_rwy or "none",
                      "taxi": round(taxi, 2), "unavoidable_bust": "yes" if unavoidable else "no"}, cs)


def check(wave: Wave, plan: JointPlan, mm: dict[str, FlightMetrics] | None = None) -> list[RuleCheck]:
    """All rules for all flights. Raises ValueError if the plan does not have exactly one
    assignment per wave flight. Memoised on (wave key, plan id) when scored on the plan's own metrics (the
    memoised `metrics_map`); each call gets its own list, the checks in it are shared (read-only)."""
    validate_structure(wave, plan)
    own = metrics_map(wave, plan)
    if mm is not None and mm is not own:
        return _check(wave, plan, mm)
    k = (route.wave_key(wave), plan.id)
    hit = _CHECKS.get(k)
    if hit is None:
        hit = _check(wave, plan, own)
        trajectory._put(_CHECKS, k, hit, CHECK_CACHE_MAX)   # FIFO eviction under the shared lock (worker threads)
    return list(hit)


CHECK_CACHE_MAX = 512   # ~35 KB a plan (~18 MB); a plan is re-checked soon after its first check
_CHECKS: dict[tuple, list[RuleCheck]] = {}


def _check(wave: Wave, plan: JointPlan, mm: dict[str, FlightMetrics]) -> list[RuleCheck]:
    rwys = {r.id: r for r in wave.airport.runways}
    gates = {g.id: g for g in wave.gates}
    flights = {f.callsign: f for f in wave.flights}
    wake = _wake_checks(wave, plan)
    gfree = _gate_free_checks(wave, plan, mm)
    opposite = direction_conflicts(wave, plan)
    cap = _capacity_checks(wave, plan)
    stack = _stack_checks(wave, plan)
    ts = trajectory.build(wave, plan)
    rs = route.routes_for_wave(wave)
    turn = turnaround_min(wave)
    metar = wave.metar
    out: list[RuleCheck] = []
    for a in plan.assignments:
        f = flights[a.callsign]
        m = mm[a.callsign]
        cs = a.callsign
        if a.action == "divert":
            out.append(_divert_check(f, cs))
            d = _duty_check(wave, f, a, mm, cs)
            if d:
                out.append(d)
            continue

        # ATC-RWY-OPEN (exists, open, not used against the flow on the same strip)
        rw = rwys.get(a.runway or "")
        if rw is None:
            out.append(RuleCheck("ATC-RWY-OPEN", "ATC", False, f"{cs}: runway {a.runway!r} does not exist at "
                                 f"{wave.airport.icao}", SRC_RWY, {"runway": str(a.runway)}, cs))
        else:
            probs = []
            if not rw.open:
                probs.append(f"closed ({rw.closed_reason or 'no reason given'})")
            if cs in opposite:
                probs.append(opposite[cs])
            ev = {"runway": rw.id, "open": "yes" if rw.open else "no", "strip": strip_label(rw.id)}
            if cs in opposite:
                ev["direction_conflict"] = opposite[cs]
            out.append(RuleCheck("ATC-RWY-OPEN", "ATC", not probs,
                                 f"{cs}: runway {rw.id} " + ("; ".join(probs) if probs else "open, landing with the flow"),
                                 SRC_RWY, ev, cs))

        # ATC-WAKE-SEP
        out.append(wake.get(cs) or RuleCheck("ATC-WAKE-SEP", "ATC", False, f"{cs}: no valid slot assigned",
                                             SRC_WAKE, {"runway": str(a.runway), "slot_min": str(a.slot_min)}, cs))

        # ATC-HOLD-STACK (only flights with >= 1 whole lap are in a stack)
        out.append(stack.get(cs) or RuleCheck("ATC-HOLD-STACK", "ATC", True,
                                              f"{cs}: no valid runway/slot, no holding drawn", SRC_STACK,
                                              {"hold_fix": "none", "laps": 0, "level_ft": "none", "holders": 0,
                                               "levels": holding.STACK_LEVELS}, cs))

        # ATC-TAXI-ROUTE
        g = gates.get(a.gate or "")
        out.append(_taxi_route_check(wave, a, f, g, cs))

        # ATC-TAXI-CONFLICT (the plan's hold-short pass)
        out.append(_taxi_conflict_check(ts, cs))

        # ATC-WIND-LIMITS
        if metar is None:
            out.append(RuleCheck("ATC-WIND-LIMITS", "ATC", True, f"{cs}: no METAR available, wind not assessed",
                                 "FAA Order 8400.9 §6d", {"metar": "unavailable"}, cs))
        elif rw is not None:
            hw, xw, tw = wind.components(metar.wind_dir_deg, metar.wind_kt, metar.gust_kt, rw.heading_true)
            lxw, ltw, src = wind.limits(metar.surface, f.aircraft_type)
            ok = wind.within_limits(xw, tw, lxw, ltw)
            ev = {"metar": metar.raw, "runway": rw.id, "surface": metar.surface, "headwind_kt": hw,
                  "crosswind_kt": xw, "tailwind_kt": tw, "limit_crosswind_kt": lxw, "limit_tailwind_kt": ltw,
                  "aircraft_type": f.aircraft_type or "unknown"}
            why = []
            if xw > lxw:
                why.append(f"crosswind {xw:g} kt > limit {lxw:g} kt")
            if tw > (3.0 if ltw == 0 else ltw):  # wind.within_limits: calm 0-3 kt is not a tailwind
                why.append(f"tailwind {tw:g} kt > limit {ltw:g} kt")
            detail = (f"{cs} ({f.aircraft_type or 'unknown type'}) on {rw.id}: "
                      + ("; ".join(why) if not ok else f"crosswind {xw:g}/{lxw:g} kt, tailwind {tw:g}/{ltw:g} kt within limits")
                      + f" [METAR {metar.raw}]")
            out.append(RuleCheck("ATC-WIND-LIMITS", "ATC", ok, detail, src, ev, cs))
        else:
            out.append(RuleCheck("ATC-WIND-LIMITS", "ATC", False, f"{cs}: unknown runway {a.runway!r}",
                                 "FAA Order 8400.9 §6d", {"metar": metar.raw, "runway": str(a.runway)}, cs))

        # PLT-EARLIEST-ETA (incl. absorption bounds): runway-dependent earliest landing (spec §6.2)
        slot_ok = has_slot(a)
        slot = float(a.slot_min) if slot_ok else float("nan")
        sr, gd = a.speed_reduction_min, a.ground_delay_min
        earliest = trajectory.earliest(wave, cs, a.runway, sr, gd)
        rex = trajectory.earliest(wave, cs, a.runway) - f.eta_earliest_min   # T_route(r) - T_route(nominal)
        r = rs.get((cs, a.runway)) if a.runway else None
        t_entry = trajectory.entry_time(wave, cs) if r is not None else None
        if r is not None and t_entry is not None:
            how = (f"entry T{t_entry:+.2f} at {r.entry_fix or 'route start'} + {r.t_route_min:.2f} min route "
                   f"to {a.runway}")
        else:
            how = f"ETA T{f.eta_earliest_min:+g}, no route geometry for {a.runway}"
        how += f" + speed reduction {sr:g} + ground delay {gd:g}"
        problems = []
        if not slot_ok:
            problems.append(f"no valid slot ({a.slot_min!r})")
        elif slot < earliest - EPS_MIN:
            problems.append(f"slot T{slot:+g} before earliest achievable T{earliest:+.2f} on {a.runway} ({how})")
        if not (math.isfinite(sr) and 0 <= sr <= MAX_SPEED_REDUCTION_MIN):
            problems.append(f"speed reduction {sr:g} min outside 0-{MAX_SPEED_REDUCTION_MIN:g}")
        if not math.isfinite(gd):
            problems.append(f"non-finite ground delay {gd!r}")
        elif gd < 0:
            problems.append(f"negative ground delay {gd:g}")
        if math.isfinite(gd) and gd > 0 and f.airborne:
            problems.append(f"ground delay {gd:g} min impossible: {cs} already airborne")
        fin_e = math.isfinite(earliest)
        out.append(RuleCheck(
            "PLT-EARLIEST-ETA", "PILOT", not problems,
            f"{cs}: " + ("; ".join(problems) if problems else
                         f"slot T{slot:+g} >= earliest achievable T{earliest:+.2f} on {a.runway} ({how})"),
            SRC_ETA, {"slot_min": slot if slot_ok else "none", "eta_earliest_min": f.eta_earliest_min,
                      "earliest_min": round(earliest, 2) if fin_e else str(earliest),
                      "runway": str(a.runway),
                      "route_min": round(r.t_route_min, 2) if r is not None else "none",
                      "route_extra_min": round(rex, 2),
                      "entry_fix": (r.entry_fix or "none") if r is not None else "none",
                      "t_entry": round(t_entry, 2) if t_entry is not None else "none",
                      "speed_reduction_min": sr if math.isfinite(sr) else str(sr),
                      "ground_delay_min": gd if math.isfinite(gd) else str(gd),
                      "airborne": "yes" if f.airborne else "no"}, cs))

        # PLT-FUEL-RESERVE: unrounded hold (= laps x lap_min + vector) plus any longer route than nominal
        extra, alt, _, fin = fuel_info(f)
        hold = max(0.0, slot - earliest) if slot_ok else float("inf")
        route_x = max(0.0, rex)
        need = hold + route_x
        ok = need <= extra + EPS_MIN
        fmt = lambda x: f"{x:.2f}".rstrip("0").rstrip(".")
        if not slot_ok:
            parts = "hold unknown"
        elif m.lap_min is not None and m.hold_laps >= 1:
            parts = (f"hold {fmt(hold)} min ({m.hold_laps} laps x {m.lap_min:.2f} min at {m.hold_fix or 'hold fix'}"
                     f" + {fmt(m.vector_min)} min vectors)")
        else:
            parts = f"hold {fmt(hold)} min"
        if slot_ok and route_x > 0:
            parts += f" + {fmt(route_x)} min longer route to {a.runway} = {fmt(need)} min"
        out.append(RuleCheck(
            "PLT-FUEL-RESERVE", "PILOT", ok,
            f"{cs}: {parts} {'<=' if ok else '>'} {extra:g} min extra fuel above alternate "
            f"({alt or 'none'}) + {fin:g} min final reserve", SRC_FUEL,
            {"hold_min": round(hold, 2) if slot_ok else "unknown", "route_extra_min": round(rex, 2),
             "need_min": round(need, 2) if slot_ok else "unknown", "extra_min": extra,
             "laps": m.hold_laps, "lap_min": round(m.lap_min, 2) if m.lap_min is not None else "none",
             "hold_fix": m.hold_fix or "none",
             "alternate": str(alt or "none"), "final_reserve_min": fin}, cs))

        # PLT-CREW-DUTY
        d = _duty_check(wave, f, a, mm, cs)
        if d:
            out.append(d)

        # GND-GATE-SIZE
        if g is None:
            out.append(RuleCheck("GND-GATE-SIZE", "GROUND", False, f"{cs}: unknown gate {a.gate!r}", SRC_OPS,
                                 {"gate": str(a.gate)}, cs))
        else:
            ok = g.remote or g.size == "wide" or f.gate_size == "narrow"
            out.append(RuleCheck(
                "GND-GATE-SIZE", "GROUND", ok,
                f"{cs}: gate {g.id} ({'remote stand' if g.remote else g.size + '-body'}) "
                f"{'fits' if ok else 'cannot take'} {f.aircraft_type or ''} ({f.gate_size}-body)",
                SRC_OPS, {"gate": g.id, "gate_size": g.size, "aircraft_size": f.gate_size}, cs))

        # GND-GATE-FREE
        out.append(gfree[cs])

        # GND-GATE-NOTICE (workbook: gate change decided >= 20 min before arrival; decision time T0)
        if g is not None:
            changed = a.gate != f.scheduled_gate
            arr = float(a.slot_min) if slot_ok else float("nan")
            late = changed and not g.remote and not (slot_ok and arr >= GATE_NOTICE_MIN)
            if not changed:
                det = f"{cs}: keeps scheduled gate {a.gate}"
            elif g.remote:
                det = f"{cs}: moved {f.scheduled_gate} -> remote stand {g.id} (remote stand exempt from notice)"
            else:
                det = (f"{cs}: gate change {f.scheduled_gate} -> {g.id} decided at T+0, arrival T{arr:+g} "
                       f"({'>=' if not late else '<'} {GATE_NOTICE_MIN:g} min notice)")
            out.append(RuleCheck("GND-GATE-NOTICE", "GROUND", not late, det, SRC_NOTICE,
                                 {"scheduled_gate": f.scheduled_gate, "gate": g.id,
                                  "arrival_min": arr if slot_ok else "none", "notice_min": GATE_NOTICE_MIN,
                                  "remote": "yes" if g.remote else "no"}, cs))

        # GND-CREW-DUTY (workbook: overtime after the 8 h shift is allowed and costs 1.5x)
        turn_end = (m.in_block_min or 0) + turn
        over = m.overtime_min
        out.append(RuleCheck(
            "GND-CREW-DUTY", "GROUND", True,
            f"{cs}: turnaround ends T{turn_end:+g}, crew shift ends T{f.crew_shift_end_min:+g}"
            + (f" ({over:g} min overtime at x{OVERTIME_MULT:g} cost = {over * OVERTIME_MULT:g} paid min)"
               if over > 0 else " (no overtime)"),
            SRC_CREW, {"turnaround_end_min": round(turn_end, 1), "crew_shift_end_min": f.crew_shift_end_min,
                       "overtime_min": over, "overtime_mult": OVERTIME_MULT}, cs))

        # ATC-RWY-CAPACITY
        out.append(cap[cs])
    return out


def feasible(wave: Wave, plan: JointPlan) -> bool:
    """True iff every rule passes. A structurally malformed plan is infeasible (never raises)."""
    try:
        return all(c.passed for c in check(wave, plan))
    except ValueError:
        return False


def violations(wave: Wave, plan: JointPlan) -> list[RuleCheck]:
    """Failing checks. Raises ValueError for a structurally malformed plan."""
    return [c for c in check(wave, plan) if not c.passed]
