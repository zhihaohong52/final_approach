-- Least privilege (design §12). Two NOLOGIN group roles; the login role atm_app is created by the user.
-- Nothing is granted on raw or meta.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'app_read') then
    create role app_read nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'app_write') then
    create role app_write nologin;
  end if;
end
$$;

-- app_read: usage + select everywhere the app reads.
grant usage on schema ref, geo, live, eval, extensions to app_read;
grant select on all tables in schema ref, geo, live, eval to app_read;

-- app_write: usage on the schemas it writes to (and extensions for the geography type), plus DML.
grant usage on schema live, eval, extensions to app_write;
grant insert, update, delete on all tables in schema live to app_write;
grant insert, update on all tables in schema eval to app_write;
grant usage, select on all sequences in schema live, eval to app_write;

-- Tables the owner creates later (including the seeder's <t>__new tables) inherit the same grants.
alter default privileges for role current_user in schema ref, geo, live, eval grant select on tables to app_read;
alter default privileges for role current_user in schema live grant insert, update, delete on tables to app_write;
alter default privileges for role current_user in schema eval grant insert, update on tables to app_write;
alter default privileges for role current_user in schema live, eval grant usage, select on sequences to app_write;
