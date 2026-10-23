"""One-time database setup done by hospital IT (DBA rights): the application role and database.

  meditrack   login; owns database "meditrack" and everything in it (the EHR's own tables)

Idempotent. Usage: DB_ADMIN_URL=postgresql://admin:...@host/postgres python -m meditrack.backend.bootstrap
"""
import os

import psycopg
from psycopg import sql


def main() -> None:
    admin_url = os.environ["DB_ADMIN_URL"]
    password = os.getenv("MEDITRACK_DB_PASSWORD", "meditrack")
    with psycopg.connect(admin_url, autocommit=True) as c:
        exists = c.execute("SELECT 1 FROM pg_roles WHERE rolname = 'meditrack'").fetchone()
        c.execute(sql.SQL(("ALTER" if exists else "CREATE") + " ROLE meditrack LOGIN PASSWORD {}").format(sql.Literal(password)))
        c.execute("ALTER ROLE meditrack RESET ALL")   # no leftover settings (e.g. a custom search_path) from earlier setups
        try:   # managed servers (Azure) give the admin a non-superuser role and need explicit membership
            c.execute(sql.SQL("GRANT meditrack TO {}").format(sql.Identifier(c.info.user)))
        except psycopg.Error:
            pass
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = 'meditrack'").fetchone():
            c.execute("CREATE DATABASE meditrack OWNER meditrack")
    print("MediTrack role and database ready")


if __name__ == "__main__":
    main()
