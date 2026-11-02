"""Load the reference, live and eval datasets into Postgres as the owner role (design §9).

    python3 scripts/db_seed.py [dataset ...] [--check] [--budget-mb 750] [--drop raw] [--url-env NAME]

Reads the URL from $DATABASE_URL (direct, owner) or the variable named by --url-env; it is never printed. A dataset
whose source files + SCRIPT_VERSION hash matches meta.seed_manifest is skipped. --check only reports what is stale.
Exit codes: 0 ok, 1 stale (--check) or database error, 2 URL not set, 4 over the size budget.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atm  # noqa: E402,F401  (loads .env)
from atm.datastore import pack  # noqa: E402
from atm.datastore.datasets import CH2, DATASETS, WAVES, WORKBOOK, Dataset  # noqa: E402
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



# ------------------------------------------------------------------ reference datasets (Task 8)
#
# Every index_sql below runs against <table>__new before the old table is dropped, so every index and constraint
# is UNNAMED: PostgreSQL then picks a free name and the second swap cannot collide with the first table's.

_INT = re.compile(r"-?\d+")
_SMALL_FILES = ("runways", "runway_history", "aircraft", "gates", "airports", "airlines", "openflights")
_WORKBOOK_SHEETS = ("airport_capacity", "wake_separation")


def _field(v: str | None, text: bool) -> str:
    """One CSV field for COPY. COPY reads an unquoted empty field as NULL, so '' in a text column is written as
    "" (stays an empty string) and an empty numeric/date field stays unquoted (NULL)."""
    if v is None:
        return ""
    if v == "":
        return '""' if text else ""
    if v == "\\." or any(c in v for c in ',"\n\r'):
        return '"' + v.replace('"', '""') + '"'
    return v


def _pg_line(vals: list, text: list[bool]) -> str:
    return ",".join(_field(v, t) for v, t in zip(vals, text)) + "\n"


def _cell(v: str, pgtype: str) -> str | None:
    """A file field as the value COPY should get. Integer columns accept '7599.0' (written as 7599) but nothing else."""
    if pgtype == "text":
        return v
    if v == "":
        return None
    if pgtype in ("int2", "int4") and not _INT.fullmatch(v):
        f = float(v)
        if not f.is_integer():
            raise ValueError(f"{pgtype} column got {v!r}")
        return str(int(f))
    return v


def _dataset_lines(ds: Dataset) -> Iterable[str]:
    """CSV lines (ord first) for one file-backed dataset, in file order."""
    text = [True] + [c.pgtype == "text" for c in ds.columns]
    with open(ds.path, newline="") as f:
        rd = csv.reader(f)
        if ds.header:
            next(rd)
        for i, row in enumerate(rd):
            if len(row) != len(ds.columns):
                raise ValueError(f"{ds.path.name} row {i}: {len(row)} fields, expected {len(ds.columns)}")
            yield _pg_line([str(i)] + [_cell(v, c.pgtype) for v, c in zip(row, ds.columns)], text)


def _ord_columns(ds: Dataset) -> list[str]:
    return ["ord"] + [c.col for c in ds.columns]


_PK_ORD = ["alter table {t} add primary key (ord)"]
_REF_INDEXES: dict[str, list[str]] = {
    "ref.runways": _PK_ORD + ["alter table {t} add unique (airport, runway)",
                              "update {t} set le_geog = extensions.st_makepoint(le_lon, le_lat)::extensions.geography "
                              "where le_lat is not null and le_lon is not null",
                              "update {t} set he_geog = extensions.st_makepoint(he_lon, he_lat)::extensions.geography "
                              "where he_lat is not null and he_lon is not null"],
    "ref.runway_history": _PK_ORD,
    "ref.aircraft": _PK_ORD + ["alter table {t} add unique (tail)"],
    "ref.gates": _PK_ORD,
    "ref.airports": _PK_ORD + ["alter table {t} add unique (iata_code)"],
    "ref.airlines": _PK_ORD + ["alter table {t} add unique (iata_code)"],
    "ref.openflights": _PK_ORD,
    "ref.metar_2024": _PK_ORD + ["create index on {t} (airport)"],
    "ref.disruptions_v1": _PK_ORD + ["alter table {t} add unique (id)"],
    "ref.waves": ["alter table {t} add primary key (wave_id)", "create index on {t} (airport, ord)",
                  "create index on {t} (ord)"],
    "ref.bts_arrivals_day": ["alter table {t} add primary key (airport, date)"],
    "ref.flights2015": ["alter table {t} add primary key (row_id)", "create index on {t} (dest, arr_date)",
                        "create index on {t} (dest, ord) where cancelled = 0", "create index on {t} (ord) where cancelled = 0"],
    "ref.workbook": ["alter table {t} add primary key (sheet, row_no)"],
}


def _swap(conn, table: str, columns: list[str], lines: Iterable[str]) -> int:
    return swap_load(conn, table, columns, lines, [s.format(t=f"{table}__new") for s in _REF_INDEXES[table]])


def _files_small() -> list[Path]:
    return [DATASETS[n].path for n in _SMALL_FILES]


def _load_small(conn) -> int:
    n = 0
    for name in _SMALL_FILES:
        ds = DATASETS[name]
        n += _swap(conn, ds.table, _ord_columns(ds), _dataset_lines(ds))
    return n


def _files_workbook() -> list[Path]:
    return [WORKBOOK]


def _workbook_lines() -> Iterable[str]:
    import openpyxl
    wb = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    try:
        for sheet in _WORKBOOK_SHEETS:
            for row_no, cells in enumerate(wb[sheet].iter_rows(min_row=4, values_only=True), start=4):
                yield _pg_line([sheet, str(row_no), json.dumps(list(cells))], [True, False, True])   # no default=: fail on odd types
    finally:
        wb.close()


def _load_workbook(conn) -> int:
    return _swap(conn, "ref.workbook", ["sheet", "row_no", "cells"], _workbook_lines())


def _files_waves() -> list[Path]:
    """The index plus everything a summary is computed from."""
    return ([DATASETS["waves_index"].path, DATASETS["metar_2024"].path, DATASETS["airports"].path,
             ROOT / "atm" / "wave" / "build.py"]       # the summary code: a change there makes `waves` stale
            + sorted((WAVES / "arrivals").glob("*.csv.gz")))


def _wave_lines() -> Iterable[str]:
    """index.csv rows with `summary` = build._summary(row) minus replay_available, computed on a FileStore."""
    from atm import datastore
    from atm.datastore.files import FileStore
    from atm.wave import build
    ds = DATASETS["waves_index"]
    with open(ds.path, newline="") as f:
        rows = list(csv.DictReader(f))
    prev = datastore._store
    datastore.set_store(FileStore())
    build._reset_caches()
    try:
        summaries: dict[int, dict] = {}
        for i in sorted(range(len(rows)), key=lambda i: rows[i]["airport"]):   # one airport at a time: the arrivals LRU holds 4
            row = dict(rows[i])
            row["disrupted_flights"] = re.sub(r"\.0(?=;|$)", "", row["disrupted_flights"])
            s = build._summary(row, set())
            s.pop("replay_available")
            summaries[i] = s
    finally:
        build._reset_caches()
        datastore.set_store(prev)
    text = [True, True, True, False, False, False, False, True, True]
    for i, r in enumerate(rows):
        yield _pg_line([r["wave_id"], str(i), r["airport"], r["date"], r["hour"], r["n_arrivals"], r["n_disrupted"],
                        r["disrupted_flights"], json.dumps(summaries[i])], text)


def _load_waves(conn) -> int:
    return _swap(conn, "ref.waves", ["wave_id", "ord", "airport", "date", "hour", "n_arrivals", "n_disrupted",
                                     "disrupted_flights", "summary"], list(_wave_lines()))   # computed before the transaction opens


def _files_bts() -> list[Path]:
    return sorted((WAVES / "arrivals").glob("*.csv.gz"))


def _bts_lines() -> Iterable[str]:
    """One gzip CSV per (airport, date): the file's own rows and columns, read and written as text so nothing is reformatted."""
    import pandas as pd
    for p in _files_bts():
        airport = p.name.split(".")[0]
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        for (date,), sub in pack.split_by(df, ["date"]):
            blob = pack.gzip_csv(sub)
            yield _pg_line([airport, date, str(len(sub)), "\\x" + blob.hex(), hashlib.sha256(blob).hexdigest()],
                           [True, False, False, False, True])


