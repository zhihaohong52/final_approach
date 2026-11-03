"""Shared wave-mode data model. Field names match docs/api-contract.md exactly;
`to_dict()` on any object gives the contract JSON."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, replace
from typing import Literal

from backend.wave.version import ENGINE_VERSION

Wake = Literal["SUPER", "HEAVY", "B757", "LARGE", "SMALL"]
GateSize = Literal["narrow", "wide"]


class _Dict:
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Provenance(_Dict):
    layer: str            # flights|queue|runways|weather|aircraft|gates|fuel|crew|connections|gate_occupancy
                          # v2.2 adds: procedures|surface|ground_traffic
    status: str           # real|simulated|generic
    source: str
    detail: str | None = None


@dataclass
class Runway(_Dict):
    id: str               # runway end, e.g. "08L"
    heading_true: float
    length_ft: int
    open: bool = True
    closed_reason: str | None = None
    le_lat: float | None = None
    le_lon: float | None = None
    he_lat: float | None = None
    he_lon: float | None = None


@dataclass
class Metar(_Dict):
    raw: str
    station: str
    observed_at: str
    wind_dir_deg: float | None
    wind_kt: float
    gust_kt: float | None
    visibility_sm: float | None
    ceiling_ft: float | None
    wx_codes: list[str]
    surface: Literal["dry", "wet"]


@dataclass
class RunwayWind(_Dict):
    runway: str
    headwind_kt: float
    crosswind_kt: float
    tailwind_kt: float
    limit_crosswind_kt: float
    limit_tailwind_kt: float
    limit_source: str
    within_limits: bool


@dataclass
class QueueArrival(_Dict):
    callsign: str
    aircraft_type: str | None
    wake: Wake
    runway: str
    slot_min: float
    origin: str | None = None


@dataclass
class Gate(_Dict):
    id: str
    size: GateSize
    pier: str
    remote: bool = False
    busy: list[dict] = field(default_factory=list)   # {from_min, to_min, label}
    lat: float | None = None
    lon: float | None = None
    geo_src: str | None = None   # osm-ref|osm-near|osm-gate|osm-generic|synth


@dataclass
class Airport(_Dict):
    iata: str
    icao: str
    name: str
    lat: float
    lon: float
    runways: list[Runway]


@dataclass
class WaveFlight(_Dict):
    callsign: str
    carrier: str
    airline: str
    flight_no: int
    tail: str | None
    aircraft_type: str | None
    wake: Wake
    gate_size: GateSize
    origin: str
    sched_arr: str
    sched_min: float
    eta_earliest_min: float
    airborne: bool
    disrupted: bool
    delays: dict            # weather, late_aircraft, nas, carrier, arr_delay (real BTS minutes)
    fuel: dict              # extra_min, alternate, alternate_min, final_reserve_min
    connections: list[dict]  # flight, pax, departs_min
    scheduled_gate: str
    crew_shift_end_min: float
    next_departure_min: float
    provenance: list[Provenance] = field(default_factory=list)
    crew_duty_end_min: float | None = None   # flight crew 13 h duty end (T0-relative); omitted from JSON when None
    # taxi-in reference (spec §6.1): real BTS TAXI_IN, else the airport's 2015 median; omitted from JSON when None
    # (the engine then uses utility.GENERIC_TAXI_IN_MIN)
    taxi_in_ref_min: float | None = None


@dataclass
class Wave(_Dict):
    """Contract `WaveScenario`. Times are minutes relative to window_start (T0)."""
    id: str
    airport: Airport
    window_start: str
    metar: Metar | None
    runway_wind: list[RunwayWind]
    queue: list[QueueArrival]
    flights: list[WaveFlight]
    gates: list[Gate]
    provenance: list[Provenance] = field(default_factory=list)
    severity: dict | None = None
    focus: str | None = None   # callsign of the disruption row's flight (omitted from JSON when None)

    def geo_available(self) -> bool:
        """True iff the airport is not at (0, 0) and every open runway end has both end coordinates.
        Procedures are optional (an airport without them still gets synthetic routes)."""
        a = self.airport
        if a.lat == 0 and a.lon == 0:
            return False
        return all(None not in (r.le_lat, r.le_lon, r.he_lat, r.he_lon)
                   for r in a.runways if r.open)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["engine_version"] = ENGINE_VERSION
        d["geo_available"] = self.geo_available()
        if d["focus"] is None:
            del d["focus"]
        from backend.wave import airlines   # lazy: airlines imports Wave from this module
        d["airlines"] = airlines.describe(self)   # derived, never stored
        for f in d["flights"]:
            for k in ("crew_duty_end_min", "taxi_in_ref_min"):
                if f.get(k) is None:
                    f.pop(k, None)
        return d

    def flight(self, callsign: str) -> WaveFlight:
        return next(f for f in self.flights if f.callsign == callsign)

    def runway(self, rid: str) -> Runway:
        return next(r for r in self.airport.runways if r.id == rid)

    def gate(self, gid: str) -> Gate:
        return next(g for g in self.gates if g.id == gid)


@dataclass
class FlightMetrics(_Dict):
    hold_min: float = 0.0
    fuel_kg: float = 0.0
    delay_min: float = 0.0
    in_block_min: float | None = None
    pax_protected: int = 0
    pax_total: int = 0
    overtime_min: float = 0.0
    next_dep_delay_min: float = 0.0
    # v2.2 path fields (stored unrounded)
    hold_laps: int = 0
    hold_fix: str | None = None
    hold_level_ft: int | None = None
    lap_min: float | None = None
    vector_min: float = 0.0
    route_min: float | None = None
    route_extra_min: float = 0.0
    exit: str | None = None
    rot_s: float | None = None
    taxi_unimpeded_min: float | None = None
    taxi_wait_min: float = 0.0
    taxi_min: float | None = None
    # v3-traffic: strip of the flight's longest hold-short wait (None: no hold-short)
    hold_short_strip: str | None = None


@dataclass
class Assignment(_Dict):
    callsign: str
    action: Literal["land", "divert"] = "land"
    runway: str | None = None
    slot_min: float | None = None
    gate: str | None = None              # "RS" = remote stand
    speed_reduction_min: float = 0.0     # 0-8, absorbed en route
    ground_delay_min: float = 0.0        # >0 only if not airborne
    metrics: FlightMetrics = field(default_factory=FlightMetrics)

    def key(self) -> tuple:
        return (self.callsign, self.action, self.runway, self.slot_min, self.gate,
                self.speed_reduction_min, self.ground_delay_min)

    def to_dict(self) -> dict:
        """Contract Assignment: runway / slot_min / gate are optional, omitted when None."""
        d = asdict(self)
        for k in ("runway", "slot_min", "gate"):
            if d[k] is None:
                del d[k]
        return d


@dataclass
class JointPlan(_Dict):
    assignments: list[Assignment]
    label: str = ""

    @property
    def id(self) -> str:
        keys = sorted(repr(a.key()) for a in self.assignments)
        return hashlib.sha1("|".join(keys).encode()).hexdigest()[:10]

    def get(self, callsign: str) -> Assignment:
        return next(a for a in self.assignments if a.callsign == callsign)

    def with_assignment(self, a: Assignment) -> "JointPlan":
        return JointPlan([a if x.callsign == a.callsign else x for x in self.assignments], self.label)

    def describe(self) -> str:
        parts = []
        for a in self.assignments:
            if a.action == "divert":
                parts.append(f"{a.callsign} DIVERT")
            else:
                slot = f"T{'+' if (a.slot_min or 0) >= 0 else ''}{a.slot_min:g}" if a.slot_min is not None else "T?"
                parts.append(f"{a.callsign} R{a.runway} {slot} {a.gate}")
        return " · ".join(parts)

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label or self.describe(),
                "assignments": [a.to_dict() for a in self.assignments]}


@dataclass
class Move(_Dict):
    id: str
    agent: str              # ATC | GROUND | PILOT:<callsign>
    kind: str               # swap_order|change_runway|shift_slot|hold|speed_reduction|ground_delay|request_divert|accept|reassign_gate|remote_stand|swap_gates
    callsigns: list[str]
    params: dict
    label: str


@dataclass
class RuleCheck(_Dict):
    rule: str
    owner: Literal["ATC", "PILOT", "GROUND"]
    passed: bool
    detail: str
    source: str
    evidence: dict = field(default_factory=dict)
    callsign: str | None = None


def agent_ids(wave: Wave) -> list[str]:
    """All negotiating agents for a wave: ATC, one Pilot per flight, Ground."""
    return ["ATC"] + [f"PILOT:{f.callsign}" for f in wave.flights] + ["GROUND"]
