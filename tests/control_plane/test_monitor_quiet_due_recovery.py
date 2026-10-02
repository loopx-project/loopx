"""Compose quiet observation, late binding and monitor closeout through the CLI.

Independent oracle: the pre-due observation cannot settle a later Todo. Its
exact poll commits once, closes only that Turn, and never debits quota. Losing
the poll response permits readback/replay, not another observation or successor.
"""

from __future__ import annotations

import json
import shlex

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID,
    GOAL_ID,
    _add_monitor,
    _write_fixture,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.testing.canary_harness import run_json_cli, run_json_cli_result
from loopx.todos import add_goal_todo, list_goal_todos


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("material", [False, True], ids=["unchanged", "material"])
@pytest.mark.parametrize("peer_gate", [False, True], ids=["no-gate", "peer-gate"])
def test_quiet_turn_binds_due_monitor_and_recovers_lost_response(
    tmp_path,
    monkeypatch,
    provider,
    material,
    peer_gate,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _write_fixture(tmp_path)
    add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Independent work awaiting a dependency",
        task_class="advancement_task",
        status="blocked",
        claimed_by=AGENT_ID,
        agent_id=AGENT_ID,
    )
    monitor = _add_monitor(
        registry, text="Observe a public target", target_key="public-target"
    )
    if provider != "legacy":
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"],
            handoff_mode="soft_claim",
        )
        initialize_canonical_authority(
            runtime, GOAL_ID, projection, state_path=state, provider=provider
        )

    def call(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)

    def polls():
        rows = [
            json.loads(line)
            for line in (runtime / "goals" / GOAL_ID / "runs/index.jsonl")
            .read_text()
            .splitlines()
        ]
        assert not any(
            row["classification"] in {"quota_slot_spent", "state_refreshed"}
            for row in rows
        )
        return [row for row in rows if row["classification"] == "quota_monitor_poll"]

    def todos():
        return call("todo", "list", "--goal-id", GOAL_ID)["todos"]

    scope = (
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "codex_app_ssh_goal",
    )
    first = call("quota", "should-run", *scope, "--begin-turn")
    assert first["effective_action"] == "monitor_quiet_skip", first
    assert "settlement_identity" not in first["heartbeat_receipt"]
    turn = first["heartbeat_receipt"]["turn_instance_id"]
    guard = ("quota", "should-run", *scope, "--turn-instance-id", turn)
    (quiet_poll,) = polls()
    assert quiet_poll.get("todo_id") is None
    assert call(*guard)["should_run"] is False
    assert polls() == [quiet_poll]

    # A supported metadata update makes the synthetic monitor due; no clock
    # sleep or rewrite of provider authority is needed after initialization.
    call(
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        monitor["todo_id"],
        "--next-due-at",
        "2000-01-01T00:00:00Z",
        "--watch-only",
    )
    selected = call(*guard, "--todo-id", monitor["todo_id"])
    assert selected["heartbeat_receipt"]["status"] == "upgraded"
    assert (
        selected["heartbeat_receipt"]["settlement_identity"]["todo_id"]
        == monitor["todo_id"]
    )
    selected = call(*guard)
    assert (
        selected["agent_lane_next_action"]["receipt_bound_monitor_phase"] == "poll_due"
    )
    assert selected["execution_obligation"]["must_attempt_work"] is True
    assert polls() == [quiet_poll]

    if peer_gate:
        call(
            "todo", "add", "--goal-id", GOAL_ID, "--role", "user",
            "--task-class", "user_gate", "--blocks-agent", "codex-main-control",
            "--text", "Choose the peer's destination",
        )
        call(
            "todo", "add", "--goal-id", GOAL_ID, "--role", "user",
            "--task-class", "user_action", "--bound-agent", AGENT_ID,
            "--text", "Read the optional guide",
        )

    poll = (
        "quota",
        "monitor-poll",
        *scope,
        "--turn-instance-id",
        turn,
        "--todo-id",
        monitor["todo_id"],
        "--target-key",
        "public-target",
        "--result-hash",
        "observed-head",
        "--execute",
        *(
            (
                "--material-change",
                "--next-agent-todo",
                "Validate the observed change",
                "--next-action-kind",
                "validate",
            )
            if material
            else ()
        ),
    )
    # Deliberately discard the first process's response. Recovery uses only
    # durable readback and an exact retry, as after a lost caller acknowledgement.
    call(*poll)
    committed = polls()
    assert len(committed) == 2
    assert committed[0] == quiet_poll
    assert committed[1]["todo_id"] == monitor["todo_id"]
    assert (
        committed[1]["quota_monitor_poll_commit"]["effect_id"]
        != quiet_poll["quota_monitor_poll_commit"]["effect_id"]
    )
    committed_todos, committed_state = todos(), state.read_bytes()

    for _ in range(2):
        readback = call(*guard)
        assert readback["selected_todo"]["todo_id"] == monitor["todo_id"]
        assert (
            readback["agent_lane_next_action"]["receipt_bound_monitor_phase"]
            == "settled"
        )
        assert readback["should_run"] is False
        assert readback["execution_obligation"]["must_attempt_work"] is False
        assert (
            readback["interaction_contract"]["agent_channel"]["delivery_allowed"]
            is False
        )
        replay = call(*poll)
        assert replay["replayed"] is True and replay["appended"] is False
        assert replay["turn_continuation"]["current_turn_settled"] is True
        assert polls() == committed
        assert todos() == committed_todos
        assert state.read_bytes() == committed_state

    # A new result is a new intent, not an idempotent retry of this Turn.
    changed_poll = tuple(
        "different-head" if arg == "observed-head" else arg for arg in poll
    )
    rc, conflict = run_json_cli_result(
        *changed_poll, registry_path=registry, runtime_root=runtime
    )
    assert (
        rc != 0 and conflict["error_code"] == "heartbeat_receipt_identity_conflict"
    ), conflict
    assert polls() == committed
    assert todos() == committed_todos
    assert state.read_bytes() == committed_state
    current_monitor = next(
        todo for todo in committed_todos if todo["todo_id"] == monitor["todo_id"]
    )
    assert current_monitor["status"] == "open"  # Turn closeout is not Todo completion.
    assert int(current_monitor.get("material_change_generation") or 0) == int(material)
    successors = [
        todo for todo in committed_todos if todo.get("action_kind") == "validate"
    ]
    assert len(successors) == int(material)
    if material:
        next_turn = call(
            "quota",
            "should-run",
            *scope,
            "--turn-instance-id",
            "independent-successor-turn",
        )
        assert next_turn["selected_todo"]["todo_id"] == successors[0]["todo_id"]
        assert next_turn["execution_obligation"]["must_attempt_work"] is True


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_blocked_bound_monitor_requires_verified_lifecycle_repair(
    tmp_path,
    monkeypatch,
    provider,
):
    """A blocker changes execution eligibility, never the committed binding."""
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _write_fixture(tmp_path)
    monitor = _add_monitor(
        registry,
        text="Observe a public target",
        target_key="public-target",
        next_due_at="2000-01-01T00:00:00Z",
    )
    if provider != "legacy":
        initialize_canonical_authority(
            runtime,
            GOAL_ID,
            build_todo_runtime_shadow_projection(
                goal_id=GOAL_ID,
                todos=list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"],
                handoff_mode="soft_claim",
            ),
            state_path=state,
            provider=provider,
        )

    def call(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)

    scope = (
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "codex_app_ssh_goal",
        "--turn-instance-id",
        "blocked-monitor-turn",
    )
    guard = ("quota", "should-run", *scope)
    initial = call(*guard)
    binding = initial["heartbeat_receipt"]["settlement_identity"]
    assert binding["todo_id"] == monitor["todo_id"]
    call(
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        monitor["todo_id"],
        "--status",
        "blocked",
    )
    before = state.read_bytes()
    for _ in range(2):
        recovery = call(*guard)
        assert recovery["effective_action"] == "unsettled_host_turn_recovery", recovery
        assert recovery["heartbeat_receipt"]["settlement_identity"] == binding
        assert recovery["heartbeat_receipt"]["status"] == "replayed"
        assert recovery["unsettled_host_turn_recovery"]["repair"] == "lifecycle"
        assert recovery["unsettled_host_turn_recovery"]["scope"] == "current_turn"
        assert (
            recovery["interaction_contract"]["agent_channel"]["delivery_allowed"]
            is False
        )
        assert "replan_action_packet" not in recovery
        assert state.read_bytes() == before

    # The fixture's temporary blocker is now independently resolved. Execute
    # the offered conditional metadata repair, then re-enter the original guard.
    actions = recovery["interaction_contract"]["cli_channel"]["next_cli_actions"]
    restore = next(action for action in actions if " todo update " in action)
    assert "--status open" in restore
    command = shlex.split(restore)
    result = run_json_cli(*command[1:], registry_path=registry, runtime_root=runtime)
    assert result["ok"] is True
    resumed = call(*guard)
    assert resumed["heartbeat_receipt"]["settlement_identity"] == binding
    assert (
        resumed["agent_lane_next_action"]["receipt_bound_monitor_phase"] == "poll_due"
    )
    poll = call(
        "quota",
        "monitor-poll",
        *scope,
        "--todo-id",
        monitor["todo_id"],
        "--target-key",
        "public-target",
        "--result-hash",
        "verified-head",
        "--execute",
    )
    assert poll["turn_continuation"]["current_turn_settled"] is True
    assert call(*guard)["should_run"] is False
    rows = [
        json.loads(line)
        for line in (runtime / "goals" / GOAL_ID / "runs/index.jsonl")
        .read_text()
        .splitlines()
    ]
    assert sum(row["classification"] == "quota_monitor_poll" for row in rows) == 1
    assert not any(
        row["classification"] in {"quota_slot_spent", "state_refreshed"} for row in rows
    )
