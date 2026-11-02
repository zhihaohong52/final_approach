"""IATA <-> ICAO airport codes from OpenFlights (airports.dat: c5 = IATA, c6 = ICAO)."""
from __future__ import annotations

import functools

from atm.datastore import get_store


@functools.cache
def _maps() -> tuple[dict[str, str], dict[str, str]]:
    to_icao: dict[str, str] = {}
    to_iata: dict[str, str] = {}
    for r in get_store().rows("openflights"):
        iata, icao = str(r.get("c5") or "").strip(), str(r.get("c6") or "").strip()
        if len(iata) != 3 or len(icao) != 4:   # "\N" or blank placeholders
            continue
        to_icao.setdefault(iata, icao)
        to_iata.setdefault(icao, iata)
    return to_icao, to_iata


def icao_for(iata: str) -> str:
    """ICAO code for an IATA code; raises KeyError if unknown."""
    return _maps()[0][iata]


def iata_for(icao: str) -> str | None:
    return _maps()[1].get(icao)
