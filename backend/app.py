"""Fake retail bank exposing the tools the voice agent calls as webhooks.

Every endpoint is a POST under /tools/{name}, matching the webhook configs in agent/tools.json.
Errors come back as 200 with {"error": ...} so the agent can read the reason and react,
instead of the platform treating it as a failed tool call.

Account tools take an opaque session_token issued by verify_customer, never a customer id.
The prompt tells the agent to verify first, but the backend enforces it: a jailbroken agent
still cannot read an account the caller did not verify.
"""

# Standard library
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Annotated, NotRequired, TypedDict

# Third-party
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

load_dotenv()
TOOL_SECRET = os.environ.get("TOOL_SECRET", "")
LOG = logging.getLogger("bank")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# A spoken reply can only carry a few movements, so the agent's requested count is clamped to this range.
MIN_TRANSACTIONS = 1
MAX_TRANSACTIONS = 10
NOT_VERIFIED_MESSAGE = "sesión inválida, verificá la identidad primero"


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


class Transaction(TypedDict):
    """One account movement as the agent receives it; negative amounts are charges."""

    id: str
    date: str
    merchant: str
    amount_ars: float


@dataclass
class Card:
    """One of a customer's cards."""

    kind: CardKind
    status: CardStatus = CardStatus.ACTIVE


@dataclass
class Customer:
    """A bank customer and their account."""

    full_name: str
    dni: str
    cards: dict[str, Card]  # keyed by the card's last 4 digits
    balance_ars: float
    transactions: list[Transaction]  # newest first

    @property
    def first_name(self) -> str:
        """The name the agent greets the caller with."""
        return self.full_name.split()[0]


class VerifyRequest(BaseModel):
    """Identity data as the agent heard it."""

    dni: str
    card_last4: str


class SessionRequest(BaseModel):
    """A request from a verified call."""

    session_token: str


class TransactionsRequest(SessionRequest):
    """How many recent movements to list."""

    limit: int = 3


class BlockCardRequest(SessionRequest):
    """Which card to block, and why.

    `reason` stays a plain string here and is converted in the endpoint, so an unknown reason comes
    back as an error the agent can explain, instead of FastAPI's 422 that the agent cannot read.
    """

    card_last4: str
    reason: str


class ToolError(TypedDict):
    """A failure the agent reads out to the caller in its own words."""

    error: str


class VerifyResult(TypedDict):
    """Outcome of an identity check; a session token and first name only when it succeeded."""

    verified: bool
    session_token: NotRequired[str]
    first_name: NotRequired[str]
    error: NotRequired[str]


class BalanceResult(TypedDict):
    """The account balance."""

    balance_ars: float


class TransactionsResult(TypedDict):
    """The most recent movements, newest first."""

    transactions: list[Transaction]


class BlockResult(TypedDict):
    """What block_card did, with a case number when it blocked the card."""

    status: BlockOutcome
    case_id: NotRequired[str]


# ponytail: in-memory demo data and sessions; swap for a core-banking client and an expiring session store.
CUSTOMERS: dict[str, Customer] = {
    "c_001": Customer(
        full_name="Lucía Fernández",
        dni="30123456",
        cards={"4821": Card(CardKind.DEBIT)},
        balance_ars=1_254_300.50,
        transactions=[
            {"id": "t_9001", "date": "2026-11-07", "merchant": "Supermercado Día", "amount_ars": -38_450.00},
            {"id": "t_9002", "date": "2026-11-06", "merchant": "SUBE recarga", "amount_ars": -10_000.00},
            {"id": "t_9003", "date": "2026-11-05", "merchant": "Transferencia recibida", "amount_ars": 450_000.00},
            {"id": "t_9004", "date": "2026-11-04", "merchant": "MercadoLibre", "amount_ars": -129_999.00},
        ],
    ),
    "c_002": Customer(
        full_name="Martín Gómez",
        dni="27987654",
        cards={"1190": Card(CardKind.CREDIT)},
        balance_ars=82_000.00,
        transactions=[
            {"id": "t_8001", "date": "2026-11-08", "merchant": "YPF", "amount_ars": -45_000.00},
        ],
    ),
}
SESSIONS: dict[str, str] = {}  # session_token -> customer_id


def digits_only(spoken: str) -> str:
    """Keep only the digits, since speech-to-text writes a DNI as "30.123.456" or "30 123 456"."""
    return "".join(char for char in spoken if char.isdigit())


