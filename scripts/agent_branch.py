"""Create or archive the ElevenLabs agent branch that a pull request's evals run on.

A branch holds its own copy of the agent config (prompt, model, voice, tool list), so a PR can
be pushed and tested without touching the main branch that live calls use. Tools are shared by
every branch, so a PR's tool config changes are not isolated and reach ElevenLabs only on deploy.

Usage: uv run python -m scripts.agent_branch {ensure,archive} BRANCH_NAME
"""

# Standard library
import argparse
from collections.abc import Callable
from enum import StrEnum, auto
from typing import TypedDict, cast

# Local
from scripts.elevenlabs_api import HttpMethod, call_api, deployed_agent_id

# Archived branches are listed too, so a reopened PR revives its old branch instead of
# failing on the name, which must be unique within the agent.
BRANCH_LIST_QUERY = "include_archived=true&limit=100"


class BranchAction(StrEnum):
    """What to do with a PR's branch: make sure it exists and is active, or archive it."""

    ENSURE = auto()
    ARCHIVE = auto()


class AgentBranch(TypedDict):
    """The fields of one entry in the branch-list reply that this script reads."""

    id: str
    name: str
    is_archived: bool


def find_branch(branches: list[AgentBranch], name: str) -> AgentBranch | None:
    """Return the branch with this name, archived or not, or None when the agent has none."""
    return next((branch for branch in branches if branch["name"] == name), None)


def list_branches(agent_id: str) -> list[AgentBranch]:
    """Every branch of the agent, archived ones included."""
    reply = call_api(HttpMethod.GET, f"/v1/convai/agents/{agent_id}/branches?{BRANCH_LIST_QUERY}")
    return cast(list[AgentBranch], reply["results"])


def set_archived(agent_id: str, branch: AgentBranch, archived: bool) -> None:
    """Archive or unarchive one branch."""
    call_api(HttpMethod.PATCH, f"/v1/convai/agents/{agent_id}/branches/{branch['id']}", {"is_archived": archived})


def create_branch(agent_id: str, name: str) -> str:
    """Branch off the latest version of main and return the new branch's ID."""
    # Fetching the agent without a branch returns main, so its version_id is main's tip.
    main_version_id = call_api(HttpMethod.GET, f"/v1/convai/agents/{agent_id}")["version_id"]
    body = {"parent_version_id": main_version_id, "name": name, "description": f"Eval branch {name}, managed by CI"}
    return call_api(HttpMethod.POST, f"/v1/convai/agents/{agent_id}/branches", body)["created_branch_id"]


def active_branch_id(agent_id: str, name: str) -> str:
    """Return the ID of the active branch with this name, failing with the name when there is none."""
    branch = find_branch(list_branches(agent_id), name)
    if branch is None or branch["is_archived"]:
        raise SystemExit(f"agent has no active branch {name!r}; run: uv run python -m scripts.agent_branch ensure {name}")
    return branch["id"]


def ensure_branch(agent_id: str, name: str) -> None:
    """Create the branch, or unarchive it, so a push to it succeeds; the CLI refuses unknown branches."""
    branch = find_branch(list_branches(agent_id), name)
    if branch is None:
        print(f"created branch {name} ({create_branch(agent_id, name)})")
        return
    if branch["is_archived"]:
        set_archived(agent_id, branch, archived=False)
    print(f"branch {name} ready ({branch['id']})")


def archive_branch(agent_id: str, name: str) -> None:
    """Archive the branch, if it exists and is active; archived branches stay in the dashboard history."""
    branch = find_branch(list_branches(agent_id), name)
    if branch is None or branch["is_archived"]:
        print(f"no active branch {name}, nothing to archive")
        return
    set_archived(agent_id, branch, archived=True)
    print(f"archived branch {name} ({branch['id']})")


ACTIONS: dict[BranchAction, Callable[[str, str], None]] = {
    BranchAction.ENSURE: ensure_branch,
    BranchAction.ARCHIVE: archive_branch,
}


def main(action: BranchAction, name: str) -> None:
    """Ensure or archive one branch of the agent in agents.json.

    How it works:
    1. Read the agent's ID from agents.json, the registry the CLI pushes from.
    2. Run the action; both are idempotent, so every push to a PR can safely repeat them.

    Args:
        action: ENSURE creates the branch off main's latest version (or unarchives it) before a
            push; ARCHIVE hides it once the PR closes.
        name: The branch name, `pr-<number>` in CI. Must differ from the main branch's name.
    """
    # 1. Branches belong to one agent, and the registry names it.
    agent_id = deployed_agent_id()

    # 2. Dispatch; adding an action means adding an entry to ACTIONS.
    ACTIONS[action](agent_id, name)


def cli() -> None:
    """Parse the command line and run main."""
    parser = argparse.ArgumentParser(description="Create or archive a PR's agent branch on ElevenLabs.")
    parser.add_argument("action", type=BranchAction, choices=list(BranchAction))
    parser.add_argument("name", help="branch name, e.g. pr-12")
    args = parser.parse_args()
    main(args.action, args.name)


if __name__ == "__main__":
    cli()
