"""X-Plane apt.dat -> Overpass-like elements -> the OSM surface builder (backend.geo.xplane_surface)."""
from backend.geo import xplane_surface as XS
from backend.geo.osm_surface import build_surface
from backend.wave import surface

# a 1 km east-west runway 09/27 on the taxi network, a parallel taxiway A 200 m north joined by two links,
# a hot-zone link at each end (hold-short where it meets A) and one gate 100 m north of A
APT = """1100 Version
1 0 0 0 KXXX Test
100 45.00 1 0 0.25 0 2 1 09 0.0000 0.0000 0 0 3 0 0 0 27 0.0000 0.0127 0 0 3 0 0 0
1201 0.0000 0.0000 both 0 rw_w
1201 0.0000 0.0127 both 1 rw_e
1201 0.0018 0.0000 both 2 a_w
1201 0.0018 0.0127 both 3 a_e
1201 0.0009 0.0000 both 4 link_w
1201 0.0009 0.0127 both 5 link_e
1202 0 1 twoway runway 09/27
1204 departure 09,27
1202 0 4 twoway taxiway_E A1
1204 arrival 09,27
1202 4 2 twoway taxiway_E A1
1202 1 5 twoway taxiway_E A2
1204 arrival 09,27
1202 5 3 twoway taxiway_E A2
1202 2 3 twoway taxiway_E A
1300 0.0027 0.0063 180.0 gate jets G1
"""
RUNWAYS = [{"airport": "XXX", "runway": "09", "le_lat": "0.0", "le_lon": "0.0", "he_lat": "0.0", "he_lon": "0.0127"},
           {"airport": "XXX", "runway": "27", "le_lat": "0.0", "le_lon": "0.0127", "he_lat": "0.0", "he_lon": "0.0"}]


def test_parse_reads_nodes_edges_hot_zones_ramps_and_runways():
    p = XS.parse(APT)
    assert len(p["nodes"]) == 6 and len(p["edges"]) == 6
    assert p["edges"][0][2:4] == ["runway", "09/27"] and p["edges"][0][4] == {"09", "27"}
    assert p["edges"][1][3] == "A1" and p["edges"][2][4] == set()
    assert p["ramps"] == [{"lat": 0.0027, "lon": 0.0063, "kind": "gate", "name": "G1"}]
    assert p["runways"][0]["ids"] == ["09", "27"] and p["runways"][0]["width_m"] == 45.0


def test_holds_sit_where_a_hot_zone_edge_meets_a_cold_one_off_the_runway():
    els = XS.to_elements(APT)["elements"]
    holds = [e for e in els if e.get("tags", {}).get("aeroway") == "holding_position"]
    assert sorted((e["lat"], e["lon"]) for e in holds) == [(0.0009, 0.0), (0.0009, 0.0127)]
    stand = next(e for e in els if e.get("tags", {}).get("aeroway") == "parking_position")
    assert stand["tags"]["ref"] == "G1"


def test_builds_a_surface_whose_runway_ends_reach_the_gate():
    doc = build_surface(XS.to_elements(APT), "KXXX", RUNWAYS, (0.0009, 0.0063), "")
    assert [s["ref"] for s in doc["stands"]] == ["G1"]
    assert doc["quality"]["reach_pct"] == {"09": 1.0, "27": 1.0}


def test_source_label_names_the_gateway_scenery():
    assert surface.source_label({"source": XS.HEADER["source"], "gateway_scenery": 99761}) == \
        "X-Plane Scenery Gateway (scenery 99761)"
    assert surface.source_label({"osm_timestamp": "2026-11-02T20:31:26Z"}) == "OpenStreetMap 2026-11-02"
    assert surface.source_label(None) == "OpenStreetMap snapshot"
