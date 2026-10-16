from __future__ import annotations

from pathlib import Path

import pytest

from loopx.control_plane.scheduler.monitor_poll_writeback import (
    write_monitor_poll_todo_state,
)
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todo_fields_if_promoted,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.testing.canary_harness import (
    run_json_cli_result,
    write_fixture_registry,
)
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos.external_wait_contract import (
    TodoExternalWaitAuthoringError,
)
from loopx.todos import complete_goal_todo, update_goal_todo
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
)


GOAL_ID = "external-wait-fixture"
AGENT_ID = "codex-main"
WAITING_ID = "todo_waiting001"
MONITOR_ID = "todo_monitor001"
DEPENDENCY_ID = "todo_dependency001"
FALLBACK_ID = "todo_fallback001"


def _write_fixture(
    root: Path,
    *,
    dependency: bool = False,
) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    dependency_todo = (
        "- [ ] [P0] Finish the exact prerequisite.\n"
        "  <!-- loopx:todo "
        f"todo_id={DEPENDENCY_ID} status=open task_class=advancement_task "
        f"claimed_by={AGENT_ID} successor_todo_ids={WAITING_ID} -->\n"
        if dependency
        else ""
    )
    state_file.write_text(
        "---\nstatus: active\nupdated_at: 2026-08-25T00:00:00Z\n---\n\n"
        "# Active Goal State\n\n## Agent Todo\n\n"
        f"{dependency_todo}"
        "- [ ] [P0] Resume the validated slice after its typed dependency.\n"
        "  <!-- loopx:todo "
        f"todo_id={WAITING_ID} status=open task_class=advancement_task "
        f"claimed_by={AGENT_ID} -->\n"
        "- [ ] [P0] Poll the external lifecycle.\n"
        "  <!-- loopx:todo "
        f"todo_id={MONITOR_ID} status=open task_class=continuous_monitor "
        f"claimed_by={AGENT_ID} target_key=external-review watch_only=true "
        "result_hash=review-v2 material_change=false "
        "material_change_generation=2 -->\n"
        "- [ ] [P1] Advance the independent fallback.\n"
        "  <!-- loopx:todo "
        f"todo_id={FALLBACK_ID} status=open task_class=advancement_task "
        f"claimed_by={AGENT_ID} -->\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL_ID,
        domain="external-wait",
        adapter_kind="generic_project_goal_v0",
        registered_agents=[AGENT_ID],
        quota_allowed_slots=None,
    )
    todos = parse_active_state_todos(state_file.read_text(encoding="utf-8"))[
        "agent_todos"
    ]["items"]
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=todos,
            handoff_mode="soft_claim",
        ),
        state_path=state_file,
    )
    return registry, runtime, state_file


def _todos(runtime: Path) -> dict[str, dict]:
    source = read_canonical_todos_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL_ID,
    )
    assert source is not None
    return {item["todo_id"]: item for item in source["todos"]}


def _summary_todos(runtime: Path) -> dict[str, dict]:
    fields = read_canonical_todo_fields_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL_ID,
    )
    assert fields is not None
    return {
        item["todo_id"]: item
        for item in fields["agent_todos"]["items"]
    }


def test_monitor_change_wait_binds_generation_and_resumes_only_after_increment(
    tmp_path: Path,
) -> None:
    registry, runtime, state_file = _write_fixture(tmp_path)

    update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=WAITING_ID,
        resume_when=f"monitor_changed:{MONITOR_ID}",
        successor_todo_ids=[FALLBACK_ID],
        agent_id=AGENT_ID,
    )

    waiting = _todos(runtime)[WAITING_ID]
    assert waiting["status"] == "open"
    assert waiting["resume_when"] == f"monitor_changed:{MONITOR_ID}"
    assert waiting["resume_monitor_generation"] == 2
    assert waiting["successor_todo_ids"] == [FALLBACK_ID]
    assert _summary_todos(runtime)[WAITING_ID]["resume_ready"] is False

    unchanged = write_monitor_poll_todo_state(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        execute=True,
        reason_summary="external review remains unchanged",
        generated_at="2026-08-25T01:00:00Z",
        todo_id=MONITOR_ID,
        result_hash="review-v3-unclassified",
        material_change=False,
        next_due_at="2026-08-25T02:00:00Z",
        agent_id=AGENT_ID,
    )
    assert unchanged["material_change_generation"] == 2
    assert _summary_todos(runtime)[WAITING_ID]["resume_ready"] is False

    changed = write_monitor_poll_todo_state(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        execute=True,
        reason_summary="external review produced a typed material change",
        generated_at="2026-08-25T02:00:00Z",
        todo_id=MONITOR_ID,
        result_hash="review-v3-approved",
        material_change=True,
        agent_id=AGENT_ID,
    )
    assert changed["material_change_generation"] == 3
    resumed = _summary_todos(runtime)[WAITING_ID]
    assert resumed["resume_ready"] is True
    assert resumed["resume_condition"]["generation_fence"] == (
        "strictly_greater_than_baseline"
    )

    replay = write_monitor_poll_todo_state(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        execute=True,
        reason_summary="replay the same material observation",
        generated_at="2026-08-25T02:01:00Z",
        todo_id=MONITOR_ID,
        result_hash="review-v3-approved",
        material_change=True,
        agent_id=AGENT_ID,
    )
    assert replay["material_change_generation"] == 3

    with pytest.raises(ValueError, match="clear the satisfied resume_when"):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=WAITING_ID,
            resume_when=f"monitor_changed:{MONITOR_ID}",
            successor_todo_ids=[FALLBACK_ID],
            agent_id=AGENT_ID,
        )


