"""Create or update the webhook tools and the agent on ElevenLabs from agent/*.json and agent/prompt.md.

Remote IDs are kept in .state.json, so re-running updates everything in place.
Usage: uv run python -m scripts.deploy
"""

# Standard library
import copy
import json
import os
from typing import cast

# Local
from scripts.elevenlabs_api import (
    AGENT,
    ROOT,
    TOOL,
    DeployState,
    JsonObject,
    create_or_update,
    load_state,
    save_state,
)

AGENT_DIR = ROOT / "agent"
# Public URL of backend/app.py (a cloudflared tunnel in development); every tool posts to it.
BACKEND_URL = os.environ.get("BACKEND_URL", "")
TOOL_SECRET = os.environ.get("TOOL_SECRET", "")


def build_tool_config(tool_definition: JsonObject, backend_url: str, tool_secret: str) -> JsonObject:
    """Fill the backend URL and the shared-secret header into one tool definition from agent/tools.json.

    The secret goes in as a plain header value, which ElevenLabs stores in the tool config and shows
    to anyone with dashboard access. A real deployment would use a workspace secret reference instead.
    """
    tool_config = copy.deepcopy(tool_definition)
    api_schema = tool_config["api_schema"]
    api_schema["url"] = api_schema["url"].replace("{BACKEND_URL}", backend_url.rstrip("/"))
    api_schema["request_headers"] = {"X-Tool-Secret": tool_secret}
    return {"tool_config": tool_config}


def build_agent_config(agent_definition: JsonObject, prompt: str, tool_ids: list[str]) -> JsonObject:
    """Attach the system prompt and the deployed tool IDs to the agent definition from agent/agent.json."""
    agent_config = copy.deepcopy(agent_definition)
    agent_config["conversation_config"]["agent"]["prompt"].update(prompt=prompt, tool_ids=tool_ids)
    return agent_config


def deploy_tools(state: DeployState) -> list[str]:
    """Create or update every tool in agent/tools.json and return their remote IDs."""
    tool_definitions = cast(list[JsonObject], json.loads((AGENT_DIR / "tools.json").read_text()))
    tool_ids: list[str] = []
    for tool_definition in tool_definitions:
        name = tool_definition["name"]
        existing_id = state["tools"].get(name)
        tool_config = build_tool_config(tool_definition, BACKEND_URL, TOOL_SECRET)
        state["tools"][name] = create_or_update(TOOL, existing_id, tool_config)
        # Saved after each tool, so a failure halfway doesn't lose the IDs of the tools already created.
        save_state(state)
        tool_ids.append(state["tools"][name])
        print(f"{'updated' if existing_id else 'created'} tool {name} ({state['tools'][name]})")
    return tool_ids


def deploy_agent(state: DeployState, tool_ids: list[str]) -> None:
    """Create or update the agent with the system prompt and the given tools."""
    agent_definition = cast(JsonObject, json.loads((AGENT_DIR / "agent.json").read_text()))
    prompt = (AGENT_DIR / "prompt.md").read_text()
    existing_id = state.get("agent_id")
    state["agent_id"] = create_or_update(AGENT, existing_id, build_agent_config(agent_definition, prompt, tool_ids))
    save_state(state)
    print(f"{'updated' if existing_id else 'created'} agent {state['agent_id']}")


def main() -> None:
    """Deploy the webhook tools, then the agent that uses them.

    How it works:
    1. Check that the backend URL and the tool secret are set, since every tool needs both.
    2. Create or update each tool in agent/tools.json, pointed at the backend.
    3. Create or update the agent with the prompt and exactly the tools from step 2.
    """
    # 1. Fail before touching ElevenLabs, so a missing variable can't leave tools pointing at nothing.
    if not BACKEND_URL or not TOOL_SECRET:
        raise SystemExit(f"set BACKEND_URL and TOOL_SECRET in {ROOT / '.env'}")

    state = load_state()

    # 2. Deploy the tools first, because the agent references them by ID.
    tool_ids = deploy_tools(state)

    # 3. Attach only this deploy's tools, so a tool removed from tools.json also leaves the agent.
    deploy_agent(state, tool_ids)
    print("open it in the ElevenLabs dashboard (Agents) to talk to it")


if __name__ == "__main__":
    main()
