"""Loader for the per-airport CIFP procedure documents (data/ref/procedures/<ICAO>.json.gz).

Reads through the datastore (PgStore in production, FileStore offline); never touches the network.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path

from atm.datastore import get_store

ROOT = Path(__file__).resolve().parent.parent.parent
PROCEDURES_DIR = ROOT / "data/ref/procedures"


@lru_cache(maxsize=4)
def load_procedures(icao: str) -> dict | None:
    """Procedures document for `icao` (header, airport, fixes, runways, stars, approaches, holds), or None."""
    blob = get_store().procedures(icao)
    return None if blob is None else json.loads(gzip.decompress(blob))
