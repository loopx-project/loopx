"""Source/transport adapter for the typed governed amendment owner.

The Goal lifecycle and registry guards remain held through canonical read/write;
this named owner does not activate the generic source-session runtime profile.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

from ...agent_registry import registered_agent_ids_for_goal
from ..projects.registry_codec import source_session_registry_transaction, load_project_registry
from ..quota.accounting_admission import quota_accounting_admission
from ..coordination.local_authority import local_authority_is_promoted
from ..effect_runtime import effect_runtime_result, CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS
from ..runtime.time import now_utc_iso
from .goal_ref_validation import exact_goal_ref
from .goal_amendment_proposal import (_derive_open_replan_obligation_inventory, _open_replan_obligation_inventory)
from .source_session_registry_state import current_goal_ref
from .source_session_turn_effects import source_turn_effect_gate_path
from .goal_frontier.ack_policy import replan_successor_transition_ack, autonomous_replan_ack_satisfies_obligation
from ..work_items.autonomous_replan_obligation import ensure_replan_novelty_policy


def _replan_readback(derived: Mapping[str, Any], inventory: list[dict[str, Any]], view: Mapping[str, Any],
                     *, stride_strategy: str = "off") -> list[dict[str, Any]]:
    """Transport the existing replan owner's decisions, never create an ACK."""
    if view.get("current_lifetime") is not True or not isinstance(view.get("intent"), dict):
        return []
    candidates = list((derived.get("autonomous_replan_obligations_by_agent") or {}).values())
    if isinstance(derived.get("autonomous_replan_obligation"), dict):
        candidates.append(derived["autonomous_replan_obligation"])
    obligations: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        normalized = ensure_replan_novelty_policy(candidate)
        obligations.setdefault(normalized["obligation_id"], normalized)
    rows = []
    for item in inventory:
        obligation = obligations[item["obligation_id"]]
        for agent in item["bound_agent_ids"]:
            ack = replan_successor_transition_ack(None, agent_id=agent,
                replan_obligation=obligation, agent_todo_items=view.get("work_items") or [],
                stride_strategy=stride_strategy)
            accepted = autonomous_replan_ack_satisfies_obligation(ack,
                replan_obligation=obligation, acceptance_gaps=None,
                stride_strategy=stride_strategy)
            rows.append({"obligation_id": item["obligation_id"], "agent_id": agent,
                "state": "settled" if accepted else "pending",
                "acknowledgement": ack if accepted else None,
                "provider_revision": view.get("provider_revision")})
    return rows


