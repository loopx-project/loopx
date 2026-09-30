from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.cli_commands import todo as todo_command
from loopx.cli_commands import task_lease as task_lease_command
from loopx.control_plane.coordination.runtime_shadow import (
    RUNTIME_SHADOW_BOOTSTRAP_METHOD,
    RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA_VERSION,
    RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
    RUNTIME_SHADOW_METHOD,
    RUNTIME_SHADOW_ROLLBACK_METHOD,
    RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA_VERSION,
    bootstrap_coordination_runtime_shadow,
    build_todo_runtime_shadow_projection,
    dispatch_coordination_runtime_shadow,
    inspect_coordination_runtime_shadow,
    load_task_lease_runtime_shadow_records,
    qualify_coordination_runtime_shadow,
    read_coordination_runtime_shadow_todo_candidate,
    resolve_coordination_runtime_shadow_config,
    rollback_coordination_runtime_shadow,
)


def _canonical_todo(
    *,
    todo_id: str = "todo_one",
    role: str = "agent",
    status: str = "open",
    **fields: object,
) -> dict[str, object]:
    return {
        "schema_version": "todo_item_v0",
        "todo_id": todo_id,
        "role": role,
        "status": status,
        "done": status == "done",
        "text": f"{status} {todo_id}",
        "archive_state": "active",
        "source_section": "User Todo" if role == "user" else "Agent Todo",
        **fields,
    }


