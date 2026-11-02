"""Postgres connection layer: pooled connections (Neon pgbouncer), lazy open, bounded retry.

Store methods are added in later tasks; until then they raise NotImplementedError and the
FallbackStore serves the file backend.
"""
from __future__ import annotations

import copy
import logging
import random
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import NullConnectionPool, PoolTimeout

T = TypeVar("T")
log = logging.getLogger(__name__)

PRUNE_EVERY_S = 3600
IEM_MAX_BYTES = 40 * 1024 * 1024
IEM_KEEP_BYTES = 30 * 1024 * 1024
_UNSET = object()


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
    return NullConnectionPool(
        conninfo=url,
        max_size=4,
        timeout=10,
        open=False,
        kwargs={"prepare_threshold": None, "connect_timeout": 10, "application_name": "final-approach"},
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
        def go():
            with self._conn() as conn:
                return conn.execute("select body from eval.replays where wave_id = %s and engine_version = %s",
                                    (wave_id, _engine_version())).fetchone()
        row = go()
        return None if row is None else row[0]

    def replay_ids(self) -> set[str]:
        cached = self._replay_ids
        if cached is None:
            def go():
                with self._conn() as conn:
                    return conn.execute("select wave_id from eval.replays where engine_version = %s",
                                        (_engine_version(),)).fetchall()
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
