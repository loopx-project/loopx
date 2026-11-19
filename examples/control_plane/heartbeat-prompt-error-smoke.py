#!/usr/bin/env python3
"""Smoke-test heartbeat-prompt failure rendering."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "heartbeat-error-goal"
ACTIVE_STATE = Path("/tmp/heartbeat-error-goal/ACTIVE_GOAL_STATE.md")
REGISTRY = Path("/tmp/heartbeat-error-goal/registry.json")
AGENT_ID = "codex-side-bypass"
SCOPE = "scope without identity"
INVALID_SCOPE = "invalid <scope>"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def assert_failure_payload(payload: dict) -> None:
    assert set(payload) == {"schema_version", "ok", "goal_id", "error"}, payload
    assert payload["schema_version"] == "heartbeat_agent_input_v1", payload
    assert payload["ok"] is False, payload
    assert payload["goal_id"] == GOAL_ID, payload
    assert "--agent-scope requires --agent-id" in payload["error"], payload


def main() -> int:
    ACTIVE_STATE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_STATE.write_text("# Active State\n", encoding="utf-8")
    REGISTRY.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    markdown_result = run_cli(
        "heartbeat-prompt",
        "--goal-id",
        GOAL_ID,
        "--active-state",
        str(ACTIVE_STATE),
        "--agent-scope",
        SCOPE,
    )
    assert markdown_result.returncode == 1, markdown_result.stdout
    markdown = markdown_result.stdout
    assert "# Heartbeat Automation Prompt Error" in markdown, markdown
    assert "No heartbeat task body was generated." in markdown, markdown
    assert "--agent-scope requires --agent-id" in markdown, markdown
    assert "Copy this task body into a Codex App heartbeat automation." not in markdown, markdown
    assert "````text\n\n````" not in markdown, markdown
    assert f"- goal_id: `{GOAL_ID}`" in markdown, markdown
    assert f"- active_state = resolved_active_state: `{ACTIVE_STATE}`" in markdown, markdown
    assert "- active_state_source: `explicit`" in markdown, markdown
    assert f"- agent_scopes: `['{SCOPE}']`" in markdown, markdown
    assert "- thin: `True`" in markdown, markdown
    assert "- full:" not in markdown, markdown

    json_result = run_cli(
        "--format",
        "json",
        "heartbeat-prompt",
        "--goal-id",
        GOAL_ID,
        "--active-state",
        str(ACTIVE_STATE),
        "--agent-scope",
        SCOPE,
    )
    assert json_result.returncode == 1, json_result.stdout
    assert_failure_payload(json.loads(json_result.stdout))

    invalid_scope_result = run_cli(
        "--format",
        "json",
        "--registry",
        str(REGISTRY),
        "heartbeat-prompt",
        "--goal-id",
        GOAL_ID,
        "--active-state",
        str(ACTIVE_STATE),
        "--agent-id",
        AGENT_ID,
        "--agent-scope",
        INVALID_SCOPE,
    )
    assert invalid_scope_result.returncode == 1, invalid_scope_result.stdout
    invalid_scope_payload = json.loads(invalid_scope_result.stdout)
    assert "agent scope must be compact text without angle brackets" in invalid_scope_payload["error"], (
        invalid_scope_payload
    )
    assert invalid_scope_payload["agent_id"] == AGENT_ID, invalid_scope_payload
    assert "agent_scopes" not in invalid_scope_payload, invalid_scope_payload

    print("heartbeat-prompt-error-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