def _execute_goal_amendment(
    *, registry_path: Path, goal_id: str, goal_instance_id: str,
    operation: str, actor_agent_id: str | None,
    replan_configuration_reader: Callable[[Mapping[str, Any], Path], Mapping[str, Any]],
    parameters: Mapping[str, Any] | None = None,
    runtime_root: Path | None = None, execute: bool = False,
    request_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    requested = exact_goal_ref(goal_id, goal_instance_id)
    parameters = dict(parameters or {})
    if set(parameters) & {"operation", "goal_ref", "source", "runtime_root", "actor_agent_id", "observed_at", "dry_run"}:
        raise ValueError("amendment parameters cannot replace source-owned routing facts")
    # Preserve quota's index -> source -> canonical-writer lock order. The
    # runtime borrows these guards so caller timeout cannot discard its basis.
    initial = load_project_registry(registry_path)
    configured_root = initial.get("common_runtime_root")
    if not isinstance(configured_root, str) or not configured_root:
        raise ValueError("source Goal requires its configured runtime root")
    root = Path(configured_root).expanduser().resolve()
    lock_goal_ref = requested
    if operation in {"inspect", "request_basis", "receipt"}:
        lock_goal_ref, _ = current_goal_ref(initial, goal_id=goal_id, require_active=False)
    with quota_accounting_admission(runtime_root=root, registry_path=registry_path,
        goal_id=goal_id, goal_ref=lock_goal_ref, operation="goal_amendment_source_read") as admission:
        with source_session_registry_transaction(registry_path, operation="goal_amendment_source_read") as transaction:
            registry = transaction.payload_copy()
            goal_ref, goal = current_goal_ref(registry, goal_id=goal_id, require_active=operation not in {"inspect", "request_basis", "receipt", "abort_request"})
            configured = registry.get("common_runtime_root")
            if not isinstance(configured, str) or not configured:
                raise ValueError("source Goal requires its configured runtime root")
            if root != Path(configured).expanduser().resolve():
                raise ValueError("source Goal runtime changed while acquiring its read guards")
            if runtime_root is not None and runtime_root.expanduser().resolve() != root:
                raise ValueError("amendment runtime root does not match source Goal routing")
            if not local_authority_is_promoted(runtime_root=root, goal_id=goal_id):
                raise ValueError("amendment requires existing canonical authority; it never promotes a provider")
            gate_path = source_turn_effect_gate_path(registry_path, goal_id)
            gate = json.loads(gate_path.read_text()) if gate_path.exists() else None
            agents = registered_agent_ids_for_goal(goal)
            effective_replan_goal: Mapping[str, Any] | None = None
            derived = _derive_open_replan_obligation_inventory(runtime_root=root, goal_id=goal_id,
                goal=goal, state_text="", goal_ref=goal_ref)
            obligations = _open_replan_obligation_inventory(goal_id=goal_id, registered_agents=agents, status_item=derived)
            births = [row for row in registry.get("lifetime_receipts", [])
                if row.get("schema_version") == "loopx_goal_creation_receipt_v1" and row.get("goal_ref") == goal_ref]
            commitments = births[0].get("root_commitments") if len(births) == 1 else None
            source = {"profile_id": registry.get("profile_id"), "current_goal_ref": goal_ref,
                "root_commitments": commitments,
                "registry_source": transaction.source_read_witness(), "accounting_admission": admission,
                "gate": gate, "goal_status": goal.get("status"), "registered_agents": agents,
                "open_replan_obligations": obligations}
            if request_context is not None:
                source["request_entry"] = dict(request_context["entry"])
                if request_context.get("reservation_digest") is not None:
                    source["request_reservation_digest"] = request_context["reservation_digest"]
                if request_context.get("preview") is True:
                    source["request_preview"] = True
            common = {"goal_ref": requested, "actor_agent_id": actor_agent_id,
                "runtime_root": str(root), "observed_at": now_utc_iso(), "source": source}

            def inspect() -> dict[str, Any]:
                nonlocal effective_replan_goal
                value = effect_runtime_result("goal.amendment.execute", {**common, "operation": "inspect", "dry_run": True},
                    timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS)
                if not isinstance(value, dict):
                    raise TypeError("Goal amendment readback is invalid")
                # Resolve once for current review observations. Historical
                # receipt lookup must not depend on current configuration IO.
                if effective_replan_goal is None:
                    effective_replan_goal = replan_configuration_reader(goal, root)
                value["replan_observations"] = _replan_readback(derived, obligations, value,
                    stride_strategy=(effective_replan_goal.get("execution_profile") or {}).get("stride_strategy", "off"))
                return value

            if operation in {"propose", "commit", "claim"}:
                before = inspect()
                settled = {(row["obligation_id"], row["agent_id"]) for row in before["replan_observations"] if row["state"] == "settled"}
                source["open_replan_obligations"] = [
                    {**row, "bound_agent_ids": [agent for agent in row["bound_agent_ids"] if (row["obligation_id"], agent) not in settled]}
                    for row in obligations]
            if operation == "inspect":
                result = inspect()
            else:
                result = effect_runtime_result("goal.amendment.execute", {
                    **parameters, **common, "operation": operation, "dry_run": not execute,
                }, timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS)
                if isinstance(result, dict) and operation not in {"receipt", "request_basis"} and result.get("request_outcome") is None and result.get("ok") is True and result.get("status") != "planned":
                    # Current consumption is a separate readback; never rewrite
                    # the original operation's committed receipt or pending fact.
                    result["current_projection"] = inspect()
                    result["current_projection"].pop("ok", None)
    if not isinstance(result, dict):
        raise TypeError("Goal amendment owner returned an invalid result")
    return result


def execute_goal_amendment(
    *, registry_path: Path, goal_id: str, goal_instance_id: str,
    operation: str, actor_agent_id: str | None,
    replan_configuration_reader: Callable[[Mapping[str, Any], Path], Mapping[str, Any]],
    parameters: Mapping[str, Any] | None = None,
    runtime_root: Path | None = None, execute: bool = False,
) -> dict[str, Any]:
    """Route explicit request-derived work through its source reservation owner."""
    params = dict(parameters or {})
    if operation in {"abort_request", "request_basis"}:
        raise ValueError("request effect recovery must use its original request entrypoint")
    if "source_request" not in params:
        return _execute_goal_amendment(registry_path=registry_path, goal_id=goal_id, goal_instance_id=goal_instance_id,
            operation=operation, actor_agent_id=actor_agent_id, parameters=params, runtime_root=runtime_root, execute=execute,
            replan_configuration_reader=replan_configuration_reader)
    if operation not in {"propose", "commit"} or actor_agent_id is None:
        raise ValueError("source request context requires an explicit receiver proposal or commit")
    from ..collaboration.request_amendment import execute_request_amendment, request_proposal_context

    if operation == "commit":
        return execute_request_amendment(registry_path=registry_path, runtime_root=runtime_root,
            goal_ref=exact_goal_ref(goal_id, goal_instance_id), receiver_agent_id=actor_agent_id,
            parameters=params, execute=execute, replan_configuration_reader=replan_configuration_reader)
    root, context = request_proposal_context(registry_path=registry_path, runtime_root=runtime_root,
        goal_ref=exact_goal_ref(goal_id, goal_instance_id), receiver_agent_id=actor_agent_id, reference=params["source_request"])
    return _execute_goal_amendment(registry_path=registry_path, goal_id=goal_id, goal_instance_id=goal_instance_id,
        operation="propose", actor_agent_id=actor_agent_id, parameters=params, runtime_root=root,
        execute=execute, request_context=context, replan_configuration_reader=replan_configuration_reader)
