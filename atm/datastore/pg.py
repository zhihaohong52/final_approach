"""Postgres connection layer: pooled connections (Neon pgbouncer), lazy open, bounded retry.

Store methods are added in later tasks; until then they raise NotImplementedError and the
FallbackStore serves the file backend.
"""
from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

import psycopg
from psycopg_pool import NullConnectionPool, PoolTimeout

T = TypeVar("T")

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
    def __init__(self, url: str):
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


def _unimplemented(name: str):
    def method(self, *args, **kwargs):
        raise NotImplementedError(f"PgStore.{name}")
    method.__name__ = name
    return method


for _name in STORE_METHODS:
    setattr(PgStore, _name, _unimplemented(_name))
