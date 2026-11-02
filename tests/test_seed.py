import pytest
from scripts import db_seed

def test_default_order_puts_raw_last():
    names = list(db_seed.SEEDERS)
    assert names[-1] == "raw" and names.index("replays") < names.index("raw")

def test_no_url_exits_2(capsys):
    assert db_seed.main(["--check"]) == 2

@pytest.mark.live_db
def test_seed_then_check_is_clean(live_db_url):
    assert db_seed.main(["--url-env", "DATABASE_TEST_URL", "replays", "benchmark", "v1_eval"]) == 0
    assert db_seed.main(["--url-env", "DATABASE_TEST_URL", "--check", "replays", "benchmark", "v1_eval"]) == 0

@pytest.mark.skip(reason="needs Task 8's small seeder")              # Task 8 removes this line
@pytest.mark.live_db
def test_failed_load_keeps_old_table(live_db_url, monkeypatch):
    import psycopg
    db_seed.main(["--url-env", "DATABASE_TEST_URL", "small"])            # runways loaded (needs Task 8's seeder)
    manifest = "select source_sha256, loaded_at from meta.seed_manifest where dataset = 'small'"
    with psycopg.connect(live_db_url) as c:
        before = c.execute("select count(*) from ref.runways").fetchone()[0]
        m_before = c.execute(manifest).fetchone()
        def boom(*a, **k): raise psycopg.OperationalError("dropped mid-COPY")
        monkeypatch.setattr(db_seed, "_copy_rows", boom)
        with pytest.raises(psycopg.OperationalError):
            db_seed.swap_load(c, "ref.runways", ["ord"], iter(()), [])
        c.rollback()
        assert c.execute("select count(*) from ref.runways").fetchone()[0] == before
        assert c.execute(manifest).fetchone() == m_before
