# Final Approach data in Postgres (Neon): design

Status: draft for user review, 2 Nov 2026 (revised the same day: Neon Free replaces Supabase). Branch
`redesign/console`. No product code yet. Implementation plan: `docs/superpowers/plans/2026-11-02-postgres-data.md`.
Builds on: `docs/superpowers/specs/2026-10-31-disruption-negotiator-design.md` (v2 design),
`docs/api-contract.md` (v2.1), `docs/superpowers/specs/2026-11-02-flight-paths-and-taxi-design.md` and its plan
`docs/superpowers/plans/2026-11-02-flight-paths-and-taxi.md` (35 tasks, not started).

User decisions (binding):
- Move **all** app data into Postgres: live state, reference data (2015 BTS flights, 2024 BTS waves, runways,
  METAR/wind, gates, replays, benchmark results, decisions and edits, recorded runs) and the coming geometry
  (FAA CIFP procedures, OSM taxiway graphs, 2015 movements).
- Host: **Neon, Free plan**.
- Layout (coordinator ruling): §7 layout B, **plus the raw challenge datasets as plain tables**. The seed stops
  and reports above 750 MB; the raw tables are the first to drop.
- This work happens **before** the flight-path build. §15 says how that plan's M0 data tasks change.

Every decision this doc makes on its own is marked **(our call)**. The design is provider-neutral Postgres 16+;
Neon-specific behaviour (pooler, scale-to-zero, roles, region) is confined to §4, §8.3 and §16.

## 1. Intent

Today all data is files. The reference data is baked into the Docker image. Live state (runs, duty-manager
decisions, the on-demand METAR cache) goes to the Render container's ephemeral disk, so **every restart or
redeploy loses it**. A judge who approves a plan, or shares a run link, loses that record at the next deploy.

After this change:
- Postgres is the runtime source for every dataset the app reads or writes.
- Live state survives restarts. Run links (`/api/runs/{id}`) become shareable for real.
- The 512 MB Render instance stops holding whole-airport pandas frames: it fetches the slice a wave needs.
- The geometry the flight-path build adds goes into the same database, with PostGIS where it helps.

The negotiation engine does not change. A `Wave` is built once from the store. After that, every rule check,
move and utility runs in memory, exactly as today (§10).

## 2. Goals, non-goals, anti-goals

**Goals**
- G1. One data access module (`backend/datastore/`) is the only code that knows where data lives.
- G2. Postgres holds every dataset in §3 (including the raw challenge files) behind versioned, idempotent
  migrations and seed scripts.
- G3. Runs, decisions, edits, replays, benchmark reports and the IEM METAR cache persist across restarts.
- G4. API behaviour is unchanged (contract v2.1). The only changes are additive and announced with `contract:` (§11).
- G5. Offline still works: `?mock=1`, the bundled samples and `python3 -m pytest -q` (130 tests) need no network.
- G6. It fits the Neon Free plan (1 GB storage, 100 CU-hours a month), and the size is measured, not assumed.
- G7. Every fallback (Postgres → files → error) is logged and visible in `/api/config`.
- G8. The app tolerates a database that is asleep (scale-to-zero) or out of compute hours.

**Non-goals**
- Database-side auth, user accounts, Neon's Data API, a REST layer over the database.
- Moving the negotiation or safety rules into SQL. Rules stay deterministic Python.
- Multi-instance scale-out. One Render instance; the rate limiter stays in memory.
- A local Postgres for tests (no Docker or Postgres on the build machine, verified).

**Anti-goals (failure even if it "works")**
- Any database call inside `rules.check`, `utility.evaluate`, move generation or a vote. A negotiation calls
  `rules.check` 183-273 times (flight-path design §3); none of these calls may touch the network.
- A silent fallback: serving file data while claiming Postgres, or dropping a decision without saying so.
- Any database URL in the frontend bundle, in logs or in a committed file.
- A wave that builds differently from Postgres than from files. Parity is tested (§13).
- Changed provenance. Postgres is storage, not a source: provenance strings stay "BTS On-Time 2024-07",
  "IEM METAR", "OurAirports", and so on.
- Anything that wakes the database on a timer (health checks, keep-alives, background pruning). This would burn
  the 100 CU-hour budget for nothing (§10.2). Nothing wakes the database on a timer: the one-shot warm-up runs
  once per process start, never periodically.

## 3. Current state (verified by reading code and files, 2 Nov 2026)

### 3.1 Data inventory

| Path | Size | What it is | Format | Read by | In git / image |
|---|---|---|---|---|---|
| `data/waves/index.csv` | 2.5 MB | 36,550 BTS 2024 waves (airport, date, hour, counts, disrupted callsigns) | CSV | `build._index`, `list_waves`, `random_wave` | git, image |
| `data/waves/arrivals/{IATA}.csv.gz` | 12 MB (30 files) | 1,198,356 BTS Jun-Aug 2024 arrivals at the 30 hubs, 13 columns | gz CSV | `build._arrivals` (lru 4) | git, image |
| `data/waves/arrivals.csv.gz` | 20.8 MB | the same rows with 18 columns (pre-split) | gz CSV | `scripts/fetch_bts.py`, `split_arrivals.py` only | git, **dockerignored** |
| `data/ref/metar.csv` | 9.6 MB | 66,192 METARs at the 30 hubs, Jun-Aug 2024 | CSV | `build._metar_table` (lru 8, streamed) | git, image |
| `data/ref/runways.csv` | 101 KB | 1,262 runway ends (OurAirports today) with end coordinates | CSV | `build._runways_by_airport` | git, image |
| `data/ref/runway_history.csv` | 11 KB | 78 opened/closed events that wind runways back to the wave date | CSV | `build._runway_history` | git, image |
| `data/ref/aircraft.csv` | 344 KB | 7,921 tails → type, wake, gate size (FAA registry) | CSV | `build._aircraft_table` | git, image |
| `data/ref/gates.csv` | 21 KB | 1,481 OSM gate refs at 10 hubs, no coordinates | CSV | `build._gates_table` | git, image |
| `data/challenge2/flights_index.csv` | 23.5 MB | 358,774 rows of the 2015 challenge `flights.csv`, 14 columns | CSV | `flights2015._index` (whole file into pandas, ~15 MB RAM) | git, image |
| `data/challenge2/flights/{DEST}.csv.gz` | 7.2 MB (320 files) | the same 358,774 rows split per destination, 16 columns | gz CSV | `flights2015._dest_frame` (lru 2) | git, image |
| `data/challenge2/flights.csv` | 36.8 MB | raw challenge file, 358,774 rows × 31 columns | CSV | `split_flights2015.py` only | gitignored, dockerignored |
| `data/challenge2/metar_cache/` | 15 MB | 1,260 IEM station-day CSVs (89,681 observations) fetched **at runtime** for 2015 waves | CSV | `flights2015._fetch_iem` (read and write) | gitignored; on Render it starts empty and is lost on restart |
| `data/challenge2/airports.csv`, `airlines.csv` | 24 KB | 322 airports (state, coordinates), 14 airlines | CSV | `build._airports`, `_airlines`, `scenario` | git, image |
| `data/challenge2/challenge2_operational_constraints.xlsx` | 27 KB | the workbook limits (wake separation, runway caps, burn rates) | xlsx | `constraints._load` | git, image |
| `data/challenge2/disruptions.csv` | 1.0 MB | 11,914 v1 single-flight disruptions | CSV | `scenario.disruptions` (v1 endpoints) | git, **dockerignored** |
| `data/replay/F2015-*.json` | 408 KB | 5 pre-recorded negotiations (ORD, MIA, ATL, DFW, DEN) | JSON | `replay.load`, `server._replay_summaries`, `build._summary` | git, image |
| `eval/benchmark.json`, `benchmark_100.json`, `BENCHMARK*.md` | 30 KB | current benchmark report + variants | JSON/MD | `server._benchmark` | git, image |
| `eval/results.json`, `rows.json`, `REPORT.md` | 520 KB | frozen v1 evaluation | JSON/MD | `server.eval_results` (`/api/eval`) | git, image |
| `eval/runs/*.json` | 7.3 MB (97 runs) | recorded negotiation results with logs (mean 78 KB each) | JSON | `server.get_run` (disk after the 200-run LRU) | untracked locally; ephemeral on Render |
| `eval/decisions.jsonl` | 706 B | duty-manager decisions; holds both v1 rows (`flight_id`) and v2 rows (`run_id`, `edits`) | JSONL | written by `POST /api/decision`, never read | gitignored; ephemeral on Render |
| `data/challenge3/airports.dat`, `kalimantan_…csv` | 1.1 MB | OpenFlights airports (correct ICAO; flight-path plan T1 will read it); challenge 3 locations | CSV | nothing yet | git, image |
| `data/challenge1/` | 0 | challenge 1 train/test (downloaded by `scripts/fetch_data.sh`, **absent on this machine**) | CSV | nothing | gitignored |
| `data/challenge4/ad_nprm.xlsx` | 245 KB | challenge 4 input | xlsx | nothing | git, image |
| `data/challenge5/Flight_Data_With_Weather_Final.csv` | 61 MB | challenge 5 input, 148,273 rows × 123 columns (24 entirely empty) | CSV | nothing | gitignored |
| `.cache/bts/`, `.cache/ref/`, `.cache/drive_new/` | 231 MB | raw downloads (BTS PREZIP months, FAA/IEM/OurAirports, a copy of the hackathon Drive) | mixed | fetch scripts only | gitignored |

