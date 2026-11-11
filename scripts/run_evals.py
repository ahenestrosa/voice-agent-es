"""Sync evals/tests.json to ElevenLabs agent tests, run them against the deployed agent, report results.

Tests reference tools by name; IDs come from .state.json (written by scripts.deploy).
Simulated calls cost most of the credits, so they only run with --sim. evals/results/latest.md
is the committed record, so it is only rewritten when the full suite ran.

Usage: uv run python -m scripts.run_evals [--sim] [--only TEXT] [--repeat N]
"""

import argparse
import json
import time
from datetime import datetime

from scripts.el import ROOT, api, load_state, save_state


def to_payload(t: dict, tools: dict) -> dict:
    body = {
        "name": t["name"],
        "type": t["type"],
        "chat_history": [
            {"role": m["role"], "message": m["message"], "time_in_call_secs": i * 5}
            for i, m in enumerate(t.get("chat_history", []))
        ],
    }
    if t["type"] == "tool":
        p = t["tool_call_parameters"]
        body["tool_call_parameters"] = {
            "referenced_tool": {"id": tools[p["tool"]], "type": "webhook"},
            "parameters": p["parameters"],
            "verify_absence": p.get("verify_absence", False),
        }
    elif t["type"] == "llm":
        body["success_condition"] = t["success_condition"]
        body["success_examples"] = [{"response": r, "type": "success"} for r in t["success_examples"]]
        body["failure_examples"] = [{"response": r, "type": "failure"} for r in t["failure_examples"]]
    else:  # simulation: every tool is mocked, so evals never touch the backend
        body |= {
            "simulation_scenario": t["simulation_scenario"],
            "simulation_max_turns": t["simulation_max_turns"],
            "success_conditions": t["success_conditions"],
            "tool_mock_config": {"mocking_strategy": "all", "fallback_strategy": "raise_error"},
            "tool_mock_overrides": {
                tools[name]: [{"mock_result": m["mock_result"], "parameter_conditions": [], "is_error": False} for m in mocks]
                for name, mocks in t.get("tool_mock_overrides", {}).items()
            },
        }
    return body


def sync_tests(state: dict) -> list[tuple[dict, str]]:
    """Create or update every test on ElevenLabs (free: no LLM runs). Returns (test, remote id) pairs."""
    synced = []
    for t in json.loads((ROOT / "evals/tests.json").read_text()):
        body = to_payload(t, state["tools"])
        if tid := state["tests"].get(t["name"]):
            api("PUT", f"/v1/convai/agent-testing/{tid}", body)
        else:
            state["tests"][t["name"]] = tid = api("POST", "/v1/convai/agent-testing/create", body)["id"]
            save_state(state)
        synced.append((t, tid))
    return synced


def wanted(t: dict, only: str | None, sim: bool) -> bool:
    """--only picks tests by name; without it, every test runs except simulations unless --sim is set."""
    if only:
        return only.lower() in t["name"].lower()
    return sim or t["type"] != "simulation"


def wait(invocation_id: str) -> dict:
    while True:
        inv = api("GET", f"/v1/convai/test-invocations/{invocation_id}")
        if all(r["status"] != "pending" for r in inv["test_runs"]):
            return inv
        time.sleep(5)


def report(inv: dict) -> str:
    runs = inv["test_runs"]
    passed = sum(r["status"] == "passed" for r in runs)
    lines = [
        f"# Eval run — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"**{passed}/{len(runs)} passed** (invocation `{inv['id']}`, repeat_count={inv.get('repeat_count', 1)})",
        "",
        "| Test | Type | Status | Evaluator rationale |",
        "|---|---|---|---|",
    ]
    for r in sorted(runs, key=lambda r: r.get("test_name", "")):
        why = ((r.get("condition_result") or {}).get("rationale") or {}).get("summary", "")
        kind = (r.get("metadata") or {}).get("test_type", "")
        lines.append(f"| {r.get('test_name')} | {kind} | {r['status']} | {why.replace('|', '/').replace(chr(10), ' ')} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1, help="runs per test (2-20) to measure flakiness; cost scales with it")
    ap.add_argument("--sim", action="store_true", help="also run the simulated-call tests (most of the credit cost)")
    ap.add_argument("--only", help="run only tests whose name contains this text, e.g. --only e2e")
    args = ap.parse_args()

    state = load_state()
    if "agent_id" not in state:
        raise SystemExit("no agent yet, run: uv run python -m scripts.deploy")

    synced = sync_tests(state)
    ids = [tid for t, tid in synced if wanted(t, args.only, args.sim)]
    if not ids:
        raise SystemExit(f"no test name contains {args.only!r}")
    print(f"running {len(ids)}/{len(synced)} tests x{args.repeat}")
    inv = api("POST", f"/v1/convai/agents/{state['agent_id']}/run-tests",
              {"tests": [{"test_id": i} for i in ids], "repeat_count": args.repeat})
    inv = wait(inv["id"])

    out = ROOT / "evals/results"
    out.mkdir(exist_ok=True)
    (out / "latest.json").write_text(json.dumps(inv, indent=2, ensure_ascii=False))
    md = report(inv)
    if len(ids) == len(synced):
        (out / "latest.md").write_text(md)
    else:
        print("partial run: evals/results/latest.md left unchanged\n")
    print(md)
    raise SystemExit(0 if all(r["status"] == "passed" for r in inv["test_runs"]) else 1)


if __name__ == "__main__":
    main()
