"""Characterize the host settlement adapter's own partial-step behavior.

C4.3's obligation comparison recorded that the host Todo adapter keeps no
journal of its own: the durable settlement receipts live in the CLI, and the
TypeScript owner classifies a stopped plan. What was still uncharacterized is
the adapter's own behavior when a step succeeds and a later step fails, and
where the recovery boundary between the adapter and the CLI receipts sits.

These cases pin that behavior before any recovery change touches this path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from loopx.control_plane import host_adapter_settlement
from loopx.control_plane.coordination.goal_storage_initialization import (
    initialize_goal_storage_target,
)
from loopx.control_plane.host_adapter_settlement import (
    HostTodoSettlementRequest,
    settle_host_todo_completion,
)
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo


GOAL_ID = "adapter-partial-steps-fixture"
AGENT_ID = "claude"
TODO_ID = "todo_abc123"


def _request() -> HostTodoSettlementRequest:
    return HostTodoSettlementRequest(
        goal_id="goal",
        agent_id="agent",
        todo_id=TODO_ID,
        runtime_profile="claude_code",
        legacy_host_surface="claude_code",
        scheduler_owner="agent_cli_loop",
        execution_mode="interactive",
        completion_args=("todo", "complete", TODO_ID),
    )


def _guard_payload() -> str:
    """The guard shape the TypeScript owner accepts as a Todo selection."""
    return json.dumps({"ok": True, "selected_todo": {"todo_id": TODO_ID}})


def test_an_unreadable_step_output_stops_the_plan_and_the_owner_classifies_it() -> None:
    """The adapter stops; the TypeScript owner decides what the stop means."""
    calls: list[list[str]] = []

    def provider(args: list[str], *, legacy_args: list[str] | None = None) -> str:
        del legacy_args
        calls.append(args)
        # A CLI failure returns its stderr, which is not JSON at all.
        return "quota should-run failed: synthetic CLI failure"

    result = json.loads(settle_host_todo_completion(_request(), run_cli=provider))

    assert [call[:2] for call in calls] == [["quota", "should-run"]]
    assert result["ok"] is False
    assert result["settlement_blocked_completion"] is True
    assert result["settlement"]["failed_stage"] == "guard"
    assert result["settlement"]["reason"] == "quota guard returned malformed JSON"


def test_a_raising_step_leaves_no_finalize_and_no_adapter_local_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed step ends the call before the owner is asked to finalize."""
    phases: list[str] = []
    runtime_result = host_adapter_settlement.effect_runtime_result

    def spy(method: str, params: dict[str, object]) -> object:
        phases.append(str(params.get("phase")))
        return runtime_result(method, params)

    monkeypatch.setattr(host_adapter_settlement, "effect_runtime_result", spy)

    calls: list[list[str]] = []

    def provider(args: list[str], *, legacy_args: list[str] | None = None) -> str:
        del legacy_args
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("synthetic CLI step failure")
        return _guard_payload()

    with pytest.raises(RuntimeError, match="synthetic CLI step failure"):
        settle_host_todo_completion(_request(), run_cli=provider)

    # The finalize phase never ran, so the owner never classified the failure.
    assert phases == ["prepare"]
    # The failing step was attempted; the earlier step had already succeeded.
    assert [call[:2] for call in calls] == [
        ["quota", "should-run"],
        ["todo", "complete"],
    ]

    calls.clear()
    phases.clear()
    with pytest.raises(RuntimeError, match="synthetic CLI step failure"):
        settle_host_todo_completion(_request(), run_cli=provider)

    # A retry re-enters the plan at its first step. The adapter holds no
    # record of the step that already succeeded and never resumes mid-plan.
    assert phases == ["prepare"]
    assert [call[:2] for call in calls] == [
        ["quota", "should-run"],
        ["todo", "complete"],
    ]


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "status: active",
                "updated_at: 2026-10-29T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "creation_operation_id": f"goal-create:{GOAL_ID}",
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file.name,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "slot_minutes": 1,
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                            "storage_target": {
                                "schema_version": "loopx_new_goal_storage_target_v1",
                                "provider": "file",
                                "handoff_mode": "soft_claim",
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    goal = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    initialized = initialize_goal_storage_target(
        tmp_path / "runtime",
        goal,
        registry_path=registry,
    )
    assert initialized["authority_initialized"] is True
    assert initialized["legacy_fallback_used"] is False
    return registry, state_file


def _control(registry: Path) -> GoalModeMCPControlPlane:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx-test",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {
            "goal_id": GOAL_ID,
            "registry": str(registry),
            "agent_id": AGENT_ID,
        },
    )
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli"]
    return control


