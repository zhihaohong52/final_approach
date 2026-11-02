import pytest

from atm.geo import store


def test_kord_procedures_present():
    d = store.load_procedures("KORD")
    assert d["header"]["cycle"] == "2611"
    assert any(s["id"] == "BENKY6" for s in d["stars"]) and "10C" in d["approaches"]


def test_absent_airports():
    assert store.load_procedures("KPBI") is None and store.load_procedures("KISN") is None


def test_coverage_counts():
    import os
    from atm.wave.icao import icao_for
    dests = [f[:-7] for f in os.listdir("data/challenge2/flights")]
    have = [store.load_procedures(icao_for(d)) for d in dests]
    assert sum(bool(h and h["stars"]) for h in have) == 111
    # 313, not the 315 of spec §4.1: KASE (RNV-F) and PAPG (LDA-D, RNV-B) publish only circling-only
    # approaches with no runway to fly a straight-in final to, so extract_airport keeps none of them.
    assert sum(bool(h and h["approaches"]) for h in have) == 313


def test_procedures_size_cap():
    from pathlib import Path
    assert sum(p.stat().st_size for p in Path("data/ref/procedures").glob("*.json.gz")) <= 3_000_000


@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL, after `db_seed.py procedures` into atm_test
def test_geo_procedures_match_files_by_sha256(live_db_url):
    import hashlib, psycopg
    from pathlib import Path
    with psycopg.connect(live_db_url) as c:
        db = dict(c.execute("select icao, sha256 from geo.procedures").fetchall())
    files = {p.name.split(".")[0]: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in Path("data/ref/procedures").glob("*.json.gz")}
    assert db == files
