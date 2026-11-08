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


def _packet(tmp_path: Path, *, turn: str | None = "turn-example") -> dict[str, object]:
    registry = (tmp_path / "loopx-project-a" / ".loopx" / "registry.json").resolve()
    runtime_root = (tmp_path / "loopx-runtime-a").resolve()
    return {
        "ok": True,
        "goal_id": GOAL,
        "registry": str(registry),
        "runtime_root": str(runtime_root),
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


def test_compact_request_accepts_only_exact_host_action(tmp_path: Path) -> None:
    packet = _packet(tmp_path)
    request = compact_execution_preparation_request(packet)
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
            "registry_path": str((tmp_path / "loopx-project-a" / ".loopx" / "registry.json").resolve()),
            "runtime_root": str((tmp_path / "loopx-runtime-a").resolve()),
        },
    }

    malformed = _packet(tmp_path)
    malformed["execution_preparation"]["claim"]["cli_args"][-1] = "agent-b"
    assert compact_execution_preparation_request(malformed) is None


@pytest.mark.parametrize("scenario", ["codex_app_heartbeat", "codex_app_ssh_goal"])
@pytest.mark.parametrize("turn", ["turn-example", None])
def test_executor_claims_then_proves_same_turn_ready(tmp_path: Path, scenario: str, turn: str | None) -> None:
    request = compact_execution_preparation_request(_packet(tmp_path, turn=turn))
    assert request is not None
    calls: list[list[str]] = []

    def runner(command, *, cwd, timeout):
        calls.append(list(command))
        if "should-run" not in command:
            return _completed({"ok": True, "claimed": True,
                "claim_operation_id": "todo-claim:goal-example:todo_example:original"})
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
            **({"heartbeat_receipt": {"turn_instance_id": turn}} if turn is not None else {}),
        })

    receipt = execute_codex_execution_preparation(
        request,
        cwd=tmp_path.resolve(),
        scenario=scenario,
        runner=runner,
    )

    assert receipt["event"] == "host_execution_prepared"
    assert receipt["normal_delivery_allowed"] is True
    assert receipt["claim_operation_id"] == "todo-claim:goal-example:todo_example:original"
    assert len(calls) == 2
    for call in calls:
        assert call[call.index("--registry") + 1] == (
            str((tmp_path / "loopx-project-a" / ".loopx" / "registry.json").resolve())
        )
        assert call[call.index("--runtime-root") + 1] == str((tmp_path / "loopx-runtime-a").resolve())
    assert calls[0][-len(request["claim_args"]):] == request["claim_args"]
    if turn is not None:
        assert calls[1][-4:] == ["--todo-id", TODO, "--turn-instance-id", turn]
    else:
        assert calls[1][-2:] == ["--todo-id", TODO]
        assert "--turn-instance-id" not in calls[1] and "turn_instance_id" not in receipt
    context = codex_execution_preparation_context(receipt)
    assert "do not repeat" in context and '"state":"ready"' in context


@pytest.mark.parametrize("changed", ["goal", "agent", "todo", "turn"])
def test_executor_rejects_changed_todo_or_turn(tmp_path: Path, changed: str) -> None:
    request = compact_execution_preparation_request(_packet(tmp_path))
    assert request is not None

    def runner(command, *, cwd, timeout):
        if "should-run" not in command:
            return _completed({"ok": True})
        return _completed({
            "ok": True,
            "goal_id": "goal-other" if changed == "goal" else GOAL,
            "should_run": True,
            "normal_delivery_allowed": True,
            "selected_todo": {"todo_id": "todo-other" if changed == "todo" else TODO},
            "execution_preparation": {
                "schema_version": "execution_preparation_v0",
                "state": "ready",
                "todo_id": TODO,
                "agent_id": "agent-other" if changed == "agent" else AGENT,
                "delivery_allowed": True,
            },
            "heartbeat_receipt": {"turn_instance_id": "turn-other" if changed == "turn" else "turn-example"},
        })

    with pytest.raises(RuntimeError, match="same-Todo ready"):
        execute_codex_execution_preparation(
            request,
            cwd=tmp_path.resolve(),
            scenario="codex_app_ssh_goal",
            runner=runner,
        )
