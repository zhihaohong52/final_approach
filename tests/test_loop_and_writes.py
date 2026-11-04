"""Final-review fixes at the API level: no store I/O on the event loop, rejected writes fall back, per-call persisted,
v1 decision validation, shutdown drains run saves, the demo list is built once. Offline: no database."""
import asyncio
import threading
import time
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import httpx
import psycopg
import pytest

from backend import datastore, mcp_server, server
from backend.datastore import pg
from backend.datastore.fallback import FallbackStore
from backend.wave import stub

WID = "ATL-2024-07-15-16"


class Slow:
    """A primary whose every store call takes ~1 s (a waking or slow database), then answers from the files."""

    def __init__(self, files, delay=1.0):
        self._files, self._delay = files, delay

    def session(self):
        return nullcontext()

    def __getattr__(self, name):
        target = getattr(self._files, name)

        def call(*a, **k):
            time.sleep(self._delay)
            return target(*a, **k)
        return call


def test_slow_store_does_not_block_the_event_loop(api_client, monkeypatch):
    from backend.wave import replay
    files = datastore.get_store()
    monkeypatch.setattr(replay, "WB", stub)
    monkeypatch.setattr(replay, "GR", stub)
    replay.record([WID], out_dir=files.paths.replay_dir)
    datastore.set_store(FallbackStore(Slow(files), files))
    monkeypatch.setattr(server, "_save_run", lambda res: None)   # not under test here (a background thread anyway)

    async def scenario():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            assert (await c.get("/api/config")).status_code == 200          # warm imports and the threadpool
            neg = asyncio.create_task(c.post("/api/negotiate-wave", json={"wave_id": WID, "mode": "decision",
                                                                          "replay": True, "stream": False}))
            tool = asyncio.create_task(mcp_server.mcp.call_tool("list_waves", {"limit": 1}))
            await asyncio.sleep(0.1)                                           # both are now inside a slow store call
            t0 = time.perf_counter()
            r = await c.get("/api/config")
            dt = time.perf_counter() - t0
            pending = not neg.done() and not tool.done()                       # still waiting on the store
            res = await neg
            await tool
            return r, dt, pending, res

    r, dt, pending, res = asyncio.run(scenario())
    assert r.status_code == 200 and dt < 0.2, f"/api/config took {dt:.3f}s while store calls were in flight"
    assert pending
    assert res.status_code == 200 and res.json()["wave_id"] == WID


class Rejecting:
    """A database that is up but rejects the write (constraint, bad data)."""

    def __init__(self, exc):
        self.exc = exc

    def session(self):
        return nullcontext()

    def __getattr__(self, name):
        def fail(*a, **k): raise self.exc("rejected")
        return fail


@pytest.mark.parametrize("exc", [psycopg.errors.IntegrityError, psycopg.errors.DataError])
def test_rejected_decision_falls_back_and_reports_not_persisted(api_client, monkeypatch, exc):
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)
    files = datastore.get_store()
    fb = FallbackStore(Rejecting(exc), files)
    datastore.set_store(fb)
    r = api_client.post("/api/decision", json={"run_id": "0123456789ab", "decision": "APPROVE"})
    assert r.status_code == 200 and r.json()["persisted"] is False
    assert files.paths.decisions_path.read_text().strip()               # kept in the file store
    s = api_client.get("/api/config").json()["store"]
    assert s["degraded"] is True and exc.__name__ in s["reason"]
    assert fb._opened_at is None and fb._failures == 0


def test_rejected_save_run_is_kept_in_files(api_client, monkeypatch):
    files = datastore.get_store()
    datastore.set_store(FallbackStore(Rejecting(psycopg.errors.DataError), files))
    r = api_client.post("/api/negotiate-wave", json={"wave_id": WID, "mode": "formulas", "stream": False}).json()
    assert server._wait_saves(5)
    assert files.get_run(r["run_id"])["run_id"] == r["run_id"]


def test_persisted_reflects_this_call_not_global_status(api_client):
    files = datastore.get_store()

    class Flaky:                      # the first decision is rejected, the second is stored
        n = 0
        def session(self): return nullcontext()
        def record_decision(self, row):
            Flaky.n += 1
            if Flaky.n == 1:
                raise psycopg.errors.IntegrityError("x")
            return True
    datastore.set_store(FallbackStore(Flaky(), files))
    body = {"run_id": "0123456789ab", "decision": "APPROVE"}
    assert api_client.post("/api/decision", json=body).json()["persisted"] is False
    assert api_client.post("/api/decision", json=body).json()["persisted"] is True


