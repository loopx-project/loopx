from __future__ import annotations

import hashlib
import shlex
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...file_lock import exclusive_cross_runtime_file_lock, exclusive_mutation_file_lock
from ..coordination.local_authority import (
    local_authority_is_promoted,
    LOCAL_AUTHORITY_SOURCES,
    LOCAL_COORDINATION_TODO_LIST_TIMEOUT_SECONDS,
)
from ..coordination.shadow_management import (
    require_shadow_primary_write_allowed,
    shadow_maintenance_lock_target,
)
from ..effect_runtime import effect_runtime_result
from ..goals.activation import goal_is_stopped
from ..todos.contract import normalize_todo_claimed_by, normalize_todo_id
from ..work_items.action_selection_contract import (
    action_portfolio_requires_explicit_selection,
)
from ..work_items.task_lease import lease_is_active
from ..work_items.recommendation_source_io import load_recommendation_source_goal

if TYPE_CHECKING:
    from .settlement import QuotaSettlementReadback


def _unavailable_facts(*, goal_id: str, todo_id: str, agent_id: str) -> dict[str, Any]:
    return {
        "schema_version": "execution_preparation_facts_v0",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "handoff_mode": "hard_lease",
        "authority_available": False,
        "selected_todo": {"todo_id": todo_id, "status": "open"},
        "leases": [],
    }


