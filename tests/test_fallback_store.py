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


# ---- review fixes ----
import threading
import typing
from contextlib import nullcontext
from psycopg_pool import PoolTimeout
from atm.datastore.base import Store


def test_store_methods_match_protocol():
    names = {n for n in vars(Store) if not n.startswith("_")} - {"session"}
    assert set(pg.STORE_METHODS) == names


def test_session_enter_does_not_connect():
    store = pg.PgStore("postgresql://u:p@127.0.0.1:1/db")
    with store.session():
        pass


class Timeout:
    calls = 0
    def wave_row(self, wave_id):
        Timeout.calls += 1
        raise PoolTimeout("busy")


def test_pool_timeout_falls_back_without_retry(monkeypatch):
    monkeypatch.setattr(pg.time, "sleep", lambda s: pytest.fail("must not sleep"))
    Timeout.calls = 0
    fb = FallbackStore(Timeout(), FileStore())
    wid = FileStore().wave_rows(None, None, 1)[0]["wave_id"]
    assert fb.wave_row(wid)["wave_id"] == wid
    assert Timeout.calls == 1
    assert fb.status().degraded


def test_not_implemented_does_not_open_breaker_or_degrade():
    fb = FallbackStore(pg.PgStore("postgresql://u:p@127.0.0.1:1/db"), FileStore())
    wid = FileStore().wave_rows(None, None, 1)[0]["wave_id"]
    for _ in range(5):
        assert fb.wave_row(wid)["wave_id"] == wid
    s = fb.status()
    assert (s.backend, s.degraded, s.persistent) == ("postgres", False, True)
    assert fb._opened_at is None


def test_half_open_allows_one_trial(monkeypatch):
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)
    t = [0.0]
    entered, release = threading.Event(), threading.Event()

    class Slow:
        calls = 0
        fail = True
        def wave_row(self, wave_id):
            Slow.calls += 1
            if Slow.fail:
                raise psycopg.OperationalError("down")
            entered.set()
            release.wait(5)
            return {"wave_id": wave_id}

    fb = FallbackStore(Slow(), FileStore(), clock=lambda: t[0])
    wid = FileStore().wave_rows(None, None, 1)[0]["wave_id"]
    for _ in range(3): fb.wave_row(wid)
    Slow.fail = False
    t[0] += 61
    Slow.calls = 0
    th = threading.Thread(target=fb.wave_row, args=(wid,))
    th.start()
    assert entered.wait(5)
    fb.wave_row(wid)                       # concurrent: goes to secondary
    assert Slow.calls == 1
    release.set(); th.join()
    assert fb.status().backend == "postgres"


def test_write_fallback_stays_degraded_until_write_succeeds(monkeypatch):
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)

    class P:
        ok = False
        def save_run(self, r):
            if not P.ok: raise psycopg.OperationalError("x")
            return True
        def wave_row(self, w): return {"wave_id": w}

    class S:
        def save_run(self, r): return False
    fb = FallbackStore(P(), S())
    assert fb.save_run({}) is False
    fb.wave_row("w")
    s = fb.status()
    assert s.persistent is False and s.degraded
    P.ok = True
    fb.save_run({})
    assert fb.status().persistent is True


class FakeConn:
    def __init__(self): self.commits = self.rollbacks = 0
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


class FakePool:
    def __init__(self): self.conns, self.put = [], []
    def open(self, wait=False): pass
    def getconn(self):
        c = FakeConn(); self.conns.append(c); return c
    def putconn(self, c): self.put.append(c)


def _store():
    s = pg.PgStore("postgresql://u:p@127.0.0.1:1/db")
    s._pool = FakePool()
    return s


def test_bound_session_commits_and_rolls_back():
    s = _store()
    with s.session():
        with s._conn() as c: pass
        assert c.commits == 1
        with pytest.raises(ValueError):
            with s._conn() as c2: raise ValueError
        assert c2 is c and c.rollbacks == 1
    assert s._pool.put == [c]


def test_bound_session_drops_conn_after_operational_error():
    s = _store()
    with s.session():
        with pytest.raises(psycopg.OperationalError):
            with s._conn() as c: raise psycopg.OperationalError("gone")
        assert s._pool.put == [c]
        with s._conn() as c2: pass
        assert c2 is not c
    assert s._pool.put == [c, c2]
