from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from loopx.cli import build_parser
from loopx.cli_commands.task_lease import handle_task_lease_command


def _run_acquire(monkeypatch, result: dict) -> dict:
    captured: list[dict] = []
    import loopx.cli_commands.task_lease as task_lease_cli

    monkeypatch.setattr(
        task_lease_cli,
        "execute_native_task_lease_acquire",
        lambda **kwargs: deepcopy(result),
    )
    args = build_parser().parse_args(
        [
            "task-lease",
            "acquire",
            "--goal-id",
            "cli-native-goal",
            "--todo-id",
            "todo_cli_native",
            "--owner",
            "codex-cli-agent",
            "--idempotency-key",
            "cli-native-key",
            "--ttl-seconds",
            "300",
        ]
    )
    status = handle_task_lease_command(
        args,
        registry_path=Path("/tmp/registry.json"),
        runtime_root_arg=None,
        output_format=lambda _args: "json",
        print_payload=lambda payload, _fmt, _render: captured.append(payload),
    )
    assert status == (0 if result.get("ok") else 1)
    return captured[0]


def _run_acquire_raising(monkeypatch, error: Exception) -> dict:
    captured: list[dict] = []
    import loopx.cli_commands.task_lease as task_lease_cli

    def raise_error(**_kwargs):
        raise error

    monkeypatch.setattr(task_lease_cli, "execute_native_task_lease_acquire", raise_error)
    args = build_parser().parse_args(
        [
            "task-lease",
            "acquire",
            "--goal-id",
            "cli-native-goal",
            "--todo-id",
            "todo_cli_native",
            "--owner",
            "codex-cli-agent",
            "--idempotency-key",
            "cli-native-key",
            "--ttl-seconds",
            "300",
        ]
    )
    status = handle_task_lease_command(
        args,
        registry_path=Path("/tmp/registry.json"),
        runtime_root_arg=None,
        output_format=lambda _args: "json",
        print_payload=lambda payload, _fmt, _render: captured.append(payload),
    )
    assert status == 1
    return captured[0]


def _assert_refusal(payload: dict, expected: dict) -> None:
    inspection = payload["inspection"]
    assert set(inspection) == {"cli_args", "reason", "execution_authority_granted"}
    assert inspection["cli_args"] == [
        f"--registry={Path('/tmp/registry.json')}", "--format", "json",
        "task-lease", "inspect", "--goal-id=cli-native-goal",
        "--todo-id=todo_cli_native",
    ]
    assert isinstance(inspection["reason"], str) and inspection["reason"].strip()
    assert inspection["execution_authority_granted"] is False
    assert {key: value for key, value in payload.items() if key != "inspection"} == expected


def test_cli_envelope_keys_win_over_a_typed_exception_payload(monkeypatch) -> None:
    """A typed payload that carries its own schema_version cannot clobber the envelope's."""

    from loopx.control_plane.coordination.local_authority import (
        LocalCoordinationAuthorityUnavailable,
    )

    error = LocalCoordinationAuthorityUnavailable(
        "canonical Todo authority is unavailable",
        code="local_authority_todo_list_unavailable",
        payload={
            "ok": True,
            "schema_version": "loopx_local_coordination_todo_list_result_v0",
            "action": "release",
            "error": "payload must not replace the original refusal",
            "error_code": "payload_error",
            "status": "missing",
            "source_authority": "file_v0",
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        },
    )
    _assert_refusal(_run_acquire_raising(monkeypatch, error), {
        "ok": False,
        "schema_version": "task_lease_v0",
        "action": "acquire",
        "error": "canonical Todo authority is unavailable",
        "error_code": "local_authority_todo_list_unavailable",
        "status": "missing",
        "source_authority": "file_v0",
        "decision_read_from_provider": True,
        "legacy_fallback_used": False,
    })


def test_cli_acquire_passes_through_native_success(monkeypatch) -> None:
    result = {
        "ok": True,
        "schema_version": "task_lease_v0",
        "action": "acquire",
        "acquired": True,
        "idempotent": False,
        "lease": {
            "goal_id": "cli-native-goal",
            "todo_id": "todo_cli_native",
            "owner": "codex-cli-agent",
            "version": 1,
        },
        "lease_path": "/tmp/runtime/goals/cli-native-goal/task-leases/todo_cli_native.json",
        "settlement": {
            "effect_id": "goal:agent:todo:key",
            "receipts": [
                {
                    "step": "durable_writeback",
                    "status": "committed",
                    "effect_id": "goal:agent:todo:key",
                }
            ],
        },
    }
    assert _run_acquire(monkeypatch, result) == result


def test_cli_acquire_passes_through_native_idempotent_replay(monkeypatch) -> None:
    result = {
        "ok": True,
        "schema_version": "task_lease_v0",
        "action": "acquire",
        "acquired": False,
        "idempotent": True,
        "lease": {"todo_id": "todo_cli_native", "owner": "codex-cli-agent"},
        "lease_path": "/tmp/runtime/goals/cli-native-goal/task-leases/todo_cli_native.json",
        "settlement": {
            "effect_id": "goal:agent:todo:key",
            "receipts": [
                {
                    "step": "durable_writeback",
                    "status": "idempotent",
                    "effect_id": "goal:agent:todo:key",
                }
            ],
        },
    }
    assert _run_acquire(monkeypatch, result) == result


def test_cli_acquire_passes_through_native_typed_failure(monkeypatch) -> None:
    result = {
        "ok": False,
        "schema_version": "task_lease_v0",
        "action": "acquire",
        "error": "idempotency key was reused with different acquire parameters",
        "error_code": "idempotency_key_reuse",
        "lease_path": "/tmp/runtime/goals/cli-native-goal/task-leases/todo_cli_native.json",
        "settlement": {
            "effect_id": "goal:agent:todo:key",
            "receipts": [],
            "failure": {
                "step": "durable_writeback",
                "kind": "invalid_identity",
                "code": "idempotency_key_reuse",
            },
        },
    }
    expected = deepcopy(result)
    actual = _run_acquire(monkeypatch, result)
    assert result == expected
    _assert_refusal(actual, expected)
