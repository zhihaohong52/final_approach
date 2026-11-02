"""Registry of the reference files: where each lives, how today's code reads it, and the Postgres table and
column types it maps to (design §6.1-§6.3). Column order = source header order. The read arguments are the
exact `pd.read_csv` arguments the readers used before the store existed."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
REF = ROOT / "data" / "ref"
WAVES = ROOT / "data" / "waves"
CH2 = ROOT / "data" / "challenge2"
WORKBOOK = CH2 / "challenge2_operational_constraints.xlsx"


@dataclass(frozen=True)
class Column:
    src: str       # header in the source file (or a positional name when the file has no header)
    col: str       # Postgres column name
    pgtype: str


@dataclass(frozen=True)
class Dataset:
    name: str
    path: Path
    table: str
    columns: tuple[Column, ...]
    read_kwargs: dict
    header: bool = True


def _cols(spec: str) -> tuple[Column, ...]:
    """'name:type name:type ...' -> columns whose Postgres name is the lower-cased source header."""
    out = []
    for item in spec.split():
        src, pgtype = item.split(":")
        out.append(Column(src, src.lower(), pgtype))
    return tuple(out)


# dtype dicts of today's readers of the large per-airport / per-destination files
ARRIVALS_KWARGS = {"dtype": {
    "date": str, "carrier": str, "origin": str, "tail": str,
    "flight_no": "int32", "sched_arr": "int32", "sched_dep": "float32",
    "weather_delay": "float32", "nas_delay": "float32", "late_aircraft_delay": "float32",
    "carrier_delay": "float32", "arr_delay": "float32", "cancelled": "bool"}}
FLIGHTS2015_DAY_KWARGS = {"dtype": {
    "date": str, "arr_date": str, "carrier": str, "tail": str, "origin": str,
    "row_id": "int32", "flight_no": "int32", "sched_dep": "int32",
    "sched_arr": "int32", "diverted": "int8", "cancelled": "int8"}}

_FLIGHTS2015_INDEX_KWARGS = {"dtype": {
    "row_id": "int32", "date": "category", "carrier": "category", "flight_no": "int32",
    "tail": "category", "origin": "category", "dest": "category", "sched_arr": "float32",
    "arr_delay": "float32", "weather_delay": "float32", "late_aircraft_delay": "float32",
    "diverted": "float32", "cancelled": "float32", "arr_date": "category"}}

_DS = [
    Dataset("runways", REF / "runways.csv", "ref.runways",
            _cols("airport:text runway:text heading_true:real length_ft:int4 le_lat:float8 le_lon:float8 "
                  "he_lat:float8 he_lon:float8 surface:text"),
            {"dtype": {"runway": str}}),
    Dataset("runway_history", REF / "runway_history.csv", "ref.runway_history",
            _cols("airport:text runway:text event:text date:text until:text old_id:text heading_true:text "
                  "length_ft:text le_lat:text le_lon:text he_lat:text he_lon:text reason:text source:text"),
            {}),
    Dataset("aircraft", REF / "aircraft.csv", "ref.aircraft",
            _cols("tail:text type_icao:text mfr_model:text wake:text gate_size:text"),
            {"dtype": str}),
    Dataset("gates", REF / "gates.csv", "ref.gates",
            _cols("airport:text gate_ref:text wide:text"),
            {"dtype": str}),
    Dataset("airports", CH2 / "airports.csv", "ref.airports",
            _cols("IATA_CODE:text AIRPORT:text CITY:text STATE:text COUNTRY:text LATITUDE:float8 "
                  "LONGITUDE:float8"),
            {}),
    Dataset("airlines", CH2 / "airlines.csv", "ref.airlines",
            _cols("IATA_CODE:text AIRLINE:text"),
            {}),
    Dataset("disruptions_v1", CH2 / "disruptions.csv", "ref.disruptions_v1",
            _cols("id:int4 year:int4 month:int4 day:int4 carrier:text tail:text flight_no:int4 origin:text "
                  "dest:text sched_dep:int4 sched_arr:int4 arr_delay:float8 distance:float8 "
                  "weather_delay:float8 nas_delay:float8 late_aircraft_delay:float8 dest_precip_mm:float8 "
                  "dest_wind_kmh:float8 dest_gust_kmh:float8 dest_condition:float8"),
            {}),
    Dataset("openflights", ROOT / "data" / "challenge3" / "airports.dat", "ref.openflights",
            tuple(Column(f"c{i}", f"c{i}", "text") for i in range(1, 15)),
            {"header": None, "dtype": str}, header=False),
    Dataset("waves_index", WAVES / "index.csv", "ref.waves",
            _cols("wave_id:text airport:text date:date hour:int2 n_arrivals:int2 n_disrupted:int2 "
                  "disrupted_flights:text"),
            {"dtype": {"date": str, "airport": "category", "hour": "int8",
                       "n_arrivals": "int16", "n_disrupted": "int16"}}),
    Dataset("metar_2024", REF / "metar.csv", "ref.metar_2024",
            _cols("station:text airport:text valid_utc:text local_hour_key:text drct:real sknt:real gust:real "
                  "vsby:real ceiling_ft:real wxcodes:text raw:text"),
            {}),
    Dataset("flights2015_index", CH2 / "flights_index.csv", "ref.flights2015",
            _cols("row_id:int4 date:date carrier:text flight_no:int4 tail:text origin:text dest:text "
                  "sched_arr:int2 arr_delay:int2 weather_delay:int2 late_aircraft_delay:int2 diverted:int2 "
                  "cancelled:int2 arr_date:date"),
            _FLIGHTS2015_INDEX_KWARGS),
]
DATASETS: dict[str, Dataset] = {d.name: d for d in _DS}
