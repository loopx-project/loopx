from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver import executor as turn_executor
from loopx.control_plane.turn_driver import (
    LOOPX_TURN_RESULT_SCHEMA_VERSION,
    TurnRecoveryBlockedError,
    build_loopx_turn_plan,
    load_loopx_turn_plan_from_journal,
    run_loopx_turn_once,
    validate_loopx_turn_host_result,
)
from loopx.control_plane.turn_driver.subagent_execution_topology import (
    OPAQUE_REF_PATTERN,
    child_execution_receipts_json_schema,
)
from loopx.control_plane.turn_driver.executor import (
    BuiltInHostError,
    LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
    _task_validation_stage,
    turn_journal_path,
)
from loopx.control_plane.turn_driver.host_binding import managed_executor_binding
from loopx.control_plane.turn_driver.settlement import execute_turn_driver_settlement
from loopx.control_plane.turn_driver.transaction import TRANSACTION_PHASES


def _plan() -> dict[str, object]:
    return build_loopx_turn_plan(
        {
            "ok": True,
            "schema_version": "loopx_turn_envelope_v0",
            "goal_id": "fixture-goal",
            "agent_id": "codex-fixture",
            "should_run": True,
            "effective_action": "normal_run",
            "action": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
                "selected_todo": {
                    "todo_id": "todo_fixture0001",
                    "text": "Advance one public fixture",
                },
            },
            "user": {
                "action_required": False,
                "open_count": 0,
                "notify": "DONT_NOTIFY",
            },
            "writeback": {"spend_after_validation": True},
            "scheduler": {"action": "run_now"},
            "action_signature": {
                "matches": True,
                "source_hash": "sha256:fixture",
                "envelope_hash": "sha256:fixture",
            },
            "compaction": {"within_budget": True},
        },
        host="generic-cli",
        execution_mode="isolated-headless",
    )


def _codex_plan() -> dict[str, object]:
    plan = _plan()
    envelope = plan["turn_envelope"]
    assert isinstance(envelope, dict)
    return build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="isolated-headless",
    )


def _managed_plan(*, runtime_available: bool) -> dict[str, object]:
    """One dsh plan carrying the executor readback the command layer attaches."""

    plan = _plan()
    envelope = plan["turn_envelope"]
    assert isinstance(envelope, dict)
    managed = build_loopx_turn_plan(
        envelope,
        host="dsh",
        execution_mode="isolated-headless",
    )
    managed["managed_executor"] = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "fixture-operator-credential"},
        module_probe=lambda _module: runtime_available,
    )
    return managed


def _adaptive_observation_plan(
    *,
    required_write_scopes: list[str] | None = None,
) -> dict[str, object]:
    plan = _plan()
    envelope = plan["turn_envelope"]
    assert isinstance(envelope, dict)
    envelope["task_orchestration_contract"] = {
        "schema_version": "task_orchestration_contract_v2",
        "mode": "adaptive",
        "coordinator_agent_id": "codex-fixture",
        "primary_todo_id": "todo_fixture0001",
        "child_brief_defaults": {
            "schema_version": "subagent_control_plane_handoff_v0",
            "parent_goal_id": "fixture-goal",
            "authority_artifact": "quota_should_run.goal_boundary",
            "latest_state_ref": "quota_should_run.action_signature.source_hash",
            "context_policy": {
                "selection_owner": "task_coordinator",
                "default": "fresh",
                "allowed": ["fresh"],
            },
            "expected_output": "public_safe_evidence",
            "execution_policy": {
                "timeout": "bounded_by_host_turn",
                "cancel": "task_coordinator_or_host_timeout",
            },
            "child_guard_policy": "prevention_first_v0",
            "validation_policy": "report validation commands and results",
            "acceptance": [
                "report completed scope and evidence",
                "do not write LoopX state or spend quota",
            ],
        },
        "eligible_child_lanes": [
            {
                "todo_id": "todo_child001",
                "task_domain": "validation",
                "execution_kind": "ephemeral_child",
                "child_brief": {
                    "todo_id": "todo_child001",
                    "objective": "Validate one independent fixture.",
                    "action_kind": "validate",
                    "task_domain": "validation",
                    "required_capabilities": [],
                    "task_repository": None,
                    "required_write_scopes": required_write_scopes or [],
                    "workspace_isolation": (
                        "independent_git_worktree"
                        if required_write_scopes
                        else "not_required"
                    ),
                },
            }
        ],
        "writeback_owner": "task_coordinator",
    }
    return build_loopx_turn_plan(
        envelope,
        host="codex-cli",
        execution_mode="isolated-headless",
    )


def _adaptive_plan() -> dict[str, object]:
    plan = _plan()
    envelope = plan["turn_envelope"]
    assert isinstance(envelope, dict)
    envelope["action"]["selected_todo"] = {"todo_id": "todo-stale"}
    envelope["task_orchestration_contract"] = {
        "schema_version": "task_orchestration_contract_v2",
        "mode": "adaptive",
        "primary_todo_id": "todo_fixture0001",
    }
    return build_loopx_turn_plan(
        envelope,
        host="generic-cli",
        execution_mode="isolated-headless",
    )


def _host_result(
    plan: dict[str, object], *, kind: str = "validated_progress"
) -> dict[str, object]:
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    result: dict[str, object] = {
        "schema_version": LOOPX_TURN_RESULT_SCHEMA_VERSION,
        "turn_key": transaction["turn_key"],
        "result_kind": kind,
        "completed_phases": ["host_execute", "typed_result"],
    }
    if kind in {"validated_progress", "validated_completion"}:
        result.update(
            classification=(
                "fixture_progress"
                if kind == "validated_progress"
                else "fixture_completion"
            ),
            recommended_action=(
                "Continue the public fixture."
                if kind == "validated_progress"
                else "Refresh the active goal after this Todo completion."
            ),
            next_action=(
                "Run the next public fixture check."
                if kind == "validated_progress"
                else "Select the next Todo from a fresh decision."
            ),
            delivery_batch_scale="implementation",
            delivery_outcome="outcome_progress",
            vision_unchanged_reason="The fixture objective is unchanged after validated progress.",
            summary=(
                "One public fixture advanced."
                if kind == "validated_progress"
                else "One public fixture completed."
            ),
        )
    return result


