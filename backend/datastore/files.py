"""File-backed store: the repo's data/ and eval/ files."""
from __future__ import annotations

import bisect
import csv
import functools
import gzip
import io
import json
import os
import re
import threading
import uuid
import zlib
from collections import OrderedDict
from datetime import datetime, timezone
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import pack
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


class _DayIndex:
    """What one per-airport CSV keeps resident so that a day can be read without the whole-file frame: the frame's
    row count, per-day row counts, column dtypes (categories included), an empty slice, and the last few day
    slices. Built once from today's whole-file parse, which is kept only until the first `day` call (that call
    selects from it, so the first day costs no second parse) and then dropped. `safe` is False when the file text may
    not map one line to one frame row (a quote, a "\r", a blank line, a header that is not the frame's columns):
    every read then takes the whole-file path."""

    KEEP = 4      # day slices kept per file (callers ask the day and its neighbours, prefetch then build)

    def __init__(self, path: Path, df: pd.DataFrame, day_col: str, raw: bytes | None = None):
        self.path, self.day_col, self.n = path, day_col, len(df)
        self._seed: pd.DataFrame | None = df     # the whole-file frame, until the first `day` call takes it
        self.dtypes = df.dtypes
        self.counts = {k: v for k, v in df[day_col].value_counts(dropna=True).items() if v}
        self.empty = df[np.zeros(len(df), dtype=bool)]
        if raw is None:
            raw = gzip.decompress(path.read_bytes())
        self.size = len(raw)
        nl = raw.find(b"\n")
        try:
            header = (raw if nl < 0 else raw[:nl]).decode().split(",")
        except UnicodeDecodeError:
            header = []
        lines = raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1)
        self.safe = (header == list(df.columns) and lines - 1 == self.n and b'"' not in raw and b"\r" not in raw
                     and b"\n\n" not in raw and not raw.startswith(b"\n") and day_col in header[:-1])
        self.col = header.index(day_col) if self.safe else -1
        self.days: OrderedDict = OrderedDict()
        self.lock = threading.Lock()

    def day(self, key: str, full) -> pd.DataFrame | None:
        """A fresh copy of the rows whose `day_col` equals `key`, exactly as `full()[full()[day_col] == key]` (same
        dtypes, categories, column order, row order and int64 row-position index); None when there are none."""
        with self.lock:
            seed, self._seed = self._seed, None              # dropped on the first call, whatever it asks
        if key not in self.counts:
            return None
        with self.lock:
            hit = self.days.get(key)
            if hit is not None:
                self.days.move_to_end(key)
                return hit.copy()
        got = None if seed is not None else self._read(key)
        if got is None:                                      # the text did not reproduce the frame: whole file
            got = self.select(full() if seed is None else seed, key)
        del seed
        with self.lock:
            self.days[key] = got
            while len(self.days) > self.KEEP:
                self.days.popitem(last=False)
        return got.copy()

    def _read(self, key: str) -> pd.DataFrame | None:
        if not self.safe:
            return None
        raw = gzip.decompress(self.path.read_bytes())
        if len(raw) != self.size:
            return None
        want, c = key.encode(), self.col
        # every line holding `,key,` (or starting `key,`): kept when its field c is the key; the count must match
        needle = (b"\n" if c == 0 else b",") + want + b","
        pos, lines, lineno, last, i = [], [], 0, 0, raw.find(needle)
        while i >= 0:
            start = i + 1 if c == 0 else raw.rfind(b"\n", 0, i) + 1
            end = raw.find(b"\n", start)
            end = len(raw) if end < 0 else end
            line = raw[start:end]
            f = line.split(b",", c + 1)
            if start and len(f) > c and f[c] == want:
                lineno += raw.count(b"\n", last, start)
                last = start
                pos.append(lineno - 1)
                lines.append(line)
            i = raw.find(needle, end)
        if len(pos) != self.counts[key]:
            return None
        body = raw[:raw.find(b"\n")] + b"\n" + b"\n".join(lines) + b"\n"
        del raw, lines
        df = pd.read_csv(io.BytesIO(body), **self.kwargs())
        for col, dt in self.dtypes.items():
            if df[col].dtype == dt:
                continue
            if isinstance(dt, pd.CategoricalDtype) or (dt == np.float64 and df[col].dtype == np.int64):
                df[col] = df[col].astype(dt)                  # categories of the whole file; int -> float is exact
            else:
                return None
        if not df.dtypes.equals(self.dtypes) or list(df.columns) != list(self.dtypes.index) \
                or not df[self.day_col].eq(key).all():
            return None
        df.index = self.index_of(np.asarray(pos, dtype=np.intp))
        return df

    def kwargs(self) -> dict:
        raise NotImplementedError

    def index_of(self, pos: np.ndarray) -> pd.Index:
        """The index the whole-file selection of rows `pos` carries (taken the way `select` takes it)."""
        raise NotImplementedError

    def select(self, df: pd.DataFrame, key: str) -> pd.DataFrame:
        """The rows of `key` from the whole-file frame (the reader before the day index)."""
        raise NotImplementedError


