"""Load the reference, live and eval datasets into Postgres as the owner role (design §9).

    python3 scripts/db_seed.py [dataset ...] [--check] [--budget-mb 750] [--drop raw] [--url-env NAME]

Reads the URL from $DATABASE_URL (direct, owner) or the variable named by --url-env; it is never printed. A dataset
whose source files + SCRIPT_VERSION hash matches meta.seed_manifest is skipped. --check only reports what is stale.
Exit codes: 0 ok, 1 stale (--check) or database error, 2 URL not set, 4 over the size budget.
"""
import argparse
import csv
import io
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atm  # noqa: E402,F401  (loads .env)
from atm.datastore import pack  # noqa: E402
from atm.datastore.files import FilePaths  # noqa: E402

SCRIPT_VERSION = "1"
_P = FilePaths()
ROOT = _P.root
EVAL = ROOT / "eval"


@dataclass
class Seeder:
    name: str
    sources: Callable[[], list[Path]]
    load: Callable[[object], int]      # load(conn) -> rows


# ------------------------------------------------------------------ shadow-table load

def _copy_rows(cur, table: str, columns: list[str], rows: Iterable[str]) -> None:
    """Stream CSV lines (each ending in a newline) into `table` with COPY."""
    cols = ", ".join(columns)
    with cur.copy(f"copy {table} ({cols}) from stdin with (format csv)") as cp:
        for line in rows:
            cp.write(line)


def swap_load(conn, table: str, columns: list[str], rows_csv: Iterable[str], index_sql: list[str]) -> int:
    """Build <table>__new, COPY the rows in, verify the count, then drop/rename in one transaction. A failure rolls
    everything back and leaves the old table. `index_sql` statements (primary key, unique, indexes) run against
    <table>__new, since `like ... including constraints` copies neither; they run before the drop to keep the
    window short. `table` and `columns` come only from the registry (identifiers are not parameterisable)."""
    new = f"{table}__new"
    n = 0

    def counted(it: Iterable[str]):
        nonlocal n
        for line in it:
            n += 1
            yield line

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"drop table if exists {new}")   # a previous failed load may have left one
            cur.execute(f"create table {new} (like {table} including defaults including constraints "
                        f"including generated including storage)")
            _copy_rows(cur, new, columns, counted(rows_csv))
            got = cur.execute(f"select count(*) from {new}").fetchone()[0]
            if got != n:
                raise RuntimeError(f"{table}: copied {got} rows, expected {n}")
            for stmt in index_sql:
                cur.execute(stmt)
            cur.execute(f"drop table {table}")
            cur.execute(f"alter table {new} rename to {table.split('.', 1)[-1]}")
    return n


def _csv_line(*vals) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(vals)
    return buf.getvalue()


# ------------------------------------------------------------------ datasets of this task

def _replay_files() -> list[Path]:
    return sorted(_P.replay_dir.glob("*.json"))


def _load_replays(conn) -> int:
    from psycopg.types.json import Jsonb
    files = _replay_files()
    with conn.cursor() as cur:
        for f in files:
            cur.execute(
                "insert into eval.replays (wave_id, engine_version, body) values (%s, 'v2', %s) "
                "on conflict (wave_id, engine_version) do update set body = excluded.body, recorded_at = now()",
                (f.stem, Jsonb(json.loads(f.read_text()))))
    return len(files)


def _run_files() -> list[Path]:
    return sorted(_P.runs_dir.glob("*.json"))


def _load_runs(conn) -> int:
    from psycopg.types.json import Jsonb
    n = 0
    with conn.cursor() as cur:
        for f in _run_files():
            r = json.loads(f.read_text())
            cur.execute(
                "insert into live.runs (run_id, wave_id, mode, model, outcome, engine_version, result) "
                "values (%s, %s, %s, %s, %s, %s, %s) on conflict (run_id) do nothing",
                (r["run_id"], r["wave_id"], r["mode"], r.get("model"), r.get("outcome"),
                 r.get("engine_version") or "v2", Jsonb(r)))
            n += cur.rowcount
    return n


_BENCH = [   # (label, report json, markdown, current)
    ("main", EVAL / "benchmark.json", EVAL / "BENCHMARK.md", True),
    ("100", EVAL / "benchmark_100.json", EVAL / "BENCHMARK_100.md", False),
    ("1000", None, EVAL / "BENCHMARK_1000.md", False),
]


def _benchmark_files() -> list[Path]:
    return [p for _, j, m, _ in _BENCH for p in (j, m) if p is not None]


def _load_benchmark(conn) -> int:
    from psycopg.types.json import Jsonb
    labels = [b[0] for b in _BENCH]
    with conn.cursor() as cur:
        cur.execute("update eval.benchmark_reports set is_current = false where is_current")
        cur.execute("delete from eval.benchmark_reports where label = any(%s)", (labels,))
        for label, jpath, mpath, current in _BENCH:
            report = json.loads(jpath.read_text()) if jpath is not None else {}
            cur.execute("insert into eval.benchmark_reports (label, is_current, report, markdown) "
                        "values (%s, %s, %s, %s)", (label, current, Jsonb(report), mpath.read_text()))
    return len(_BENCH)


_V1 = {"results": EVAL / "results.json", "rows": EVAL / "rows.json"}


def _load_v1_eval(conn) -> int:
    from psycopg.types.json import Jsonb
    with conn.cursor() as cur:
        for name, p in _V1.items():
            cur.execute("insert into eval.v1_eval (name, body) values (%s, %s) "
                        "on conflict (name) do update set body = excluded.body", (name, Jsonb(json.loads(p.read_text()))))
    return len(_V1)


def _iem_files() -> list[Path]:
    return sorted(_P.iem_dir.glob("*.csv"))


