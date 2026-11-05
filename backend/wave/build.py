"""Build a `Wave` (contract WaveScenario) and WaveSummary dicts from real BTS / METAR /
OurAirports / FAA data, plus seeded simulation of the layers no public source has
(fuel, crew, connections, gate occupancy). Deterministic: same wave_id -> same Wave."""
from __future__ import annotations

import functools
import hashlib
import math
import random
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from backend.datastore import files as _files, get_store
from backend.wave import constraints, icao, wind
from backend.wave.utility import GENERIC_TAXI_IN_MIN, TURNAROUND_MIN
from backend.wave.model import (Airport, Gate, Metar, Provenance, QueueArrival, Runway, Wave,
                            WaveFlight)

ROOT = Path(__file__).resolve().parent.parent.parent
WAVES = ROOT / "data" / "waves"
REF = ROOT / "data" / "ref"
CH2 = ROOT / "data" / "challenge2"

_CACHE: dict = {}
_ARR_COLS = ["date", "carrier", "flight_no", "tail", "origin", "sched_dep", "sched_arr", "weather_delay",
             "nas_delay", "late_aircraft_delay", "carrier_delay", "arr_delay", "cancelled"]


# ---------------------------------------------------------------- loading

def _cached(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def _index() -> pd.DataFrame:
    """The FileStore's 2024 wave index frame (callsigns normalised). Tests and warm-up only: builders go
    through `get_store().wave_row/wave_rows`."""
    return _files._waves_index()


def _arrivals_cache_clear() -> None:
    """Drop the FileStore's per-airport arrivals cache (tests only)."""
    _files._arrivals.cache_clear()


def _reset_caches() -> None:
    """Drop every in-process reader cache of the wave builders (tests only: parity runs swap the store between
    builds). The FileStore's own file caches are not touched."""
    from backend import scenario
    from backend.wave import flights2015, surface
    _CACHE.clear()
    _waves_cache_clear()
    _metar_table.cache_clear()
    airport_taxi_median.cache_clear()
    surface.cache_clear()        # gate bindings, wave tiers, per-wave taxi tables, Dijkstra tree FIFO
    flights2015._IEM_MEM.clear()
    constraints._load.cache_clear()
    for fn in (scenario.disruptions, scenario.airports, scenario.airlines):
        fn.cache_clear()


def _frame(name: str) -> pd.DataFrame:
    df = get_store().frame(name)
    if df is None:
        raise FileNotFoundError(name)
    return df


def _runways_by_airport() -> dict[str, pd.DataFrame]:
    def load():
        df = _frame("runways")
        df = df.dropna(subset=["heading_true"])
        return {k: g for k, g in df.groupby("airport")}
    return _cached("runways", load)


@functools.lru_cache(maxsize=8)
def _metar_table(iata: str) -> dict:
    """{(airport, local_hour_key): last observation row (dict)} for one airport. metar.csv is streamed
    row by row and filtered, so only that airport's rows are ever resident."""
    out: dict = {}
    for r in get_store().metar_2024_rows(iata):
        k = (iata, r["local_hour_key"])
        if k not in out or r["valid_utc"] >= out[k]["valid_utc"]:  # latest observation wins
            out[k] = r
    return out


def _aircraft_table() -> dict:
    def load():
        df = _frame("aircraft")
        return {r["tail"]: r for r in df.to_dict("records")}
    return _cached("aircraft", load)


def _gates_table() -> dict[str, list[dict]]:
    def load():
        df = get_store().frame("gates")
        if df is None:
            return {}
        out: dict[str, list[dict]] = {}
        for r in df.to_dict("records"):
            out.setdefault(r["airport"], []).append(r)
        return out
    return _cached("gates", load)


def _airlines() -> dict[str, str]:
    def load():
        df = _frame("airlines")
        return dict(zip(df["IATA_CODE"], df["AIRLINE"]))
    return _cached("airlines", load)


def _airports() -> dict[str, dict]:
    def load():
        df = _frame("airports")
        return {r["IATA_CODE"]: r for r in df.to_dict("records")}
    return _cached("airports", load)


# ---------------------------------------------------------------- helpers

def _rng(*parts: str) -> random.Random:
    return random.Random(int(hashlib.sha256("".join(parts).encode()).hexdigest(), 16))


def _num(v, default=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def _hhmm_min(hhmm) -> int:
    """HHMM -> minutes since local midnight (2400 -> 1439, kept inside the day)."""
    v = int(hhmm)
    return min(v // 100, 23) * 60 + (59 if v >= 2400 else v % 100)


def _parse_id(wave_id: str) -> tuple[str, str, int]:
    try:
        iata, y, m, d, h = wave_id.split("-")
        return iata, f"{y}-{m}-{d}", int(h)
    except ValueError:
        raise KeyError(wave_id) from None


def _index_row(wave_id: str) -> dict:
    row = get_store().wave_row(wave_id)
    if row is None:
        raise KeyError(wave_id)
    return row


def _haversine_nm(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def _airport_coords(iata: str) -> tuple[float, float] | None:
    a = _airports().get(iata)
    if a is not None:
        lat, lon = _num(a.get("LATITUDE")), _num(a.get("LONGITUDE"))
        if lat is not None and lon is not None:
            return lat, lon
    rw = _runways_by_airport().get(iata)
    if rw is not None:
        lat, lon = rw["le_lat"].dropna(), rw["le_lon"].dropna()
        if len(lat) and len(lon):
            return float(lat.mean()), float(lon.mean())
    return None


def _alternates() -> dict[str, tuple[float, float]]:
    def load():
        out = {}
        for iata in _runways_by_airport():
            c = _airport_coords(iata)
            if c is not None:
                out[iata] = c
        return out
    return _cached("alternates", load)


def _nearest_alternate(iata: str) -> tuple[str, float]:
    here = _airport_coords(iata)
    best = ("", float("inf"))
    if here is None:
        return "N/A", 0.0
    for other, (lat, lon) in _alternates().items():
        if other == iata:
            continue
        d = _haversine_nm(here[0], here[1], lat, lon)
        if d < best[1] and d > 5:
            best = (other, d)
    return best


# ---------------------------------------------------------------- window selection

REDEYE_MIN = 360   # a scheduled arrival clock this far before the departure clock is the next day (not a time zone)


def landing_times(df: pd.DataFrame, iata: str) -> pd.DataFrame:
    """Adds `sched_abs`, `dep_abs` and `land_abs`: minutes from midnight of the row's BTS date (its departure date).
    The scheduled arrival moves to the next day for a red-eye; landing = scheduled gate arrival + ARR_DELAY - taxi-in,
    with the row's TAXI_IN, else the airport's reference (`taxi_in_ref_row`). NaN landing: no ARR_DELAY (diverted).
    Waves are grouped by `land_abs`, when the aircraft really arrive, not by schedule."""
    sarr = df["sched_arr"].map(_hhmm_min).astype(float)
    sdep = pd.to_numeric(df["sched_dep"], errors="coerce").map(lambda v: float(_hhmm_min(v)) if v == v else float("nan"))
    sched = sarr + 1440.0 * ((sdep - sarr) > REDEYE_MIN)
    if "taxi_in" in df.columns:
        taxi = pd.to_numeric(df["taxi_in"], errors="coerce")
    else:
        taxi = pd.Series(float("nan"), index=df.index)
    taxi = taxi.fillna(taxi_in_ref_row(iata, {})[0])
    return df.assign(sched_abs=sched, dep_abs=sdep,
                     land_abs=sched + pd.to_numeric(df["arr_delay"], errors="coerce").astype(float) - taxi)


def _window_rows(iata: str, date: str, hour: int) -> pd.DataFrame:
    """Non-cancelled arrivals at `iata` that land inside [hour:00, hour+1:00) local on `date` (from the BTS rows of
    `date` and the day before: late and red-eye flights land a day after they depart), one row per callsign, with
    `callsign`, `sched_min`, `dep_min` and `land_min` (minutes vs T0; a late flight's schedule is hours before T0)."""
    d0 = datetime.strptime(date, "%Y-%m-%d")
    parts = []
    for k in (-1, 0):
        day = get_store().arrivals_day(iata, (d0 + timedelta(days=k)).strftime("%Y-%m-%d"))
        if day is not None:
            parts.append(day[~day["cancelled"]].assign(_off=1440.0 * k))
    if not parts:
        return pd.DataFrame(columns=_ARR_COLS + ["callsign", "sched_min", "dep_min", "land_min"])
    w = landing_times(pd.concat(parts), iata)
    t0 = hour * 60.0
    land = w["land_abs"] + w["_off"]
    w = w[(land >= t0) & (land < t0 + 60)].copy()
    for c in ("carrier", "origin", "tail", "date"):  # small slice: plain str, not category
        w[c] = w[c].astype(object)
    for c in ("weather_delay", "nas_delay", "late_aircraft_delay", "carrier_delay", "arr_delay", "sched_dep"):
        w[c] = w[c].astype("float64")
    w["sched_min"] = w["sched_abs"] + w["_off"] - t0
    w["dep_min"] = w["dep_abs"] + w["_off"] - t0
    w["land_min"] = w["land_abs"] + w["_off"] - t0
    w["callsign"] = w["carrier"] + w["flight_no"].astype(int).astype(str)
    return w.drop(columns=["_off", "sched_abs", "dep_abs", "land_abs"]).sort_values("land_min") \
            .drop_duplicates("callsign")


NEGOTIATED = 5   # spec asks 3-5 negotiated flights; all five land within minutes of each other


def _select(w: pd.DataFrame, disrupted_cs: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split window rows into (negotiated, queue): the most delayed disrupted flight (the focus) and the flights
    landing nearest to it, disrupted or not, NEGOTIATED in all, so the negotiation sequences aircraft that really
    meet on final. Without a disrupted flight, the flights nearest the middle of the hour."""
    if w.empty:
        return w, w
    key = "land_min" if "land_min" in w.columns else "sched_min"
    dis = w[w["callsign"].isin(disrupted_cs)]
    if len(dis):
        centre = float(dis.sort_values("arr_delay", ascending=False, kind="stable")[key].iloc[0])
    else:
        centre = 30.0
    near = w.assign(_d=(w[key] - centre).abs()).sort_values(["_d", "callsign"], kind="stable")
    chosen = set(near["callsign"].head(NEGOTIATED))
    return w[w["callsign"].isin(chosen)], w[~w["callsign"].isin(chosen)]


# ---------------------------------------------------------------- weather

def _metar_for(iata: str, date: str, hour: int) -> Metar | None:
    tbl = _metar_table(iata)
    d = datetime.strptime(date, "%Y-%m-%d") + timedelta(hours=hour)
    for dt in (d, d - timedelta(hours=1)):
        r = tbl.get((iata, dt.strftime("%Y-%m-%d-%H")))
        if r is not None:
            break
    else:
        return None
    drct = _num(r.get("drct"))
    codes = str(r["wxcodes"]).split() if isinstance(r.get("wxcodes"), str) else []
    gust = _num(r.get("gust"))
    valid = str(r["valid_utc"]).replace(" ", "T")
    return Metar(
        raw=str(r.get("raw", "")), station=str(r.get("station", iata)),
        observed_at=f"{valid}:00Z" if len(valid) == 16 else f"{valid}Z",
        wind_dir_deg=drct, wind_kt=_num(r.get("sknt"), 0.0) or 0.0, gust_kt=gust,
        visibility_sm=_num(r.get("vsby")), ceiling_ft=_num(r.get("ceiling_ft")),
        wx_codes=codes, surface=wind.surface_from_metar(codes))


def _weather_summary(m: Metar | None) -> str:
    if m is None:
        return "no METAR"
    d = "VRB" if m.wind_dir_deg is None else f"{int(round(m.wind_dir_deg)) % 360:03d}"
    g = f"G{int(round(m.gust_kt)):02d}" if m.gust_kt else ""
    parts = [f"{d}{int(round(m.wind_kt)):02d}{g}KT"]
    if m.visibility_sm is not None:
        parts.append(f"{m.visibility_sm:g}SM")
    parts += m.wx_codes
    return " ".join(parts) + f", {m.surface}"


# ---------------------------------------------------------------- runways / gates

def _runway_history() -> dict[str, list[dict]]:
    """runway_history.csv: runway ends that opened or closed after the OurAirports snapshot's past.
    `opened` hides an end before its date; `closed` adds an end (with its own geometry) before its date."""
    def load():
        out: dict[str, list[dict]] = {}
        for r in get_store().rows("runway_history") or []:
            out.setdefault(r["airport"], []).append(r)
        return out
    return _cached("runway_history", load)


_GEO = ("heading_true", "length_ft", "le_lat", "le_lon", "he_lat", "he_lon")


def _runways(iata: str, date: str | None = None) -> list[Runway]:
    """Runway ends at `iata` as they were on `date` (YYYY-MM-DD; None: today's layout).

    runways.csv is today's layout; runway_history.csv winds it back: `opened` runways are removed
    before their date, `closed` ones added back with their own geometry, `modified` ones get their
    earlier geometry, `renamed` ones their earlier designator, and `temp_closed` ones are marked
    closed between `date` and `until`."""
    df = _runways_by_airport().get(iata)
    out = []
    if df is None:
        return out
    rows = df.to_dict("records")
    shut: dict[str, str] = {}
    if date:
        hist = _runway_history().get(iata, [])
        later = lambda e: [h for h in hist if h["event"] == e and date < h["date"]]
        rid = lambda r: str(r["runway"]).strip()
        not_yet = {h["runway"] for h in later("opened")}
        rows = [r for r in rows if rid(r) not in not_yet]
        for h in later("modified"):
            rows = [{**r, **{k: h[k] for k in _GEO}} if rid(r) == h["runway"] else r for r in rows]
        for h in sorted(later("renamed"), key=lambda h: h["date"], reverse=True):   # undo the newest first
            rows = [{**r, "runway": h["old_id"]} if rid(r) == h["runway"] else r for r in rows]
        rows += later("closed")
        shut = {h["runway"]: h["reason"] or "closed" for h in hist
                if h["event"] == "temp_closed" and h["date"] <= date < h["until"]}
    seen = set()
    for r in rows:
        rid = str(r["runway"]).strip()
        if rid in seen:
            continue
        seen.add(rid)
        out.append(Runway(id=rid, heading_true=float(r["heading_true"]),
                          length_ft=int(_num(r.get("length_ft"), 0) or 0),
                          open=rid not in shut, closed_reason=shut.get(rid),
                          le_lat=_num(r.get("le_lat")), le_lon=_num(r.get("le_lon")),
                          he_lat=_num(r.get("he_lat")), he_lon=_num(r.get("he_lon"))))
    return out


def _close_unsafe(runways: list[Runway], rwinds: list) -> None:
    """Close runways outside FAA 8400.9 limits for a LARGE jet (aircraft_type=None)."""
    by_id = {r.id: r for r in runways}
    for rw in rwinds:
        r = by_id[rw.runway]
        if rw.within_limits or not r.open:          # already closed (construction): keep that reason
            continue
        r.open = False
        if rw.crosswind_kt > rw.limit_crosswind_kt:
            r.closed_reason = (f"crosswind {rw.crosswind_kt:g} kt > {rw.limit_crosswind_kt:g} kt "
                               f"({rw.limit_source})")
        else:
            r.closed_reason = (f"tailwind {rw.tailwind_kt:g} kt > {rw.limit_tailwind_kt:g} kt "
                               f"({rw.limit_source})")


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "t", "yes", "y", "wide")


def _make_gates(wave_id: str, iata: str) -> tuple[list[Gate], bool]:
    real = _gates_table().get(iata)
    gates: list[Gate] = []
    if real:
        narrow = [r for r in real if not _truthy(r.get("wide"))][:10]
        wide = [r for r in real if _truthy(r.get("wide"))][-2:]
        for r, size in [(r, "narrow") for r in narrow] + [(r, "wide") for r in wide]:
            ref = str(r["gate_ref"]).strip()
            pier = "".join(itertools_takewhile_alpha(ref)).upper() or "T"
            gates.append(Gate(ref, size, pier))
    is_real = bool(gates)
    if gates and not any(g.size == "wide" for g in gates):
        # the OSM table has no wide-body gate for this airport: without one every wide-body
        # (B772, A359...) fails GND-GATE-SIZE by construction. Add two simulated wide gates.
        gates += [Gate("W1", "wide", "W"), Gate("W2", "wide", "W")]
    if not gates:
        for pier in "AB":
            for n in range(1, 6):
                gates.append(Gate(f"{pier}{n}", "wide" if n == 5 else "narrow", pier))
    for g in gates:
        rng = _rng(wave_id, g.id)
        if rng.random() < 0.30:
            dur = rng.randint(20, 60)
            start = rng.randint(-20, 60 - dur // 2)
            g.busy.append({"from_min": start, "to_min": start + dur, "label": "other-flight (simulated)"})
    gates.append(Gate("RS", "wide", "R", remote=True))
    return gates, is_real


def itertools_takewhile_alpha(s: str):
    for ch in s:
        if not ch.isalpha():
            break
        yield ch


@functools.lru_cache(maxsize=512)
def airport_taxi_median(iata: str) -> float | None:
    """Median BTS TAXI_IN of the airport's 2015 arrivals (data/challenge2/flights/<IATA>.csv.gz, the
    challenge's delayed-flight subset), or None when the airport has no rows. Build time only: cached per
    airport, never called from the hot path (the result is stored on `WaveFlight.taxi_in_ref_min`)."""
    df = _files._dest_frame(iata.upper())
    if df is None or "taxi_in" not in df.columns:
        return None
    v = df.loc[df["cancelled"] == 0, "taxi_in"].median()
    return None if pd.isna(v) else float(v)


def taxi_in_ref_row(iata: str, r: dict) -> tuple[float, float | None, str]:
    """(taxi-in reference, value to store on the flight, provenance text) for one arrival row (spec §6.1):
    the row's real TAXI_IN, else the airport's 2015 median, else the generic 6 min."""
    real = _num(r.get("taxi_in"))
    if real is not None:
        return real, real, f"taxi-in {real:g} min (real TAXI_IN)"
    med = airport_taxi_median(iata)
    if med is not None:
        return med, med, f"taxi-in {med:g} min ({iata} 2015 median TAXI_IN over the challenge's delayed-flight rows)"
    return GENERIC_TAXI_IN_MIN, None, f"taxi-in {GENERIC_TAXI_IN_MIN:g} min (generic, no TAXI_IN for {iata})"


TIME_BASE_NOTE = "scheduled times are gate times (BTS); landing = wheels-on"


def _gate_free(g: Gate, t: float) -> bool:
    return all(not (b["from_min"] <= t <= b["to_min"]) for b in g.busy)


# ---------------------------------------------------------------- flights

def _build_flight(wave_id: str, iata: str, date: str, hour: int, r: dict, disrupted: bool,
                  gates: list[Gate], used: set[str], origin_known: bool = True, taxi_floor=None) -> WaveFlight:
    """`taxi_floor(gate_id, gate_size)`: ROT + unimpeded routed taxi to the gate, minimised over the open runways
    (`surface.floor_taxi`, spec §6.1); the crew floor falls back to the taxi-in reference without it."""
    cs = r["callsign"]
    rng = _rng(wave_id, cs)
    tail = r.get("tail") if isinstance(r.get("tail"), str) else None
    ac = _aircraft_table().get(tail) if tail else None
    taxi_ref, taxi_store, taxi_note = taxi_in_ref_row(iata, r)
    prov = [Provenance("flights", "real", f"BTS On-Time {date[:7]}",
                       "airborne inferred from scheduled departure vs window start; origin/"
                       f"destination time zones ignored (BTS times are local); {TIME_BASE_NOTE}; {taxi_note}")]
    if ac:
        atype, wake, size = ac.get("type_icao") or None, ac.get("wake") or "LARGE", ac.get("gate_size") or "narrow"
        prov.append(Provenance("aircraft", "real", "FAA registry", ac.get("mfr_model")))
    else:
        atype, wake, size = None, "LARGE", "narrow"
        prov.append(Provenance("aircraft", "generic", "assumed narrow-body", "tail not in FAA registry"))

    sched_min = float(r["sched_min"])
    late, carr = float(r["late_aircraft_delay"]), float(r["carrier_delay"])
    wx, nas = float(r["weather_delay"]), float(r["nas_delay"])
    # Earliest feasible landing: the BTS-attributed delay (late inbound aircraft, carrier,
    # weather, NAS) is already baked into when the aircraft can arrive. The negotiation absorbs
    # the remaining airport-side congestion (holding, sequencing, diversion). §6.1: BTS scheduled
    # arrival is a gate (in-block) time, so touchdown is taxi-in earlier. An early flight (negative ARR_DELAY, no
    # attributed delay) could land as early as it really did.
    eta_earliest = sched_min + min(late + carr + wx + nas, float(r["arr_delay"])) - taxi_ref

    dep_min = _num(r.get("dep_min"))   # T0-relative scheduled departure (`_window_rows`)
    airborne = False
    duty_end = None   # flight crew duty end (13 h from sched dep - 60 min); None when sched_dep is unknown
    if dep_min is not None:
        airborne = dep_min < -30
        duty_end = dep_min - 60 + constraints.RULES["crew_duty_h"] * 60

    # gate: seeded pick of a size-compatible, preferably free and unused gate
    cands = [g for g in gates if not g.remote and g.size == size] or [g for g in gates if not g.remote]
    pref = [g for g in cands if g.id not in used and _gate_free(g, sched_min)] \
        or [g for g in cands if g.id not in used] or cands
    gate = pref[rng.randrange(len(pref))].id
    used.add(gate)

    alt, dist = _nearest_alternate(iata)
    fuel = {"extra_min": rng.randint(14, 45), "alternate": alt,
            "alternate_min": int(dist / 7 + 10), "final_reserve_min": 30}
    conns = [{"flight": f"{r['carrier']}{rng.randint(100, 2999)}", "pax": rng.randint(5, 40),
              "departs_min": sched_min + rng.randint(40, 120)} for _ in range(rng.randint(0, 3))]
    prov += [Provenance("fuel", "simulated", "seeded model", f"seed={wave_id}+{cs}"),
             Provenance("crew", "simulated", "seeded model",
                        "shift end = earliest turnaround end (earliest landing incl. late-aircraft/carrier delay "
                        "+ ROT + unimpeded routed taxi to the gate, best open runway + turnaround) + 0-90 min margin: "
                        "the roster already knows the inbound is late"),
             Provenance("connections", "simulated", "seeded model")]
    # Crew shift end is anchored to the earliest achievable turnaround end, not the schedule: with
    # multi-hour BTS late-aircraft delays a schedule-anchored shift made ~70% of flights breach
    # GND-CREW-DUTY before any move (no move can land a flight earlier than its ETA). Airlines
    # re-roster / reserve-crew a known-late inbound; the 0-90 min margin keeps holding, far taxi and
    # ground delay able to cause overtime. One randint at the same position keeps later draws stable.
    floor_taxi = taxi_floor(gate, size) if taxi_floor is not None else None
    crew_end = eta_earliest + (taxi_ref if floor_taxi is None else floor_taxi) + TURNAROUND_MIN \
        + rng.randint(0, 90)   # §6.1: + ROT + unimpeded routed taxi (v3-taxi)
    return WaveFlight(
        callsign=cs, carrier=r["carrier"], airline=_airlines().get(r["carrier"], r["carrier"]),
        flight_no=int(r["flight_no"]), tail=tail, aircraft_type=atype, wake=wake, gate_size=size,
        origin=str(r["origin"]), sched_arr=t0_iso(date, hour, sched_min),
        sched_min=sched_min, eta_earliest_min=eta_earliest, airborne=airborne, disrupted=disrupted,
        delays={"weather": float(r["weather_delay"]), "late_aircraft": late, "nas": float(r["nas_delay"]),
                "carrier": carr, "arr_delay": float(r["arr_delay"])},
        fuel=fuel, connections=conns, scheduled_gate=gate,
        crew_shift_end_min=crew_end,
        next_departure_min=sched_min + rng.randint(45, 120), provenance=prov, crew_duty_end_min=duty_end,
        taxi_in_ref_min=taxi_store)


def _queue(w: pd.DataFrame, runways: list[Runway], rwinds: list, iata: str) -> list[QueueArrival]:
    """Fixed-traffic arrivals (spec §6.1): each lands at its real wheels-on when known (`_queue_slot`), else at
    its scheduled gate time minus the taxi-in reference. Membership is still chosen by scheduled time."""
    open_rw = [r for r in runways if r.open]
    if not open_rw:
        return []
    hw = {x.runway: x.headwind_kt - x.crosswind_kt for x in rwinds}
    best = sorted(open_rw, key=lambda r: -hw.get(r.id, 0))[:2]
    out = []
    for i, r in enumerate(w.to_dict("records")):
        ac = _aircraft_table().get(r["tail"]) if isinstance(r.get("tail"), str) else None
        out.append(QueueArrival(
            callsign=r["callsign"], aircraft_type=(ac or {}).get("type_icao") or None,
            wake=(ac or {}).get("wake") or "LARGE", runway=best[i % len(best)].id,
            slot_min=_queue_slot(iata, r),
            origin=str(r["origin"]) if isinstance(r.get("origin"), str) and r["origin"] else None))
    return out


def t0_iso(date: str, hour: int, minutes: float) -> str:
    """Local ISO time `minutes` after T0 = `date` `hour`:00 (negative: before T0, possibly an earlier day)."""
    t = datetime.strptime(date, "%Y-%m-%d") + timedelta(hours=hour, minutes=round(minutes))
    return t.strftime("%Y-%m-%dT%H:%M:00")


def _queue_slot(iata: str, r: dict) -> float:
    """T0-relative landing of a fixed-traffic arrival. BTS: ARRIVAL_TIME = scheduled + ARRIVAL_DELAY and
    WHEELS_ON = ARRIVAL_TIME - TAXI_IN, so `sched_min + arr_delay - taxi_in` is the real wheels-on with no clock
    ambiguity (the split's `wheels_on` reads a day early for arrivals ~12 h late). Then `land_min` (the same with
    the airport's taxi-in reference, `_window_rows`), then `wheels_min`, then `sched_min - taxi_in_ref`."""
    if _num(r.get("land_min")) is not None and _num(r.get("taxi_in")) is None:
        return float(r["land_min"])
    delay, taxi = _num(r.get("arr_delay")), _num(r.get("taxi_in"))
    if delay is not None and taxi is not None:
        return float(r["sched_min"]) + delay - taxi
    wheels = _num(r.get("wheels_min"))
    return wheels if wheels is not None else float(r["sched_min"]) - taxi_in_ref_row(iata, r)[0]


# ---------------------------------------------------------------- public API

_WAVES: OrderedDict[str, Wave] = OrderedDict()
_WAVES_MAX = 32
_WAVES_LOCK = threading.Lock()


def build_wave(wave_id: str) -> Wave:
    """The wave for `wave_id`, from an LRU of the last 32 builds. A built wave is treated as immutable: callers make
    variants with dataclasses.replace, never mutate it. A flights2015 wave built without a METAR (IEM unreachable,
    retried after 120 s) is not cached."""
    if wave_id.startswith("F2015-") and wave_id[6:].isdigit():
        wave_id = f"F2015-{int(wave_id[6:])}"      # one cache entry for F2015-0270287 and F2015-270287
    with _WAVES_LOCK:
        hit = _WAVES.get(wave_id)
        if hit is not None:
            _WAVES.move_to_end(wave_id)
            return hit
    with get_store().session():     # one pooled connection for all the store reads of this build
        constraints._load()         # warm the workbook limits here so rules.check / utility.evaluate never hit the store
        w = _build_wave(wave_id)
        from backend.wave import route   # lazy: keeps this module's import graph unchanged
        route.precompute(w)          # routes (CIFP reads) pinned on the wave: the hot path never hits the store
        from backend.wave import surface
        surface.precompute(w)        # taxi graph + gate binding pinned, per-wave taxi table filled (spec §6.8); after
                                     # annotate (which sets wave_key) so the table is keyed on the final wave
    if not (wave_id.startswith("F2015-") and w.metar is None):
        with _WAVES_LOCK:
            _WAVES[wave_id] = w
            while len(_WAVES) > _WAVES_MAX:
                _WAVES.pop(next(iter(_WAVES)), None)
    return w


def _waves_cache_clear() -> None:
    with _WAVES_LOCK:
        _WAVES.clear()


build_wave.cache_clear = _waves_cache_clear     # type: ignore[attr-defined]


def _surface_inputs(wave_id: str, airport: Airport, window_start: str, metar, rwinds, gates: list[Gate]):
    """Build time (store reads): bind the gates to OSM stands (sets Gate.lat/lon/geo_src), and return the `surface`
    provenance and the crew-floor taxi `floor(gate_id, size)` = min over open runways of ROT + unimpeded taxi."""
    from backend.wave import surface
    geo = surface.gate_geo(airport.icao, [(g.id, g.remote) for g in gates])
    surface.apply_gate_geo(geo, gates, (airport.lat, airport.lon))
    proto = Wave(id=wave_id, airport=airport, window_start=window_start, metar=metar, runway_wind=rwinds, queue=[],
                 flights=[], gates=gates)
    return surface.provenance(proto, geo), lambda gid, size: surface.floor_taxi(proto, geo, gid, size)


def _build_wave(wave_id: str) -> Wave:
    if wave_id.startswith("F2015-"):
        from backend.wave import flights2015   # lazy: flights2015 imports this module
        try:
            row_id = int(wave_id[6:])
        except ValueError:
            raise KeyError(wave_id) from None
        return flights2015.build_from_row(row_id)
    iata, date, hour = _parse_id(wave_id)
    row = _index_row(wave_id)
    w = _window_rows(iata, date, hour)
    dis_cs = {c for c in str(row["disrupted_flights"]).split(";") if c}
    neg, rest = _select(w, dis_cs)

    runways = _runways(iata, date)
    metar = _metar_for(iata, date, hour)
    _close_unsafe(runways, wind.runway_wind(metar, runways))
    rwinds = wind.runway_wind(metar, runways)

    gates, real_gates = _make_gates(wave_id, iata)
    ap = _airports().get(iata, {})
    coords = _airport_coords(iata) or (0.0, 0.0)
    airport = Airport(iata=iata, icao=icao.icao_or_prefix(iata), name=str(ap.get("AIRPORT", iata)),
                      lat=coords[0], lon=coords[1], runways=runways)
    surf, floor = _surface_inputs(wave_id, airport, f"{date}T{hour:02d}:00:00", metar, rwinds, gates)
    used: set[str] = set()
    flights = [_build_flight(wave_id, iata, date, hour, r, r["callsign"] in dis_cs, gates, used, taxi_floor=floor)
               for r in neg.to_dict("records")]
    month = date[:7]
    prov = [
        Provenance("queue", "real", f"BTS On-Time {month}"),
        Provenance("runways", "real", "OurAirports"),
        (Provenance("weather", "real", "IEM METAR", metar.raw) if metar
         else Provenance("weather", "generic", "no METAR found for window")),
        (Provenance("gates", "real", "OpenStreetMap",
                    "W1, W2 simulated wide-body gates (none in OSM data)"
                    if any(g.id in ("W1", "W2") for g in gates) else None) if real_gates
         else Provenance("gates", "generic", "generic layout A1-A5, B1-B5")),
        Provenance("gate_occupancy", "simulated", "seeded model", f"seed={wave_id}"),
        surf,
    ]
    focus = max((f for f in flights if f.disrupted), key=lambda f: f.delays["arr_delay"], default=None)
    from backend.wave import ground_traffic   # 2024 waves: queue arrivals only (movements cover 2015), badged partial
    return ground_traffic.annotate(Wave(id=wave_id, airport=airport, window_start=f"{date}T{hour:02d}:00:00",
                                        metar=metar, runway_wind=rwinds, queue=_queue(rest, runways, rwinds, iata),
                                        flights=flights, gates=gates, provenance=prov,
                                        focus=focus.callsign if focus else None),
                                   f"BTS On-Time {month} queue")


def _summary(row: dict, replay_ids: set[str] | None = None) -> dict:
    wid = row["wave_id"]
    iata, date, hour = _parse_id(wid)
    w = _window_rows(iata, date, hour)
    dis_cs = {c for c in str(row["disrupted_flights"]).split(";") if c}
    neg, rest = _select(w, dis_cs)
    start = datetime.strptime(f"{date} {hour:02d}", "%Y-%m-%d %H")
    ap = _airports().get(iata, {})
    return {
        "id": wid,
        "airport": {"iata": iata, "icao": icao.icao_or_prefix(iata), "name": str(ap.get("AIRPORT", iata))},
        "window_start": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "window_end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        "n_flights": len(neg), "n_queue": len(rest),
        "callsigns": list(neg["callsign"]),
        "disrupted_callsigns": [c for c in neg["callsign"] if c in dis_cs],
        "weather_summary": _weather_summary(_metar_for(iata, date, hour)),
        "replay_available": wid in (get_store().replay_ids() if replay_ids is None else replay_ids),
    }


def _row_summary(row: dict, replay_ids: set[str]) -> dict:
    """The WaveSummary of an index row. PgStore rows carry the summary precomputed at seed time (one indexed query
    per listing); FileStore rows do not, so it is computed from the arrivals and METAR files."""
    pre = row.get("summary")
    if pre is not None:
        return pre | {"replay_available": row["wave_id"] in replay_ids}
    return _summary(row, replay_ids)


def list_waves(airport: str | None = None, q: str | None = None, limit: int = 50) -> list[dict]:
    store = get_store()
    with store.session():
        ids = store.replay_ids()
        return [_row_summary(r, ids) for r in store.wave_rows(airport.upper() if airport else None, q, limit)]


def list_waves_spread(limit: int = 300) -> list[dict]:
    """Up to `limit` 2024 waves taken round-robin over the hubs (each hub's own order: most disrupted first), so the
    unfiltered listing shows every airport instead of the few with the most disrupted hours."""
    from backend.wave.hubs import HUBS
    store = get_store()
    with store.session():
        ids = store.replay_ids()
        rows = store.wave_rows_spread(-(-max(limit, 0) // len(HUBS)))
        return [_row_summary(r, ids) for r in rows[:limit]]


def random_wave(airport: str | None = None, seed: int | None = None) -> dict:
    store = get_store()
    ap = airport.upper() if airport else None
    with store.session():
        n = store.wave_count(ap)
        if n == 0:
            raise KeyError(airport)
        rng = random.Random(seed)
        return _row_summary(store.wave_rows(ap, None, 1, rng.randrange(n))[0], store.replay_ids())
