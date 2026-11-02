import psycopg
import pytest
from atm.datastore.fallback import FallbackStore
from atm.datastore.files import FileStore
from atm.datastore import pg

class Down:
    calls = 0
    def wave_row(self, wave_id):
        Down.calls += 1
        raise psycopg.OperationalError("connection refused")
    def status(self): raise AssertionError("status must not query")

def test_fallback_serves_files_after_retries(monkeypatch):
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)
    Down.calls = 0
    fb = FallbackStore(Down(), FileStore())
    wid = FileStore().wave_rows(None, None, 1)[0]["wave_id"]
    assert fb.wave_row(wid)["wave_id"] == wid
    assert Down.calls == 3                                  # 1 try + 2 retries
    s = fb.status()
    assert (s.backend, s.degraded, s.persistent) == ("files", True, False) and "wave_row" in s.reason

def test_breaker_opens_and_half_opens(monkeypatch):
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)
    t = [0.0]
    Down.calls = 0
    fb = FallbackStore(Down(), FileStore(), clock=lambda: t[0])
    wid = FileStore().wave_rows(None, None, 1)[0]["wave_id"]
    for _ in range(3): fb.wave_row(wid)
    n = Down.calls; fb.wave_row(wid); assert Down.calls == n      # open: primary skipped
    t[0] += 61; fb.wave_row(wid); assert Down.calls > n           # half-open trial

def test_retry_jitter_bounds():
    slept = []
    with pytest.raises(psycopg.OperationalError):
        pg.with_retry(lambda: (_ for _ in ()).throw(psycopg.OperationalError("x")), sleep=slept.append)
    assert 0.5 <= slept[0] <= 0.65 and 2.0 <= slept[1] <= 2.6

def test_pool_settings(monkeypatch):
    seen = {}
    monkeypatch.setattr(pg, "NullConnectionPool", lambda *a, **k: seen.update(k) or object())
    pg.make_pool("postgresql://u:p@example-pooler.us-west-2.aws.neon.tech/db?sslmode=require")
    assert seen["max_size"] == 4 and seen["timeout"] == 10 and seen["open"] is False
    assert seen["kwargs"] == {"prepare_threshold": None, "connect_timeout": 10, "application_name": "final-approach"}
