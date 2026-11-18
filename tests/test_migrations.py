"""Tests that the Alembic migrations and backend/tables.py describe the same schema."""

# Third-party
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine

# Local
from backend.tables import METADATA


def test_migrations_match_the_tables(test_engine: Engine) -> None:
    """A table change without a matching migration would only break on the next deploy; catch it here."""
    with test_engine.connect() as conn:
        differences = compare_metadata(MigrationContext.configure(conn), METADATA)
    assert differences == [], f"tables.py and migrations differ; run `uv run alembic revision --autogenerate`: {differences}"
