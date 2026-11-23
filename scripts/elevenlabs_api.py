"""ElevenLabs REST helper, and readers for the CLI registries that hold the deployed agent and tool IDs."""

# Standard library
import json
import os
from dataclasses import dataclass
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


class HttpMethod(StrEnum):
    """The HTTP verbs the ElevenLabs endpoints used here accept."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"


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


def call_api(method: HttpMethod, path: str, body: JsonObject | None = None) -> JsonObject:
    """Send one request and return the JSON reply; on an error, stop the script with ElevenLabs' own message."""
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
