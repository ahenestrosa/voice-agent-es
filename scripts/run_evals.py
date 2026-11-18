"""Sync evals/tests.json to ElevenLabs agent tests, run them against the deployed agent, report results.

Tests name their tools locally; the remote IDs come from .state.json, written by scripts.deploy.
Simulated calls cost most of the credits, so they only run with --sim. evals/results/latest.md
is the committed record, so it is only rewritten when the full suite ran.

Usage: uv run python -m scripts.run_evals [--sim] [--only TEXT] [--repeat N]
"""

# Standard library
import argparse
import json
import time
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum, auto
from typing import Any, NotRequired, TypedDict, cast

# Local
from scripts.elevenlabs_api import (
    ROOT,
    TEST,
    DeployState,
    HttpMethod,
    JsonObject,
    call_api,
    create_or_update,
    load_state,
    save_state,
)

TESTS_PATH = ROOT / "evals/tests.json"
RESULTS_DIR = ROOT / "evals/results"
# A full suite takes one to two minutes; polling every 5 s reports quickly without flooding the API.
POLL_INTERVAL_SECS = 5
# The API wants a timestamp on every scripted turn; 5 s apart is a plausible phone-call pace.
SECS_PER_TURN = 5


class EvalType(StrEnum):
    """The three kinds of agent test: one tool call, one judged reply, or a whole simulated call."""

    TOOL = auto()
    LLM = auto()
    SIMULATION = auto()


class RunStatus(StrEnum):
    """Status of one test run in an invocation."""

    PENDING = auto()
    PASSED = auto()
    FAILED = auto()


class Speaker(StrEnum):
    """Who says a scripted turn."""

    AGENT = auto()
    USER = auto()


class ScriptedToolCall(TypedDict):
    """A tool call the agent already made earlier in the scripted conversation, with the result it got."""

    tool: str
    params: JsonObject
    result: JsonObject


class ChatTurn(TypedDict):
    """One scripted turn of the conversation that leads up to the tested reply."""

    role: Speaker
    message: str
    tool_call: NotRequired[ScriptedToolCall]


class ToolCallCheck(TypedDict):
    """Which tool the agent must call (or must not call), and checks on its arguments."""

    tool: str
    parameters: list[JsonObject]
    verify_absence: NotRequired[bool]


class MockedReply(TypedDict):
    """A canned tool result returned to the agent during a simulated call."""

    mock_result: str


class EvalTest(TypedDict):
    """The fields every test in evals/tests.json has; `type` decides which subtype the rest follows."""

    name: str
    type: EvalType
    chat_history: NotRequired[list[ChatTurn]]


class ToolEvalTest(EvalTest):
    """A test that checks the next tool call."""

    tool_call_parameters: ToolCallCheck


class LlmEvalTest(EvalTest):
    """A test where an LLM judges the next reply against a condition and examples."""

    success_condition: str
    success_examples: list[str]
    failure_examples: list[str]


class SimulationEvalTest(EvalTest):
    """A test where a simulated caller holds a whole call with the agent, with every tool mocked."""

    simulation_scenario: str
    simulation_max_turns: int
    success_conditions: list[str]
    tool_mock_overrides: NotRequired[dict[str, list[MockedReply]]]


def resolve_tool_id(tool_ids: dict[str, str], name: str) -> str:
    """Return the remote ID of a tool named in a test, failing with the name if it was never deployed."""
    if (tool_id := tool_ids.get(name)) is None:
        raise SystemExit(f"test references tool {name!r}, which is not deployed; run: uv run python -m scripts.deploy")
    return tool_id


def build_tool_exchange(call: ScriptedToolCall, request_id: str, tool_ids: dict[str, str]) -> JsonObject:
    """API fields for a scripted tool call and its result, paired by a shared request ID.

    Without a real call and result in the history, a line like "ya te verifiqué" is only a claim,
    and an agent that follows its prompt rightly asks for the DNI again instead of moving on.
    """
    tool_name = call["tool"]
    tool_id = resolve_tool_id(tool_ids, tool_name)
    tool_call = {
        "type": "webhook",
        "request_id": request_id,
        "tool_name": tool_name,
        "tool_id": tool_id,
        "params_as_json": json.dumps(call["params"], ensure_ascii=False),
        "tool_has_been_called": True,
    }
    tool_result = {
        "type": "webhook",
        "request_id": request_id,
        "tool_name": tool_name,
        "result_value": json.dumps(call["result"], ensure_ascii=False),
        "is_error": False,
        "tool_has_been_called": True,
    }
    return {"tool_calls": [tool_call], "tool_results": [tool_result]}


