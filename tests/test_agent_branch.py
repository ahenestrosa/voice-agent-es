"""Tests for finding a PR's agent branch in the branch-list reply."""

# Local
from scripts.agent_branch import AgentBranch, find_branch

BRANCHES: list[AgentBranch] = [
    {"id": "agtbrch_main", "name": "Main", "is_archived": False},
    {"id": "agtbrch_pr7", "name": "pr-7", "is_archived": True},
]


def test_find_branch_returns_archived_branches_too() -> None:
    """A reopened PR must revive its archived branch, since creating one with the same name fails."""
    branch = find_branch(BRANCHES, "pr-7")
    assert branch is not None and branch["id"] == "agtbrch_pr7", f"archived branch not found: {branch}"


def test_find_branch_returns_none_for_an_unknown_name() -> None:
    """An unknown name must read as missing, so ensure creates the branch instead of pushing to nothing."""
    assert find_branch(BRANCHES, "pr-8") is None, "found a branch that does not exist"