def _child_execution_receipt(
    plan: dict[str, object],
    *,
    effect_classes: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> dict[str, object]:
    topology = plan["subagent_execution_topology"]
    assert isinstance(topology, dict)
    lanes = topology["lanes"]
    assert isinstance(lanes, list)
    lane = lanes[0]
    assert isinstance(lane, dict)
    return {
        "schema_version": "subagent_host_execution_receipt_v0",
        "bundle_id": topology["bundle_id"],
        "lane_id": lane["lane_id"],
        "goal_id": topology["goal_id"],
        "todo_id": lane["todo_id"],
        "execution_kind": lane["execution_kind"],
        "runtime_id": "codex-cli",
        "worker_ref": "worker:fixture-child",
        "source_state_ref": topology["source_state_ref"],
        "task_packet_digest": lane["task_packet_digest"],
        "context_mode": lane["task_packet"]["context"]["mode"],
        "workspace_ref": None,
        "status": "completed",
        "effect_classes": (
            ["local_read"] if effect_classes is None else effect_classes
        ),
        "evidence_refs": (
            ["artifact:fixture-review"] if evidence_refs is None else evidence_refs
        ),
        "raw_transcript_copied": False,
    }


def test_task_validation_stage_reads_result_kind_through_effect_turn(
    tmp_path: Path,
) -> None:
    plan = _plan()
    result = _host_result(plan, kind="wait")
    journal = {
        "schema_version": LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
        "goal_id": "fixture-goal",
        "turn_key": plan["transaction"]["turn_key"],
        "status": "in_progress",
        "completed_phases": list(TRANSACTION_PHASES[:2]),
        "plan": plan,
    }
    journal_path = tmp_path / "journal.json"
    turn_executor._write_journal(
        journal_path,
        {**journal, "completed_phases": []},
    )
    turn_executor._write_journal(journal_path, journal)

    completed, payload = _task_validation_stage(
        plan,
        result,
        task_validator=None,
        completed_phases=list(TRANSACTION_PHASES[:2]),
        journal=journal,
        journal_path=journal_path,
        effects={},
    )

    assert completed == list(TRANSACTION_PHASES[:3])
    assert journal["status"] == "stopped"
    assert payload is not None
    assert payload["status"] == "stopped"


def test_typed_settlement_fails_closed_when_journal_receipt_payload_is_missing() -> (
    None
):
    transaction = _plan()["transaction"]
    assert isinstance(transaction, dict)
    calls = {"writeback": 0, "spend": 0, "checkpoint": 0}

    result = execute_turn_driver_settlement(
        transaction,
        transaction_phases=TRANSACTION_PHASES,
        completed_phases=TRANSACTION_PHASES[:4],
        writeback_payload=None,
        quota_spend_payload=None,
        writeback=lambda: (
            calls.__setitem__("writeback", calls["writeback"] + 1)
            or {"ok": True, "appended": True}
        ),
        spend=lambda: (
            calls.__setitem__("spend", calls["spend"] + 1)
            or {"ok": True, "appended": True}
        ),
        checkpoint=lambda _kind, _payload, _phases: calls.__setitem__(
            "checkpoint", calls["checkpoint"] + 1
        ),
    )

    assert result.failure is not None
    assert result.failure.kind.value == "receipt_missing"
    assert result.failure.step_kind.value == "durable_writeback"
    assert [receipt.step_kind.value for receipt in result.receipts] == ["validation"]
    assert calls == {"writeback": 0, "spend": 0, "checkpoint": 0}


def test_typed_settlement_fails_closed_when_plan_has_no_settlement_plan() -> None:
    transaction = _plan()["transaction"]
    assert isinstance(transaction, dict)
    legacy = {
        key: value for key, value in transaction.items() if key != "settlement_plan"
    }
    calls = {"writeback": 0, "spend": 0, "checkpoint": 0}

    result = execute_turn_driver_settlement(
        legacy,
        transaction_phases=TRANSACTION_PHASES,
        completed_phases=TRANSACTION_PHASES[:3],
        writeback_payload=None,
        quota_spend_payload=None,
        writeback=lambda: (
            calls.__setitem__("writeback", calls["writeback"] + 1)
            or {"ok": True, "appended": True}
        ),
        spend=lambda: (
            calls.__setitem__("spend", calls["spend"] + 1)
            or {"ok": True, "appended": True}
        ),
        checkpoint=lambda _kind, _payload, _phases: calls.__setitem__(
            "checkpoint", calls["checkpoint"] + 1
        ),
    )

    assert result.failure is not None
    assert result.failure.kind.value == "receipt_missing"
    assert result.failure.step_kind.value == "validation"
    assert "typed settlement plan" in result.failure.reason
    assert result.receipts == ()
    assert calls == {"writeback": 0, "spend": 0, "checkpoint": 0}


def _host_argv(result_path: Path, count_path: Path) -> list[str]:
    script = """
import json
import pathlib
import sys
request = json.load(sys.stdin)
result = json.loads(pathlib.Path(sys.argv[1]).read_text())
result["turn_key"] = request["turn_key"]
count = pathlib.Path(sys.argv[2])
count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
json.dump(result, sys.stdout)
"""
    return [sys.executable, "-c", script, str(result_path), str(count_path)]


def _callbacks(calls: dict[str, int]):
    def writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["writeback"] += 1
        return {"ok": True, "appended": True, "classification": "fixture_progress"}

    def spend() -> dict[str, object]:
        calls["spend"] += 1
        return {"ok": True, "appended": True, "slots": 1}

    def scheduler(_spend: dict[str, object]) -> dict[str, object]:
        calls["scheduler"] += 1
        return {"completed": True, "acknowledged": False, "disposition": "not_required"}

    return writeback, spend, scheduler


def _journal(runtime_root: Path) -> dict[str, object]:
    journal_paths = [
        path
        for path in (runtime_root / "goals" / "fixture-goal" / "turns").glob("*.json")
        if not path.name.endswith(".lock.holder.json")
    ]
    assert len(journal_paths) == 1
    return json.loads(journal_paths[0].read_text(encoding="utf-8"))


def _passing_validator(
    _plan: dict[str, object],
    _result: dict[str, object],
) -> dict[str, object]:
    return {
        "status": "passed",
        "validator_kind": "fixture",
        "summary": "independent fixture postconditions passed",
    }


def test_host_result_requires_bounded_public_material_fields() -> None:
    plan = _plan()
    result = _host_result(plan)
    result["raw_trajectory"] = "not allowed"

    validation = validate_loopx_turn_host_result(plan, result)

    assert validation["ok"] is False
    assert "unsupported host result fields" in " ".join(validation["errors"])


def test_child_receipt_schema_excludes_registered_peer_authority() -> None:
    schema = child_execution_receipts_json_schema()
    item_schema = schema["items"]
    properties = item_schema["properties"]

    assert properties["execution_kind"]["enum"] == ["ephemeral_child"]
    assert {"agent_id", "session_ref", "task_lease_ref"}.isdisjoint(properties)
    assert {"agent_id", "session_ref", "task_lease_ref"}.isdisjoint(
        item_schema["required"]
    )
    assert properties["worker_ref"]["pattern"] == OPAQUE_REF_PATTERN
    assert (
        properties["evidence_refs"]["items"]["pattern"]
        == OPAQUE_REF_PATTERN
    )
    assert properties["evidence_refs"]["minItems"] == 1


def test_child_receipt_rejects_unknown_context_mode() -> None:
    plan = _adaptive_observation_plan()
    result = _host_result(plan)
    result["child_execution_receipts"] = [
        {
            **_child_execution_receipt(plan),
            "context_mode": "implicit_parent_history",
        }
    ]

    validation = validate_loopx_turn_host_result(plan, result)

    assert validation["ok"] is False
    assert "context_mode is unsupported" in " ".join(validation["errors"])


def test_host_result_reconciles_aligned_child_receipt() -> None:
    plan = _adaptive_observation_plan()
    result = _host_result(plan)
    result["child_execution_receipts"] = [_child_execution_receipt(plan)]

    validation = validate_loopx_turn_host_result(plan, result)

    assert validation["ok"] is True
    reconciliation = validation["result"]["subagent_reconciliation"]
    assert reconciliation["status"] == "reconciled"
    assert reconciliation["observation_only"] is True
    assert reconciliation["settlement_enforced"] is False
    assert reconciliation["child_guard"] == {
        "schema_version": "child_execution_guard_v0",
        "enforced_boundaries": ["pre_spawn_task_packet"],
        "validated_observations": [
            "receipt_task_packet_binding",
            "context_mode_binding",
            "workspace_boundary",
            "effect_boundary",
        ],
        "projected_dispositions": [
            "stop_child",
            "quarantine_evidence",
            "continue_parent",
            "fallback_actions",
        ],
        "unsupported_boundaries": [
            "evidence_acceptance_enforcement",
            "live_host_tool_interception",
            "automatic_host_child_termination",
        ],
        "parent_acceptance_required": True,
    }
    assert reconciliation["parent_blocked"] is False
    assert reconciliation["parent_continuation"] == "continue"
    assert reconciliation["counts"] == {
        "planned": 1,
        "pre_spawn_rejected": 0,
        "observed": 1,
        "aligned": 1,
        "incomplete": 0,
        "rejected": 0,
        "cancelled": 0,
        "drifted": 0,
        "orphaned": 0,
    }
    lane = reconciliation["lanes"][0]
    assert lane["status"] == "aligned"
    assert lane["evidence_disposition"] == "candidate_for_parent_acceptance"
    assert lane["recommended_child_action"] == "return_to_parent"
    assert lane["parent_blocked"] is False
    assert lane["candidate_evidence_refs"] == ["artifact:fixture-review"]
    assert "quarantined_evidence_refs" not in lane


def test_pre_spawn_rejection_remains_visible_without_blocking_parent() -> None:
    plan = _adaptive_observation_plan()
    topology = plan["subagent_execution_topology"]
    topology["pre_spawn_rejections"] = [
        {
            "schema_version": "child_execution_rejection_v0",
            "todo_id": "todo_child002",
            "stage": "pre_spawn",
            "reason_codes": ["child_task_packet_incomplete"],
            "launch_allowed": False,
            "recommended_child_action": "do_not_launch",
            "parent_blocked": False,
            "parent_continuation": "continue",
            "fallback_actions": [
                "retry_fresh",
                "replace_child",
                "serial_takeover",
                "ignore_optional_result",
            ],
        }
    ]
    result = _host_result(plan)
    result["child_execution_receipts"] = [_child_execution_receipt(plan)]

    validation = validate_loopx_turn_host_result(plan, result)

    assert validation["ok"] is True
    reconciliation = validation["result"]["subagent_reconciliation"]
    assert reconciliation["status"] == "guarded"
    assert reconciliation["counts"]["pre_spawn_rejected"] == 1
    assert reconciliation["parent_blocked"] is False
    assert reconciliation["pre_spawn_rejections"] == topology[
        "pre_spawn_rejections"
    ]


def test_host_result_observes_missing_and_drifted_child_receipts() -> None:
    plan = _adaptive_observation_plan()
    missing = validate_loopx_turn_host_result(plan, _host_result(plan))

    assert missing["ok"] is True
    assert missing["result"]["subagent_reconciliation"]["status"] == "incomplete"
    assert missing["result"]["subagent_reconciliation"]["lanes"][0][
        "reason_codes"
    ] == ["worker_receipt_missing"]

    drifted_result = _host_result(plan)
    drifted_result["child_execution_receipts"] = [
        _child_execution_receipt(
            plan,
            effect_classes=["external_write"],
        )
    ]
    drifted = validate_loopx_turn_host_result(plan, drifted_result)

    assert drifted["ok"] is True
    reconciliation = drifted["result"]["subagent_reconciliation"]
    assert reconciliation["status"] == "drifted"
    assert reconciliation["lanes"][0]["reason_codes"] == [
        "side_effect_boundary_exceeded"
    ]
    assert reconciliation["lanes"][0]["evidence_disposition"] == "quarantined"
    assert reconciliation["lanes"][0]["recommended_child_action"] == "stop_child"
    assert reconciliation["lanes"][0]["parent_blocked"] is False
    assert reconciliation["lanes"][0]["quarantined_evidence_refs"] == [
        "artifact:fixture-review"
    ]
    assert "candidate_evidence_refs" not in reconciliation["lanes"][0]
    assert reconciliation["lanes"][0]["fallback_actions"] == [
        "retry_fresh",
        "replace_child",
        "serial_takeover",
        "ignore_optional_result",
    ]

    packet_mismatch_result = _host_result(plan)
    packet_mismatch_result["child_execution_receipts"] = [
        {
            **_child_execution_receipt(plan),
            "task_packet_digest": "sha256:" + "0" * 64,
        }
    ]
    packet_mismatch = validate_loopx_turn_host_result(
        plan, packet_mismatch_result
    )
    assert packet_mismatch["ok"] is True
    assert packet_mismatch["result"]["subagent_reconciliation"]["lanes"][0][
        "reason_codes"
    ] == ["task_packet_mismatch"]

    context_mismatch_result = _host_result(plan)
    context_mismatch_result["child_execution_receipts"] = [
        {
            **_child_execution_receipt(plan),
            "context_mode": "forked_snapshot",
        }
    ]
    context_mismatch = validate_loopx_turn_host_result(
        plan, context_mismatch_result
    )
    assert context_mismatch["ok"] is True
    context_lane = context_mismatch["result"]["subagent_reconciliation"]["lanes"][0]
    assert context_lane["reason_codes"] == ["context_mode_mismatch"]
    assert context_lane["evidence_disposition"] == "quarantined"

    no_evidence_result = _host_result(plan)
    no_evidence_result["child_execution_receipts"] = [
        _child_execution_receipt(plan, evidence_refs=[])
    ]
    no_evidence = validate_loopx_turn_host_result(plan, no_evidence_result)
    assert no_evidence["ok"] is True
    assert no_evidence["result"]["subagent_reconciliation"]["lanes"][0][
        "reason_codes"
    ] == ["aggregate_settlement_without_lane_evidence"]


@pytest.mark.parametrize(
    ("receipt_status", "lane_status"),
    [
        ("failed", "rejected"),
        ("rejected", "rejected"),
        ("cancelled", "cancelled"),
    ],
)
def test_host_result_guards_terminal_unsuccessful_child_receipts(
    receipt_status: str,
    lane_status: str,
) -> None:
    plan = _adaptive_observation_plan()
    result = _host_result(plan)
    result["child_execution_receipts"] = [
        {
            **_child_execution_receipt(plan),
            "status": receipt_status,
        }
    ]

    validation = validate_loopx_turn_host_result(plan, result)

    assert validation["ok"] is True
    reconciliation = validation["result"]["subagent_reconciliation"]
    assert reconciliation["status"] == "guarded"
    assert reconciliation["parent_blocked"] is False
    lane = reconciliation["lanes"][0]
    assert lane["status"] == lane_status
    assert lane["evidence_disposition"] == "quarantined"
    assert lane["recommended_child_action"] == "stop_child"


def test_terminal_unsuccessful_child_takes_priority_over_incomplete_sibling() -> None:
    plan = _adaptive_observation_plan()
    topology = plan["subagent_execution_topology"]
    lanes = topology["lanes"]
    assert isinstance(lanes, list)
    sibling = {
        **lanes[0],
        "lane_id": "lane_sibling",
        "todo_id": "todo_sibling",
    }
    lanes.append(sibling)
    result = _host_result(plan)
    result["child_execution_receipts"] = [
        {
            **_child_execution_receipt(plan),
            "status": "failed",
        }
    ]

    validation = validate_loopx_turn_host_result(plan, result)

    assert validation["ok"] is True
    reconciliation = validation["result"]["subagent_reconciliation"]
    assert reconciliation["status"] == "guarded"
    assert reconciliation["counts"]["rejected"] == 1
    assert reconciliation["counts"]["incomplete"] == 1


def test_host_result_requires_planned_workspace_for_writing_child() -> None:
    plan = _adaptive_observation_plan(required_write_scopes=["src/**"])
    topology = plan["subagent_execution_topology"]
    lane = topology["lanes"][0]
    receipt = _child_execution_receipt(
        plan,
        effect_classes=["local_read", "held_workspace_write"],
    )
    result = _host_result(plan)
    result["child_execution_receipts"] = [receipt]

    missing_workspace = validate_loopx_turn_host_result(plan, result)
    assert missing_workspace["ok"] is True
    assert missing_workspace["result"]["subagent_reconciliation"]["lanes"][0][
        "reason_codes"
    ] == ["workspace_mismatch"]

    receipt["workspace_ref"] = lane["workspace_ref"]
    aligned = validate_loopx_turn_host_result(plan, result)
    assert aligned["ok"] is True
    assert aligned["result"]["subagent_reconciliation"]["status"] == "reconciled"


def test_disabled_host_result_rejects_unadmitted_child_receipt() -> None:
    plan = _plan()
    receipt = {
        "schema_version": "subagent_host_execution_receipt_v0",
        "bundle_id": "bundle_unadmitted",
        "lane_id": "lane_unadmitted",
        "goal_id": "fixture-goal",
        "todo_id": "todo_unadmitted",
        "execution_kind": "ephemeral_child",
        "runtime_id": "generic-cli",
        "worker_ref": "worker:unadmitted",
        "source_state_ref": "sha256:fixture",
        "task_packet_digest": "sha256:" + "f" * 64,
        "context_mode": "fresh",
        "workspace_ref": None,
        "status": "completed",
        "effect_classes": ["local_read"],
        "evidence_refs": ["artifact:unadmitted"],
        "raw_transcript_copied": False,
    }
    result = _host_result(plan)
    result["child_execution_receipts"] = [receipt]

    rejected = validate_loopx_turn_host_result(plan, result)
    assert rejected["ok"] is False
    assert "unsupported host result fields: child_execution_receipts" in (
        " ".join(rejected["errors"])
    )
    assert "subagent_reconciliation" not in rejected["result"]


def test_enabled_host_result_rejects_receipt_local_path() -> None:
    plan = _adaptive_observation_plan()
    result = _host_result(plan)
    receipt = _child_execution_receipt(plan)
    receipt["workspace_ref"] = "/Users/example/raw-worker-path"
    result["child_execution_receipts"] = [receipt]

    rejected = validate_loopx_turn_host_result(plan, result)
    assert rejected["ok"] is False
    assert "absolute local path" in " ".join(rejected["errors"])


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        # A drive-qualified path is now recognized as a local path, so the
        # shared public-safety rule reports it before the opaque-shape check.
        # Both rules reject the value; only the diagnostic differs.
        (
            "worker_ref",
            "C:/workspace/private/worker.json",
            "contains an absolute local path",
        ),
        (
            "evidence_refs",
            ["file:/tmp/private-result.json"],
            "opaque 1-192 character public-safe reference",
        ),
    ],
)
def test_enabled_host_result_rejects_path_shaped_opaque_refs(
    field: str,
    value: object,
    expected_error: str,
) -> None:
    plan = _adaptive_observation_plan()
    result = _host_result(plan)
    receipt = _child_execution_receipt(plan)
    receipt[field] = value
    result["child_execution_receipts"] = [receipt]

    rejected = validate_loopx_turn_host_result(plan, result)

    assert rejected["ok"] is False
    assert expected_error in " ".join(rejected["errors"])
    assert "child_execution_receipts" not in rejected["result"]
    rejected_value = value[0] if isinstance(value, list) else value
    assert rejected_value not in json.dumps(
        rejected["result"],
        ensure_ascii=False,
    )
    reconciliation = rejected["result"]["subagent_reconciliation"]
    assert reconciliation["counts"]["observed"] == 0
    assert reconciliation["lanes"][0]["receipt_present"] is False


