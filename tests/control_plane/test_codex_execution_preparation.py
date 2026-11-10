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
ORIGINAL_DIGEST = "a" * 64
OBSERVED_DIGEST = "b" * 64
READY_DIGEST = "c" * 64
CLAIM = f"todo-claim:codex-capture:{ORIGINAL_DIGEST}"


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


@pytest.mark.parametrize("turn", ["turn-example", None])
def test_compact_request_accepts_only_exact_host_action(tmp_path: Path, turn: str | None) -> None:
    packet = _packet(tmp_path, turn=turn)
    request = compact_execution_preparation_request(packet)
    assert request == {
        "goal_id": GOAL,
        "agent_id": AGENT,
        "todo_id": TODO,
        **({"turn_instance_id": turn} if turn else {}),
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


def _bound_request(tmp_path: Path) -> dict:
    request = compact_execution_preparation_request(_packet(tmp_path), claim_source_sha256=ORIGINAL_DIGEST)
    assert request is not None
    return {**request, "source_sha256": OBSERVED_DIGEST}


@pytest.mark.parametrize("scenario", ["codex_app_heartbeat", "codex_app_ssh_goal"])
def test_executor_claims_then_proves_same_turn_ready(tmp_path: Path, scenario: str) -> None:
    request = _bound_request(tmp_path)
    capture, native = tmp_path / "ready", tmp_path / "native"
    calls: list[list[str]] = []

    def runner(command, *, cwd, timeout):
        calls.append(list(command))
        if "should-run" not in command:
            return _completed({"ok": True, "claimed": True,
                "claim_operation_id": CLAIM})
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
            "capture_sha256": READY_DIGEST,
        })

    receipt = execute_codex_execution_preparation(
        request,
        cwd=tmp_path.resolve(),
        scenario=scenario,
        capture_directory=capture,
        context_directory=native,
        runner=runner,
    )

    assert receipt["event"] == "host_execution_prepared"
    assert receipt["normal_delivery_allowed"] is True
    assert receipt["claim_operation_id"] == CLAIM
    assert receipt["turn_instance_id"] == "turn-example"
    assert receipt["capture_sha256"] == READY_DIGEST
    assert receipt["capture_path"] == str(capture / "decision.json")
    assert len(calls) == 2
    for call in calls:
        assert call[call.index("--registry") + 1] == (
            str((tmp_path / "loopx-project-a" / ".loopx" / "registry.json").resolve())
        )
        assert call[call.index("--runtime-root") + 1] == str((tmp_path / "loopx-runtime-a").resolve())
    assert calls[0][-len(request["claim_args"]):] == request["claim_args"]
    assert calls[0][-2:] == ["--claim-operation-id", CLAIM]
    assert calls[1][-12:] == [
        "--context-delivery", "on", "--codex-context-dir", str(native), "--defer-context-presentation",
        "--todo-id", TODO, "--turn-instance-id", "turn-example",
        "--decision-output-dir", str(capture), "--prepared-capture-reference",
    ]
    context = codex_execution_preparation_context(receipt)
    assert "do not repeat" in context and '"state":"ready"' in context


@pytest.mark.parametrize("changed", ["goal", "agent", "todo", "turn"])
def test_executor_rejects_changed_todo_or_turn(tmp_path: Path, changed: str) -> None:
    request = _bound_request(tmp_path)

    def runner(command, *, cwd, timeout):
        if "should-run" not in command:
            return _completed({"ok": True, "claim_operation_id": CLAIM})
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
            capture_directory=tmp_path / "ready",
            context_directory=tmp_path / "native",
            runner=runner,
        )


@pytest.mark.parametrize("fault", ["unbound", "missing-source", "missing-turn", "readback-only"])
def test_executor_does_not_run_a_claim_without_live_binding(tmp_path: Path, fault: str) -> None:
    request = _bound_request(tmp_path)
    capture, native = tmp_path / "ready", tmp_path / "native"
    if fault == "unbound":
        request = compact_execution_preparation_request(_packet(tmp_path, turn=None))
        assert request is not None
        capture = native = None
    elif fault == "missing-source":
        request.pop("source_sha256")
    elif fault == "missing-turn":
        request.pop("turn_instance_id")
    else:
        request["readback_only"] = True

    with pytest.raises(ValueError, match="bound captured Turn"):
        execute_codex_execution_preparation(
            request, cwd=tmp_path.resolve(), scenario="codex_app_heartbeat",
            capture_directory=capture, context_directory=native,
            runner=lambda *_args, **_kwargs: pytest.fail("unbound preparation must not run a subprocess"),
        )
