"""Tests for the fake bank backend: the shared secret, identity verification and card blocking."""

# Standard library
import copy
from typing import Any

# Third-party
import httpx
import pytest
from fastapi.testclient import TestClient

# Local
from backend import app as bank

TEST_SECRET = "test-secret"
# Annotated as httpx.Client, which TestClient subclasses: this Starlette version hides its own types from Pyright.
CLIENT: httpx.Client = TestClient(bank.app)
LUCIA = {"dni": "30123456", "card_last4": "4821"}
MARTIN = {"dni": "27987654", "card_last4": "1190"}


# Utility functions
def call_tool(tool: str, **body: Any) -> dict[str, Any]:
    """POST one tool call with the right secret and return the JSON reply."""
    return CLIENT.post(f"/tools/{tool}", json=body, headers={"X-Tool-Secret": TEST_SECRET}).json()


def open_session(identity: dict[str, str]) -> str:
    """Verify a test customer and return the session token."""
    return call_tool("verify_customer", **identity)["session_token"]


@pytest.fixture(autouse=True)
def fresh_bank(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test its own customers, sessions and secret, so a card blocked in one test stays active in the next."""
    monkeypatch.setattr(bank, "TOOL_SECRET", TEST_SECRET)
    monkeypatch.setattr(bank, "CUSTOMERS", copy.deepcopy(bank.CUSTOMERS))
    monkeypatch.setattr(bank, "SESSIONS", {})


def test_request_without_secret_is_rejected() -> None:
    """Only the deployed agent, which sends the secret, may call the tools."""
    status = CLIENT.post("/tools/get_balance", json={"session_token": "x"}).status_code
    assert status == 401, f"expected 401 without X-Tool-Secret, got {status}"


def test_unset_secret_rejects_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backend started without TOOL_SECRET must not accept requests that send an empty header."""
    monkeypatch.setattr(bank, "TOOL_SECRET", "")
    status = CLIENT.post("/tools/get_balance", json={"session_token": "x"}, headers={"X-Tool-Secret": ""}).status_code
    assert status == 401, f"expected 401 with no secret configured, got {status}"


def test_spoken_dni_with_dots_verifies() -> None:
    """Speech-to-text writes the DNI with dots; that must still match the customer."""
    reply = call_tool("verify_customer", dni="30.123.456", card_last4="4821")
    assert reply["verified"] is True, f"DNI with dots did not verify: {reply}"


def test_card_of_another_customer_does_not_verify() -> None:
    """A real DNI plus someone else's card digits is not an identity."""
    reply = call_tool("verify_customer", dni=LUCIA["dni"], card_last4=MARTIN["card_last4"])
    assert reply["verified"] is False, f"mismatched DNI and card verified: {reply}"


def test_guessed_customer_id_reads_nothing() -> None:
    """Passing a customer ID as the session token must not open the account."""
    reply = call_tool("get_balance", session_token="c_002")
    assert "error" in reply, f"guessed customer ID read an account: {reply}"


def test_verified_session_reads_own_balance() -> None:
    """A token from verify_customer opens exactly that customer's account."""
    reply = call_tool("get_balance", session_token=open_session(MARTIN))
    assert reply == {"balance_ars": 82_000.00}, f"unexpected balance reply: {reply}"


def test_transaction_limit_is_clamped() -> None:
    """An oversized limit returns at most what exists, instead of an error."""
    reply = call_tool("list_transactions", session_token=open_session(MARTIN), limit=50)
    assert len(reply["transactions"]) == 1, f"expected Martín's single movement, got {reply}"


def test_cannot_block_another_customers_card() -> None:
    """A verified caller can only block their own cards."""
    reply = call_tool("block_card", session_token=open_session(LUCIA), card_last4=MARTIN["card_last4"], reason="lost")
    assert "error" in reply, f"blocked another customer's card: {reply}"


def test_unknown_block_reason_is_an_error_the_agent_can_read() -> None:
    """An invalid reason comes back as a 200 error message, not a 422 the agent cannot explain."""
    response = CLIENT.post(
        "/tools/block_card",
        json={"session_token": open_session(LUCIA), "card_last4": "4821", "reason": "me aburrí"},
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
