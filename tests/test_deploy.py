"""Tests for building the tool and agent configs that scripts.deploy sends to ElevenLabs."""

# Standard library
import copy

# Local
from scripts.deploy import build_agent_config, build_tool_config
from scripts.elevenlabs_api import JsonObject

TOOL_DEFINITION: JsonObject = {
    "name": "get_balance",
    "api_schema": {"url": "{BACKEND_URL}/tools/get_balance", "method": "POST"},
}
AGENT_DEFINITION: JsonObject = {"name": "demo", "conversation_config": {"agent": {"prompt": {}}}}


def test_tool_config_points_at_backend_without_double_slash() -> None:
    """A trailing slash in BACKEND_URL must not produce "//tools/..." in the webhook URL."""
    config = build_tool_config(TOOL_DEFINITION, "https://example.trycloudflare.com/", "s3cret")
    url = config["tool_config"]["api_schema"]["url"]
    assert url == "https://example.trycloudflare.com/tools/get_balance", f"unexpected webhook URL {url}"


def test_tool_config_carries_the_secret_header() -> None:
    """Every tool call must send the secret the backend checks."""
    config = build_tool_config(TOOL_DEFINITION, "https://example.com", "s3cret")
    headers = config["tool_config"]["api_schema"]["request_headers"]
    assert headers == {"X-Tool-Secret": "s3cret"}, f"unexpected headers {headers}"


def test_building_configs_leaves_the_loaded_definitions_unchanged() -> None:
    """The definitions read from agent/*.json are reused, so building a config must not edit them."""
    tool_before, agent_before = copy.deepcopy(TOOL_DEFINITION), copy.deepcopy(AGENT_DEFINITION)
    build_tool_config(TOOL_DEFINITION, "https://example.com", "s3cret")
    build_agent_config(AGENT_DEFINITION, "Sos Matías.", ["tool_1"])
    assert TOOL_DEFINITION == tool_before, "build_tool_config mutated its input"
    assert AGENT_DEFINITION == agent_before, "build_agent_config mutated its input"


def test_agent_config_gets_prompt_and_tool_ids() -> None:
    """The agent must be deployed with the prompt file and the tools from this deploy."""
    prompt = build_agent_config(AGENT_DEFINITION, "Sos Matías.", ["tool_1", "tool_2"])["conversation_config"]["agent"]["prompt"]
    assert prompt == {"prompt": "Sos Matías.", "tool_ids": ["tool_1", "tool_2"]}, f"unexpected prompt config {prompt}"