class _ArrivalsIndex(_DayIndex):
    def kwargs(self) -> dict:
        return ARRIVALS_KWARGS

    def index_of(self, pos: np.ndarray) -> pd.Index:
        return pd.DataFrame(index=pd.RangeIndex(self.n)).iloc[pos].index

    def select(self, df: pd.DataFrame, key: str) -> pd.DataFrame:
        return df.iloc[np.flatnonzero((df[self.day_col] == key).to_numpy())]


class _FlightsIndex(_DayIndex):
    def kwargs(self) -> dict:
        return FLIGHTS2015_DAY_KWARGS

    def index_of(self, pos: np.ndarray) -> pd.Index:
        mask = np.zeros(self.n, dtype=bool)
        mask[pos] = True
        return pd.DataFrame(index=pd.RangeIndex(self.n))[mask].index        # all rows selected -> RangeIndex

    def select(self, df: pd.DataFrame, key: str) -> pd.DataFrame:
        return df[df[self.day_col] == key]


def _arrivals_frame(iata: str, raw: bytes | None = None) -> pd.DataFrame | None:
    """The whole frame of one destination hub, from the per-airport files written by scripts/split_arrivals.py (the
    1.2M-row arrivals.csv.gz is never loaded at runtime: 512 MB host). Not kept: `_arrivals` keeps what a day needs.
    `raw` is the file already decompressed (the same bytes the gzip path would stream to the parser)."""
    p = WAVES / "arrivals" / f"{iata}.csv.gz"
    if raw is None and not p.exists():
        return None
    # streamed in chunks (low parse peak), then categoricals rebuilt over the whole airport
    src = p if raw is None else io.BytesIO(raw)
    df = pd.concat(pd.read_csv(src, chunksize=20000, **ARRIVALS_KWARGS), ignore_index=True)
    for c in ("date", "carrier", "origin", "tail"):
        df[c] = df[c].astype("category")
    return df


@functools.lru_cache(maxsize=8)
def _arrivals(iata: str) -> _ArrivalsIndex | None:
    """The day index of one hub's arrivals file (None without one): a few hundred KB (the airport-wide categories)
    where the whole frame was ~3-4 MB, plus the last few day slices. `cache_clear` drops those too."""
    p = WAVES / "arrivals" / f"{iata}.csv.gz"
    if not p.exists():
        return None
    raw = gzip.decompress(p.read_bytes())                    # decompressed once, for the parse and the index
    return _ArrivalsIndex(p, _arrivals_frame(iata, raw), "date", raw)


REF = ROOT / "data/ref"


