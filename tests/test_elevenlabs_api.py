"""Tests for call_api's retry on HTTP 504, run against a fake transport so nothing reaches the network."""

# Standard library
from collections.abc import Callable

# Third-party
import httpx
import pytest

# Local
from scripts import elevenlabs_api
from scripts.elevenlabs_api import HttpMethod, call_api

OK_BODY = {"ok": True}


# Utility functions
def install_fake_server(monkeypatch: pytest.MonkeyPatch, statuses: list[int]) -> list[httpx.Request]:
    """Answer requests with these statuses in order, without delays; return the list of requests received."""
    received: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        received.append(request)
        status = statuses[min(len(received), len(statuses)) - 1]
        return httpx.Response(status, json=OK_BODY if status == 200 else {"detail": "error"})

    monkeypatch.setattr(elevenlabs_api, "CLIENT", httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(answer)))
    monkeypatch.setattr(elevenlabs_api, "RETRY_DELAYS_SECS", (0, 0, 0))
    return received


def call_expecting_exit(call: Callable[[], object]) -> str:
    """Run a call that must stop the script, and return the message it stopped with."""
    with pytest.raises(SystemExit) as stopped:
        call()
    return str(stopped.value)


def test_get_retries_a_504_until_it_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow server answers 504 now and then; a read must ride over it instead of failing the run."""
    received = install_fake_server(monkeypatch, [504, 504, 200])
    reply = call_api(HttpMethod.GET, "/v1/thing")
    assert reply == OK_BODY, f"unexpected reply after retries: {reply}"
    assert len(received) == 3, f"expected 3 requests (2 retries), got {len(received)}"


def test_post_is_not_retried_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A POST that creates something may have succeeded before the 504, so a retry could duplicate it."""
    received = install_fake_server(monkeypatch, [504, 200])
    message = call_expecting_exit(lambda: call_api(HttpMethod.POST, "/v1/thing", {}))
    assert len(received) == 1, f"POST was retried {len(received) - 1} times"
    assert "504" in message, f"exit message does not name the status: {message}"


def test_post_marked_safe_to_repeat_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Starting an eval run is safe to repeat, and it is the call CI depends on most."""
    received = install_fake_server(monkeypatch, [504, 200])
    reply = call_api(HttpMethod.POST, "/v1/thing", {}, safe_to_repeat=True)
    assert reply == OK_BODY, f"unexpected reply after retry: {reply}"
    assert len(received) == 2, f"expected 2 requests (1 retry), got {len(received)}"


def test_other_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeating a 401 can't change the answer, so it must fail at once instead of waiting 14 s."""
    received = install_fake_server(monkeypatch, [401, 200])
    message = call_expecting_exit(lambda: call_api(HttpMethod.GET, "/v1/thing"))
    assert len(received) == 1, f"a 401 was retried {len(received) - 1} times"
    assert "401" in message, f"exit message does not name the status: {message}"


def test_persistent_504_stops_after_the_last_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that never recovers must end the run with its 504, not retry forever."""
    received = install_fake_server(monkeypatch, [504])
    message = call_expecting_exit(lambda: call_api(HttpMethod.GET, "/v1/thing"))
    expected_requests = 1 + len(elevenlabs_api.RETRY_DELAYS_SECS)
    assert len(received) == expected_requests, f"expected {expected_requests} requests, got {len(received)}"
    assert "504" in message, f"exit message does not name the status: {message}"
