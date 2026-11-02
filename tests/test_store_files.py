import pandas as pd
from atm import datastore
from atm.datastore.files import FileStore
from atm.datastore.datasets import DATASETS
from atm.wave import build, flights2015 as F

def test_frame_equals_direct_read():
    for name in ("runways", "aircraft", "airports", "waves_index"):
        ds = DATASETS[name]
        pd.testing.assert_frame_equal(FileStore().frame(name), pd.read_csv(ds.path, **ds.read_kwargs))
    # the registry must carry today's read arguments, not just any arguments
    assert DATASETS["runways"].read_kwargs == {"dtype": {"runway": str}}
    assert DATASETS["aircraft"].read_kwargs == {"dtype": str}

def test_arrivals_day_slice():
    df = FileStore().arrivals_day("ATL", "2024-07-15")
    assert len(df) > 500 and set(df["date"]) == {"2024-07-15"}
    assert FileStore().arrivals_day("ATL", "1999-01-01") is None

def test_flights2015_day_and_row():
    s = FileStore()
    r = s.flight2015_row(270287)
    assert r["dest"] == "ORD" and isinstance(r["sched_arr"], str)
    day = s.flights2015_day("ORD", r["arr_date"])
    assert 270287 in set(day["row_id"]) and set(day["arr_date"]) == {r["arr_date"]}

def test_search_matches_flight_row_shape():
    rows, total = FileStore().search_flights2015("ATL", "DL", 3, 0)
    assert total > 3 and len(rows) == 3 and all(r["dest"] == "ATL" for r in rows)
    assert set(rows[0]) == {"row_id", "wave_id", "date", "carrier", "flight", "origin", "dest", "sched_arr",
                            "arr_delay", "weather_delay", "late_aircraft_delay"}

def test_builders_go_through_the_store(monkeypatch):
    calls = []
    class Spy(FileStore):
        def arrivals_day(self, iata, date):
            calls.append((iata, date)); return super().arrivals_day(iata, date)
    datastore.set_store(Spy())
    try:
        build._arrivals_cache_clear()                     # see Step 3
        build.build_wave(build.list_waves(airport="ORD", limit=1)[0]["id"])
    finally:
        datastore.set_store(None)
    assert calls and calls[0][0] == "ORD"
