-- Flight-path geometry (design §6.7). Created empty; filled by the flight-path M0 data build.
-- Documents are gzip bytea, byte-identical to data/ref/**/*.json.gz; point features are also PostGIS rows.

create table geo.procedures (           -- one per ICAO: procedures document
  icao text primary key, cycle text not null, tier text not null,   -- 'star+iap' | 'iap' | 'none'
  doc bytea not null, sha256 text not null, loaded_at timestamptz default now());

create table geo.fixes (icao text, ident text, geog extensions.geography(Point, 4326),
  primary key (icao, ident));

create table geo.surface (              -- one per ICAO: surface document
  icao text primary key, osm_timestamp text, doc bytea not null, sha256 text not null,
  reach_pct jsonb, loaded_at timestamptz default now());

create table geo.stands (icao text, id text, ref text, node int4, geog extensions.geography(Point, 4326),
  primary key (icao, id));

create table geo.osm_gates (icao text, ref text, geog extensions.geography(Point, 4326),
  primary key (icao, ref));

create table geo.holding_positions (icao text, node int4, strip text, geog extensions.geography(Point, 4326),
  primary key (icao, node));

create table geo.movements2015_day (    -- 2015 movements, packed per airport-day
  airport text, date date, n_rows int4 not null, payload bytea not null, sha256 text not null,
  primary key (airport, date));

create table geo.coverage (iata text primary key, icao text, procedures_tier text, surface_tier text,
  stands int4, movements bool);

create index on geo.stands using gist (geog);
create index on geo.osm_gates using gist (geog);
