"""Create or update the webhook tools and the agent from agent/*.json + agent/prompt.md.

Idempotent: remote IDs are kept in .state.json, so re-running updates in place.
Usage: uv run python -m scripts.deploy
"""

import copy
import json
import os

from scripts.el import ROOT, api, load_state, save_state


def tool_config(raw: dict) -> dict:
    cfg = copy.deepcopy(raw)
    cfg["api_schema"]["url"] = cfg["api_schema"]["url"].replace("{BACKEND_URL}", os.environ["BACKEND_URL"].rstrip("/"))
    # ponytail: plain header value; move to a workspace secret reference before using real credentials
    cfg["api_schema"]["request_headers"] = {"X-Tool-Secret": os.environ["TOOL_SECRET"]}
    return {"tool_config": cfg}


def main() -> None:
    state = load_state()

    for raw in json.loads((ROOT / "agent/tools.json").read_text()):
        name, body = raw["name"], tool_config(raw)
        if tid := state["tools"].get(name):
            api("PATCH", f"/v1/convai/tools/{tid}", body)
            print(f"updated tool {name} ({tid})")
        else:
            state["tools"][name] = api("POST", "/v1/convai/tools", body)["id"]
            print(f"created tool {name} ({state['tools'][name]})")
        save_state(state)

    agent = json.loads((ROOT / "agent/agent.json").read_text())
    agent["conversation_config"]["agent"]["prompt"].update(
        prompt=(ROOT / "agent/prompt.md").read_text(),
        tool_ids=list(state["tools"].values()),
    )
    if aid := state.get("agent_id"):
        api("PATCH", f"/v1/convai/agents/{aid}", agent)
        print(f"updated agent {aid}")
    else:
        state["agent_id"] = api("POST", "/v1/convai/agents/create", agent)["agent_id"]
        print(f"created agent {state['agent_id']}")
    save_state(state)
    print("open it in the ElevenLabs dashboard (Agents) to talk to it")


if __name__ == "__main__":
    main()