@functools.lru_cache(maxsize=4)
def _movements(iata: str, year: str = "2015") -> tuple[pd.DataFrame | None, str | None]:
    """(all rows as text, date column name) of one airport's data/ref/movements{year} file (2015, or 2024 for the
    hub months)."""
    p = REF / f"movements{year}" / f"{iata}.csv.gz"
    if not p.exists():
        return None, None
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    col = next((c for c in pack.MOVEMENT_DATE_COLS if c in df.columns), None)
    return df, col


_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _movements_text(iata: str, year: str = "2015") -> str | None:
    """The decompressed text of one airport's movements file (None without one). Not kept: `movements_day` reads a
    day through `_movements_index`; this is the seam tests replace (None forces the frame path)."""
    p = REF / f"movements{year}" / f"{iata}.csv.gz"
    try:
        st = p.stat()
    except OSError:
        return None
    return _read_text(p, st.st_size, st.st_mtime_ns)


def _read_text(p: Path, size: int, mtime_ns: int) -> str:
    return gzip.decompress(p.read_bytes()).decode()


_GZ_CHUNK = 1 << 16          # compressed bytes fed to the decompressor at a time
_CKPT_EVERY = 4 << 20        # decompressed bytes between two resume points (~40 KB of zlib state each)


class _MovementsIndex:
    """Where each day's rows sit in one movements file's decompressed text, and zlib resume points every ~4 MB of
    text, so a day is cut by decompressing at most ~4 MB plus its own rows. `days[date]` is (start, end) of the rows
    when `_day_block` would cut them, False when it would step aside; `safe` False means it steps aside for every
    date. Built in one pass over the text, which is then dropped (the text was kept before: 5-37 MB a file)."""

    def __init__(self, p: Path, head: bytes, safe: bool, days: dict, ckpts: list, commas: int):
        self.p, self.head, self.safe, self.days, self.ckpts, self.commas = p, head, safe, days, ckpts, commas
        self.ckpt_out = [c[1] for c in ckpts]

    def day_block(self, date: str) -> bytes | None | bool:
        """Exactly `_day_block(text, date)` of this file's text, encoded."""
        if not self.safe or not _ISO_DAY.fullmatch(date):
            return False
        span = self.days.get(date)
        if span is None or span is False:
            return span
        start, end = span
        in_pos, out_pos, d = self.ckpts[bisect.bisect_right(self.ckpt_out, start) - 1]
        d = d.copy()
        buf = bytearray()
        with open(self.p, "rb") as f:
            f.seek(in_pos)
            while out_pos + len(buf) < end:
                chunk = f.read(_GZ_CHUNK)
                if not chunk:
                    break
                buf += d.decompress(chunk)
        block = bytes(buf[start - out_pos:end - out_pos])
        if any(line.count(b",") != self.commas for line in block.rstrip(b"\n").split(b"\n")):
            return False                          # a short or long row (the frame path pads or rejects it)
        return self.head + b"\n" + block + (b"" if block.endswith(b"\n") else b"\n")


@functools.lru_cache(maxsize=8)
def _movements_index(p: Path, size: int, mtime_ns: int) -> _MovementsIndex | None:
    """The day index of one movements file, memoised on its path, size and mtime. None when the file is not one
    plain ASCII gzip member (the caller then cuts from the whole text, as before)."""
    raw = p.read_bytes()
    d = zlib.decompressobj(31)
    out = bytearray()
    ckpts = [(0, 0, d.copy())]
    for i in range(0, len(raw), _GZ_CHUNK):
        out += d.decompress(raw[i:i + _GZ_CHUNK])
        if d.eof:
            if d.unused_data or i + _GZ_CHUNK < len(raw):
                return None                       # several gzip members: gzip.decompress reads them all
            break
        if len(out) >= ckpts[-1][1] + _CKPT_EVERY:
            ckpts.append((i + _GZ_CHUNK, len(out), d.copy()))
    del raw
    if not d.eof or not out.isascii():
        return None                               # truncated (gzip raises) or non-ASCII (str offsets != byte offsets)
    nl0 = out.find(b"\n")
    head = bytes(out if nl0 < 0 else out[:nl0])
    cols = head.split(b",")
    safe = (cols[0].decode() in pack.MOVEMENT_DATE_COLS and len(set(cols)) == len(cols) and all(cols)
            and b'"' not in out and b"\r" not in out)
    days = _movement_days(out) if safe else {}
    return _MovementsIndex(p, head, safe, days, ckpts, len(cols) - 1)


