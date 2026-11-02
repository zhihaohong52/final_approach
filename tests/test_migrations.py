import re
from pathlib import Path
import pytest
from scripts import db_migrate

M = Path(__file__).resolve().parents[1] / "migrations"

def test_names_sorted_and_patterned():
    files = sorted(p.name for p in M.glob("*.sql"))
    assert files[:4] == ["0001_schemas_meta.sql", "0002_roles_grants.sql", "0003_ref.sql", "0004_live_eval.sql"]
    assert all(re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", f) for f in files)

def test_checksums_match_committed_files():
    committed = dict(reversed(l.split("  ")) for l in (M / "CHECKSUMS").read_text().splitlines())
    assert committed == db_migrate.checksums(M)

def test_no_url_exits_2(monkeypatch, capsys):
    assert db_migrate.main([]) == 2 and "DATABASE_URL" in capsys.readouterr().err

def test_every_file_table_has_ord_and_pk():
    sql = (M / "0003_ref.sql").read_text()
    for t in ("flights2015", "waves", "metar_2024", "runways", "aircraft", "gates", "airports", "airlines"):
        block = re.search(rf"create table ref\.{t} \((.*?)\);", sql, re.S).group(1)
        assert "ord int4" in block and "primary key" in block

@pytest.mark.live_db
def test_migrate_twice_is_noop(live_db_url):
    assert db_migrate.main(["--url-env", "DATABASE_TEST_URL"]) == 0
    assert db_migrate.main(["--url-env", "DATABASE_TEST_URL"]) == 0   # prints "up to date"
