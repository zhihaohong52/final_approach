"""Loaders for the per-airport CIFP procedure documents (data/ref/procedures/) and OSM surface documents
(data/ref/surface/), both <ICAO>.json.gz, and the 2015 ground movements (data/ref/movements2015/<IATA>.csv.gz).

Reads through the datastore (PgStore in production, FileStore offline); never touches the network.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
from dataclasses import dataclass
from datetime import date as _date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

from backend.datastore import get_store

ROOT = Path(__file__).resolve().parent.parent.parent
PROCEDURES_DIR = ROOT / "data/ref/procedures"
MOVEMENTS_DIR = ROOT / "data/ref/movements2015"

# Top 30 IATA codes by 2015 arrivals (BTS On-Time, all 12 months); printed by `fetch_bts.py --movements2015`.
TOP30: tuple[str, ...] = (
    'ATL', 'BNA', 'BOS', 'BWI', 'CLT', 'DAL', 'DCA', 'DEN', 'DFW', 'DTW', 'EWR', 'FLL', 'HOU', 'IAH', 'JFK', 'LAS',
    'LAX', 'LGA', 'MCO', 'MDW', 'MIA', 'MSP', 'ORD', 'PHL', 'PHX', 'SAN', 'SEA', 'SFO', 'SLC', 'TPA')
# Sorted union of TOP30 and the benchmark airports (eval/benchmark.json wave_ids -> dest): AUS and IAD on top.
# The files are not in git (about 94 MB); they are seeded to Postgres only. Absent -> None -> generic ground traffic.
MOVEMENT_AIRPORTS: tuple[str, ...] = tuple(sorted({*TOP30, 'AUS', 'IAD'}))


@lru_cache(maxsize=4)
def load_procedures(icao: str) -> dict | None:
    """Procedures document for `icao` (header, airport, fixes, runways, stars, approaches, holds), or None."""
    blob = get_store().procedures(icao)
    return None if blob is None else json.loads(gzip.decompress(blob))


@lru_cache(maxsize=4)
def load_surface(icao: str) -> dict | None:
    """OSM surface document for `icao` (header, nodes, edges, exits, stands, gates, holding_positions, ...), or None."""
    blob = get_store().surface(icao)
    return None if blob is None else json.loads(gzip.decompress(blob))


@dataclass
class Movement:
    callsign: str
    tail: str | None
    kind: Literal["arrival", "departure"]
    date: str
    sched_min_abs: int            # local minutes since midnight of the requested `date` (next day: +1440)
    wheels_min_abs: int | None    # wheels-on (arrival) / wheels-off (departure), same clock; None if cancelled
    taxi_min: float | None        # BTS TaxiIn / TaxiOut
    other_airport: str            # origin of an arrival, destination of a departure


def _day_rows(blob: bytes | None, day: str, shift: int) -> list[Movement]:
    if blob is None:
        return []
    out = []
    for r in csv.DictReader(io.StringIO(gzip.decompress(blob).decode())):
        wheels, taxi = r["wheels"], r["taxi"]
        out.append(Movement(
            callsign=f"{r['carrier']}{r['flight_no']}", tail=r["tail"] or None, kind=r["kind"], date=day,
            sched_min_abs=int(float(r["sched"])) + shift,
            wheels_min_abs=int(float(wheels)) + shift if wheels else None,
            taxi_min=float(taxi) if taxi else None, other_airport=r["other"]))
    return out


@lru_cache(maxsize=16)
def load_movements(iata: str, date: str, from_abs: int, to_abs: int) -> list[Movement] | None:
    """Movements at `iata` with scheduled time in [from_abs, to_abs) local minutes since midnight of `date`
    (a window past 1440 also reads the next day, one before 0 the previous day), or None when there is no data."""
    st = get_store()
    blob = st.movements_day(iata, date)
    if blob is None:
        return None
    d0 = _date.fromisoformat(date)
    rows = _day_rows(blob, date, 0)
    if to_abs > 1440:
        rows += _day_rows(st.movements_day(iata, (d0 + timedelta(days=1)).isoformat()), (d0 + timedelta(days=1)).isoformat(), 1440)
    if from_abs < 0:
        rows += _day_rows(st.movements_day(iata, (d0 - timedelta(days=1)).isoformat()), (d0 - timedelta(days=1)).isoformat(), -1440)
    return [m for m in rows if from_abs <= m.sched_min_abs < to_abs]