def test_monitor_wait_diagnoses_status_and_successor_faults_separately(
    tmp_path: Path,
) -> None:
    registry, _, _ = _write_fixture(tmp_path)

    with pytest.raises(TodoExternalWaitAuthoringError) as status_error:
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=WAITING_ID,
            status="blocked",
            resume_when=f"monitor_changed:{MONITOR_ID}",
            successor_todo_ids=[FALLBACK_ID],
            agent_id=AGENT_ID,
            dry_run=True,
        )
    assert status_error.value.code == "external_wait_todo_status_must_remain_open"
    assert "must remain status=open" in str(status_error.value)
    assert "successor" not in str(status_error.value)
    assert status_error.value.authoring_contract["waiting_todo"] == {
        "status": "open",
        "task_class": "advancement_task",
        "resume_when": f"monitor_changed:{MONITOR_ID}",
        "successor_todo_ids": [FALLBACK_ID],
        "successor_requirement": "independent_runnable_advancement_task",
        "runnable_state": "excluded_until_resume_condition_satisfied",
    }

    with pytest.raises(TodoExternalWaitAuthoringError) as successor_error:
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=WAITING_ID,
            resume_when=f"monitor_changed:{MONITOR_ID}",
            successor_todo_ids=[],
            agent_id=AGENT_ID,
            dry_run=True,
        )
    assert successor_error.value.code == "external_wait_successor_required"


def test_todo_cli_projects_external_wait_repair_contract(tmp_path: Path) -> None:
    registry, _, _ = _write_fixture(tmp_path)

    returncode, payload = run_json_cli_result(
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--todo-id",
        WAITING_ID,
        "--status",
        "blocked",
        "--resume-when",
        f"monitor_changed:{MONITOR_ID}",
        "--successor-todo-id",
        FALLBACK_ID,
        "--agent-id",
        AGENT_ID,
        "--dry-run",
        registry_path=registry,
    )

    assert returncode == 1
    assert payload["error_code"] == "external_wait_todo_status_must_remain_open"
    assert "successor" not in payload["error"]
    contract = payload["authoring_contract"]
    assert contract["schema_version"] == "monitor_advancement_authoring_v0"
    assert contract["monitor"]["execution"] == "observe_only"
    assert contract["material_change"]["next_agent_todo_effect"] == (
        "emit_independent_open_advancement_task"
    )


