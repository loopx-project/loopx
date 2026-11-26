from __future__ import annotations

import pytest

from loopx.control_plane.quota.settlement_workspace_causality import (
    build_delivery_workspace_causality,
    delivery_workspace_causality_event_fields,
    delivery_workspace_causality_from_event_details,
    missing_delivery_workspace_resolution,
    resolve_settlement_workspace_requirement,
    uses_registered_verification_workspace,
)


@pytest.mark.parametrize(
    ("contract", "reason"),
    [
        (
            {"task_repository": "git:github.com/example/loopx"},
            "declared_repository_or_write_contract",
        ),
        (
            {"required_write_scopes": ["loopx/**"]},
            "declared_repository_or_write_contract",
        ),
        (
            {"required_capabilities": ["filesystem_write"]},
            "declared_repository_or_write_contract",
        ),
    ],
)
def test_declared_repository_write_contract_requires_workspace(
    contract: dict[str, object],
    reason: str,
) -> None:
    causality = build_delivery_workspace_causality(
        {"todo_id": "todo_write", **contract}
    )

    assert causality is not None
    assert causality["requirement"] == "required"
    assert causality["reason"] == reason


def test_explicit_non_delivery_contract_does_not_require_workspace() -> None:
    causality = build_delivery_workspace_causality(
        {
            "todo_id": "todo_read_only",
            "continuation_policy": "same_agent_non_delivery",
            "required_capabilities": ["shell", "filesystem_read"],
        }
    )

    assert causality == {
        "schema_version": "delivery_workspace_causality_v0",
        "todo_id": "todo_read_only",
        "requirement": "not_required",
        "source": "selected_todo_contract",
        "reason": "explicit_non_delivery_without_repository_writes",
    }


@pytest.mark.parametrize(
    "write_contract",
    [
        {"task_repository": "git:github.com/example/loopx"},
        {"required_write_scopes": ["loopx/**"]},
        {"required_capabilities": ["filesystem_write"]},
    ],
)
def test_non_delivery_policy_cannot_override_repository_write_contract(
    write_contract: dict[str, object],
) -> None:
    causality = build_delivery_workspace_causality(
        {
            "todo_id": "todo_write",
            "continuation_policy": "same_agent_non_delivery",
            **write_contract,
        }
    )

    assert causality is not None
    assert causality["requirement"] == "required"
    assert causality["reason"] == "declared_repository_or_write_contract"


def test_declared_read_only_verification_keeps_the_workspace_receipt() -> None:
    causality = build_delivery_workspace_causality(
        {"todo_id": "todo_verify", "action_kind": "verify",
         "required_capabilities": ["filesystem_read", "shell"]}
    )

    assert causality is not None
    assert causality["requirement"] == "required"
    assert causality["reason"] == "declared_read_only_verification"
    assert uses_registered_verification_workspace(
        causality, task_repository=None, explicit_peer_worktree_requirement=None,
    )
    assert not uses_registered_verification_workspace(
        causality, task_repository=None, explicit_peer_worktree_requirement=True,
    )
    assert not uses_registered_verification_workspace(
        causality, task_repository=None, explicit_peer_worktree_requirement="true",
    )
    assert not uses_registered_verification_workspace(
        causality, task_repository="git:github.com/example/other",
        explicit_peer_worktree_requirement=None,
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"required_capabilities": ["shell"]}, "unknown"),
        ({"action_kind": "repair"}, "unknown"),
        ({"required_capabilities": ["filesystem_read", "network"]}, "unknown"),
        ({"required_capabilities": ["filesystem_read", "filesystem_write"]}, "required"),
        ({"required_write_scopes": ["docs/**"]}, "required"),
        ({"required_write_scopes": ["../outside"]}, "unknown"),
        ({"task_repository": "git:github.com/example/other"}, "required"),
    ],
)
def test_verification_does_not_infer_a_read_only_workspace(
    overrides: dict[str, object], expected: str,
) -> None:
    causality = build_delivery_workspace_causality(
        {"todo_id": "todo_verify", "action_kind": "verify",
         "required_capabilities": ["filesystem_read", "shell"], **overrides}
    )

    assert causality is not None
    assert causality["requirement"] == expected
    assert not uses_registered_verification_workspace(
        causality, task_repository=None, explicit_peer_worktree_requirement=None,
    )