def test_a_raising_step_after_a_durable_commit_recovers_through_the_cli_receipt(
    tmp_path: Path,
) -> None:
    """The recovery boundary: the CLI owns the receipt, not the adapter."""
    registry, _ = _write_fixture(tmp_path)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Characterize the partial-step recovery boundary.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
    )
    todo_id = str(added["todo_id"])
    control = _control(registry)
    original = control.run_cli
    injected = False
    bound_identity: dict[str, object] = {}
    bound_turn_instance_id = ""
    bound_effect_id = ""
    calls: list[list[str]] = []

    def raise_after_commit(args: list[str], **kwargs: object) -> str:
        nonlocal injected, bound_identity, bound_turn_instance_id, bound_effect_id
        calls.append(list(args))
        output = original(args, **kwargs)  # type: ignore[arg-type]
        if not injected and args[:2] == ["quota", "spend-slot"]:
            committed = json.loads(output)
            if committed.get("appended") is True:
                injected = True
                bound_identity = dict(committed["settlement_identity"])
                bound_turn_instance_id = str(bound_identity["turn_instance_id"])
                bound_effect_id = str(bound_identity["effect_id"])
                raise RuntimeError("synthetic CLI transport failure after commit")
        return output

    control.run_cli = raise_after_commit  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="synthetic CLI transport failure"):
        control.complete_task(
            todo_id,
            AGENT_ID,
            "partial-step fixture check passed",
            no_follow_up=True,
        )

    assert injected
    readback = read_heartbeat_settlement(
        tmp_path / "runtime",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=todo_id,
        turn_instance_id=bound_turn_instance_id,
    )
    # The spend receipt was committed before the failure, and the adapter left
    # nothing of its own behind it.
    assert readback.replay_phase == "settled"
    assert readback.progress is not None
    assert readback.progress["state"] == "settled"
    # Only the spend stage was reached, so only its receipts exist. The
    # validation receipt is committed even when the plan stops early.
    assert _committed_receipt_kinds(readback) == [
        "validation",
        "durable_writeback",
        "quota_spend",
    ]

    calls.clear()
    replay = json.loads(
        control.complete_task(
            todo_id,
            AGENT_ID,
            "partial-step fixture check passed",
            no_follow_up=True,
        )
    )

    assert replay["ok"] is True, json.dumps(replay, indent=2, sort_keys=True)
    # The retry reuses the same settlement identity: the identity is derived
    # from the same intent, not from adapter-local state.
    assert replay["settlement_identity"]["turn_instance_id"] == bound_turn_instance_id
    assert replay["settlement_identity"]["effect_id"] == bound_effect_id
    assert replay["settlement_identity"]["todo_id"] == bound_identity["todo_id"]
    # The retry replays the CLI receipt instead of spending a second slot.
    assert replay["settlement"]["quota_spend"]["appended"] is False
    assert replay["settlement"]["quota_spend"]["idempotent_replay"] is True
    # The retry re-entered the plan at its first step: no adapter-local resume.
    assert calls[0][:2] == ["quota", "should-run"]

    status = json.loads(control.should_run())
    assert status["quota"]["spent_slots"] == 1  # type: ignore[index]


