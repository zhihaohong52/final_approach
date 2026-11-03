# Final Approach data in Postgres (Neon) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every dataset the app reads or writes (live state, reference data, raw challenge files and, later, flight-path geometry) lives in Neon Postgres. A file backend keeps tests, offline dev and outages working with identical results.

**Architecture:**
- A new `backend/datastore/` package is the only code that knows where data lives. It has three stores:
  - `FileStore`: today's files.
  - `PgStore`: psycopg 3 over Neon's pooled endpoint.
  - `FallbackStore`: Postgres first, then files, with a circuit breaker.
- Readers keep their parse code. The store hands them data in the source format, so the two backends are comparable.
- Plain SQL migrations and an idempotent COPY-based seeder load the committed files into Postgres. The engine's hot path never touches the store.

**Tech Stack:** Python 3.12, FastAPI, pandas 2.2.3, `psycopg[binary]==3.3.6`, `psycopg-pool==3.3.3`, PostgreSQL 16+ with PostGIS (Neon Free), pytest 9.1.1.

**Spec:** `docs/superpowers/specs/2026-11-02-postgres-data-design.md` (read §6 schema, §8 data access layer, §9 seeding, §10 budgets, §12 security before any task). Also `docs/api-contract.md` (v2.1 → v2.1.1).

## Global Constraints

