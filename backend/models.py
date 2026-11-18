"""Value sets and database row shapes of the fake bank, shared by the schema, the queries and the endpoints."""

# Standard library
from datetime import date
from decimal import Decimal
from enum import StrEnum, auto
from typing import TypedDict


class CardKind(StrEnum):
    """Card type, with the Spanish word the agent says aloud."""

    DEBIT = "débito"
    CREDIT = "crédito"


class CardStatus(StrEnum):
    """Whether a card can still be used."""

    ACTIVE = auto()
    BLOCKED = auto()


class BlockReason(StrEnum):
    """Why the caller wants the card blocked; the same values as the enum in agent/tools.json."""

    LOST = auto()
    STOLEN = auto()
    FRAUD = auto()


class BlockOutcome(StrEnum):
    """What block_card did."""

    BLOCKED = auto()
    ALREADY_BLOCKED = auto()


class CustomerRow(TypedDict):
    """One row of `customers`."""

    id: int
    full_name: str
    dni: str
    balance_ars: Decimal


class CardRow(TypedDict):
    """One row of `cards`; `last4` is how the caller names the card."""

    id: int
    customer_id: int
    last4: str
    kind: CardKind
    status: CardStatus


class TransactionRow(TypedDict):
    """One row of `transactions`; negative amounts are charges."""

    id: int
    customer_id: int
    reference: str
    booked_on: date
    merchant: str
    amount_ars: Decimal