def test_observation_only_reconciliation_does_not_change_settlement(
    tmp_path: Path,
) -> None:
    plan = _adaptive_observation_plan()
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    committed = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: _host_result(plan),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )

    assert committed["ok"] is True
    assert committed["status"] == "committed"
    assert committed["subagent_reconciliation"]["status"] == "incomplete"
    assert committed["subagent_reconciliation"]["settlement_enforced"] is False
    assert committed["subagent_reconciliation"]["parent_blocked"] is False
    assert _journal(tmp_path / "runtime")["host_result"][
        "subagent_reconciliation"
    ] == committed["subagent_reconciliation"]
    assert calls == {"writeback": 1, "spend": 1, "scheduler": 1}


def test_run_once_preview_has_no_host_or_journal_effects(tmp_path: Path) -> None:
    plan = _plan()

    payload = run_loopx_turn_once(
        plan,
        host_argv=[sys.executable, "-c", "raise SystemExit(9)"],
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=False,
    )

    assert payload["ok"] is True
    assert payload["status"] == "preview"
    assert payload["effects"] == {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    assert not (tmp_path / "runtime").exists()


def test_run_once_rejects_oversized_built_in_host_result(tmp_path: Path) -> None:
    plan = _plan()
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)
    oversized = _host_result(plan)
    oversized["summary"] = "x" * 13_000

    payload = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: oversized,
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )

    assert payload["ok"] is False
    assert payload["reason"] == "built-in host result exceeded the result budget"
    assert calls == {"writeback": 0, "spend": 0, "scheduler": 0}


