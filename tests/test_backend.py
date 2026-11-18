"""Tests for the fake bank backend: the shared secret, Verification, per-Call Sessions and card blocking."""

# Standard library
from datetime import timedelta
from typing import Any

# Third-party
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update

# Local
from backend import app as bank
from backend import db as bank_db
from backend.demo_data import reset_demo_data
from backend.queries import utc_now
from backend.tables import card_blocks_t, cards_t, sessions_t

TEST_SECRET = "test-secret"
# Annotated as httpx.Client, which TestClient subclasses: this Starlette version hides its own types from Pyright.
CLIENT: httpx.Client = TestClient(bank.app)
CALL_ID = "conv_test_call_1"
OTHER_CALL_ID = "conv_test_call_2"
LUCIA = {"dni": "30123456", "card_last4": "4821"}
MARTIN = {"dni": "27987654", "card_last4": "1190"}


# Utility functions
def call_tool(tool: str, **body: Any) -> dict[str, Any]:
    """POST one tool call with the right secret, as part of CALL_ID unless the body names another Call."""
    return CLIENT.post(f"/tools/{tool}", json={"conversation_id": CALL_ID, **body}, headers={"X-Tool-Secret": TEST_SECRET}).json()


def open_session(identity: dict[str, str]) -> str:
    """Verify a demo customer during CALL_ID and return the session token."""
    return call_tool("verify_customer", **identity)["session_token"]


@pytest.fixture(autouse=True)
def fresh_bank(monkeypatch: pytest.MonkeyPatch, test_engine: Engine) -> None:
    """Point the backend at the test database and reload the demo data, so each test starts from the same bank."""
    monkeypatch.setattr(bank, "TOOL_SECRET", TEST_SECRET)
    monkeypatch.setattr(bank_db, "ENGINE", test_engine)
    with test_engine.begin() as conn:
        reset_demo_data(conn)


def test_request_without_secret_is_rejected() -> None:
    """Only the deployed agent, which sends the secret, may call the tools."""
    status = CLIENT.post("/tools/get_balance", json={"session_token": "x", "conversation_id": CALL_ID}).status_code
    assert status == 401, f"expected 401 without X-Tool-Secret, got {status}"


def test_unset_secret_rejects_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backend started without TOOL_SECRET must not accept requests that send an empty header."""
    monkeypatch.setattr(bank, "TOOL_SECRET", "")
    response = CLIENT.post("/tools/get_balance", json={"session_token": "x", "conversation_id": CALL_ID}, headers={"X-Tool-Secret": ""})
    assert response.status_code == 401, f"expected 401 with no secret configured, got {response.status_code}"


def test_spoken_dni_with_dots_verifies() -> None:
    """Speech-to-text writes the DNI with dots; that must still match the customer."""
    reply = call_tool("verify_customer", dni="30.123.456", card_last4="4821")
    assert reply["verified"] is True, f"DNI with dots did not verify: {reply}"


def test_card_of_another_customer_does_not_verify() -> None:
    """A real DNI plus someone else's card digits is not an identity."""
    reply = call_tool("verify_customer", dni=LUCIA["dni"], card_last4=MARTIN["card_last4"])
    assert reply["verified"] is False, f"mismatched DNI and card verified: {reply}"


def test_empty_conversation_id_is_rejected() -> None:
    """A Session bound to an empty Call ID would be shared by every Call, so verification must refuse it."""
    response = CLIENT.post("/tools/verify_customer", json={**LUCIA, "conversation_id": ""}, headers={"X-Tool-Secret": TEST_SECRET})
    assert response.status_code == 422, f"expected 422 for an empty conversation_id, got {response.status_code}"


def test_guessed_customer_id_reads_nothing() -> None:
    """Passing a customer ID as the session token must not open the account."""
    reply = call_tool("get_balance", session_token="1")
    assert "error" in reply, f"guessed customer ID read an account: {reply}"


def test_verified_session_reads_own_balance() -> None:
    """A token from verify_customer opens exactly that customer's account, as a plain JSON number."""
    reply = call_tool("get_balance", session_token=open_session(MARTIN))
    assert reply == {"balance_ars": 82_000.0}, f"unexpected balance reply: {reply}"


def test_session_from_another_call_reads_nothing() -> None:
    """A Session belongs to the Call that verified it; a token leaked into another Call opens nothing."""
    reply = call_tool("get_balance", session_token=open_session(LUCIA), conversation_id=OTHER_CALL_ID)
    assert "error" in reply, f"a token from another Call read the account: {reply}"


def test_expired_session_reads_nothing(test_engine: Engine) -> None:
    """Past the backstop lifetime a Session is dead, even inside its own Call."""
    session_token = open_session(LUCIA)
    with test_engine.begin() as conn:
        conn.execute(update(sessions_t).where(sessions_t.c.token == session_token).values(expires_at=utc_now() - timedelta(minutes=1)))
    reply = call_tool("get_balance", session_token=session_token)
    assert "error" in reply, f"an expired Session read the account: {reply}"


def test_transactions_come_newest_first_and_clamped() -> None:
    """The agent reads movements in order, and an oversized limit returns at most what exists."""
    reply = call_tool("list_transactions", session_token=open_session(LUCIA), limit=50)
    dates = [movement["date"] for movement in reply["transactions"]]
    assert dates == ["2026-11-07", "2026-11-06", "2026-11-05", "2026-11-04"], f"unexpected movement order: {dates}"


def test_cannot_block_another_customers_card() -> None:
    """A verified caller can only block their own cards."""
    reply = call_tool("block_card", session_token=open_session(LUCIA), card_last4=MARTIN["card_last4"], reason="lost")
    assert "error" in reply, f"blocked another customer's card: {reply}"


def test_unknown_block_reason_is_an_error_the_agent_can_read() -> None:
    """An invalid reason comes back as a 200 error message, not a 422 the agent cannot explain."""
    response = CLIENT.post(
        "/tools/block_card",
        json={"session_token": open_session(LUCIA), "card_last4": "4821", "reason": "me aburrí", "conversation_id": CALL_ID},
        headers={"X-Tool-Secret": TEST_SECRET},
    )
    assert response.status_code == 200, f"expected 200, got {response.status_code}"
    assert "error" in response.json(), f"invalid reason was accepted: {response.json()}"


def test_second_block_reports_already_blocked() -> None:
    """Blocking twice gives a case number once, then says the card was already blocked."""
    session_token = open_session(LUCIA)
    first = call_tool("block_card", session_token=session_token, card_last4="4821", reason="stolen")
    second = call_tool("block_card", session_token=session_token, card_last4="4821", reason="stolen")
    assert first["status"] == "blocked" and first["case_id"].startswith("BLQ-"), f"first block failed: {first}"
    assert second == {"status": "already_blocked"}, f"second block was not reported as repeated: {second}"


def test_block_is_stored_with_its_case(test_engine: Engine) -> None:
    """The block must survive a restart: the card stays blocked and its case is on record with the reason."""
    reply = call_tool("block_card", session_token=open_session(LUCIA), card_last4="4821", reason="stolen")
    with test_engine.connect() as conn:
        status = conn.execute(select(cards_t.c.status).where(cards_t.c.last4 == "4821")).scalar_one()
        reason = conn.execute(select(card_blocks_t.c.reason).where(card_blocks_t.c.case_id == reply["case_id"])).scalar_one()
    assert status == "blocked", f"card status not stored: {status}"
    assert reason == "stolen", f"case not stored with its reason: {reason}"
