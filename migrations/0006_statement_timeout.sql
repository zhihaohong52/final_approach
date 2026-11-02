-- Design §8.3/§12: statement_timeout lives on the role, because SET does not survive transaction pooling (pgbouncer).
-- Applies to the role that runs this migration (the owner, which DATABASE_URL_POOLED uses until atm_app exists), in
-- this database only: new sessions get it, so a runaway query from the app is cancelled after 5 s. db_seed.py and
-- db_migrate.py lift it to 0 on their own connections. atm_app (README) gets the same setting when it is created.
do $$
begin
  execute format('alter role %I in database %I set statement_timeout = %L', current_user, current_database(), '5s');
end
$$;
