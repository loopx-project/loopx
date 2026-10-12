from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from loopx.cli_commands import (
    project_lifecycle,
    project_lifecycle_refresh_state,
)
from loopx.cli_runtime import add_subcommand_format, build_cli_parser
from loopx.control_plane.capability_hooks import (
    POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
    PostWritebackHookRegistration,
)
from loopx.extensions.lark.goal_channel_contracts import (
    human_gate_auto_notify_marker_path,
    write_human_gate_auto_notify_marker,
)
from tests.control_plane import test_refresh_external_delivery as settlement_fixtures
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, TURN_ID

settlement_session = settlement_fixtures.session


def _args(*, suppress_external_sinks: bool = False) -> argparse.Namespace:
    parser, subparsers = build_cli_parser()
    project_lifecycle_refresh_state.register_refresh_state_command(
        subparsers, add_subcommand_format
    )
    argv = [
        "refresh-state",
        "--goal-id",
        "goal-public-fixture",
        "--agent-id",
        "agent-public-fixture",
        "--classification",
        "validated",
        "--format",
        "json",
    ]
    if suppress_external_sinks:
        argv.append("--suppress-external-sinks")
    return parser.parse_args(argv)


@pytest.mark.parametrize(
    ("gate_sync", "expected_exit", "expected_ok"),
    [
        (
            {
                "ok": True,
                "enabled": True,
                "status": "sent_verified",
                "delivery_postcondition": {
                    "satisfied": True,
                    "blocks_delivery": False,
                },
            },
            0,
            True,
        ),
        (
            {
                "ok": False,
                "enabled": True,
                "status": "failed",
                "delivery_postcondition": {
                    "satisfied": False,
                    "blocks_delivery": True,
                },
            },
            1,
            False,
        ),
    ],
)
def test_refresh_state_applies_goal_channel_delivery_postcondition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gate_sync: dict[str, Any],
    expected_exit: int,
    expected_ok: bool,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "refresh_state_run",
        lambda **kwargs: {
            "ok": True,
            "appended": True,
            "dry_run": False,
            "classification": "validated",
        },
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_explore_graph_after_material_refresh",
        lambda **kwargs: {
            "enabled": False,
            "delivery_postcondition": {
                "satisfied": True,
                "blocks_delivery": False,
            },
        },
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_human_gate_after_refresh",
        lambda **kwargs: gate_sync,
    )

    result = project_lifecycle.handle_project_lifecycle_command(
        _args(),
        registry_path=tmp_path / ".loopx" / "registry.json",
        print_payload=lambda payload, fmt, renderer: captured.update(payload),
        output_format=lambda args: "json",
        append_cli_rollout_event=lambda *args, **kwargs: {},
    )

    assert result == expected_exit
    assert captured["ok"] is expected_ok
    assert captured["goal_channel_gate_sync"] == gate_sync
    if expected_ok:
        assert "error" not in captured
    else:
        assert "human-gate notification/readback failed" in captured["error"]


def test_refresh_state_forwards_external_sink_suppression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "refresh_state_run",
        lambda **kwargs: {
            "ok": True,
            "appended": True,
            "dry_run": False,
            "classification": "validated",
        },
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_explore_graph_after_material_refresh",
        lambda **kwargs: {
            "enabled": False,
            "delivery_postcondition": {
                "satisfied": True,
                "blocks_delivery": False,
            },
        },
    )

    def sync_gate(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {
            "ok": True,
            "enabled": True,
            "status": "external_sink_suppressed",
            "delivery_postcondition": {
                "satisfied": True,
                "blocks_delivery": False,
            },
        }

    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_human_gate_after_refresh",
        sync_gate,
    )

    result = project_lifecycle.handle_project_lifecycle_command(
        _args(suppress_external_sinks=True),
        registry_path=tmp_path / ".loopx" / "registry.json",
        print_payload=lambda payload, fmt, renderer: None,
        output_format=lambda args: "json",
        append_cli_rollout_event=lambda *args, **kwargs: {},
    )

    assert result == 0
    assert captured_kwargs["external_sink_delivery_authorized"] is False