def test_run_once_explicitly_retries_failed_host_without_duplicate_effects(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        if calls["host"] == 1:
            raise BuiltInHostError("codex_cli_model_requires_newer_codex")
        return _host_result(plan)

    kwargs = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }
    failed = run_loopx_turn_once(plan, **kwargs)
    replayed = run_loopx_turn_once(plan, **kwargs)
    recovered = run_loopx_turn_once(plan, retry_failed=True, **kwargs)

    assert failed["reason"] == "codex_cli_model_requires_newer_codex"
    assert failed["result_kind"] == "host_failure"
    assert failed["receipt"]["result_kind"] == "host_failure"
    assert failed["receipt"]["failed_phase"] == "host_execute"
    assert replayed["replayed"] is True
    assert recovered["status"] == "committed"
    assert calls == {"host": 2, "writeback": 1, "spend": 1, "scheduler": 1}


def test_run_once_bounds_provider_capacity_retries_without_spending_quota(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        raise BuiltInHostError(
            "codex_cli_provider_capacity",
            failure_kind="provider_capacity",
        )

    common = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }

    first = run_loopx_turn_once(plan, **common)
    second = run_loopx_turn_once(plan, retry_failed=True, **common)
    third = run_loopx_turn_once(plan, retry_failed=True, **common)

    assert first["host_failure"]["attempt"] == 1
    assert first["host_failure"]["retry"]["backoff_seconds"] == 30
    assert second["host_failure"]["attempt"] == 2
    assert second["host_failure"]["retry"]["backoff_seconds"] == 60
    assert third["host_failure"]["attempt"] == 3
    assert third["host_failure"]["retry"]["max_attempts"] == 3
    with pytest.raises(TurnRecoveryBlockedError) as exc_info:
        run_loopx_turn_once(plan, retry_failed=True, **common)
    assert exc_info.value.decision["reason"] == "host_retry_budget_exhausted"
    assert calls == {"host": 3, "writeback": 0, "spend": 0, "scheduler": 0}


def test_run_once_never_blindly_retries_output_budget_exhaustion(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        raise BuiltInHostError(
            "dsh_output_budget_exhausted_no_final",
            failure_kind="output_budget_exhausted",
        )

    common = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }

    failed = run_loopx_turn_once(plan, **common)

    assert failed["reason"] == "dsh_output_budget_exhausted_no_final"
    assert failed["host_failure"] == {
        "schema_version": "loopx_turn_host_failure_v0",
        "kind": "output_budget_exhausted",
        "attempt": 1,
        "retryable": False,
    }
    with pytest.raises(TurnRecoveryBlockedError) as exc_info:
        run_loopx_turn_once(plan, retry_failed=True, **common)
    assert exc_info.value.decision["reason"] == "host_retry_not_available"
    assert calls == {"host": 1, "writeback": 0, "spend": 0, "scheduler": 0}


def test_run_once_resumes_session_observed_by_recoverable_failed_turn(
    tmp_path: Path,
) -> None:
    plan = _codex_plan()
    calls = {"host": 0, "writeback": 0, "spend": 0, "scheduler": 0}
    session_actions: list[str] = []
    writeback, spend, scheduler = _callbacks(calls)

    def host(request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        session = request["session"]
        assert isinstance(session, dict)
        session_actions.append(str(session["action"]))
        if calls["host"] == 1:
            raise BuiltInHostError(
                "codex_cli_timeout",
                recovery_kind="resume_session",
            )
        return _host_result(plan)

    def session_binding(
        _turn_envelope: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "schema_version": "loopx_turn_session_binding_v0",
            "goal_id": "fixture-goal",
            "agent_id": "codex-fixture",
            "todo_id": "todo_fixture0001",
        }

    common = {
        "host_runner": host,
        "session_binding_resolver": session_binding,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }

    failed = run_loopx_turn_once(plan, **common)
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    inspected = turn_executor.inspect_loopx_turn_journal(
        tmp_path / "runtime",
        goal_id="fixture-goal",
        agent_id="codex-fixture",
        turn_key=str(transaction["turn_key"]),
        retry_failed=True,
        session_binding_resolver=session_binding,
    )
    recovered = run_loopx_turn_once(plan, retry_failed=True, **common)

    assert failed["reason"] == "codex_cli_timeout"
    assert inspected["recovery_decision"]["can_continue"] is True
    assert inspected["recovery_decision"]["resume_from"] == "host_execute"
    assert inspected["recovery_decision"]["reinvoke_host"] is True
    assert recovered["recovery"]["planned"] == inspected["recovery_decision"]
    assert recovered["status"] == "committed"
    assert session_actions == ["start_new", "resume"]
    assert calls == {"host": 2, "writeback": 1, "spend": 1, "scheduler": 1}


@pytest.mark.parametrize("host_recovery", ["corrupted", {}])
def test_run_once_rejects_corrupted_failed_turn_recovery_before_host_retry(
    tmp_path: Path,
    host_recovery: object,
) -> None:
    plan = _codex_plan()
    calls = {"host": 0, "writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def host(request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        if calls["host"] == 1:
            raise BuiltInHostError(
                "codex_cli_timeout",
                recovery_kind="resume_session",
            )
        return _host_result(plan)

    common = {
        "host_runner": host,
        "session_binding_resolver": lambda _turn_envelope: {
            "schema_version": "loopx_turn_session_binding_v0",
            "goal_id": "fixture-goal",
            "agent_id": "codex-fixture",
            "todo_id": "todo_fixture0001",
        },
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }

    failed = run_loopx_turn_once(plan, **common)
    journal_paths = [
        path
        for path in (tmp_path / "runtime" / "goals" / "fixture-goal" / "turns").glob(
            "*.json"
        )
        if not path.name.endswith(".lock.holder.json")
    ]
    assert len(journal_paths) == 1
    journal = json.loads(journal_paths[0].read_text(encoding="utf-8"))
    journal["host_recovery"] = host_recovery
    journal_paths[0].write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(ValueError, match="host recovery"):
        run_loopx_turn_once(plan, retry_failed=True, **common)

    assert failed["reason"] == "codex_cli_timeout"
    assert calls == {"host": 1, "writeback": 0, "spend": 0, "scheduler": 0}


def test_run_once_recoverable_failed_turn_rejects_session_identity_drift(
    tmp_path: Path,
) -> None:
    plan = _codex_plan()
    calls = {"host": 0, "writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        raise BuiltInHostError(
            "codex_cli_timeout",
            recovery_kind="resume_session",
        )

    common = {
        "host_runner": host,
        "session_binding_resolver": lambda _turn_envelope: {
            "schema_version": "loopx_turn_session_binding_v0",
            "goal_id": "fixture-goal",
            "agent_id": "codex-fixture",
            "todo_id": "todo_from_another_turn",
        },
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }

    failed = run_loopx_turn_once(plan, **common)
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    inspected = turn_executor.inspect_loopx_turn_journal(
        tmp_path / "runtime",
        goal_id="fixture-goal",
        agent_id="codex-fixture",
        turn_key=str(transaction["turn_key"]),
        retry_failed=True,
        session_binding_resolver=common["session_binding_resolver"],
    )
    assert inspected["recovery_decision"]["action"] == "blocked"
    assert (
        inspected["recovery_decision"]["reason"]
        == "session_binding_identity_mismatch"
    )
    with pytest.raises(ValueError, match="session binding does not match") as exc_info:
        run_loopx_turn_once(plan, retry_failed=True, **common)
    assert exc_info.value.decision == inspected["recovery_decision"]

    assert failed["reason"] == "codex_cli_timeout"
    assert calls == {"host": 1, "writeback": 0, "spend": 0, "scheduler": 0}


def test_run_once_commits_once_and_replays_without_duplicate_effects(
    tmp_path: Path,
) -> None:
    plan = _plan()
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_host_result(plan)), encoding="utf-8")
    count_path = tmp_path / "host-count"
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)
    kwargs = {
        "host_argv": _host_argv(result_path, count_path),
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }

    first = run_loopx_turn_once(plan, **kwargs)
    replay = run_loopx_turn_once(plan, **kwargs)

    assert first["ok"] is True
    assert first["status"] == "committed"
    assert first["receipt"]["status"] == "committed"
    assert [
        receipt["step_kind"] for receipt in first["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback", "quota_spend"]
    assert first["effects"]["host_invoked"] is True
    assert first["effects"]["state_written"] is True
    assert first["effects"]["quota_spent"] is True
    assert replay["replayed"] is True
    assert not any(replay["effects"].values())
    assert count_path.read_text(encoding="utf-8") == "1"
    assert calls == {"writeback": 1, "spend": 1, "scheduler": 1}

    # The route is a persisted compatibility surface, not just in-process state.
    # Exercise the actual TypeScript-backed journal writer and Python resume reader.
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    turn_key = str(transaction["turn_key"])
    stored = json.loads(turn_journal_path(
        tmp_path / "runtime", goal_id="fixture-goal", turn_key=turn_key,
    ).read_text(encoding="utf-8"))
    assert stored["plan"]["route"]["kind"] == "ready_for_host"
    resumed = load_loopx_turn_plan_from_journal(
        tmp_path / "runtime", goal_id="fixture-goal", turn_key=turn_key,
    )
    assert resumed["route"] == stored["plan"]["route"]


def test_provider_can_commit_before_its_journal_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    _writeback, spend, scheduler = _callbacks(calls)
    provider_records: dict[str, dict[str, object]] = {}

    def writeback(_result: dict[str, object], effect_ref: str) -> dict[str, object]:
        calls["writeback"] += 1
        payload = {"ok": True, "appended": True, "effect_ref": effect_ref}
        provider_records[effect_ref] = payload
        return payload

    write_journal = turn_executor._write_journal

    def fail_before_writeback_checkpoint(
        path: Path,
        journal: Mapping[str, object],
    ) -> None:
        if "writeback" in journal and "quota_spend" not in journal:
            raise RuntimeError("injected crash before writeback checkpoint")
        write_journal(path, journal)

    monkeypatch.setattr(
        turn_executor,
        "_write_journal",
        fail_before_writeback_checkpoint,
    )

    with pytest.raises(
        RuntimeError,
        match="injected crash before writeback checkpoint",
    ):
        run_loopx_turn_once(
            plan,
            host_runner=lambda _request: _host_result(plan),
            project=tmp_path,
            runtime_root=tmp_path / "runtime",
            goal_id="fixture-goal",
            timeout_seconds=5,
            execute=True,
            task_validator=_passing_validator,
            writeback=writeback,
            spend=spend,
            scheduler=scheduler,
        )

    journal = _journal(tmp_path / "runtime")
    assert calls == {"writeback": 1, "spend": 0, "scheduler": 0}
    assert journal["completed_phases"] == list(TRANSACTION_PHASES[:3])
    assert "writeback" not in journal
    prepared = journal["effect_attempts"]["durable_writeback"]
    assert prepared["status"] == "prepared"
    assert prepared["effect_ref"] in provider_records

    monkeypatch.setattr(turn_executor, "_write_journal", write_journal)
    resumed = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: pytest.fail("host must not run during recovery"),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        writeback=writeback,
        writeback_resolver=lambda effect_ref: {
            "kind": "committed",
            "payload": provider_records[effect_ref],
        },
        spend=spend,
        scheduler=scheduler,
    )

    assert resumed["status"] == "committed"
    assert calls == {"writeback": 1, "spend": 1, "scheduler": 1}
    assert "effect_attempts" not in _journal(tmp_path / "runtime")