def build_chat_history(turns: list[ChatTurn], tool_ids: dict[str, str]) -> list[JsonObject]:
    """Turn the scripted turns into the API's chat history, with one timestamp per turn."""
    history: list[JsonObject] = []
    for index, turn in enumerate(turns):
        entry: JsonObject = {"role": Speaker(turn["role"]), "message": turn["message"], "time_in_call_secs": index * SECS_PER_TURN}
        if (call := turn.get("tool_call")) is not None:
            entry |= build_tool_exchange(call, f"call_{index}", tool_ids)
        history.append(entry)
    return history


def tool_test_fields(test: EvalTest, tool_ids: dict[str, str]) -> JsonObject:
    """API fields for a tool test: the referenced tool and the checks on its arguments."""
    check = cast(ToolEvalTest, test)["tool_call_parameters"]
    return {
        "tool_call_parameters": {
            "referenced_tool": {"id": resolve_tool_id(tool_ids, check["tool"]), "type": "webhook"},
            "parameters": check["parameters"],
            "verify_absence": check.get("verify_absence", False),
        },
    }


def llm_test_fields(test: EvalTest, _tool_ids: dict[str, str]) -> JsonObject:
    """API fields for an LLM-judged test: the condition and labelled example replies."""
    llm_test = cast(LlmEvalTest, test)
    return {
        "success_condition": llm_test["success_condition"],
        "success_examples": [{"response": reply, "type": "success"} for reply in llm_test["success_examples"]],
        "failure_examples": [{"response": reply, "type": "failure"} for reply in llm_test["failure_examples"]],
    }


def simulation_test_fields(test: EvalTest, tool_ids: dict[str, str]) -> JsonObject:
    """API fields for a simulated call, with every tool mocked so the run never reaches the backend.

    `raise_error` as the fallback makes a tool call without a mock fail the run loudly, instead of
    silently calling the real webhook and making the result depend on the backend's state.
    """
    simulation = cast(SimulationEvalTest, test)
    mock_overrides: dict[str, list[JsonObject]] = {
        resolve_tool_id(tool_ids, name): [
            {"mock_result": reply["mock_result"], "parameter_conditions": [], "is_error": False} for reply in replies
        ]
        for name, replies in simulation.get("tool_mock_overrides", {}).items()
    }
    return {
        "simulation_scenario": simulation["simulation_scenario"],
        "simulation_max_turns": simulation["simulation_max_turns"],
        "success_conditions": simulation["success_conditions"],
        "tool_mock_config": {"mocking_strategy": "all", "fallback_strategy": "raise_error"},
        "tool_mock_overrides": mock_overrides,
    }


TYPE_FIELDS: dict[EvalType, Callable[[EvalTest, dict[str, str]], JsonObject]] = {
    EvalType.TOOL: tool_test_fields,
    EvalType.LLM: llm_test_fields,
    EvalType.SIMULATION: simulation_test_fields,
}


def build_test_payload(test: EvalTest, tool_ids: dict[str, str]) -> JsonObject:
    """Turn one test from evals/tests.json into the agent-testing API body."""
    eval_type = EvalType(test["type"])
    return {
        "name": test["name"],
        "type": eval_type,
        "chat_history": build_chat_history(test.get("chat_history", []), tool_ids),
        **TYPE_FIELDS[eval_type](test, tool_ids),
    }


def is_selected(test: EvalTest, only: str | None, sim: bool) -> bool:
    """--only picks tests by name; without it, every test runs except simulations, unless --sim is set."""
    if only:
        return only.lower() in test["name"].lower()
    return sim or EvalType(test["type"]) != EvalType.SIMULATION


def sync_tests(state: DeployState) -> list[tuple[EvalTest, str]]:
    """Create or update every test on ElevenLabs and return (test, remote ID) pairs.

    Syncing is free, since no LLM runs, so all tests are synced even when only some will run.
    That keeps the dashboard's copy of the suite identical to evals/tests.json.
    """
    tests = cast(list[EvalTest], json.loads(TESTS_PATH.read_text()))
    synced: list[tuple[EvalTest, str]] = []
    for test in tests:
        existing_id = state["tests"].get(test["name"])
        state["tests"][test["name"]] = create_or_update(TEST, existing_id, build_test_payload(test, state["tools"]))
        save_state(state)
        synced.append((test, state["tests"][test["name"]]))
    return synced


def wait_for_invocation(invocation_id: str) -> JsonObject:
    """Poll the test invocation until no run is pending, then return it."""
    while True:
        invocation = call_api(HttpMethod.GET, f"/v1/convai/test-invocations/{invocation_id}")
        if all(RunStatus(run["status"]) != RunStatus.PENDING for run in invocation["test_runs"]):
            return invocation
        time.sleep(POLL_INTERVAL_SECS)