def _load_bts(conn) -> int:
    return _swap(conn, "ref.bts_arrivals_day", ["airport", "date", "n_rows", "payload", "sha256"], _bts_lines())


def _load_dataset(name: str) -> Callable:
    ds = DATASETS[name]
    return lambda conn: _swap(conn, ds.table, _ord_columns(ds), _dataset_lines(ds))


_F15_COLS = ["ord", "row_id", "date", "arr_date", "carrier", "flight_no", "tail", "origin", "dest", "sched_dep",
             "sched_arr", "arr_delay", "weather_delay", "late_aircraft_delay", "nas_delay", "carrier_delay",
             "diverted", "cancelled"]
_F15_TYPES = ["text" if c in ("carrier", "tail", "origin", "dest") else "date" if c in ("date", "arr_date") else "int4"
              for c in _F15_COLS]
_F15_TEXT = [t == "text" for t in _F15_TYPES]


def _files_flights2015() -> list[Path]:
    return [DATASETS["flights2015_index"].path] + sorted((CH2 / "flights").glob("*.csv.gz"))


def _flights2015_lines() -> Iterable[str]:
    """flights_index.csv (row order = ord) merged with the per-destination files on row_id for the three columns
    the index lacks (sched_dep, nas_delay, carrier_delay)."""
    import gzip
    extra: dict[str, tuple[str, str, str]] = {}
    for p in sorted((CH2 / "flights").glob("*.csv.gz")):
        with gzip.open(p, "rt", newline="") as f:
            for r in csv.DictReader(f):
                extra[r["row_id"]] = (r["sched_dep"], r["nas_delay"], r["carrier_delay"])
    with open(DATASETS["flights2015_index"].path, newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            if r["row_id"] not in extra:
                raise ValueError(f"flights_index row_id {r['row_id']} is missing from flights/{r['dest']}.csv.gz")
            sd, nas, carr = extra[r["row_id"]]
            vals = [str(i), r["row_id"], r["date"], r["arr_date"], r["carrier"], r["flight_no"], r["tail"], r["origin"],
                    r["dest"], sd, r["sched_arr"], r["arr_delay"], r["weather_delay"], r["late_aircraft_delay"],
                    nas, carr, r["diverted"], r["cancelled"]]
            yield _pg_line([_cell(v, t) for v, t in zip(vals, _F15_TYPES)], _F15_TEXT)


def _load_flights2015(conn) -> int:
    return _swap(conn, "ref.flights2015", _F15_COLS, _flights2015_lines())

def _later(task: str) -> Callable:
    def load(conn):
        raise NotImplementedError(f"lands in Task {task}")
    return load


def _none() -> list[Path]:
    return []


SEEDERS: dict[str, Seeder] = {s.name: s for s in [
    Seeder("small", _files_small, _load_small),
    Seeder("workbook", _files_workbook, _load_workbook),
    Seeder("waves", _files_waves, _load_waves),
    Seeder("bts_arrivals", _files_bts, _load_bts),
    Seeder("metar_2024", lambda: [DATASETS["metar_2024"].path], _load_dataset("metar_2024")),
    Seeder("iem_cache", _iem_files, _load_iem),
    Seeder("flights2015", _files_flights2015, _load_flights2015),
    Seeder("disruptions_v1", lambda: [DATASETS["disruptions_v1"].path], _load_dataset("disruptions_v1")),
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
