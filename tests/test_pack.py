import gzip, io
import pandas as pd
from atm.datastore import pack

def test_gzip_is_deterministic_and_roundtrips():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", None]})
    b1, b2 = pack.gzip_csv(df), pack.gzip_csv(df)
    assert b1 == b2
    pd.testing.assert_frame_equal(pd.read_csv(io.BytesIO(gzip.decompress(b1))), df)

def test_like_escape():
    assert pack.like_escape("50%_a\\b") == "50\\%\\_a\\\\b"

def test_hash_changes_with_content(tmp_path):
    p = tmp_path / "x.csv"; p.write_text("a\n1\n")
    h1 = pack.source_sha256([p], "1")
    assert h1 != pack.source_sha256([p], "2")          # script version change
    p.write_text("a\n2\n")
    assert h1 != pack.source_sha256([p], "1")          # content change

def test_split_by_groups_in_key_order():
    df = pd.DataFrame({"k": ["b", "a", "b"], "v": [1, 2, 3]})
    out = [(k, list(s["v"])) for k, s in pack.split_by(df, ["k"])]
    assert out == [(("a",), [2]), (("b",), [1, 3])]


def test_infer_types_narrowest():
    df = pd.DataFrame({"a": [1, 2], "b": [1, 70000], "c": [1.5, None], "d": [None, None], "e": ["x", "y"],
                       "f": [1, 2**40]})
    assert pack.infer_pg_types(df) == {"a": "int2", "b": "int4", "c": "float8", "d": "text", "e": "text", "f": "int8"}


def test_pg_ident():
    assert [pack.pg_ident(h) for h in ["FL_DATE", "Wind Speed (km/h)", "1st"]] == ["fl_date", "wind_speed_km_h_", "c_1st"]


def test_pg_ident_dedupes_and_reserves():
    taken = {"ord"}
    assert [pack.pg_ident(h, taken) for h in ["A b", "a_b", "A-B", "ORD", ""]] == ["a_b", "a_b_2", "a_b_3", "ord_2", "c_"]


def test_type_scan_streams_across_chunks_and_keeps_text_exact():
    scan = pack.TypeScan(["n", "z", "f", "e", "i"])
    scan.update(pd.DataFrame({"n": ["1", ""], "z": ["12", "7"], "f": ["1", "2"], "e": ["", ""], "i": ["3.0", "4"]}))
    scan.update(pd.DataFrame({"n": ["40000", "-2"], "z": ["007", "8"], "f": ["2.5", ""], "e": ["", ""], "i": ["", "5"]}))
    assert scan.types() == {"n": "int4", "z": "text", "f": "float8", "e": "text", "i": "int2"}
