-- Schemas, PostGIS (in `extensions`) and the meta tables. Design §6, §6.8.
create schema if not exists ref;
create schema if not exists geo;
create schema if not exists live;
create schema if not exists eval;
create schema if not exists raw;
create schema if not exists meta;
create schema if not exists extensions;

create extension if not exists postgis with schema extensions;

create table if not exists meta.schema_migrations (version text primary key, name text, sha256 text not null,
  applied_at timestamptz default now());
create table if not exists meta.seed_manifest (dataset text primary key, source_sha256 text not null, script_version text,
  n_rows int8, bytes int8, loaded_at timestamptz default now());
