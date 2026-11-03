"""Postgres connection layer: pooled connections (Neon pgbouncer), lazy open, bounded retry.

Methods not built yet raise NotImplementedError and the FallbackStore serves the file backend.
"""
from __future__ import annotations

import copy
import csv
import gzip
import io
import logging
import random
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import NullConnectionPool, PoolTimeout

from . import pack
from .base import DataUnavailable
from .datasets import ARRIVALS_KWARGS, DATASETS, FLIGHTS2015_DAY_KWARGS

T = TypeVar("T")
log = logging.getLogger(__name__)

PRUNE_EVERY_S = 3600
IEM_MAX_BYTES = 40 * 1024 * 1024
IEM_KEEP_BYTES = 30 * 1024 * 1024
_UNSET = object()
ARRIVALS_LRU = 16
FLIGHTS_DAY_LRU = 64
REPLAY_LRU = 8
_DOTZERO = re.compile(r"\.0(?=;|$)")


class _LRU:
    """Small thread-safe LRU keyed by anything hashable."""

    def __init__(self, size: int):
        self._size, self._d, self._lock = size, OrderedDict(), threading.Lock()

    def get(self, key, default=_UNSET):
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
        return default

    def put(self, key, value) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self._size:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


def _engine_version() -> str:
    try:
        from atm.wave.version import ENGINE_VERSION
        return ENGINE_VERSION
    except ImportError:
        return "v2"

STORE_METHODS = (
    "frame", "rows", "workbook_rows",
    "wave_row", "wave_rows", "wave_count", "wave_summary", "arrivals_day", "metar_2024_rows",
    "flight2015_row", "flights2015_day", "search_flights2015",
    "iem_get", "iem_put",
    "save_run", "get_run", "list_runs", "record_decision",
    "replay", "replay_ids", "put_replay", "benchmark_current", "put_benchmark", "v1_eval",
    "procedures", "surface", "movements_day",
    "status",
)


