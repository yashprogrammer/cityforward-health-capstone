"""Postgres access. Every request-scoped query goes through `session(principal)` so the database knows who is asking."""
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None
SCHEMA = Path(__file__).with_name("schema.sql")


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings().db_url, min_size=1, max_size=10, kwargs={"row_factory": dict_row, "autocommit": False}, open=True)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def init_schema() -> None:
    with pool().connection() as c:
        c.execute(SCHEMA.read_text())
        for extra in sorted(SCHEMA.parent.glob("schema_*.sql")):
            c.execute(extra.read_text())


@contextmanager
def session(principal=None):
    """A transaction. `app.*` settings tell row-level-security policies who is asking (set per transaction, never leaked to the next one)."""
    with pool().connection() as c:
        if principal is not None:
            c.execute("SELECT set_config('app.user', %s, true), set_config('app.role', %s, true), set_config('app.units', %s, true), set_config('app.patients', %s, true)",
                      (principal.user, principal.role, ",".join(principal.units), ",".join(principal.patients)))
        yield c


def vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


__all__ = ["pool", "session", "init_schema", "vec", "psycopg"]
