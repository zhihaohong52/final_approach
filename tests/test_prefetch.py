"""build._prefetch: concurrent store reads before a build under a remote store; the built wave is unchanged."""
import threading

from backend import datastore
from backend.geo import store as geo
from backend.wave import build


def _cold():
    build.build_wave.cache_clear()
    build._reset_caches()
    geo.load_surface.cache_clear()
    geo.load_procedures.cache_clear()


def test_prefetch_is_a_no_op_for_the_file_store(monkeypatch):
    monkeypatch.setattr(build, "_lane", lambda *a: (_ for _ in ()).throw(AssertionError("prefetched")))
    build._prefetch("ATL-2024-06-07-20")


def test_prefetch_under_a_remote_store_keeps_the_wave(monkeypatch):
    wid = "ATL-2024-06-07-20"
    files = datastore.get_store()
    _cold()
    ref = build.build_wave(wid).to_dict()
    readers = set()

    class Remote:                       # not a FileStore, so the prefetch runs
        def __getattr__(self, name):
            attr = getattr(files, name)
            if not callable(attr) or name == "session":
                return attr

            def call(*a, **k):
                readers.add(threading.current_thread().name)
                return attr(*a, **k)
            return call
    monkeypatch.setattr(datastore, "_store", Remote())
    _cold()
    try:
        assert build.build_wave(wid).to_dict() == ref
    finally:
        _cold()
    assert any(t.startswith("prefetch") for t in readers)
