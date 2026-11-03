import pytest


def test_coverage_rows_and_totals():
    from scripts.geo_coverage import coverage
    rows = coverage()
    assert len(rows) == 320
    # brief said 111; the Task 3 ruling (KASE and PAPG have circling-only approaches -> "none") makes it 110
    assert sum(r["procedures_tier"] == "star+iap" for r in rows) == 110
    assert {r["iata"] for r in rows if r["movements"]} == {
        "ATL", "AUS", "BNA", "BOS", "BWI", "CLT", "DAL", "DCA", "DEN", "DFW", "DTW", "EWR", "FLL", "HOU", "IAD", "IAH",
        "JFK", "LAS", "LAX", "LGA", "MCO", "MDW", "MIA", "MSP", "ORD", "PHL", "PHX", "SAN", "SEA", "SFO", "SLC", "TPA"}


def test_ord_surface_rss_under_60mb():
    # fresh interpreter: ru_maxrss is a high-water mark, so an in-process delta can read 0
    import subprocess, sys
    code = ("import resource,sys;from atm.geo import store;"
            "b=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;store.load_surface('KORD');store.load_procedures('KORD');"
            "a=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;print((a-b)/(1048576 if sys.platform=='darwin' else 1024))")
    mb = float(subprocess.check_output([sys.executable, "-c", code], text=True))
    print(f"ORD surface+procedures RSS delta: {mb:.1f} MB")
    assert mb <= 60


@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL; PgStore variant of the RSS check (needs surface and procedures seeded)
def test_ord_surface_rss_under_60mb_pgstore(live_db_url):
    import subprocess, sys
    code = ("import resource,sys;from atm.datastore.pg import PgStore;"
            "import os;st=PgStore(os.environ['DATABASE_TEST_URL']);st.open();"
            "b=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;"
            "import gzip,json;[json.loads(gzip.decompress(f(i))) for f,i in ((st.surface,'KORD'),(st.procedures,'KORD'))];"
            "a=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;print((a-b)/(1048576 if sys.platform=='darwin' else 1024))")
    mb = float(subprocess.check_output([sys.executable, "-c", code], text=True))
    assert mb <= 60
