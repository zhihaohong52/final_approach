"""Datastore selection. psycopg is imported lazily, only when the Postgres backend is chosen."""
from __future__ import annotations

import os

from .base import DataUnavailable, Store, StoreStatus
from .files import FileStore

__all__ = ["DataUnavailable", "Store", "StoreStatus", "FileStore", "get_store", "set_store"]

_store: Store | None = None


def _build() -> Store:
    mode = os.environ.get("ATM_STORE")
    if mode == "files":
        return FileStore()
    if mode == "pg" or os.environ.get("DATABASE_URL_POOLED"):
        from .fallback import FallbackStore
        from .pg import PgStore

        url = os.environ.get("DATABASE_URL_POOLED")
        if not url:
            raise RuntimeError("ATM_STORE=pg requires DATABASE_URL_POOLED to be set")
        primary = PgStore(url)
        primary.open()
        return FallbackStore(primary, FileStore())
    return FileStore()


def get_store() -> Store:
    global _store
    if _store is None:
        _store = _build()
    return _store


def set_store(store: Store | None) -> None:
    global _store
    _store = store
