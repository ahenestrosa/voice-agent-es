"""The MySQL engine, the two connection scopes the endpoints use, and typed row reads."""

# Standard library
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar, cast

# Third-party
from dotenv import load_dotenv
from sqlalchemy import Connection, Engine, Executable, create_engine

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# One engine for the server's lifetime. pre_ping replaces connections MySQL closed while idle;
# recycle keeps every connection younger than MySQL's default 8-hour wait_timeout.
ENGINE: Engine | None = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600) if DATABASE_URL else None

RowT = TypeVar("RowT")


def require_engine() -> Engine:
    """Return the engine, or fail with what to set when DATABASE_URL is missing."""
    if ENGINE is None:
        raise RuntimeError(f"DATABASE_URL is not set; add it to {ROOT / '.env'} (see .env.example)")
    return ENGINE


@contextmanager
def read_connection() -> Generator[Connection]:
    """A connection for reads only; it is returned to the pool on exit."""
    with require_engine().connect() as conn:
        yield conn


@contextmanager
def transaction() -> Generator[Connection]:
    """A connection inside one transaction: it commits when the block ends and rolls back on an exception."""
    with require_engine().begin() as conn:
        yield conn


def first_row(conn: Connection, statement: Executable, _row_type: type[RowT]) -> RowT | None:
    """Run the statement and return its first row as `_row_type`, or None when there is no row."""
    row = conn.execute(statement).mappings().first()
    return cast(RowT, dict(row)) if row is not None else None


def all_rows(conn: Connection, statement: Executable, _row_type: type[RowT]) -> list[RowT]:
    """Run the statement and return every row as `_row_type`."""
    return [cast(RowT, dict(row)) for row in conn.execute(statement).mappings()]
