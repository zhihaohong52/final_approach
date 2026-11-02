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