@pytest.mark.parametrize(
    "constant", ["NaN", "Infinity", "-Infinity"]
)
def test_refresh_state_rejects_non_standard_usage_json_constants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
) -> None:
    """--usage-json is strict JSON: NaN/Infinity must fail before any refresh."""
    captured: dict[str, Any] = {}
    refresh_calls: list[dict[str, Any]] = []

    def _record_refresh(**kwargs: Any) -> dict[str, Any]:
        refresh_calls.append(kwargs)
        return {"ok": True, "appended": True, "dry_run": False}

    monkeypatch.setattr(project_lifecycle_refresh_state, "refresh_state_run", _record_refresh)

    args = _args()
    args.usage_json = (
        '{"input_tokens": 1, "output_tokens": 1, "provider": "p", '
        '"model": "m", "source_snapshot_id": "s", "cost_usd": ' + constant + "}"
    )
    result = project_lifecycle.handle_project_lifecycle_command(
        args,
        registry_path=tmp_path / ".loopx" / "registry.json",
        print_payload=lambda payload, fmt, renderer: captured.update(payload),
        output_format=lambda args: "json",
        append_cli_rollout_event=lambda *a, **kw: {},
    )

    assert result == 1
    assert captured["ok"] is False
    assert "strict JSON" in captured["error"]
    assert refresh_calls == []


@pytest.mark.parametrize(
    ("error_type", "reason"),
    [
        (ValueError, "invalid_input_or_config"),
        (TimeoutError, "timeout"),
        (PermissionError, "permission_denied"),
        (FileNotFoundError, "runtime_unavailable"),
        (OSError, "io_error"),
        (RuntimeError, "unexpected_failure"),
    ],
)
def test_refresh_state_redacts_goal_channel_exception_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    reason: str,
) -> None:
    captured: dict[str, Any] = {}
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "goal-public-fixture",
                        "repo": str(tmp_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    write_human_gate_auto_notify_marker(
        human_gate_auto_notify_marker_path(
            registry_path.parent / "goal-channel.json",
            "goal-public-fixture",
        )
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "refresh_state_run",
        lambda **kwargs: {
            "ok": True,
            "appended": True,
            "dry_run": False,
            "classification": "validated",
        },
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_explore_graph_after_material_refresh",
        lambda **kwargs: {
            "enabled": False,
            "delivery_postcondition": {
                "satisfied": True,
                "blocks_delivery": False,
            },
        },
    )

    def fail_with_private_details(**kwargs: Any) -> dict[str, Any]:
        raise error_type(
            f"private binding failed at {tmp_path}/.loopx/goal-channel.json "
            "for oc_private_fixture"
        )

    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_human_gate_after_refresh",
        fail_with_private_details,
    )

    result = project_lifecycle.handle_project_lifecycle_command(
        _args(),
        registry_path=registry_path,
        print_payload=lambda payload, fmt, renderer: captured.update(payload),
        output_format=lambda args: "json",
        append_cli_rollout_event=lambda *args, **kwargs: {},
    )

    serialized = str(captured)
    assert result == 1
    assert captured["goal_channel_gate_sync"]["blocker"] == (
        "goal_channel_gate_sync_failed"
    )
    assert str(tmp_path) not in serialized
    assert "oc_private_fixture" not in serialized
    assert captured["goal_channel_gate_sync"]["failure"]["stage"] == "lifecycle"
    assert captured["goal_channel_gate_sync"]["failure"]["reason_code"] == reason
    assert captured["goal_channel_gate_sync"]["failure"]["external_write_status"] == "unknown"
    assert f"lifecycle ({reason})" in captured["error"]


