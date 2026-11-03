import os

import backend  # noqa: F401  (its .env loader runs now, before the URLs are stripped below)
import pytest

TEST_DB_URL = os.environ.get("DATABASE_TEST_URL")
for _k in ("DATABASE_URL_POOLED", "DATABASE_URL", "DATABASE_TEST_URL"):
    os.environ.pop(_k, None)
os.environ["ATM_STORE"] = "files"
os.environ["ATM_WARM"] = "0"   # no startup warm-up thread under tests (tests/test_warm.py turns it on)

from backend import datastore  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _reset_store():
    datastore.set_store(None)


@pytest.fixture(autouse=True)
def _fresh_waves():
    """build_wave caches by wave id: tests swap stores and monkeypatch builder internals, so each starts cold."""
    from backend.wave import build
    build.build_wave.cache_clear()
    yield
    build.build_wave.cache_clear()


@pytest.fixture
def live_db_url(monkeypatch):
    if os.environ.get("ATM_LIVE_DB") != "1":
        pytest.skip("live_db tests are opt-in: set ATM_LIVE_DB=1 (and DATABASE_TEST_URL)")
    if TEST_DB_URL is None:
        pytest.skip("DATABASE_TEST_URL not set")
    monkeypatch.setenv("DATABASE_TEST_URL", TEST_DB_URL)
    return TEST_DB_URL


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """TestClient over the stub-backed app with a FileStore in tmp_path (shared by the API test files)."""
    from fastapi.testclient import TestClient

    from backend import server
    from backend.datastore.files import FilePaths, FileStore
    from backend.wave import stub
    monkeypatch.setattr(server, "WB", stub)
    monkeypatch.setattr(server, "GR", stub)
    datastore.set_store(FileStore(FilePaths(runs_dir=tmp_path / "runs", decisions_path=tmp_path / "decisions.jsonl",
                                            replay_dir=tmp_path / "replay", benchmark_path=tmp_path / "benchmark.json")))
    monkeypatch.setattr(stub, "_REPLAY_DIR", tmp_path / "replay")
    monkeypatch.setattr(server, "REPLAY_SPACING_S", 0)
    server._RATE.clear(); server.RUNS.clear()
    yield TestClient(server.app)
    datastore.set_store(None)
