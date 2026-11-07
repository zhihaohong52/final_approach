"""The FileStore's day readers (arrivals_day, flights2015_day, movements_day) keep only a small per-file day index
and read a day's rows on demand. They must give exactly what the whole-file readers they replaced gave: the same
frame (values, dtypes, categories, column order, row order, index values and index class) or the same bytes."""
import gzip
import io

import numpy as np
import pandas as pd
import pytest

from backend.datastore import files, pack
from backend.datastore.datasets import ARRIVALS_KWARGS, CH2, FLIGHTS2015_DAY_KWARGS, WAVES


# ---------------------------------------------------------------- the readers before the day index (verbatim)

def old_arrivals(path):
    df = pd.concat(pd.read_csv(path, chunksize=20000, **ARRIVALS_KWARGS), ignore_index=True)
    for c in ("date", "carrier", "origin", "tail"):
        df[c] = df[c].astype("category")
    return df, df.groupby("date", observed=True).indices


def old_arrivals_day(path, date):
    df, groups = old_arrivals(path)
    pos = groups.get(date)
    return None if pos is None else df.iloc[pos]


def old_flights2015_day(path, arr_date):
    df = pd.read_csv(path, **FLIGHTS2015_DAY_KWARGS)
    return df[df["arr_date"] == arr_date]


def old_movements_day(path, date):
    """`path` is the file of the year the date names (movements_day reads movements{date[:4]}, else 2015)."""
    year = date[:4] if date[:4].isdigit() else "2015"
    path = path.parent.parent / f"movements{year}" / path.name
    if not path.exists():
        return None
    text = gzip.decompress(path.read_bytes()).decode()
    got = files._day_block(text, date)
    if got is not False:
        return None if got is None else pack.gzip_bytes(got)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    col = next((c for c in pack.MOVEMENT_DATE_COLS if c in df.columns), None)
    if col is None:
        return None
    sub = df[df[col] == date]
    return None if sub.empty else pack.gzip_csv(sub)


def same_frame(a, b, tag=None):
    if a is None or b is None:
        assert a is None and b is None, tag
        return
    pd.testing.assert_frame_equal(a, b, check_exact=True, check_index_type=True, check_column_type=True,
                                  check_categorical=True, check_freq=True, check_flags=True)
    pd.testing.assert_index_equal(a.index, b.index, exact=True)
    pd.testing.assert_index_equal(a.columns, b.columns, exact=True)
    assert type(a.index) is type(b.index) and a.dtypes.equals(b.dtypes), tag
    for c in a.columns:
        if isinstance(a[c].dtype, pd.CategoricalDtype):
            assert a[c].cat.categories.equals(b[c].cat.categories), (tag, c)
            assert np.array_equal(a[c].cat.codes.to_numpy(), b[c].cat.codes.to_numpy()), (tag, c)


@pytest.fixture(autouse=True)
def _clean_caches():
    def clear():
        files._arrivals.cache_clear(); files._dest_index.cache_clear(); files._movements_index.cache_clear()
        files._movements.cache_clear()
    clear()
    yield
    clear()


def _sample(days, step):
    days = sorted(days)
    return sorted(set(days[::step]) | {days[0], days[-1]})


# ---------------------------------------------------------------- real files

ARR_AIRPORTS = ["ATL", "ORD", "DEN", "PHX", "BOS", "SEA"]


@pytest.mark.parametrize("iata", ARR_AIRPORTS)
def test_arrivals_day_equals_whole_file_reader(iata):
    path = WAVES / "arrivals" / f"{iata}.csv.gz"
    if not path.exists():
        pytest.skip("no arrivals file")
    df, groups = old_arrivals(path)
    s = files.FileStore()
    days = _sample(groups, 4)
    for d in days + ["1999-01-01", "", "2024-6-7"]:
        pos = groups.get(d)
        same_frame(s.arrivals_day(iata, d), None if pos is None else df.iloc[pos], (iata, d))
    # the day and the day before (what _window_rows concatenates) keep one categorical dtype across both days
    a, b = s.arrivals_day(iata, days[1]), s.arrivals_day(iata, days[2])
    both = pd.concat([a, b])
    assert isinstance(both["tail"].dtype, pd.CategoricalDtype)
    same_frame(both, pd.concat([df.iloc[groups[days[1]]], df.iloc[groups[days[2]]]]))


def test_arrivals_day_missing_airport_is_none():
    assert files.FileStore().arrivals_day("XXX", "2024-06-07") is None


