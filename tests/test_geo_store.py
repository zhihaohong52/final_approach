import pytest

from backend.geo import store


def test_kord_procedures_present():
    d = store.load_procedures("KORD")
    assert d["header"]["cycle"] == "2611"
    assert any(s["id"] == "BENKY6" for s in d["stars"]) and "10C" in d["approaches"]


def test_absent_airports():
    assert store.load_procedures("KPBI") is None and store.load_procedures("KISN") is None


def test_coverage_counts():
    import os
    from backend.wave.icao import icao_for
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


def _movements_dir(tmp_path, monkeypatch):
    """Gzip the fixture into tmp_path/movements2015/XXX.csv.gz and point the loader and the FileStore at it."""
    import gzip, shutil
    from backend.datastore import files
    d = tmp_path / "movements2015"; d.mkdir()
    with open("tests/data/movements/mini.csv", "rb") as f, gzip.open(d / "XXX.csv.gz", "wb") as g:
        shutil.copyfileobj(f, g)
    monkeypatch.setattr(store, "MOVEMENTS_DIR", d)
    monkeypatch.setattr(files, "REF", tmp_path)   # FileStore reads REF / "movements2015" / {IATA}.csv.gz
    files._movements.cache_clear(); store.load_movements.cache_clear()


def test_movements_window_from_fixture(tmp_path, monkeypatch):
    _movements_dir(tmp_path, monkeypatch)
    ms = store.load_movements("XXX", "2015-08-19", 8 * 60, 9 * 60)
    assert {m.kind for m in ms} == {"arrival", "departure"} and all(8 * 60 <= m.sched_min_abs < 9 * 60 for m in ms)
    assert store.load_movements("NOPE", "2015-08-19", 0, 1440) is None
    cancelled = next(m for m in ms if m.callsign == "DL411")
    assert cancelled.wheels_min_abs is None and cancelled.taxi_min is None and cancelled.tail is None
    first = next(m for m in ms if m.callsign == "UA201")
    assert (first.tail, first.wheels_min_abs, first.taxi_min, first.other_airport) == ("N201UA", 490, 9.0, "DEN")
    files_cleanup(monkeypatch)


def test_movements_window_crosses_midnight(tmp_path, monkeypatch):
    _movements_dir(tmp_path, monkeypatch)
    ms = store.load_movements("XXX", "2015-08-19", 1400, 1500)
    assert [(m.callsign, m.sched_min_abs, m.wheels_min_abs) for m in ms] == [("AA740", 1435, 1442), ("AA750", 1450, 1460)]
    files_cleanup(monkeypatch)


def files_cleanup(monkeypatch):
    monkeypatch.undo()
    from backend.datastore import files
    files._movements.cache_clear(); store.load_movements.cache_clear()


def test_flights2015_rows_have_taxi_in():
    from backend.datastore import files
    df = files._dest_frame("ORD")
    assert {"wheels_on", "taxi_in", "wheels_off", "taxi_out"} <= set(df.columns) and df["taxi_in"].notna().mean() > 0.99


def test_top30_and_movement_airports():
    assert len(store.TOP30) == 30 and store.TOP30 == tuple(sorted(store.TOP30))
    assert set(store.TOP30) <= set(store.MOVEMENT_AIRPORTS) and len(store.MOVEMENT_AIRPORTS) == 32
    assert store.MOVEMENT_AIRPORTS == tuple(sorted(store.MOVEMENT_AIRPORTS))
    from pathlib import Path
    d = Path("data/ref/movements2015")   # gitignored: generated locally and seeded to Postgres only
    if d.is_dir() and any(d.glob("*.csv.gz")):
        assert {p.name[:3] for p in d.glob("*.csv.gz")} == set(store.MOVEMENT_AIRPORTS)


def test_movements_absent_file_is_none(tmp_path, monkeypatch):
    from backend.datastore import files
    monkeypatch.setattr(files, "REF", tmp_path)
    files._movements.cache_clear(); store.load_movements.cache_clear()
    assert files.FileStore().movements_day("ORD", "2015-08-19") is None
    assert store.load_movements("ORD", "2015-08-19", 0, 1440) is None   # no data: generic ground traffic
    files._movements.cache_clear(); store.load_movements.cache_clear()


@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL, after `db_seed.py movements2015` into atm_test
def test_movements_day_blob_matches_file(live_db_url):
    import gzip, io
    import pandas as pd
    from backend.datastore.pg import PgStore
    st = PgStore(live_db_url); st.open()
    try:
        blob = st.movements_day("ORD", "2015-08-19")
    finally:
        st.close()
    assert blob is not None
    day = pd.read_csv(io.BytesIO(gzip.decompress(blob)), dtype=str)
    full = pd.read_csv("data/ref/movements2015/ORD.csv.gz", dtype=str)
    assert len(day) > 0 and len(day) == (full["date"] == "2015-08-19").sum()
