"""Apply migrations/NNNN_<name>.sql to Postgres as the owner role.

    python3 scripts/db_migrate.py [--dry-run] [--url-env NAME] [--write-checksums]

Reads the connection URL from $DATABASE_URL (direct, owner) or the variable named by --url-env. Each file runs in its
own transaction and is recorded in meta.schema_migrations. Applied files are append-only: a changed sha256 aborts.
The URL is never printed. Exit codes: 0 ok, 2 URL not set, 3 an applied migration changed.
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atm  # noqa: E402,F401  (loads .env)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migration_files(dir: Path = MIGRATIONS) -> list[Path]:
    return sorted(dir.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def checksums(dir: Path) -> dict[str, str]:
    """{filename: sha256} for every migration file in `dir`."""
    return {p.name: _sha(p) for p in migration_files(dir)}


def _version(path: Path) -> str:
    return path.name.split("_", 1)[0]


class ChangedMigration(Exception):
    pass


def apply(conn, files: list[Path], dry_run: bool) -> list[str]:
    """Apply every file whose version is not in meta.schema_migrations, in sorted order. Returns versions applied
    (or, with dry_run, the versions that would be). Raises ChangedMigration if an applied file's sha256 differs."""
    with conn.cursor() as cur:
        cur.execute("select to_regclass('meta.schema_migrations')")
        has_table = cur.fetchone()[0] is not None
        done: dict[str, str] = {}
        if has_table:
            cur.execute("select version, sha256 from meta.schema_migrations")
            done = dict(cur.fetchall())
    conn.commit()
    for f in files:
        if _version(f) in done and done[_version(f)] != _sha(f):
            raise ChangedMigration(f.name)
    applied: list[str] = []
    for f in sorted(files):
        v = _version(f)
        if v in done:
            continue
        if not dry_run:
            with conn.transaction():
                conn.execute(f.read_text())
                conn.execute("insert into meta.schema_migrations (version, name, sha256) values (%s, %s, %s)",
                             (v, f.name, _sha(f)))
        applied.append(v)
    return applied


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="list pending migrations without applying them")
    ap.add_argument("--url-env", default="DATABASE_URL", help="environment variable holding the connection URL")
    ap.add_argument("--write-checksums", action="store_true", help="regenerate migrations/CHECKSUMS (no database)")
    args = ap.parse_args(argv)
    if args.write_checksums:
        lines = [f"{h}  {n}\n" for n, h in checksums(MIGRATIONS).items()]
        (MIGRATIONS / "CHECKSUMS").write_text("".join(lines))
        print(f"wrote {len(lines)} checksums")
        return 0
    url = os.environ.get(args.url_env)
    if not url:
        print(f"{args.url_env} not set", file=sys.stderr)
        return 2
    import psycopg
    try:
        with psycopg.connect(url, autocommit=False) as conn:
            applied = apply(conn, migration_files(), args.dry_run)
    except ChangedMigration as e:
        print(f"migration {e} changed after it was applied; migrations are append-only", file=sys.stderr)
        return 3
    except psycopg.Error as e:   # never echo the URL; the SQLSTATE and message are enough
        print(f"database error ({e.sqlstate or 'no sqlstate'}): {str(e).splitlines()[0] if str(e) else type(e).__name__}",
              file=sys.stderr)
        return 1
    if applied:
        print(("would apply: " if args.dry_run else "applied: ") + repr(applied))
    else:
        print("up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
