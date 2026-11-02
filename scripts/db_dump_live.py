"""Dump live.runs and live.decisions to JSON lines (a backup of what the app recorded).

    python3 scripts/db_dump_live.py [--out DIR] [--url-env NAME]

Reads the connection URL from $DATABASE_URL (direct, dev machine) or the variable named by --url-env and writes
live_runs.jsonl and live_decisions.jsonl under --out (default .cache/dump/, gitignored). The URL is never printed.
Exit codes: 0 ok, 1 database error, 2 URL not set.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atm  # noqa: E402,F401  (loads .env)

ROOT = Path(__file__).resolve().parents[1]
QUERIES = {
    "live_runs.jsonl": "select run_id, wave_id, mode, model, outcome, engine_version, created_at, pinned, result "
                       "from live.runs order by created_at, run_id",
    "live_decisions.jsonl": "select id, run_id, flight_id, plan_id, decision, note, edits, recorded_at "
                            "from live.decisions order by id",
}


def dump(conn, out: Path) -> dict[str, int]:
    """Write one JSON object per row; returns {filename: row count}."""
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, sql in QUERIES.items():
        n = 0
        with conn.cursor() as cur, (out / name).open("w") as f:
            cur.execute(sql)
            cols = [d.name for d in cur.description]
            for row in cur:
                f.write(json.dumps(dict(zip(cols, row)), default=str, separators=(",", ":")) + "\n")
                n += 1
        counts[name] = n
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / ".cache" / "dump"), help="output directory (default .cache/dump/)")
    ap.add_argument("--url-env", default="DATABASE_URL", help="environment variable holding the connection URL")
    args = ap.parse_args(argv)
    url = os.environ.get(args.url_env)
    if not url:
        print(f"{args.url_env} not set", file=sys.stderr)
        return 2
    import psycopg
    try:
        with psycopg.connect(url) as conn:
            counts = dump(conn, Path(args.out))
    except psycopg.Error as e:   # never echo the URL
        print(f"database error ({e.sqlstate or 'no sqlstate'}): {str(e).splitlines()[0] if str(e) else type(e).__name__}",
              file=sys.stderr)
        return 1
    for name, n in counts.items():
        print(f"{Path(args.out) / name}: {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
