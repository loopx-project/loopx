"""Real CLI characterization: Turn settlement is not terminal Todo acceptance."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from test_quota_settlement_cli import (
    AGENT_ID,
    ALTERNATIVE_TODO_ID,
    GOAL_ID,
    TODO_ID,
    _configure_completion_validation_todo,
    _configure_selectable_alternative,
    _run_cli,
    _spend_run_count,
    _write_fixture,
)


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("waiting", [False, True])
def test_in_flight_turn_replay_preserves_open_todo_and_fresh_turn_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    waiting: bool,
) -> None:
    if provider == "sqlite":
        # The shared SQLite fixture predates typed test helpers.
        isolate_sqlite_runtime(tmp_path, monkeypatch)  # type: ignore[no-untyped-call]
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_completion_validation_todo(project)
    _configure_selectable_alternative(project)

    def cli(*args: str) -> dict[str, Any]:
        rc, result = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0, result
        return result

    def todo() -> dict[str, Any]:
        listed = cli("todo", "list", "--goal-id", GOAL_ID)
        return next(item for item in listed["todos"] if item["todo_id"] == TODO_ID)

    if provider != "legacy":
        listed = cli("todo", "list", "--goal-id", GOAL_ID)
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=listed["todos"], handoff_mode="soft_claim",
        )
        initialize_canonical_authority(
            runtime, GOAL_ID, projection, state_path=state, provider=provider,
        )
    before = todo()
    turn_id = f"inflight-{provider}-{waiting}"
    binding = ("--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", turn_id)
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID, "--scan-path", str(project))
    first = cli(*guard, *binding)
    assert first["should_run"] is True
    refresh = cli(
        "refresh-state", "--goal-id", GOAL_ID, *binding,
        "--classification", "validated_intermediate_progress",
        "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation",
        "--no-global-sync", "--suppress-external-sinks",
    )
    assert refresh["vision_checkpoint"]["satisfied"] is True
    spend_args = ("quota", "spend-slot", "--goal-id", GOAL_ID, *binding,
                  "--slots", "1", "--source", "heartbeat", "--execute")
    spend = cli(*spend_args)
    assert spend["appended"] is True
    assert spend["settlement_progress"]["state"] == "settled"
    if waiting:
        cli(
            "todo", "update", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--todo-id", TODO_ID, "--status", "open",
            "--resume-when", "resume_at:2099-01-01T00:00:00Z",
            "--successor-todo-id", ALTERNATIVE_TODO_ID,
            "--reason", "Wait for independent evidence; continue the runnable successor.",
        )

    # Argument-less and explicit reentry must both respect the original receipt,
    # even if the live frontier now recommends a different Todo.
    for selection in ((), ("--todo-id", TODO_ID)):
        replay = cli(*guard, "--agent-id", AGENT_ID, "--turn-instance-id", turn_id, *selection)
        assert replay["should_run"] is False, replay
        assert replay["effective_action"] == "heartbeat_settled_skip"
        assert replay["execution_obligation"]["kind"] == "heartbeat_settled_skip"
        assert replay["execution_obligation"]["must_attempt_work"] is False
        assert replay["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID
        assert replay["interaction_contract"]["agent_channel"]["must_attempt"] is False
        assert replay["interaction_contract"]["cli_channel"]["spend_after_validation"] is False
        envelope = build_turn_envelope(replay)
        assert envelope["contract_capsule"]["execution_obligation"]["must_attempt_work"] is False
        assert envelope["writeback"]["spend_after_validation"] is False
    spend_replay = cli(*spend_args)
    assert spend_replay["appended"] is False
    assert _spend_run_count(runtime) == 1
    after = todo()
    assert after["status"] == "open"
    assert after["completion_validation_required"] is True
    assert after["completion_validation_sha256"] == before["completion_validation_sha256"]

    fresh = cli(*guard, "--agent-id", AGENT_ID, "--turn-instance-id", f"{turn_id}-next")
    assert fresh["should_run"] is True, fresh
    assert fresh["effective_action"] != "unsettled_host_turn_recovery"
    assert fresh["selected_todo"]["todo_id"] == (ALTERNATIVE_TODO_ID if waiting else TODO_ID)
    assert _spend_run_count(runtime) == 1