@pytest.mark.parametrize("dest", ["ORD", "ATL", "DEN", "LAX", "DFW", "SFO"])
def test_flights2015_day_equals_whole_file_reader(dest):
    path = CH2 / "flights" / f"{dest}.csv.gz"
    if not path.exists():
        pytest.skip("no flights file")
    df = pd.read_csv(path, **FLIGHTS2015_DAY_KWARGS)
    s = files.FileStore()
    for d in _sample(df["arr_date"].dropna().unique(), 9) + ["1999-01-01", ""]:
        same_frame(s.flights2015_day(dest, d), df[df["arr_date"] == d], (dest, d))
    assert s.flights2015_day("XXX", "2015-01-01") is None


def test_returned_frames_are_independent_copies():
    s = files.FileStore()
    a = s.arrivals_day("ATL", "2024-07-15")
    want = a.copy()
    a.loc[a.index[0], "arr_delay"] = -9999.0
    a.drop(columns="tail", inplace=True)
    same_frame(s.arrivals_day("ATL", "2024-07-15"), want)
    f = s.flights2015_day("ORD", "2015-03-02")
    want = f.copy()
    f.loc[f.index[0], "taxi_in"] = -1.0
    same_frame(s.flights2015_day("ORD", "2015-03-02"), want)
    e = s.flights2015_day("ORD", "1999-01-01")
    e["x"] = 1
    assert "x" not in s.flights2015_day("ORD", "1999-01-01").columns


@pytest.mark.parametrize("first", ["hit", "missing"])
def test_first_day_is_served_from_the_build_parse(monkeypatch, first):
    """The index keeps its whole-file parse only until the first day call: that call selects from it (so a cold
    first day decompresses the file once, for the parse and the index together) and drops it, whatever it asks."""
    n = [0]
    real = gzip.decompress

    def counting(b):
        n[0] += 1
        return real(b)

    files._arrivals.cache_clear()
    files._dest_index.cache_clear()
    monkeypatch.setattr(files.gzip, "decompress", counting)
    s = files.FileStore()
    d, fd = ("2024-07-15", "2015-03-02") if first == "hit" else ("1999-01-01", "1999-01-01")
    a, f = s.arrivals_day("ATL", d), s.flights2015_day("ORD", fd)
    assert n[0] == 2                                         # one decompress per file, none for the day itself
    assert files._arrivals("ATL")._seed is None and files._dest_index("ORD")._seed is None
    monkeypatch.setattr(files.gzip, "decompress", real)
    df, groups = old_arrivals(WAVES / "arrivals" / "ATL.csv.gz")
    pos = groups.get(d)
    same_frame(a, None if pos is None else df.iloc[pos])
    old = pd.read_csv(CH2 / "flights" / "ORD.csv.gz", **FLIGHTS2015_DAY_KWARGS)
    same_frame(f, old[old["arr_date"] == fd])
    files._arrivals.cache_clear()
    files._dest_index.cache_clear()


def test_arrivals_cache_clear_drops_day_slices():
    s = files.FileStore()
    s.arrivals_day("ORD", "2024-06-04")
    ix = files._arrivals("ORD")
    assert "2024-06-04" in ix.days and len(ix.days) <= ix.KEEP
    files._arrivals.cache_clear()            # what build._arrivals_cache_clear does
    assert files._arrivals("ORD") is not ix


@pytest.mark.parametrize("year", ["2024", "2015"])
def test_movements_day_equals_whole_text_reader(year):
    d = files.REF / f"movements{year}"
    paths = sorted(d.glob("*.csv.gz"))[:3] if d.is_dir() else []
    if not paths:
        pytest.skip("no movements files")
    s = files.FileStore()
    for path in paths:
        text = gzip.decompress(path.read_bytes()).decode()
        days = sorted({ln[:10] for ln in text.split("\n")[1:] if ln})
        for day in _sample(days, 11) + [f"{year}-13-45", f"{year}-01-01x", ""]:
            assert s.movements_day(path.name[:3], day) == old_movements_day(path, day), (path.name, day)


# ---------------------------------------------------------------- synthetic files: every guard of the fast paths

ARR_HEAD = "date,carrier,flight_no,tail,origin,sched_dep,sched_arr,weather_delay,nas_delay,late_aircraft_delay," \
           "carrier_delay,arr_delay,cancelled\n"
ARR_ROWS = ["2024-06-07,AA,1,N1,ORD,600,700,0.0,0.0,0.0,0.0,-3.0,0",
            "2024-06-08,AA,1,N2,ORD,600,700,,1.0,0.0,0.0,12.0,0",
            "2024-06-07,DL,2,,ATL,,815,0.0,0.0,0.0,0.0,,1",
            "2024-06-09,UA,3,N3,DEN,905,1010,0.0,2.5,0.0,0.0,40.0,0",
            "2024-06-07,UA,4,N4,SFO,1100,1300,0.0,0.0,0.0,0.0,1.0,0"]


def _arrivals_case(tmp_path, monkeypatch, text: bytes):
    d = tmp_path / "arrivals"
    d.mkdir(exist_ok=True)
    (d / "XXX.csv.gz").write_bytes(gzip.compress(text))
    monkeypatch.setattr(files, "WAVES", tmp_path)
    files._arrivals.cache_clear()
    return d / "XXX.csv.gz"


