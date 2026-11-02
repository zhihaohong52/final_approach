-- Runtime writes (design §6.3 iem_cache, §6.4) and benchmark/evaluation (§6.5).

create table live.iem_cache (           -- one IEM response per station-day, exactly as fetched (2015 waves)
  station text not null, date date not null, fetched_at timestamptz not null default now(),
  body text,                            -- null = failed fetch (retried after 120 s, as today)
  primary key (station, date));

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
  run_id text,                                         -- v2 rows (no FK, see design §6.4)
  flight_id int4, plan_id text,                        -- v1 rows (web/index.html), kept as they come
  decision text not null check (decision in ('APPROVE', 'OVERRIDE')),
  note text not null default '',
  edits jsonb not null default '[]',                   -- Assignment[] the duty manager moved before approving
  recorded_at timestamptz not null default now(),
  check (run_id is not null or flight_id is not null),
  check (decision <> 'OVERRIDE' or note <> '')         -- the API already enforces this; the DB enforces it too
);
create index on live.decisions (run_id);

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
