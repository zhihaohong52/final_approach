"""FallbackStore: serve from Postgres, fall back to the file store when it is down."""
from __future__ import annotations

import logging
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
        self._failures = 0
        self._opened_at: float | None = None
        self._last_fallback: str | None = None  # "<method>: <error class>" of the last fallback

    def _breaker_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self._clock() - self._opened_at >= self._cooldown_s:
            return False  # half-open: allow one trial call
        return True

    def _call(self, name: str, *args, **kwargs):
        if self._breaker_open():
            self._last_fallback = f"{name}: breaker open"
            return getattr(self.secondary, name)(*args, **kwargs)
        try:
            result = pg.with_retry(lambda: getattr(self.primary, name)(*args, **kwargs))
        except _FALLBACK_ERRORS as e:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = self._clock()
            cls = type(e).__name__
            log.warning("store fallback: method=%s error=%s sqlstate=%s", name, cls, getattr(e, "sqlstate", None))
            self._last_fallback = f"{name}: {cls}"
            return getattr(self.secondary, name)(*args, **kwargs)
        self._failures = 0
        self._opened_at = None
        self._last_fallback = None
        return result

    def session(self) -> AbstractContextManager[None]:
        return self.primary.session()

    def status(self) -> StoreStatus:
        if self._last_fallback is not None or self._breaker_open():
            return StoreStatus("files", True, self._last_fallback, False)
        return StoreStatus("postgres", False, None, True)


def _delegate(name: str):
    def method(self, *args, **kwargs):
        return self._call(name, *args, **kwargs)
    method.__name__ = name
    return method


for _name in pg.STORE_METHODS:
    if _name != "status":
        setattr(FallbackStore, _name, _delegate(_name))