def make_pool(url: str) -> NullConnectionPool:
    # Neon's pooled endpoint is pgbouncer in transaction mode: no server-side prepared statements.
    # TCP keepalives (libpq connect parameters, not SET) detect a dead peer during a long query instead of hanging;
    # statement_timeout lives on the role (migration 0006), since SET does not survive transaction pooling.
    return NullConnectionPool(
        conninfo=url,
        max_size=4,
        timeout=10,
        open=False,
        kwargs={"prepare_threshold": None, "connect_timeout": 10, "application_name": "final-approach",
                "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3},
    )


def with_retry(fn: Callable[[], T], *, delays=(0.5, 2.0), jitter=0.3, sleep=None) -> T:
    """Call fn, retrying only on psycopg.OperationalError with jittered backoff."""
    if sleep is None:
        sleep = time.sleep  # resolved at call time so tests can patch pg.time.sleep
    for delay in delays:
        try:
            return fn()
        except PoolTimeout:
            raise  # already waited the pool timeout; retrying would stall the request further
        except psycopg.errors.QueryCanceled:
            raise  # hit statement_timeout: the same query would time out again
        except psycopg.OperationalError:
            sleep(delay + random.uniform(0, jitter * delay))
    return fn()


class PgStore:
    run_cap: int = 2000

    def __init__(self, url: str):
        self._last_prune = 0.0
        self._last_iem_check = 0.0
        self._throttle_lock = threading.Lock()
        self._replay_ids = None
        self._benchmark = _UNSET
        self._pool = make_pool(url)
        self._open_lock = threading.Lock()
        self._opened = False
        self._local = threading.local()
        self._frames: dict = {}                       # per dataset name; the reference tables are small and static
        self._rows: dict = {}
        self._workbook: dict = {}
        self._arrivals = _LRU(ARRIVALS_LRU)
        self._flights_day = _LRU(FLIGHTS_DAY_LRU)
        self._replays = _LRU(REPLAY_LRU)              # replay bodies by wave id; cleared by put_replay

    def open(self) -> None:
        with self._open_lock:
            if not self._opened:
                self._pool.open(wait=False)
                self._opened = True

    def close(self) -> None:
        with self._open_lock:
            if self._opened:
                self._pool.close()
                self._opened = False

    @contextmanager
    def session(self) -> Iterator[None]:
        """Bind one pooled connection to this thread. Lazy: the connection is taken on first use."""
        if getattr(self._local, "active", False):  # nested: share the outer session
            yield
            return
        self._local.active = True
        self._local.conn = None
        try:
            yield
        finally:
            conn = self._local.conn
            self._local.active = False
            self._local.conn = None
            if conn is not None:
                self._pool.putconn(conn)

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        self.open()
        if getattr(self._local, "active", False):
            if self._local.conn is None:
                self._local.conn = self._pool.getconn()
            conn = self._local.conn
            try:
                yield conn
                conn.commit()
            except psycopg.OperationalError:
                # broken connection: hand it back (the pool discards it) so a retry gets a fresh one
                self._local.conn = None
                self._pool.putconn(conn)
                raise
            except BaseException:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            return
        with self._pool.connection() as conn:
            yield conn


    # ------------------------------------------------------------ reference

    def _copy_text(self, sql: str, params=None) -> str:
        """Run `copy (...) to stdout` and return the whole output as text."""
        buf = bytearray()
        with self._conn() as conn:
            with conn.cursor() as cur:
                with cur.copy(sql, params) as cp:
                    for chunk in cp:
                        buf += chunk
        return buf.decode("utf-8")

    @staticmethod
    def _dataset_select(name: str, header: bool) -> str:
        ds = DATASETS[name]
        cols = ", ".join(f'"{c.col}"{"::text" if c.pgtype == "date" else ""} as "{c.src}"' for c in ds.columns)
        return (f"copy (select {cols} from {ds.table} order by ord) to stdout "
                f"with (format csv, header {'true' if header else 'false'})")

    def _dataset_csv(self, name: str, header: bool) -> str:
        text = self._copy_text(self._dataset_select(name, header))
        if text.count("\n") <= (1 if header else 0):    # header only (or nothing): the table was never seeded
            raise DataUnavailable(f"{DATASETS[name].table} is empty")
        return text

    def frame(self, name: str) -> pd.DataFrame | None:
        ds = DATASETS[name]
        df = self._frames.get(name)
        if df is None:
            df = self._frames[name] = pd.read_csv(io.StringIO(self._dataset_csv(name, ds.header)), **ds.read_kwargs)
        return df.copy()

    def rows(self, name: str) -> list[dict[str, str]] | None:
        ds = DATASETS[name]
        rows = self._rows.get(name)
        if rows is None:
            text = io.StringIO(self._dataset_csv(name, False))
            rows = self._rows[name] = list(csv.DictReader(text, fieldnames=[c.src for c in ds.columns]))
        return [dict(r) for r in rows]

    def workbook_rows(self, sheet: str) -> list[tuple]:
        rows = self._workbook.get(sheet)
        if rows is None:
            def go():
                with self._conn() as conn:
                    return conn.execute("select cells from ref.workbook where sheet = %s order by row_no",
                                        (sheet,)).fetchall()
            got = go()
            if not got:
                raise DataUnavailable(f"ref.workbook has no sheet {sheet}")
            rows = self._workbook[sheet] = [tuple(r[0]) for r in got]
        return list(rows)

    # ------------------------------------------------------------ 2024 waves

    _WAVE_COLS = "wave_id, airport, date::text, hour, n_arrivals, n_disrupted, disrupted_flights"

    @staticmethod
    def _wave_dict(r: tuple) -> dict:
        return {"wave_id": r[0], "airport": r[1], "date": r[2], "hour": r[3], "n_arrivals": r[4],
                "n_disrupted": r[5], "disrupted_flights": _DOTZERO.sub("", r[6])}   # callsigns, as FileStore

    def wave_row(self, wave_id: str) -> dict | None:
        with self._conn() as conn:
            r = conn.execute(f"select {self._WAVE_COLS} from ref.waves where wave_id = %s", (wave_id,)).fetchone()
        return None if r is None else self._wave_dict(r)

    @staticmethod
    def _wave_where(airport: str | None, q: str | None) -> tuple[str, list]:
        where, params = [], []
        if airport:
            where.append("airport = %s")
            params.append(airport.upper())
        if q:
            pat = "%" + pack.like_escape(q.strip().upper()) + "%"
            where.append("(upper(wave_id) like %s escape '\\' or "
                         "upper(regexp_replace(disrupted_flights, '\\.0(;|$)', '\\1', 'g')) like %s escape '\\')")
            params += [pat, pat]
        return (" where " + " and ".join(where)) if where else "", params

    def wave_rows(self, airport: str | None, q: str | None, limit: int, offset: int = 0) -> list[dict]:
        where, params = self._wave_where(airport, q)
        with self._conn() as conn:
            got = conn.execute(f"select {self._WAVE_COLS}, summary from ref.waves{where} order by ord limit %s offset %s",
                               params + [max(limit, 0), max(offset, 0)]).fetchall()
        return [{**self._wave_dict(r), "summary": r[7]} for r in got]

    def wave_count(self, airport: str | None) -> int:
        where, params = self._wave_where(airport, None)
        with self._conn() as conn:
            return conn.execute(f"select count(*) from ref.waves{where}", params).fetchone()[0]

    def wave_summary(self, wave_id: str) -> dict | None:
        with self._conn() as conn:
            r = conn.execute("select summary from ref.waves where wave_id = %s", (wave_id,)).fetchone()
        if r is None:
            return None
        return {**r[0], "replay_available": wave_id in self.replay_ids()}

    def arrivals_day(self, iata: str, date: str) -> pd.DataFrame | None:
        key = (iata, date)
        hit = self._arrivals.get(key)
        if hit is not _UNSET:
            return None if hit is None else hit.copy()
        with self._conn() as conn:
            r = conn.execute("select payload from ref.bts_arrivals_day where airport = %s and date = %s::date",
                             (iata, date)).fetchone()
        df = None if r is None else pd.read_csv(io.BytesIO(gzip.decompress(r[0])), **ARRIVALS_KWARGS)
        self._arrivals.put(key, df)
        return None if df is None else df.copy()

    def procedures(self, icao: str) -> bytes | None:
        return self._geo_blob("select doc from geo.procedures where icao = %s", (icao.upper(),))

    def surface(self, icao: str) -> bytes | None:
        return self._geo_blob("select doc from geo.surface where icao = %s", (icao.upper(),))

    def movements_day(self, iata: str, date: str) -> bytes | None:
        return self._geo_blob("select payload from geo.movements2015_day where airport = %s and date = %s::date",
                              (iata.upper(), date))

    def _geo_blob(self, sql: str, params: tuple) -> bytes | None:
        with self._conn() as conn:
            r = conn.execute(sql, params).fetchone()
        return None if r is None else bytes(r[0])

    def metar_2024_rows(self, iata: str) -> list[dict[str, str]]:
        """Rows as strings. Real columns (drct, sknt, ...) are numerically equal to the file's text ("90" for
        "90.00"), not byte-identical; every reader parses them with float."""
        cols = ("station", "airport", "valid_utc", "local_hour_key", "drct", "sknt", "gust", "vsby", "ceiling_ft",
                "wxcodes", "raw")
        sel = ", ".join(f"coalesce({c}::text, '')" for c in cols)
        with self._conn() as conn:
            got = conn.execute(f"select {sel} from ref.metar_2024 where airport = %s order by ord", (iata,)).fetchall()
        return [dict(zip(cols, r)) for r in got]

    # ------------------------------------------------------------ 2015 flights

    def flight2015_row(self, row_id: int) -> dict | None:
        keys = ("row_id", "date", "carrier", "flight_no", "tail", "origin", "dest", "sched_arr", "arr_delay",
                "weather_delay", "late_aircraft_delay", "diverted", "cancelled", "arr_date")
        sel = ", ".join(f"coalesce({k}::text, '')" for k in keys)
        with self._conn() as conn:
            r = conn.execute(f"select {sel} from ref.flights2015 where row_id = %s and cancelled <> 1",
                             (row_id,)).fetchone()
        return None if r is None else dict(zip(keys, r))

    def flights2015_day(self, dest: str, arr_date: str) -> pd.DataFrame | None:
        key = (dest, arr_date)
        hit = self._flights_day.get(key)
        if hit is not _UNSET:
            return None if hit is None else hit.copy()
        cols = ("row_id, date::text as date, arr_date::text as arr_date, carrier, flight_no, tail, origin, sched_dep, "
                "sched_arr, arr_delay, weather_delay, late_aircraft_delay, nas_delay, carrier_delay, diverted, cancelled, "
                "wheels_on, taxi_in, wheels_off, taxi_out")
        text = self._copy_text(f"copy (select {cols} from ref.flights2015 where dest = %s and arr_date = %s::date "
                               f"order by ord) to stdout with (format csv, header true)", (dest, arr_date))
        df = pd.read_csv(io.StringIO(text), **FLIGHTS2015_DAY_KWARGS)
        if df.empty:
            with self._conn() as conn:
                known = conn.execute("select exists (select 1 from ref.flights2015 where dest = %s)", (dest,)).fetchone()[0]
            if not known:
                df = None      # no file for this destination, as FileStore
        self._flights_day.put(key, df)
        return None if df is None else df.copy()

    def search_flights2015(self, airport: str | None, q: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        from atm.wave import flights2015 as F   # lazy: flights2015 imports the store
        # supported = a destination with runway data (heading_true set), as FileStore. `cancelled = 0` (not <> 1)
        # lets the partial indexes serve the listing; the column is a 0/1 flag.
        where = ["cancelled = 0", "dest in (select airport from ref.runways where heading_true is not null)"]
        params: list = []
        if airport and airport.strip():
            where.append("dest = %s")
            params.append(airport.strip().upper())
        if q and q.strip():
            qq = q.strip().upper()
            pat = "%" + pack.like_escape(qq) + "%"
            ors = [f"{c} ilike %s escape '\\'" for c in ("callsign", "tail", "origin", "dest")]
            params += [pat] * 4
            digits = qq[len(F.PREFIX):] if qq.startswith(F.PREFIX) else qq
            if digits.isdigit():
                ors.append("row_id::text like %s")
                params.append("%" + digits + "%")
            where.append("(" + " or ".join(ors) + ")")
        w = " where " + " and ".join(where)
        with self._conn() as conn:
            got = conn.execute(
                "select row_id, date::text, carrier, callsign, origin, dest, sched_arr, arr_delay, weather_delay, "
                f"late_aircraft_delay, count(*) over () from ref.flights2015{w} order by ord limit %s offset %s",
                params + [max(limit, 0), max(offset, 0)]).fetchall()
            if got:
                total = got[0][10]
            else:    # an empty page (limit 0, or offset past the end) carries no window count
                total = conn.execute(f"select count(*) from ref.flights2015{w}", params).fetchone()[0]
        keys = ("row_id", "date", "carrier", "callsign", "origin", "dest", "sched_arr", "arr_delay", "weather_delay",
                "late_aircraft_delay")
        return [F.flight_row(dict(zip(keys, r))) for r in got], total

    # ------------------------------------------------------------ IEM cache

    def iem_get(self, station: str, date: str) -> tuple[float, str | None] | None:
        def go():
            with self._conn() as conn:
                return conn.execute("select fetched_at, body from live.iem_cache where station = %s and date = %s::date",
                                    (station, date)).fetchone()
        row = go()
        return None if row is None else (row[0].timestamp(), row[1])

    def iem_put(self, station: str, date: str, body: str | None) -> None:
        if body is None:
            return

        def go():
            with self._conn() as conn:
                conn.execute(
                    "insert into live.iem_cache (station, date, body) values (%s, %s::date, %s) "
                    "on conflict (station, date) do update set body = excluded.body, fetched_at = now()",
                    (station, date, body))
        go()
        try:
            self._trim_iem()
        except Exception:
            log.warning("iem cache trim failed", exc_info=True)

    def _trim_iem(self, force: bool = False) -> int:
        """Once the cache table passes 40 MB, delete the oldest-fetched rows down to ~30 MB. Hourly."""
        with self._throttle_lock:
            now = time.monotonic()
            if not force and self._last_iem_check and now - self._last_iem_check < PRUNE_EVERY_S:
                return 0
            self._last_iem_check = now
        with self._conn() as conn:
            # live data size, not relation size: bloat persists until vacuum and would erode the cache
            size, n = conn.execute(
                "select coalesce(sum(octet_length(body)), 0), count(*) from live.iem_cache").fetchone()
            if size <= IEM_MAX_BYTES:
                return 0
            keep = int(n * IEM_KEEP_BYTES / size)
            return conn.execute(
                "delete from live.iem_cache where (station, date) in "
                "(select station, date from live.iem_cache order by fetched_at desc, station, date offset %s)",
                (keep,)).rowcount

    # ------------------------------------------------------------ runs and decisions

    def save_run(self, result: dict) -> bool:
        def go():
            with self._conn() as conn:
                conn.execute(
                    "insert into live.runs (run_id, wave_id, mode, model, outcome, engine_version, result) "
                    "values (%s, %s, %s, %s, %s, %s, %s) "
                    "on conflict (run_id) do update set result = excluded.result",
                    (result["run_id"], result["wave_id"], result["mode"], result.get("model"), result.get("outcome"),
                     result.get("engine_version") or "v2", Jsonb(result)))
        go()
        try:
            self._prune_runs()
        except Exception:
            log.warning("run retention prune failed", exc_info=True)
        return True

    def _prune_runs(self, force: bool = False) -> int:
        """Delete unpinned runs beyond the newest `run_cap`. Throttled to once an hour per process."""
        with self._throttle_lock:
            now = time.monotonic()
            if not force and self._last_prune and now - self._last_prune < PRUNE_EVERY_S:
                return 0
            self._last_prune = now
        with self._conn() as conn:
            return conn.execute(
                "delete from live.runs where run_id in "
                "(select run_id from live.runs where not pinned order by created_at desc, run_id offset %s)",
                (max(self.run_cap, 0),)).rowcount

    def get_run(self, run_id: str) -> dict | None:
        def go():
            with self._conn() as conn:
                return conn.execute("select result from live.runs where run_id = %s", (run_id,)).fetchone()
        row = go()
        return None if row is None else row[0]

    def list_runs(self, wave_id: str, limit: int) -> list[dict]:
        limit = max(1, min(int(limit), 100))

        def go():
            with self._conn() as conn:
                return conn.execute(
                    "select r.run_id, r.wave_id, r.mode, r.model, r.outcome, r.created_at, "
                    "exists (select 1 from live.decisions d where d.run_id = r.run_id) "
                    "from live.runs r where r.wave_id = %s order by r.created_at desc, r.run_id limit %s",
                    (wave_id, limit)).fetchall()
        return [{"run_id": r[0], "wave_id": r[1], "mode": r[2], "model": r[3], "outcome": r[4],
                 "created_at": r[5].isoformat(), "decided": r[6]} for r in go()]

    def record_decision(self, row: dict) -> bool:
        with self._conn() as conn:    # one transaction: insert the decision, pin its run
            conn.execute(
                "insert into live.decisions (run_id, flight_id, plan_id, decision, note, edits) "
                "values (%s, %s, %s, %s, %s, %s)",
                (row.get("run_id"), row.get("flight_id"), row.get("plan_id"), row["decision"],
                 row.get("note") or "", Jsonb(row.get("edits") or [])))
            if row.get("run_id"):
                conn.execute("update live.runs set pinned = true where run_id = %s", (row["run_id"],))
        return True

    # ------------------------------------------------------------ replays and evaluation

    def replay(self, wave_id: str) -> dict | None:
        """The newest recording for the wave, preferring the current engine version. Like FileStore (one file per
        wave, last write wins), an older-version body is still returned, so a version guard can read its
        `engine_version` and refuse what it cannot replay."""
        hit = self._replays.get(wave_id)
        if hit is not _UNSET:
            return copy.deepcopy(hit)
        with self._conn() as conn:
            row = conn.execute("select body from eval.replays where wave_id = %s "
                               "order by (engine_version = %s) desc, recorded_at desc limit 1",
                               (wave_id, _engine_version())).fetchone()
        body = None if row is None else row[0]
        self._replays.put(wave_id, body)
        return copy.deepcopy(body)

    def replay_ids(self) -> set[str]:
        """Waves with a recording of any engine version (the demo list), as FileStore."""
        cached = self._replay_ids
        if cached is None:
            def go():
                with self._conn() as conn:
                    return conn.execute("select distinct wave_id from eval.replays").fetchall()
            cached = self._replay_ids = {r[0] for r in go()}
        return set(cached)

    def put_replay(self, wave_id: str, body: dict, engine_version: str | None = None) -> None:
        engine_version = engine_version or _engine_version()

        def go():
            with self._conn() as conn:
                conn.execute(
                    "insert into eval.replays (wave_id, engine_version, body) values (%s, %s, %s) "
                    "on conflict (wave_id, engine_version) do update set body = excluded.body, recorded_at = now()",
                    (wave_id, engine_version, Jsonb(body)))
        go()
        self._replay_ids = None
        self._replays.clear()

    def benchmark_current(self) -> dict | None:
        cached = self._benchmark
        if cached is _UNSET:
            def go():
                with self._conn() as conn:
                    return conn.execute("select report from eval.benchmark_reports where is_current").fetchone()
            row = go()
            cached = self._benchmark = None if row is None else row[0]
        return copy.deepcopy(cached)

    def put_benchmark(self, report: dict, markdown: str | None, label: str, current: bool = True) -> None:
        with self._conn() as conn:    # one transaction: the previous current row is cleared with the insert
            if current:
                conn.execute("update eval.benchmark_reports set is_current = false where is_current")
            conn.execute(
                "insert into eval.benchmark_reports (label, is_current, report, markdown) values (%s, %s, %s, %s)",
                (label, current, Jsonb(report), markdown))
        self._benchmark = _UNSET

    def v1_eval(self, name: str) -> dict | list | None:
        def go():
            with self._conn() as conn:
                return conn.execute("select body from eval.v1_eval where name = %s", (name,)).fetchone()
        row = go()
        return None if row is None else row[0]


def _unimplemented(name: str):
    def method(self, *args, **kwargs):
        raise NotImplementedError(f"PgStore.{name}")
    method.__name__ = name
    return method


for _name in STORE_METHODS:
    if _name not in PgStore.__dict__:
        setattr(PgStore, _name, _unimplemented(_name))