def project_selected_todo_execution_preparation(
    *,
    runtime_root: str | Path | None,
    goal_id: str,
    selected_todo: Mapping[str, Any],
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Read the selected Todo's canonical lease and project one preparation step.

    No lease is acquired here.  ``claim.cli_args`` is an ordinary managed
    claim that a Host may execute before launching the model; the claim remains
    the mutation authority and rechecks current workspace and conflicts.
    """

    todo_id = normalize_todo_id(selected_todo.get("todo_id"))
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    if not todo_id or not safe_agent_id or runtime_root is None:
        return None
    try:
        snapshot = _read_execution_snapshot(runtime_root, goal_id, todo_id)
        facts = _execution_facts(snapshot, goal_id, todo_id, safe_agent_id)
    except (OSError, RuntimeError, TypeError, ValueError):
        facts = _unavailable_facts(goal_id=goal_id, todo_id=todo_id, agent_id=safe_agent_id)
    return _project_execution_facts(facts)


def _read_execution_snapshot(
    runtime_root: str | Path, goal_id: str, todo_id: str,
) -> dict[str, Any] | None:
    response = effect_runtime_result(
        "todo.read",
        {"schema_version": "todo_read_request_v0", "kind": "execution_preparation_source",
         "source": {"runtime_root": str(Path(runtime_root).expanduser().resolve(strict=False)),
                    "goal_id": goal_id},
         "todo_id": todo_id},
        timeout=LOCAL_COORDINATION_TODO_LIST_TIMEOUT_SECONDS,
    )
    snapshot = (
        response.get("execution_preparation_source") if isinstance(response, dict)
        and response.get("schema_version") == "todo_read_result_v0" else None
    )
    if (
        not isinstance(snapshot, dict) or snapshot.get("goal_id") != goal_id
        or snapshot.get("todo_id") != todo_id or snapshot.get("source_authority") not in LOCAL_AUTHORITY_SOURCES
        or snapshot.get("decision_read_from_provider") is not True
        or snapshot.get("legacy_fallback_used") is not False
        or not isinstance(snapshot.get("provider_revision"), str) or not snapshot["provider_revision"]
        or not isinstance(snapshot.get("todo"), dict) or snapshot["todo"].get("todo_id") != todo_id
        or not isinstance(snapshot.get("leases"), list)
    ):
        return None
    return snapshot


def _execution_facts(
    snapshot: dict[str, Any] | None, goal_id: str, todo_id: str, agent_id: str,
) -> dict[str, Any]:
    if snapshot is None:
        return _unavailable_facts(goal_id=goal_id, todo_id=todo_id, agent_id=agent_id)
    todo = snapshot["todo"]
    leases = []
    for lease in snapshot["leases"]:
        if not isinstance(lease, dict) or lease.get("todo_id") != todo_id:
            raise ValueError("execution preparation returned a lease for a different Todo")
        status = str(lease.get("status") or "unknown").strip().lower()
        leases.append({
            "status": status if status in {"active", "released"} else "unknown",
            "active": lease_is_active(dict(lease)),
            **({"owner": lease["owner"]} if normalize_todo_claimed_by(lease.get("owner")) else {}),
            "lease_proof": {"idempotency_key": lease.get("idempotency_key"),
                            "expected_version": lease.get("version")},
            "lease_epoch": lease.get("lease_epoch"),
        })
    return {
        "schema_version": "execution_preparation_facts_v0",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "handoff_mode": str(snapshot.get("handoff_mode") or "hard_lease"),
        "authority_available": True,
        "selected_todo": {
            "todo_id": todo_id,
            "status": todo.get("status"),
            "claimed_by": normalize_todo_claimed_by(todo.get("claimed_by")),
        },
        "leases": leases,
    }


def _project_execution_facts(facts: dict[str, Any]) -> dict[str, Any]:
    result = effect_runtime_result("work_item.execution_preparation.project", facts)
    if not isinstance(result, dict) or result.get("schema_version") != "execution_preparation_v0":
        raise TypeError("typed execution preparation returned an invalid projection")
    claim = result.get("claim")
    if isinstance(claim, dict) and isinstance(claim.get("cli_args"), list):
        claim["command_line"] = shlex.join(["loopx", *map(str, claim["cli_args"]), "--thin"])
    return result


class RefreshTaskLeaseUnavailable(ValueError):
    code = "refresh_task_lease_unavailable"


def prepared_lease_receipt_fields(
    proof: Mapping[str, Any], *, lease_epoch: int | None = None,
) -> dict[str, Any]:
    """Serialize the prepared generation without persisting its operation key."""
    return {
        "prepared_lease_key_sha256": hashlib.sha256(proof["idempotency_key"].encode("utf-8")).hexdigest(),
        "prepared_lease_version": proof["expected_version"],
        **({"prepared_lease_epoch": lease_epoch} if lease_epoch is not None else {}),
    }


def quota_prepared_lease_receipt_fields(
    preparation: object, *, todo_id: str | None, selected_todo_id: object,
    agent_id: str | None, delivery_admitted: bool,
) -> dict[str, Any]:
    if (
        todo_id is None or selected_todo_id != todo_id
        or not isinstance(preparation, Mapping)
        or preparation.get("handoff_mode") != "hard_lease"
        or preparation.get("todo_id") != todo_id
        or preparation.get("agent_id") != agent_id
    ):
        return {}
    fields: dict[str, Any] = {"prepared_lease_required": True}
    if delivery_admitted and preparation.get("state") == "ready" and preparation.get("delivery_allowed") is True:
        fields.update(prepared_lease_receipt_fields(
            preparation["lease_proof"], lease_epoch=preparation.get("lease_epoch"),
        ))
    return fields


@contextmanager
def guard_first_writeback_execution(
    *, runtime_root: Path, registry_path: Path, goal_id: str,
    todo_id: str, agent_id: str, requested_proof: dict[str, Any] | None,
    settlement: QuotaSettlementReadback,
    locked_goal: dict[str, Any] | None,
) -> Iterator[None]:
    prepared_lease = settlement.prepared_lease
    prepared_required = settlement.prepared_lease_required or prepared_lease is not None
    if (requested_proof is None and not prepared_required
            and not local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal_id)):
        yield
        return

    # Legacy spend acquires maintenance before the run index. Try maintenance
    # once here so this order cannot block a competing accounting process.
    with ExitStack() as locks:
        locks.enter_context(exclusive_mutation_file_lock(
            shadow_maintenance_lock_target(runtime_root, goal_id),
            timeout_seconds=0,
            operation="refresh-state-execution",
        ))
        require_shadow_primary_write_allowed(runtime_root, goal_id)
        snapshot = _read_execution_snapshot(runtime_root, goal_id, todo_id)
        if snapshot is None:
            raise RefreshTaskLeaseUnavailable("The selected Todo's execution authority could not be read.")
        if prepared_required and snapshot.get("handoff_mode") != "hard_lease":
            raise RefreshTaskLeaseUnavailable("The Todo's original hard-lease authority is unavailable.")
        if snapshot.get("handoff_mode") == "hard_lease":
            todo_open = snapshot["todo"].get("status") == "open"
            facts = _execution_facts(snapshot, goal_id, todo_id, agent_id)
            current = _project_execution_facts({**facts, "include_retired_lease_for_writeback": not todo_open})
            current_proof = (current.get("lease_proof") if current.get("state") == "ready"
                             else current.get("retired_lease_proof"))
            if todo_open or prepared_required:
                # Legacy prepared receipts can prove their exact version, but cannot
                # advance to a later version without a recorded generation.
                lease_epoch = (current.get("lease_epoch")
                               if prepared_lease and "prepared_lease_epoch" in prepared_lease else None)
                # Recovery after an authoritative lifecycle transition can read
                # the original retired proof from the same locked snapshot.
                proof = current_proof if not todo_open and requested_proof is None else requested_proof
                execution_bound = (
                    proof is not None
                    and current_proof == proof
                    and prepared_lease == prepared_lease_receipt_fields(proof, lease_epoch=lease_epoch)
                )
            else:
                # Older admitted closeouts may predate persisted lease readiness.
                # Their retired holder still has to match the original binding.
                admission = settlement.native_child_admission or {}
                execution_bound = (current_proof is not None
                    and admission.get("report_permission") in {"new_operation", "existing_operation_only"}
                    and (requested_proof is None or requested_proof == current_proof))
            if not execution_bound:
                raise RefreshTaskLeaseUnavailable(
                    "The original writeback needs a current task lease recorded for this Turn. "
                    "Read the same Todo and Turn through its current guard before retrying."
                )
            if snapshot["todo"].get("status") == "done" and prepared_required and settlement.completion_event is None:
                raise RefreshTaskLeaseUnavailable("The completed Todo is missing this Turn's completion receipt.")
            goal = locked_goal
            if goal is None:
                source, _ = load_recommendation_source_goal(registry_path, goal_id)
                invoked = registry_path.resolve()
                for registry in dict.fromkeys((source, invoked)):
                    locks.enter_context(exclusive_cross_runtime_file_lock(
                        registry, timeout_seconds=0, operation="refresh-state-execution",
                    ))
                current_source, goal = load_recommendation_source_goal(registry_path, goal_id)
                if current_source != source or goal is None:
                    raise RefreshTaskLeaseUnavailable("The Goal source changed before this writeback committed.")
            if goal_is_stopped(goal):
                raise RefreshTaskLeaseUnavailable("The Goal was stopped before this writeback committed.")
        yield


def apply_selected_todo_execution_preparation(
    payload: dict[str, Any],
    *,
    runtime_root: str | Path | None,
    goal_id: str,
    agent_id: str | None,
    obligation: dict[str, Any] | None = None,
    recommendation: dict[str, Any] | None = None,
    receipt_bound: bool = False,
) -> None:
    selected = payload.get("selected_todo")
    if (
        not isinstance(selected, dict)
        or payload.get("should_run") is not True
        # Planning and recovery can retain a Todo without authorizing its
        # business execution. Preserve their existing typed route.
        or payload.get("normal_delivery_allowed") is not True
        or action_portfolio_requires_explicit_selection(payload)
    ):
        return
    # Consume the same explicit non-delivery fact as the typed interaction
    # owner. Planning/control-plane work does not execute the selected Todo;
    # its lifecycle or lease cannot replace that original operation. Omitted
    # facts retain the existing preparation checks for ordinary delivery.
    if obligation is not None and obligation.get("delivery_allowed") is False:
        return
    preparation = project_selected_todo_execution_preparation(
        runtime_root=runtime_root,
        goal_id=goal_id,
        selected_todo=selected,
        agent_id=agent_id,
    )
    # Existing receipt-bound recovery predates this projection and may be
    # reconstructed without canonical lease authority in synthetic/legacy
    # environments. Preserve that recovery path, while a Host that just ran a
    # projected claim still requires the explicit `ready` postcondition before
    # launching work. Available authority continues to surface conflicts and
    # stale leases for the same bound Todo.
    if receipt_bound and (
        preparation is None
        or preparation.get("state") == "authority_unavailable"
    ):
        return
    if preparation is None or preparation.get("state") == "not_required":
        return
    payload["execution_preparation"] = preparation
    state = preparation.get("state")
    if state == "ready":
        return
    payload["normal_delivery_allowed"] = False
    if obligation is not None:
        obligation["delivery_allowed"] = False
    reason = str(preparation.get("reason") or "Selected Todo execution is not prepared.")
    payload["reason"] = reason
    if recommendation is not None:
        recommendation["reason"] = reason
    if state == "claim_required":
        selected["claim_required_before_work"] = True
        payload["actionable_by_codex"] = True
        if obligation is not None:
            obligation["must_attempt_work"] = True
            obligation["reason"] = reason
        command = (preparation.get("claim") or {}).get("command_line")
        if isinstance(command, str):
            payload["recommended_action"] = command
    else:
        payload["should_run"] = False
        payload["actionable_by_codex"] = False
        if obligation is not None:
            obligation["must_attempt_work"] = False


def attach_interaction_execution_preparation(
    contract: dict[str, Any], payload: dict[str, Any],
) -> None:
    preparation = payload.get("execution_preparation")
    if not isinstance(preparation, dict):
        return
    contract["agent_channel"]["execution_preparation"] = preparation
    contract["cli_channel"]["execution_preparation"] = preparation
    state = preparation.get("state")
    if state == "ready":
        return
    reason = str(preparation.get("reason") or "Selected Todo execution is not prepared.")
    contract["agent_channel"]["delivery_allowed"] = False
    contract["agent_channel"]["primary_action"] = reason
    if state != "claim_required":
        return
    claim = preparation.get("claim")
    command = claim.get("command_line") if isinstance(claim, dict) else None
    if isinstance(command, str) and command:
        contract["agent_channel"]["must_attempt"] = True
        contract["agent_channel"]["quiet_noop_allowed"] = False
        contract["agent_channel"]["primary_action"] = (
            f"Run the atomic execution preparation action: {command}"
        )
        contract["cli_channel"]["next_cli_actions"] = [command]
        for key in (
            "settlement_plan", "quota_spend_source", "replan_settlement_contract",
        ):
            contract["cli_channel"].pop(key, None)
