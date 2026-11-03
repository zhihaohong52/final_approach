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
        assert not th.is_alive() and started == [wid]
        assert build._WAVES.get(wid) is not None          # the replay wave is in build_wave's LRU


def test_warm_is_off_with_atm_warm_0(monkeypatch):
    monkeypatch.setenv("ATM_WARM", "0")
    assert server._warm() is None
