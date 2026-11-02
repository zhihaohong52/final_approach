-- Reference data (design §6.1-§6.3). Every table copied from a file has ord = row position in the file.

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

create table ref.metar_2024 (           -- data/ref/metar.csv as is
  ord int4 primary key, station text, airport text not null, valid_utc text not null,
  local_hour_key text not null, drct real, sknt real, gust real, vsby real, ceiling_ft real,
  wxcodes text, raw text);
create index on ref.metar_2024 (airport);
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
create table ref.disruptions_v1 (       -- the 20 columns of disruptions.csv, typed from its header, plus ord
  ord int4 primary key,
  id int4 not null, year int4, month int4, day int4, carrier text, tail text, flight_no int4,
  origin text, dest text, sched_dep int4, sched_arr int4, arr_delay float8, distance float8,
  weather_delay float8, nas_delay float8, late_aircraft_delay float8, dest_precip_mm float8,
  dest_wind_kmh float8, dest_gust_kmh float8, dest_condition float8,
  unique (id)
);
