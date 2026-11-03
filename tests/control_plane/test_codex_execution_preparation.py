from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from loopx.control_plane.turn_driver.codex_execution_preparation import (
    codex_execution_preparation_context,
    compact_execution_preparation_request,
    execute_codex_execution_preparation,
)


GOAL = "goal-example"
TODO = "todo_example"
AGENT = "agent-a"


def _packet(*, turn: str | None = "turn-example") -> dict[str, object]:
    return {
        "ok": True,
        "goal_id": GOAL,
        "registry": "/tmp/loopx-project-a/.loopx/registry.json",
        "runtime_root": "/tmp/loopx-runtime-a",
        "should_run": True,
        "selected_todo": {"todo_id": TODO, "claimed_by": AGENT},
        "execution_preparation": {
            "schema_version": "execution_preparation_v0",
            "state": "claim_required",
            "todo_id": TODO,
            "agent_id": AGENT,
            "claim": {
                "host_execution_allowed": True,
                "cli_args": [
                    "todo", "claim", "--goal-id", GOAL, "--todo-id", TODO,
                    "--agent-id", AGENT, "--claimed-by", AGENT,
                ],
            },
        },
        **(
            {"heartbeat_receipt": {"turn_instance_id": turn}}
            if turn is not None
            else {}
        ),
    }


def _completed(payload: dict[str, object], *, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=code, stdout=json.dumps(payload), stderr="",
    )


def test_compact_request_accepts_only_exact_host_action() -> None:
    request = compact_execution_preparation_request(_packet())
    assert request == {
        "goal_id": GOAL,
        "agent_id": AGENT,
        "todo_id": TODO,
        "turn_instance_id": "turn-example",
        "claim_args": [
            "todo", "claim", "--goal-id", GOAL, "--todo-id", TODO,
            "--agent-id", AGENT, "--claimed-by", AGENT,
        ],
        "authority_route": {
            "registry_path": "/tmp/loopx-project-a/.loopx/registry.json",
            "runtime_root": "/tmp/loopx-runtime-a",
        },
    }

    malformed = _packet()
    malformed["execution_preparation"]["claim"]["cli_args"][-1] = "agent-b"
    assert compact_execution_preparation_request(malformed) is None


def test_executor_claims_then_proves_same_turn_ready(tmp_path: Path) -> None:
    request = compact_execution_preparation_request(_packet())
    assert request is not None
    calls: list[list[str]] = []

    def runner(command, *, cwd, timeout):
        calls.append(list(command))
        if "should-run" not in command:
            return _completed({"ok": True, "claimed": True})
        return _completed({
            "ok": True,
            "goal_id": GOAL,
            "should_run": True,
            "normal_delivery_allowed": True,
            "selected_todo": {"todo_id": TODO},
            "execution_preparation": {
                "schema_version": "execution_preparation_v0",
                "state": "ready",
                "todo_id": TODO,
                "agent_id": AGENT,
                "delivery_allowed": True,
            },
            "heartbeat_receipt": {"turn_instance_id": "turn-example"},
        })

    receipt = execute_codex_execution_preparation(
        request,
        cwd=tmp_path.resolve(),
        scenario="codex_app_ssh_goal",
        runner=runner,
    )

    assert receipt["event"] == "host_execution_prepared"
    assert receipt["normal_delivery_allowed"] is True
    assert len(calls) == 2
    for call in calls:
        assert call[call.index("--registry") + 1] == (
            "/tmp/loopx-project-a/.loopx/registry.json"
        )
        assert call[call.index("--runtime-root") + 1] == "/tmp/loopx-runtime-a"
    assert calls[0][-len(request["claim_args"]):] == request["claim_args"]
    assert calls[1][-4:] == ["--todo-id", TODO, "--turn-instance-id", "turn-example"]
    context = codex_execution_preparation_context(receipt)
    assert "do not repeat" in context and '"state":"ready"' in context


def test_executor_rejects_changed_todo_or_turn(tmp_path: Path) -> None:
    request = compact_execution_preparation_request(_packet())
    assert request is not None

    def runner(command, *, cwd, timeout):
        if "should-run" not in command:
            return _completed({"ok": True})
        return _completed({
            "ok": True,
            "should_run": True,
            "normal_delivery_allowed": True,
            "selected_todo": {"todo_id": "todo-other"},
            "execution_preparation": {
                "schema_version": "execution_preparation_v0",
                "state": "ready",
                "delivery_allowed": True,
            },
            "heartbeat_receipt": {"turn_instance_id": "turn-other"},
        })

    with pytest.raises(RuntimeError, match="same-Todo ready"):
        execute_codex_execution_preparation(
            request,
            cwd=tmp_path.resolve(),
            scenario="codex_app_ssh_goal",
            runner=runner,
        )