Two facts here shape the design:
- **`data/` is wholly copied into the image** (`Dockerfile: COPY data/ ./data/`). Only `arrivals.csv.gz`,
  `flights.csv` and `disruptions.csv` are dockerignored.
- **The IEM METAR cache is live state, not reference data.** It is written by `_fetch_iem` during requests
  and lost on every Render restart. Each cold wave at a new station-day costs a 5 s-timeout call to IEM.

"Raw challenge datasets" (coordinator ruling) means the files under `data/challenge1..5` and the full
`data/challenge2/flights.csv`. The `.cache/` downloads are fetch-script intermediates, not datasets. They stay
out (our call): the BTS zips re-download, and `drive_new` duplicates the challenge folders.

### 3.2 Code boundaries that make this safe

- `build.build_wave(wave_id)` (and `flights2015.build_from_row`) returns a `Wave` dataclass. `rules`,
  `utility`, `moves`, `graph`, `fallback` and `edit` only read that object. So the database change is confined
  to **how `build_wave` and the listing endpoints get their inputs**. The hot path is untouched by
  construction, and §13 adds a test that proves it.
- Readers already sit behind small cached functions (`_index`, `_arrivals`, `_metar_table`, `_aircraft_table`,
  `_gates_table`, `_airports`, `_airlines`, `_runways_by_airport`, `_runway_history`, `flights2015._index`,
  `_locate`, `_dest_frame`, `search`, `_fetch_iem`, `constraints._load`, `scenario.*`, `replay.load/record`,
  `server._save_run/get_run/decision/_benchmark/eval_results`). Each one maps to a store method (§8.2).
- Every reader parses CSV with `pd.read_csv(..., dtype=...)` or `csv.DictReader`. **The store returns CSV
  text** (from the file, or from Postgres via `COPY (select …) TO STDOUT WITH CSV HEADER`). The existing parse
  code then runs unchanged, which is what makes the two backends byte-for-byte comparable (§8.2).
- `list_waves` builds each summary by reading that wave's arrival slice and METAR (`build._summary`), so a
  50-row listing reads 50 slices. In Postgres those summary fields are **precomputed at seed time** (§6.2).
- `backend/__init__.py` loads `.env` into `os.environ` on import, using `setdefault`. Once the user adds
  `DATABASE_URL` to `.env`, **every local test run would see it**. Backend selection must therefore be explicit
  for tests (§8.1, §13).
- `tests/test_api.py` and `scripts/make_samples.py` monkeypatch `server.RUNS_DIR`, `DECISIONS`, `REPLAY_DIR`
  and `BENCHMARK`. These patches move to installing a `FileStore` pointed at temp paths (§13).
- Measured locally from files (Apple silicon): cold `build_wave("F2015-270287")` (ORD) takes 0.42 s, including
  the flights index load; a warm build takes 5-20 ms; `list_waves(airport="ORD", limit=1)` takes 0.18 s. Peak
  RSS after these is 277 MB, with pandas imported.

### 3.3 Deploy today

`render.yaml`: Render free plan, Docker, **no `region` key, so the service runs in `oregon`** (the Blueprint
spec default), health check on `/api/config`. Secrets are `sync: false` env vars. RAM limit 512 MB. The free
instance sleeps when idle and cold-starts on the next request.

## 4. Neon and Render facts this design relies on (verified in docs, 2 Nov 2026)

| Fact | Source |
|---|---|
| Free: **1 GB Postgres storage per project** (20 GB across projects); exceeding it makes "operations that increase storage (inserts, updates, and deletes) … fail", and no data is deleted | neon.com/pricing, neon.com/docs/introduction/plans |
| Storage is the **logical data size**, measured hourly. Restore history: 6 hours, capped at 1 GB of change history; the docs do not say whether history counts toward the 1 GB | neon.com/docs/introduction/plans |
| Free: **100 CU-hours per project per month**; when they run out, compute is suspended until the next billing period. 1 CU ≈ 4 GB RAM; CU-hours = compute size × hours running; suspended compute accrues none. Autoscaling up to 2 CU | neon.com/pricing |
| Free: 5 GB public network transfer per project per month; running out also suspends compute. Up to 10 branches per project | neon.com/pricing |
| **Scale-to-zero:** the compute suspends after 5 minutes of inactivity; on Free "this setting is fixed". Reactivation "within a few hundred milliseconds" | neon.com/docs/introduction/scale-to-zero |
| Cold starts: use longer connect timeouts (`connect_timeout=10` is their Python example) and retries with exponential backoff and jitter | neon.com/docs/connect/connection-latency |
| Suspension severs connections; apps must reconnect (the pool creates new connections, which wake the compute) | neon.com/blog/using-neons-auto-suspend-with-long-running-applications, neon.com/docs/introduction/compute-lifecycle |
| **Pooled connection** = `-pooler` added to the endpoint host. It is PgBouncer in **transaction mode**: no `SET`/`RESET`, `LISTEN`/`NOTIFY`, `WITH HOLD` cursors or session advisory locks. SQL `PREPARE`/`EXECUTE` is unsupported; protocol-level prepared statements work from PgBouncer 1.22. Set `search_path` per role, not with `SET`. **Use the direct (non-pooled) string for migrations, `pg_dump`/`pg_restore` and admin tasks** | neon.com/docs/connect/connection-pooling |
| psycopg 3 behind a pooler: "you should disable prepared statements … by setting `Connection.prepare_threshold` … to `None`" | psycopg.org/psycopg3/docs/advanced/prepare.html |
| psycopg 3 pool: `NullConnectionPool` keeps no idle connections (`min_size=0`); `ConnectionPool` defaults to `min_size=4`; `kwargs` are passed to `connect()` | psycopg.org/psycopg3/docs/api/pool.html |
| Roles created with SQL (`CREATE ROLE … LOGIN PASSWORD …`) are **not** members of `neon_superuser` and get only default privileges; roles created in the Console/API are, with CREATEROLE and BYPASSRLS. Passwords need ≥ 60 bits of entropy | neon.com/docs/manage/roles |
| Neon AWS regions include `aws-us-west-2` (Oregon), `aws-us-east-1`, `aws-us-east-2`, `aws-eu-central-1`, `aws-ap-southeast-1`; a project's region is permanent | neon.com/docs/introduction/regions |
| Render regions: `oregon`, `ohio`, `virginia`, `frankfurt`, `singapore`; **default `oregon`** when `region` is omitted | render.com/docs/blueprint-spec, render.com/docs/regions |
| Neon documents Render as a deploy target that connects through `DATABASE_URL` | neon.com/docs/guides/render |
| PostGIS is available on Neon (`create extension postgis`) | per the coordinator's reading of neon.com/pricing; `0001` migration fails loudly in D0 if it is not |

## 5. Architecture

```
FastAPI handlers (sync def, threadpool)          offline scripts (fetch_*, split_*, benchmark, replay.record)
        │                                                    │ write the committed seed files (unchanged role)
        ▼                                                    ▼
backend/datastore/  get_store() -> Store                scripts/db_seed.py ──COPY──▶ Postgres (Neon)
   ├─ PgStore    (psycopg 3, NullConnectionPool,      scripts/db_migrate.py ─▶ migrations/*.sql
   │              pooled endpoint, app role)                   (owner role, direct endpoint)
   ├─ FileStore  (today's file locations)
   ├─ FallbackStore (Pg first, then files; breaker; status)
   └─ in-process caches (small tables whole; per-airport-day slices LRU)
        │
        ▼
build_wave → Wave (in memory) → negotiation / rules / utility / moves  (never touch the store)
```

- **One module (our call):** `backend/datastore/` with `base.py` (the `Store` protocol and `StoreStatus`),
  `datasets.py` (the dataset registry: file path ↔ table ↔ columns), `files.py`, `pg.py`, `fallback.py` and
  `__init__.py` (`get_store()`, `set_store()`). The name avoids a clash with the flight-path plan's
  `backend/geo/store.py`, which becomes a thin shim over it (§15).