@pytest.mark.parametrize("text, fast", [
    ((ARR_HEAD + "\n".join(ARR_ROWS) + "\n").encode(), True),
    ((ARR_HEAD + "\n".join(ARR_ROWS)).encode(), True),                                     # no final newline
    ((ARR_HEAD + "\n".join(ARR_ROWS[:2]) + "\n\n" + "\n".join(ARR_ROWS[2:]) + "\n").encode(), False),   # blank line
    ((ARR_HEAD + "\n".join(ARR_ROWS[:4]) + '\n2024-06-07,UA,4,"N4",SFO,1100,1300,0,0,0,0,1,0\n').encode(), False),
    ((ARR_HEAD + "\r\n".join(ARR_ROWS) + "\r\n").encode(), False),                          # \r\n line ends
    ((ARR_HEAD.replace("date,carrier", "carrier,date") + "\n".join(
        ",".join([r.split(",")[1], r.split(",")[0]] + r.split(",")[2:]) for r in ARR_ROWS) + "\n").encode(), True),
])
def test_arrivals_day_synthetic(tmp_path, monkeypatch, text, fast):
    path = _arrivals_case(tmp_path, monkeypatch, text)
    s = files.FileStore()
    for d in ("2024-06-07", "2024-06-08", "2024-06-09", "2024-06-10", ""):
        same_frame(s.arrivals_day("XXX", d), old_arrivals_day(path, d), d)
    assert files._arrivals("XXX").safe is fast


FL_HEAD = "row_id,date,arr_date,carrier,flight_no,tail,origin,sched_dep,sched_arr,arr_delay,weather_delay," \
          "late_aircraft_delay,nas_delay,carrier_delay,diverted,cancelled,wheels_on,taxi_in,wheels_off,taxi_out\n"
FL_ROWS = ["1,2015-03-01,2015-03-01,AA,10,N1,DEN,1815,2135,40,9,24,7,0,0,0,1328,7,1161,33",
           "2,2015-03-01,2015-03-02,AA,11,N2,SJU,2300,115,299,0,299,0,,0,0,1447,7,1278,10",   # an empty delay: float
           "3,2015-03-02,2015-03-02,DL,12,N3,ATL,1600,1915,25,0,0,25,0,0,0,1900,5,1610,12",
           "4,2015-03-03,2015-03-03,UA,13,,SFO,700,1300,61,0,61,0,0,1,0,,,705,",
           "5,2015-03-02,2015-03-03,UA,14,N5,LAX,2350,550,30,0,30,0,0,0,0,600,4,10,9"]


def _flights_case(tmp_path, monkeypatch, text: bytes):
    d = tmp_path / "flights"
    d.mkdir(exist_ok=True)
    (d / "XXX.csv.gz").write_bytes(gzip.compress(text))
    monkeypatch.setattr(files, "CH2", tmp_path)
    files._dest_index.cache_clear()
    return d / "XXX.csv.gz"


@pytest.mark.parametrize("rows, fast", [
    (FL_ROWS, True),
    (FL_ROWS[:1], True),                      # one row: the whole-file selection keeps its RangeIndex
    (FL_ROWS[2:3] + FL_ROWS[4:], True),       # no empty delays: int columns
    (FL_ROWS[:2] + ['6,2015-03-02,"2015-03-02",DL,1,N,ATL,1,2,3,0,0,0,0,0,0,1,1,1,1'], False),
])
def test_flights2015_day_synthetic(tmp_path, monkeypatch, rows, fast):
    path = _flights_case(tmp_path, monkeypatch, (FL_HEAD + "\n".join(rows) + "\n").encode())
    s = files.FileStore()
    for d in ("2015-03-01", "2015-03-02", "2015-03-03", "2015-03-04", ""):
        same_frame(s.flights2015_day("XXX", d), old_flights2015_day(path, d), d)
    assert files._dest_index("XXX").safe is fast


def _movements_case(tmp_path, monkeypatch, name, raw: bytes, compress=gzip.compress):
    d = tmp_path / "movements2015"
    d.mkdir(exist_ok=True)
    (d / f"{name}.csv.gz").write_bytes(compress(raw))
    monkeypatch.setattr(files, "REF", tmp_path)
    return d / f"{name}.csv.gz"


