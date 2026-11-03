from scripts import db_seed as d


def test_geo_seeds_are_documents_only():
    assert not any(hasattr(d, n) for n in ("_point", "_fix_rows", "_stand_rows", "_gate_rows", "_hold_rows"))
    assert "coverage" not in d.SEEDERS
    assert set(d._GEO_INDEXES) == {"geo.procedures", "geo.surface", "geo.movements2015_day"}


def test_surface_and_procedure_rows_keep_document_bytes():
    import hashlib
    blob = b"gz"
    assert list(d._procedure_row("KXXX", blob, {"header": {"cycle": "2611", "tier": "iap"}})) == [[
        "KXXX", "2611", "iap", "\\x" + blob.hex(), hashlib.sha256(blob).hexdigest()]]
    row = list(d._surface_row("KXXX", blob, {"header": {"osm_timestamp": "t"}, "quality": {"reach_pct": {"a": 1}}}))[0]
    assert row[:2] == ["KXXX", "t"] and row[4] == '{"a": 1}'
