"""Tests for turning evals/tests.json into agent-testing payloads, selecting tests and rendering the report."""

# Standard library
from typing import Any, cast

# Third-party
import pytest

# Local
from scripts.run_evals import EvalTest, build_test_payload, is_selected, render_report

TOOL_IDS = {"verify_customer": "tool_remote_1", "block_card": "tool_remote_2"}


# Utility functions
def make_test(**fields: Any) -> EvalTest:
    """Build one test definition the way json.loads returns it from evals/tests.json."""
    return cast(EvalTest, {"name": "demo", "type": "llm", **fields})


def test_tool_test_references_the_remote_tool_id() -> None:
    """Tests name tools locally; the payload must carry the ID ElevenLabs knows them by."""
    test = make_test(type="tool", tool_call_parameters={"tool": "verify_customer", "parameters": []})
    referenced = build_test_payload(test, TOOL_IDS)["tool_call_parameters"]["referenced_tool"]
    assert referenced == {"id": "tool_remote_1", "type": "webhook"}, f"unexpected tool reference {referenced}"


def test_unknown_tool_name_fails_with_that_name() -> None:
    """A typo in a tool name must stop the run and say which name, not send a broken test."""
    test = make_test(type="tool", tool_call_parameters={"tool": "verify_custmer", "parameters": []})
    with pytest.raises(SystemExit, match="verify_custmer"):
        build_test_payload(test, TOOL_IDS)


def test_simulation_mocks_every_tool_by_remote_id() -> None:
    """Simulations must never reach the backend, and mocks must be keyed by remote tool ID."""
    test = make_test(
        type="simulation",
        simulation_scenario="Sos Lucía.",
        simulation_max_turns=4,
        success_conditions=["blocks the card"],
        tool_mock_overrides={"block_card": [{"mock_result": '{"status": "blocked"}'}]},
    )
    payload = build_test_payload(test, TOOL_IDS)
    assert payload["tool_mock_config"]["mocking_strategy"] == "all", "simulation does not mock every tool"
    assert list(payload["tool_mock_overrides"]) == ["tool_remote_2"], f"mocks not keyed by remote ID: {payload['tool_mock_overrides']}"


def test_chat_history_gets_increasing_timestamps() -> None:
    """The API requires a timestamp on every scripted turn."""
    turns = [{"role": "agent", "message": "Hola"}, {"role": "user", "message": "Hola"}]
    test = make_test(chat_history=turns, success_condition="ok", success_examples=[], failure_examples=[])
    times = [turn["time_in_call_secs"] for turn in build_test_payload(test, TOOL_IDS)["chat_history"]]
    assert times == [0, 5], f"unexpected turn timestamps {times}"


def test_scripted_tool_call_carries_remote_id_and_paired_result() -> None:
    """A scripted verification must reach the API as a real call and result, or the agent will not trust it."""
    turns = [
        {
            "role": "agent",
            "message": "",
            "tool_call": {"tool": "verify_customer", "params": {"dni": "30123456"}, "result": {"verified": True}},
        },
    ]
    test = make_test(chat_history=turns, success_condition="ok", success_examples=[], failure_examples=[])
    turn = build_test_payload(test, TOOL_IDS)["chat_history"][0]
    call, result = turn["tool_calls"][0], turn["tool_results"][0]
    assert call["tool_id"] == "tool_remote_1", f"tool call not resolved to the remote ID: {call}"
    assert call["request_id"] == result["request_id"], f"call and result are not paired: {call} / {result}"
    assert result["result_value"] == '{"verified": true}', f"result not sent as JSON text: {result}"


def test_simulations_run_only_when_requested() -> None:
    """Simulated calls cost the most credits, so a default run skips them."""
    simulation = make_test(type="simulation")
    assert not is_selected(simulation, only=None, sim=False), "simulation ran without --sim"
    assert is_selected(simulation, only=None, sim=True), "simulation skipped with --sim"


def test_only_selects_by_name_ignoring_case() -> None:
    """--only picks tests by name, including simulations, without needing --sim."""
    simulation = make_test(name="e2e: stolen card", type="simulation")
    assert is_selected(simulation, only="E2E", sim=False), "--only did not match regardless of case"
    assert not is_selected(make_test(name="scope"), only="e2e", sim=False), "--only matched an unrelated test"


def test_report_counts_passes_and_keeps_the_table_intact() -> None:
    """A '|' or newline in the evaluator's reason must not break the Markdown table."""
    invocation = {
        "id": "suite_1",
        "test_runs": [
            {"test_name": "a", "status": "passed", "condition_result": {"rationale": {"summary": "ok | fine\nreally"}}},
            {"test_name": "b", "status": "failed"},
        ],
    }
    report = render_report(invocation)
    assert "**1/2 passed**" in report, f"wrong pass count in report:\n{report}"
    assert "| a |  | passed | ok / fine really |" in report, f"reason not flattened into one cell:\n{report}"
