import time

from fastapi.testclient import TestClient

from backend import server
from backend.wave import build


def test_startup_returns_at_once_and_warms_replay_waves(monkeypatch):
    wid = "F2015-58926"                                   # the MIA replay: a quick build
    monkeypatch.setenv("ATM_WARM", "1")
    monkeypatch.setattr(server, "_replay_ids", lambda live=False: {wid})
    monkeypatch.setattr(server, "_REPLAY_SUMMARIES", None)
    monkeypatch.setattr(server, "_bts_waves", lambda *a: [])
    started = []
    real = build.build_wave

    def slow_build(w):                                    # a slow build must not hold startup
        time.sleep(0.5)
        started.append(w)
        return real(w)
    monkeypatch.setattr(server.WB, "build_wave", slow_build)
    t = time.perf_counter()
    with TestClient(server.app):
        assert time.perf_counter() - t < 0.4
        th = server._WARM_THREAD
        assert th is not None and th.daemon
        th.join(timeout=60)
        assert not th.is_alive() and started[0] == wid    # the default wave is built first
        assert build._WAVES.get(wid) is not None          # the replay wave is in build_wave's LRU


def test_warm_is_off_with_atm_warm_0(monkeypatch):
    monkeypatch.setenv("ATM_WARM", "0")
    assert server._warm() is None


def test_non_file_store_warms_once_on_first_request(monkeypatch):
    from backend.datastore import get_store
    files = get_store()

    class PgLike:                                         # not a FileStore: startup must not query (design 8.3)
        def __getattr__(self, name):
            return getattr(files, name)

    monkeypatch.setenv("ATM_WARM", "1")
    monkeypatch.setattr(server, "get_store", lambda: PgLike())
    monkeypatch.setattr(server, "_WARM_THREAD", None)
    monkeypatch.setattr(server, "_WARM_KICKED", False)
    monkeypatch.setattr(server, "_replay_summaries", lambda: [])
    monkeypatch.setattr(server, "_bts_waves", lambda *a: [])
    real, calls = server._warm, []
    monkeypatch.setattr(server, "_warm", lambda: calls.append(1) or real())
    with TestClient(server.app) as c:
        assert calls == [] and server._WARM_THREAD is None   # startup did not warm
        assert c.get("/api/config").status_code == 200
        assert len(calls) == 1 and server._WARM_THREAD is not None
        first = server._WARM_THREAD
        c.get("/api/config"); c.get("/api/waves?limit=1")
        assert len(calls) == 1 and server._WARM_THREAD is first   # one-shot
        first.join(timeout=60)
