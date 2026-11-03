import importlib.util
import json
from pathlib import Path

import pytest

from atm.geo import store
from atm.geo.gates_geo import bind_gates
from atm.geo.osm_surface import build_surface

OSM = "tests/data/osm/mini_airport.json"
RWY = "tests/data/osm/mini_runways.json"


def _mini(extra=()):
    osm = json.load(open(OSM))
    osm["elements"] += list(extra)
    return build_surface(osm, "KXXX", json.load(open(RWY)), (0.0, 0.0), "t")


def _fetch_mod():
    spec = importlib.util.spec_from_file_location("fetch_surface", Path("scripts/fetch_surface.py"))
    m = importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0, "scripts")
    spec.loader.exec_module(m)
    return m


def test_bind_exact_ref_and_generic_on_fixture():
    s = _mini()
    b = bind_gates([("1", False), ("A1", False), ("RS", True)], s)
    assert b["1"]["geo_src"] == "osm-ref" and b["A1"]["geo_src"] == "osm-generic" and b["RS"]["stand"] != b["A1"]["stand"]


def test_replay_hubs_have_real_surface():
    for icao in ("KORD", "KATL", "KDFW", "KDEN", "KMIA"):
        s = store.load_surface(icao)
        assert s["quality"]["reach_pct"] and min(s["quality"]["reach_pct"].values()) >= 0.8, icao
        assert len(s["stands"]) >= 200 and s["header"]["attribution"] == "© OpenStreetMap contributors"


def test_gate_node_fallback_when_no_stand_matches():
    s = _mini([{"type": "node", "id": 990001, "lat": 0.0021, "lon": 0.0090,
                "tags": {"aeroway": "gate", "ref": "G9"}}])   # > 150 m from both stands
    g = bind_gates([("G9", False)], s)["G9"]
    assert g["geo_src"] == "osm-gate" and g["stand"] == "G9" and 0 <= g["node"] < len(s["nodes"])


def test_near_binding_to_stand_within_150m():
    extra = [{"type": "node", "id": 990005, "lat": 0.0028, "lon": 0.0153, "tags": {"aeroway": "gate", "ref": "X"}}]
    b = bind_gates([("X", False)], _mini(extra))["X"]   # gate node X sits ~15 m from stand S2, no stand ref matches
    assert b["geo_src"] == "osm-near" and b["stand"] == "S2"


def test_normalised_ref_needs_a_pier_of_gates_with_that_prefix():
    extra = [{"type": "node", "id": 990002, "lat": 0.0030, "lon": 0.0150, "tags": {"aeroway": "gate", "ref": "B7"}}]
    s = _mini(extra)
    b = bind_gates([("B1", False), ("b01", False), ("C1", False)], s)
    assert b["B1"]["geo_src"] == "osm-ref" and b["B1"]["stand"] == "S1"   # B1 -> ref 1, near gate B7
    assert b["b01"]["geo_src"] == "osm-ref"
    assert b["C1"]["geo_src"] == "osm-generic"                             # no gate with prefix C


def test_duplicate_refs_pick_stand_nearest_the_gate_node_then_first_id():
    far = {"type": "node", "id": 990003, "lat": 0.0027, "lon": 0.0300, "tags": {"aeroway": "parking_position", "name": "Z1", "ref": "1"}}
    s = _mini([far, {"type": "node", "id": 990004, "lat": 0.0027, "lon": 0.0300 + 0.0001,
                     "tags": {"aeroway": "gate", "ref": "1"}}])
    assert bind_gates([("1", False)], s)["1"]["stand"] == "Z1"            # nearest the gate node "1"
    s2 = _mini([far])
    assert bind_gates([("1", False)], s2)["1"]["stand"] == "S1"           # no gate node: first by id


def test_unknown_gate_without_stands_is_synth_at_the_reference_point():
    s = _mini()
    s["stands"] = []
    b = bind_gates([("Q9", False), ("RS", True)], s, ref_latlon=(1.0, 2.0))
    assert b["Q9"] == {"lat": 1.0, "lon": 2.0, "stand": "synth", "node": -1, "geo_src": "synth"} and b["RS"]["geo_src"] == "synth"


def test_expand_geometry_synthesises_vertex_nodes():
    m = _fetch_mod()
    raw = {"osm3s": {"timestamp_osm_base": "T"}, "elements": [
        {"type": "way", "id": 1, "nodes": [10, 11], "geometry": [{"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 0.001}],
         "tags": {"aeroway": "taxiway"}},
        {"type": "node", "id": 11, "lat": 0.0, "lon": 0.001, "tags": {"aeroway": "holding_position"}}]}
    out = m.expand_geometry(raw)
    nodes = {e["id"]: e for e in out["elements"] if e["type"] == "node"}
    assert set(nodes) == {10, 11} and nodes[11]["tags"]["aeroway"] == "holding_position"
    assert "geometry" not in out["elements"][-1]


def test_fetch_query_strategies_and_bbox():
    m = _fetch_mod()
    assert m.query("icao", "KORD").startswith("[out:json][timeout:90];") and m.query("icao", "KORD").endswith("out geom;")
    assert 'area[iata="MIA"]' in m.query("iata", "MIA")
    s, w, n, e = m.bbox(40.0, -75.0)
    assert 0.05 < n - s < 0.11 and w < -75.0 < e


def test_derive_reads_timestamp_and_skips_networkless_airports():
    m = _fetch_mod()
    osm = json.load(open(OSM))
    osm["osm3s"] = {"timestamp_osm_base": "2026-11-02T00:00:00Z"}
    d = m.derive(osm, "KXXX", (0.0, 0.0), json.load(open(RWY)))
    assert d["header"]["osm_timestamp"] == "2026-11-02T00:00:00Z" and d["header"]["ref"] == [0.0, 0.0]
    assert m.derive({"elements": []}, "KXXX", (0.0, 0.0), []) is None


def test_ord_replay_gates_bind_to_osm():
    from atm.wave import build
    w = build.build_wave("F2015-270287")
    b = bind_gates([(g.id, g.remote) for g in w.gates], store.load_surface("KORD"))
    assert sum(v["geo_src"] in ("osm-ref", "osm-near") for v in b.values()) >= len(w.gates) - 3


@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL, after `db_seed.py surface` into atm_test
def test_bindings_agree_with_nearest_stand_query(live_db_url):
    import psycopg
    from atm.wave import build
    w = build.build_wave("F2015-270287")
    b = bind_gates([(g.id, g.remote) for g in w.gates], store.load_surface("KORD"))
    with psycopg.connect(live_db_url) as c:
        c.execute("set search_path = public, extensions")   # PostGIS lives in the `extensions` schema on Neon
        for gid, v in list(b.items())[:20]:
            if v["geo_src"] not in ("osm-ref", "osm-near"):
                continue
            row = c.execute("select 1 from geo.stands where icao = 'KORD' and id = %s and "
                            "ST_DWithin(geog, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 150)",
                            (v["stand"], v["lon"], v["lat"])).fetchone()
            assert row, gid
