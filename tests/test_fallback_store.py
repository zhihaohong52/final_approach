import psycopg
import pytest
from backend.datastore.fallback import FallbackStore
from backend.datastore.files import FileStore
from backend.datastore import pg

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
    assert seen["kwargs"] == {"prepare_threshold": None, "connect_timeout": 10, "application_name": "final-approach",
                              "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3}


# ---- review fixes ----
import threading
import typing
from contextlib import nullcontext
from psycopg_pool import PoolTimeout
from backend.datastore.base import Store


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


class Unbuilt(pg.PgStore):      # a method the Postgres store does not implement yet (wave_row is built since Task 8)
    def wave_row(self, wave_id):
        raise NotImplementedError("PgStore.wave_row")


def test_not_implemented_does_not_open_breaker_or_degrade():
    fb = FallbackStore(Unbuilt("postgresql://u:p@127.0.0.1:1/db"), FileStore())
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


def test_no_retry_writes_attempted_once_reads_retried(monkeypatch, tmp_path):
    from backend.datastore.files import FilePaths
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)

    class P:
        n = {"record_decision": 0, "get_run": 0}
        def record_decision(self, row):
            P.n["record_decision"] += 1
            raise psycopg.OperationalError("down")
        def get_run(self, run_id):
            P.n["get_run"] += 1
            raise psycopg.OperationalError("down")

    paths = FilePaths(runs_dir=tmp_path / "runs", decisions_path=tmp_path / "d.jsonl")
    fb = FallbackStore(P(), FileStore(paths))
    assert fb.record_decision({"run_id": "0123456789ab", "decision": "APPROVE"}) is True   # fell back to file
    assert P.n["record_decision"] == 1
    fb.get_run("0123456789ab")
    assert P.n["get_run"] == 3


# ---- final review fixes ----
from backend.datastore.base import DataUnavailable


def _tmp_files(tmp_path):
    from backend.datastore.files import FilePaths
    return FileStore(FilePaths(runs_dir=tmp_path / "runs", decisions_path=tmp_path / "d.jsonl",
                               replay_dir=tmp_path / "replay", benchmark_path=tmp_path / "b.json"))


@pytest.mark.parametrize("exc", [psycopg.errors.IntegrityError, psycopg.errors.DataError,
                                 psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege])
def test_rejected_writes_fall_back_without_breaker(monkeypatch, tmp_path, exc):
    monkeypatch.setattr(pg.time, "sleep", lambda s: pytest.fail("a rejected write must not be retried"))

    class P:
        n = 0
        def save_run(self, r):
            P.n += 1; raise exc("rejected")
        def record_decision(self, row):
            P.n += 1; raise exc("rejected")

    files = _tmp_files(tmp_path)
    fb = FallbackStore(P(), files)
    run = {"run_id": "0123456789ab", "wave_id": "W", "mode": "formulas"}
    for _ in range(4):
        assert fb.save_run(run) is True                                             # kept in the file store
        assert fb.record_decision({"run_id": "0123456789ab", "decision": "APPROVE"}) is True
    assert P.n == 8                                                                 # once each, no retry
    assert files.get_run("0123456789ab")["run_id"] == "0123456789ab"
    assert fb._failures == 0 and fb._opened_at is None                              # database is up: no breaker
    s = fb.status()
    assert s.degraded and not s.persistent and exc.__name__ in s.reason
    assert fb.wrote_to_secondary("record_decision") and fb.wrote_to_secondary("save_run")


def test_read_database_error_is_not_swallowed():
    class P:
        def get_run(self, run_id): raise psycopg.errors.UndefinedTable("no table")
    fb = FallbackStore(P(), FileStore())
    with pytest.raises(psycopg.errors.UndefinedTable):
        fb.get_run("0123456789ab")
    assert fb._failures == 0


def test_operational_error_still_counts_for_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)

    class P:
        def save_run(self, r): raise psycopg.OperationalError("down")
    fb = FallbackStore(P(), _tmp_files(tmp_path))
    for _ in range(3):
        fb.save_run({"run_id": "0123456789ab", "wave_id": "W", "mode": "formulas"})
    assert fb._opened_at is not None


def test_data_unavailable_does_not_open_breaker():
    class P:
        n = 0
        def wave_row(self, wave_id):
            P.n += 1; raise DataUnavailable("ref.waves is empty")
    fb = FallbackStore(P(), FileStore())
    wid = FileStore().wave_rows(None, None, 1)[0]["wave_id"]
    for _ in range(5):
        assert fb.wave_row(wid)["wave_id"] == wid
    assert P.n == 5                                    # primary tried every time: breaker never opened
    assert fb._failures == 0 and fb._opened_at is None
    assert fb.status().degraded and "DataUnavailable" in fb.status().reason


def test_wrote_to_secondary_is_per_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)

    class P:
        fail = True
        def record_decision(self, row):
            if P.fail: raise psycopg.errors.IntegrityError("x")
            return True
    fb = FallbackStore(P(), _tmp_files(tmp_path))
    fb.record_decision({"run_id": "0123456789ab", "decision": "APPROVE"})          # this thread: secondary
    P.fail = False
    seen = {}
    th = threading.Thread(target=lambda: (fb.record_decision({"run_id": "0123456789ab", "decision": "APPROVE"}),
                                          seen.update(other=fb.wrote_to_secondary("record_decision"))))
    th.start(); th.join()
    assert seen["other"] is False                          # the other thread's own call reached the primary
    assert fb.wrote_to_secondary("record_decision") is True
    fb.record_decision({"run_id": "0123456789ab", "decision": "APPROVE"})
    assert fb.wrote_to_secondary("record_decision") is False


class _ReplayConn:
    def __init__(self, log, body): self.log, self.body = log, body
    def execute(self, sql, params=None):
        self.log.append((sql, params))
        return self
    def fetchone(self): return None if self.body is None else (self.body,)
    def fetchall(self): return [("W1",), ("W2",)]
    def commit(self): pass


def test_pg_replay_prefers_current_version_and_caches(monkeypatch):
    from contextlib import contextmanager
    s = pg.PgStore("postgresql://u:p@127.0.0.1:1/db")
    log = []
    body = {"engine_version": "v2", "messages": [], "result": {"x": 1}}

    @contextmanager
    def conn():
        yield _ReplayConn(log, body)
    monkeypatch.setattr(s, "_conn", conn)
    got = s.replay("W1")
    assert got == body
    sql, params = log[0]
    assert "order by (engine_version = %s) desc, recorded_at desc limit 1" in sql
    assert "engine_version = %s and" not in sql and params[0] == "W1"
    got["result"]["x"] = 2                                 # callers get a copy, the cache is not mutated
    assert s.replay("W1")["result"]["x"] == 1 and len(log) == 1   # served from the LRU
    for i in range(8):
        s.replay(f"X{i}")
    s.replay("W1"); assert len(log) == 10                 # evicted after 8 others
    monkeypatch.setattr(s, "_conn", conn)
    s.put_replay("W1", body)
    s.replay("W1"); assert len(log) == 12                 # put_replay cleared the cache (insert + select)
    assert s.replay_ids() == {"W1", "W2"}
    assert "engine_version" not in log[-1][0]              # ids of any engine version


def test_query_canceled_is_not_retried(monkeypatch):
    import psycopg
    from backend.datastore import pg as pgmod
    monkeypatch.setattr(pgmod.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("retried")))
    calls = []
    def boom():
        calls.append(1)
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    with pytest.raises(psycopg.errors.QueryCanceled):
        pgmod.with_retry(boom)
    assert len(calls) == 1
