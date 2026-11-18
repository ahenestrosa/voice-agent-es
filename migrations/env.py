"""Alembic environment: migrations run against DATABASE_URL and are generated from backend.tables.METADATA.

A caller (the test suite) can set `sqlalchemy.url` on the Alembic config to point at another database.
"""

# Standard library
from logging.config import fileConfig

# Third-party
from alembic import context
from sqlalchemy import create_engine, pool

# Local
from backend.db import DATABASE_URL
from backend.tables import METADATA

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url() -> str:
    """The URL set on the Alembic config, else DATABASE_URL; fail with what to set when both are empty."""
    if url := config.get_main_option("sqlalchemy.url") or DATABASE_URL:
        return url
    raise RuntimeError("DATABASE_URL is not set; add it to .env (see .env.example)")


def run_migrations_offline() -> None:
    """Print the migration SQL instead of running it (`alembic upgrade head --sql`)."""
    context.configure(url=database_url(), target_metadata=METADATA, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations on a live connection; NullPool because the process exits right after."""
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=METADATA)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
