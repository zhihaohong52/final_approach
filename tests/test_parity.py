import json
import pytest
from atm import datastore
from atm.datastore.files import FileStore
from atm.datastore.pg import PgStore
from atm.wave import build
pytestmark = pytest.mark.live_db

REPLAYS = ["F2015-270287", "F2015-58926", "F2015-69315", "F2015-75", "F2015-77162"]
# benchmark.sample_wave_ids(20, 7) + benchmark.sample_flights2015_ids(20, 7) on FileStore, 3 Nov 2026.
# Hard-coded: calling the samplers in parametrize costs ~63 s at collection on every offline run.
# All 25 F2015 station-days are in data/challenge2/metar_cache, so neither backend calls IEM.
SAMPLED = ["DCA-2024-06-22-16", "DTW-2024-07-22-23", "ATL-2024-06-15-18", "LAS-2024-07-27-19", "AUS-2024-06-09-18",
           "SAN-2024-07-09-22", "PHL-2024-06-11-19", "MDW-2024-06-06-00", "ORD-2024-06-23-14", "FLL-2024-07-10-20",
           "EWR-2024-06-20-23", "JFK-2024-07-06-16", "BWI-2024-07-19-12", "SFO-2024-08-19-23", "BNA-2024-07-07-21",
           "LGA-2024-06-27-19", "CLT-2024-08-14-15", "LAX-2024-06-15-11", "MIA-2024-06-28-21", "SEA-2024-06-27-16",
           "F2015-144520", "F2015-343919", "F2015-210010", "F2015-156608", "F2015-317184", "F2015-105396",
           "F2015-324426", "F2015-53108", "F2015-60842", "F2015-209722", "F2015-314303", "F2015-302254",
           "F2015-115532", "F2015-112396", "F2015-169005", "F2015-161169", "F2015-251827", "F2015-209820",
           "F2015-298859", "F2015-112468"]

def _both(live_db_url, fn):
    out = []
    for st in (FileStore(), PgStore(live_db_url)):
        datastore.set_store(st); build._reset_caches()
        try: out.append(json.dumps(fn(), sort_keys=True, default=str))
        finally: datastore.set_store(None)
    return out

@pytest.mark.parametrize("wid", REPLAYS + SAMPLED)
def test_parity_build_wave(live_db_url, wid):
    a, b = _both(live_db_url, lambda: build.build_wave(wid).to_dict())
    assert a == b

@pytest.mark.parametrize("args", [dict(), dict(airport="ORD"), dict(q="DL"), dict(airport="ATL", q="N9"),
                                  dict(q="F2015-2702"), dict(q="%"), dict(q="_"), dict(offset=50), dict(limit=0),
                                  dict(airport="CID")])
def test_parity_listings(live_db_url, args):
    from atm.wave import flights2015 as F
    a, b = _both(live_db_url, lambda: F.search(**{"limit": 20, **args}))
    assert a == b
    a, b = _both(live_db_url, lambda: build.list_waves(airport=args.get("airport"), q=args.get("q"), limit=20))
    assert a == b

def test_like_escape_percent_matches_nothing(live_db_url):
    datastore.set_store(PgStore(live_db_url))
    try:
        from atm.wave import flights2015 as F
        assert F.search(q="%", limit=5) == ([], 0)
    finally:
        datastore.set_store(None)

def test_parity_benchmark_and_imported_run(live_db_url):      # design §13
    fs, ps = FileStore(), PgStore(live_db_url)
    assert ps.benchmark_current() == fs.benchmark_current()
    rid = sorted(p.stem for p in fs.paths.runs_dir.glob("*.json"))[0]   # an eval/runs file, imported by runs_import
    assert ps.get_run(rid) == fs.get_run(rid)
