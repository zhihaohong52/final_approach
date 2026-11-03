"""Import backend.wave.<name>, falling back to backend.wave.stub only if that module itself is missing."""
from __future__ import annotations

import importlib
import logging
import os

log = logging.getLogger(__name__)


def _load(name: str):
    try:
        return importlib.import_module(f"backend.wave.{name}")
    except ModuleNotFoundError as e:
        if e.name != f"backend.wave.{name}":
            raise  # a dependency of the real module is broken: surface it, never hide behind the stub
        if os.environ.get("ATM_REQUIRE_REAL") == "1":
            raise
        log.warning("backend.wave.%s not found; using backend.wave.stub", name)
        return importlib.import_module("backend.wave.stub")
