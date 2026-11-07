"""backend.geo.store doc caches: the surface document keeps its gzipped bytes per airport and only the last parsed
document resident; procedures stay parsed (read once per flight). Same documents, no extra store reads, and no
parse per flight."""
import gzip
import json
import types

import pytest

from backend import datastore
from backend.geo import store
from backend.wave import build

AIRPORTS = ("KATL", "KORD", "KDEN", "KPHX")


def _cold():
    build.build_wave.cache_clear()
    build._reset_caches()
    store.load_surface.cache_clear()
    store.load_procedures.cache_clear()


@pytest.fixture
def cold():
    _cold()
    yield
    _cold()


def test_documents_are_the_stored_documents(cold):
    st = datastore.get_store()
    for icao in ("KATL", "KORD"):
        assert store.load_surface(icao) == json.loads(gzip.decompress(st.surface(icao)))
        assert store.load_procedures(icao) == json.loads(gzip.decompress(st.procedures(icao)))
    assert store.load_surface("KXXX") is None and store.load_procedures("KXXX") is None


def test_cache_api(cold):
    assert callable(store.load_surface.cache_clear) and callable(store.load_procedures.cache_clear)
    store.load_surface("KATL")
    assert store.load_surface.cache_info().currsize == 1
    store.load_surface.cache_clear()
    assert store.load_surface.cache_info().currsize == 0 and store._surface_blob.cache_info().currsize == 0


def test_one_parsed_surface_resident_and_one_read_per_airport(cold, monkeypatch):
    reads = []
    real = type(datastore.get_store()).surface
    monkeypatch.setattr(type(datastore.get_store()), "surface", lambda self, icao: reads.append(icao) or real(self, icao))
    for _ in range(2):
        for icao in AIRPORTS:
            assert store.load_surface(icao) is not None
            assert store.load_surface.cache_info().currsize <= 1
    assert sorted(reads) == sorted(AIRPORTS)      # bytes cached: switching back is a parse, not a store read
    a = store.load_surface("KPHX")
    assert store.load_surface("KPHX") is a       # repeated reads of one airport share the parsed document


def test_parses_per_build_and_geo_body(cold, monkeypatch):
    from backend import server
    parses = {"surface": 0, "procedures": 0}
    real = json.loads

    def counting(s, *a, **k):
        d = real(s, *a, **k)
        if isinstance(d, dict) and "header" in d:
            parses["surface" if "nodes" in d else "procedures"] += 1
        return d
    monkeypatch.setattr(store, "json", types.SimpleNamespace(loads=counting))
    monkeypatch.setattr(server, "_GEO", type(server._GEO)())
    for wid in ("ATL-2024-06-07-20", "ATL-2024-06-07-21"):
        server._geo_body(build.build_wave(wid))
    assert parses == {"surface": 1, "procedures": 1}  # not per flight, not per reader, not per wave of one airport
