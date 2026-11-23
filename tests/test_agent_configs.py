"""Checks on the agent and tool configs that `elevenlabs agents push` and `elevenlabs tools push` send.

The CLI pushes these files as they are, so every rule the backend depends on is checked here,
before a push can break a live call.
"""

# Standard library
import json
from pathlib import Path
from typing import Any, cast

# Third-party
import pytest
import yaml

# Local
from scripts.elevenlabs_api import (
    AGENTS_REGISTRY_PATH,
    ROOT,
    TOOLS_REGISTRY_PATH,
    JsonObject,
    deployed_tool_ids,
)

TOOL_CONFIG_PATHS = sorted((ROOT / "tool_configs").glob("*.json"))
# Filled by ElevenLabs at call time from the workspace environment variables; never a literal value.
BACKEND_URL_PREFIX = "https://{{system__env_backend_host}}/tools/"
SECRET_HEADER = {"X-Tool-Secret": {"env_var_label": "tool_secret"}}
CONVERSATION_ID_PROPERTY = {"type": "string", "dynamic_variable": "system__conversation_id"}


def load_tool_config(path: Path) -> JsonObject:
    """Read one tool config from tool_configs/."""
    return cast(JsonObject, json.loads(path.read_text()))


def load_agent_config() -> JsonObject:
    """Read the agent config that agents.json points to."""
    agent_entry = json.loads(AGENTS_REGISTRY_PATH.read_text())["agents"][0]
    return cast(JsonObject, yaml.safe_load((ROOT / agent_entry["config"]).read_text()))


def test_every_tool_config_is_registered() -> None:
    """A config missing from tools.json is never pushed, so the agent would silently lack that tool."""
    registered = {entry["config"] for entry in json.loads(TOOLS_REGISTRY_PATH.read_text())["tools"]}
    on_disk = {str(path.relative_to(ROOT)) for path in TOOL_CONFIG_PATHS}
    assert on_disk == registered, f"tool configs on disk {on_disk} differ from tools.json {registered}"


@pytest.mark.parametrize("path", TOOL_CONFIG_PATHS, ids=lambda path: path.stem)
def test_tool_posts_to_backend_host_variable(path: Path) -> None:
    """A literal URL would point at a dead tunnel; the variable lets the host change without a push."""
    tool = load_tool_config(path)
    expected_url = f"{BACKEND_URL_PREFIX}{tool['name']}"
    assert tool["api_schema"]["url"] == expected_url, f"{path.name} posts to {tool['api_schema']['url']}, expected {expected_url}"


@pytest.mark.parametrize("path", TOOL_CONFIG_PATHS, ids=lambda path: path.stem)
def test_tool_sends_secret_from_workspace_secret(path: Path) -> None:
    """The secret must be a reference; a literal value would be committed and shown in the dashboard."""
    headers = load_tool_config(path)["api_schema"]["request_headers"]
    assert headers == SECRET_HEADER, f"{path.name} sends headers {headers}, expected {SECRET_HEADER}"


@pytest.mark.parametrize("path", TOOL_CONFIG_PATHS, ids=lambda path: path.stem)
def test_tool_requires_platform_conversation_id(path: Path) -> None:
    """The backend binds each Session to its Call through this ID and rejects requests without it."""
    body_schema = load_tool_config(path)["api_schema"]["request_body_schema"]
    conversation_id = body_schema["properties"].get("conversation_id")
    assert conversation_id == CONVERSATION_ID_PROPERTY, f"{path.name} has conversation_id {conversation_id}"
    assert "conversation_id" in body_schema["required"], f"{path.name} does not require conversation_id"


def test_agent_uses_exactly_the_registered_tools() -> None:
    """A stale or missing ID in the agent config leaves the agent with the wrong set of tools."""
    tool_ids = load_agent_config()["conversation_config"]["agent"]["prompt"]["tool_ids"]
    registered_ids = set(deployed_tool_ids().values())
    assert set(tool_ids) == registered_ids, f"agent tool_ids {tool_ids} differ from tools.json IDs {registered_ids}"


def test_deployed_tool_ids_maps_config_names_to_registry_ids(tmp_path: Path) -> None:
    """run_evals resolves tool names through this map, so a name read from the wrong place breaks every test."""
    (tmp_path / "tool_configs").mkdir()
    (tmp_path / "tool_configs/a.json").write_text(json.dumps({"name": "get_balance"}))
    registry: dict[str, Any] = {"tools": [{"type": "webhook", "config": "tool_configs/a.json", "id": "tool_1"}]}
    (tmp_path / "tools.json").write_text(json.dumps(registry))
    tool_ids = deployed_tool_ids(tmp_path / "tools.json")
    assert tool_ids == {"get_balance": "tool_1"}, f"unexpected map {tool_ids}"
