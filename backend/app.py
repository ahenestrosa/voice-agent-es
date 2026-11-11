"""Fake retail bank exposing the tools the voice agent calls as webhooks.

Every endpoint is a POST under /tools/{name}, matching the webhook configs in agent/tools.json.
Errors come back as 200 with {"error": ...} so the agent can read the reason and react,
instead of the platform treating it as a failed tool call.

Account tools take an opaque session_token issued by verify_customer, never a customer id.
The prompt tells the agent to verify first, but the backend enforces it: a jailbroken agent
still cannot read an account the caller did not verify.
"""

import logging
import os
import secrets
import time

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

load_dotenv()
log = logging.getLogger("bank")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# ponytail: in-memory demo data and sessions, swap for a real core-banking client + expiring session store
CUSTOMERS = {
    "c_001": {
        "name": "Lucía Fernández",
        "dni": "30123456",
        "cards": {"4821": {"status": "active", "type": "débito"}},
        "balance_ars": 1_254_300.50,
        "transactions": [
            {"id": "t_9001", "date": "2026-11-07", "merchant": "Supermercado Día", "amount_ars": -38_450.00},
            {"id": "t_9002", "date": "2026-11-06", "merchant": "SUBE recarga", "amount_ars": -10_000.00},
            {"id": "t_9003", "date": "2026-11-05", "merchant": "Transferencia recibida", "amount_ars": 450_000.00},
            {"id": "t_9004", "date": "2026-11-04", "merchant": "MercadoLibre", "amount_ars": -129_999.00},
        ],
    },
    "c_002": {
        "name": "Martín Gómez",
        "dni": "27987654",
        "cards": {"1190": {"status": "active", "type": "crédito"}},
        "balance_ars": 82_000.00,
        "transactions": [
            {"id": "t_8001", "date": "2026-11-08", "merchant": "YPF", "amount_ars": -45_000.00},
        ],
    },
}
SESSIONS: dict[str, str] = {}  # session_token -> customer_id
BLOCK_REASONS = {"lost", "stolen", "fraud"}
NOT_VERIFIED = {"error": "sesión inválida, verificá la identidad primero"}

app = FastAPI(title="Fake bank tools")


def check_secret(x_tool_secret: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_tool_secret, os.environ.get("TOOL_SECRET", "")):
        raise HTTPException(status_code=401, detail="bad tool secret")


@app.middleware("http")
async def log_latency(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    log.info("%s %s %d %.0fms", request.method, request.url.path, response.status_code, (time.perf_counter() - start) * 1000)
    return response


class VerifyIn(BaseModel):
    dni: str
    card_last4: str


class SessionIn(BaseModel):
    session_token: str


class TransactionsIn(SessionIn):
    limit: int = 3


class BlockCardIn(SessionIn):
    card_last4: str
    reason: str


def customer_for(token: str) -> dict | None:
    cid = SESSIONS.get(token)
    return CUSTOMERS.get(cid) if cid else None


@app.post("/tools/verify_customer", dependencies=[Depends(check_secret)])
def verify_customer(body: VerifyIn):
    dni = "".join(ch for ch in body.dni if ch.isdigit())  # speech-to-text often yields "30.123.456" or "30 123 456"
    last4 = "".join(ch for ch in body.card_last4 if ch.isdigit())
    for cid, c in CUSTOMERS.items():
        if c["dni"] == dni and last4 in c["cards"]:
            token = secrets.token_urlsafe(16)
            SESSIONS[token] = cid
            return {"verified": True, "session_token": token, "first_name": c["name"].split()[0]}
    return {"verified": False, "error": "DNI y tarjeta no coinciden"}


@app.post("/tools/get_balance", dependencies=[Depends(check_secret)])
def get_balance(body: SessionIn):
    c = customer_for(body.session_token)
    return {"balance_ars": c["balance_ars"]} if c else NOT_VERIFIED


@app.post("/tools/list_transactions", dependencies=[Depends(check_secret)])
def list_transactions(body: TransactionsIn):
    c = customer_for(body.session_token)
    return {"transactions": c["transactions"][: max(1, min(body.limit, 10))]} if c else NOT_VERIFIED


@app.post("/tools/block_card", dependencies=[Depends(check_secret)])
def block_card(body: BlockCardIn):
    c = customer_for(body.session_token)
    if not c:
        return NOT_VERIFIED
    card = c["cards"].get(body.card_last4)
    if not card:
        return {"error": "la tarjeta no pertenece al cliente"}
    if body.reason not in BLOCK_REASONS:
        return {"error": f"motivo inválido, usar uno de {sorted(BLOCK_REASONS)}"}
    if card["status"] == "blocked":
        return {"status": "already_blocked"}
    card["status"] = "blocked"
    return {"status": "blocked", "case_id": f"BLQ-{secrets.token_hex(3).upper()}"}