def find_customer_id(dni: str, card_last4: str) -> str | None:
    """Return the ID of the customer with this DNI who also holds a card ending in these digits."""
    return next(
        (customer_id for customer_id, customer in CUSTOMERS.items() if customer.dni == dni and card_last4 in customer.cards),
        None,
    )


def verified_customer(session_token: str) -> Customer | None:
    """Return the customer this session was issued for, or None for a token verify_customer never issued."""
    customer_id = SESSIONS.get(session_token)
    return CUSTOMERS.get(customer_id) if customer_id else None


def parse_block_reason(raw_reason: str) -> BlockReason | None:
    """Convert the agent's reason to a BlockReason, or None when it is not one of them."""
    try:
        return BlockReason(raw_reason)
    except ValueError:
        return None


def new_case_id() -> str:
    """A case number short enough to spell out over the phone, e.g. BLQ-7F3A21."""
    return f"BLQ-{secrets.token_hex(3).upper()}"


def require_tool_secret(x_tool_secret: Annotated[str, Header()] = "") -> None:
    """Reject requests that don't carry the shared secret, so only the deployed agent can call the tools.

    An unset TOOL_SECRET rejects everything: comparing against an empty secret would otherwise let a
    request with no header through.
    """
    if not TOOL_SECRET or not secrets.compare_digest(x_tool_secret, TOOL_SECRET):
        raise HTTPException(status_code=401, detail="missing or wrong X-Tool-Secret header")


app = FastAPI(title="Fake bank tools", dependencies=[Depends(require_tool_secret)])


@app.middleware("http")
async def log_latency(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Log each request's duration, since a slow tool shows up on the call as dead air."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    LOG.info("%s %s %d %.0fms", request.method, request.url.path, response.status_code, elapsed_ms)
    return response


# response_model=None: the return annotations type the code, but FastAPI must not filter the reply
# through them, because a union of result shapes could drop the keys of the shape it didn't pick.
@app.post("/tools/verify_customer", response_model=None)
def verify_customer(body: VerifyRequest) -> VerifyResult:
    """Check the DNI and card digits, and open a session for the account tools when they match."""
    if (customer_id := find_customer_id(digits_only(body.dni), digits_only(body.card_last4))) is None:
        return {"verified": False, "error": "DNI y tarjeta no coinciden"}
    session_token = secrets.token_urlsafe(16)
    SESSIONS[session_token] = customer_id
    return {"verified": True, "session_token": session_token, "first_name": CUSTOMERS[customer_id].first_name}


@app.post("/tools/get_balance", response_model=None)
def get_balance(body: SessionRequest) -> BalanceResult | ToolError:
    """Return the verified customer's balance."""
    if (customer := verified_customer(body.session_token)) is None:
        return {"error": NOT_VERIFIED_MESSAGE}
    return {"balance_ars": customer.balance_ars}


@app.post("/tools/list_transactions", response_model=None)
def list_transactions(body: TransactionsRequest) -> TransactionsResult | ToolError:
    """Return the verified customer's most recent movements."""
    if (customer := verified_customer(body.session_token)) is None:
        return {"error": NOT_VERIFIED_MESSAGE}
    count = max(MIN_TRANSACTIONS, min(body.limit, MAX_TRANSACTIONS))
    return {"transactions": customer.transactions[:count]}


@app.post("/tools/block_card", response_model=None)
def block_card(body: BlockCardRequest) -> BlockResult | ToolError:
    """Block one of the verified customer's cards; blocking an already-blocked card is reported, not repeated."""
    if (customer := verified_customer(body.session_token)) is None:
        return {"error": NOT_VERIFIED_MESSAGE}
    card_last4 = digits_only(body.card_last4)
    if (card := customer.cards.get(card_last4)) is None:
        return {"error": f"la tarjeta terminada en {card_last4} no es del cliente"}
    if (reason := parse_block_reason(body.reason)) is None:
        return {"error": f"motivo inválido {body.reason!r}, usar uno de: {', '.join(BlockReason)}"}
    if card.status == CardStatus.BLOCKED:
        return {"status": BlockOutcome.ALREADY_BLOCKED}
    card.status = CardStatus.BLOCKED
    case_id = new_case_id()
    # Audit line for the one irreversible action; the DNI stays out of the log.
    LOG.info("blocked card *%s reason=%s case=%s", card_last4, reason, case_id)
    return {"status": BlockOutcome.BLOCKED, "case_id": case_id}
