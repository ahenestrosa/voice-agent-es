"""The fake bank's MySQL schema as SQLAlchemy Core tables; Alembic migrations are generated from METADATA."""

# Standard library
from enum import Enum

# Third-party
from sqlalchemy import (
    CHAR,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum

# Local
from backend.models import BlockReason, CardKind, CardStatus

# Explicit constraint names, so Alembic migrations name them the same on every database.
METADATA = MetaData(
    naming_convention={
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "uq": "uix_%(table_name)s_%(column_0_N_name)s",
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    },
)
# DECIMAL, not FLOAT: a float can't hold every cent exactly.
MONEY = Numeric(15, 2)


def enum_values(enum_type: type[Enum]) -> list[str]:
    """Store an enum by its values ("débito"), not its member names ("DEBIT")."""
    return [str(member.value) for member in enum_type]


def enum_column(enum_type: type[Enum], name: str) -> SqlEnum:
    """A MySQL ENUM column type whose values come from a Python enum."""
    return SqlEnum(enum_type, name=name, values_callable=enum_values)


customers_t = Table(
    "customers",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("full_name", String(120), nullable=False),
    Column("dni", String(10), nullable=False, unique=True),
    Column("balance_ars", MONEY, nullable=False),
)

cards_t = Table(
    "cards",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("customer_id", Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
    Column("last4", CHAR(4), nullable=False),
    Column("kind", enum_column(CardKind, "card_kind"), nullable=False),
    Column("status", enum_column(CardStatus, "card_status"), nullable=False),
    # Callers name a card by its last 4 digits, so they must identify one card per customer.
    UniqueConstraint("customer_id", "last4"),
)

transactions_t = Table(
    "transactions",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("customer_id", Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
    Column("reference", String(16), nullable=False, unique=True),
    Column("booked_on", Date, nullable=False),
    Column("merchant", String(120), nullable=False),
    Column("amount_ars", MONEY, nullable=False),
    Index(None, "customer_id", "booked_on"),
)

sessions_t = Table(
    "sessions",
    METADATA,
    Column("token", String(64), primary_key=True),
    Column("customer_id", Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
    # The ElevenLabs conversation ID: a Session is valid only inside the Call that verified it.
    Column("conversation_id", String(64), nullable=False, index=True),
    Column("created_at", DateTime, nullable=False),  # UTC
    Column("expires_at", DateTime, nullable=False),  # UTC
)

card_blocks_t = Table(
    "card_blocks",
    METADATA,
    Column("case_id", String(16), primary_key=True),
    Column("card_id", Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False),
    Column("reason", enum_column(BlockReason, "block_reason"), nullable=False),
    Column("blocked_at", DateTime, nullable=False),  # UTC
)
