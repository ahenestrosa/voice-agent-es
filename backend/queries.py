"""The SQL the tools run. Each function takes the caller's connection, so the caller owns the transaction."""

# Standard library
import secrets
from datetime import UTC, datetime, timedelta

# Third-party
from sqlalchemy import Connection, insert, select, update

# Local
from backend.db import all_rows, first_row
from backend.models import BlockReason, CardRow, CardStatus, CustomerRow, TransactionRow
from backend.tables import card_blocks_t, cards_t, customers_t, sessions_t, transactions_t

# Backstop only: a Session already ends with its Call, but the backend never hears the hang-up.
SESSION_LIFETIME = timedelta(minutes=30)


def utc_now() -> datetime:
    """Current UTC time without tzinfo, the form the DATETIME columns store."""
    return datetime.now(UTC).replace(tzinfo=None)


def find_verified_customer(conn: Connection, dni: str, card_last4: str) -> CustomerRow | None:
    """Return the customer with this DNI who also holds a card ending in these digits."""
    statement = (
        select(customers_t)
        .join(cards_t, cards_t.c.customer_id == customers_t.c.id)
        .where(customers_t.c.dni == dni, cards_t.c.last4 == card_last4)
    )
    return first_row(conn, statement, CustomerRow)


def open_session(conn: Connection, customer_id: int, conversation_id: str, now: datetime) -> str:
    """Store a new Session for this customer and Call, and return its token."""
    session_token = secrets.token_urlsafe(32)
    statement = insert(sessions_t).values(
        token=session_token,
        customer_id=customer_id,
        conversation_id=conversation_id,
        created_at=now,
        expires_at=now + SESSION_LIFETIME,
    )
    conn.execute(statement)
    return session_token


def session_customer(conn: Connection, session_token: str, conversation_id: str, now: datetime) -> CustomerRow | None:
    """Return the customer of a live Session, or None for an unknown token, another Call or an expired Session.

    Matching the conversation ID as well as the token is what makes a Session per Call: a token
    copied out of one Call's transcript opens nothing in any other Call.
    """
    statement = (
        select(customers_t)
        .join(sessions_t, sessions_t.c.customer_id == customers_t.c.id)
        .where(
            sessions_t.c.token == session_token,
            sessions_t.c.conversation_id == conversation_id,
            sessions_t.c.expires_at > now,
        )
    )
    return first_row(conn, statement, CustomerRow)


def recent_transactions(conn: Connection, customer_id: int, count: int) -> list[TransactionRow]:
    """Return the customer's latest movements, newest first."""
    statement = (
        select(transactions_t)
        .where(transactions_t.c.customer_id == customer_id)
        .order_by(transactions_t.c.booked_on.desc(), transactions_t.c.id.desc())
        .limit(count)
    )
    return all_rows(conn, statement, TransactionRow)


def lock_card(conn: Connection, customer_id: int, card_last4: str) -> CardRow | None:
    """Return the customer's card ending in these digits, locked until the transaction ends.

    `FOR UPDATE` makes a second, simultaneous block of the same card wait here, then read
    `blocked`, so one card can never get two case numbers.
    """
    statement = (
        select(cards_t)
        .where(cards_t.c.customer_id == customer_id, cards_t.c.last4 == card_last4)
        .with_for_update()
    )
    return first_row(conn, statement, CardRow)


def record_block(conn: Connection, card_id: int, reason: BlockReason, case_id: str, now: datetime) -> None:
    """Mark the card blocked and store the case that blocked it."""
    conn.execute(update(cards_t).where(cards_t.c.id == card_id).values(status=CardStatus.BLOCKED))
    conn.execute(insert(card_blocks_t).values(case_id=case_id, card_id=card_id, reason=reason, blocked_at=now))