def test_monitor_provider_effect_replay_does_not_advance_counters(
    tmp_path: Path,
) -> None:
    registry, runtime, state_file = _write_fixture(tmp_path)
    kwargs = {
        "registry_path": registry,
        "runtime_root": tmp_path / "runtime",
        "goal_id": GOAL_ID,
        "execute": True,
        "generated_at": "2026-08-25T01:00:00Z",
        "todo_id": MONITOR_ID,
        "result_hash": "review-v2",
        "material_change": False,
        "next_due_at": "2026-08-25T02:00:00Z",
        "agent_id": AGENT_ID,
        "monitor_effect_id": "quota-monitor-poll:provider-reentry",
    }

    first = write_monitor_poll_todo_state(**kwargs)
    replayed = write_monitor_poll_todo_state(**kwargs)

    assert first is not None
    assert replayed is not None
    assert first["monitor_effect_id"] == "quota-monitor-poll:provider-reentry"
    assert replayed["provider_replayed"] is True
    assert replayed["consecutive_no_change"] == first["consecutive_no_change"]
    monitor = _todos(runtime)[MONITOR_ID]
    assert monitor["monitor_effect_id"] == "quota-monitor-poll:provider-reentry"
    assert int(monitor["consecutive_no_change"]) == first["consecutive_no_change"]

    with pytest.raises(ValueError, match="monitor effect identity is already bound"):
        write_monitor_poll_todo_state(
            **{
                **kwargs,
                "result_hash": "review-v3-conflict",
            }
        )

    newer = write_monitor_poll_todo_state(
        **{
            **kwargs,
            "generated_at": "2026-08-25T02:00:00Z",
            "result_hash": "review-v3-newer",
            "next_due_at": "2026-08-25T03:00:00Z",
            "monitor_effect_id": "quota-monitor-poll:newer-effect",
        }
    )
    assert newer is not None
    before_replay = state_file.read_bytes()
    historical = write_monitor_poll_todo_state(**kwargs)
    expected = {**first, "provider_replayed": True}
    actual = dict(historical)
    # Projection delivery describes the current head, not the immutable receipt.
    for key in (
        "coordination_runtime_shadow",
        "projection_delivery",
        "projection_outbox",
    ):
        actual.pop(key, None)
        expected.pop(key, None)
    expected["todo_update"] = {key: value for key, value in first["todo_update"].items()
                               if key != "coordination_runtime_shadow"}
    assert actual == expected
    assert state_file.read_bytes() == before_replay
    monitor = _todos(runtime)[MONITOR_ID]
    assert monitor["result_hash"] == "review-v3-newer"
    assert monitor["monitor_effect_id"] == "quota-monitor-poll:newer-effect"


def test_monitor_provider_effect_replay_reuses_material_successor(
    tmp_path: Path,
) -> None:
    registry, _, state_file = _write_fixture(tmp_path)
    successor_text = "Advance the material monitor transition."
    kwargs = {
        "registry_path": registry,
        "runtime_root": tmp_path / "runtime",
        "goal_id": GOAL_ID,
        "execute": True,
        "generated_at": "2026-08-25T01:00:00Z",
        "todo_id": MONITOR_ID,
        "result_hash": "review-v3-approved",
        "material_change": True,
        "next_agent_todo": successor_text,
        "next_action_kind": "advance_material_transition",
        "next_required_capabilities": ["filesystem_write"],
        "next_continuation_policy": "same_agent_non_delivery",
        "next_claimed_by": AGENT_ID,
        "agent_id": AGENT_ID,
        "monitor_effect_id": "quota-monitor-poll:material-provider-reentry",
    }

    first = write_monitor_poll_todo_state(**kwargs)
    replayed = write_monitor_poll_todo_state(**kwargs)

    assert first is not None
    assert replayed is not None
    assert replayed["provider_replayed"] is True
    assert replayed["material_change_generation"] == first[
        "material_change_generation"
    ]
    assert replayed["successor_receipts"] == first["successor_receipts"]
    assert len(first["successor_receipts"]) == 1
    assert state_file.read_text(encoding="utf-8").count(successor_text) == 1


def test_dependency_completion_automatically_resumes_waiting_todo(tmp_path: Path) -> None:
    registry, runtime, _ = _write_fixture(tmp_path, dependency=True)

    update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=WAITING_ID,
        resume_when=f"todo_done:{DEPENDENCY_ID}",
        successor_todo_ids=[FALLBACK_ID],
        agent_id=AGENT_ID,
    )
    waiting = _todos(runtime)[WAITING_ID]
    assert waiting["resume_when"] == f"todo_done:{DEPENDENCY_ID}"
    assert waiting["successor_todo_ids"] == [FALLBACK_ID]
    assert _summary_todos(runtime)[WAITING_ID]["resume_ready"] is False

    complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=DEPENDENCY_ID,
        successor_todo_ids=[WAITING_ID],
        agent_id=AGENT_ID,
    )

    waiting = _summary_todos(runtime)[WAITING_ID]
    assert waiting["resume_ready"] is True
    assert waiting["resume_condition"]["target_status"] == "done"


def test_waiting_prose_without_typed_condition_remains_runnable(tmp_path: Path) -> None:
    registry, runtime, _ = _write_fixture(tmp_path)

    update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=WAITING_ID,
        note="External reviewer approval is pending; continue only after review.",
        agent_id=AGENT_ID,
    )

    waiting = _todos(runtime)[WAITING_ID]
    assert "resume_when" not in waiting
    fields = read_canonical_todo_fields_if_promoted(
        runtime_root=runtime,
        goal_id=GOAL_ID,
    )
    assert fields is not None
    executable = fields["agent_todos"]["first_executable_items"]
    assert WAITING_ID in {item["todo_id"] for item in executable}
