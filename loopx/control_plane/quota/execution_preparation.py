from __future__ import annotations

import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..coordination.local_authority import (
    LOCAL_AUTHORITY_SOURCES,
    LOCAL_COORDINATION_TODO_LIST_TIMEOUT_SECONDS,
)
from ..effect_runtime import effect_runtime_result
from ..todos.contract import normalize_todo_claimed_by, normalize_todo_id
from ..work_items.action_selection_contract import (
    action_portfolio_requires_explicit_selection,
)
from ..work_items.task_lease import lease_is_active


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
            facts = _unavailable_facts(
                goal_id=goal_id, todo_id=todo_id, agent_id=safe_agent_id,
            )
        else:
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
                })
            facts = {
                "schema_version": "execution_preparation_facts_v0",
                "goal_id": goal_id,
                "agent_id": safe_agent_id,
                "handoff_mode": str(snapshot.get("handoff_mode") or "hard_lease"),
                "authority_available": True,
                "selected_todo": {
                    "todo_id": todo_id,
                    "status": todo.get("status"),
                    "claimed_by": normalize_todo_claimed_by(todo.get("claimed_by")),
                },
                "leases": leases,
            }
    except (OSError, RuntimeError, TypeError, ValueError):
        facts = _unavailable_facts(
            goal_id=goal_id, todo_id=todo_id, agent_id=safe_agent_id,
        )

    result = effect_runtime_result("work_item.execution_preparation.project", facts)
    if not isinstance(result, dict) or result.get("schema_version") != "execution_preparation_v0":
        raise TypeError("typed execution preparation returned an invalid projection")
    claim = result.get("claim")
    if isinstance(claim, dict) and isinstance(claim.get("cli_args"), list):
        # The displayed command selects a smaller receipt. Hosts still receive
        # the exact mutation argv in cli_args, including on historical captures.
        claim["command_line"] = shlex.join(["loopx", *map(str, claim["cli_args"]), "--thin"])
    return result


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