def test_run_once_legacy_plan_without_settlement_plan_is_upgraded(
    tmp_path: Path,
) -> None:
    plan = _plan()
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    transaction.pop("settlement_plan", None)
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_host_result(plan)), encoding="utf-8")
    count_path = tmp_path / "host-count"
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    committed = run_loopx_turn_once(
        plan,
        host_argv=_host_argv(result_path, count_path),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )

    assert committed["ok"] is True
    assert committed["status"] == "committed"
    assert committed["receipt"]["status"] == "committed"
    assert committed["settlement_result"]["ok"] is True
    assert [
        receipt["step_kind"] for receipt in committed["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback", "quota_spend"]
    assert calls == {"writeback": 1, "spend": 1, "scheduler": 1}


def test_adaptive_completion_upgrades_legacy_plan_with_primary_todo_identity(
    tmp_path: Path,
) -> None:
    plan = _adaptive_plan()
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    transaction.pop("settlement_plan", None)
    calls = {"completion": 0, "spend": 0, "scheduler": 0}

    def completion_writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["completion"] += 1
        return {
            "ok": True,
            "appended": True,
            "completion": {
                "todo_id": "todo_fixture0001",
                "continuation": "active_goal",
            },
        }

    committed = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: _host_result(
            plan,
            kind="validated_completion",
        ),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        writeback=lambda _result: {"ok": True, "appended": True},
        completion_writeback=completion_writeback,
        completion_intent=lambda _result: {
            "todo_id": "todo_fixture0001",
            "continuation": "active_goal",
        },
        terminal_closeout=lambda _result: {
            "ok": True,
            "appended": True,
            "completion": {
                "todo_id": "todo_fixture0001",
                "continuation": "no_followup",
            },
        },
        spend=lambda: (
            calls.__setitem__("spend", calls["spend"] + 1)
            or {"ok": True, "appended": True}
        ),
        scheduler=lambda _spend: (
            calls.__setitem__("scheduler", calls["scheduler"] + 1)
            or {"completed": True, "acknowledged": True}
        ),
    )

    assert committed["status"] == "committed"
    assert committed["receipt"]["lineage"]["todo_id"] == "todo_fixture0001"
    assert calls == {"completion": 1, "spend": 1, "scheduler": 1}


def test_validated_completion_requires_explicit_lifecycle_writeback(
    tmp_path: Path,
) -> None:
    plan = _plan()
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(_host_result(plan, kind="validated_completion")),
        encoding="utf-8",
    )
    calls = {"writeback": 0, "completion": 0, "spend": 0, "scheduler": 0}

    def writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["writeback"] += 1
        return {"ok": True, "appended": True}

    def spend() -> dict[str, object]:
        calls["spend"] += 1
        return {"ok": True, "appended": True}

    def scheduler(_spend: dict[str, object]) -> dict[str, object]:
        calls["scheduler"] += 1
        return {"completed": True, "acknowledged": True}

    payload = run_loopx_turn_once(
        plan,
        host_argv=_host_argv(result_path, tmp_path / "host-count"),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )

    assert payload["result_kind"] == "validation_failed"
    assert payload["receipt"]["failed_phase"] == "validation"
    assert calls == {"writeback": 0, "completion": 0, "spend": 0, "scheduler": 0}


def test_validated_completion_commits_once_with_lifecycle_outcome(
    tmp_path: Path,
) -> None:
    plan = _plan()
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(_host_result(plan, kind="validated_completion")),
        encoding="utf-8",
    )
    calls = {"writeback": 0, "completion": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def completion_writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["completion"] += 1
        return {
            "ok": True,
            "appended": True,
            "completion": {
                "todo_id": "todo_fixture0001",
                "continuation": "active_goal",
            },
        }

    kwargs = {
        "host_argv": _host_argv(result_path, tmp_path / "host-count"),
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "completion_writeback": completion_writeback,
        "completion_intent": lambda _result: {
            "todo_id": "todo_fixture0001",
            "continuation": "active_goal",
        },
        "terminal_closeout": lambda _result: {
            "ok": True,
            "appended": True,
            "completion": {
                "todo_id": "todo_fixture0001",
                "continuation": "no_followup",
            },
        },
        "spend": spend,
        "scheduler": scheduler,
    }
    first = run_loopx_turn_once(plan, **kwargs)
    replay = run_loopx_turn_once(plan, **kwargs)

    assert first["status"] == "committed"
    assert first["effects"]["state_written"] is True
    assert first["effects"]["quota_spent"] is True
    assert replay["replayed"] is True
    assert not any(replay["effects"].values())
    assert calls == {"writeback": 0, "completion": 1, "spend": 1, "scheduler": 1}
    assert _journal(tmp_path / "runtime")["writeback"]["completion"] == {
        "todo_id": "todo_fixture0001",
        "continuation": "active_goal",
    }


def test_invalid_completion_outcome_fails_closed_without_spending(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"completion": 0, "spend": 0, "scheduler": 0}
    writeback, _spend, _scheduler = _callbacks(calls)

    def completion_writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["completion"] += 1
        return {
            "ok": True,
            "appended": True,
            "completion": {"todo_id": "todo_other", "continuation": "active_goal"},
        }

    payload = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: _host_result(plan, kind="validated_completion"),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        writeback=writeback,
        completion_writeback=completion_writeback,
        completion_intent=lambda _result: {
            "todo_id": "todo_other",
            "continuation": "active_goal",
        },
        terminal_closeout=lambda _result: {
            "ok": True,
            "appended": True,
            "completion": {
                "todo_id": "todo_fixture0001",
                "continuation": "no_followup",
            },
        },
        spend=lambda: (
            calls.__setitem__("spend", calls["spend"] + 1)
            or {
                "ok": True,
                "appended": True,
            }
        ),
        scheduler=lambda _spend: (
            calls.__setitem__("scheduler", calls["scheduler"] + 1)
            or {"completed": True, "acknowledged": True}
        ),
    )

    assert payload["result_kind"] == "writeback_failed"
    assert payload["receipt"]["result_kind"] == "writeback_failed"
    assert payload["receipt"]["failed_phase"] == "durable_writeback"
    assert payload["settlement_result"]["failure"]["kind"] == "writeback_rejected"
    assert calls == {"completion": 0, "spend": 0, "scheduler": 0}