def all_passed(invocation: JsonObject) -> bool:
    """Return whether every run in the invocation passed."""
    return all(RunStatus(run["status"]) == RunStatus.PASSED for run in invocation["test_runs"])


def nested_text(data: JsonObject, *keys: str) -> str:
    """Read a string several levels down a JSON reply, or "" when any level is missing or null."""
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = cast(JsonObject, value).get(key)
    return value if isinstance(value, str) else ""


def report_row(run: JsonObject) -> str:
    """One Markdown table row for a test run, with the evaluator's reason flattened onto one line."""
    rationale = nested_text(run, "condition_result", "rationale", "summary")
    test_type = nested_text(run, "metadata", "test_type")
    cell = rationale.replace("|", "/").replace("\n", " ")
    return f"| {run.get('test_name')} | {test_type} | {run['status']} | {cell} |"


def render_report(invocation: JsonObject) -> str:
    """Render an invocation as the Markdown report committed in evals/results/latest.md."""
    runs = sorted(invocation["test_runs"], key=lambda run: run.get("test_name", ""))
    passed = sum(RunStatus(run["status"]) == RunStatus.PASSED for run in runs)
    header = [
        f"# Eval run — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"**{passed}/{len(runs)} passed** (invocation `{invocation['id']}`, repeat_count={invocation.get('repeat_count', 1)})",
        "",
        "| Test | Type | Status | Evaluator rationale |",
        "|---|---|---|---|",
    ]
    return "\n".join(header + [report_row(run) for run in runs]) + "\n"


def save_results(invocation: JsonObject, report: str, full_suite: bool) -> None:
    """Write the raw invocation to latest.json, and the report to latest.md only for a full-suite run."""
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "latest.json").write_text(json.dumps(invocation, indent=2, ensure_ascii=False))
    if not full_suite:
        print("partial run: evals/results/latest.md left unchanged\n")
        return
    (RESULTS_DIR / "latest.md").write_text(report)


def main(only: str | None, sim: bool, repeat: int) -> bool:
    """Run the selected tests against the deployed agent and report; return whether all passed.

    How it works:
    1. Load the deployed agent's ID, since tests run against what is deployed, not the local files.
    2. Sync every test in evals/tests.json to ElevenLabs.
    3. Pick the tests to run from --only and --sim.
    4. Start one invocation for them and wait until every run finishes.
    5. Save the raw results and the Markdown report, and print the report.

    Args:
        only: Run only tests whose name contains this text, ignoring case. Overrides `sim`.
        sim: Also run the simulated-call tests, which cost most of the credits.
        repeat: Runs per test (1-20). More runs expose flaky tests; credits scale with it.
    """
    # 1. Without a deployed agent there is nothing to test.
    state = load_state()
    if (agent_id := state.get("agent_id")) is None:
        raise SystemExit("no agent yet, run: uv run python -m scripts.deploy")

    # 2. Sync everything, so the remote suite always matches the local file.
    synced = sync_tests(state)

    # 3. Select by name or type; an empty selection is a typo in --only.
    test_ids = [test_id for test, test_id in synced if is_selected(test, only, sim)]
    if not test_ids:
        raise SystemExit(f"no test name contains {only!r}")
    print(f"running {len(test_ids)}/{len(synced)} tests x{repeat}")

    # 4. One invocation runs every selected test `repeat` times on ElevenLabs' side.
    run_request = {"tests": [{"test_id": test_id} for test_id in test_ids], "repeat_count": repeat}
    started = call_api(HttpMethod.POST, f"/v1/convai/agents/{agent_id}/run-tests", run_request)
    invocation = wait_for_invocation(started["id"])

    # 5. Only a full-suite run replaces the committed report.
    report = render_report(invocation)
    save_results(invocation, report, full_suite=len(test_ids) == len(synced))
    print(report)
    return all_passed(invocation)


def cli() -> None:
    """Parse the command line, run main, and exit non-zero when any test failed."""
    parser = argparse.ArgumentParser(description="Run the agent's evals on ElevenLabs.")
    parser.add_argument("--only", help="run only tests whose name contains this text, e.g. --only e2e")
    parser.add_argument("--sim", action="store_true", help="also run the simulated-call tests (most of the credit cost)")
    parser.add_argument("--repeat", type=int, default=1, help="runs per test (1-20) to measure flakiness; cost scales with it")
    args = parser.parse_args()
    raise SystemExit(0 if main(args.only, args.sim, args.repeat) else 1)


if __name__ == "__main__":
    cli()
