import os

os.environ["TOOL_SECRET"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402

client = TestClient(app)
H = {"X-Tool-Secret": "test-secret"}


def call(tool: str, **body):
    return client.post(f"/tools/{tool}", json=body, headers=H).json()


def test_rejects_missing_secret():
    assert client.post("/tools/get_balance", json={"session_token": "x"}).status_code == 401


def test_verify_normalizes_spoken_dni_and_wrong_card_fails():
    assert call("verify_customer", dni="30.123.456", card_last4="4821")["verified"] is True
    assert call("verify_customer", dni="30123456", card_last4="1190")["verified"] is False


def test_account_tools_require_session_from_verify():
    assert "error" in call("get_balance", session_token="c_002")  # guessing a customer id gets nothing
    token = call("verify_customer", dni="27987654", card_last4="1190")["session_token"]
    assert call("get_balance", session_token=token) == {"balance_ars": 82_000.00}
    assert len(call("list_transactions", session_token=token, limit=50)["transactions"]) == 1


def test_block_card_flow():
    token = call("verify_customer", dni="30123456", card_last4="4821")["session_token"]
    assert "error" in call("block_card", session_token=token, card_last4="1190", reason="lost")  # someone else's card
    assert "error" in call("block_card", session_token=token, card_last4="4821", reason="me aburrí")
    assert call("block_card", session_token=token, card_last4="4821", reason="lost")["status"] == "blocked"
    assert call("block_card", session_token=token, card_last4="4821", reason="lost")["status"] == "already_blocked"
