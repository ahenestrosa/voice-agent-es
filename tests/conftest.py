"""Shared fixture: a disposable MySQL database whose schema the real Alembic migrations build."""

# Standard library
import os
from collections.abc import Iterator
from typing import NoReturn

# Third-party
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError

# Local
from backend.db import ROOT
from backend.tables import METADATA

# backend.db loaded ROOT/.env on import, so this is set when the developer configured it.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
TEST_DATABASE_SUFFIX = "_test"
# GitHub Actions sets CI=true; there a missing database is a broken build, not a laptop without Docker.
RUNNING_IN_CI = os.environ.get("CI") == "true"


# Utility functions
def skip_or_fail(reason: str) -> NoReturn:
    """Skip the database tests on a laptop, but fail them in CI, so a broken MySQL service can't pass the build."""
    if RUNNING_IN_CI:
        pytest.fail(reason)
    pytest.skip(reason)



def migrate_to_head(database_url: str) -> None:
    """Run every Alembic migration against this database."""
    config = Config(str(ROOT / "alembic.ini"))
    # configparser treats "%" as interpolation, and URL-encoded passwords contain it.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def drop_everything(engine: Engine) -> None:
    """Drop every table, including Alembic's version table, so the migrations start from nothing."""
    METADATA.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    """An engine on a freshly migrated `*_test` database, shared by the whole test run.

    Building the schema with the migrations rather than `create_all()` means a broken migration
    fails the tests, instead of being found on the next real deploy.
    """
    if not TEST_DATABASE_URL:
        skip_or_fail("set TEST_DATABASE_URL in .env to run the database tests (see .env.example)")
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    if not (engine.url.database or "").endswith(TEST_DATABASE_SUFFIX):
        pytest.fail(f"refusing to drop tables in {engine.url.database!r}: TEST_DATABASE_URL must name a *{TEST_DATABASE_SUFFIX} database")
    try:
        engine.connect().close()
    except OperationalError as error:
        skip_or_fail(f"MySQL is not reachable at {engine.url.render_as_string(hide_password=True)}; run `docker compose up -d` ({error.orig})")
    drop_everything(engine)
    migrate_to_head(TEST_DATABASE_URL)
    yield engine
    engine.dispose()