MOV = {
    "AAA": b"date,kind,tail,taxi\n2015-01-01,arrival,N1,\n2015-01-01,departure,,7\n2015-01-02,arrival,N2,3\n",
    "BBB": b"date,tail\n2015-01-01,N1\n2015-01-02,N2\n2015-01-01,N3\n",           # unsorted
    "CCC": b'date,tail\n2015-01-01,"N,1"\n',                                      # quoted field
    "DDD": b"date,tail,x\n2015-01-01,N1\n2015-01-02,N2,3\n",                      # short row on one day only
    "EEE": b"tail,date\nN1,2015-01-01\n",                                          # date not first
    "FFF": b"date,tail\n2015-01-01,N1\n2015-01-02,N2",                             # no final newline
    "GGG": b"date,tail\n2015-01-01,N1\nnot a row\n2015-01-01,N2\n2015-01-02,N3\n",  # a run broken by another line
    "HHH": b"date,tail\n2015-01-01,N1\n2015-01-01,N2,extra\n",                     # long row
    "III": b"date,tail\r\n2015-01-01,N1\r\n",                                       # \r\n
    "JJJ": b"date,tail\n",                                                          # header only
    "KKK": b"",                                                                     # empty
    "LLL": "date,tail\n2015-01-01,Nø\n2015-01-02,N2\n".encode(),                   # non-ASCII: whole-text path
    "MMM": b"date,date\n2015-01-01,2015-01-01\n",                                   # duplicate header
    "NNN": b"date,tail\n2015-01-01\n2015-01-01,N1\n",                               # a row with no comma at all
}


@pytest.mark.parametrize("name", sorted(MOV))
def test_movements_day_synthetic(tmp_path, monkeypatch, name):
    path = _movements_case(tmp_path, monkeypatch, name, MOV[name])
    s = files.FileStore()
    for day in ("2015-01-01", "2015-01-02", "2015-01-03", "2015-1-1", ""):
        # a long row or an empty file makes the frame path raise: it must raise the same way
        assert _outcome(s.movements_day, name, day) == _outcome(old_movements_day, path, day), (name, day)


def _outcome(fn, *args):
    try:
        return "ok", fn(*args)
    except Exception as e:  # noqa: BLE001 - the exception class is the compared outcome
        return "raised", type(e)


def test_movements_day_multi_member_gzip_and_resume_points(tmp_path, monkeypatch):
    """Several gzip members (gzip.decompress reads all) take the whole-text path; a long file is read from the
    nearest zlib resume point and still cuts exactly the same bytes for every day."""
    rows = [f"2015-{m:02d}-{d:02d},arrival,N{i},{i % 17}" for m in range(1, 13) for d in range(1, 29) for i in range(40)]
    raw = ("date,kind,tail,taxi\n" + "\n".join(rows) + "\n").encode()
    half = raw.index(b"\n2015-07-01") + 1
    multi = _movements_case(tmp_path, monkeypatch, "MUL", raw, lambda b: gzip.compress(b[:half]) + gzip.compress(b[half:]))
    monkeypatch.setattr(files, "_CKPT_EVERY", 4096)
    monkeypatch.setattr(files, "_GZ_CHUNK", 512)
    one = _movements_case(tmp_path, monkeypatch, "ONE", raw)
    st = one.stat()
    ix = files._movements_index(one, st.st_size, st.st_mtime_ns)
    assert ix is not None and len(ix.ckpts) > 50
    st = multi.stat()
    assert files._movements_index(multi, st.st_size, st.st_mtime_ns) is None
    s = files.FileStore()
    for day in ["2015-01-01", "2015-06-28", "2015-07-01", "2015-12-28", "2015-12-29"] + \
               [f"2015-{m:02d}-{d:02d}" for m in range(1, 13) for d in (3, 17)]:
        want = old_movements_day(one, day)
        assert s.movements_day("ONE", day) == want and s.movements_day("MUL", day) == want, day


def test_movements_text_seam_still_forces_the_frame_path(tmp_path, monkeypatch):
    """Tests replace `_movements_text` with None for fixture airports: movements_day must then use the frames."""
    _movements_case(tmp_path, monkeypatch, "AAA", MOV["AAA"])
    frame = pd.DataFrame({"date": ["2015-01-01"], "tail": ["FIXTURE"]})
    monkeypatch.setattr(files, "_movements", lambda iata, year="2015": (frame, "date"))
    monkeypatch.setattr(files, "_movements_text", lambda iata, year="2015": None)
    got = files.FileStore().movements_day("AAA", "2015-01-01")
    assert pd.read_csv(io.BytesIO(gzip.decompress(got)), dtype=str)["tail"].tolist() == ["FIXTURE"]


def test_movements_index_keeps_no_text():
    d = files.REF / "movements2024"
    path = d / "ORD.csv.gz"
    if not path.exists():
        pytest.skip("no movements file")
    st = path.stat()
    ix = files._movements_index(path, st.st_size, st.st_mtime_ns)
    resident = sum(len(v) if isinstance(v, (bytes, bytearray)) else 0 for v in vars(ix).values())
    assert resident < 4096 and len(ix.ckpts) <= st.st_size // 100_000 + 2
