"""Fake retail bank exposing the tools the voice agent calls as webhooks.

Every endpoint is a POST under /tools/{name}, matching the webhook configs in agent/tools.json.
Errors come back as 200 with {"error": ...} so the agent can read the reason and react,
instead of the platform treating it as a failed tool call.

Account tools take an opaque session_token issued by verify_customer, never a customer id.
The prompt tells the agent to verify first, but the backend enforces it: a jailbroken agent
still cannot read an account the caller did not verify. A Session is also bound to the Call
that opened it, through the conversation_id ElevenLabs fills into every request.
"""

# Standard library
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, NotRequired, TypedDict

# Third-party
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

# Local
from backend.db import read_connection, transaction
from backend.models import BlockOutcome, BlockReason, CardStatus, TransactionRow
from backend.queries import (
    find_verified_customer,
    lock_card,
    open_session,
    recent_transactions,
    record_block,
    session_customer,
    utc_now,
)

# backend.db already loaded ROOT/.env, so the secret is in the environment by now.
TOOL_SECRET = os.environ.get("TOOL_SECRET", "")
LOG = logging.getLogger("bank")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# A spoken reply can only carry a few movements, so the agent's requested count is clamped to this range.
MIN_TRANSACTIONS = 1
MAX_TRANSACTIONS = 10
NOT_VERIFIED_MESSAGE = "sesión inválida, verificá la identidad primero"


class CallRequest(BaseModel):
    """Fields every tool call carries.

    ElevenLabs fills `conversation_id` from `system__conversation_id`; the LLM never sees it.
    An empty value is rejected, because a Session bound to "" would be shared by every Call.
    """

    conversation_id: str = Field(min_length=1)


class VerifyRequest(CallRequest):
    """Identity data as the agent heard it."""

    dni: str
    card_last4: str


class SessionRequest(CallRequest):
    """A request from a verified Call."""

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


class SpokenTransaction(TypedDict):
    """One movement as the agent receives it; negative amounts are charges."""

    id: str
    date: str
    merchant: str
    amount_ars: float


class TransactionsResult(TypedDict):
    """The most recent movements, newest first."""

    transactions: list[SpokenTransaction]


class BlockResult(TypedDict):
    """What block_card did, with a case number when it blocked the card."""

    status: BlockOutcome
    case_id: NotRequired[str]


def digits_only(spoken: str) -> str:
    """Keep only the digits, since speech-to-text writes a DNI as "30.123.456" or "30 123 456"."""
    return "".join(char for char in spoken if char.isdigit())


def parse_block_reason(raw_reason: str) -> BlockReason | None:
    """Convert the agent's reason to a BlockReason, or None when it is not one of them."""
    try:
        return BlockReason(raw_reason)
    except ValueError:
        return None


def new_case_id() -> str:
    """A case number short enough to spell out over the phone, e.g. BLQ-7F3A21."""
    return f"BLQ-{secrets.token_hex(3).upper()}"


def to_spoken_transaction(row: TransactionRow) -> SpokenTransaction:
    """Turn a stored movement into the JSON the agent reads: an ISO date and a plain number amount."""
    return {
        "id": row["reference"],
        "date": row["booked_on"].isoformat(),
        "merchant": row["merchant"],
        "amount_ars": float(row["amount_ars"]),
    }


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
    """Check the DNI and card digits, and open a Session for this Call when they match."""
    with transaction() as conn:
        if (customer := find_verified_customer(conn, digits_only(body.dni), digits_only(body.card_last4))) is None:
            return {"verified": False, "error": "DNI y tarjeta no coinciden"}
        session_token = open_session(conn, customer["id"], body.conversation_id, utc_now())
    return {"verified": True, "session_token": session_token, "first_name": customer["full_name"].split()[0]}


@app.post("/tools/get_balance", response_model=None)
def get_balance(body: SessionRequest) -> BalanceResult | ToolError:
    """Return the verified customer's balance."""
    with read_connection() as conn:
        customer = session_customer(conn, body.session_token, body.conversation_id, utc_now())
    if customer is None:
        return {"error": NOT_VERIFIED_MESSAGE}
    return {"balance_ars": float(customer["balance_ars"])}


@app.post("/tools/list_transactions", response_model=None)
def list_transactions(body: TransactionsRequest) -> TransactionsResult | ToolError:
    """Return the verified customer's most recent movements."""
    count = max(MIN_TRANSACTIONS, min(body.limit, MAX_TRANSACTIONS))
    with read_connection() as conn:
        if (customer := session_customer(conn, body.session_token, body.conversation_id, utc_now())) is None:
            return {"error": NOT_VERIFIED_MESSAGE}
        rows = recent_transactions(conn, customer["id"], count)
    return {"transactions": [to_spoken_transaction(row) for row in rows]}


@app.post("/tools/block_card", response_model=None)
def block_card(body: BlockCardRequest) -> BlockResult | ToolError:
    """Block one of the verified customer's cards; blocking an already-blocked card is reported, not repeated.

    Everything runs in one transaction with the card row locked, so the checks and the block
    can't be split by a second request for the same card.
    """
    card_last4 = digits_only(body.card_last4)
    with transaction() as conn:
        now = utc_now()
        if (customer := session_customer(conn, body.session_token, body.conversation_id, now)) is None:
            return {"error": NOT_VERIFIED_MESSAGE}
        if (card := lock_card(conn, customer["id"], card_last4)) is None:
            return {"error": f"la tarjeta terminada en {card_last4} no es del cliente"}
        if (reason := parse_block_reason(body.reason)) is None:
            return {"error": f"motivo inválido {body.reason!r}, usar uno de: {', '.join(BlockReason)}"}
        if card["status"] == CardStatus.BLOCKED:
            return {"status": BlockOutcome.ALREADY_BLOCKED}
        case_id = new_case_id()
        record_block(conn, card["id"], reason, case_id, now)
    # Audit line for the one irreversible action; the DNI stays out of the log.
    LOG.info("blocked card *%s reason=%s case=%s", card_last4, reason, case_id)
    return {"status": BlockOutcome.BLOCKED, "case_id": case_id}
