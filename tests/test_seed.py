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

@pytest.mark.live_db
def test_failed_load_keeps_old_table(live_db_url, monkeypatch):
    import psycopg
    db_seed.main(["--url-env", "DATABASE_TEST_URL", "small"])            # runways loaded
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


def test_copy_fields_keep_empty_strings_and_nulls_apart():
    # COPY csv reads an unquoted empty field as NULL: text '' must be quoted, numeric blanks must not be
    assert db_seed._pg_line(["a", "", None, "x,y", 'q"r'], [True, True, True, True, True]) == 'a,"",,"x,y","q""r"\n'
    assert db_seed._cell("", "text") == "" and db_seed._cell("", "int4") is None
    assert db_seed._cell("7599.0", "int4") == "7599" and db_seed._cell("5.5", "float8") == "5.5"
    with pytest.raises(ValueError):
        db_seed._cell("7.5", "int4")


def test_reference_index_sql_is_unnamed():
    # runs against <t>__new before the old table is dropped: a named index or constraint would collide
    for table, stmts in db_seed._REF_INDEXES.items():
        for st in stmts:
            assert " constraint " not in st.lower() and not st.lower().startswith("create index if"), (table, st)
            if st.lower().startswith(("create index", "create unique")):
                assert st.lower().split()[2 if st.lower().startswith("create index") else 3] == "on", (table, st)


@pytest.mark.live_db
def test_swap_twice_with_pk_and_index(live_db_url):
    import psycopg
    with psycopg.connect(live_db_url, autocommit=True) as c:
        c.execute("drop table if exists ref.swap_probe")
        c.execute("create table ref.swap_probe (a int4, b text)")
        try:
            for _ in range(2):          # the second swap must pick fresh index and constraint names
                n = db_seed.swap_load(c, "ref.swap_probe", ["a", "b"], iter(["1,x\n", "2,y\n"]),
                                      ["alter table ref.swap_probe__new add primary key (a)",
                                       "create index on ref.swap_probe__new (b)"])
                assert n == 2
            q = lambda sql: c.execute(sql).fetchone()[0]
            assert q("select count(*) from ref.swap_probe") == 2
            assert q("select count(*) from pg_indexes where schemaname = 'ref' and tablename = 'swap_probe'") == 2
            assert q("select has_table_privilege('app_read', 'ref.swap_probe', 'select')")
        finally:
            c.execute("drop table if exists ref.swap_probe")
            c.execute("drop table if exists ref.swap_probe__new")


@pytest.mark.live_db
def test_grants_survive_reseed(live_db_url):                  # design §9: default privileges reach the swapped table
    import psycopg
    with psycopg.connect(live_db_url) as c:
        c.execute("delete from meta.seed_manifest where dataset = 'small'")   # force a real swap
    assert db_seed.main(["--url-env", "DATABASE_TEST_URL", "small"]) == 0
    with psycopg.connect(live_db_url) as c:
        q = lambda sql: c.execute(sql).fetchone()[0]
        assert q("select has_table_privilege('app_read', 'ref.runways', 'select')")
        assert q("select has_table_privilege('app_write', 'live.runs', 'insert')")
        assert not q("select has_schema_privilege('app_read', 'raw', 'usage')")
        assert not q("select has_schema_privilege('app_read', 'meta', 'usage')")
        assert q("select count(*) from pg_constraint where conrelid = 'ref.runways'::regclass and contype = 'p'") == 1


def test_raw_plan_order_and_absent_files(tmp_path, monkeypatch):
    d = tmp_path / "data"
    for sub, name in [("challenge3", "airports.dat"), ("challenge2", "flights.csv"), ("challenge5", "Flight_Data_With_Weather_Final.csv"),
                      ("challenge3", "kalimantan_80_locations_6800kt_full_range.csv")]:
        (d / sub).mkdir(parents=True, exist_ok=True)
        (d / sub / name).write_text("a,b\n1,2\n")
    monkeypatch.setattr(db_seed, "DATA", d)
    names = [t.table for t in db_seed._raw_tables()]
    assert names == ["raw.ch3_airports_dat", "raw.ch3_kalimantan_locations", "raw.ch2_flights", "raw.ch5_flights_weather"]


def test_raw_estimate_flags_big_tables(tmp_path):
    big = db_seed.RawTable("raw.x", tmp_path / "x.csv", "csv", None)
    (tmp_path / "x.csv").write_bytes(b"0" * 1000)
    assert db_seed._estimate_bytes(big) == 1500


def test_raw_csv_roundtrip_lines(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text('Id,Name,Score\n1,"a,b",\n2,,1.5\n')
    t = db_seed.RawTable("raw.t", p, "csv", True)
    cols, types, lines = db_seed._raw_prepare(t, chunk_rows=1)
    assert cols == ["id", "name", "score"] and types == ["int2", "text", "float8"]
    assert list(lines) == ['0,1,"a,b",\n', '1,2,"",1.5\n']


@pytest.mark.live_db
def test_raw_loads_last_and_drops(live_db_url):
    import psycopg
    assert db_seed.main(["--url-env", "DATABASE_TEST_URL", "--budget-mb", "1", "raw"]) == 4   # stops after the first table (cheap; no 150 MB of ch2/ch5 WAL per run)
    with psycopg.connect(live_db_url) as c:
        n = c.execute("select count(*) from raw.ch3_airports_dat").fetchone()[0]
        assert n > 7000
    assert db_seed.main(["--url-env", "DATABASE_TEST_URL", "--drop", "raw"]) == 0


def test_replays_seeded_with_their_engine_version(tmp_path, monkeypatch):
    import json
    (tmp_path / "A.json").write_text(json.dumps({"messages": [], "result": {}}))
    (tmp_path / "B.json").write_text(json.dumps({"engine_version": "v3-air", "messages": [], "result": {}}))
    monkeypatch.setattr(db_seed, "_replay_files", lambda: sorted(tmp_path.glob("*.json")))
    seen = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params): seen.append(params[:2])

    class Conn:
        def cursor(self): return Cur()
    assert db_seed._load_replays(Conn()) == 2
    assert seen == [("A", "v2"), ("B", "v3-air")]
