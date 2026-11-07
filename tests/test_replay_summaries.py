"""data/replay_summaries.json: the demo list's summaries, read instead of building the replay waves."""
from contextlib import contextmanager

from backend import server
from scripts import replay_summaries


def test_stored_summaries_match_the_builds():
    stored = server._stored_replay_summaries()
    assert stored, "data/replay_summaries.json is missing: run python -m scripts.replay_summaries"
    assert stored == replay_summaries.compute(), "stale: rerun python -m scripts.replay_summaries"


def test_demo_list_reads_stored_summaries_without_building(monkeypatch):
    stored = server._stored_replay_summaries()
    ids = set(stored)

    class Store:
        @contextmanager
        def session(self):
            yield

        def wave_summary(self, wid):
            raise AssertionError(f"read {wid} from the store")

    def build_wave(wid):
        raise AssertionError(f"built {wid}")
    monkeypatch.setattr(server, "_REPLAY_SUMMARIES", None)
    monkeypatch.setattr(server, "_replay_ids", lambda live=False: ids)
    monkeypatch.setattr(server, "get_store", lambda: Store())
    monkeypatch.setattr(server.WB, "build_wave", build_wave)
    out = server._replay_summaries()
    assert [w["id"] for w in out] == sorted(ids, key=lambda i: (i.startswith("F2015-"), i))
    assert all(w == {**stored[w["id"]], "replay_available": True} for w in out)
