"""An unreconcilable `--todo-id` guard names its own cause and next read."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from loopx.cli_commands.quota_action_selection import (
    _requested_quota_action_selection_preflight,
)
from loopx.cli_commands.quota_failure_report import quota_failure_payload
from loopx.control_plane.quota.error_codes import (
    QuotaActionSelectionConflictError,
    QuotaActionSelectionConflictKind,
    quota_error_code,
)


REQUESTED_TODO_ID = "todo_requested_selection"
SELECTED_TODO_ID = "todo_projected_selection"


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "should_run": True,
        "effective_action": "normal_run",
        "selected_todo": None,
        "execution_obligation": {"must_attempt_work": True},
        "interaction_contract": {"agent_channel": {"must_attempt": True}},
    }
    payload.update(overrides)
    return payload


def _raise(payload: dict[str, object]) -> QuotaActionSelectionConflictError:
    with pytest.raises(QuotaActionSelectionConflictError) as raised:
        _requested_quota_action_selection_preflight(
            payload,
            requested_todo_id=REQUESTED_TODO_ID,
            receipt_bound_todo_id=None,
            receipt_bound_replan_obligation_id=None,
            receipt_pending_action_todo_id=None,
            receipt_identity_upgraded=False,
        )
    return raised.value


def _qualified_for(todo_id: str) -> dict[str, object]:
    return {
        "schema_version": "action_selection_qualification_v0",
        "state": "qualified",
        "requested_todo_id": REQUESTED_TODO_ID,
        "selected_todo": {"todo_id": todo_id},
    }


def test_conflicting_qualification_names_requested_and_selected_todo() -> None:
    error = _raise(
        _payload(action_selection_qualification=_qualified_for(SELECTED_TODO_ID))
    )

    assert error.kind is QuotaActionSelectionConflictKind.CONFLICT
    assert error.error_code == "quota_action_selection_conflict"
    assert REQUESTED_TODO_ID in str(error)
    assert SELECTED_TODO_ID in str(error)
    assert "qualified" in str(error)
    assert quota_error_code(error) == "quota_action_selection_conflict"


def test_missing_qualification_is_typed_rather_than_unexplained() -> None:
    error = _raise(_payload(selected_todo={"todo_id": SELECTED_TODO_ID}))

    assert error.kind is QuotaActionSelectionConflictKind.UNQUALIFIED
    assert REQUESTED_TODO_ID in str(error)
    assert "no typed action-selection qualification" in str(error)


def test_a_qualified_selection_for_the_requested_todo_is_not_a_conflict() -> None:
    is_conflict = _requested_quota_action_selection_preflight(
        _payload(action_selection_qualification=_qualified_for(REQUESTED_TODO_ID)),
        requested_todo_id=REQUESTED_TODO_ID,
        receipt_bound_todo_id=None,
        receipt_bound_replan_obligation_id=None,
        receipt_pending_action_todo_id=None,
        receipt_identity_upgraded=False,
    )

    assert is_conflict is None


def test_failure_payload_reports_the_conflict_instead_of_collection_failure() -> None:
    error = _raise(
        _payload(action_selection_qualification=_qualified_for(SELECTED_TODO_ID))
    )
    args = argparse.Namespace(
        quota_command="should-run",
        goal_id="quota-conflict-fixture",
        agent_id="agent-fixture",
        runtime_root=None,
        verbose=False,
    )

    payload = quota_failure_payload(
        args,
        registry_path=Path("/tmp/quota-conflict-registry.json"),
        runtime_root_arg=None,
        error=error,
    )

    assert payload["error_code"] == "quota_action_selection_conflict"
    assert payload["status"] == "quota_action_selection_conflict"
    assert payload["reason"] != "quota collection failed"
    assert payload["reason"] == str(error)
    assert "heartbeat receipt writeback" not in str(payload["recommended_action"])
    assert payload["action_selection_conflict"] == {
        "kind": "conflict",
        "requested_todo_id": REQUESTED_TODO_ID,
        "selected_todo_id": SELECTED_TODO_ID,
        "qualification_state": "qualified",
    }


def test_missing_delivery_permission_is_unknown_on_same_selection_refusal() -> None:
    """The projection already selects the requested Todo, so name the refusal."""

    error = _raise(
        _payload(
            should_run=False,
            selected_todo={"todo_id": REQUESTED_TODO_ID},
            action_selection_qualification=_qualified_for(REQUESTED_TODO_ID),
        )
    )

    assert error.kind is QuotaActionSelectionConflictKind.NOT_ADMITTED
    assert error.error_code == "quota_action_selection_conflict"
    assert REQUESTED_TODO_ID in str(error)
    # The self-contradictory sentence puts the same id on both sides; a caller
    # cannot act on it.
    assert "neither the projection's current selection" not in str(error)
    assert "not admitted to settle" in str(error)
    assert error.admission_must_attempt is True
    assert error.admission_delivery_allowed is None


def test_unsettled_prior_turn_is_named_instead_of_a_selection_conflict() -> None:
    """A refused receipt write caused by an unsettled prior Turn names that Turn."""

    prior_turn_id = "2026-09-23T07:29:19.141Z"
    error = _raise(
        _payload(
            should_run=False,
            selected_todo={"todo_id": REQUESTED_TODO_ID},
            action_selection_qualification=_qualified_for(REQUESTED_TODO_ID),
            unsettled_host_turn_recovery={
                "schema_version": "unsettled_host_turn_recovery_v0",
                "binding_id": REQUESTED_TODO_ID,
                "prior_turn_instance_id": prior_turn_id,
                "repair": "resume_prior_turn",
            },
        )
    )

    assert error.kind is QuotaActionSelectionConflictKind.NOT_ADMITTED
    assert prior_turn_id in str(error)
    assert "resume_prior_turn" in str(error)
    assert "neither the projection's current selection" not in str(error)

    args = argparse.Namespace(
        quota_command="should-run",
        goal_id="quota-conflict-fixture",
        agent_id="agent-fixture",
        runtime_root=None,
        verbose=False,
    )
    payload = quota_failure_payload(
        args,
        registry_path=Path("/tmp/quota-conflict-registry.json"),
        runtime_root_arg=None,
        error=error,
    )

    assert payload["action_selection_conflict"] == {
        "kind": "not_admitted",
        "requested_todo_id": REQUESTED_TODO_ID,
        "selected_todo_id": REQUESTED_TODO_ID,
        "qualification_state": "qualified",
        "unsettled_prior_turn_instance_id": prior_turn_id,
        "unsettled_repair": "resume_prior_turn",
        "admission": {"agent_must_attempt": True, "delivery_allowed": None},
    }


def test_refused_delivery_boundary_is_published_as_a_typed_admission_fact() -> None:
    """A caller must be able to read which side of the boundary refused it."""

    error = _raise(
        _payload(
            should_run=False,
            selected_todo={"todo_id": REQUESTED_TODO_ID},
            action_selection_qualification=_qualified_for(REQUESTED_TODO_ID),
            interaction_contract={
                "agent_channel": {"must_attempt": True, "delivery_allowed": False}
            },
        )
    )

    assert error.kind is QuotaActionSelectionConflictKind.NOT_ADMITTED
    assert error.admission_must_attempt is True
    assert error.admission_delivery_allowed is False
    assert "delivery_allowed=False" in str(error)

    args = argparse.Namespace(
        quota_command="should-run",
        goal_id="quota-conflict-fixture",
        agent_id="agent-fixture",
        runtime_root=None,
        verbose=False,
    )
    payload = quota_failure_payload(
        args,
        registry_path=Path("/tmp/quota-conflict-registry.json"),
        runtime_root_arg=None,
        error=error,
    )

    assert payload["action_selection_conflict"]["admission"] == {
        "agent_must_attempt": True,
        "delivery_allowed": False,
    }


def test_retained_selection_names_the_replan_obligation_that_owns_the_turn() -> None:
    """A receipt-bound replan Turn cannot hand its settlement to another Todo."""

    retained_todo_id = "todo_retained_selection"
    replan_obligation_id = "replan-retained-selection-fixture"
    with pytest.raises(QuotaActionSelectionConflictError) as raised:
        _requested_quota_action_selection_preflight(
            _payload(selected_todo={"todo_id": retained_todo_id}),
            requested_todo_id=REQUESTED_TODO_ID,
            receipt_bound_todo_id=None,
            receipt_bound_replan_obligation_id=replan_obligation_id,
            receipt_pending_action_todo_id=retained_todo_id,
            receipt_identity_upgraded=True,
        )
    error = raised.value

    assert error.kind is QuotaActionSelectionConflictKind.CONFLICT
    assert error.retained_selection is True
    assert error.receipt_replan_obligation_id == replan_obligation_id
    assert retained_todo_id in str(error)
    assert replan_obligation_id in str(error)
    assert "retained pending selection" in str(error)
    assert "own" in str(error.recommended_action)

    args = argparse.Namespace(
        quota_command="should-run",
        goal_id="quota-conflict-fixture",
        agent_id="agent-fixture",
        runtime_root=None,
        verbose=False,
    )
    payload = quota_failure_payload(
        args,
        registry_path=Path("/tmp/quota-conflict-registry.json"),
        runtime_root_arg=None,
        error=error,
    )

    assert payload["action_selection_conflict"] == {
        "kind": "conflict",
        "requested_todo_id": REQUESTED_TODO_ID,
        "selected_todo_id": retained_todo_id,
        "qualification_state": "retained_selection",
        "retained_selection": True,
        "retained_selection_todo_id": retained_todo_id,
        "receipt_replan_obligation_id": replan_obligation_id,
    }
