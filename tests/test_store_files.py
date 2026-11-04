import pandas as pd
from backend import datastore
from backend.datastore.files import FileStore
from backend.datastore.datasets import DATASETS
from backend.wave import build, flights2015 as F

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


def test_runs_roundtrip_and_list(tmp_path):
    from backend.datastore.files import FilePaths
    s = FileStore(FilePaths(runs_dir=tmp_path / "runs"))
    assert s.save_run({"run_id": "abcdef012345", "wave_id": "W1", "outcome": "consensus"})
    assert s.get_run("abcdef012345")["wave_id"] == "W1"
    assert s.get_run("../etc/passwd") is None
    assert [r["run_id"] for r in s.list_runs("W1", 5)] == ["abcdef012345"]


def test_decision_appends_both_shapes(tmp_path):
    from backend.datastore.files import FilePaths
    p = tmp_path / "d.jsonl"
    s = FileStore(FilePaths(decisions_path=p))
    assert s.record_decision({"run_id": "abcdef012345", "decision": "APPROVE", "note": "", "edits": []})
    assert s.record_decision({"flight_id": 5, "plan_id": "R1", "decision": "OVERRIDE", "note": "x"})
    assert len(p.read_text().splitlines()) == 2


def test_replay_ids_are_the_demo_set():
    assert FileStore().replay_ids() == {"F2015-7053", "F2015-292602", "F2015-152758", "F2015-75", "F2015-77162",
                                        "ATL-2024-07-18-21", "CLT-2024-07-29-19", "BWI-2024-08-02-21",
                                        "TPA-2024-07-12-16", "JFK-2024-08-30-12"}


def test_iem_cache_roundtrip(tmp_path):
    from backend.datastore.files import FilePaths
    s = FileStore(FilePaths(iem_dir=tmp_path))
    assert s.iem_get("ORD", "2015-08-19") is None
    s.iem_put("ORD", "2015-08-19", "station,valid\nORD,2015-08-19 00:51\n")
    assert s.iem_get("ORD", "2015-08-19")[1].startswith("station,valid")


def test_list_runs_decided_and_created_at(tmp_path):
    from backend.datastore.files import FilePaths
    s = FileStore(FilePaths(runs_dir=tmp_path / "runs", decisions_path=tmp_path / "d.jsonl"))
    s.save_run({"run_id": "abcdef012345", "wave_id": "W1"})
    r = s.list_runs("W1", 5)[0]
    assert r["decided"] is False and r["created_at"]
    s.record_decision({"run_id": "abcdef012345", "decision": "APPROVE"})
    assert s.list_runs("W1", 5)[0]["decided"] is True


def test_put_benchmark_not_current_is_noop(tmp_path):
    from backend.datastore.files import FilePaths
    s = FileStore(FilePaths(benchmark_path=tmp_path / "b.json"))
    s.put_benchmark({"a": 1}, "md", label="main", current=True)
    s.put_benchmark({"a": 2}, "md", label="other", current=False)
    assert s.benchmark_current() == {"a": 1}


def test_iem_put_none_writes_nothing(tmp_path):
    from backend.datastore.files import FilePaths
    s = FileStore(FilePaths(iem_dir=tmp_path / "iem"))
    s.iem_put("ORD", "2015-08-19", None)
    assert s.iem_get("ORD", "2015-08-19") is None
    assert not (tmp_path / "iem").exists()


def test_geo_methods_none_before_m0():
    s = FileStore()
    assert s.procedures("KXXX") is None and s.surface("KXXX") is None and s.movements_day("XXX", "2015-01-01") is None


def test_movements_day_rows_for_date(tmp_path, monkeypatch):
    import gzip, io
    import pandas as pd
    from backend.datastore import files
    d = tmp_path / "movements2015"
    d.mkdir()
    (d / "XXX.csv.gz").write_bytes(gzip.compress(b"date,tail\n2015-01-01,N1\n2015-01-02,N2\n2015-01-01,N3\n"))
    monkeypatch.setattr(files, "REF", tmp_path)
    files._movements.cache_clear()
    try:
        got = FileStore().movements_day("XXX", "2015-01-01")
        df = pd.read_csv(io.BytesIO(gzip.decompress(got)), dtype=str)
        assert list(df["tail"]) == ["N1", "N3"]
        assert FileStore().movements_day("XXX", "2015-02-01") is None
    finally:
        files._movements.cache_clear()


def test_movements_day_text_path_is_byte_identical_to_the_frame_path(tmp_path, monkeypatch):
    """The fast path (rows cut from the file text) gives exactly pack.gzip_csv of the frame path, and steps aside
    (False) whenever it could differ: unsorted dates, a quote, a short row, a non-date first column."""
    import gzip
    from backend.datastore import files, pack
    d = tmp_path / "movements2015"
    d.mkdir()
    monkeypatch.setattr(files, "REF", tmp_path)
    cases = {
        "AAA": b"date,kind,tail,taxi\n2015-01-01,arrival,N1,\n2015-01-01,departure,,7\n2015-01-02,arrival,N2,3\n",
        "BBB": b"date,tail\n2015-01-01,N1\n2015-01-02,N2\n2015-01-01,N3\n",           # unsorted
        "CCC": b'date,tail\n2015-01-01,"N,1"\n',                                      # quoted field
        "DDD": b"date,tail,x\n2015-01-01,N1\n",                                        # short row
        "EEE": b"tail,date\nN1,2015-01-01\n",                                          # date not first
        "FFF": b"date,tail\n2015-01-01,N1\n2015-01-02,N2",                             # no final newline
    }
    for iata, raw in cases.items():
        (d / f"{iata}.csv.gz").write_bytes(gzip.compress(raw))
    files._movements.cache_clear()
    try:
        for iata in cases:
            df, col = files._movements(iata)
            for day in ("2015-01-01", "2015-01-02", "2015-03-01", ""):
                sub = df[df[col] == day]
                want = None if sub.empty else pack.gzip_csv(sub)
                assert FileStore().movements_day(iata, day) == want, (iata, day)
        text = files._movements_text
        assert files._day_block(text("AAA"), "2015-01-01") not in (False, None)
        assert files._day_block(text("FFF"), "2015-01-02") not in (False, None)
        assert all(files._day_block(text(k), "2015-01-01") is False for k in ("BBB", "CCC", "DDD", "EEE"))
    finally:
        files._movements.cache_clear()
