import os
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = Path(__file__).resolve().parent / "schema.sql"

# PostgreSQL (stands in for the hospital's Oracle). The app logs in as its owner role "meditrack".
DB_URL = os.getenv("MEDITRACK_DB_URL", "postgresql://meditrack:meditrack@localhost:5432/meditrack")
# File stores (SMB shares in the hospital):
#   documents        - scanned + uploaded patient documents
#   import_hotfolder - batch document import; other systems may DROP files here
FILES_DIR = ROOT / "data"
DOCS_DIR = Path(os.getenv("MEDITRACK_DOCS_DIR", FILES_DIR / "documents"))
HOTFOLDER = Path(os.getenv("MEDITRACK_HOTFOLDER", FILES_DIR / "import_hotfolder"))


def upper_row(cursor):
    """Rows as dicts keyed by UPPERCASE column names, as the vendor's code expects (Postgres folds names to lowercase)."""
    names = [d.name.upper() for d in cursor.description] if cursor.description else []
    return lambda values: dict(zip(names, values))


_pool: ConnectionPool | None = None


def connect():
    """Pooled connection; use as `with connect() as c:` (commits on success, rolls back on error)."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DB_URL, min_size=1, max_size=8, open=True, check=ConnectionPool.check_connection,
                               kwargs={"row_factory": upper_row, "autocommit": False})
    return _pool.connection()


def connect_once() -> psycopg.Connection:
    """Plain, unpooled connection (seed / ETL / one-off scripts)."""
    return psycopg.connect(DB_URL, row_factory=upper_row)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def rows(conn, sql: str, *args) -> list[dict]:
    return conn.execute(sql, args).fetchall()


def row(conn, sql: str, *args) -> dict | None:
    return conn.execute(sql, args).fetchone()


def audit(conn, usr: str, actn: str, ref: str) -> None:
    conn.execute("INSERT INTO SYS_AUDIT (TS, USR, ACTN, REF) VALUES (%s,%s,%s,%s)", (now(), usr, actn, ref))