def test_runtime_shadow_todo_read_candidate_is_default_off_and_typed(
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, ...]] = []
    disabled = read_coordination_runtime_shadow_todo_candidate(
        goal={"id": "goal-a"},
        runtime_root=tmp_path,
        goal_id="goal-a",
        todo_id="todo_one",
        projection={"schema_version": "projection_v0", "todos": []},
        runtime_invoker=lambda *args: calls.append(args),
    )
    assert disabled["status"] == "disabled"
    assert disabled["read_candidate_qualified"] is False
    assert disabled["decision_read_from_shadow"] is False
    assert calls == []

    goal = {
        "id": "goal-a",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
                "provider": "file_v0",
            }
        },
    }

    def invoke(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((method, params))
        return {
            "schema_version": "loopx_coordination_runtime_shadow_todo_read_result_v0",
            "status": "matched",
            "todo_id": "todo_one",
            "todo": {"todo_id": "todo_one", "status": "open"},
            "read_candidate_qualified": True,
            "decision_read_from_shadow": False,
        }

    matched = read_coordination_runtime_shadow_todo_candidate(
        goal=goal,
        runtime_root=tmp_path,
        goal_id="goal-a",
        todo_id="todo_one",
        projection={
            "schema_version": "projection_v0",
            "goal_id": "goal-a",
            "todos": [{"todo_id": "todo_one", "status": "open"}],
        },
        runtime_invoker=invoke,
    )
    assert matched["status"] == "matched"
    assert matched["read_candidate_qualified"] is True
    assert calls[0][0] == "coordination.runtime_shadow.todo_read_candidate"
    assert calls[0][1]["todo_id"] == "todo_one"


def _dispatch(
    tmp_path: Path,
    goal: dict[str, object],
    runtime_invoker,
) -> dict[str, object]:
    return dispatch_coordination_runtime_shadow(
        goal=goal,
        runtime_root=tmp_path,
        goal_id="goal-a",
        operation_id="todo:goal-a:todo_one:v1",
        event_kind="todo_claim",
        source_version="state:1",
        projection={"schema_version": "projection_v0", "todos": []},
        runtime_invoker=runtime_invoker,
    )


def test_runtime_shadow_is_zero_call_default_off(tmp_path: Path) -> None:
    calls: list[object] = []

    result = _dispatch(tmp_path, {"id": "goal-a"}, lambda *args: calls.append(args))

    assert result["status"] == "disabled"
    assert result["reason_code"] == "configuration_absent"
    assert result["primary_writeback_preserved"] is True
    assert result["decision_read_from_shadow"] is False
    assert calls == []


def test_runtime_shadow_bootstrap_is_explicit_default_off_and_typed(
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, ...]] = []
    disabled = bootstrap_coordination_runtime_shadow(
        goal={"id": "goal-a"},
        runtime_root=tmp_path,
        goal_id="goal-a",
        operation_id="bootstrap:goal-a:state-1",
        source_version="state:1",
        projection={"schema_version": "projection_v0", "todos": []},
        goal_ref=None,
        runtime_invoker=lambda *args: calls.append(args),
    )
    assert disabled["status"] == "disabled"
    assert disabled["decision_read_from_shadow"] is False
    assert calls == []

    goal = {
        "id": "goal-a",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
                "provider": "file_v0",
            }
        },
    }

    def invoke(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((method, params))
        return {
            "schema_version": "loopx_coordination_runtime_shadow_bootstrap_result_v0",
            "status": "applied",
            "bootstrap_receipts_empty": True,
            "decision_read_from_shadow": False,
        }

    applied = bootstrap_coordination_runtime_shadow(
        goal=goal,
        runtime_root=tmp_path,
        goal_id="goal-a",
        operation_id="bootstrap:goal-a:state-1",
        source_version="state:1",
        projection={"schema_version": "projection_v0", "todos": []},
        goal_ref=None,
        runtime_invoker=invoke,
    )
    assert applied["status"] == "applied"
    method, params = calls[-1]
    assert method == RUNTIME_SHADOW_BOOTSTRAP_METHOD
    assert params["schema_version"] == RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA_VERSION
    assert params["source_version"] == "state:1"


def test_runtime_shadow_rollback_is_explicit_default_off_and_revision_fenced(
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, ...]] = []
    disabled = rollback_coordination_runtime_shadow(
        goal={"id": "goal-a"},
        runtime_root=tmp_path,
        goal_id="goal-a",
        operation_id="rollback:goal-a:file-revision-1",
        expected_provider_revision="file:revision-1",
        runtime_invoker=lambda *args: calls.append(args),
    )
    assert disabled["status"] == "disabled"
    assert calls == []

    goal = {
        "id": "goal-a",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
                "provider": "file_v0",
            }
        },
    }

    def invoke(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((method, params))
        return {
            "schema_version": "loopx_coordination_runtime_shadow_rollback_result_v0",
            "status": "applied",
            "active_shadow_removed": True,
            "archive_retained": True,
            "decision_read_from_shadow": False,
        }

    applied = rollback_coordination_runtime_shadow(
        goal=goal,
        runtime_root=tmp_path,
        goal_id="goal-a",
        operation_id="rollback:goal-a:file-revision-1",
        expected_provider_revision="file:revision-1",
        runtime_invoker=invoke,
    )

    assert applied["status"] == "applied"
    method, params = calls[-1]
    assert method == RUNTIME_SHADOW_ROLLBACK_METHOD
    assert params["schema_version"] == RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA_VERSION
    assert params["expected_provider_revision"] == "file:revision-1"


def test_runtime_shadow_requires_complete_explicit_file_opt_in(tmp_path: Path) -> None:
    calls: list[object] = []
    goal = {
        "id": "goal-a",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": "wrong",
                "provider": "file_v0",
            }
        },
    }

    result = _dispatch(tmp_path, goal, lambda *args: calls.append(args))

    assert result["status"] == "disabled"
    assert result["reason_code"] == "schema_mismatch"
    assert calls == []
    assert resolve_coordination_runtime_shadow_config(goal).enabled is False


def test_runtime_shadow_dispatches_exact_typed_request_after_opt_in(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def invoke(method: str, params: dict[str, object]) -> dict[str, object]:
        captured["method"] = method
        captured["params"] = params
        return {
            "schema_version": "loopx_coordination_runtime_shadow_result_v0",
            "status": "applied",
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }

    goal = {
        "id": "goal-a",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
                "provider": "file_v0",
            }
        },
    }
    result = _dispatch(tmp_path, goal, invoke)

    assert result["status"] == "applied"
    assert captured["method"] == RUNTIME_SHADOW_METHOD
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["runtime_root"] == str(tmp_path.resolve())
    assert params["operation_id"] == "todo:goal-a:todo_one:v1"


def test_runtime_shadow_failure_never_changes_primary_truth(tmp_path: Path) -> None:
    goal = {
        "id": "goal-a",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
                "provider": "file_v0",
            }
        },
    }

    def fail(*_args) -> object:
        raise RuntimeError("runtime unavailable")

    result = _dispatch(tmp_path, goal, fail)

    assert result["status"] == "failed"
    assert result["reason_code"] == "shadow_runtime_unavailable"
    assert result["primary_writeback_preserved"] is True
    assert result["decision_read_from_shadow"] is False


