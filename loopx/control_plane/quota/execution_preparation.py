from __future__ import annotations

import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..coordination.local_authority import read_canonical_todos_if_promoted
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
        snapshot = read_canonical_todos_if_promoted(
            runtime_root=Path(runtime_root), goal_id=goal_id, include_leases=True,
        )
        authoritative = [
            row for row in snapshot["todos"]
            if normalize_todo_id(row.get("todo_id")) == todo_id
        ]
        if len(authoritative) != 1:
            facts = _unavailable_facts(
                goal_id=goal_id, todo_id=todo_id, agent_id=safe_agent_id,
            )
        else:
            todo = authoritative[0]
            leases = []
            for lease in snapshot["leases"]:
                if normalize_todo_id(lease.get("todo_id")) != todo_id:
                    continue
                status = str(lease.get("status") or "unknown").strip().lower()
                leases.append({
                    "status": status if status in {"active", "released"} else "unknown",
                    "active": lease_is_active(dict(lease)),
                    **({"owner": lease["owner"]} if normalize_todo_claimed_by(lease.get("owner")) else {}),
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
        claim["command_line"] = shlex.join(["loopx", *map(str, claim["cli_args"])])
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
        or receipt_bound
        or action_portfolio_requires_explicit_selection(payload)
    ):
        return
    preparation = project_selected_todo_execution_preparation(
        runtime_root=runtime_root,
        goal_id=goal_id,
        selected_todo=selected,
        agent_id=agent_id,
    )
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
