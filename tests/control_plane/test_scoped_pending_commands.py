"""Historical source recovery commands execute on their original admission."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from loopx.control_plane.projects.registry_codec import source_session_registry_transaction
from loopx.control_plane.quota.settlement import render_settlement_progress_markdown
from tests.control_plane.test_checkpoint_provider_fence import (
    INSTANCE_B, _historical_source_context, _replace_source_goal,
)
from tests.control_plane.test_quota_settlement_cli import _run_cli, _run_generated_cli, _spend_run_count
from tests.control_plane.test_scoped_checkpoint_writer import _decision


def _retained_source(tmp_path, monkeypatch, provider):
    runtime, registry, ref, identity, read = _historical_source_context(
        tmp_path, monkeypatch, provider, "delivery_result")
    read()
    binding = ("--goal-id", identity.goal_id, "--goal-instance-id", ref["goal_instance_id"],
        "--agent-id", identity.agent_id, "--todo-id", identity.todo_id,
        "--turn-instance-id", identity.turn_instance_id)
    rc, retained = _run_cli(registry, runtime, "refresh-state", *binding,
        "--classification", "retained_source_result", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--delivery-workspace-path", str(registry.parent.parent),
        "--no-global-sync", "--suppress-external-sinks")
    assert rc == 0 and retained["appended"] and not retained["vision_checkpoint"]["satisfied"], retained
    return runtime, registry, ref, identity, retained


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_pending_source_projection_commands_retain_result_and_settle_same_turn(
    tmp_path, monkeypatch, provider,
):
    runtime, registry, ref, identity, retained = _retained_source(tmp_path, monkeypatch, provider)
    binding = ("--goal-id", identity.goal_id, "--goal-instance-id", ref["goal_instance_id"],
        "--agent-id", identity.agent_id, "--todo-id", identity.todo_id,
        "--turn-instance-id", identity.turn_instance_id)
    original = Path(retained["json_path"])
    frozen = original.read_bytes()
    pending = retained
    assert pending["settlement_progress"]["state"] == "direction_pending", pending
    read_command, direction_command, spend_command = pending["next_cli_actions"]
    markdown = "\n".join(render_settlement_progress_markdown(pending))
    assert all(command in markdown for command in pending["next_cli_actions"])
    for command in (read_command, direction_command, spend_command):
        tokens = shlex.split(command)
        for flag, value in zip(binding[::2], binding[1::2]):
            assert tokens[tokens.index(flag) + 1] == value
    assert "checkpoint-context" in shlex.split(read_command)
    assert shlex.split(read_command)[shlex.split(read_command).index("--purpose") + 1] == "supplement_checkpoint"

    # Generated spend cannot accept a retained result before its fresh direction.
    rc, denied = _run_generated_cli(spend_command, registry_path=registry, cwd=registry.parent.parent)
    assert rc != 0 and not denied["appended"], denied
    assert _spend_run_count(runtime) == 0 and original.read_bytes() == frozen
    rc, context = _run_generated_cli(read_command, registry_path=registry, cwd=registry.parent.parent)
    assert rc == 0 and context["settlement_identity"] == identity.as_dict(), context
    assert context["basis"]["source"]["goal_ref"] == ref
    decision_file = tmp_path / "current-direction.json"
    decision_file.write_text(json.dumps(_decision()))
    tokens = shlex.split(direction_command)
    tokens[tokens.index("--checkpoint-read-context") + 1] = context["read_context_id"]
    tokens[tokens.index("--agent-vision-json") + 1] = str(decision_file)
    authored = shlex.join(tokens)
    rc, accepted = _run_generated_cli(authored, registry_path=registry, cwd=registry.parent.parent)
    assert rc == 0 and accepted["vision_checkpoint"]["satisfied"], json.dumps({
        key: accepted.get(key) for key in ("ok", "error", "error_code", "refresh_recovery", "vision_checkpoint")})
    assert original.read_bytes() == frozen
    rc, settled = _run_generated_cli(spend_command, registry_path=registry, cwd=registry.parent.parent)
    assert rc == 0 and settled["settlement_progress"]["state"] == "settled", settled
    index = Path(retained["index_path"])
    settled_index = index.read_bytes()
    rc, replay = _run_generated_cli(authored, registry_path=registry, cwd=registry.parent.parent)
    assert rc == 0 and replay["idempotent_replay"] and not replay["appended"], replay
    rc, replay = _run_generated_cli(spend_command, registry_path=registry, cwd=registry.parent.parent)
    assert rc == 0 and replay["idempotent_replay"] and not replay["appended"], replay
    assert index.read_bytes() == settled_index and original.read_bytes() == frozen
    assert _spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("drift", ["requirements", "replacement", "stopped"])
def test_generated_source_direction_rechecks_changes_after_the_read(
    tmp_path, monkeypatch, provider, drift,
):
    runtime, registry, _, _, retained = _retained_source(tmp_path, monkeypatch, provider)
    read_command, direction_command, spend_command = retained["next_cli_actions"]
    rc, context = _run_generated_cli(read_command, registry_path=registry, cwd=registry.parent.parent)
    assert rc == 0, context
    decision_file = tmp_path / "current-direction.json"
    decision_file.write_text(json.dumps(_decision()))
    tokens = shlex.split(direction_command)
    tokens[tokens.index("--checkpoint-read-context") + 1] = context["read_context_id"]
    tokens[tokens.index("--agent-vision-json") + 1] = str(decision_file)
    if drift == "requirements":
        state = Path(context["basis"]["source"]["state_file"])
        state.write_text(state.read_text() + "\n## Acceptance\nVerify the changed requirements.\n")
    elif drift == "replacement":
        _replace_source_goal(registry, INSTANCE_B)
    else:
        with source_session_registry_transaction(registry, operation="fixture_source_stop") as transaction:
            data = transaction.payload_copy()
            data["goals"][0]["activation"] = {"schema_version": "loopx_goal_activation_v1",
                "state": "stopped", "updated_at": "2026-10-10T12:00:00Z", "reason": "Owner stopped the Goal"}
            transaction.commit(data)
    paths = [registry, Path(retained["json_path"]), Path(retained["index_path"])]
    frozen = [path.read_bytes() for path in paths]
    rc, rejected = _run_generated_cli(shlex.join(tokens), registry_path=registry, cwd=registry.parent.parent)
    assert rc != 0 and not rejected["appended"], rejected
    if drift == "requirements":
        assert rejected["error_code"] == "checkpoint_read_context_stale", rejected
    rc, denied = _run_generated_cli(spend_command, registry_path=registry, cwd=registry.parent.parent)
    assert rc != 0 and denied["ok"] is False, denied
    assert [path.read_bytes() for path in paths] == frozen and _spend_run_count(runtime) == 0
