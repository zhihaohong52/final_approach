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


def test_surface_tier():
    from scripts.geo_coverage import _surface_tier
    stands = [{"id": "s1"}]
    assert _surface_tier(None) == "none"
    assert _surface_tier({"stands": stands, "quality": {"reach_pct": {}}}) == "generic"
    assert _surface_tier({"stands": stands, "quality": {"reach_pct": {"10": 0.99, "28": 0.79}}}) == "generic"
    assert _surface_tier({"stands": stands, "quality": {"reach_pct": {"10": 0.99, "28": 0.8}}}) == "real"


def test_ord_surface_rss_under_60mb():
    # fresh interpreter: ru_maxrss is a high-water mark, so an in-process delta can read 0
    import subprocess, sys
    code = ("import resource,sys;from backend.geo import store;"
            "b=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;store.load_surface('KORD');store.load_procedures('KORD');"
            "a=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;print((a-b)/(1048576 if sys.platform=='darwin' else 1024))")
    mb = float(subprocess.check_output([sys.executable, "-c", code], text=True))
    print(f"ORD surface+procedures RSS delta: {mb:.1f} MB")
    assert mb <= 60


@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL; PgStore variant of the RSS check (needs surface and procedures seeded)
def test_ord_surface_rss_under_60mb_pgstore(live_db_url):
    import subprocess, sys
    code = ("import resource,sys;from backend.datastore.pg import PgStore;"
            "import os;st=PgStore(os.environ['DATABASE_TEST_URL']);st.open();"
            "b=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;"
            "import gzip,json;[json.loads(gzip.decompress(f(i))) for f,i in ((st.surface,'KORD'),(st.procedures,'KORD'))];"
            "a=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;print((a-b)/(1048576 if sys.platform=='darwin' else 1024))")
    mb = float(subprocess.check_output([sys.executable, "-c", code], text=True))
    assert mb <= 60


def test_evaluate_p95_under_3ms_ord():
    """Budget (global constraints): utility.evaluate p95 <= 3 ms on ORD wave F2015-270287
    (KORD, the recorded ORD replay in data/replay; not one of the 50 benchmark waves), over 200 distinct
    plans reached by legal_moves + apply. legal_moves already built every candidate's trajectories, so the
    plan-keyed trajectory cache is emptied first: each timed call builds its plan's paths (per-wave
    precomputation stays warm, as in a negotiation)."""
    import statistics, time
    from backend.wave import build, trajectory
    from backend.wave.moves import apply, fcfs_plan, legal_moves
    from backend.wave.utility import evaluate, negotiators
    w = build.build_wave("F2015-270287")
    start = fcfs_plan(w)
    seen, frontier = {start.id: start}, [start]
    while frontier and len(seen) < 200:
        p = frontier.pop(0)
        for ag in negotiators(w):
            for m in legal_moves(w, p, ag):
                q = apply(w, p, m)
                if q.id not in seen and len(seen) < 200:
                    seen[q.id] = q
                    frontier.append(q)
    assert len(seen) == 200
    evaluate(w, start)
    trajectory._CACHE.clear()
    ms = []
    for p in seen.values():
        t = time.perf_counter()
        evaluate(w, p)
        ms.append((time.perf_counter() - t) * 1000)
    p95 = statistics.quantiles(ms, n=20)[18]
    print(f"evaluate p95 on ORD: {p95:.2f} ms")
    assert p95 <= 3.0


def test_evaluate_p95_on_ord_replay_wave():
    """Spec §6.8 budget: utility.evaluate p95 <= 3 ms on the ORD replay wave, over distinct plans (each plan is
    memoised on (wave key, plan id), so repeating one plan would measure a cache hit)."""
    import time
    from backend.wave import build, moves, surface, trajectory, utility
    w = build.build_wave("F2015-270287")
    p = moves.fcfs_plan(w)
    plans, seen, frontier = [], {p.id}, [p]
    while len(plans) < 150 and frontier:
        q = frontier.pop(0)
        for ag in utility.negotiators(w):
            for m in moves._candidates(w, q, ag):
                r = moves.apply(w, q, m)
                if r.id not in seen:
                    seen.add(r.id); plans.append(r); frontier.append(r)
    trajectory.build.cache_clear(); surface.precompute(w)     # plan caches cold, the per-wave taxi table warm
    ts = []
    for q in plans[:150]:
        t = time.perf_counter(); utility.evaluate(w, q); ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    p95 = ts[int(len(ts) * 0.95)]
    print(f"evaluate p95 {p95:.3f} ms over {len(ts)} plans")
    assert len(ts) >= 100 and p95 <= 3.0
