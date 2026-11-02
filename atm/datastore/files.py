"""File-backed store: the repo's data/ and eval/ files. Reads and writes land in later tasks."""
from __future__ import annotations

import csv
import functools
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .base import StoreStatus
from .datasets import (ARRIVALS_KWARGS, DATASETS, FLIGHTS2015_DAY_KWARGS, WAVES, WORKBOOK, CH2)

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class FilePaths:
    root: Path = ROOT
    runs_dir: Path = ROOT / "eval/runs"
    decisions_path: Path = ROOT / "eval/decisions.jsonl"
    replay_dir: Path = ROOT / "data/replay"
    benchmark_path: Path = ROOT / "eval/benchmark.json"
    iem_dir: Path = ROOT / "data/challenge2/metar_cache"


_FLIGHT_KEYS = ("row_id", "date", "carrier", "flight_no", "tail", "origin", "dest", "sched_arr", "arr_delay",
                "weather_delay", "late_aircraft_delay", "diverted", "cancelled", "arr_date")


@functools.lru_cache(maxsize=1)
def _waves_index() -> pd.DataFrame:
    """index.csv with flight numbers normalised to callsigns. Loaded once per process."""
    ds = DATASETS["waves_index"]
    df = pd.read_csv(ds.path, **ds.read_kwargs)
    # index.csv writes flight numbers as floats ("AA807.0"); normalise to callsigns
    df["disrupted_flights"] = df["disrupted_flights"].fillna("").str.replace(r"\.0(?=;|$)", "", regex=True)
    return df


@functools.lru_cache(maxsize=4)
def _arrivals(iata: str):
    """(frame, {date: row positions}) for one destination hub, from the per-airport files written by
    scripts/split_arrivals.py (the 1.2M-row arrivals.csv.gz is never loaded at runtime: 512 MB host)."""
    p = WAVES / "arrivals" / f"{iata}.csv.gz"
    if not p.exists():
        return None, {}
    # streamed in chunks (low parse peak), then categoricals rebuilt over the whole airport
    df = pd.concat(pd.read_csv(p, chunksize=20000, **ARRIVALS_KWARGS), ignore_index=True)
    for c in ("date", "carrier", "origin", "tail"):
        df[c] = df[c].astype("category")
    return df, df.groupby("date", observed=True).indices


@functools.lru_cache(maxsize=2)
def _dest_frame(dest: str) -> pd.DataFrame | None:
    p = CH2 / "flights" / f"{dest}.csv.gz"
    if not p.exists():
        return None
    return pd.read_csv(p, **FLIGHTS2015_DAY_KWARGS)


@functools.lru_cache(maxsize=1)
def _flights2015_index() -> pd.DataFrame:
    """The whole flights.csv index, loaded once with compact dtypes (~15 MB), cancelled rows dropped."""
    ds = DATASETS["flights2015_index"]
    df = pd.read_csv(ds.path, **ds.read_kwargs)
    df = df[df["cancelled"] != 1].reset_index(drop=True)
    df["callsign"] = (df["carrier"].astype(str) + df["flight_no"].astype(str)).astype("category")
    rw = pd.read_csv(DATASETS["runways"].path, **DATASETS["runways"].read_kwargs).dropna(subset=["heading_true"])
    df["supported"] = df["dest"].isin(set(rw["airport"]))
    return df


class FileStore:
    def __init__(self, paths: FilePaths | None = None):
        self.paths = paths or FilePaths()

    def status(self) -> StoreStatus:
        return StoreStatus("files", False, None, False)

    # ------------------------------------------------------------ reference

    def frame(self, name: str) -> pd.DataFrame | None:
        ds = DATASETS[name]
        if not ds.path.exists():
            return None
        return pd.read_csv(ds.path, **ds.read_kwargs)

    def rows(self, name: str) -> list[dict[str, str]] | None:
        ds = DATASETS[name]
        if not ds.path.exists():
            return None
        with open(ds.path, newline="") as f:
            if ds.header:
                return list(csv.DictReader(f))
            return list(csv.DictReader(f, fieldnames=[c.src for c in ds.columns]))

    def workbook_rows(self, sheet: str) -> list[tuple]:
        import openpyxl
        wb = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
        try:
            return list(wb[sheet].iter_rows(min_row=4, values_only=True))
        finally:
            wb.close()

    # ------------------------------------------------------------ 2024 waves

    def wave_row(self, wave_id: str) -> dict | None:
        idx = _waves_index()
        hit = idx[idx["wave_id"] == wave_id]
        return None if hit.empty else hit.iloc[0].to_dict()

    def _wave_frame(self, airport: str | None, q: str | None = None) -> pd.DataFrame:
        idx = _waves_index()
        if airport:
            idx = idx[idx["airport"] == airport.upper()]
        if q:
            qq = q.strip().upper()
            idx = idx[idx["wave_id"].str.upper().str.contains(qq, regex=False)
                      | idx["disrupted_flights"].str.upper().str.contains(qq, regex=False)]
        return idx

    def wave_rows(self, airport: str | None, q: str | None, limit: int, offset: int = 0) -> list[dict]:
        return self._wave_frame(airport, q).iloc[offset:offset + limit].to_dict("records")

    def wave_count(self, airport: str | None) -> int:
        return len(self._wave_frame(airport))

    def wave_summary(self, wave_id: str) -> dict | None:
        return None   # FileStore summaries are computed by build._summary

    def arrivals_day(self, iata: str, date: str) -> pd.DataFrame | None:
        df, groups = _arrivals(iata)
        pos = groups.get(date)
        return None if pos is None else df.iloc[pos]

    def metar_2024_rows(self, iata: str) -> list[dict[str, str]]:
        with open(DATASETS["metar_2024"].path, newline="") as f:
            return [r for r in csv.DictReader(f) if r["airport"] == iata]

    # ------------------------------------------------------------ 2015 flights

    def flight2015_row(self, row_id: int) -> dict | None:
        """Index row for `row_id` (raw strings, as the CSV has them)."""
        df = _flights2015_index()
        hit = df[df["row_id"] == row_id]
        if hit.empty:
            return None
        r = hit.iloc[0]
        return {k: ("" if pd.isna(r[k]) else str(int(r[k])) if isinstance(r[k], (float, np.floating)) else str(r[k]))
                for k in _FLIGHT_KEYS}

    def flights2015_day(self, dest: str, arr_date: str) -> pd.DataFrame | None:
        df = _dest_frame(dest)
        if df is None:
            return None
        return df[df["arr_date"] == arr_date]

    def search_flights2015(self, airport: str | None, q: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        from atm.wave import flights2015 as F   # lazy: flights2015 imports the store
        df = _flights2015_index()
        df = df[df["supported"]]
        if airport and airport.strip():
            df = df[df["dest"] == airport.strip().upper()]
        if q and q.strip():
            qq = q.strip().upper()
            m = (F._cat_match(df["callsign"], qq) | F._cat_match(df["tail"], qq) | F._cat_match(df["origin"], qq)
                 | F._cat_match(df["dest"], qq))
            digits = qq[len(F.PREFIX):] if qq.startswith(F.PREFIX) else qq
            if digits.isdigit():
                m |= df["row_id"].astype(str).str.contains(digits, regex=False)
            df = df[m]
        return [F.flight_row(r) for r in df.iloc[offset:offset + limit].to_dict("records")], len(df)

    # ------------------------------------------------------------ other

    def session(self) -> AbstractContextManager[None]:
        return nullcontext()
