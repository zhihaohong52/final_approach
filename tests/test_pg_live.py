import pytest
from atm.datastore.pg import PgStore
pytestmark = pytest.mark.live_db

def _clean(url, current_id=None):
    import psycopg
    with psycopg.connect(url) as c:
        c.execute("delete from live.decisions where run_id in ('0123456789ab', '0123456789ac', '0123456789ad', 'ffffffffffff')")
        c.execute("delete from live.runs where wave_id = 'TEST-W'")
        c.execute("delete from eval.benchmark_reports where label like 'test-%'")
        c.execute("delete from live.iem_cache where station = 'TST'")
        if current_id is not None and not c.execute("select 1 from eval.benchmark_reports where is_current").fetchone():
            c.execute("update eval.benchmark_reports set is_current = true where id = %s", (current_id,))

@pytest.fixture
def s(live_db_url):
    import psycopg
    with psycopg.connect(live_db_url) as c:     # the seeded 'main' report (a killed earlier run may have left test-b current)
        row = c.execute("select id from eval.benchmark_reports where label = 'main' order by id desc limit 1").fetchone()
    _clean(live_db_url)
    st = PgStore(live_db_url); st.open(); yield st; st.close()
    _clean(live_db_url, row[0] if row else None)

def test_retention_prunes_only_unpinned(s, live_db_url):
    import psycopg
    s.save_run({"run_id": "0123456789ad", "wave_id": "TEST-W", "mode": "formulas", "outcome": "consensus"})
    s.save_run({"run_id": "0123456789ac", "wave_id": "TEST-W", "mode": "formulas", "outcome": "fallback"})
    s.record_decision({"run_id": "0123456789ac", "decision": "APPROVE", "note": "", "edits": []})
    with psycopg.connect(live_db_url) as c:     # make the two test runs the oldest
        c.execute("update live.runs set created_at = '2000-01-01' where wave_id = 'TEST-W'")
        n = c.execute("select count(*) from live.runs where not pinned").fetchone()[0]
    s.run_cap = n - 1                             # exactly one unpinned run over the cap: the oldest
    assert s._prune_runs(force=True) == 1
    assert s.get_run("0123456789ad") is None and s.get_run("0123456789ac") is not None

def test_run_roundtrip_and_list(s):
    r = {"run_id": "0123456789ab", "wave_id": "TEST-W", "mode": "formulas", "model": "formula", "outcome": "consensus", "log": []}
    assert s.save_run(r) and s.get_run("0123456789ab") == r
    assert s.list_runs("TEST-W", 5)[0] | {"created_at": None} == {"run_id": "0123456789ab", "wave_id": "TEST-W",
        "mode": "formulas", "model": "formula", "outcome": "consensus", "created_at": None, "decided": False}

def test_decision_pins_run(s):
    s.save_run({"run_id": "0123456789ac", "wave_id": "TEST-W", "mode": "formulas", "outcome": "fallback"})
    assert s.record_decision({"run_id": "0123456789ac", "decision": "APPROVE", "note": "", "edits": []})
    assert s.list_runs("TEST-W", 5)[0]["decided"] is True

def test_decision_for_unknown_run_is_stored(s):
    assert s.record_decision({"run_id": "ffffffffffff", "decision": "OVERRIDE", "note": "manual", "edits": []})

def test_override_without_note_rejected_by_db(s):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        s.record_decision({"run_id": "ffffffffffff", "decision": "OVERRIDE", "note": "", "edits": []})

def test_benchmark_current_switches(s):
    s.put_benchmark({"n": 1}, None, "test-a"); s.put_benchmark({"n": 2}, "md", "test-b")
    assert s.benchmark_current() == {"n": 2}

def test_iem_roundtrip(s):
    s.iem_put("TST", "2015-01-01", "station,valid\n")
    assert s.iem_get("TST", "2015-01-01")[1] == "station,valid\n"