def test_underspecified_current_todo_keeps_workspace_guard_fail_closed() -> None:
    causality = build_delivery_workspace_causality(
        {"todo_id": "todo_legacy", "action_kind": "inspect"}
    )

    assert causality is not None
    assert causality["requirement"] == "unknown"
    assert causality["reason"] == "todo_write_contract_not_explicit"


def test_unknown_causality_projects_typed_contract_repair() -> None:
    causality = build_delivery_workspace_causality({"todo_id": "todo_unknown"})

    assert causality is not None
    assert missing_delivery_workspace_resolution(causality) == {
        "schema_version": "delivery_workspace_resolution_v0",
        "todo_id": "todo_unknown",
        "decision": "repair_contract",
        "requirement": "unknown",
        "reason": "todo_delivery_contract_not_explicit",
        "accepted_resolutions": [
            "declare_repository_or_write_contract",
            "declare_explicit_non_delivery_contract",
        ],
    }


def test_event_fields_round_trip_without_nested_rollout_details() -> None:
    causality = build_delivery_workspace_causality(
        {
            "todo_id": "todo_read_only",
            "continuation_policy": "same_agent_non_delivery",
        }
    )
    details = delivery_workspace_causality_event_fields(causality)

    assert "delivery_workspace_causality" not in details
    assert delivery_workspace_causality_from_event_details(
        details,
        todo_id="todo_read_only",
    ) == causality


def test_event_causality_rejects_cross_todo_replay() -> None:
    causality = build_delivery_workspace_causality(
        {
            "todo_id": "todo_original",
            "continuation_policy": "same_agent_non_delivery",
        }
    )
    details = delivery_workspace_causality_event_fields(causality)

    assert (
        delivery_workspace_causality_from_event_details(
            details,
            todo_id="todo_successor",
        )
        is None
    )


def test_typed_autonomous_replan_binding_is_explicit_non_repository() -> None:
    assert resolve_settlement_workspace_requirement(
        None,
        settlement_binding_kind="autonomous_replan",
    ) == {
        "schema_version": "settlement_workspace_requirement_v0",
        "settlement_binding_kind": "autonomous_replan",
        "requirement": "not_required",
        "source": "typed_settlement_identity",
        "reason": "autonomous_replan_is_non_repository_control_plane_work",
    }


def test_missing_todo_causality_remains_fail_closed() -> None:
    requirement = resolve_settlement_workspace_requirement(
        None,
        settlement_binding_kind="todo",
    )

    assert requirement["requirement"] == "unknown"
    assert requirement["reason"] == "todo_delivery_contract_not_available"


def test_complete_legacy_todo_settlement_can_finish_quota_accounting() -> None:
    effect_id = "goal:agent:todo_legacy:turn-1"
    requirement = resolve_settlement_workspace_requirement(
        None,
        settlement_binding_kind="todo",
        legacy_settlement_evidence={
            "schema_version": "legacy_settlement_receipt_evidence_v0",
            "settlement_effect_id": effect_id,
            "delivery_workspace_present": False,
            "receipts": [
                {
                    "step_kind": "validation",
                    "status": "committed",
                    "effect_id": effect_id,
                },
                {
                    "step_kind": "durable_writeback",
                    "status": "committed",
                    "effect_id": effect_id,
                },
            ],
        },
    )

    assert requirement == {
        "schema_version": "settlement_workspace_requirement_v0",
        "settlement_binding_kind": "todo",
        "requirement": "not_required",
        "source": "legacy_settlement_receipts",
        "reason": "pre_causality_settlement_already_committed",
    }


def test_partial_legacy_todo_settlement_remains_fail_closed() -> None:
    requirement = resolve_settlement_workspace_requirement(
        None,
        settlement_binding_kind="todo",
        legacy_settlement_evidence={
            "schema_version": "legacy_settlement_receipt_evidence_v0",
            "settlement_effect_id": "effect-1",
            "delivery_workspace_present": False,
            "receipts": [
                {
                    "step_kind": "validation",
                    "status": "committed",
                    "effect_id": "effect-1",
                }
            ],
        },
    )

    assert requirement["requirement"] == "unknown"
