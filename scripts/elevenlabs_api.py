"""ElevenLabs REST helper, and readers for the CLI registries that hold the deployed agent and tool IDs."""

# Standard library
import json
import os
import time
from dataclasses import dataclass
from http import HTTPStatus
from enum import StrEnum
from pathlib import Path
from typing import Any, TypedDict, cast

# Third-party
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# Written by `elevenlabs agents pull/push` and `elevenlabs tools pull/push`; they map configs to remote IDs.
AGENTS_REGISTRY_PATH = ROOT / "agents.json"
TOOLS_REGISTRY_PATH = ROOT / "tools.json"

load_dotenv(ROOT / ".env")
CLIENT = httpx.Client(
    base_url="https://api.elevenlabs.io",
    headers={"xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "")},
    # Syncing a test can take several seconds; httpx's 5 s default times out.
    timeout=60,
)

JsonObject = dict[str, Any]

# Statuses that mean "try again shortly". ElevenLabs answers 504 when a request outlives its
# server-side timeout (enforced from 2026-11-26); the same request usually succeeds moments later.
RETRYABLE_STATUSES = frozenset({HTTPStatus.GATEWAY_TIMEOUT})
# Seconds to wait before each retry; doubling gives the server room, and 14 s in total stays
# well inside a CI job.
RETRY_DELAYS_SECS = (2, 4, 8)


class HttpMethod(StrEnum):
    """The HTTP verbs the ElevenLabs endpoints used here accept."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"


# Sending these twice leaves the same result as sending them once, so a retry can't duplicate anything.
IDEMPOTENT_METHODS = frozenset({HttpMethod.GET, HttpMethod.PUT, HttpMethod.PATCH})


class AgentEntry(TypedDict):
    """One agent in agents.json, as the CLI writes it."""

    config: str
    id: str


class ToolEntry(TypedDict):
    """One tool in tools.json, as the CLI writes it; the tool's name lives in its config file."""

    type: str
    config: str
    id: str


@dataclass(frozen=True)
class Resource:
    """Where one kind of ElevenLabs resource is created and updated; each endpoint family differs."""

    create_path: str
    update_path: str  # contains an {id} placeholder
    update_method: HttpMethod
    id_key: str  # the key holding the new ID in the create response


TEST = Resource("/v1/convai/agent-testing/create", "/v1/convai/agent-testing/{id}", HttpMethod.PUT, "id")


def call_api(method: HttpMethod, path: str, body: JsonObject | None = None, safe_to_repeat: bool = False) -> JsonObject:
    """Send one request and return the JSON reply; on an error, stop the script with ElevenLabs' own message.

    A status in RETRYABLE_STATUSES is retried after each delay in RETRY_DELAYS_SECS, but only when
    repeating the request is harmless. A 504 means the server ran out of time, not that it did
    nothing: a POST that creates a test or a branch may already have succeeded, and a retry would
    create a duplicate. So GET, PUT and PATCH always retry, and a POST retries only when the caller
    passes `safe_to_repeat`. Every other error stops at once, since repeating a 401 or a 422 can't
    change the answer.
    """
    retry_delays = RETRY_DELAYS_SECS if method in IDEMPOTENT_METHODS or safe_to_repeat else ()
    response = CLIENT.request(method, path, json=body)
    for delay in retry_delays:
        if response.status_code not in RETRYABLE_STATUSES:
            break
        print(f"{method} {path} -> {response.status_code}, retrying in {delay} s")
        time.sleep(delay)
        response = CLIENT.request(method, path, json=body)
    if response.is_error:
        raise SystemExit(f"{method} {path} -> {response.status_code}\n{response.text}")
    return cast(JsonObject, response.json()) if response.content else {}


def create_or_update(resource: Resource, remote_id: str | None, body: JsonObject) -> str:
    """Update the resource in place when it already has a remote ID, otherwise create it; return its ID.

    Updating instead of recreating keeps IDs stable, so the synced tests and dashboard links
    survive a re-sync.
    """
    if remote_id:
        call_api(resource.update_method, resource.update_path.format(id=remote_id), body)
        return remote_id
    return call_api(HttpMethod.POST, resource.create_path, body)[resource.id_key]


def deployed_agent_id(registry_path: Path = AGENTS_REGISTRY_PATH) -> str:
    """Return the ID of the one agent in agents.json, failing with the path when there is none."""
    agents = cast(list[AgentEntry], json.loads(registry_path.read_text())["agents"])
    if not agents:
        raise SystemExit(f"no agent in {registry_path}; run: elevenlabs agents push")
    return agents[0]["id"]


def deployed_tool_ids(registry_path: Path = TOOLS_REGISTRY_PATH) -> dict[str, str]:
    """Map each tool's name, read from its config file, to the remote ID tools.json records for it."""
    tools = cast(list[ToolEntry], json.loads(registry_path.read_text())["tools"])
    return {json.loads((registry_path.parent / tool["config"]).read_text())["name"]: tool["id"] for tool in tools}