def test_terminal_closeout_runs_only_after_matching_spend_receipt(
    tmp_path: Path,
) -> None:
    plan = _plan()
    events: list[str] = []

    payload = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: _host_result(
            plan,
            kind="validated_completion",
        ),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        writeback=lambda _result: (
            events.append("writeback") or {"ok": True, "appended": True}
        ),
        completion_writeback=lambda _result: pytest.fail(
            "terminal completion must not use the pre-spend lifecycle callback"
        ),
        completion_intent=lambda _result: {
            "todo_id": "todo_fixture0001",
            "continuation": "no_followup",
        },
        spend=lambda: events.append("spend") or {"ok": True, "appended": True},
        terminal_closeout=lambda _result: (
            events.append("terminal_closeout")
            or {
                "ok": True,
                "appended": True,
                "completion": {
                    "todo_id": "todo_fixture0001",
                    "continuation": "no_followup",
                },
            }
        ),
        scheduler=lambda _spend: (
            events.append("scheduler") or {"completed": True, "acknowledged": True}
        ),
    )

    assert payload["status"] == "committed"
    assert events == ["writeback", "spend", "terminal_closeout", "scheduler"]
    assert [
        receipt["step_kind"] for receipt in payload["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback", "quota_spend", "terminal_closeout"]
    assert payload["todo_completion"] == {
        "todo_id": "todo_fixture0001",
        "continuation": "no_followup",
    }


def test_terminal_closeout_lost_receipt_retries_without_repeating_effects(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {
        "writeback": 0,
        "spend": 0,
        "terminal_attempt": 0,
        "terminal_mutation": 0,
        "scheduler": 0,
    }
    terminal_committed = False

    def terminal_closeout(_result: dict[str, object]) -> dict[str, object]:
        nonlocal terminal_committed
        calls["terminal_attempt"] += 1
        if not terminal_committed:
            terminal_committed = True
            calls["terminal_mutation"] += 1
            return {
                "ok": False,
                "appended": False,
                "reason": "terminal closeout receipt was interrupted",
            }
        return {
            "ok": True,
            "appended": True,
            "completion": {
                "todo_id": "todo_fixture0001",
                "continuation": "no_followup",
            },
        }

    common = {
        "host_runner": lambda _request: _host_result(
            plan,
            kind="validated_completion",
        ),
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": lambda _result: (
            calls.__setitem__("writeback", calls["writeback"] + 1)
            or {"ok": True, "appended": True}
        ),
        "completion_writeback": lambda _result: pytest.fail(
            "terminal completion must not use the pre-spend lifecycle callback"
        ),
        "completion_intent": lambda _result: {
            "todo_id": "todo_fixture0001",
            "continuation": "no_followup",
        },
        "spend": lambda: (
            calls.__setitem__("spend", calls["spend"] + 1)
            or {"ok": True, "appended": True}
        ),
        "terminal_closeout": terminal_closeout,
        "scheduler": lambda _spend: (
            calls.__setitem__("scheduler", calls["scheduler"] + 1)
            or {"completed": True, "acknowledged": True}
        ),
    }

    failed = run_loopx_turn_once(plan, **common)
    recovered = run_loopx_turn_once(plan, retry_failed=True, **common)

    assert failed["result_kind"] == "terminal_closeout_failed"
    assert failed["receipt"]["failed_phase"] == "terminal_closeout"
    assert failed["effects"]["quota_spent"] is True
    assert recovered["status"] == "committed"
    assert calls == {
        "writeback": 1,
        "spend": 1,
        "terminal_attempt": 2,
        "terminal_mutation": 1,
        "scheduler": 1,
    }


def test_validated_completion_recovers_after_writeback_without_repeating_completion(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"completion": 0, "spend": 0, "scheduler": 0}

    def completion_writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["completion"] += 1
        return {
            "ok": True,
            "appended": True,
            "completion": {
                "todo_id": "todo_fixture0001",
                "continuation": "active_goal",
            },
        }

    def interrupted_spend() -> dict[str, object]:
        calls["spend"] += 1
        raise SystemExit(8)

    def scheduler(_spend: dict[str, object]) -> dict[str, object]:
        calls["scheduler"] += 1
        return {"completed": True, "acknowledged": True}

    def healthy_spend() -> dict[str, object]:
        calls["spend"] += 1
        return {"ok": True, "appended": True}

    common = {
        "host_runner": lambda _request: _host_result(
            plan,
            kind="validated_completion",
        ),
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": lambda _result: {"ok": True, "appended": True},
        "completion_writeback": completion_writeback,
        "completion_intent": lambda _result: {
            "todo_id": "todo_fixture0001",
            "continuation": "active_goal",
        },
        "terminal_closeout": lambda _result: {
            "ok": True,
            "appended": True,
            "completion": {
                "todo_id": "todo_fixture0001",
                "continuation": "no_followup",
            },
        },
        "scheduler": scheduler,
    }
    with pytest.raises(SystemExit):
        run_loopx_turn_once(plan, spend=interrupted_spend, **common)

    recovered = run_loopx_turn_once(
        plan,
        spend=healthy_spend,
        spend_resolver=lambda _effect_ref: {"kind": "absent"},
        **common,
    )

    assert recovered["status"] == "committed"
    assert calls == {"completion": 1, "spend": 2, "scheduler": 1}


def test_run_once_recovers_after_process_exit_before_writeback(tmp_path: Path) -> None:
    plan = _plan()
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_host_result(plan)), encoding="utf-8")
    count_path = tmp_path / "host-count"
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    healthy_writeback, spend, scheduler = _callbacks(calls)

    def interrupted_writeback(_result: dict[str, object]) -> dict[str, object]:
        raise SystemExit(7)

    common = {
        "host_argv": _host_argv(result_path, count_path),
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "spend": spend,
        "scheduler": scheduler,
    }
    with pytest.raises(SystemExit):
        run_loopx_turn_once(plan, writeback=interrupted_writeback, **common)

    interrupted = _journal(tmp_path / "runtime")
    assert interrupted["completed_phases"] == [
        "host_execute",
        "typed_result",
        "validation",
    ]
    assert interrupted["task_validation"]["status"] == "passed"
    assert "writeback" not in interrupted

    recovered = run_loopx_turn_once(
        plan,
        writeback=healthy_writeback,
        writeback_resolver=lambda _effect_ref: {"kind": "absent"},
        **common,
    )

    assert recovered["status"] == "committed"
    assert count_path.read_text(encoding="utf-8") == "1"
    assert calls == {"writeback": 1, "spend": 1, "scheduler": 1}


@pytest.mark.parametrize(
    ("drift_kind", "expected_violations"),
    (
        (
            "goal_and_agent",
            ["goal_mismatch", "owner_mismatch", "journal_not_terminal"],
        ),
        (
            "todo_binding",
            ["settlement_binding_mismatch", "journal_not_terminal"],
        ),
    ),
)
def test_run_once_blocks_drifted_settlement_identity_before_any_provider(
    tmp_path: Path,
    drift_kind: str,
    expected_violations: list[str],
) -> None:
    plan = _plan()
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    settlement_plan = transaction["settlement_plan"]
    assert isinstance(settlement_plan, dict)
    identity = settlement_plan["identity"]
    assert isinstance(identity, dict)
    if drift_kind == "goal_and_agent":
        identity["goal_id"] = "other-goal"
        identity["agent_id"] = "other-agent"
    else:
        identity["todo_id"] = "todo_other0002"
    identity["effect_id"] = ":".join(
        (
            str(identity["goal_id"]),
            str(identity["agent_id"]),
            str(identity["todo_id"]),
            str(identity["turn_instance_id"]),
        )
    )
    turn_key = str(transaction["turn_key"])
    runtime_root = tmp_path / "runtime"
    journal_path = turn_executor.turn_journal_path(
        runtime_root,
        goal_id="fixture-goal",
        turn_key=turn_key,
    )
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text(
        json.dumps(
            {
                "schema_version": LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
                "goal_id": "fixture-goal",
                "turn_key": turn_key,
                "status": "in_progress",
                "completed_phases": list(TRANSACTION_PHASES[:3]),
                "plan": plan,
                "host_result": _host_result(plan),
                "task_validation": _passing_validator(plan, _host_result(plan)),
            }
        ),
        encoding="utf-8",
    )
    loaded_plan = load_loopx_turn_plan_from_journal(
        runtime_root,
        goal_id="fixture-goal",
        turn_key=turn_key,
    )
    inspected = turn_executor.inspect_loopx_turn_journal(
        runtime_root,
        goal_id="fixture-goal",
        agent_id="codex-fixture",
        turn_key=turn_key,
    )
    assert inspected["violations"] == expected_violations
    assert inspected["journal_consistent"] is False
    assert inspected["recovery_decision"]["action"] == "blocked"

    calls = {
        "host": 0,
        "validation": 0,
        "writeback": 0,
        "writeback_readback": 0,
        "spend": 0,
        "spend_readback": 0,
        "scheduler": 0,
        "terminal_closeout": 0,
        "terminal_closeout_readback": 0,
    }

    def unexpected_provider(name: str):
        def invoke(*_args: object, **_kwargs: object) -> dict[str, object]:
            calls[name] += 1
            raise AssertionError(f"{name} provider must not run")

        return invoke

    with pytest.raises(TurnRecoveryBlockedError) as exc_info:
        run_loopx_turn_once(
            loaded_plan,
            host_runner=unexpected_provider("host"),
            project=tmp_path,
            runtime_root=runtime_root,
            goal_id="fixture-goal",
            timeout_seconds=5,
            execute=True,
            task_validator=unexpected_provider("validation"),
            writeback=unexpected_provider("writeback"),
            writeback_resolver=unexpected_provider("writeback_readback"),
            spend=unexpected_provider("spend"),
            spend_resolver=unexpected_provider("spend_readback"),
            scheduler=unexpected_provider("scheduler"),
            terminal_closeout=unexpected_provider("terminal_closeout"),
            terminal_closeout_resolver=unexpected_provider(
                "terminal_closeout_readback"
            ),
        )

    assert exc_info.value.decision == inspected["recovery_decision"]
    assert all(count == 0 for count in calls.values())


