"""ElevenLabs REST helper, plus the local state file that maps each resource's local name to its remote ID."""

# Standard library
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

# Third-party
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".state.json"

load_dotenv(ROOT / ".env")
CLIENT = httpx.Client(
    base_url="https://api.elevenlabs.io",
    headers={"xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "")},
    # Creating an agent or syncing a test can take several seconds; httpx's 5 s default times out.
    timeout=60,
)

JsonObject = dict[str, Any]


class HttpMethod(StrEnum):
    """The HTTP verbs the ElevenLabs endpoints used here accept."""

    GET = "GET"
    POST = "POST"
    PATCH = "PATCH"
    PUT = "PUT"


class DeployState(TypedDict):
    """Contents of .state.json: remote IDs keyed by the local name of each tool and test."""

    tools: dict[str, str]
    tests: dict[str, str]
    agent_id: NotRequired[str]


@dataclass(frozen=True)
class Resource:
    """Where one kind of ElevenLabs resource is created and updated; each endpoint family differs."""

    create_path: str
    update_path: str  # contains an {id} placeholder
    update_method: HttpMethod
    id_key: str  # the key holding the new ID in the create response


TOOL = Resource("/v1/convai/tools", "/v1/convai/tools/{id}", HttpMethod.PATCH, "id")
AGENT = Resource("/v1/convai/agents/create", "/v1/convai/agents/{id}", HttpMethod.PATCH, "agent_id")
TEST = Resource("/v1/convai/agent-testing/create", "/v1/convai/agent-testing/{id}", HttpMethod.PUT, "id")


def call_api(method: HttpMethod, path: str, body: JsonObject | None = None) -> JsonObject:
    """Send one request and return the JSON reply; on an error, stop the script with ElevenLabs' own message."""
    response = CLIENT.request(method, path, json=body)
    if response.is_error:
        raise SystemExit(f"{method} {path} -> {response.status_code}\n{response.text}")
    return cast(JsonObject, response.json()) if response.content else {}


def create_or_update(resource: Resource, remote_id: str | None, body: JsonObject) -> str:
    """Update the resource in place when it already has a remote ID, otherwise create it; return its ID.

    Updating instead of recreating keeps IDs stable, so the agent's tool list, the synced tests and
    dashboard links all survive a redeploy. ElevenLabs merges an update into the stored config, so
    fields the body leaves out (for example the account's default LLM) are kept.
    """
    if remote_id:
        call_api(resource.update_method, resource.update_path.format(id=remote_id), body)
        return remote_id
    return call_api(HttpMethod.POST, resource.create_path, body)[resource.id_key]


def load_state() -> DeployState:
    """Read .state.json, or start empty before the first deploy."""
    if not STATE_PATH.exists():
        return {"tools": {}, "tests": {}}
    return cast(DeployState, json.loads(STATE_PATH.read_text()))


def save_state(state: DeployState) -> None:
    """Write .state.json."""
    STATE_PATH.write_text(json.dumps(state, indent=2))