def test_runtime_shadow_inspection_is_default_off_and_forwards_compact_projection(
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, ...]] = []
    disabled = inspect_coordination_runtime_shadow(
        goal={"id": "goal-a"},
        runtime_root=tmp_path,
        goal_id="goal-a",
        projection={"schema_version": "projection_v0", "todos": []},
        runtime_invoker=lambda *args: calls.append(args),
    )
    assert disabled["status"] == "disabled"
    assert disabled["decision_read_from_shadow"] is False
    assert calls == []

    goal = {
        "id": "goal-a",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
                "provider": "file_v0",
            }
        },
    }
    captured: dict[str, object] = {}

    def inspect(method: str, params: dict[str, object]) -> dict[str, object]:
        captured["method"] = method
        captured["params"] = params
        return {
            "schema_version": "loopx_coordination_runtime_shadow_inspection_v0",
            "status": "matched",
            "parity_matches": True,
            "bootstrap_required": False,
            "decision_read_from_shadow": False,
        }

    matched = inspect_coordination_runtime_shadow(
        goal=goal,
        runtime_root=tmp_path,
        goal_id="goal-a",
        projection={"schema_version": "projection_v0", "todos": []},
        runtime_invoker=inspect,
    )
    assert matched["status"] == "matched"
    assert matched["decision_read_from_shadow"] is False
    assert captured["method"] == "coordination.runtime_shadow.inspect"
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["runtime_root"] == str(tmp_path.resolve())
    assert params["projection"] == {
        "schema_version": "projection_v0",
        "todos": [],
    }


def test_runtime_shadow_qualification_is_default_off_and_forwards_policy(
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, ...]] = []
    disabled = qualify_coordination_runtime_shadow(
        goal={"id": "goal-a"},
        runtime_root=tmp_path,
        goal_id="goal-a",
        projection={"schema_version": "projection_v0", "todos": []},
        minimum_operations=3,
        required_event_kinds=["todo_claim"],
        runtime_invoker=lambda *args: calls.append(args),
    )
    assert disabled["status"] == "disabled"
    assert disabled["qualified"] is False
    assert calls == []

    goal = {
        "id": "goal-a",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
                "provider": "file_v0",
            }
        },
    }
    captured: dict[str, object] = {}

    def qualify(method: str, params: dict[str, object]) -> dict[str, object]:
        captured["method"] = method
        captured["params"] = params
        return {
            "schema_version": "loopx_coordination_runtime_shadow_qualification_v0",
            "status": "qualified",
            "qualified": True,
            "decision_read_from_shadow": False,
        }

    result = qualify_coordination_runtime_shadow(
        goal=goal,
        runtime_root=tmp_path,
        goal_id="goal-a",
        projection={"schema_version": "projection_v0", "todos": []},
        minimum_operations=5,
        required_event_kinds=["todo_claim", "task_lease_acquire"],
        runtime_invoker=qualify,
    )
    assert result["status"] == "qualified"
    assert result["decision_read_from_shadow"] is False
    assert captured["method"] == "coordination.runtime_shadow.qualify"
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["minimum_operations"] == 5
    assert params["required_event_kinds"] == [
        "todo_claim",
        "task_lease_acquire",
    ]


def test_todo_projection_is_complete_stable_and_declares_read_model_contract() -> None:
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a",
        todos=[
            {
                "schema_version": "todo_item_v0",
                "todo_id": "todo_b",
                "role": "agent",
                "status": "open",
                "done": False,
                "text": "Qualify provider cutover",
                "archive_state": "active",
                "source_section": "Agent Todo",
                "claimed_by": "agent-a",
                "note": "operator note retained by local canonical authority",
                "evidence": "durable evidence retained for Todo list semantics",
            },
            {
                "schema_version": "todo_item_v0",
                "todo_id": "todo_a",
                "role": "user",
                "status": "done",
                "done": True,
                "text": "Approve provider cutover",
                "archive_state": "active",
                "source_section": "User Todo",
                "successor_todo_ids": ["todo_b"],
            },
        ],
        leases=[
            {
                "todo_id": "todo_b",
                "owner": "agent-a",
                "version": 2,
                "lease_epoch": 1,
                "status": "released",
            },
            {
                "todo_id": "todo_retired",
                "owner": "agent-a",
                "version": 9,
                "lease_epoch": 4,
                "status": "released",
            },
        ],
    )

    assert [item["todo_id"] for item in projection["todos"]] == [
        "todo_a",
        "todo_b",
    ]
    assert (
        projection["todos"][1]["note"]
        == "operator note retained by local canonical authority"
    )
    assert (
        projection["todos"][1]["evidence"]
        == "durable evidence retained for Todo list semantics"
    )
    assert projection["leases"] == [
        {
            "todo_id": "todo_b",
            "owner": "agent-a",
            "version": 2,
            "lease_epoch": 1,
            "status": "released",
        }
    ]
    assert projection["todo_read_model"]["todo_count"] == 2
    assert "text" in projection["todo_read_model"]["contract_fields"]
    assert "resume_when" in projection["todo_read_model"]["contract_fields"]