def _movement_days(text: bytearray) -> dict:
    """{date: (start, end) | False} for every 10-character key that starts a data line followed by a comma, as
    `_day_block` judges it: its lines must be one contiguous run (else False); (start, end) spans the run, its last
    newline included. The comma count of each row is checked when the day is read."""
    arr = np.frombuffer(text, dtype=np.uint8)
    n = len(arr)
    nl = np.concatenate([np.flatnonzero(arr[a:a + (1 << 20)] == 10) + a   # chunked: no text-sized temporaries
                         for a in range(0, n, 1 << 20)] or [np.zeros(0, np.int64)])
    if len(nl) == 0:
        return {}
    starts = nl + 1                                # data line j starts after newline j
    ends = np.append(nl[1:] + 1, n)                # end of line j, its newline included
    ok = starts + 11 <= n
    key = np.zeros((len(starts), 16), dtype=np.uint8)   # the line's first 10 bytes, padded: two uint64 per line
    idx = np.flatnonzero(ok)
    cand = np.zeros(len(starts), dtype=bool)
    if len(idx):
        cand[idx] = arr[starts[idx] + 10] == 44
        idx = np.flatnonzero(cand)
        for k in range(10):                        # column by column: no (lines x 10) int64 index temporary
            key[idx, k] = arr[starts[idx] + k]
    if not len(idx):
        return {}
    k64 = key.view(np.uint64)
    # runs of consecutive lines with one key (lines with no key end a run)
    change = np.ones(len(starts), dtype=bool)
    change[1:] = (cand[1:] != cand[:-1]) | (k64[1:] != k64[:-1]).any(axis=1)
    run_lo = np.flatnonzero(change)
    run_hi = np.append(run_lo[1:], len(starts))    # exclusive
    keep = cand[run_lo]
    run_lo, run_hi = run_lo[keep], run_hi[keep]
    out: dict = {}
    for lo, hi in zip(run_lo.tolist(), run_hi.tolist()):
        day = key[lo, :10].tobytes().decode()
        if day in out:
            out[day] = False                       # a second run: not sorted by date
        else:
            out[day] = (int(starts[lo]), int(ends[hi - 1]))
    return out


def _day_block(text: str, date: str) -> bytes | None | bool:
    """The rows of `date` cut straight from the file text: exactly what `pack.gzip_csv` of the frame path writes
    (header + those rows, "\n"-terminated), or None when the date has no rows. False when this text cannot be cut
    safely, so the caller takes the frame path: the date is not the first column, a field may be quoted (a `"`),
    a "\r" line end, duplicate or empty header names, or the date's rows are not contiguous."""
    head = text[:text.find("\n")] if "\n" in text else text
    cols = head.split(",")
    if not _ISO_DAY.fullmatch(date) or cols[0] not in pack.MOVEMENT_DATE_COLS or len(set(cols)) != len(cols) \
            or not all(cols) or '"' in text or "\r" in text:
        return False
    key = f"\n{date},"
    start = text.find(key)
    if start < 0:
        return None
    pos, n = start + 1, 0
    while text.startswith(key[1:], pos):
        nl = text.find("\n", pos)
        pos = len(text) if nl < 0 else nl + 1
        n += 1
    if n != text.count(key):
        return False                       # rows of this date elsewhere too: not sorted by date
    block = text[start + 1:pos]
    commas = len(cols) - 1
    if any(line.count(",") != commas for line in block.rstrip("\n").split("\n")):
        return False                       # a short or long row (the frame path pads or rejects it)
    return (head + "\n" + block + ("" if block.endswith("\n") else "\n")).encode()


