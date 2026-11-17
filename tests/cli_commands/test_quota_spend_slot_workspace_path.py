from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from loopx.cli import build_parser, main
import loopx.cli_commands.quota as quota_command
from loopx.cli_commands.quota_context import QuotaCommandContext
from loopx.cli_commands.quota_request import (
    validate_quota_command_request,
    validate_scheduler_observation_request,
)
from loopx.control_plane.quota.error_codes import QuotaCommandValidationError


def test_quota_spend_slot_forwards_delivery_workspace_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    registry = tmp_path / "registry.json"
    registry.write_text("{}", encoding="utf-8")

    context = QuotaCommandContext(
        runtime_root=tmp_path / "runtime",
        status_limit=1,
        status_goal_id="fixture-goal",
        status_payload={},
        cache_metadata=None,
        scheduler_context=None,
        operator_inbox_urgency_projector=lambda **_kwargs: {},
        detail_sections=frozenset(),
        heartbeat_turn_id=None,
    )
    captured_spend_kwargs: list[dict[str, object]] = []

    def fake_spend(*_args: object, **kwargs: object) -> dict[str, object]:
        captured_spend_kwargs.append(kwargs)
        return {"ok": True, "mode": "spend-slot", "appended": False, "dry_run": True}

    monkeypatch.setattr(
        quota_command, "_prepare_quota_command_execution", lambda *_a, **_kw: (None, None, context)
    )
    monkeypatch.setattr(quota_command, "spend_quota_slot", fake_spend)
    monkeypatch.setattr(quota_command, "should_log_quota", lambda *_a: False)
    monkeypatch.setattr(quota_command, "observe_quota_result", lambda *_a, **_kw: None)

    args = build_parser().parse_args(
        [
            "--format",
            "json",
            "quota",
            "spend-slot",
            "--goal-id",
            "fixture-goal",
            "--agent-id",
            "fixture-agent",
            "--delivery-workspace-path",
            "~/worktree",
        ]
    )
    output: list[dict[str, object]] = []
    rc = quota_command.handle_quota_command(
        args,
        registry_path=registry,
        runtime_root_arg=str(tmp_path / "runtime"),
        print_payload=lambda payload, *_a: output.append(payload),
        append_cli_rollout_event=Mock(),
    )
    assert rc == 0, output
    assert captured_spend_kwargs[-1]["workspace_path"] == worktree

    args_omitted = build_parser().parse_args(
        [
            "--format",
            "json",
            "quota",
            "spend-slot",
            "--goal-id",
            "fixture-goal",
            "--agent-id",
            "fixture-agent",
        ]
    )
    rc_omitted = quota_command.handle_quota_command(
        args_omitted,
        registry_path=registry,
        runtime_root_arg=str(tmp_path / "runtime"),
        print_payload=lambda payload, *_a: output.append(payload),
        append_cli_rollout_event=Mock(),
    )
    assert rc_omitted == 0
    assert captured_spend_kwargs[-1]["workspace_path"] is None


@pytest.mark.parametrize(
    "subcommand",
    ["status", "plan", "should-run", "monitor-poll", "void-slot", "scheduler-ack"],
)
def test_delivery_workspace_path_is_rejected_outside_spend_slot(
    subcommand: str, capsys: pytest.CaptureFixture[str],
) -> None:
    extra = (
        ["--void-generated-at", "2026-01-01T00:00:00Z"]
        if subcommand == "void-slot"
        else []
    )
    args = build_parser().parse_args(
        [
            "--format",
            "json",
            "quota",
            subcommand,
            "--goal-id",
            "fixture-goal",
            "--agent-id",
            "fixture-agent",
            "--delivery-workspace-path",
            "/tmp/example-worktree",
            *extra,
        ]
    )
    with pytest.raises(
        QuotaCommandValidationError,
        match="--delivery-workspace-path is only valid with `loopx quota spend-slot`",
    ):
        validate_quota_command_request(args)

    rc = main(
        [
            "--format",
            "json",
            "quota",
            subcommand,
            "--goal-id",
            "fixture-goal",
            "--agent-id",
            "fixture-agent",
            "--delivery-workspace-path",
            "/tmp/example-worktree",
            *extra,
        ]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    message_field = (
        "reason"
        if subcommand in {"should-run", "monitor-poll", "void-slot", "scheduler-ack"}
        else "error"
    )
    assert (
        "--delivery-workspace-path is only valid with `loopx quota spend-slot`"
        in payload[message_field]
    )


def test_scheduler_observation_rejects_delivery_workspace_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = [
        "--format",
        "json",
        "quota",
        "should-run",
        "--goal-id",
        "fixture-goal",
        "--agent-id",
        "fixture-agent",
        "--scheduler-observation",
        "--delivery-workspace-path",
        "/tmp/example-worktree",
    ]
    args = build_parser().parse_args(argv)
    with pytest.raises(
        QuotaCommandValidationError,
        match="--scheduler-observation cannot be combined with --delivery-workspace-path",
    ):
        validate_scheduler_observation_request(args)

    rc = main(argv)
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "--delivery-workspace-path" in payload["reason"]