def test_notification_failure_keeps_exact_primary_settlement_and_one_spend(
    settlement_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, args, run, journal, index = settlement_session
    failure = {
        "enabled": True, "ok": False, "status": "failed",
        "failure": {"stage": "provider_send", "reason_code": "timeout",
                    "external_write_status": "unknown"},
        "failure_summary": "Goal Channel notification failed at provider_send (timeout)",
        "delivery_postcondition": {"satisfied": False, "blocks_delivery": True},
    }
    monkeypatch.setattr(project_lifecycle_refresh_state, "sync_human_gate_after_refresh",
                        lambda **kwargs: dict(failure))
    first = run(args, expected=1)
    identity = first["settlement_identity"]
    assert first["settlement_progress"]["state"] == "spend_required"
    assert first["refresh_recovery"]["reason"] == "first_writeback"
    assert "provider_send (timeout)" in first["error"]
    rendered = project_lifecycle_refresh_state.render_state_refresh_markdown(first)
    assert "provider_send (timeout)" in rendered and "spend_required" in rendered
    written = index.read_bytes()
    replay = run(args, expected=1)
    assert replay["settlement_identity"] == identity
    assert replay["idempotent_replay"] is True
    rendered_replay = project_lifecycle_refresh_state.render_state_refresh_markdown(replay)
    assert "provider_send (timeout)" in rendered_replay and "spend_required" in rendered_replay
    assert index.read_bytes() == written

    spend = ["quota", "spend-slot", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID,
             "--slots", "1", "--source", "heartbeat", "--execute"]
    run(spend)
    run(spend)
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    assert sum(row.get("classification") == "validated_change" for row in rows) == 1
    settled_index = index.read_bytes()
    monkeypatch.setattr(project_lifecycle_refresh_state, "sync_human_gate_after_refresh",
                        lambda **kwargs: {"enabled": False, "ok": True})
    recovered = run(args)
    assert recovered["settlement_identity"] == identity
    assert recovered["settlement_progress"]["state"] == "settled"
    assert index.read_bytes() == settled_index


def test_refresh_state_dispatches_and_replays_post_writeback_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    runtime_root = tmp_path / "runtime"
    state_path = tmp_path / "goal.md"
    state_path.write_text("# Goal\n", encoding="utf-8")
    args = _args()
    args.todo_id = "todo-stage"
    args.turn_instance_id = "turn-stage"
    args.replan_obligation_id = None
    calls = 0

    def producer(value: Mapping[str, Any]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        receipt = value["receipt"]
        return {
            "schema_version": POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "fixture.stage",
            "capability_id": "fixture-capability",
            "phase": "post_writeback",
            "status": "intent",
            "intent": {
                "schema_version": "loopx_capability_intent_v0",
                "intent_kind": "fixture.evaluate",
                "idempotency_key": "fixture:stage-1",
                "source_receipt_id": receipt["event_id"],
                "payload": {"stage_identity": "stage-1"},
                "requested_write_scope": [],
            },
        }

    hook = PostWritebackHookRegistration(
        hook_id="fixture.stage",
        capability_id="fixture-capability",
        event_kinds=("refresh_state",),
        intent_kinds=("fixture.evaluate",),
        requested_read_scope=("stage_completion",),
        producer=producer,
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "refresh_state_run",
        lambda **kwargs: {
            "ok": True,
            "appended": True,
            "dry_run": False,
            "goal_id": args.goal_id,
            "agent_id": args.agent_id,
            "classification": "validated",
            "generated_at": "2026-08-30T08:00:00Z",
            "state": {"path": str(state_path)},
            "settlement_identity": {
                "effect_id": "goal-public-fixture:agent-public-fixture:todo-stage:turn-stage"
            },
        },
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "read_heartbeat_settlement",
        lambda *args, **kwargs: SimpleNamespace(
            identity=SimpleNamespace(value=None),
            delivery=SimpleNamespace(failure=None),
            progress={
                "schema_version": "quota_settlement_progress_v0",
                "state": "settled",
                "next_step": None,
                "quota_spend_source": "heartbeat",
            },
        ),
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "settlement_result_payload",
        lambda result: {"status": "settled"},
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "resolve_runtime_root",
        lambda *args, **kwargs: runtime_root,
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_explore_graph_after_material_refresh",
        lambda **kwargs: {"enabled": False},
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_human_gate_after_refresh",
        lambda **kwargs: {"enabled": False},
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"common_runtime_root": str(runtime_root), "goals": []}),
        encoding="utf-8",
    )

    for _ in range(2):
        result = project_lifecycle.handle_project_lifecycle_command(
            args,
            registry_path=registry_path,
            print_payload=lambda payload, fmt, renderer: captured.update(payload),
            output_format=lambda args: "json",
            append_cli_rollout_event=lambda *args, **kwargs: {},
            post_writeback_hooks=(hook,),
            post_writeback_projection_builder=lambda **kwargs: {
                "stage_completion": {"stage_identity": "stage-1"}
            },
        )
        assert result == 0

    dispatch = captured["post_writeback_hooks"]
    assert calls == 1
    assert dispatch["intent_count"] == 1
    assert dispatch["invoked_count"] == 0
    assert dispatch["replayed_hooks"] == ["fixture.stage"]
    assert dispatch["external_writes_performed"] is False


def test_refresh_state_disabled_post_writeback_hook_has_zero_projection_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection_calls = 0

    def projection(**kwargs: Any) -> dict[str, object]:
        nonlocal projection_calls
        projection_calls += 1
        return {}

    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "refresh_state_run",
        lambda **kwargs: {
            "ok": True,
            "appended": True,
            "dry_run": False,
            "classification": "validated",
        },
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_explore_graph_after_material_refresh",
        lambda **kwargs: {"enabled": False},
    )
    monkeypatch.setattr(
        project_lifecycle_refresh_state,
        "sync_human_gate_after_refresh",
        lambda **kwargs: {"enabled": False},
    )

    result = project_lifecycle.handle_project_lifecycle_command(
        _args(),
        registry_path=tmp_path / "registry.json",
        print_payload=lambda payload, fmt, renderer: None,
        output_format=lambda args: "json",
        append_cli_rollout_event=lambda *args, **kwargs: {},
        post_writeback_hooks=(),
        post_writeback_projection_builder=projection,
    )

    assert result == 0
    assert projection_calls == 0
