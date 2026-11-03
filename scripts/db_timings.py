"""Time the read paths against the design section 10 budgets, through FallbackStore(PgStore).

    python3 scripts/db_timings.py [--n 20] [--url-env DATABASE_URL_POOLED] [--after-idle]

Times, per path, n samples (p50 / p95 in ms): build_wave cold (caches reset before each sample) and warm for each
recorded replay wave, list_waves, flights search and get_run. Also times the first /api/waves-equivalent call in a
fresh process (list_waves plus the replay summaries) against 3 s. --after-idle sleeps 6 minutes first (Neon suspends
compute after 5) and takes one "first call after idle" sample. The URL is never printed.
Exit codes: 0 ok, 2 URL not set. Run from the dev machine, or from Render's shell for the deployed numbers.
"""
import argparse
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend  # noqa: E402,F401  (loads .env)

IDLE_S = 6 * 60
FIRST_WAVES_BUDGET_MS = 3000.0
BUDGETS_MS = {   # design section 10.1
    "build_wave cold": 400.0, "build_wave warm": 50.0, "list_waves": 300.0, "search": 300.0, "get_run": 150.0,
    "first call after idle": 1900.0,   # 400 ms build budget + 1.5 s compute wake
}


def pct(samples: list[float], p: float) -> float:
    """Nearest-rank percentile of a non-empty list."""
    s = sorted(samples)
    return s[max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))]


def timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def report(name: str, samples: list[float], budget: float | None = None) -> None:
    if not samples:
        print(f"{name:<28} no samples")
        return
    p50, p95 = pct(samples, 50), pct(samples, 95)
    verdict = "" if budget is None else ("  ok" if p95 <= budget else "  OVER")
    b = "" if budget is None else f"  budget p95 <= {budget:g} ms"
    print(f"{name:<28} n={len(samples):<3} p50={p50:8.1f} ms  p95={p95:8.1f} ms{b}{verdict}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=20, help="samples per path (default 20)")
    ap.add_argument("--url-env", default="DATABASE_URL_POOLED", help="environment variable holding the connection URL")
    ap.add_argument("--after-idle", action="store_true", help="sleep 6 minutes, then time one first call after idle")
    args = ap.parse_args(argv)
    url = os.environ.get(args.url_env)
    if not url:
        print(f"{args.url_env} not set", file=sys.stderr)
        return 2
    n = max(1, args.n)

    from backend.datastore import FileStore, set_store
    from backend.datastore.fallback import FallbackStore
    from backend.datastore.pg import PgStore
    from backend.wave import build

    primary = PgStore(url)
    primary.open()
    store = FallbackStore(primary, FileStore())
    set_store(store)
    try:
        def first_waves() -> None:   # what the first GET /api/waves does in a fresh process
            build.list_waves(limit=50)
            for wid in sorted(i for i in store.replay_ids() if i.startswith("F2015-")):
                build.build_wave(wid)

        build._reset_caches()
        first = timed(first_waves)
        print(f"{'first /api/waves (fresh)':<28} {first:8.1f} ms  budget <= {FIRST_WAVES_BUDGET_MS:g} ms"
              f"  {'ok' if first <= FIRST_WAVES_BUDGET_MS else 'OVER'}")

        if args.after_idle:
            print(f"sleeping {IDLE_S // 60} minutes so the compute suspends ...", flush=True)
            time.sleep(IDLE_S)
            build._reset_caches()
            report("first call after idle", [timed(lambda: build.build_wave(sorted(store.replay_ids())[0]))],
                   BUDGETS_MS["first call after idle"])

        waves = sorted(store.replay_ids())
        cold: list[float] = []
        warm: list[float] = []
        for i in range(n):
            wid = waves[i % len(waves)] if waves else None
            if wid is None:
                break
            build._reset_caches()
            cold.append(timed(lambda: build.build_wave(wid)))
            warm.append(timed(lambda: build.build_wave(wid)))
        report("build_wave cold", cold, BUDGETS_MS["build_wave cold"])
        report("build_wave warm", warm, BUDGETS_MS["build_wave warm"])
        report("list_waves", [timed(lambda: build.list_waves(limit=50)) for _ in range(n)], BUDGETS_MS["list_waves"])
        report("search", [timed(lambda: store.search_flights2015(None, "AA", 50, 0)) for _ in range(n)],
               BUDGETS_MS["search"])
        run_id = next((r.get("run_id") for w in waves for r in store.list_runs(w, 1)), None)
        if run_id:
            report("get_run", [timed(lambda: store.get_run(run_id)) for _ in range(n)], BUDGETS_MS["get_run"])
        else:
            print(f"{'get_run':<28} skipped (no saved run for any replay wave)")
        st = store.status()
        print(f"store: backend={st.backend} degraded={st.degraded}"
              + (f" reason={st.reason}" if st.reason else ""))
        if st.degraded:
            print("WARNING: some samples were served from files; the numbers above are not Postgres numbers")
    finally:
        primary.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