def test_production_scale_projection_filters_retired_lease_history() -> None:
    envelope = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "control_plane"
            / "coordination_production_scale_v0.json"
        ).read_text(encoding="utf-8")
    )
    todos: list[dict[str, object]] = []
    for role, counts in (
        ("agent", envelope["agent_status_counts"]),
        ("user", envelope["user_status_counts"]),
    ):
        index = 0
        for status, count in counts.items():
            for _ in range(count):
                todos.append(
                    _canonical_todo(
                        todo_id=f"todo_fixture_{role}_{index:03d}",
                        role=role,
                        status=status,
                        index=index + 1,
                    )
                )
                index += 1
    current_lease_count = int(envelope["current_lease_count"])
    current_ids = [str(todo["todo_id"]) for todo in todos[:current_lease_count]]
    leases = [
        {
            "todo_id": todo_id,
            "owner": "agent-a",
            "version": index + 1,
            "lease_epoch": index + 1,
            "status": "released",
        }
        for index, todo_id in enumerate(current_ids)
    ]
    leases.extend(
        {
            "todo_id": f"todo_fixture_retired_{index:03d}",
            "owner": "agent-a",
            "version": index + 1,
            "lease_epoch": index + 1,
            "status": "released",
        }
        for index in range(int(envelope["retired_lease_count"]))
    )

    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-production-scale",
        todos=todos,
        leases=leases,
    )

    assert len(projection["todos"]) == len(todos) == 464
    assert len(leases) == 224
    assert len(projection["leases"]) == current_lease_count == 64
    assert {lease["todo_id"] for lease in projection["leases"]} == set(current_ids)


def test_todo_projection_rejects_incomplete_consumer_semantics() -> None:
    with pytest.raises(ValueError, match="omits required fields"):
        build_todo_runtime_shadow_projection(
            goal_id="goal-a",
            todos=[{"todo_id": "todo_incomplete", "status": "open"}],
        )


def test_todo_projection_rejects_unversioned_machine_owned_fields() -> None:
    with pytest.raises(ValueError, match="unversioned fields: future_authority_field"):
        build_todo_runtime_shadow_projection(
            goal_id="goal-a",
            todos=[
                {
                    "schema_version": "todo_item_v0",
                    "todo_id": "todo_complete",
                    "role": "agent",
                    "status": "open",
                    "done": False,
                    "text": "Do not silently drop state.",
                    "archive_state": "active",
                    "source_section": "Agent Todo",
                    "future_authority_field": "must be reviewed",
                }
            ],
        )






def test_lease_projection_preserves_complete_terminal_record(tmp_path: Path) -> None:
    lease_dir = tmp_path / "goals" / "goal-a" / "task-leases"
    lease_dir.mkdir(parents=True)
    (lease_dir / "todo_b.json").write_text(
        '{"schema_version":"task_lease_v0","goal_id":"goal-a",'
        '"todo_id":"todo_b","owner":"agent-a","version":2,'
        '"lease_epoch":1,"status":"released","released_at":"later",'
        '"idempotency_key":"retained-identity"}',
        encoding="utf-8",
    )

    records = load_task_lease_runtime_shadow_records(
        runtime_root=tmp_path,
        goal_id="goal-a",
    )

    assert records == [
        {
            "schema_version": "task_lease_v0",
            "goal_id": "goal-a",
            "idempotency_key": "retained-identity",
            "todo_id": "todo_b",
            "owner": "agent-a",
            "version": 2,
            "lease_epoch": 1,
            "released_at": "later",
            "status": "released",
        }
    ]






def test_retired_cli_observers_cannot_overwrite_transaction_evidence() -> None:
    assert not hasattr(todo_command, "_mirror_committed_todo_runtime_shadow")
    assert not hasattr(task_lease_command, "_mirror_committed_task_lease_runtime_shadow")
