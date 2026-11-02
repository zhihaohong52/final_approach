import os

import atm  # noqa: F401  (its .env loader runs now, before the URLs are stripped below)
import pytest

TEST_DB_URL = os.environ.get("DATABASE_TEST_URL")
for _k in ("DATABASE_URL_POOLED", "DATABASE_URL", "DATABASE_TEST_URL"):
    os.environ.pop(_k, None)
os.environ["ATM_STORE"] = "files"

from atm import datastore  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _reset_store():
    datastore.set_store(None)


@pytest.fixture
def live_db_url(monkeypatch):
    if TEST_DB_URL is None:
        pytest.skip("DATABASE_TEST_URL not set")
    monkeypatch.setenv("DATABASE_TEST_URL", TEST_DB_URL)
    return TEST_DB_URL