def test_a_raised_step_before_completion_commits_no_settlement(
    tmp_path: Path,
) -> None:
    """A failed transport never becomes a settlement the CLI would replay."""
    registry, state_file = _write_fixture(tmp_path)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Prove that a raised step commits no settlement receipt.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
    )
    todo_id = str(added["todo_id"])
    control = _control(registry)
    original = control.run_cli
    turn_instance_id = ""

    def raise_on_completion(args: list[str], **kwargs: object) -> str:
        nonlocal turn_instance_id
        if "--turn-instance-id" in args:
            turn_instance_id = str(args[args.index("--turn-instance-id") + 1])
        if args[:2] == ["todo", "complete"]:
            raise RuntimeError("synthetic CLI transport failure before completion")
        return original(args, **kwargs)  # type: ignore[arg-type]

    control.run_cli = raise_on_completion  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="synthetic CLI transport failure"):
        control.complete_task(
            todo_id,
            AGENT_ID,
            "raised step fixture check passed",
        )

    # Nothing was completed and nothing was spent: the failure happened
    # before the CLI committed the lifecycle completion.
    status = json.loads(control.should_run())
    assert status["quota"]["spent_slots"] == 0  # type: ignore[index]

    todos = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    by_id = {
        str(item["todo_id"]): item
        for item in todos["agent_todos"]["items"]
    }
    assert by_id[todo_id]["status"] == "open"

    # The guard already ran, so the validation receipt is committed. No
    # lifecycle completion, writeback or spend receipt exists, and the
    # settlement is still open rather than pending.
    readback = read_heartbeat_settlement(
        tmp_path / "runtime",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
    )
    assert readback is not None
    assert readback.replay_phase == "open"
    assert readback.progress is not None
    assert readback.progress["state"] == "writeback_required"
    assert _committed_receipt_kinds(readback) == ["validation"]


def _raise_after_step(
    tmp_path: Path,
    step_prefix: tuple[str, ...],
) -> tuple[Path, str, str]:
    """Complete a Todo whose transport fails just after ``step_prefix`` commits.

    Returns the runtime root, the Todo id and the turn instance id the plan used,
    so the caller can read back the receipts the CLI already committed.
    """
    registry, _ = _write_fixture(tmp_path)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Characterize the partial-step recovery boundary.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
    )
    todo_id = str(added["todo_id"])
    control = _control(registry)
    original = control.run_cli
    turn_instance_id = ""
    injected = False

    def raise_after_commit(args: list[str], **kwargs: object) -> str:
        nonlocal turn_instance_id, injected
        if "--turn-instance-id" in args:
            turn_instance_id = str(args[args.index("--turn-instance-id") + 1])
        output = original(args, **kwargs)  # type: ignore[arg-type]
        if not injected and tuple(args[: len(step_prefix)]) == step_prefix:
            injected = True
            raise RuntimeError("synthetic CLI transport failure after commit")
        return output

    control.run_cli = raise_after_commit  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="synthetic CLI transport failure"):
        control.complete_task(
            todo_id,
            AGENT_ID,
            "partial-step fixture check passed",
            no_follow_up=True,
        )
    return tmp_path / "runtime", todo_id, turn_instance_id


def _committed_receipt_kinds(readback: object) -> list[str]:
    """The receipt kinds the CLI committed, in settlement order."""
    return [
        receipt.step_kind.value
        for result in (
            readback.identity,  # type: ignore[attr-defined]
            readback.writeback,  # type: ignore[attr-defined]
            readback.spend,  # type: ignore[attr-defined]
        )
        for receipt in result.receipts
    ]


@pytest.mark.parametrize(
    ("step_prefix", "expected_replay_phase", "expected_state", "expected_receipt_kinds"),
    [
        (("quota", "should-run"), "open", "writeback_required", ["validation"]),
        (("todo", "complete"), "settlement_pending", "writeback_required", ["validation"]),
        (
            ("refresh-state",),
            "settlement_pending",
            "spend_required",
            ["validation", "durable_writeback"],
        ),
        (
            ("quota", "spend-slot"),
            "settled",
            "settled",
            ["validation", "durable_writeback", "quota_spend"],
        ),
    ],
)
def test_a_raised_step_after_a_durable_commit_leaves_only_that_stage_pending(
    tmp_path: Path,
    step_prefix: tuple[str, ...],
    expected_replay_phase: str,
    expected_state: str,
    expected_receipt_kinds: list[str],
) -> None:
    """Each durable stage leaves exactly its own receipt behind."""
    runtime_root, todo_id, turn_instance_id = _raise_after_step(tmp_path, step_prefix)

    readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
    )
    assert readback is not None
    assert readback.replay_phase == expected_replay_phase
    assert readback.progress is not None
    assert readback.progress["state"] == expected_state
    assert _committed_receipt_kinds(readback) == expected_receipt_kinds
