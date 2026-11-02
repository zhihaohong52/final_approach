"""FallbackStore: serve from Postgres, fall back to the file store when it is down."""
from __future__ import annotations

import logging
import threading
import time
from contextlib import AbstractContextManager

import psycopg
from psycopg_pool import PoolTimeout

from . import pg
from .base import DataUnavailable, Store, StoreStatus
from .files import FileStore

log = logging.getLogger(__name__)

_FALLBACK_ERRORS = (psycopg.OperationalError, PoolTimeout, DataUnavailable, NotImplementedError)
_WRITES = ("save_run", "record_decision", "put_replay", "put_benchmark", "iem_put")


class FallbackStore:
    def __init__(self, primary: Store, secondary: FileStore, *, threshold: int = 3,
                 cooldown_s: float = 60.0, clock=time.monotonic):
        self.primary = primary
        self.secondary = secondary
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False
        self._last_error: str | None = None
        self._read_reason: str | None = None   # cleared by a later successful primary read
        self._write_reason: str | None = None  # cleared only by a later successful primary write

    def _admit(self) -> str:
        """'primary' (closed), 'trial' (the single half-open probe) or 'secondary' (breaker open)."""
        with self._lock:
            if self._opened_at is None:
                return "primary"
            if self._clock() - self._opened_at < self._cooldown_s or self._trial_in_flight:
                return "secondary"
            self._trial_in_flight = True
            return "trial"

    def _call(self, name: str, *args, **kwargs):
        is_write = name in _WRITES
        route = self._admit()
        if route == "secondary":
            with self._lock:
                reason = f"{name}: breaker open ({self._last_error})" if self._last_error else f"{name}: breaker open"
                self._mark(is_write, reason)
            return getattr(self.secondary, name)(*args, **kwargs)
        trial = route == "trial"
        try:
            result = pg.with_retry(lambda: getattr(self.primary, name)(*args, **kwargs))
        except NotImplementedError:
            # not built yet: serve files, but this is not an outage (no breaker, no degraded status)
            return getattr(self.secondary, name)(*args, **kwargs)
        except _FALLBACK_ERRORS as e:
            cls = type(e).__name__
            log.warning("store fallback: method=%s error=%s sqlstate=%s", name, cls, getattr(e, "sqlstate", None))
            with self._lock:
                self._last_error = cls
                self._failures += 1
                if trial or self._failures >= self._threshold:
                    self._opened_at = self._clock()
                self._mark(is_write, f"{name}: {cls}")
            return getattr(self.secondary, name)(*args, **kwargs)
        else:
            with self._lock:
                self._failures = 0
                self._opened_at = None
                self._last_error = None
                if is_write:
                    self._write_reason = None
                    self._read_reason = None
                else:
                    self._read_reason = None
            return result
        finally:
            if trial:
                with self._lock:
                    self._trial_in_flight = False

    def _mark(self, is_write: bool, reason: str) -> None:
        if is_write:
            self._write_reason = reason
        else:
            self._read_reason = reason

    def session(self) -> AbstractContextManager[None]:
        return self.primary.session()

    def status(self) -> StoreStatus:
        with self._lock:
            reason = self._write_reason or self._read_reason
            if reason is None and self._opened_at is not None:
                reason = f"breaker open ({self._last_error})"
            if reason is not None:
                return StoreStatus("files", True, reason, False)
        return StoreStatus("postgres", False, None, True)


def _delegate(name: str):
    def method(self, *args, **kwargs):
        return self._call(name, *args, **kwargs)
    method.__name__ = name
    return method


for _name in pg.STORE_METHODS:
    if _name != "status":
        setattr(FallbackStore, _name, _delegate(_name))