def _load_iem(conn) -> int:
    """Files -> live.iem_cache through a temp table, so `on conflict do nothing` keeps rows already fetched."""
    with conn.cursor() as cur:
        cur.execute("create temp table _iem_stage (like live.iem_cache including defaults) on commit drop")
        lines = []
        for f in _iem_files():
            station, date = f.stem.rsplit("_", 1)
            fetched = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()
            lines.append(_csv_line(station, date, fetched, f.read_text()))
        _copy_rows(cur, "_iem_stage", ["station", "date", "fetched_at", "body"], lines)
        cur.execute("insert into live.iem_cache (station, date, fetched_at, body) "
                    "select station, date, fetched_at, body from _iem_stage on conflict (station, date) do nothing")
        return cur.rowcount


def _later(task: str) -> Callable:
    def load(conn):
        raise NotImplementedError(f"lands in Task {task}")
    return load


def _none() -> list[Path]:
    return []


SEEDERS: dict[str, Seeder] = {s.name: s for s in [
    Seeder("small", _none, _later("8")),
    Seeder("workbook", _none, _later("8")),
    Seeder("waves", _none, _later("8")),
    Seeder("bts_arrivals", _none, _later("8")),
    Seeder("metar_2024", _none, _later("8")),
    Seeder("iem_cache", _iem_files, _load_iem),
    Seeder("flights2015", _none, _later("8")),
    Seeder("disruptions_v1", _none, _later("8")),
    Seeder("replays", _replay_files, _load_replays),
    Seeder("runs_import", _run_files, _load_runs),
    Seeder("benchmark", _benchmark_files, _load_benchmark),
    Seeder("v1_eval", lambda: sorted(_V1.values()), _load_v1_eval),
    Seeder("raw", _none, _later("9")),
]}


# ------------------------------------------------------------------ driver

def _size_report(conn) -> None:
    rows = conn.execute(
        "select n.nspname || '.' || c.relname, pg_total_relation_size(c.oid) from pg_class c "
        "join pg_namespace n on n.oid = c.relnamespace where c.relkind in ('r', 'm') "
        "and n.nspname in ('ref', 'geo', 'live', 'eval', 'raw', 'meta') order by 2 desc").fetchall()
    for name, size in rows:
        print(f"  {size / 1e6:10.1f} MB  {name}")


def _stale(conn, s: Seeder) -> tuple[bool, str]:
    sha = pack.source_sha256(s.sources(), SCRIPT_VERSION)
    row = conn.execute("select source_sha256 from meta.seed_manifest where dataset = %s", (s.name,)).fetchone()
    return (row is None or row[0] != sha), sha


def run(conn, names: list[str], check: bool, budget_mb: int) -> int:
    """Seed `names` in order. Returns 0, 1 (--check found stale datasets), or 4 (over the size budget)."""
    stale = 0
    for name in names:
        s = SEEDERS[name]
        is_stale, sha = _stale(conn, s)
        conn.commit()
        if not is_stale:
            print(f"{name}: up to date")
            continue
        if check:
            print(f"{name}: stale")
            stale += 1
            continue
        try:
            n = s.load(conn)
        except NotImplementedError as e:
            conn.rollback()
            print(f"{name}: skipped ({e})")
            continue
        size = sum(p.stat().st_size for p in s.sources())
        conn.execute(
            "insert into meta.seed_manifest (dataset, source_sha256, script_version, n_rows, bytes) "
            "values (%s, %s, %s, %s, %s) on conflict (dataset) do update set source_sha256 = excluded.source_sha256, "
            "script_version = excluded.script_version, n_rows = excluded.n_rows, bytes = excluded.bytes, "
            "loaded_at = now()", (name, sha, SCRIPT_VERSION, n, size))
        conn.commit()
        print(f"{name}: loaded {n} rows")
        used = conn.execute("select pg_database_size(current_database())").fetchone()[0]
        conn.commit()
        if used > budget_mb * 1024 * 1024:
            print(f"database is {used / 1048576:.0f} MB, budget {budget_mb} MB; per-table sizes:")
            _size_report(conn)
            conn.commit()
            print("over budget: drop raw.* first (db_seed.py --drop raw)")
            return 4
    return 1 if stale else 0


def _drop_raw(conn) -> None:
    tables = conn.execute("select quote_ident(tablename) from pg_tables where schemaname = 'raw'").fetchall()
    with conn.transaction():
        for (t,) in tables:
            conn.execute(f"drop table raw.{t}")
        conn.execute("delete from meta.seed_manifest where dataset = 'raw'")
    print(f"dropped {len(tables)} raw tables")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("datasets", nargs="*", help=f"default: all of {', '.join(SEEDERS)}")
    ap.add_argument("--check", action="store_true", help="report stale datasets without loading; exit 1 if any")
    ap.add_argument("--budget-mb", type=int, default=750, help="stop when pg_database_size exceeds this")
    ap.add_argument("--drop", choices=["raw"], help="drop the raw.* tables and exit")
    ap.add_argument("--url-env", default="DATABASE_URL", help="environment variable holding the connection URL")
    args = ap.parse_args(argv)
    unknown = [d for d in args.datasets if d not in SEEDERS]
    if unknown:
        print(f"unknown dataset(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    url = os.environ.get(args.url_env)
    if not url:
        print(f"{args.url_env} not set", file=sys.stderr)
        return 2
    import psycopg
    try:
        with psycopg.connect(url, autocommit=False) as conn:
            if args.drop:
                _drop_raw(conn)
                return 0
            return run(conn, args.datasets or list(SEEDERS), args.check, args.budget_mb)
    except psycopg.Error as e:   # never echo the URL
        print(f"database error ({e.sqlstate or 'no sqlstate'}): {str(e).splitlines()[0] if str(e) else type(e).__name__}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
