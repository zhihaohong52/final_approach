from scripts import db_seed as d


def test_point_is_lon_first_and_null_when_missing():
    assert d._point(1.5, 2.5) == "SRID=4326;POINT(2.5 1.5)"
    assert d._point(None, 2.5) is None and d._point(1.5, None) is None


def test_stand_rows_dedupe_and_stringify():
    doc = {"stands": [{"id": 1, "ref": "A1", "node": 0, "lat": 1, "lon": 2}, {"id": 1, "ref": "dup"},
                      {"id": "x", "ref": None, "node": None}]}
    rows = list(d._stand_rows("KXXX", b"", doc))
    assert rows == [["KXXX", "1", "A1", "0", "SRID=4326;POINT(2.0 1.0)"], ["KXXX", "x", None, None, None]]


def test_hold_rows_use_node_coordinates():
    doc = {"nodes": [[1, 2], [3, 4]], "holding_positions": [{"node": 1, "strip": "09R/27L"}, {"node": 1}, {"node": 9}]}
    rows = list(d._hold_rows("KXXX", b"", doc))
    assert rows == [["KXXX", "1", "09R/27L", "SRID=4326;POINT(4.0 3.0)"], ["KXXX", "9", None, None]]