- **Postgres is the runtime source in production; the committed files stay as the seed and the offline backend
  (our call).** Reasons: the tests and offline dev must run without network; git keeps data changes reviewable
  and re-seedable; and Neon Free keeps only a 6-hour restore history, so reference data must be reproducible
  from somewhere other than the database. The user's "move everything" is met at runtime: in production every
  read and write goes to Postgres while it is reachable.
- **Fallback chain (our call):** `PgStore` → `FileStore` (read-only for reference data; live writes go to
  memory plus local disk, as today) → `503 data_unavailable`. A switch happens when a query fails after its
  retries (§8.3), or after 3 consecutive failures (a circuit breaker that retries Postgres after 60 s,
  mirroring the decision-layer breaker in the v2 design §8). Every switch is logged with the reason, and
  `GET /api/config` reports it (§11). The image keeps `data/` so this fallback works when Neon is out of compute
  hours or unreachable. The `.dockerignore` entries stay as they are. Raw challenge tables have no file fallback
  (nothing reads them at runtime).

## 6. Schema

Six schemas (our call): `ref` (reference, read-mostly), `geo` (geometry, flight-path M0), `live` (runtime
writes), `eval` (benchmark and evaluation), `raw` (challenge files as loaded), `meta` (migrations and seed
manifest). PostGIS goes in `extensions`.

Conventions (our call):
- **Columns keep the source file's names and representation**, so `COPY … TO STDOUT WITH CSV HEADER`
  reproduces the file's parse result. Local `HHMM` times stay `int2`; flags such as `diverted`/`cancelled` stay
  `int2` 0/1 (not `bool`, which would COPY out as `t`/`f`); dates are `date` (ISO on output). Mixed-case
  source headers (`IATA_CODE` in `airports.csv`) map to lower-case column names, and the registry aliases them
  back on output.
- Every table copied from a file has `ord int4` = the row's position in the source file, and reads `order by
  ord`. Today's code depends on file order (`list_waves` takes `idx.head(limit)`; `search` pages by position).
- Every table has a primary key. `live`/`eval` rows have `created_at timestamptz default now()`.

### 6.1 `ref`: 2015 challenge flights

```sql
create table ref.flights2015 (          -- union of flights_index.csv and flights/{DEST}.csv.gz, one row per flights.csv row
  row_id int4 primary key, ord int4 not null,
  date date not null, arr_date date not null,
  carrier text not null, flight_no int4 not null, tail text,
  origin text not null, dest text not null,
  sched_dep int2, sched_arr int2 not null,
  arr_delay int2, weather_delay int2, late_aircraft_delay int2, nas_delay int2, carrier_delay int2,
  diverted int2 not null, cancelled int2 not null,
  wheels_on int2, taxi_in int2, wheels_off int2, taxi_out int2,   -- filled by flight-path T6; null before
  callsign text generated always as (carrier || flight_no::text) stored
);
create index on ref.flights2015 (dest, arr_date);              -- wave context (one destination-day)
create index on ref.flights2015 (dest, ord) where cancelled = 0;  -- per-airport listing in file order
create index on ref.flights2015 (ord) where cancelled = 0;     -- unfiltered listing
```
`supported` (the destination has runway data) is `dest in (select airport from ref.runways)`, not a stored
column. Search (`GET /api/flights?q=`) keeps today's semantics exactly: a case-insensitive substring match on
callsign, tail, origin or dest, plus a digit match on `row_id` (also after an `F2015-` prefix). It is run as
`ILIKE '%q%'` (with `%`, `_` and `\` escaped) plus `count(*) over ()`, over at most 358,774 narrow rows (§10). A
`pg_trgm` GIN index (about +15-25 MB) is the lever if p95 misses its budget **(our call: not by default)**.

### 6.2 `ref`: 2024 BTS waves

```sql
create table ref.waves (                -- index.csv, plus summary fields precomputed at seed time
  wave_id text primary key, ord int4 not null,
  airport text not null, date date not null, hour int2 not null,
  n_arrivals int2 not null, n_disrupted int2 not null, disrupted_flights text not null,   -- as in the file
  summary jsonb not null                -- build._summary(row) minus replay_available (parity-tested, §13)
);
create index on ref.waves (airport, ord);