def _dest_frame(dest: str, raw: bytes | None = None) -> pd.DataFrame | None:
    """The whole 2015 flights frame of one destination (not kept: `_dest_index` keeps what a day needs). `raw` is
    the file already decompressed."""
    p = CH2 / "flights" / f"{dest}.csv.gz"
    if raw is None and not p.exists():
        return None
    return pd.read_csv(p if raw is None else io.BytesIO(raw), **FLIGHTS2015_DAY_KWARGS)


@functools.lru_cache(maxsize=8)
def _dest_index(dest: str) -> _FlightsIndex | None:
    p = CH2 / "flights" / f"{dest}.csv.gz"
    if not p.exists():
        return None
    raw = gzip.decompress(p.read_bytes())                    # decompressed once, for the parse and the index
    return _FlightsIndex(p, _dest_frame(dest, raw), "arr_date", raw)


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


_MOVEMENTS_TEXT = _movements_text


def _movements_cut(iata: str, year: str, date: str) -> bytes | None | bool:
    """`_day_block` of the airport's movements text for `date` (False: take the frame path), read through the day
    index. When a test has replaced `_movements_text` (the fixture seam), its text is used as before."""
    if _movements_text is not _MOVEMENTS_TEXT:
        text = _movements_text(iata, year)
        return False if text is None else _day_block(text, date)
    p = REF / f"movements{year}" / f"{iata}.csv.gz"
    try:
        st = p.stat()
    except OSError:
        return False
    ix = _movements_index(p, st.st_size, st.st_mtime_ns)
    if ix is None:
        return _day_block(_read_text(p, st.st_size, st.st_mtime_ns), date)
    return ix.day_block(date)


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

    def wave_rows_spread(self, per_airport: int) -> list[dict]:
        idx = self._wave_frame(None)
        idx = idx.assign(_rk=idx.groupby("airport", sort=False).cumcount())
        idx = idx[idx["_rk"] < max(per_airport, 0)].sort_values(["_rk", "airport"], kind="stable")
        return idx.drop(columns="_rk").to_dict("records")

    def wave_count(self, airport: str | None) -> int:
        return len(self._wave_frame(airport))

    def wave_summary(self, wave_id: str) -> dict | None:
        return None   # FileStore summaries are computed by build._summary

    def arrivals_day(self, iata: str, date: str) -> pd.DataFrame | None:
        ix = _arrivals(iata)
        return None if ix is None else ix.day(date, lambda: _arrivals_frame(iata))

    # ------------------------------------------------------------ geometry (empty until the flight-path M0)

    def procedures(self, icao: str) -> bytes | None:
        return self._ref_blob("procedures", icao)

    def surface(self, icao: str) -> bytes | None:
        return self._ref_blob("surface", icao)

    @staticmethod
    def _ref_blob(kind: str, icao: str) -> bytes | None:
        p = REF / kind / f"{icao.upper()}.json.gz"
        return p.read_bytes() if p.is_file() else None

    def movements_day(self, iata: str, date: str) -> bytes | None:
        year = date[:4] if date[:4].isdigit() else "2015"
        got = _movements_cut(iata.upper(), year, date)
        if got is not False:
            return None if got is None else pack.gzip_bytes(got)
        df, col = _movements(iata.upper(), year)
        if df is None or col is None:
            return None
        sub = df[df[col] == date]
        return None if sub.empty else pack.gzip_csv(sub)

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
        ix = _dest_index(dest)
        if ix is None:
            return None
        got = ix.day(arr_date, lambda: _dest_frame(dest))
        return ix.empty.copy() if got is None else got

    def search_flights2015(self, airport: str | None, q: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        from backend.wave import flights2015 as F   # lazy: flights2015 imports the store
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

    # ------------------------------------------------------------ IEM cache

    def iem_get(self, station: str, date: str) -> tuple[float, str | None] | None:
        p = self.paths.iem_dir / f"{station}_{date}.csv"
        try:
            return p.stat().st_mtime, p.read_text()
        except OSError:
            return None

    def iem_put(self, station: str, date: str, body: str | None) -> None:
        if body is None:
            return
        try:
            self.paths.iem_dir.mkdir(parents=True, exist_ok=True)
            (self.paths.iem_dir / f"{station}_{date}.csv").write_text(body)
        except OSError:
            pass

    # ------------------------------------------------------------ runs and decisions

    def save_run(self, result: dict) -> bool:
        d = self.paths.runs_dir
        try:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f".{result['run_id']}.{uuid.uuid4().hex[:6]}.tmp"
            tmp.write_text(json.dumps(result))
            os.replace(tmp, d / f"{result['run_id']}.json")
            return True
        except OSError:
            return False

    def get_run(self, run_id: str) -> dict | None:
        if not re.fullmatch(r"[0-9a-f]{12}", run_id):
            return None
        try:
            return json.loads((self.paths.runs_dir / f"{run_id}.json").read_text())
        except (OSError, ValueError):
            return None

    def _decided_ids(self) -> set[str]:
        try:
            lines = self.paths.decisions_path.read_text().splitlines()
        except OSError:
            return set()
        out = set()
        for ln in lines:
            try:
                rid = json.loads(ln).get("run_id")
            except (ValueError, AttributeError):
                continue
            if rid:
                out.add(str(rid))
        return out

    def list_runs(self, wave_id: str, limit: int) -> list[dict]:
        d = self.paths.runs_dir
        if not d.is_dir():
            return []
        found = []
        for p in d.glob("*.json"):
            try:
                found.append((p.stat().st_mtime, json.loads(p.read_text())))
            except (OSError, ValueError):
                continue
        decided = self._decided_ids()
        found.sort(key=lambda t: t[0], reverse=True)
        out = []
        for mtime, r in found:
            if r.get("wave_id") != wave_id:
                continue
            out.append({"run_id": r.get("run_id"), "wave_id": r.get("wave_id"), "mode": r.get("mode"),
                        "model": r.get("model"), "outcome": r.get("outcome"),
                        "created_at": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
                        "decided": r.get("run_id") in decided})
            if len(out) >= limit:
                break
        return out

    def record_decision(self, row: dict) -> bool:
        try:
            self.paths.decisions_path.parent.mkdir(parents=True, exist_ok=True)
            with self.paths.decisions_path.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            return True
        except OSError:
            return False

    # ------------------------------------------------------------ replays and evaluation

    def replay(self, wave_id: str) -> dict | None:
        try:
            return json.loads((self.paths.replay_dir / f"{wave_id}.json").read_text())
        except (OSError, ValueError):
            return None

    def replay_ids(self) -> set[str]:
        d = self.paths.replay_dir
        return {p.stem for p in d.glob("*.json") if p.is_file()} if d.is_dir() else set()

    def put_replay(self, wave_id: str, body: dict, engine_version: str = "v2") -> None:
        self.paths.replay_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.replay_dir / f"{wave_id}.json").write_text(json.dumps(body))

    def benchmark_current(self) -> dict | None:
        try:
            return json.loads(self.paths.benchmark_path.read_text())
        except (OSError, ValueError):
            return None

    def benchmark_report(self, label: str) -> dict | None:
        if not re.fullmatch(r"[a-z0-9-]+", label):
            return None
        p = self.paths.benchmark_path if label == "main" else self.paths.benchmark_path.parent / label / "benchmark.json"
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None

    def put_benchmark(self, report: dict, markdown: str | None, label: str, current: bool = True) -> None:
        if not current:
            return
        self.paths.benchmark_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.benchmark_path.write_text(json.dumps(report, indent=2))

    def v1_eval(self, name: str) -> dict | list | None:
        try:
            return json.loads((self.paths.root / "eval" / f"{name}.json").read_text())
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------ other

    def session(self) -> AbstractContextManager[None]:
        return nullcontext()
