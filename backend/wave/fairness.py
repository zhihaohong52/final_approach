"""Airline fairness floor (spec §3.4): an airline compares each of its own flights' delay and fuel with a floor plan.

The floor is `baseline.status_quo` when it is feasible, else the best feasible plan seen so far (`floor_source`).
Two variants, picked by the benchmark (A10):
- firm: no slack; a flight may not be worse than the floor beyond metric rounding (eps);
- relaxing: the floor loosens each turn on the reservation schedule (graph.py `reservation`: x0.9 on turn 1,
  -0.12 per turn), i.e. slack = 1 - reservation = 0.10, 0.22, 0.34 of the flight's normalisation cap.
  Relaxing allowance per flight over the floor, turns 1/2/3: delay +6.0 / +13.2 / +20.4 min (cap 60 min), fuel
  +0.10 / +0.22 / +0.34 x the PILOT fuel budget max(extra_min, 1) x hold burn, e.g. +120 / +264 / +408 kg for a
  narrowbody (2400 kg/h) with 30 min extra fuel (1200 kg), +325 / +715 / +1105 kg for a widebody (6500 kg/h) with 30 min (3250 kg).
  Each comes on top of eps (0.05 min, 0.5 kg).
The variant is per call: callers pass it explicitly (`negotiate_wave(..., fairness=None)` uses `current()`).
Deterministic, formulas only; it decides an airline's objection, never a plan's legality (that is CHECK).
"""
from __future__ import annotations

import logging
import os

from backend.wave import airlines
from backend.wave.model import FlightMetrics, JointPlan, Wave
from backend.wave.utility import DELAY_CAP_MIN, fuel_budget, metrics_map

log = logging.getLogger(__name__)

VARIANTS = ("firm", "relaxing")
FAIRNESS_DEFAULT = "relaxing"     # until A10 picks the shipped variant
FLOOR_SOURCES = ("status_quo", "best_seen")
DELAY_EPS_MIN = 0.05          # metrics are rounded to 0.1 min / 0.1 kg; eps absorbs that, nothing more
FUEL_EPS_KG = 0.5
TURNS = (1, 2, 3)             # negotiation turns are 1-based, at most 3

_warned: set[str] = set()


def current() -> str:
    """The default variant: ATM_FAIRNESS when it is a known variant, else FAIRNESS_DEFAULT
    (an invalid ATM_FAIRNESS logs one warning per bad value). No process-wide override: callers that want
    another variant pass it per call."""
    env = os.environ.get("ATM_FAIRNESS")
    if env is None or env == "":
        return FAIRNESS_DEFAULT
    if env in VARIANTS:
        return env
    if env not in _warned:
        _warned.add(env)
        log.warning("ATM_FAIRNESS=%r is not one of %s; using %r", env, VARIANTS, FAIRNESS_DEFAULT)
    return FAIRNESS_DEFAULT


def _check_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unknown fairness variant {variant!r}; expected one of {VARIANTS}")


def slack(variant: str, rnd: int) -> float:
    """Share of a flight's cap the floor gives way in turn `rnd` (1-based): 0 for firm,
    1 - reservation(rnd) for relaxing (0.10, 0.22, 0.34 in turns 1, 2, 3)."""
    _check_variant(variant)
    if rnd not in TURNS:
        raise ValueError(f"turn {rnd!r} outside 1..3")
    if variant == "firm":
        return 0.0
    return 1 - (0.9 - 0.12 * (rnd - 1))


def floor_source(wave: Wave, status_quo: JointPlan, sq_feasible: bool,
                 best_seen: JointPlan | None) -> JointPlan | None:
    """The floor plan: the status quo when it is feasible, else the best feasible plan seen so far
    (None before any feasible plan is seen, so nothing can breach). `wave` is reserved (pinned signature)."""
    return status_quo if sq_feasible else best_seen


def breaches(wave: Wave, plan: JointPlan, code: str, floor: JointPlan | None, variant: str, rnd: int, *,
             source: str, mm_plan: dict[str, FlightMetrics] | None = None,
             mm_floor: dict[str, FlightMetrics] | None = None) -> list[dict]:
    """Airline `code`'s flights in `plan` that are worse than in `floor`, beyond the allowance.

    allowed = floor value + slack(variant, rnd) x cap + eps, with cap = DELAY_CAP_MIN and eps 0.05 min for delay,
    and cap = max(extra_min, 1) x hold_burn (the PILOT fuel budget) and eps 0.5 kg for fuel. A breach is
    value > allowed. `source` (keyword-only, required) labels the floor ("status_quo" or "best_seen") and is
    copied to each entry as "floor_source"; the caller knows which plan `floor_source()` returned. `mm_plan` / `mm_floor` take precomputed metrics (else `metrics_map`)."""
    _check_variant(variant)
    if source not in FLOOR_SOURCES:
        raise ValueError(f"unknown floor source {source!r}; expected one of {FLOOR_SOURCES}")
    if floor is None:
        return []
    own = airlines.groups(wave).get(code)
    if not own:
        raise ValueError(f"no airline {code!r} in wave {wave.id}")
    s = slack(variant, rnd)
    mm_plan = mm_plan if mm_plan is not None else metrics_map(wave, plan)
    mm_floor = mm_floor if mm_floor is not None else metrics_map(wave, floor)
    out = []
    for cs in own:
        f, m, fm = wave.flight(cs), mm_plan[cs], mm_floor[cs]
        fuel_cap = fuel_budget(f)
        for metric, cap, eps in (("delay_min", DELAY_CAP_MIN, DELAY_EPS_MIN), ("fuel_kg", fuel_cap, FUEL_EPS_KG)):
            base, value = float(getattr(fm, metric)), float(getattr(m, metric))
            allowed = base + s * cap + eps
            if value > allowed:
                out.append({"callsign": cs, "metric": metric, "floor": base, "value": value,
                            "allowed": round(allowed, 3), "floor_source": source})
    return out