create table ref.bts_arrivals_day (     -- layout B: one packed blob per airport-day
  airport text not null, date date not null,
  n_rows int4 not null, payload bytea not null,   -- gzip of the CSV slice, same 13 columns as data/waves/arrivals/*
  sha256 text not null,
  primary key (airport, date)
);
alter table ref.bts_arrivals_day alter column payload set storage external;   -- already compressed
```
Why blobs: a wave reads exactly one airport-day (`_arrivals` + `groupby("date")`), and nothing queries single
BTS rows. Measured: 2,760 airport-days, 19.4 MB gzipped in total (mean 7.0 KB). As plain rows the same data is
about 98 MB (§7). `replay_available` is added at read time from `eval.replays` (§6.5).

### 6.3 `ref`: weather, runways, aircraft, gates, airports, workbook

```sql
create table ref.metar_2024 (           -- data/ref/metar.csv as is
  ord int4 primary key, station text, airport text not null, valid_utc text not null,
  local_hour_key text not null, drct real, sknt real, gust real, vsby real, ceiling_ft real,
  wxcodes text, raw text);
create index on ref.metar_2024 (airport);
create table live.iem_cache (           -- one IEM response per station-day, exactly as fetched (2015 waves)
  station text not null, date date not null, fetched_at timestamptz not null default now(),
  body text,                            -- null = failed fetch (retried after 120 s, as today)
  primary key (station, date));
create table ref.runways (ord int4 primary key, airport text not null, runway text not null,
  heading_true real, length_ft int4, le_lat float8, le_lon float8, he_lat float8, he_lon float8, surface text,
  le_geog extensions.geography(Point, 4326), he_geog extensions.geography(Point, 4326),   -- not in COPY out
  unique (airport, runway));
create table ref.runway_history (ord int4 primary key, airport text, runway text, event text, date text,
  until text, old_id text, heading_true text, length_ft text, le_lat text, le_lon text, he_lat text,
  he_lon text, reason text, source text);   -- csv.DictReader reads strings; text keeps blanks as blanks
create table ref.aircraft (ord int4 primary key, tail text unique, type_icao text, mfr_model text, wake text,
  gate_size text);
create table ref.gates (ord int4 primary key, airport text, gate_ref text, wide text);
create table ref.airports (ord int4 primary key, iata_code text unique, airport text, city text, state text,
  country text, latitude float8, longitude float8);      -- challenge2/airports.csv (header aliased back)
create table ref.airlines (ord int4 primary key, iata_code text unique, airline text);
create table ref.openflights (ord int4 primary key, c1 text, c2 text, c3 text, c4 text, c5 text, c6 text,
  c7 text, c8 text, c9 text, c10 text, c11 text, c12 text, c13 text, c14 text);  -- challenge3/airports.dat as text (no header, \N nulls; plan T1)
create table ref.workbook (sheet text, row_no int4, cells jsonb not null, primary key (sheet, row_no));
create table ref.disruptions_v1 (...);  -- the 20 columns of disruptions.csv, typed from the CSV header, plus ord
```
- **Exact round trips:** float columns can print differently from the source text (`40.65236` stays
  `40.65236` with PG 12+ shortest-exact output, but `7599.0` would print as `7599`). Every column the readers
  parse with `dtype=str` or `csv.DictReader` is therefore `text` (our call), and the parity tests compare
  parsed frames, not bytes.
- **2024 METAR:** `_metar_table(iata)` keeps the latest observation per `local_hour_key`. PgStore returns that
  airport's rows as CSV (`where airport = %s order by ord`) and the existing loop runs unchanged (≈ 2,200 rows,
  about 200 KB per hub). The parity follows from this, with no new time-zone code.
- **IEM on-demand cache (our call):** the raw response text is stored per station-day (`live.iem_cache`),
  exactly as the disk cache does today. The existing parser runs unchanged on it. `_fetch_iem` checks it before
  calling IEM and upserts after a fetch. The 1,260 local station-days are seeded once (15 MB of text, about
  5 MB after TOAST compression). Retention: above 40 MB the oldest-fetched rows are deleted. This check runs
  after an insert, at most once an hour, never on a timer.
- **Workbook:** the seed stores every row of the two sheets `constraints._load` reads (`airport_capacity`,
  `wake_separation`) as JSON cell arrays. PgStore rebuilds the same row tuples and the parse loop is shared.
  `constraints.SOURCE` (the cited source string) is unchanged.

### 6.4 `live`: runs, decisions, edits

```sql
create table live.runs (
  run_id text primary key check (run_id ~ '^[0-9a-f]{12}$'),
  wave_id text not null, mode text not null, model text, outcome text,
  engine_version text not null default 'v2',
  created_at timestamptz not null default now(),
  pinned bool not null default false,     -- never pruned (decided, or imported from a replay/benchmark failure)
  result jsonb not null                   -- the full NegotiationResult including `log`, exactly what GET returns
);
create index on live.runs (wave_id, created_at desc);
create index on live.runs (created_at) where not pinned;

create table live.decisions (
  id bigserial primary key,
  run_id text,                                         -- v2 rows (no FK, see below)
  flight_id int4, plan_id text,                        -- v1 rows (web/index.html), kept as they come
  decision text not null check (decision in ('APPROVE', 'OVERRIDE')),
  note text not null default '',
  edits jsonb not null default '[]',                   -- Assignment[] the duty manager moved before approving
  recorded_at timestamptz not null default now(),
  check (run_id is not null or flight_id is not null),
  check (decision <> 'OVERRIDE' or note <> '')         -- the API already enforces this; the DB enforces it too
);
create index on live.decisions (run_id);
```
- Recording a decision sets `runs.pinned = true` in the same transaction (our call). An approved plan never
  disappears.
- **No foreign key from `decisions.run_id` to `runs` (our call).** A run saved only in memory during a file
  fallback, or one pruned before the decision arrives, must never cause a decision to be rejected.
- Retention (our call): keep the newest 2,000 unpinned runs, at about 20 KB each after TOAST compression (§7).
  Pruning runs after a run insert, at most once an hour.
- `POST /api/evaluate` stays stateless. It writes nothing: edits are recorded with the decision, as the
  contract says.
- **Run writes never delay the stream (our call).** Today `finish()` calls `_save_run` before the `result`
  event is queued. With Postgres, `_save_run` puts the run in the in-memory LRU synchronously (so
  `GET /api/runs/{id}` works immediately) and writes to the store on a background thread. A failed write is
  logged and sets `Config.store.degraded`.

### 6.5 `eval`: replays, benchmark, v1 evaluation

```sql
create table eval.replays (             -- data/replay/*.json; engine_version for flight-path T16/T18
  wave_id text not null, engine_version text not null default 'v2',
  recorded_at timestamptz not null default now(),
  body jsonb not null,                  -- {"messages": [...], "result": {...}} exactly as the file
  primary key (wave_id, engine_version)
);
create table eval.benchmark_reports (
  id bigserial primary key, generated_at timestamptz not null default now(),
  engine_version text not null default 'v2',
  label text not null,                  -- 'main', '100', '1000', 'v2-frozen', ...
  is_current bool not null default false,
  report jsonb not null,                -- BenchmarkReport exactly as served
  markdown text                         -- BENCHMARK*.md body
);
create unique index on eval.benchmark_reports (is_current) where is_current;
create table eval.v1_eval (name text primary key, body jsonb not null);   -- results.json, rows.json
```
- `GET /api/benchmark` serves the `is_current` row. The flight-path plan's "engine history" (T19) is just
  more rows.
- `replay_available` (`Config`, `WaveSummary`) becomes a lookup in a cached set of replay ids for the current
  engine version, refreshed when a replay is written.
- The 97 local `eval/runs/*.json` are imported once into `live.runs` (unpinned).

### 6.6 `raw`: the challenge datasets (coordinator ruling)

One table per file, created by the seeder from the file itself (the only tables not created by a migration;
the `raw` schema is). Names: `raw.ch2_flights` (the full `flights.csv`), `raw.ch5_flights_weather`,
`raw.ch3_airports_dat`, `raw.ch3_kalimantan_locations`, `raw.ch4_ad_nprm__<sheet>` (one per sheet),
`raw.ch1_train` and `raw.ch1_test` (only if `data/challenge1/*.csv` exist when seeding; they are absent on this
machine). Columns keep the header (lower-cased, non-alphanumerics → `_`) plus `ord`. Types are inferred per
column (our call): integers to the narrowest of `int2/int4/int8` that holds the range, other numbers to
`float8`, everything else `text`. Columns that are empty in every row become `text` (null, free in the null
bitmap). `meta.seed_manifest` records the source sha256. Nothing in the app reads `raw.*`. They are there
because the user asked for everything, for SQL analysis in the Neon console, and they are **the first thing to
drop** if the size guard trips.

### 6.7 `geo`: flight-path geometry (tables created empty in milestone D4; filled by the flight-path M0 tasks)

The engine loads a whole airport (procedures, taxi graph) and runs its own routing and BFS in Python. The plan
deliberately has no networkx and no shapely. So **PostGIS is not on the hot path.** It helps where data is
looked up by location: the `/geo` endpoint (GeoJSON straight from SQL), coverage reports, and ad-hoc checks of
gate-to-stand binding. **(our call)** The graph documents are stored whole, and the point features are also
stored as PostGIS rows:

```sql
create table geo.procedures (           -- one per ICAO: the plan's procedures schema (T2), gzip JSON
  icao text primary key, cycle text not null, tier text not null,   -- 'star+iap' | 'iap' | 'none'
  doc bytea not null, sha256 text not null, loaded_at timestamptz default now());
create table geo.fixes (icao text, ident text, geog extensions.geography(Point, 4326),
  primary key (icao, ident));
create table geo.surface (              -- one per ICAO: the plan's surface schema (T4), gzip JSON
  icao text primary key, osm_timestamp text, doc bytea not null, sha256 text not null,
  reach_pct jsonb, loaded_at timestamptz default now());
create table geo.stands (icao text, id text, ref text, node int4, geog extensions.geography(Point, 4326),
  primary key (icao, id));
create table geo.osm_gates (icao text, ref text, geog extensions.geography(Point, 4326),
  primary key (icao, ref));
create table geo.holding_positions (icao text, node int4, strip text, geog extensions.geography(Point, 4326),
  primary key (icao, node));
create table geo.movements2015_day (    -- plan T6 movements, packed per airport-day (§7: rows do not fit well)
  airport text, date date, n_rows int4 not null, payload bytea not null, sha256 text not null,
  primary key (airport, date));
create table geo.coverage (iata text primary key, icao text, procedures_tier text, surface_tier text,
  stands int4, movements bool);
create index on geo.stands using gist (geog);
create index on geo.osm_gates using gist (geog);
```
`doc` is stored as gzip `bytea`, not `jsonb` (our call). The engine decodes the whole document into Python
anyway. gzip is about 2-4× smaller than TOAST-compressed jsonb for this kind of numeric JSON, and the bytes are
identical to the committed `*.json.gz` file, so the `sha256` proves parity for free.

### 6.8 `meta`

```sql
create table meta.schema_migrations (version text primary key, name text, sha256 text not null,
  applied_at timestamptz default now());
create table meta.seed_manifest (dataset text primary key, source_sha256 text not null, script_version text,
  n_rows int8, bytes int8, loaded_at timestamptz default now());
```

## 7. Size estimate vs the 1 GB storage limit

No Postgres is available on the build machine, so these are analytic estimates. Milestone D0 replaces them with
`pg_total_relation_size` per table and `pg_database_size`, and the seed script enforces the 750 MB guard (§9).
Neon measures storage as logical data size, which `pg_database_size` approximates.

**Method.** A heap row costs a 24 B tuple header (32 B once the null bitmap spills past 8 nullable columns), plus
a 4 B line pointer, plus the column widths aligned to 8 B: `int2` 2, `int4`/`date`/`real` 4, `float8` 8, a
short `text` 1 + length. A btree entry costs about 16-20 B with ~90 % leaf fill. Btree deduplication (PG 13+)
shrinks indexes with many repeated keys. A jsonb/text value over ~2 KB is TOAST-compressed: run JSON compresses
7.9× with zlib level 1 (measured, 78 KB → 9.9 KB), and pglz is assumed to reach half of that (about 20 KB per
run).

| Dataset | Rows | Row arithmetic | Total |
|---|---|---|---|
| Catalog + PostGIS `spatial_ref_sys` | – | assumption, measured in D0 | ~15 MB |
| `ref.flights2015` (+T6 taxi cols) | 358,774 | 62 B data + 36 B overhead ≈ 98 B → 35 MB heap; PK 8, (dest, arr_date) 4 (deduped), 2 partial `ord` indexes 14 | ~61 MB |
| `ref.waves` + summaries (jsonb ~250 B) | 36,550 | ~330 B | ~13 MB |
| `ref.metar_2024` | 66,192 | 88 B raw + ~70 B fields + 32 B | ~14 MB |
| `live.iem_cache` (today → cap) | 1,260 station-days | 15 MB text, TOAST ~3× | 5 → **40 MB cap** |
| small ref: runways (+geog), history, aircraft, gates, airports, airlines, workbook, v1 disruptions | ~22k | | ~6 MB |
| `live.runs` (today → cap) | 97 → 2,000 | ~20 KB TOAST each | 2 → **40 MB cap** |
| `live.decisions`, `eval.*` (5 replays ≈ 0.1 MB, reports, v1 eval 0.5 MB) | small | | ~2 MB |
| **2024 BTS arrivals, packed per airport-day** | 2,760 blobs | measured: 19.4 MB gz | **~20 MB** |
| (2024 BTS arrivals as plain rows, for comparison) | 1,198,356 | 41 B data → 48 aligned + 28 ≈ 76-80 B; (airport, date) deduped ~4 MB | (~98 MB) |
| geo.procedures + fixes (plan T3: ≤ 3 MB gz) | ~320 docs, ~50k fixes | 3 + 4 heap, GiST 3 | ~10 MB |
| geo.surface + stands/gates/holds (plan T5: 5-12 MB gz, stop at 15) | ~320 docs, ~40k points | 15 + 4, GiST 2 | ~21 MB |
| **geo.movements2015, packed per airport-day** | ~11k blobs | plan's 50 MB cap for per-*airport* files × 1.6, the inflation measured when BTS 2024 was repacked per airport-day (12 MB → 19.4 MB) | **~60-80 MB** |
| (geo.movements2015 as plain rows, for comparison) | ~6-8 M (estimate: arrivals + departures at ~30 hubs, 2015) | ~70 B + index | (~500-620 MB) |
| `raw.ch2_flights` (full `flights.csv`) | 358,774 | 26 int columns narrowed (~3 B avg) + 4 text (~20 B) + 1 float + ord ≈ 110 B data + 40 B | ~54 MB |
| `raw.ch5_flights_weather` | 148,273 | measured mean ~560 B data per row (pandas over the file, int/float as 8 B) + 36 B; narrowing ints saves some | ~70-90 MB |
| `raw.ch3_*`, `raw.ch4_*` | ~8k | | ~3 MB |
| `raw.ch1_*` | unknown | absent on this machine; measured if present when seeding | ? |

**Totals**

| Layout | At D3 (today's data) | After flight-path M0 | At retention caps | Share of 1 GB |
|---|---|---|---|---|
| **B + raw (ruled)**: rows for everything except the two bulk time series (packed per airport-day); raw challenge tables as rows | ~265-285 MB | ~356-396 MB | ~429-469 MB | **27 % → 47 %** |
| B without raw (what to fall back to if the guard trips) | ~138 MB | ~229-249 MB | ~302-322 MB | 14 % → 32 % |
| B + raw + BTS 2024 as rows | ~343-363 MB | ~434-474 MB | ~507-547 MB | 34 % → 55 % |
| Everything as plain rows, including movements | ~343-363 MB | ~874-1,014 MB | ~947-1,087 MB | **does not fit** (needs Launch, $0.35/GB-month) |

**Conclusion.** The ruled layout fits with more than 50 % headroom at the retention caps, and below the 750 MB
guard with room for a ±30 % estimate error (≈ 610 MB worst case at the caps). "Everything as plain rows" still does not fit,
because the 2015 movements alone are 6-8 M rows. That is why the two bulk time series stay packed.
`raw.ch1_*` is the unknown: the guard catches it.

**History and WAL.** Seeding about 450 MB writes about as much WAL. Neon caps Free history at 1 GB of change
history over 6 hours. Whether history counts toward the 1 GB storage is not stated, so the seeder loads one
dataset per transaction and **raw tables last**, and it checks size between datasets (§9). A full re-seed
should not be repeated within 6 hours unless it has to be.

## 8. Data access layer

### 8.1 Backend selection (our call)

`get_store()` picks the backend once per process (`set_store()` replaces it, for tests and scripts):
1. `ATM_STORE=files` → `FileStore`. **The test suite sets this in `tests/conftest.py`** (an autouse,
   session-scoped fixture that also removes `DATABASE_URL` from `os.environ`), because `backend/__init__.py`
   would otherwise load it from `.env`.
2. `ATM_STORE=pg`, or `DATABASE_URL` present → `FallbackStore(PgStore, FileStore)`. Postgres is tried first;
   files serve when it fails (logged, visible in `Config.store`). There is no "require DB" switch: the
   compute-hour limit and Render's health check both argue for staying up on files, and the fallback is never
   silent.
3. Otherwise → `FileStore`.

### 8.2 The `Store` protocol (method per current reader)

The readers keep their parse code. The store hands them **text in the source format**. That is the file
contents for `FileStore`, and `COPY (select <registry columns> … order by ord) TO STDOUT WITH CSV HEADER` (or
the blob/text column) for `PgStore`.

| Store method | Returns | Replaces | Postgres access | In-process cache |
|---|---|---|---|---|
| `csv(name)` for `runways`, `runway_history`, `aircraft`, `gates`, `airports`, `airlines`, `disruptions_v1`, `openflights` | CSV text | file reads in `_runways_by_airport`, `_runway_history`, `_aircraft_table`, `_gates_table`, `_airports`, `_airlines`, `scenario.*`, plan T1 | COPY of the whole table, on first use | the callers' existing `_cached` / `lru_cache` |
| `workbook_rows(sheet)` | `list[tuple]` | `openpyxl` in `constraints._load` | `ref.workbook` | `lru_cache(1)` as today |
| `wave_index_csv(airport, q, limit, offset)`, `wave_row(wave_id)`, `wave_summary(wave_id)` | CSV text / dict / dict or None | `build._index`, `_index_row`, `list_waves`, `random_wave`, `_summary` | `ref.waves` filtered, `order by ord` | `lru_cache(256)` on listings, as today |
| `arrivals_day(iata, date)` | CSV text (13 columns) or None | `build._arrivals` + `groupby(date)` | `ref.bts_arrivals_day` by PK → gunzip | LRU 16 |
| `flight2015_row(row_id)` | dict of strings or None | `flights2015._locate` | PK | – |
| `flights2015_day(dest, arr_date)` | CSV text (16 columns + taxi when present) | `_dest_frame` + its date filter | `(dest, arr_date)` index | LRU 64 |
| `search_flights2015(airport, q, limit, offset)` | `(list[FlightRow], total)` | `flights2015.search` | filter + `count(*) over ()` + `LIMIT/OFFSET`, `order by ord` | – |
| `metar_2024_csv(iata)` | CSV text | `_metar_table` | `where airport = %s order by ord` | `lru_cache(8)` as today |
| `iem_get(station, date)` / `iem_put(station, date, body)` | `(fetched_at, body)` or None | `_fetch_iem` disk cache | `live.iem_cache` | `_IEM_MEM` as today |
| `save_run(result)`, `get_run(run_id)`, `list_runs(wave_id, limit)` | – / dict or None / list | `server._save_run`, `get_run` | `live.runs` | `RUNS` LRU 200, as today |
| `record_decision(row)` | `bool` persisted | `DECISIONS.open("a")` | `live.decisions` (+ pin the run) | – |
| `replay(wave_id)`, `replay_ids()`, `put_replay(wave_id, body)` | dict or None / set / – | `replay.load/record`, `_replay_summaries`, `replay_available` | `eval.replays` | ids cached; bodies LRU 8 |
| `benchmark_current()`, `put_benchmark(report, markdown, label)`, `v1_eval(name)` | dict or None | `server._benchmark`, `eval_results` | `eval.*` | cached until a write |
| `procedures(icao)`, `surface(icao)`, `movements_day(iata, date)` (D4) | bytes (gzip) or None | the plan's `backend/geo/store.load_*` | `geo.*` by PK | LRU 4 airports (plan budget ≤ 60 MB each) |
| `status()` | `StoreStatus` | – | none (last known state) | – |

`FileStore` reads today's paths. Its constructor takes overridable paths (`runs_dir`, `decisions_path`,
`replay_dir`, `benchmark_path`, `iem_dir`), so the existing test fixtures keep using temp directories.

### 8.3 Connections, scale-to-zero and retries (our call)

- Driver: `psycopg[binary]==3.3.6` and `psycopg-pool==3.3.3` (latest on PyPI, 2 Nov 2026). **Sync**, not
  async: every handler that reads data is a sync `def` in FastAPI's threadpool, and the negotiation runs in a
  worker thread. An async driver would need a second code path for no gain.
- **`NullConnectionPool(conninfo=DATABASE_URL, max_size=4, timeout=10, open=False, kwargs={"prepare_threshold": None,
  "connect_timeout": 10, "application_name": "final-approach", "keepalives_idle": 30, "keepalives_interval": 10,
  "keepalives_count": 3})`.** No idle connections are held. The process
  therefore never keeps the Neon compute (or a PgBouncer server session) busy between requests, and scale-to-zero
  works. Connect overhead per request (one TLS handshake to the same-region pooler, roughly 20-60 ms) is
  accepted. A request that needs several queries (a wave build) uses one connection through
  `store.session()`. A 5th concurrent request waits at most 10 s (`timeout=10`); a `PoolTimeout` falls back to
  files like a connection error.
- `prepare_threshold=None`, as psycopg's docs require behind a transaction-mode pooler.
  `statement_timeout = '5s'` and `search_path` are set on the **role** (`ALTER ROLE … SET`), because `SET` does
  not survive transaction pooling.
- **Waking a sleeping compute:** `connect_timeout=10` (Neon's recommendation). On `psycopg.OperationalError`,
  retry twice with backoff 0.5 s and 2 s plus up to 30 % jitter. Then the `FallbackStore` serves this call from
  files and counts a failure (the breaker opens at 3 and retries after 60 s). Compute-hour or transfer
  exhaustion looks the same (connection refused or an error at connect) and is handled the same way.
- **The pool opens in the background** in the FastAPI lifespan (replacing the `on_event("startup")` warmer).
  No query runs at startup unless a request needs one.
  Clarification (T30): under PgStore the replay warm-up (`server._kick_warm`, one-shot, daemon thread, `ATM_WARM=0` off) starts on the first API request, normally Render's first health check, never at startup.
- **URL form:** `DATABASE_URL` is the Neon **pooled** string (`…-pooler.<region>.aws.neon.tech/…`) for the app
  role, with `sslmode=require` (`channel_binding=require` if the Console offers it). `DATABASE_OWNER_URL` is
  the **direct** string for the owner role. It is used only by `db_migrate.py` and `db_seed.py` on the developer
  machine, as Neon recommends for migrations. COPY runs fine over the direct endpoint.

### 8.4 Memory (512 MB host)

Today's biggest residents leave the process: the flights index (~15 MB as pandas), up to 4 whole-airport
arrival frames (ATL alone is 61 MB as raw strings, less with categoricals), and up to 2 per-destination 2015
frames. They are replaced by airport-day slices of at most ~1 MB each. Small reference tables stay resident
(~4 MB). Target (our call): steady-state RSS under PgStore ≤ the FileStore figure, measured with a subprocess
check in the style of the flight-path plan's T7.

### 8.5 Network transfer (5 GB/month)

Per cold start: the small tables, ≈ 1 MB. Per wave build: ≤ 300 KB (one airport-day blob, the window rows, one
hub's METAR CSV). Per `GET /api/runs/{id}`: ~78 KB. Even 2,000 wave opens and 2,000 run fetches a month is
≈ 0.8 GB. The flights index is **never** downloaded whole (a whole-index load per cold start would be ~25 MB
each time). The one-off seed upload is inbound to Neon. Whether ingress counts toward "public network
transfer" is not stated, so it is checked in the Console after D3.

## 9. Migrations and seeding

- **Layout (our call):** `migrations/NNNN_<name>.sql` at the repo root (provider-neutral; no Supabase/Neon CLI
  needed). Files: `0001_schemas_meta.sql` (schemas, `meta.*`, `create extension if not exists postgis with
  schema extensions`), `0002_roles_grants.sql`, `0003_ref.sql`, `0004_live_eval.sql`, later `0005_geo.sql`.
- **Runner:** `scripts/db_migrate.py [--dry-run]`, using `DATABASE_OWNER_URL`. It applies the files in order,
  each in its own transaction, and records `version, name, sha256` in `meta.schema_migrations`. If an applied
  file's sha256 has changed, it aborts (migrations are append-only). It is idempotent: a second run is a no-op.
- **Seeder:** `scripts/db_seed.py [dataset ...] [--check] [--budget-mb 750]`, using `DATABASE_OWNER_URL`.
  Datasets, in the default order: `small`, `workbook`, `waves`, `bts_arrivals`, `metar_2024`, `iem_cache`,
  `flights2015`, `disruptions_v1`, `replays`, `runs_import`, `benchmark`, `v1_eval`, then `raw`. In D4 it adds
  `procedures`, `surface`, `movements2015` and `coverage` (each skipped when its files do not exist yet).
  - For each dataset it hashes the source files plus the script version. If `meta.seed_manifest` matches, it
    skips. `--check` only reports what is stale, and exits 1 if anything is.
  - **Load path (reference tables):** `create table <t>__new (like <t> including defaults including
    constraints including generated including storage)`, without indexes. Then `COPY <t>__new
    (<explicit non-generated columns>) FROM STDIN` via psycopg's `cursor.copy()`, streamed (a generated column
    such as `flights2015.callsign` cannot be copied into). Indexes are built after the load (less WAL). Then, in
    one transaction: verify the row count, `drop <t>`, `rename <t>__new`, rename its indexes, upsert the
    manifest. Readers never see a half-loaded table, and a failure leaves the old table in place.
  - **Grants survive the swap** because `0002_roles_grants.sql` sets `ALTER DEFAULT PRIVILEGES FOR ROLE
    <owner> IN SCHEMA ref, geo, eval GRANT SELECT ON TABLES TO app_read` (and the write grants for `live` and
    `eval`). A table the owner creates later, including `<t>__new`, gets them automatically. A `live_db` test
    checks `has_table_privilege` for the app role after a re-seed.
  - `live.*` tables are never truncated by the seeder. `runs_import` and `iem_cache` insert with `on conflict
    do nothing`.
  - **Size guard:** after each dataset, `pg_database_size(current_database())` is checked. Above
    `--budget-mb` (default **750**), it stops and prints per-table sizes and the advice "drop `raw.*` first"
    (`db_seed.py --drop raw`). Raw is seeded last, so the app's data is always in before the guard can trip.
  - The seeder never prints a URL. On error it prints the host with the password redacted.
- **Who runs what:** the user creates the project, the app role (§12) and the `.env` entries. Then we run
  `db_migrate.py` and `db_seed.py` from this machine. Render never migrates or seeds on boot (our call: a
  failing migration must not take the site down, and the free instance should not spend its 512 MB on COPY).

## 10. Performance

### 10.1 Latency budgets (our call)

| Path | Budget | How it is met |
|---|---|---|
| `rules.check`, `utility.evaluate`, moves, votes, fallback search | **0 DB calls** | only read the in-memory `Wave`; enforced by a test that makes the store raise after `build_wave` and then runs a formulas negotiation (§13) |
| `build_wave` (BTS), compute awake, slices not cached | p95 ≤ 400 ms, ≤ 3 queries on one connection | wave row + arrivals blob + hub METAR CSV (resident after the first wave at that hub), plus resident small tables |
| `build_wave` (F2015), compute awake, not cached | p95 ≤ 400 ms, ≤ 3 queries (+ IEM on a cache miss, 5 s timeout as today) | flight row, destination-day rows, IEM cache row |
| any request that wakes the compute | + ≤ 1.5 s once | Neon reactivation (a few hundred ms) + connect; retries in §8.3 |
| `build_wave`, cached slices | ≤ 50 ms | LRUs in §8.2 |
| `GET /api/waves`, `/api/flights` | p95 ≤ 300 ms | precomputed summaries; one indexed query with `count(*) over ()` |
| `GET /api/runs/{id}` | p95 ≤ 150 ms (LRU hit ~1 ms) | PK lookup |
| Run save | off the SSE path | background thread after the LRU insert |
| `/api/config` (Render health check) | no DB call in the handler; under Postgres the first request starts the one-shot replay warm-up thread (once per process) | reports the last known store status |

**Region:** the Render service runs in `oregon` (Blueprint default), so the Neon project goes in
**`aws-us-west-2` (Oregon)**. Neon regions cannot be changed after creation.

Measured in D2/D3 from Render itself (log lines, not an endpoint): p50/p95 for each row above. If a budget is
missed, the levers in order are: batch the wave inputs into one SQL function returning JSON (1 round trip);
widen the LRUs; add `pg_trgm` for search.

### 10.2 Compute hours (100 CU-hours/month)

The compute runs only while queries arrive, plus 5 minutes after the last one. **Our call:** set the compute
size to a fixed 0.25 CU (min = max) in the Console. The workload is a few small queries, and autoscaling to 2 CU
would burn the budget 8× faster. At 0.25 CU the budget is **400 active hours a month**.

| Usage | Active hours/month (incl. 5-min tails) | CU-hours at 0.25 CU |
|---|---|---|
| Development + demo, ~2 h of use on 20 days | ~45 | ~11 |
| Judging week: 30 sessions/day × 20 min, 7 days, then light use | ~90 | ~23 |
| Heavy: someone uses it 8 h every day | ~250 | ~63 |
| Never idle 5 min, 24/7 (e.g. a timer that queries the DB) | 730 | **183: budget gone in ~16 days** |

So: no keep-alive (Neon does not need one, unlike Supabase's pause; a ping would only burn hours), no timer
queries, no DB in the health check, and retention runs only after writes. If the budget does run out, compute
is suspended to month end, and the site keeps running on files (degraded, badged).

## 11. API: behaviour unchanged, additive changes

All v2.1 endpoints keep their shapes and status codes. The parity tests (§13) compare responses from both
backends. Additive, in one `contract:` commit, numbered **v2.1.1** (our call: this keeps the flight-path
plan's "v2.2" references valid):

- `Config.store?: { backend: "postgres" | "files"; degraded: boolean; reason?: string | null;
  persistent: boolean }`. `persistent` is false when runs and decisions are only in memory or on ephemeral
  disk. The UI can badge "decisions are not being saved" (the teammate's choice to show it).
- `POST /api/decision` response adds `persisted?: boolean`.
- `GET /api/runs?wave_id=&limit=20` → `{ runs: { run_id, wave_id, mode, model, outcome, created_at,
  decided: boolean }[] }`, newest first, `limit ≤ 100`. Run links already work by id; this lists past runs for
  a wave.
- `NegotiationResult` is unchanged. A shareable link is just `/?run=<run_id>` on the frontend, which already
  fetches `GET /api/runs/{id}`. Whether the console reads that query parameter is a frontend task (the
  teammate's call).

`/api/config` must answer without the DB: it reports the last known store state and never blocks on a query.
That keeps Render's health check green, and it never wakes the compute.

## 12. Security

- **Secrets.** `.env` (gitignored) holds `DATABASE_URL` (pooled, app role) and `DATABASE_OWNER_URL` (direct,
  owner role). Optionally it also holds `DATABASE_TEST_URL` (§13). **Render gets only `DATABASE_URL`**
  (`render.yaml`: `- key: DATABASE_URL  sync: false`; the user pastes the value in the dashboard). No database
  URL reaches the frontend; it talks only to `/api`.
- **Least privilege (our call).**
  - `0002_roles_grants.sql` creates two NOLOGIN group roles. `app_read` gets `USAGE` on `ref`, `geo`, `eval`,
    `live` and `SELECT` on their tables. `app_write` gets `SELECT, INSERT, UPDATE, DELETE` on `live.*`,
    `INSERT, UPDATE` on `eval.*`, and the sequences. Default privileges carry these to future tables (§9). Neither
    group gets anything on `raw`, `meta` or DDL.
  - The **login** role `atm_app` is created by the user once, with SQL (not the Console, so it is not a member of
    `neon_superuser` with BYPASSRLS/CREATEROLE), from a snippet in the README:
    `create role atm_app login password '<≥60-bit password>' in role app_read, app_write; alter role atm_app
    set statement_timeout = '5s'; alter role atm_app set search_path = ref, live, eval, geo, extensions;`.
    The password goes straight into `.env` and Render. No script ever prints or generates it.
  - Migrations and seeds run as the project owner role (`DATABASE_OWNER_URL`), which never leaves the
    developer machine.
- No row-level security (our call). There are no per-user rows, and no client ever connects to the database
  directly. Grants are the control.
- Logs: a database URL is never logged. Connection errors log the host and SQLSTATE only. No client IPs are
  stored in Postgres (the rate limiter stays in memory).
- SQL uses parameters only (psycopg placeholders). Identifiers in COPY statements come from the dataset
  registry, never from requests. The `ILIKE` pattern escapes `%`, `_` and `\`.

## 13. Testing

- **Default run stays offline:** `python3 -m pytest -q` (130 tests) uses `FileStore`. `tests/conftest.py`
  sets `ATM_STORE=files` and removes `DATABASE_URL`, `DATABASE_OWNER_URL` and `DATABASE_TEST_URL` from
  `os.environ` for the session (after keeping `DATABASE_TEST_URL` for the `live_db` fixture). A guard test
  asserts that `get_store()` is a `FileStore` during the suite.
- **New, offline:**
  - `FileStore` methods: each returns exactly the file text or the parsed value today's code reads.
  - `test_no_db_in_hot_path.py`: build a wave, swap the store for one that raises on any call, then run a
    formulas negotiation and an `/api/evaluate`. It must pass. This pins §2's anti-goal.
  - `FallbackStore` with a fake primary that raises `OperationalError`: retries happen, files answer, the
    breaker opens after 3 failures and half-opens after 60 s; `/api/config` reports `backend: "files",
    degraded: true`; `POST /api/decision` returns `persisted: false`.
  - Pure functions used by the seeder: packing per airport-day (round-trip equals the source slice); the raw
    type inference; the manifest hash; the summary precompute equals `build._summary` for 5 waves.
  - Migration lint: files sort, names match `^\d{4}_[a-z0-9_]+\.sql$`, and the checksums of committed files
    match `migrations/CHECKSUMS` (append-only).
- **New, `live_db` marker** (registered in `pytest.ini` next to `live`; **skipped unless `DATABASE_TEST_URL` is
  set**):
  - `DATABASE_TEST_URL` points at a **Neon branch** named `test` (owner role, direct endpoint). It is
    copy-on-write from `main`, so tests can write freely without touching production data (our call). The
    branch's compute counts against the same 100 CU-hours; a test run is minutes.
  - **Parity:** for the 5 replay waves, 20 seeded BTS wave ids and 20 seeded F2015 row ids,
    `build_wave(id).to_dict()` from `PgStore` equals `FileStore` as canonical JSON. Same for `/api/waves`,
    `/api/flights` (10 queries including empty, airport-only, substring, `F2015-` digits and an offset page),
    `/api/benchmark`, `/api/runs/{id}` for an imported run, and `replay_available`.
  - **Live writes:** save run → get run → decision pins it → retention prunes only unpinned rows.
  - **Migrations and seed:** migrate twice (second is a no-op); `db_seed.py --check` exits 0 after a seed;
    the app role can read `ref` and write `live` but cannot read `raw` or `meta` or create tables.
  - **Budgets:** `pg_database_size()` ≤ 750 MB; p95 timings from §10 measured from the test machine (reported,
    not asserted, because the test machine is not Render).
- **Frontend:** unchanged. `?mock=1` and the bundled samples do not involve the backend. `make_samples.py`
  runs on `FileStore`, and by parity the samples equal what Postgres would serve.

## 14. What stays as files, and why

| Stays | Why |
|---|---|
| All committed `data/` seed files (CSV, gz, replay JSON) and `eval/*.json`/`*.md` | the seed source and the offline/test backend; reviewable in git; the only durable copy of reference data (Neon Free keeps 6 h of history) |
| `data/` copied into the image | the read-only fallback while Neon is unreachable or out of compute hours (§5) |
| Raw challenge files on the dev machine (`flights.csv`, `challenge5`, `challenge1`) | the seed source for `raw.*`; gitignored as today |
| `.cache/` raw downloads | fetch-script inputs, gitignored; not datasets (§3.1) |
| `frontend/src/api/samples/` | `?mock=1` and the teammate's offline build |
| `BENCHMARK*.md`, `REPORT.md`, `GEO_COVERAGE.md`, the CIFP disclaimer `README.md` | human-readable docs in the repo; benchmark bodies are also stored in `eval.benchmark_reports.markdown` |
| `eval/runs/`, `eval/decisions.jsonl` | **no longer written** by a Postgres deployment; still written by FileStore (local dev) as today |

## 15. Changes to the flight-path plan (M0 data tasks and later)

The plan's rule "derived files go to `data/ref/`, runtime reads committed files" becomes: **derived files
still go to `data/ref/` (seed and offline backend); `scripts/db_seed.py <dataset>` loads them into `geo.*`; the
runtime reads through `backend.datastore`** (PgStore in production, FileStore offline). Each task keeps its tests
and its file outputs. The DB step is additive, so the 35-task plan stays executable in order. The Postgres plan
(`docs/superpowers/plans/2026-11-02-postgres-data.md`, last task) applies these edits to the flight-path plan
file.

| Plan task | Change |
|---|---|
| Global Constraints | Add: "Runtime reads geometry through `backend.datastore`. After generating files, run `python3 scripts/db_seed.py <dataset>` (it needs `DATABASE_OWNER_URL`; skip it when unset). New tables come only via `migrations/`." `backend/geo/store.py` stays the plan's import surface; its loaders gunzip what `get_store().procedures/surface/movements_day` return |
| T1 IATA→ICAO | `icao_for` parses `get_store().csv("openflights")` (FileStore: `data/challenge3/airports.dat`, as planned; Postgres: `raw.ch3_airports_dat` is not used at runtime, a `ref.openflights` copy is). `test_every_flights2015_destination_resolves` keeps `os.listdir` (FileStore) and gains a `live_db` twin over `ref.flights2015` destinations |
| T3 CIFP procedures | `load_procedures` delegates. Add `db_seed.py procedures` (doc blob + `geo.fixes` points). `test_procedures_size_cap` stays (it checks files); add a `live_db` check that `geo.procedures` matches by sha256. `test_coverage_counts` unchanged (FileStore) |
| T5 OSM surface | `load_surface` delegates. Add `db_seed.py surface` (doc blob + stands, gates, holding positions as PostGIS points). `bind_gates` stays Python (offline tests, no PostGIS dependency); a `live_db` check compares a sample of bindings with nearest-stand queries via `ST_DWithin(…, 150)` |
| T6 movements | Files as planned (`movements2015/{IATA}.csv.gz`, 50 MB cap). `db_seed.py movements2015` repacks them **per airport-day** into `geo.movements2015_day`. `load_movements(iata, date, from, to)` keeps its signature: one blob fetch, then filter. `split_flights2015.py`'s new taxi columns reach `ref.flights2015` via `db_seed.py flights2015` (the columns exist from D2, null until then) |
| T7 coverage | `geo_coverage.coverage()` unchanged; also loaded into `geo.coverage`. `test_ord_surface_rss_under_60mb` unchanged (FileStore), plus a `live_db` PgStore variant |
| T8 M0 verification | Add steps: `python3 scripts/db_seed.py --check` (exit 0); `python3 -m pytest -q -m live_db` (parity incl. geo, needs `DATABASE_TEST_URL`); `docker run` once with and once without `DATABASE_URL`; record `pg_database_size()` against §7 |
| T16 replay guard, T18 re-record replays | `replay.record` writes the file **and** `put_replay` (keyed by `engine_version`); the 409 guard reads `engine_version` from the stored body |
| T19 benchmark history | `benchmark.py` writes `eval/benchmark.json` as today and calls `put_benchmark(report, markdown, label)`; "freeze v2" = keep the v2 row with `is_current=false` |
| T35 docs | README section "Data: Postgres (Neon) with file fallback" |

## 16. Phasing (each milestone leaves the branch deployable, with files as the default until switched)

| Milestone | Scope | Done when |
|---|---|---|
| **D0 Foundation** | `backend/datastore` with `FileStore` = today's readers, rewired; `tests/conftest.py` forcing files; `migrations/` + `db_migrate.py`; `psycopg` dependency | 130 tests pass unchanged; (with the DB) migrations apply twice cleanly and the baseline `pg_database_size()` is recorded |
| **D1 Live state** | `live.runs`, `live.decisions`, `eval.*`; seed of replays, the 97 runs, benchmark, v1 eval; `PgStore` + `FallbackStore`; `Config.store`, `persisted`, `GET /api/runs?wave_id=` (`contract:` v2.1.1); `DATABASE_URL` on Render | a run and a decision survive a Render redeploy (manual check, recorded); fallback test green |
| **D2 Reference, part 1** | small tables, workbook, `ref.metar_2024`, `live.iem_cache`, `ref.flights2015`, `ref.waves` with precomputed summaries; parity tests | `live_db` parity green for the waves in §13; budgets in §10 measured from Render |
| **D3 Reference, part 2 + raw** | `ref.bts_arrivals_day`, `ref.disruptions_v1`; production on PgStore for every read; `raw.*` loaded last under the 750 MB guard | all reads come from Postgres in production (no FileStore hits in the log over a full demo click-through); `pg_database_size()` within 30 % of §7 or the estimate updated |
| **D4 Geo-ready** | `0005_geo.sql` (empty `geo.*` tables), `db_seed.py` geo datasets (skip when files are absent), `Store.procedures/surface/movements_day`; the edits to the flight-path plan (§15) | the flight-path plan can start at T1 |

## 17. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| 100 CU-hours run out (a loop, a crawler, heavy use) | compute suspended to month end; site on files, runs and decisions not persisted | fixed 0.25 CU; no timers or health checks on the DB; `Config.store` badge; usage visible in the Neon Console; Launch plan is the escape |
| Cold start after 5 idle minutes | the first request is ~0.5-1.5 s slower | `connect_timeout=10`, retries with jitter; small tables cached in-process after the first load |
| Size estimate off by ±30 %, or `challenge1` is large | storage grows towards 1 GB; writes fail at the limit | 750 MB guard; raw loaded last and dropped first; levers: lower the run/IEM caps |
| Bulk seed and history | history cap (1 GB / 6 h) reached by repeated re-seeds | one dataset per transaction; no repeated full re-seeds within 6 hours |
| Transaction pooling breaks session features | a `SET` or prepared statement fails at random | `prepare_threshold=None`; role-level settings; no `SET`, `LISTEN` or advisory locks in app code; migrations on the direct endpoint |
| Parity drift between FileStore and PgStore | a wave differs in prod vs tests | CSV text round-trip by design; `ord` ordering; parity tests on 45 waves plus listings |
| Restore window is only 6 hours | runs and decisions lost if deleted or corrupted | accepted for a demo (our call); `scripts/db_dump_live.py` exports `live.*` to JSONL on demand; reference data is re-seedable |
| `.env` URL leaks into a test run | tests hit the network, or write to prod | conftest strips the URLs; `live_db` uses `DATABASE_TEST_URL` on a branch |
| Neon Free limits change | limits shift | measured budgets; the design is plain Postgres, so another host works with a URL change |

## 18. Decisions made (all "our call")

1. Postgres is the runtime source. The committed files stay as the seed and the offline/test backend, and `data/` stays in the image as the fallback.
2. One module `backend/datastore/` (`Store` protocol, `PgStore`, `FileStore`, `FallbackStore`, dataset registry). The store returns source-format text, so the existing parse code runs on both backends. The plan's `backend/geo/store.py` delegates to it.
3. Sync psycopg 3.3.6 with a `NullConnectionPool` (no idle connections, so scale-to-zero works) on the Neon pooled endpoint, with `prepare_threshold=None`, `connect_timeout=10` and 2 retries with jitter.
4. Fallback Postgres → files → 503, with a circuit breaker, visible in an additive `Config.store`. `/api/config` never touches the DB, and nothing queries it on a timer.
5. The two bulk time series (BTS 2024 arrivals, 2015 movements) and the geometry documents are gzip `bytea`. Point features are PostGIS rows. Everything else is rows in the source's column representation, with an `ord` column. Raw challenge files are plain inferred-type tables in `raw`, loaded last and dropped first.
6. Least privilege: NOLOGIN groups `app_read`/`app_write` with default privileges; a SQL-created login `atm_app` (not `neon_superuser`) on Render; migrations and seeds as the owner from the dev machine only; no RLS.
7. Wave summaries are precomputed at seed time. `/api/flights` search is SQL `ILIKE` without `pg_trgm` unless the budget is missed.
8. Migrations are plain numbered SQL in `migrations/`, applied by `scripts/db_migrate.py`. The seeder is idempotent (source hash manifest), uses COPY into a shadow table plus an atomic swap, and has a 750 MB guard. Render never migrates.
9. Retention: 2,000 unpinned runs and 40 MB of IEM cache. Decided runs are pinned forever. Decisions are accepted even when their run is unknown. Run writes happen off the SSE path.
10. The contract change is additive, numbered v2.1.1 (`Config.store`, `persisted`, `GET /api/runs?wave_id=`). Tests run offline by default (conftest forces files), and the `live_db` marker runs against a Neon `test` branch.

## 19. What only the user can do

There is no open design decision (the layout was ruled). These are actions only the user can take, with
secrets the agents must never see:
1. Create the Neon project in **AWS US West 2 (Oregon)**, to match Render's default `oregon`. Set the compute
   to a fixed 0.25 CU.
2. Create the `atm_app` login role with the README's SQL snippet, choosing the password yourself.
3. Put `DATABASE_URL` (pooled, `atm_app`) and `DATABASE_OWNER_URL` (direct, owner) in `.env`. Put
   `DATABASE_URL` alone in the Render dashboard.
4. Optional, for `live_db` tests: create a branch `test` and put its direct owner URL in `.env` as
   `DATABASE_TEST_URL`.
5. Optional, for `raw.ch1_*`: run `scripts/fetch_data.sh` so `data/challenge1/*.csv` exist before seeding.

## 20. Sources

- Neon pricing (Free limits): https://neon.com/pricing
- Neon plans (storage measurement, history, limits behaviour): https://neon.com/docs/introduction/plans
- Scale to zero: https://neon.com/docs/introduction/scale-to-zero
- Compute lifecycle: https://neon.com/docs/introduction/compute-lifecycle
- Connection latency and timeouts: https://neon.com/docs/connect/connection-latency
- Connection pooling (PgBouncer transaction mode, direct vs pooled): https://neon.com/docs/connect/connection-pooling
- Roles (SQL vs Console, password entropy): https://neon.com/docs/manage/roles
- Regions: https://neon.com/docs/introduction/regions
- Neon with Render: https://neon.com/docs/guides/render
- Auto-suspend with long-running apps: https://neon.com/blog/using-neons-auto-suspend-with-long-running-applications
- Render Blueprint spec (region default `oregon`): https://render.com/docs/blueprint-spec
- Render regions: https://render.com/docs/regions
- psycopg 3 pool API (`NullConnectionPool`, defaults): https://www.psycopg.org/psycopg3/docs/api/pool.html
- psycopg 3 prepared statements behind poolers: https://www.psycopg.org/psycopg3/docs/advanced/prepare.html