@pytest.mark.parametrize("body", [
    {"flight_id": 1, "plan_id": "p", "decision": "MAYBE"},
    {"flight_id": 1, "plan_id": "p", "decision": "OVERRIDE"},
    {"flight_id": 1, "plan_id": "p", "decision": "OVERRIDE", "note": "   "},
])
def test_v1_decision_validated_like_v2(api_client, body):
    r = api_client.post("/api/decision", json=body)
    assert r.status_code == 422 and "detail" in r.json()           # v1 error shape, never a 500 from the database
    assert not datastore.get_store().paths.decisions_path.exists()


def test_v1_override_with_note_is_recorded(api_client):
    r = api_client.post("/api/decision", json={"flight_id": 1, "plan_id": "p", "decision": "OVERRIDE", "note": " ok "})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_shutdown_waits_for_in_flight_saves(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    events = []

    class Primary:
        def session(self): return nullcontext()
        def save_run(self, r):
            time.sleep(0.5)
            events.append("saved")
            return True
        def close(self): events.append("closed")

    from backend.datastore.files import FilePaths, FileStore
    files = FileStore(FilePaths(runs_dir=tmp_path / "runs", decisions_path=tmp_path / "d.jsonl"))
    datastore.set_store(FallbackStore(Primary(), files))
    try:
        with TestClient(server.app):
            server._save_run({"run_id": "0123456789ab", "wave_id": WID, "mode": "formulas"})
        assert events == ["saved", "closed"]
    finally:
        datastore.set_store(None)
        server.RUNS.clear()


def test_wait_saves_is_bounded(monkeypatch):
    with server._SAVES_CV:
        server._SAVES["n"] += 1
    try:
        t0 = time.perf_counter()
        assert server._wait_saves(0.2) is False
        assert time.perf_counter() - t0 < 1
    finally:
        with server._SAVES_CV:
            server._SAVES["n"] -= 1
            server._SAVES_CV.notify_all()


def test_replay_summaries_built_once_in_one_session(monkeypatch):
    builds, sessions = [], []

    def build_wave(wid):
        builds.append(wid)
        time.sleep(0.05)
        return SimpleNamespace(to_dict=lambda: {"id": wid})

    class Store:
        @contextmanager
        def session(self):
            sessions.append(1)
            yield

    monkeypatch.setattr(server, "_REPLAY_SUMMARIES", None)
    monkeypatch.setattr(server, "WB", SimpleNamespace(build_wave=build_wave))
    monkeypatch.setattr(server, "_replay_ids", lambda live=False: {"F2015-1", "F2015-2", "ATL-x"})
    monkeypatch.setattr(server, "_f2015_summary", lambda w, replay_available=False: {"id": w["id"]})
    monkeypatch.setattr(server, "get_store", lambda: Store())
    out = []
    ths = [threading.Thread(target=lambda: out.append(server._replay_summaries())) for _ in range(4)]
    for t in ths: t.start()
    for t in ths: t.join()
    assert builds == ["ATL-x", "F2015-1", "F2015-2"] and sessions == [1]   # 2024 recorded runs first
    assert all(o == [{"id": "ATL-x"}, {"id": "F2015-1"}, {"id": "F2015-2"}] for o in out)


def test_waves_random_uses_one_session(monkeypatch):
    sessions = []

    class Store:
        @contextmanager
        def session(self):
            sessions.append(1)
            yield

    monkeypatch.setattr(server, "get_store", lambda: Store())
    monkeypatch.setattr(server, "WB", SimpleNamespace(random_wave=lambda airport=None: {"id": "W"}))
    assert server.waves_random(source="bts") == {"id": "W"} and sessions == [1]


def test_record_prints_when_db_write_fell_back(api_client, monkeypatch, capsys):
    from backend.wave import replay
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)
    monkeypatch.setattr(replay, "WB", stub)
    monkeypatch.setattr(replay, "GR", stub)
    files = datastore.get_store()
    datastore.set_store(FallbackStore(Rejecting(psycopg.errors.InsufficientPrivilege), files))
    replay.record([WID], out_dir=files.paths.replay_dir)
    out = capsys.readouterr().out
    assert "written to files only" in out and "InsufficientPrivilege" in out
    assert files.replay(WID) is not None