def test_run_once_recovers_saved_host_result_from_validation_without_host_retry(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "validation": 0, "writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        return _host_result(plan)

    def interrupted_validation(
        _plan: dict[str, object],
        _result: dict[str, object],
    ) -> dict[str, object]:
        calls["validation"] += 1
        raise SystemExit(9)

    common = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }
    with pytest.raises(SystemExit):
        run_loopx_turn_once(
            plan,
            task_validator=interrupted_validation,
            **common,
        )

    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    inspected = turn_executor.inspect_loopx_turn_journal(
        tmp_path / "runtime",
        goal_id="fixture-goal",
        agent_id="codex-fixture",
        turn_key=str(transaction["turn_key"]),
    )
    assert inspected["replay_legal"] is False
    assert inspected["recovery_decision"]["can_continue"] is True
    assert inspected["recovery_decision"]["resume_from"] == "validation"
    assert inspected["recovery_decision"]["reinvoke_host"] is False

    recovered = run_loopx_turn_once(
        plan,
        task_validator=_passing_validator,
        **common,
    )

    assert recovered["status"] == "committed"
    assert recovered["recovery"]["planned"] == inspected["recovery_decision"]
    assert recovered["recovery"]["actual"] == {
        "status": "finished",
        "journal_status": "committed",
        "completed_phases": list(TRANSACTION_PHASES),
        "host_invoked": False,
    }
    audited = turn_executor.inspect_loopx_turn_journal(
        tmp_path / "runtime",
        goal_id="fixture-goal",
        agent_id="codex-fixture",
        turn_key=str(transaction["turn_key"]),
    )
    assert audited["last_recovery"] == recovered["recovery"]
    assert calls == {
        "host": 1,
        "validation": 1,
        "writeback": 1,
        "spend": 1,
        "scheduler": 1,
    }


def test_run_once_resumes_after_writeback_without_duplicate_effects(
    tmp_path: Path,
) -> None:
    plan = _plan()
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_host_result(plan)), encoding="utf-8")
    count_path = tmp_path / "host-count"
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, healthy_spend, scheduler = _callbacks(calls)

    def interrupted_spend() -> dict[str, object]:
        raise SystemExit(8)

    common = {
        "host_argv": _host_argv(result_path, count_path),
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "scheduler": scheduler,
    }
    with pytest.raises(SystemExit):
        run_loopx_turn_once(plan, spend=interrupted_spend, **common)

    interrupted = _journal(tmp_path / "runtime")
    assert interrupted["completed_phases"] == [
        "host_execute",
        "typed_result",
        "validation",
        "durable_writeback",
    ]
    assert interrupted["writeback"]["appended"] is True
    assert "quota_spend" not in interrupted

    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    resumed_plan = load_loopx_turn_plan_from_journal(
        tmp_path / "runtime",
        goal_id="fixture-goal",
        turn_key=str(transaction["turn_key"]),
    )
    recovered = run_loopx_turn_once(
        resumed_plan,
        spend=healthy_spend,
        spend_resolver=lambda _effect_ref: {"kind": "absent"},
        **common,
    )

    assert recovered["status"] == "committed"
    assert count_path.read_text(encoding="utf-8") == "1"
    assert calls == {"writeback": 1, "spend": 1, "scheduler": 1}


def test_cancellation_before_writeback_preserves_prefix_and_resumes(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "writeback": 0, "spend": 0}

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        return _host_result(plan)

    def cancelled_writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["writeback"] += 1
        raise KeyboardInterrupt

    common = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "spend": lambda: (
            calls.__setitem__("spend", calls["spend"] + 1)
            or {"ok": True, "appended": True}
        ),
        "scheduler": lambda _spend: {"completed": True, "acknowledged": True},
    }
    with pytest.raises(KeyboardInterrupt):
        run_loopx_turn_once(plan, writeback=cancelled_writeback, **common)

    assert _journal(tmp_path / "runtime")["completed_phases"] == [
        "host_execute",
        "typed_result",
        "validation",
    ]
    recovered = run_loopx_turn_once(
        plan,
        writeback=lambda _result: (
            calls.__setitem__("writeback", calls["writeback"] + 1)
            or {"ok": True, "appended": True}
        ),
        writeback_resolver=lambda _effect_ref: {"kind": "absent"},
        **common,
    )

    assert recovered["status"] == "committed"
    assert calls == {"host": 1, "writeback": 2, "spend": 1}


def test_cancellation_during_scheduler_preserves_settlement_and_resumes(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "writeback": 0, "spend": 0, "scheduler": 0}

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        return _host_result(plan)

    def writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["writeback"] += 1
        return {"ok": True, "appended": True}

    def spend() -> dict[str, object]:
        calls["spend"] += 1
        return {"ok": True, "appended": True}

    def cancelled_scheduler(_spend: dict[str, object]) -> dict[str, object]:
        calls["scheduler"] += 1
        raise KeyboardInterrupt

    common = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "spend": spend,
    }
    with pytest.raises(KeyboardInterrupt):
        run_loopx_turn_once(plan, scheduler=cancelled_scheduler, **common)

    interrupted_journal = _journal(tmp_path / "runtime")
    assert interrupted_journal["completed_phases"] == [
        "host_execute",
        "typed_result",
        "validation",
        "durable_writeback",
        "quota_spend",
    ]
    assert [
        receipt["step_kind"]
        for receipt in interrupted_journal["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback", "quota_spend"]

    def healthy_scheduler(_spend: dict[str, object]) -> dict[str, object]:
        calls["scheduler"] += 1
        return {"completed": True, "acknowledged": True}

    recovered = run_loopx_turn_once(plan, scheduler=healthy_scheduler, **common)

    assert recovered["status"] == "committed"
    assert calls == {"host": 1, "writeback": 1, "spend": 1, "scheduler": 2}


def test_permission_denial_from_host_is_typed_and_explicitly_retried(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "writeback": 0, "spend": 0}

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        if calls["host"] == 1:
            raise PermissionError("host denied")
        return _host_result(plan)

    common = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": lambda _result: (
            calls.__setitem__("writeback", calls["writeback"] + 1)
            or {"ok": True, "appended": True}
        ),
        "spend": lambda: (
            calls.__setitem__("spend", calls["spend"] + 1)
            or {"ok": True, "appended": True}
        ),
        "scheduler": lambda _spend: {"completed": True, "acknowledged": True},
    }
    failed = run_loopx_turn_once(plan, **common)
    replayed = run_loopx_turn_once(plan, **common)
    recovered = run_loopx_turn_once(plan, retry_failed=True, **common)

    assert failed["result_kind"] == "host_failure"
    assert failed["receipt"]["failed_phase"] == "host_execute"
    assert failed["reason"] == "PermissionError"
    assert replayed["replayed"] is True
    assert recovered["status"] == "committed"
    assert calls == {"host": 2, "writeback": 1, "spend": 1}


def test_permission_denial_during_spend_preserves_writeback_and_resumes(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "writeback": 0, "spend": 0}

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        return _host_result(plan)

    def writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["writeback"] += 1
        return {"ok": True, "appended": True}

    def denied_spend() -> dict[str, object]:
        calls["spend"] += 1
        raise PermissionError("quota ledger denied")

    common = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "scheduler": lambda _spend: {"completed": True, "acknowledged": True},
    }
    with pytest.raises(PermissionError):
        run_loopx_turn_once(plan, spend=denied_spend, **common)

    assert _journal(tmp_path / "runtime")["completed_phases"] == [
        "host_execute",
        "typed_result",
        "validation",
        "durable_writeback",
    ]
    recovered = run_loopx_turn_once(
        plan,
        spend=lambda: (
            calls.__setitem__("spend", calls["spend"] + 1)
            or {"ok": True, "appended": True}
        ),
        spend_resolver=lambda _effect_ref: {"kind": "absent"},
        **common,
    )

    assert recovered["status"] == "committed"
    assert calls == {"host": 1, "writeback": 1, "spend": 2}