- Commits: one commit per task on branch `redesign/console`. Each commit step stages the task's exact files with `git add <paths>` (never `git add -A` or `.`). Never push, never merge to `main`. Never read, print, edit or commit `.env`, and never print a database URL or password.
- Every commit message ends with the two trailer lines `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01CZQD1apQx15EEBy5x3LDJF`.
- Contract changes (`docs/api-contract.md`, `frontend/src/api/types.ts`, `frontend/src/api/samples/`) go in a commit prefixed `contract:`. Contract **v2.1.1** is additive only.
- Env vars: `DATABASE_URL_POOLED` (Neon **pooled** `-pooler` endpoint, `sslmode=require`; read by the app runtime, i.e. the PgStore pool and Render; the only DB secret on Render), `DATABASE_URL` (direct endpoint, owner role; migrations and seeding, dev machine only), `DATABASE_TEST_URL` (optional; direct owner URL of the Neon branch `test`), `ATM_STORE` (`files` | `pg`).
- **Coordinator ruling (3 Nov 2026), overrides the design's env names:** the user created the Neon project (AWS us-west-2, PostgreSQL 18.6, PostGIS available) and `.env` holds `DATABASE_URL` (direct, `neondb_owner`) and `DATABASE_URL_POOLED` (pooler, `neondb_owner`), both verified with psycopg 3 and `prepare_threshold=None`. Runtime reads `DATABASE_URL_POOLED`; `db_migrate.py`/`db_seed.py` read `DATABASE_URL`. Wherever the design says runtime `DATABASE_URL` read `DATABASE_URL_POOLED`, and for `DATABASE_OWNER_URL` read `DATABASE_URL`. The least-privilege `atm_app` login is **deferred**: until Task 11's optional step, `DATABASE_URL_POOLED` uses the owner role, and no task depends on `atm_app`. `psycopg[binary]` and `psycopg-pool` (≥ 3.2) are already installed locally.
- `python3 -m pytest -q` must pass with no network and no database (130 existing tests plus the new ones). `tests/conftest.py` forces `ATM_STORE=files` and strips the three `DATABASE_*` vars. Tests that need a database carry `@pytest.mark.live_db` and skip unless `DATABASE_TEST_URL` is set; they never use `DATABASE_URL_POOLED` or `DATABASE_URL`.
- No database call inside `rules.check`, `utility.evaluate`, move generation, votes or the fallback search. `/api/config` never queries the database. Nothing queries the database on a timer.
- Connection settings (PgStore): `psycopg_pool.NullConnectionPool(conninfo=DATABASE_URL_POOLED, max_size=4, timeout=10, open=False, kwargs={"prepare_threshold": None, "connect_timeout": 10, "application_name": "final-approach"})`. A `psycopg_pool.PoolTimeout` (a 5th concurrent request waiting > 10 s) is treated like `OperationalError`: fall back to files for that call. No `SET` statements in app code. `statement_timeout` and `search_path` live on the role.
- Retry on `psycopg.OperationalError`: 2 retries with base delays 0.5 s and 2 s, each plus uniform jitter of up to 30 %. Breaker: opens after 3 consecutive failed calls, half-opens after 60 s.
- Retention: newest **2,000** unpinned runs; `live.iem_cache` ≤ **40 MB**. Pruning runs after a write, at most once per hour per process.
- Seeder: default size guard `--budget-mb 750`. The `raw` dataset always loads last and is the first thing `--drop raw` removes.
- Schemas: `ref`, `geo`, `live`, `eval`, `raw`, `meta`, plus PostGIS in `extensions`. Group roles `app_read` and `app_write` (NOLOGIN); the login role `atm_app` is created by the user, never by code (deferred: optional step in Task 11).
- Every `scripts/db_*.py` starts with `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` and `import backend` (which loads `.env`) before reading env vars, so `python3 scripts/db_*.py …` works from the repo root without `PYTHONPATH`.
- Columns keep the source file's names and representation: flags as `int2` 0/1; every file-backed table has `ord int4` = the row's 0-based position in the source, and reads `order by ord`.
- Budgets (design §10): wave build p95 ≤ 400 ms with the compute awake; `/api/waves` and `/api/flights` p95 ≤ 300 ms; `/api/runs/{id}` p95 ≤ 150 ms; a wake adds ≤ 1.5 s once.
- Neon project: region `aws-us-west-2` (Render's default region is `oregon`), compute fixed at 0.25 CU.

## Review Focus

1. **The database is asleep, out of compute hours or unreachable when a request arrives.** Expected: up to 2 retries, then the same response from files, `Config.store.degraded == true`, never a 500. Pinned in Task 5 (`test_fallback_serves_files_after_retries`, `test_breaker_opens_and_half_opens`).
2. **Listings and paging without filters** (`/api/waves`, `/api/flights?offset=50`). Expected: the same rows in the same order as the file backend, because order comes from `ord`. Pinned in Task 8 (`test_parity_listings`, which includes an offset page).
3. **Search text containing `%`, `_` or `\`** (e.g. `q=%`). Expected: a literal substring match (no rows for `%`), not a wildcard that returns everything. Pinned in Task 8 (`test_like_escape`, `test_parity_listings`).
4. **A decision for a run the database has never seen** (saved during a fallback, or pruned). Expected: the decision is stored and `persisted: true`, never a 4xx/5xx. Pinned in Task 6 (`test_decision_for_unknown_run_is_stored`).
5. **A seed interrupted half-way** (connection drop during COPY). Expected: the old table is still served, the manifest is unchanged, and re-running completes. Pinned in Task 7 (`test_failed_load_keeps_old_table`).

---

## File Structure

**New package `backend/datastore/`**
- `__init__.py`: `get_store()`, `set_store()`, backend selection.
- `base.py`: `Store` protocol, `StoreStatus`, `DataUnavailable`.
- `datasets.py`: registry of file-backed datasets (source path, table, columns, pandas read kwargs).
- `files.py`: `FileStore` (today's file reads and writes, moved out of `build.py`, `flights2015.py`, `constraints.py`, `scenario.py`, `replay.py`, `server.py`).
- `pg.py`: `PgStore` and the connection helpers (`make_pool`, `session`, `with_retry`).
- `fallback.py`: `FallbackStore` (primary then secondary, breaker, status).
- `pack.py`: pure helpers shared by the seeder and the stores (gzip CSV packing per airport-day, LIKE escaping, raw type inference, source hashing).

**New scripts and SQL**
- `migrations/0001_schemas_meta.sql`, `0002_roles_grants.sql`, `0003_ref.sql`, `0004_live_eval.sql`, `0005_geo.sql`, `migrations/CHECKSUMS`.
- `scripts/db_migrate.py`, `scripts/db_seed.py`, `scripts/db_dump_live.py`, `scripts/db_timings.py`.

**Modified**
- `backend/wave/build.py`, `backend/wave/flights2015.py`, `backend/wave/constraints.py`, `backend/scenario.py`, `backend/wave/replay.py`, `backend/wave/benchmark.py`, `backend/server.py`, `scripts/make_samples.py`.
- `requirements.txt`, `pytest.ini`, `render.yaml`, `README.md`.
- Contract files: `docs/api-contract.md`, `frontend/src/api/types.ts`, `frontend/src/api/samples/config.json`, `decision.json`.
- `docs/superpowers/plans/2026-11-02-flight-paths-and-taxi.md` (Task 13).

**Tests (new)**
- `tests/conftest.py`, `tests/test_datastore_select.py`, `tests/test_store_files.py`, `tests/test_no_db_in_hot_path.py`.
- `tests/test_migrations.py`, `tests/test_fallback_store.py`, `tests/test_pack.py`, `tests/test_seed.py`.
- `tests/test_pg_live.py` (all `live_db`), `tests/test_parity.py` (all `live_db`), `tests/test_runs_api.py`.

---

## Milestone D0: Foundation (files only; deployable, no DB needed)

### Task 1: Datastore package, backend selection, offline test guard

**Files:**
- Create: `backend/datastore/__init__.py`, `backend/datastore/base.py`, `backend/datastore/files.py` (class shell with `status()` only), `tests/conftest.py`, `tests/test_datastore_select.py`
- Modify: `requirements.txt` (add `psycopg[binary]==3.3.6`, `psycopg-pool==3.3.3`), `pytest.ini` (add marker `live_db: needs DATABASE_TEST_URL (a Neon test branch)`)

**Interfaces:**
- Produces:
  - `base.StoreStatus` (dataclass): `backend: Literal["postgres", "files"]`, `degraded: bool`, `reason: str | None`, `persistent: bool`; `to_dict() -> dict`.
  - `base.DataUnavailable(Exception)`.
  - `base.Store(Protocol)` declares every method in design §8.2. Its signatures are fixed here and implemented by later tasks:
    - reference: `frame(name: str) -> pd.DataFrame | None`; `rows(name: str) -> list[dict[str, str]]`; `workbook_rows(sheet: str) -> list[tuple]`.
    - waves: `wave_row(wave_id: str) -> dict | None`; `wave_rows(airport: str | None, q: str | None, limit: int, offset: int = 0) -> list[dict]`; `wave_count(airport: str | None) -> int`; `wave_summary(wave_id: str) -> dict | None`; `arrivals_day(iata: str, date: str) -> pd.DataFrame | None`; `metar_2024_rows(iata: str) -> list[dict[str, str]]`.
    - 2015 flights: `flight2015_row(row_id: int) -> dict | None`; `flights2015_day(dest: str, arr_date: str) -> pd.DataFrame | None`; `search_flights2015(airport: str | None, q: str | None, limit: int, offset: int) -> tuple[list[dict], int]`.
    - IEM cache: `iem_get(station: str, date: str) -> tuple[float, str | None] | None`; `iem_put(station: str, date: str, body: str | None) -> None`.
    - runs and decisions: `save_run(result: dict) -> bool`; `get_run(run_id: str) -> dict | None`; `list_runs(wave_id: str, limit: int) -> list[dict]`; `record_decision(row: dict) -> bool`.
    - replays and evaluation: `replay(wave_id: str) -> dict | None`; `replay_ids() -> set[str]`; `put_replay(wave_id: str, body: dict, engine_version: str = "v2") -> None`; `benchmark_current() -> dict | None`; `put_benchmark(report: dict, markdown: str | None, label: str, current: bool = True) -> None`; `v1_eval(name: str) -> dict | list | None`.
    - geometry: `procedures(icao: str) -> bytes | None`; `surface(icao: str) -> bytes | None`; `movements_day(iata: str, date: str) -> bytes | None`.
    - other: `session() -> ContextManager[None]`; `status() -> StoreStatus`.
  - `get_store() -> Store`, which caches a module-level instance. Selection: `ATM_STORE=files` → `FileStore()`. `ATM_STORE=pg` or `DATABASE_URL_POOLED` set → `FallbackStore(PgStore(...), FileStore())`; until Task 5 this branch raises `NotImplementedError("pg backend lands in Task 5")`. Otherwise → `FileStore()`.
  - `set_store(store: Store | None) -> None` (`None` resets the cache).
  - `files.FileStore(paths: FilePaths | None = None)` with `@dataclass FilePaths(root: Path = ROOT, runs_dir: Path = ROOT/"eval/runs", decisions_path: Path = ROOT/"eval/decisions.jsonl", replay_dir: Path = ROOT/"data/replay", benchmark_path: Path = ROOT/"eval/benchmark.json", iem_dir: Path = ROOT/"data/challenge2/metar_cache")`. The resolved paths are a public attribute, `FileStore.paths: FilePaths`. `status()` returns `StoreStatus("files", False, None, False)`.

- [ ] **Step 1: Write the failing tests** (`tests/test_datastore_select.py`)

```python
import os
import pytest
from backend import datastore
from backend.datastore.files import FileStore

def test_suite_runs_on_files():
    assert os.environ.get("ATM_STORE") == "files"
    assert "DATABASE_URL_POOLED" not in os.environ and "DATABASE_URL" not in os.environ
    assert isinstance(datastore.get_store(), FileStore)

def test_no_url_means_files(monkeypatch):
    monkeypatch.delenv("ATM_STORE", raising=False)
    datastore.set_store(None)
    assert isinstance(datastore.get_store(), FileStore)
    datastore.set_store(None)

def test_files_status_is_not_persistent():
    s = FileStore().status()
    assert (s.backend, s.degraded, s.persistent) == ("files", False, False)
```

`tests/conftest.py`, in this order:
- **At module top level**, as the file's first statements (so it also covers test *collection*): `import backend` (its `.env` loader runs now, not later), then save `os.environ.get("DATABASE_TEST_URL")` in a module global `TEST_DB_URL`, delete `DATABASE_URL_POOLED`, `DATABASE_URL` and `DATABASE_TEST_URL` from `os.environ`, and set `ATM_STORE=files`. Collection-time code (e.g. `parametrize` over `benchmark.sample_wave_ids`) must never see a database URL.
- An autouse, session-scoped fixture that calls `datastore.set_store(None)` at the start.
- A function-scoped fixture `live_db_url(monkeypatch)`. It calls `pytest.skip("DATABASE_TEST_URL not set")` when `TEST_DB_URL` is None; otherwise it runs `monkeypatch.setenv("DATABASE_TEST_URL", TEST_DB_URL)` (scripts called with `--url-env DATABASE_TEST_URL` read it) and returns the URL.

Keep `tests/fixtures.py` untouched.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_datastore_select.py -q`. Expected: FAIL (`ModuleNotFoundError: backend.datastore`).
- [ ] **Step 3: Implement** the files above. `pip install -r requirements.txt` must succeed. `backend/datastore/__init__.py` must not import `psycopg` at module import time; it is imported lazily in the pg branch.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: all pass (existing tests + 3 new).
- [ ] **Step 5: Commit**

```bash
git add backend/datastore/__init__.py backend/datastore/base.py backend/datastore/files.py tests/conftest.py tests/test_datastore_select.py requirements.txt pytest.ini
git commit -m "feat(datastore): store package, backend selection, offline test guard"
```

### Task 2: FileStore reference reads; builders read through the store

**Files:**
- Create: `backend/datastore/datasets.py`, `tests/test_store_files.py`, `tests/test_no_db_in_hot_path.py`
- Modify: `backend/datastore/files.py`, `backend/wave/build.py` (`_index`, `_index_row`, `_arrivals`, `_window_rows`, `_runways_by_airport`, `_metar_table`, `_aircraft_table`, `_gates_table`, `_airlines`, `_airports`, `_runway_history`, `_summary`, `list_waves`, `random_wave`), `backend/wave/flights2015.py` (`_index`, `_locate`, `_dest_frame`, `search`, `build_from_row`'s day filter), `backend/wave/constraints.py` (`_load`), `backend/scenario.py` (`disruptions`, `airports`, `airlines`)

**Interfaces:**
- Consumes: `Store` and `FileStore` (Task 1).
- Produces:
  - `datasets.Column(src: str, col: str, pgtype: str)`; `datasets.Dataset(name: str, path: Path, table: str, columns: tuple[Column, ...], read_kwargs: dict, header: bool = True)`; `datasets.DATASETS: dict[str, Dataset]`. The keys are `runways`, `runway_history`, `aircraft`, `gates`, `airports`, `airlines`, `disruptions_v1`, `openflights`, `waves_index`, `metar_2024`, `flights2015_index`. `read_kwargs` holds exactly today's `pd.read_csv` arguments for each file (e.g. `{"dtype": {"runway": str}}` for runways). The column lists and `pgtype`s follow design §6.1-§6.3. Column order = source header order.
  - `datasets.FLIGHTS2015_DAY_KWARGS` and `datasets.ARRIVALS_KWARGS`: today's `dtype` dicts from `flights2015._dest_frame` and `build._arrivals`.
  - `flights2015.flight_row(r: dict) -> dict`: the existing `FlightRow` formatting from `search` (shared later by PgStore).
  - `FileStore` implements the reference and wave methods of the protocol by moving today's code. Rules:
    - `frame(name)` is `pd.read_csv(ds.path, **ds.read_kwargs)` (None if the file is missing).
    - `rows(name)` is `list(csv.DictReader(...))`.
    - `arrivals_day` reads the per-airport file once (`lru_cache(4)`, as today) and returns the date's rows. `flights2015_day` does the same per destination (`lru_cache(2)`).
    - `wave_summary` returns `None` (FileStore summaries are computed by `build._summary`).
    - `metar_2024_rows(iata)` streams `metar.csv` and keeps the airport's rows.
    - `workbook_rows(sheet)` reads the xlsx with `openpyxl` (`min_row=4`, `values_only=True`).
  - Builders call `get_store()` at call time (never at import time), so `set_store` in tests takes effect. Caching stays where it is today (`_cached`, `lru_cache`).
  - Pre-flight rulings:
    - `FileStore.session()` returns `contextlib.nullcontext()` (Task 8 wraps builds in `get_store().session()`).
    - `rows(name)` passes `fieldnames=[c.src for c in ds.columns]` to `csv.DictReader` when `ds.header` is False (`openflights`).
    - Callers treat a `None` from `frame`/`rows` as today's missing-file branch (`_gates_table` → `{}`, `_runway_history` → `{}`). `scenario.disruptions()` raises `FileNotFoundError` when `frame("disruptions_v1")` is None: `disruptions.csv` is dockerignored and `server.config` relies on `except OSError`.
    - `arrivals_day`: the FileStore per-airport cache keeps today's categoricals (`_arrivals` dtypes plus the `category` casts; ATL is 61 MB as plain strings). It returns that date's slice (`None` if the airport file or the date is absent). `_window_rows` keeps its casts and returns its empty frame on `None`.
    - `wave_row`/`wave_rows` return `disrupted_flights` normalised as today's `_index` does (`\.0(?=;|$)` removed; all 36,550 rows hold `AA807.0` forms), and `q` matches the normalised text.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store_files.py
import pandas as pd
from backend import datastore
from backend.datastore.files import FileStore
from backend.datastore.datasets import DATASETS
from backend.wave import build, flights2015 as F

def test_frame_equals_direct_read():
    for name in ("runways", "aircraft", "airports", "waves_index"):
        ds = DATASETS[name]
        pd.testing.assert_frame_equal(FileStore().frame(name), pd.read_csv(ds.path, **ds.read_kwargs))
    # the registry must carry today's read arguments, not just any arguments
    assert DATASETS["runways"].read_kwargs == {"dtype": {"runway": str}}
    assert DATASETS["aircraft"].read_kwargs == {"dtype": str}

def test_arrivals_day_slice():
    df = FileStore().arrivals_day("ATL", "2024-07-15")
    assert len(df) > 500 and set(df["date"]) == {"2024-07-15"}
    assert FileStore().arrivals_day("ATL", "1999-01-01") is None

def test_flights2015_day_and_row():
    s = FileStore()
    r = s.flight2015_row(270287)
    assert r["dest"] == "ORD" and isinstance(r["sched_arr"], str)
    day = s.flights2015_day("ORD", r["arr_date"])
    assert 270287 in set(day["row_id"]) and set(day["arr_date"]) == {r["arr_date"]}

def test_search_matches_flight_row_shape():
    rows, total = FileStore().search_flights2015("ATL", "DL", 3, 0)
    assert total > 3 and len(rows) == 3 and all(r["dest"] == "ATL" for r in rows)
    assert set(rows[0]) == {"row_id", "wave_id", "date", "carrier", "flight", "origin", "dest", "sched_arr",
                            "arr_delay", "weather_delay", "late_aircraft_delay"}

def test_builders_go_through_the_store(monkeypatch):
    calls = []
    class Spy(FileStore):
        def arrivals_day(self, iata, date):
            calls.append((iata, date)); return super().arrivals_day(iata, date)
    datastore.set_store(Spy())
    build._arrivals_cache_clear()                     # see Step 3
    build.build_wave(build.list_waves(airport="ORD", limit=1)[0]["id"])
    datastore.set_store(None)
    assert calls and calls[0][0] == "ORD"
```

```python
# tests/test_no_db_in_hot_path.py
from backend import datastore
from backend.wave import build, graph

class Exploding:
    def __getattr__(self, name):
        raise AssertionError(f"store.{name} called in the hot path")

def test_negotiation_and_evaluate_never_touch_the_store():
    w = build.build_wave("F2015-270287")
    datastore.set_store(Exploding())
    try:
        res = graph.negotiate_wave(w, "formulas", ["formula"], lambda m: None, narrate=False)
        assert res["outcome"] in ("consensus", "fallback")
        from backend.wave import edit
        edit.check_edit(w, edit.plan_from_dicts(res["final_plan"]["assignments"]))
    finally:
        datastore.set_store(None)
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_store_files.py tests/test_no_db_in_hot_path.py -q`. Expected: FAIL (`ModuleNotFoundError: backend.datastore.datasets`).
- [ ] **Step 3: Implement.** Move the file reads into `FileStore` and the registry. `build._window_rows(iata, date, hour)` uses `get_store().arrivals_day(iata, date)` instead of `_arrivals(iata)` + groups. The rest of its body is unchanged: the categorical-to-object casts become no-ops on a plain-str frame. Add `build._arrivals_cache_clear()`, which clears the FileStore per-airport cache (used by tests only). `flights2015.build_from_row` uses `get_store().flights2015_day(dest, arr_date)` and keeps its `cancelled == 0` filter. `search` delegates to `get_store().search_flights2015`. Keep every public function signature unchanged.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: all pass, including `test_build.py`, `test_flights2015.py`, `test_api.py`, `test_graph.py` and `test_build_speed`. Then run `PYTHONPATH=. python3 scripts/make_samples.py` followed by `git diff --stat frontend/src/api/samples`. Expected: no changes except volatile ids/timestamps (`run_id`, `started_at`, `recorded_at`). If anything else changed, the refactor changed behaviour: fix it, do not commit samples. Then `git restore frontend/src/api/samples` so the tree is clean.
- [ ] **Step 5: Commit**

```bash
git add backend/datastore/datasets.py backend/datastore/files.py backend/wave/build.py backend/wave/flights2015.py backend/wave/constraints.py backend/scenario.py tests/test_store_files.py tests/test_no_db_in_hot_path.py
git commit -m "refactor(datastore): reference data read through FileStore; hot path guarded"
```

### Task 3: FileStore live state, replays, benchmark, IEM cache; server reads through the store

**Files:**
- Modify: `backend/datastore/files.py`, `backend/server.py` (`_save_run`, `get_run`, `decision`, `_benchmark`, `eval_results`, `_replay_summaries`, `negotiate_wave` replay load, `config.replay_available`), `backend/wave/replay.py` (`record`, `load`), `backend/wave/flights2015.py` (`_fetch_iem` disk cache), `backend/wave/build.py` (`_summary.replay_available`), `backend/wave/benchmark.py` (after writing `benchmark.json`, call `put_benchmark`), `tests/test_api.py` (fixture), `scripts/make_samples.py`
- Test: `tests/test_store_files.py` (append)

**Interfaces:**
- Consumes: `FileStore`, `FilePaths` (Task 1); `get_store`, `set_store`.
- Produces:
  - FileStore implements `save_run` (atomic tmp + `os.replace` into `runs_dir`, returns False on `OSError`), `get_run` (12-hex ids only), `list_runs` (scans `runs_dir`, newest by mtime, filtered by `wave_id`), `record_decision` (appends JSONL, returns True), `replay`/`replay_ids`/`put_replay` (`replay_dir/{wave_id}.json`), `benchmark_current`/`put_benchmark` (`benchmark_path`; `put_benchmark` with `current=True` writes it), `v1_eval(name)` (`eval/{name}.json`), and `iem_get`/`iem_put` (`iem_dir/{station}_{date}.csv`; `iem_get` returns `(file mtime, text)`; `iem_put(…, None)` writes nothing).
  - `server._save_run(result)` keeps the `RUNS` LRU insert synchronous. It then calls `get_store().save_run(result)` on a daemon thread (`threading.Thread(target=…, daemon=True).start()`), so `finish()` never waits on storage.
  - `server.decision` builds the row dict (`run_id` or `flight_id`/`plan_id`, `decision`, `note`, `edits`, `recorded_at`) and calls `get_store().record_decision(row)`. The v1 branch keeps its current validation.
  - The `server` constants `RUNS_DIR`, `DECISIONS`, `REPLAY_DIR`, `BENCHMARK` are removed; tests install `FileStore(FilePaths(...tmp...))` instead.
  - Pre-flight rulings:
    - `FileStore.list_runs` rows have Task 6's shape: `run_id, wave_id, mode, model, outcome`, `created_at` (file mtime, ISO UTC) and `decided` (the run_id appears in `decisions_path`). Task 10's test reads `decided`.
    - `FileStore.replay_ids()` returns `set()` when `replay_dir` is missing (`test_config_keys` expects `replay_available` False on a tmp store).
    - `replay.record` keeps its signature, writes the file as today, then calls `get_store().put_replay(wid, body)`. `replay.load(wave_id)` returns `get_store().replay(wave_id)` and raises `FileNotFoundError` on `None`, so the server's `except` is unchanged.
    - `benchmark.main` calls `put_benchmark(report, md, label="main", current=True)` when `out` resolves to the default `eval/`, else `put_benchmark(report, md, label=out.name, current=False)`. `FileStore.put_benchmark` with `current=True` writes `benchmark_path` as `json.dumps(report, indent=2)` (byte-identical to `benchmark.main`'s file); with `current=False` it does nothing.
    - `/api/config` must not query a database (design §2, §8.3, §11). `_benchmark()`, the replay check and `scenario.disruptions()` are store calls, so `config()` reads them through a helper `_file_view()`: `getattr(get_store(), "secondary", get_store())` (the store itself when it is a `FileStore`, re-read on each call as today; the file backend of the `FallbackStore` that Task 5 adds). Task 10 adds the server cache on top. Never construct a fresh `FileStore()` here (it would see `data/replay` and break `test_config_keys`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_store_files.py`)

```python
def test_runs_roundtrip_and_list(tmp_path):
    from backend.datastore.files import FilePaths
    s = FileStore(FilePaths(runs_dir=tmp_path / "runs"))
    assert s.save_run({"run_id": "abcdef012345", "wave_id": "W1", "outcome": "consensus"})
    assert s.get_run("abcdef012345")["wave_id"] == "W1"
    assert s.get_run("../etc/passwd") is None
    assert [r["run_id"] for r in s.list_runs("W1", 5)] == ["abcdef012345"]

def test_decision_appends_both_shapes(tmp_path):
    from backend.datastore.files import FilePaths
    p = tmp_path / "d.jsonl"
    s = FileStore(FilePaths(decisions_path=p))
    assert s.record_decision({"run_id": "abcdef012345", "decision": "APPROVE", "note": "", "edits": []})
    assert s.record_decision({"flight_id": 5, "plan_id": "R1", "decision": "OVERRIDE", "note": "x"})
    assert len(p.read_text().splitlines()) == 2

def test_replay_ids_are_the_demo_set():
    assert FileStore().replay_ids() == {"F2015-270287", "F2015-58926", "F2015-69315", "F2015-75", "F2015-77162"}

def test_iem_cache_roundtrip(tmp_path):
    from backend.datastore.files import FilePaths
    s = FileStore(FilePaths(iem_dir=tmp_path))
    assert s.iem_get("ORD", "2015-08-19") is None
    s.iem_put("ORD", "2015-08-19", "station,valid\nORD,2015-08-19 00:51\n")
    assert s.iem_get("ORD", "2015-08-19")[1].startswith("station,valid")
```

The `tests/test_api.py` fixture becomes:

```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend import datastore
    from backend.datastore.files import FileStore, FilePaths
    monkeypatch.setattr(server, "WB", stub)
    monkeypatch.setattr(server, "GR", stub)
    datastore.set_store(FileStore(FilePaths(runs_dir=tmp_path / "runs", decisions_path=tmp_path / "decisions.jsonl",
                                            replay_dir=tmp_path / "replay", benchmark_path=tmp_path / "benchmark.json")))
    monkeypatch.setattr(stub, "_REPLAY_DIR", tmp_path / "replay")
    monkeypatch.setattr(server, "REPLAY_SPACING_S", 0)
    server._RATE.clear(); server.RUNS.clear()
    yield TestClient(server.app)
    datastore.set_store(None)
```

Every other `server.RUNS_DIR` / `DECISIONS` / `REPLAY_DIR` / `BENCHMARK` reference in `tests/test_api.py` (`test_override_requires_note`, `test_replay_streams_saved_run`, `test_default_chain_uses_benchmark_winner`, `test_formula_winner_defaults_to_formulas_mode`) is changed to the store's paths (`datastore.get_store().paths.<field>`).

- [ ] **Step 2: Run** `python3 -m pytest tests/test_store_files.py tests/test_api.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. `scripts/make_samples.py` replaces its `RUNS_DIR` and `DECISIONS` patches with `datastore.set_store(FileStore(FilePaths(runs_dir=…tmp…, decisions_path=…tmp…)))`, keeps the `REPLAY_SPACING_S` patch, and replaces `server.BENCHMARK.exists()` with `datastore.get_store().benchmark_current() is not None`. `benchmark.main` keeps writing `eval/benchmark.json` and `BENCHMARK.md`, then calls `put_benchmark` as in the pre-flight rulings above.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: all pass. `PYTHONPATH=. python3 scripts/make_samples.py` then `git diff --stat frontend/src/api/samples`. Expected: only volatile ids/timestamps. Then `git restore frontend/src/api/samples`; also `git diff --stat eval/benchmark.json` must be empty.
- [ ] **Step 5: Commit**

```bash
git add backend/datastore/files.py backend/server.py backend/wave/replay.py backend/wave/flights2015.py backend/wave/build.py backend/wave/benchmark.py tests/test_api.py tests/test_store_files.py scripts/make_samples.py
git commit -m "refactor(datastore): runs, decisions, replays, benchmark and IEM cache through the store"
```

### Task 4: Migrations and the migration runner

**Files:**
- Create: `migrations/0001_schemas_meta.sql`, `migrations/0002_roles_grants.sql`, `migrations/0003_ref.sql`, `migrations/0004_live_eval.sql`, `migrations/CHECKSUMS`, `scripts/db_migrate.py`, `tests/test_migrations.py`

**Interfaces:**
- Produces:
  - The schema of design §6.1-§6.6 and §6.8 exactly (geo comes in Task 12). `0001` creates schemas `ref, geo, live, eval, raw, meta, extensions`, `create extension if not exists postgis with schema extensions`, and the `meta.*` tables. `0002`:
    - creates NOLOGIN roles `app_read` and `app_write` if they do not exist (`do $$ … $$`);
    - grants `usage` on `ref, geo, live, eval, extensions` to `app_read`, and `select` on all their tables;
    - grants `insert, update, delete` on `live` tables and `insert, update` on `eval` tables to `app_write`, plus `usage, select` on their sequences;
    - sets `alter default privileges for role current_user in schema …` to the same, so tables created later by the owner (including the seeder's `__new` tables) inherit them;
    - grants nothing on `raw` or `meta`.
  - `db_migrate.py`: `main(argv: list[str] | None = None) -> int`; `apply(conn, files: list[Path], dry_run: bool) -> list[str]` (versions applied); `checksums(dir: Path) -> dict[str, str]`. It reads `DATABASE_URL` (or `--url-env NAME`) and exits 2 with "DATABASE_URL not set" if missing. It never prints the URL. Each file runs in its own transaction and records `(version, name, sha256)` in `meta.schema_migrations`. If an applied version's sha256 differs, it exits 3 naming the file. It prints `applied: [...]` / `up to date`.
  - `migrations/CHECKSUMS`: `<sha256>  <filename>` lines, regenerated by `python3 scripts/db_migrate.py --write-checksums` (no DB needed).
  - Pre-flight rulings:
    - `checksums(dir)` returns `{filename: sha256}` (the lint test reverses each `sha  name` line).
    - The runner applies **every** file whose version is not in `meta.schema_migrations`, in sorted order, gaps allowed. Task 8 may add a `0006_…` before Task 12 adds `0005_geo.sql`.
    - `db_migrate.py` starts with `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` and `import backend` (loads `.env`), then reads `DATABASE_URL` (direct, owner).
    - Keep the spec's DDL form `create table ref.<t> (` (lowercase, one space); the lint regex depends on it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_migrations.py
import re
from pathlib import Path
import pytest
from scripts import db_migrate

M = Path(__file__).resolve().parents[1] / "migrations"

def test_names_sorted_and_patterned():
    files = sorted(p.name for p in M.glob("*.sql"))
    assert files[:4] == ["0001_schemas_meta.sql", "0002_roles_grants.sql", "0003_ref.sql", "0004_live_eval.sql"]
    assert all(re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", f) for f in files)

def test_checksums_match_committed_files():
    committed = dict(reversed(l.split("  ")) for l in (M / "CHECKSUMS").read_text().splitlines())
    assert committed == db_migrate.checksums(M)

def test_no_url_exits_2(monkeypatch, capsys):
    assert db_migrate.main([]) == 2 and "DATABASE_URL" in capsys.readouterr().err

def test_every_file_table_has_ord_and_pk():
    sql = (M / "0003_ref.sql").read_text()
    for t in ("flights2015", "waves", "metar_2024", "runways", "aircraft", "gates", "airports", "airlines"):
        block = re.search(rf"create table ref\.{t} \((.*?)\);", sql, re.S).group(1)
        assert "ord int4" in block and "primary key" in block

@pytest.mark.live_db
def test_migrate_twice_is_noop(live_db_url):
    assert db_migrate.main(["--url-env", "DATABASE_TEST_URL"]) == 0
    assert db_migrate.main(["--url-env", "DATABASE_TEST_URL"]) == 0   # prints "up to date"
```

`--url-env NAME` makes the script read `os.environ[NAME]`. The `live_db_url` fixture puts the saved test URL back into `os.environ["DATABASE_TEST_URL"]` for the test's duration (`monkeypatch.setenv`).

- [ ] **Step 2: Run** `python3 -m pytest tests/test_migrations.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the SQL (copy the DDL from design §6, adding `ord` where the design says so) and the runner. Add `scripts/__init__.py` (empty) if it does not exist, so tests can import `scripts.db_migrate`. Generate `CHECKSUMS`.
- [ ] **Step 4: Run** `python3 -m pytest tests/test_migrations.py -q`. Expected: PASS offline (the `live_db` test is SKIPPED without `DATABASE_TEST_URL`). If the user has set `DATABASE_TEST_URL`, `python3 -m pytest -q -m live_db tests/test_migrations.py` must PASS too.
- [ ] **Step 5: Commit**

```bash
git add migrations scripts/db_migrate.py scripts/__init__.py tests/test_migrations.py
git commit -m "feat(db): schema migrations (ref, live, eval, raw, meta; app roles) and runner"
```

## Milestone D1: Live state in Postgres

### Task 5: PgStore connection layer and FallbackStore

**Files:**
- Create: `backend/datastore/pg.py`, `backend/datastore/fallback.py`, `tests/test_fallback_store.py`
- Modify: `backend/datastore/__init__.py` (the pg branch)

**Interfaces:**
- Consumes: `Store`, `StoreStatus`, `FileStore`.
- Produces:
  - `pg.make_pool(url: str) -> NullConnectionPool`, with the Global Constraints settings (including `timeout=10`) and `open=False`.
  - `pg.with_retry(fn: Callable[[], T], *, delays=(0.5, 2.0), jitter=0.3, sleep=None) -> T`. It retries only on `psycopg.OperationalError` and re-raises after the last attempt. `sleep=None` resolves to `time.sleep` **at call time** (a `sleep=time.sleep` default is bound at definition, so the tests' `monkeypatch.setattr(pg.time, "sleep", …)` would not take effect and each test would really sleep 2.5 s per call).
  - `PgStore(url: str)`, with:
    - `open()`: opens the pool, non-blocking (`pool.open(wait=False)`);
    - `close()`;
    - `session()`: a context manager that binds one pooled connection to the current thread (`threading.local`), so nested store calls in one wave build share it;
    - `_conn()`: yields the bound connection or a fresh one. It opens the pool on first use if `open()` was not called (Task 8's parity helpers construct `PgStore(url)` without `open()`).
    - `session()` binds **lazily**: entering it never connects; the first `_conn()` inside it takes the connection, and exit returns it. A down database therefore surfaces as an `OperationalError` from a store call (which falls back), never from entering the session.
    Methods not yet implemented raise `NotImplementedError`.
  - `FallbackStore(primary: Store, secondary: FileStore, *, threshold=3, cooldown_s=60.0, clock=time.monotonic)`:
    - every protocol method calls `primary` through `with_retry`. On `OperationalError`, `PoolTimeout`, `DataUnavailable` or `NotImplementedError` it logs one line (method, error class, SQLSTATE if any; never the URL) and calls `secondary`;
    - after `threshold` consecutive failures the breaker opens, and calls go straight to `secondary` until `cooldown_s` passes (then one trial call);
    - `status()` is `StoreStatus("postgres", False, None, True)` when healthy. When the breaker is open or the last call fell back, it is `StoreStatus("files", True, "<method>: <error class>", False)`;
    - write methods (`save_run`, `record_decision`, `put_replay`, `put_benchmark`, `iem_put`) return the secondary's result when they fall back, and the status shows `persistent: False`;
    - `status()` never queries.
    - `session()` delegates to the primary's `session()` (lazy, see above); the secondary is exposed as the attribute `.secondary` (Tasks 3 and 10 read config values from it).
  - `get_store()` pg branch: `FallbackStore(PgStore(os.environ["DATABASE_URL_POOLED"]), FileStore())`, with `open()` called once.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fallback_store.py
import psycopg
import pytest
from backend.datastore.fallback import FallbackStore
from backend.datastore.files import FileStore
from backend.datastore import pg

class Down:
    calls = 0
    def wave_row(self, wave_id):
        Down.calls += 1
        raise psycopg.OperationalError("connection refused")
    def status(self): raise AssertionError("status must not query")

def test_fallback_serves_files_after_retries(monkeypatch):
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)
    Down.calls = 0
    fb = FallbackStore(Down(), FileStore())
    wid = FileStore().wave_rows(None, None, 1)[0]["wave_id"]
    assert fb.wave_row(wid)["wave_id"] == wid
    assert Down.calls == 3                                  # 1 try + 2 retries
    s = fb.status()
    assert (s.backend, s.degraded, s.persistent) == ("files", True, False) and "wave_row" in s.reason

def test_breaker_opens_and_half_opens(monkeypatch):
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)
    t = [0.0]
    Down.calls = 0
    fb = FallbackStore(Down(), FileStore(), clock=lambda: t[0])
    wid = FileStore().wave_rows(None, None, 1)[0]["wave_id"]
    for _ in range(3): fb.wave_row(wid)
    n = Down.calls; fb.wave_row(wid); assert Down.calls == n      # open: primary skipped
    t[0] += 61; fb.wave_row(wid); assert Down.calls > n           # half-open trial

def test_retry_jitter_bounds():
    slept = []
    with pytest.raises(psycopg.OperationalError):
        pg.with_retry(lambda: (_ for _ in ()).throw(psycopg.OperationalError("x")), sleep=slept.append)
    assert 0.5 <= slept[0] <= 0.65 and 2.0 <= slept[1] <= 2.6

def test_pool_settings(monkeypatch):
    seen = {}
    monkeypatch.setattr(pg, "NullConnectionPool", lambda *a, **k: seen.update(k) or object())
    pg.make_pool("postgresql://u:p@example-pooler.us-west-2.aws.neon.tech/db?sslmode=require")
    assert seen["max_size"] == 4 and seen["timeout"] == 10 and seen["open"] is False
    assert seen["kwargs"] == {"prepare_threshold": None, "connect_timeout": 10, "application_name": "final-approach"}
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_fallback_store.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified (`pg.time` is the module's `import time`; `pg` imports `NullConnectionPool` by name so the test can patch it). The pool object must be constructible without network access (`open=False`).
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit**

```bash
git add backend/datastore/pg.py backend/datastore/fallback.py backend/datastore/__init__.py tests/test_fallback_store.py
git commit -m "feat(datastore): PgStore connection layer (NullConnectionPool, retries) and FallbackStore with breaker"
```

### Task 6: PgStore live state, replays, benchmark, IEM cache

**Files:**
- Modify: `backend/datastore/pg.py`
- Create: `tests/test_pg_live.py` (all `live_db`)

**Interfaces:**
- Consumes: `PgStore` (Task 5); tables from `0004_live_eval.sql` (Task 4).
- Produces PgStore implementations:
  - `save_run(result)`: `insert … on conflict (run_id) do update set result = excluded.result`, with columns taken from the result (`wave_id`, `mode`, `model`, `outcome`; `engine_version` default `v2` unless the result has one). After it, the retention prune: delete unpinned runs beyond the newest 2,000, at most once per hour per process.
  - `get_run`: by PK.
  - `list_runs(wave_id, limit)`: returns `run_id, wave_id, mode, model, outcome, created_at` (ISO) and `decided` (exists in `live.decisions`), newest first, with `limit` clamped to 1..100.
  - `record_decision(row)`: inserts, and in the same transaction runs `update live.runs set pinned = true where run_id = %s` (0 rows is fine). Returns True.
  - `replay`, `replay_ids`, `put_replay(wave_id, body, engine_version)`: `replay_ids` returns ids for `engine_version = backend.wave.version.ENGINE_VERSION` if that module exists, else `'v2'`.
  - `benchmark_current`, `put_benchmark`: `put_benchmark` with `current=True` clears the previous `is_current` row in the same transaction.
  - `v1_eval`.
  - `iem_get` returns `(fetched_at.timestamp(), body)`. `iem_put` upserts; when the table exceeds 40 MB (`pg_total_relation_size('live.iem_cache')`, checked at most hourly) it deletes the oldest-fetched rows down to 30 MB.
  - All JSON goes through `psycopg.types.json.Jsonb`.
  - Pre-flight rulings:
    - Retention is testable: `PgStore.run_cap: int = 2000` and `_prune_runs(force: bool = False) -> int` (rows deleted; `force` skips the hourly throttle). `save_run` calls `_prune_runs()`.
    - Per-instance caches (design §8.2): `replay_ids()` and `benchmark_current()` are cached on the instance and refreshed by `put_replay`/`put_benchmark`.

- [ ] **Step 1: Write the failing tests** (`tests/test_pg_live.py`; the fixture deletes the test rows before and after each test and restores the benchmark row that was current, so the file is rerunnable and later tasks still find the seeded `main` report current; re-seeding would not help, the manifest hash makes it skip)

```python
import pytest
from backend.datastore.pg import PgStore
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
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_pg_live.py -q`. Expected offline: all SKIPPED. With `DATABASE_TEST_URL` set and Task 4 migrations applied to the test branch: FAIL (`NotImplementedError`).
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** `python3 -m pytest -q` (offline: PASS, `live_db` skipped). If `DATABASE_TEST_URL` is set: `python3 -m pytest -q -m live_db`, expected PASS. If it is not set, say so in the task report: the live check is then owed by the user.
- [ ] **Step 5: Commit**

```bash
git add backend/datastore/pg.py tests/test_pg_live.py
git commit -m "feat(datastore): PgStore runs, decisions, replays, benchmark and IEM cache"
```

### Task 7: Seeder framework and the live/eval datasets

**Files:**
- Create: `backend/datastore/pack.py`, `scripts/db_seed.py`, `tests/test_pack.py`, `tests/test_seed.py`

**Interfaces:**
- Consumes: `DATASETS` (Task 2), migrations (Task 4), `FilePaths` defaults.
- Produces:
  - `pack.source_sha256(paths: list[Path], script_version: str) -> str` (sorted paths, file bytes, version).
  - `pack.gzip_csv(df: pd.DataFrame) -> bytes`, with gzip `mtime=0` so the output is deterministic.
  - `pack.split_by(df: pd.DataFrame, keys: list[str]) -> Iterator[tuple[tuple, pd.DataFrame]]`.
  - `pack.like_escape(q: str) -> str`: escapes `\`, `%`, `_` with a backslash.
  - `db_seed.py`:
    - `main(argv) -> int`. Flags: `--check`, `--budget-mb 750`, `--drop raw`, `--url-env NAME` (default `DATABASE_URL`; exits 2 if unset).
    - `SCRIPT_VERSION = "1"` and a `SEEDERS: dict[str, Seeder]` registry, in the default order of design §9.
    - `Seeder` is a dataclass of `name`, `sources() -> list[Path]` and `load(conn) -> int` (rows).
    - `run(conn, names, check, budget_mb) -> int`: skips a dataset when the manifest hash matches. Otherwise it loads, upserts `meta.seed_manifest`, and checks `pg_database_size(current_database())` after each dataset. Above the budget it prints a per-table size report plus "over budget: drop raw.* first (db_seed.py --drop raw)" and returns 4.
    - `swap_load(conn, table: str, columns: list[str], rows_csv: Iterable[str], index_sql: list[str]) -> int`: the shadow-table load of design §9. It starts with `drop table if exists <table>__new` (a previous failed load may have left one), creates `<table>__new` with `like … including defaults including constraints including generated including storage`, COPYs, verifies the count, then in one transaction drops, renames, creates indexes, and returns the row count.
  - Datasets this task loads:
    - `replays`: `data/replay/*.json` → `eval.replays`, version `v2`.
    - `runs_import`: `eval/runs/*.json` → `live.runs`, `on conflict do nothing`, unpinned.
    - `benchmark`: `eval/benchmark.json` (current, label `main`, with `BENCHMARK.md`), `benchmark_100.json` (`100`, with `BENCHMARK_100.md`), and `BENCHMARK_1000.md` alone (label `1000`, report `{}`).
    - `v1_eval`: `results.json`, `rows.json`.
    - `iem_cache`: `data/challenge2/metar_cache/*.csv` → `live.iem_cache`, `fetched_at` = file mtime, `on conflict do nothing`.
  - Pre-flight rulings:
    - `SEEDERS` holds all 13 names of design §9 in order from this task on (`small, workbook, waves, bts_arrivals, metar_2024, iem_cache, flights2015, disruptions_v1, replays, runs_import, benchmark, v1_eval, raw`). Those owned by Task 8 (`small … flights2015`, `disruptions_v1`) and Task 9 (`raw`) are placeholders whose `load` raises `NotImplementedError("lands in Task 8/9")`; `iem_cache` is real here.
    - `swap_load`: `like … including constraints` does not copy primary keys or unique constraints (they are indexes), so `index_sql` must recreate them (`alter table <t>__new add primary key (…)`, `… add unique (…)`) along with the indexes.
    - `db_seed.py` starts with `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` and `import backend` (loads `.env`), then reads `DATABASE_URL` (or `--url-env`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pack.py
import gzip, io
import pandas as pd
from backend.datastore import pack

def test_gzip_is_deterministic_and_roundtrips():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", None]})
    b1, b2 = pack.gzip_csv(df), pack.gzip_csv(df)
    assert b1 == b2
    pd.testing.assert_frame_equal(pd.read_csv(io.BytesIO(gzip.decompress(b1))), df)

def test_like_escape():
    assert pack.like_escape("50%_a\\b") == "50\\%\\_a\\\\b"

def test_hash_changes_with_content(tmp_path):
    p = tmp_path / "x.csv"; p.write_text("a\n1\n")
    h1 = pack.source_sha256([p], "1")
    assert h1 != pack.source_sha256([p], "2")          # script version change
    p.write_text("a\n2\n")
    assert h1 != pack.source_sha256([p], "1")          # content change
```

```python
# tests/test_seed.py
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
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_pack.py tests/test_seed.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. `_copy_rows(cur, table, columns, rows)` wraps `cur.copy(f"copy {table} ({cols}) from stdin with (format csv)")`. Identifiers come only from the registry.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS, `live_db` skipped. With `DATABASE_TEST_URL`: `python3 -m pytest -q -m live_db tests/test_seed.py`, expected PASS (the failed-load test is skipped until Task 8).
- [ ] **Step 5: Commit**

```bash
git add backend/datastore/pack.py scripts/db_seed.py tests/test_pack.py tests/test_seed.py
git commit -m "feat(db): idempotent seeder with shadow-table swap, size guard; replays, runs, benchmark, IEM cache"
```

## Milestone D2-D3: Reference data and raw tables

### Task 8: Reference datasets in Postgres, PgStore reads, parity

**Files:**
- Modify: `scripts/db_seed.py` (seeders `small`, `workbook`, `waves`, `bts_arrivals`, `metar_2024`, `flights2015`, `disruptions_v1`), `backend/datastore/pg.py` (reference and wave methods), `backend/wave/build.py` (`session()` wrapping, `_reset_caches`, summaries from `wave_rows`), `backend/wave/flights2015.py` (`session()` wrapping), `tests/test_seed.py` (remove the Task 7 skip; add `test_grants_survive_reseed`)
- Create: `tests/test_parity.py` (all `live_db`)

**Interfaces:**
- Consumes: `DATASETS`, `swap_load`, `pack.*`, `flights2015.flight_row`, `build._summary`.
- Produces:
  - Seeders:
    - `small`: runways, runway_history, aircraft, gates, airports, airlines and openflights, with `ord`; runway `geog` columns are filled with `ST_MakePoint` after load.
    - `workbook`: rows of `airport_capacity` and `wake_separation`, `min_row=4`.
    - `waves`: the index with `summary` = `build._summary(row)` minus `replay_available`, computed with a `FileStore`.
    - `bts_arrivals`: one `pack.gzip_csv` per `(airport, date)` from `data/waves/arrivals/*.csv.gz`, columns exactly as the file minus nothing.
    - `metar_2024`, `flights2015` (merge of `flights_index.csv` and `flights/{DEST}.csv.gz` on `row_id`; `ord` = the index file row order), `disruptions_v1`.
  - PgStore methods:
    - `frame(name)`: `copy (select <src-aliased columns> from <table> order by ord) to stdout with (format csv, header true)` → `pd.read_csv(StringIO, **ds.read_kwargs)`;
    - `rows(name)`: the same through `csv.DictReader`;
    - `workbook_rows`;
    - `wave_row`, `wave_rows` (filters as `build.list_waves`: airport equality; `q` = `upper(wave_id) like` or `upper(disrupted_flights) like`, escaped), `wave_count`;
    - `wave_summary` (jsonb plus `replay_available` from `replay_ids()`);
    - `arrivals_day` (gunzip → `pd.read_csv(**ARRIVALS_KWARGS)`);
    - `metar_2024_rows`, `flight2015_row` (values as strings, like `_locate`), `flights2015_day`;
    - `search_flights2015`: `supported` = dest in `ref.runways`; `q` matches callsign/tail/origin/dest by `ilike '%'||%s||'%' escape '\'`, OR'd with the `row_id::text like` digit rule, `order by ord`, `count(*) over ()`, rows passed through `flight_row`.
  - Every wave-build path wraps its store calls in `get_store().session()` (in `build.build_wave` and `flights2015.build_from_row`).
  - Pre-flight rulings:
    - `/api/waves` budget is one indexed query (design §10.1), so `PgStore.wave_rows` rows carry a `summary` key (the jsonb). `build.list_waves`/`random_wave` use `row["summary"] | {"replay_available": wid in replay_ids}` when present and `_summary(row)` otherwise (FileStore). `wave_summary` stays for single lookups.
    - `wave_row`/`wave_rows` return `disrupted_flights` normalised like FileStore (Task 2) and match `q` against `regexp_replace(disrupted_flights, '\.0(;|$)', '\1', 'g')`; the column itself stays as in the file.
    - Per-instance caches of design §8.2: `arrivals_day` LRU 16, `flights2015_day` LRU 64, `frame`/`rows`/`workbook_rows` per name. Parity builds a fresh `PgStore` per run, so they start empty.

- [ ] **Step 1: Write the failing tests** (`tests/test_parity.py`)

```python
import json
import pytest
from backend import datastore
from backend.datastore.files import FileStore
from backend.datastore.pg import PgStore
from backend.wave import build
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
    from backend.wave import flights2015 as F
    a, b = _both(live_db_url, lambda: F.search(**{"limit": 20, **args}))
    assert a == b
    a, b = _both(live_db_url, lambda: build.list_waves(airport=args.get("airport"), q=args.get("q"), limit=20))
    assert a == b

def test_like_escape_percent_matches_nothing(live_db_url):
    datastore.set_store(PgStore(live_db_url))
    try:
        from backend.wave import flights2015 as F
        assert F.search(q="%", limit=5) == ([], 0)
    finally:
        datastore.set_store(None)

def test_parity_benchmark_and_imported_run(live_db_url):      # design §13
    fs, ps = FileStore(), PgStore(live_db_url)
    assert ps.benchmark_current() == fs.benchmark_current()
    rid = sorted(p.stem for p in fs.paths.runs_dir.glob("*.json"))[0]   # an eval/runs file, imported by runs_import
    assert ps.get_run(rid) == fs.get_run(rid)
```

```python
# tests/test_seed.py (append)
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
```

`build._reset_caches()` (new, tests only) clears every `_cached` and `lru_cache` reader in `build`, `flights2015`, `constraints` and `scenario`.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_parity.py -q`. Expected offline: SKIPPED. With `DATABASE_TEST_URL` after `db_seed.py --url-env DATABASE_TEST_URL small workbook waves bts_arrivals metar_2024 iem_cache flights2015 disruptions_v1 replays benchmark runs_import`: FAIL (`NotImplementedError`). (`iem_cache` stops F2015 parity builds from calling IEM over the network; `replays` makes `replay_available` match.)
- [ ] **Step 3: Implement** as specified. If a parity test fails on a numeric column printed differently (e.g. `7599.0` vs `7599`), change that column to `text` in `0003_ref.sql` via a **new** migration (`0006_…`; the runner applies it although `0005_geo.sql` arrives later in Task 12), never by editing an applied file (design §6.3 "Exact round trips").
- [ ] **Step 4: Run** `python3 -m pytest -q` (offline PASS). With `DATABASE_TEST_URL`: `python3 -m pytest -q -m live_db`, expected PASS. Then `python3 scripts/db_seed.py --url-env DATABASE_TEST_URL --check small workbook waves bts_arrivals metar_2024 flights2015 disruptions_v1 replays benchmark`, expected exit 0 (`runs_import` and `iem_cache` are left out: their sources, `eval/runs` and `metar_cache`, change with every local run, and `raw` lands in Task 9). Record `pg_database_size` in the commit message.
- [ ] **Step 5: Commit**

```bash
git add scripts/db_seed.py backend/datastore/pg.py backend/wave/build.py backend/wave/flights2015.py tests/test_parity.py tests/test_seed.py
git commit -m "feat(db): reference data in Postgres (flights2015, waves, BTS day blobs, METAR, refs) with parity tests"
```

### Task 9: Raw challenge tables

**Files:**
- Modify: `scripts/db_seed.py` (seeder `raw`, `--drop raw`), `backend/datastore/pack.py` (`infer_pg_types`), `tests/test_pack.py`, `tests/test_seed.py`

**Interfaces:**
- Consumes: `swap_load`, size guard (Task 7).
- Produces:
  - `pack.pg_ident(header: str) -> str`: lower-case; each run of non-`[a-z0-9]` characters → one `_` (so `"Wind Speed (km/h)"` → `wind_speed_km_h_`); leading digit → prefix `c_`; deduplicated with `_2`, `_3`.
  - `pack.infer_pg_types(df: pd.DataFrame) -> dict[str, str]`: integer-valued numeric columns → the narrowest of `int2` (−32768..32767), `int4`, `int8`; other numeric → `float8`; all-null → `text`; everything else → `text`.
  - Seeder `raw` creates and loads, in this order (each skipped with a printed note if its file is absent):
    - `raw.ch3_airports_dat` (`data/challenge3/airports.dat`, no header → `c1..c14`);
    - `raw.ch3_kalimantan_locations`;
    - `raw.ch4_ad_nprm__<sheet>` per sheet of `data/challenge4/ad_nprm.xlsx`;
    - `raw.ch2_flights` (`data/challenge2/flights.csv`);
    - `raw.ch5_flights_weather`;
    - `raw.ch1_train`, `raw.ch1_test`.
    Each gets `ord int4 primary key` and the inferred columns. Files are read in 50,000-row chunks, with types inferred over the whole file first in a streaming pass. The size guard runs after each table, and the seeder stops at the first table over budget.
  - `--drop raw`: drops every table in schema `raw` and deletes their manifest rows.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pack.py (append)
def test_infer_types_narrowest():
    df = pd.DataFrame({"a": [1, 2], "b": [1, 70000], "c": [1.5, None], "d": [None, None], "e": ["x", "y"],
                       "f": [1, 2**40]})
    assert pack.infer_pg_types(df) == {"a": "int2", "b": "int4", "c": "float8", "d": "text", "e": "text", "f": "int8"}

def test_pg_ident():
    assert [pack.pg_ident(h) for h in ["FL_DATE", "Wind Speed (km/h)", "1st"]] == ["fl_date", "wind_speed_km_h_", "c_1st"]
```

```python
# tests/test_seed.py (append)
@pytest.mark.live_db
def test_raw_loads_last_and_drops(live_db_url):
    import psycopg
    assert db_seed.main(["--url-env", "DATABASE_TEST_URL", "--budget-mb", "1", "raw"]) == 4   # stops after the first table (cheap; no 150 MB of ch2/ch5 WAL per run)
    with psycopg.connect(live_db_url) as c:
        n = c.execute("select count(*) from raw.ch3_airports_dat").fetchone()[0]
        assert n > 7000
    assert db_seed.main(["--url-env", "DATABASE_TEST_URL", "--drop", "raw"]) == 0
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_pack.py tests/test_seed.py -q`. Expected: FAIL on the two new offline tests.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS offline. With `DATABASE_TEST_URL`: `python3 -m pytest -q -m live_db tests/test_seed.py`, expected PASS. Record the per-table sizes printed by the seeder in the commit message. If `pg_database_size` exceeds 750 MB, **stop and report** to the user (the design ruling says raw goes first).
- [ ] **Step 5: Commit**

```bash
git add scripts/db_seed.py backend/datastore/pack.py tests/test_pack.py tests/test_seed.py
git commit -m "feat(db): raw challenge datasets as typed tables (loaded last, dropped first)"
```

## Milestone D1 completion: API and deploy

### Task 10: Contract v2.1.1, lifespan, config status

**Files:**
- Modify: `backend/server.py` (lifespan replaces `@app.on_event("startup")`; `config()` adds `store`; `decision()` adds `persisted`; new `GET /api/runs` list route; `V2_PREFIXES` gains `/api/runs`), `docs/api-contract.md`, `frontend/src/api/types.ts`, `frontend/src/api/samples/config.json`, `frontend/src/api/samples/decision.json`, `tests/conftest.py` (gains the `api_client` fixture), `tests/test_api.py` (its `client` fixture becomes an alias: `@pytest.fixture` `def client(api_client): return api_client`)
- Create: `tests/test_runs_api.py`

**Interfaces:**
- Consumes: `get_store().status()`, `list_runs`, `record_decision` (Tasks 3, 5, 6).
- Produces:
  - `GET /api/config` adds `"store": StoreStatus.to_dict()` (`backend`, `degraded`, `reason`, `persistent`). Unless `get_store()` is a `FileStore`, it calls no store method except `status()` (design §2, §8.3, §11; the Render health check must never wake Neon). Its three store-derived values (`replay_available`, the benchmark behind `_models`/`_default_mode`/`default_model`, `n_disruptions`) come from a server cache keyed by the store instance. That cache is filled by handlers that already read the store (`/api/waves` and `_replay_summaries` → replay ids; `/api/benchmark` and `_default_chain` in negotiate → benchmark; `/api/disruptions` → count) and refreshed after `put_replay`/`put_benchmark`. Until it is filled, the values come from Task 3's `_file_view()` (the FallbackStore's `.secondary`), never from a fresh `FileStore()`. On a `FileStore` the values are re-read on each call, as today (`test_config_keys` and `test_default_chain_uses_benchmark_winner` depend on it).
  - `POST /api/decision` v2 branch returns `{"ok": true, "recorded_at": …, "persisted": <bool>}`; `persisted` is `record_decision(...) and get_store().status().persistent`. The v1 branch (`flight_id`) is unchanged and still returns exactly `{"ok": true}` (`test_v1_decision_still_works`; design §11 adds `persisted` to the v2 response only).
  - `GET /api/runs?wave_id=&limit=20` → `{"runs": [...]}`. 422 `invalid_request` if `wave_id` is missing; `limit` is clamped to 1..100.
  - The lifespan opens the store (`get_store()`, which opens the pool without waiting). It starts today's warm thread only when the store is a `FileStore`: under Postgres the warmer would query at startup, and design §8.3 says no query runs at startup unless a request needs one. Shutdown closes the PgStore pool if present.
  - Contract text: a v2.1.1 line in the status paragraph, a `Config.store` interface, `persisted?` on the decision response, and the endpoint row and type for `GET /api/runs`, exactly as in design §11.

- [ ] **Step 1: Write the failing tests** (`tests/test_runs_api.py`, using the same store-backed client fixture as `tests/test_api.py`; move that fixture into `tests/conftest.py` as `api_client` so both files share it)

```python
import psycopg
from backend import datastore
from backend.datastore import pg
from backend.datastore.fallback import FallbackStore

class Exploding:                                     # any primary call fails the test
    def __getattr__(self, name):
        raise AssertionError(f"config called primary.{name}")

class Down:                                          # a database that is asleep or out of compute hours
    def __getattr__(self, name):
        def fail(*a, **k): raise psycopg.OperationalError("connection refused")
        return fail

def test_config_files_store(api_client):
    c = api_client.get("/api/config").json()
    assert c["store"] == {"backend": "files", "degraded": False, "reason": None, "persistent": False}

def test_config_never_queries_the_database(api_client):
    datastore.set_store(FallbackStore(Exploding(), datastore.get_store()))
    c = api_client.get("/api/config").json()
    assert c["store"] == {"backend": "postgres", "degraded": False, "reason": None, "persistent": True}

def test_down_db_reports_degraded_and_not_persisted(api_client, monkeypatch):   # design §13
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)
    datastore.set_store(FallbackStore(Down(), datastore.get_store()))
    d = api_client.post("/api/decision", json={"run_id": "0123456789ab", "decision": "APPROVE"}).json()
    assert d["ok"] is True and d["persisted"] is False
    s = api_client.get("/api/config").json()["store"]
    assert s["backend"] == "files" and s["degraded"] is True and s["persistent"] is False

def test_decision_reports_persisted(api_client):
    r = api_client.post("/api/negotiate-wave", json={"wave_id": "ATL-2024-07-15-16", "mode": "formulas", "stream": False}).json()
    d = api_client.post("/api/decision", json={"run_id": r["run_id"], "decision": "APPROVE"}).json()
    assert d["ok"] is True and d["persisted"] is False      # FileStore is not persistent on Render's disk

def test_runs_list_for_wave(api_client):
    r = api_client.post("/api/negotiate-wave", json={"wave_id": "ATL-2024-07-15-16", "mode": "formulas", "stream": False}).json()
    import time
    for _ in range(40):                                       # background save thread: poll up to 2 s
        runs = api_client.get("/api/runs?wave_id=ATL-2024-07-15-16&limit=5").json()["runs"]
        if runs: break
        time.sleep(0.05)
    assert runs[0]["run_id"] == r["run_id"] and runs[0]["decided"] is False

def test_runs_list_requires_wave_id(api_client):
    r = api_client.get("/api/runs")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_runs_api.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the server changes and the contract text and types. Then run `PYTHONPATH=. python3 scripts/make_samples.py` to regenerate `config.json` and `decision.json`, and `git restore` every other file under `frontend/src/api/samples/` (only volatile ids changed there).
- [ ] **Step 4: Run** `python3 -m pytest -q` and `cd frontend && npx tsc --noEmit && npm run build`. Expected: all pass, exit 0.
- [ ] **Step 5: Commit** (contract commit)

```bash
git add backend/server.py tests/test_runs_api.py tests/conftest.py tests/test_api.py docs/api-contract.md frontend/src/api/types.ts frontend/src/api/samples/config.json frontend/src/api/samples/decision.json
git commit -m "contract: v2.1.1 — Config.store, decision persisted flag, GET /api/runs?wave_id="
```

### Task 11: Deploy wiring, operator scripts, README

**Files:**
- Modify: `render.yaml` (add `- key: DATABASE_URL_POOLED` / `sync: false`), `README.md` (section "Data: Postgres (Neon) with file fallback")
- Create: `scripts/db_dump_live.py`, `scripts/db_timings.py`

**Interfaces:**
- Consumes: `PgStore`, `db_migrate`, `db_seed`.
- Produces:
  - `db_dump_live.py [--out DIR] [--url-env NAME]` writes `live_runs.jsonl` and `live_decisions.jsonl` (default `--out .cache/dump/`, which is gitignored). It never prints the URL.
  - `db_timings.py [--n 20] [--url-env DATABASE_URL_POOLED]` installs `FallbackStore(PgStore)` and times `build_wave` (cold and warm) for the 5 replay waves plus `list_waves`, `search` and `get_run`. It prints p50/p95 per path against the design §10 budgets, plus one "first call after idle" sample if run with `--after-idle` (it sleeps 6 minutes first).
  - The README section contains:
    - the env vars table (Global Constraints);
    - the Neon setup: region `aws-us-west-2`, compute fixed at 0.25 CU, the `test` branch;
    - an **optional, later** step (coordinator ruling: `atm_app` is deferred): the role snippet from design §12, verbatim, with `<password>` as a placeholder, then "point `DATABASE_URL_POOLED` (in `.env` and Render) at the pooler URL for `atm_app`". Until then `DATABASE_URL_POOLED` uses the owner role, and nothing depends on `atm_app`;
    - the commands `python3 scripts/db_migrate.py`, `python3 scripts/db_seed.py`, `python3 scripts/db_seed.py --check`;
    - the fallback behaviour and how to read `/api/config`'s `store`;
    - the CU-hour advice (no keep-alive).
  - Both scripts start with `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` and `import backend` (loads `.env`). `db_dump_live.py` defaults to `--url-env DATABASE_URL` (direct, dev machine).

- [ ] **Step 1: Write the failing test** (append to `tests/test_migrations.py`)

```python
def test_render_has_only_database_url_secret():
    import yaml
    from pathlib import Path
    env = {e["key"] for e in yaml.safe_load((Path(__file__).resolve().parents[1] / "render.yaml").read_text())["services"][0]["envVars"]}
    assert "DATABASE_URL_POOLED" in env and "DATABASE_URL" not in env and not any("SUPABASE" in k for k in env)
```

(PyYAML 6.0.2 is installed locally but is not in `requirements.txt`: if it is missing, parse with a regex over `- key:` lines instead; do not add a dependency.)

- [ ] **Step 2: Run** `python3 -m pytest tests/test_migrations.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the files above.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS. `docker build` is not available on this machine; note that in the task report.
- [ ] **Step 5: Commit**

```bash
git add render.yaml README.md scripts/db_dump_live.py scripts/db_timings.py tests/test_migrations.py
git commit -m "chore(deploy): DATABASE_URL_POOLED on Render; dump and timing scripts; README data section"
```

**After Task 11, user-run deploy check (not an agent step):** set `DATABASE_URL_POOLED` in Render. Redeploy, run one negotiation and approve it, redeploy again, and confirm that `GET /api/runs/<id>` still returns the run and `/api/config` shows `"backend": "postgres"`. Then run `python3 scripts/db_timings.py` from the dev machine.

## Milestone D4: Geo-ready

### Task 12: Geometry tables and store methods (empty until the flight-path M0)

**Files:**
- Create: `migrations/0005_geo.sql`
- Modify: `migrations/CHECKSUMS`, `backend/datastore/files.py`, `backend/datastore/pg.py`, `scripts/db_seed.py` (seeders `procedures`, `surface`, `movements2015`, `coverage`), `tests/test_store_files.py`, `tests/test_migrations.py`

**Interfaces:**
- Consumes: design §6.7; `swap_load`, `pack.gzip_csv`, `pack.split_by`.
- Produces:
  - `0005_geo.sql`: the `geo.*` tables of design §6.7 exactly, plus grants via default privileges (Task 4).
  - FileStore methods:
    - `procedures(icao)` → bytes of `data/ref/procedures/{ICAO}.json.gz` or None;
    - `surface(icao)` → `data/ref/surface/{ICAO}.json.gz`;
    - `movements_day(iata, date)` → `pack.gzip_csv` of that date's rows from `data/ref/movements2015/{IATA}.csv.gz` (per-airport file cached `lru_cache(4)`), or None.
  - PgStore methods: the same, by PK from `geo.*`.
  - Seeders (each returns 0 rows and prints "no files yet" when its directory is absent):
    - `procedures`: doc blob plus `geo.fixes` from the decoded `fixes`;
    - `surface`: doc blob plus `geo.stands`, `geo.osm_gates`, `geo.holding_positions`;
    - `movements2015`: per airport-day blobs;
    - `coverage`: from `data/ref/GEO_COVERAGE.md`'s source, `scripts.geo_coverage.coverage()`, if importable.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store_files.py (append)
def test_geo_methods_none_before_m0():
    s = FileStore()
    assert s.procedures("KXXX") is None and s.surface("KXXX") is None and s.movements_day("XXX", "2015-01-01") is None

# tests/test_migrations.py (append)
def test_geo_migration_tables():
    sql = (M / "0005_geo.sql").read_text()
    for t in ("procedures", "fixes", "surface", "stands", "osm_gates", "holding_positions", "movements2015_day", "coverage"):
        assert f"create table geo.{t}" in sql
    assert "using gist" in sql
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_store_files.py tests/test_migrations.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified, and regenerate `CHECKSUMS`.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS. With `DATABASE_TEST_URL`: `python3 scripts/db_migrate.py --url-env DATABASE_TEST_URL` applies `0005`, and `python3 scripts/db_seed.py --url-env DATABASE_TEST_URL procedures surface movements2015` prints "no files yet" three times and exits 0.
- [ ] **Step 5: Commit**

```bash
git add migrations/0005_geo.sql migrations/CHECKSUMS backend/datastore/files.py backend/datastore/pg.py scripts/db_seed.py tests/test_store_files.py tests/test_migrations.py
git commit -m "feat(db): geo schema and store methods, ready for flight-path M0"
```

### Task 13: Apply design §15 to the flight-path plan

**Files:**
- Modify: `docs/superpowers/plans/2026-11-02-flight-paths-and-taxi.md` (Global Constraints; Tasks 1, 3, 5, 6, 7, 8, 16, 18, 19, 35)

**Precondition:** `git status --short docs/superpowers/plans/2026-11-02-flight-paths-and-taxi.md` must be empty. On 2 Nov 2026 that file had uncommitted edits by someone else. If it is still modified, **stop and ask the user** whether those edits should be committed first; do not stage someone else's changes.

- [ ] **Step 1: Edit** the plan per the table in design §15. Each changed task gains:
  - one interface line (`backend/geo/store.py` loaders delegate to `get_store().procedures/surface/movements_day` and gunzip/parse);
  - one step running `python3 scripts/db_seed.py <dataset>` (skip when `DATABASE_URL` is unset);
  - its `live_db` test from §15, written in the plan's own test style.

  Global Constraints gains the line from §15. T8 gains the three verification steps from §15.

  Pre-flight rulings when copying §15: write the plan's method names, not the design's (`get_store().rows("openflights")` where §15 T1 says `get_store().csv("openflights")`; the Store protocol has no `csv`). Apply the env-var ruling of Global Constraints: §15's `DATABASE_OWNER_URL` becomes `DATABASE_URL`, and T8's "`docker run` with and without `DATABASE_URL`" becomes `DATABASE_URL_POOLED`. (On 3 Nov 2026 the precondition held: the file was clean.)
- [ ] **Step 2: Verify** with `grep -n "db_seed.py\|backend.datastore\|live_db" docs/superpowers/plans/2026-11-02-flight-paths-and-taxi.md`. Expected: hits in Global Constraints and in T1, T3, T5, T6, T7, T8, T16, T18 and T19.
- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-11-02-flight-paths-and-taxi.md
git commit -m "docs: flight-path plan reads and seeds geometry through the Postgres datastore"
```

---

## Self-review (applied)

- **Spec coverage:**
  - §5 architecture: T1, T5.
  - §6 schema: T4 (ref/live/eval/raw/meta), T12 (geo).
  - §6.3 exact round trips: T8 step 3.
  - §6.4 retention, pinning, no FK, background save: T3, T6.
  - §6.6 raw: T9.
  - §7 guard: T7, T9.
  - §8.1 selection: T1.
  - §8.2 methods: T2, T3, T6, T8, T12.
  - §8.3 pool and retries: T5.
  - §9: T4, T7, T8.
  - §10 budgets: T11 `db_timings.py` (measured, not asserted).
  - §11 contract: T10.
  - §12 roles and grants: T4, README in T11.
  - §13 tests: T1-T10.
  - §15 flight-path changes: T13.
  - §16 milestones: headings.
  - §19 user actions: listed in the handoff and in the README.
- **Gaps accepted:** `pg_trgm` (a lever, not built); the group roles' grants are tested in Task 8 (`test_grants_survive_reseed`); only the `atm_app` login itself (deferred, optional README step in Task 11) is a manual check.
- **Type consistency:** `StoreStatus` fields; `save_run -> bool`; `record_decision -> bool`; `iem_get -> tuple[float, str | None] | None`; `movements_day -> bytes | None`. These are used identically in T3, T5, T6, T10 and T12.
- **Proportion:** the code shown is tests and signatures; bodies are left to the implementer except the SQL, which is in the spec.
