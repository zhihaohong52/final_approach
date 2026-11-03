-- Drop the PostGIS point copies, the coverage table and PostGIS itself: the app reads the procedures and surface documents only.
-- 0001 still creates postgis and 0005 the point tables, so a fresh database runs 0001..0006 and then drops them here.
drop table if exists geo.fixes;
drop table if exists geo.stands;
drop table if exists geo.osm_gates;
drop table if exists geo.holding_positions;
drop table if exists geo.coverage;
alter table ref.runways drop column if exists le_geog, drop column if exists he_geog;
drop extension if exists postgis;
delete from meta.seed_manifest where dataset = 'coverage';