def test_budget_rejection_is_typed_and_retry_does_not_repeat_writeback(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "writeback": 0, "spend": 0}

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        return _host_result(plan)

    def writeback(_result: dict[str, object]) -> dict[str, object]:
        calls["writeback"] += 1
        return {"ok": True, "appended": True}

    def reject_budget() -> dict[str, object]:
        calls["spend"] += 1
        return {
            "ok": False,
            "appended": False,
            "reason": "quota budget rejected",
        }

    common = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "scheduler": lambda _spend: {"completed": True, "acknowledged": True},
    }
    failed = run_loopx_turn_once(plan, spend=reject_budget, **common)

    assert failed["result_kind"] == "quota_spend_failed"
    assert failed["receipt"]["result_kind"] == "quota_spend_failed"
    assert failed["receipt"]["failed_phase"] == "quota_spend"
    assert failed["settlement_result"]["failure"]["kind"] == "budget_rejected"
    assert [
        receipt["step_kind"] for receipt in failed["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback"]
    assert failed["effects"]["state_written"] is True
    assert failed["effects"]["quota_spent"] is False

    recovered = run_loopx_turn_once(
        plan,
        spend=lambda: (
            calls.__setitem__("spend", calls["spend"] + 1)
            or {"ok": True, "appended": True}
        ),
        retry_failed=True,
        **common,
    )

    assert recovered["status"] == "committed"
    assert calls == {"host": 1, "writeback": 1, "spend": 2}


def test_run_once_fails_closed_without_independent_task_validator(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    payload = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: _host_result(plan),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )

    assert payload["ok"] is False
    assert payload["status"] == "failed"
    assert payload["result_kind"] == "validation_failed"
    assert payload["validation"]["status"] == "unavailable"
    assert payload["validation"]["recovery_kind"] == "repair_required"
    assert payload["receipt"]["failed_phase"] == "validation"
    assert payload["receipt"]["completed_phases"] == ["host_execute", "typed_result"]
    assert calls == {"writeback": 0, "spend": 0, "scheduler": 0}


def test_run_once_retries_task_validation_without_reinvoking_host(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"host": 0, "writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def host(_request: dict[str, object]) -> dict[str, object]:
        calls["host"] += 1
        return _host_result(plan)

    def reject(
        _plan: dict[str, object],
        _result: dict[str, object],
    ) -> dict[str, object]:
        return {
            "status": "failed",
            "validator_kind": "fixture",
            "summary": "independent fixture postcondition is absent",
            "recovery_kind": "replan_required",
        }

    common = {
        "host_runner": host,
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }
    failed = run_loopx_turn_once(plan, task_validator=reject, **common)
    recovered = run_loopx_turn_once(
        plan,
        task_validator=_passing_validator,
        retry_failed=True,
        **common,
    )

    assert failed["result_kind"] == "validation_failed"
    assert failed["validation"]["recovery_kind"] == "replan_required"
    assert recovered["status"] == "committed"
    assert recovered["effects"]["host_invoked"] is False
    assert calls == {"host": 1, "writeback": 1, "spend": 1, "scheduler": 1}


def test_material_result_cannot_use_not_required_validation_receipt(
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    payload = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: _host_result(plan),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=lambda _plan, _result: {
            "status": "not_required",
            "validator_kind": "fixture",
            "summary": "skip validation",
        },
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )

    assert payload["result_kind"] == "validation_failed"
    assert payload["validation"]["status"] == "inconclusive"
    assert "cannot skip" in payload["validation"]["summary"]
    assert calls == {"writeback": 0, "spend": 0, "scheduler": 0}


@pytest.mark.parametrize("result_kind", ["wait", "iteration_failed"])
def test_run_once_stops_without_writeback_or_spend(
    tmp_path: Path, result_kind: str
) -> None:
    plan = _plan()
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(_host_result(plan, kind=result_kind)), encoding="utf-8"
    )
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    payload = run_loopx_turn_once(
        plan,
        host_argv=_host_argv(result_path, tmp_path / "host-count"),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )

    assert payload["ok"] is True
    assert payload["status"] == "stopped"
    assert payload["receipt"]["status"] == "stopped"
    assert calls == {"writeback": 0, "spend": 0, "scheduler": 0}


def test_run_once_projects_scheduler_action_without_false_ack(tmp_path: Path) -> None:
    plan = _plan()
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_host_result(plan)), encoding="utf-8")
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, _scheduler = _callbacks(calls)

    def scheduler(_spend: dict[str, object]) -> dict[str, object]:
        calls["scheduler"] += 1
        return {
            "completed": False,
            "apply_needed": True,
            "disposition": "host_action_required",
        }

    payload = run_loopx_turn_once(
        plan,
        host_argv=_host_argv(result_path, tmp_path / "host-count"),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
        task_validator=_passing_validator,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )

    assert payload["ok"] is True
    assert payload["status"] == "scheduler_action_required"
    assert payload["receipt"]["next_phase"] == "scheduler_apply"
    assert payload["effects"]["scheduler_acknowledged"] is False


def test_run_once_resumes_scheduler_without_repeating_committed_effects(
    tmp_path: Path,
) -> None:
    plan = _plan()
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_host_result(plan)), encoding="utf-8")
    count_path = tmp_path / "host-count"
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, _scheduler = _callbacks(calls)

    def scheduler(_spend: dict[str, object]) -> dict[str, object]:
        calls["scheduler"] += 1
        if calls["scheduler"] == 1:
            return {
                "completed": False,
                "apply_needed": True,
                "disposition": "host_action_required",
            }
        return {
            "completed": True,
            "acknowledged": True,
            "disposition": "applied_and_acknowledged",
        }

    kwargs = {
        "host_argv": _host_argv(result_path, count_path),
        "project": tmp_path,
        "runtime_root": tmp_path / "runtime",
        "goal_id": "fixture-goal",
        "timeout_seconds": 5,
        "execute": True,
        "task_validator": _passing_validator,
        "writeback": writeback,
        "spend": spend,
        "scheduler": scheduler,
    }
    first = run_loopx_turn_once(plan, **kwargs)
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    inspected = turn_executor.inspect_loopx_turn_journal(
        tmp_path / "runtime",
        goal_id="fixture-goal",
        agent_id="codex-fixture",
        turn_key=str(transaction["turn_key"]),
    )
    resumed = run_loopx_turn_once(plan, **kwargs)

    assert first["status"] == "scheduler_action_required"
    assert inspected["replay_legal"] is False
    assert inspected["recovery_decision"]["resume_from"] == "scheduler_apply"
    assert inspected["recovery_decision"]["reinvoke_host"] is False
    assert resumed["recovery"]["planned"] == inspected["recovery_decision"]
    assert resumed["status"] == "committed"
    assert resumed["effects"]["scheduler_acknowledged"] is True
    assert count_path.read_text(encoding="utf-8") == "1"
    assert calls == {"writeback": 1, "spend": 1, "scheduler": 2}
    audited = turn_executor.inspect_loopx_turn_journal(
        tmp_path / "runtime",
        goal_id="fixture-goal",
        agent_id="codex-fixture",
        turn_key=str(transaction["turn_key"]),
    )
    assert audited["last_recovery"] == resumed["recovery"]


def test_run_once_fails_closed_when_the_managed_executor_cannot_launch(tmp_path):
    plan = _managed_plan(runtime_available=False)
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    runtime_root = tmp_path / "runtime"
    journal = turn_journal_path(
        runtime_root,
        goal_id="fixture-goal",
        turn_key=str(transaction["turn_key"]),
    )

    payload = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: pytest.fail("an unavailable executor must not run"),
        project=tmp_path,
        runtime_root=runtime_root,
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=True,
    )

    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "dsh_runtime_unavailable"
    assert payload["effects"] == {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    assert payload["quota_slot_spend_count"] == 0
    assert payload["managed_executor"] == plan["managed_executor"]
    assert journal.exists() is False


def test_run_once_preview_reports_the_managed_executor_without_refusing(tmp_path):
    plan = _managed_plan(runtime_available=False)

    payload = run_loopx_turn_once(
        plan,
        host_runner=lambda _request: pytest.fail("preview must not run the host"),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        timeout_seconds=5,
        execute=False,
    )

    assert payload["ok"] is True
    assert payload["status"] == "preview"
    assert payload["managed_executor"]["available"] is False
    assert payload["managed_executor"]["unavailable_reason"] == "dsh_runtime_unavailable"


def test_run_once_does_not_refuse_a_launchable_managed_executor(tmp_path):
    plan = _managed_plan(runtime_available=True)

    # The refusal is the only guard under test here: without writeback, spend,
    # and scheduler callbacks the executor stops at its own contract instead.
    with pytest.raises(ValueError, match="requires writeback, spend, and scheduler"):
        run_loopx_turn_once(
            plan,
            host_runner=lambda _request: pytest.fail("host must not run without callbacks"),
            project=tmp_path,
            runtime_root=tmp_path / "runtime",
            goal_id="fixture-goal",
            timeout_seconds=5,
            execute=True,
        )
