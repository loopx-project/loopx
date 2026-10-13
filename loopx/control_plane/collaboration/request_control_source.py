"""Authenticated source and atomic-file adapter for request control.

Request and Goal transactions never hold each other's locks. A reservation is
durable before the Goal call; only an independently read terminal Goal receipt
can release it. Public callers cannot supply amendment/terminal source facts.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle, goal_accepts_collaboration
from .inbox import _entry, _hash, _now, _read, _receipt, _request_lock, _root, _target, _write
from .peer_context_observation import require_parent_context_access
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..content_digest import BARE_SHA256_PATTERN
from ..projects.registry_codec import load_project_registry


class RequestControlRejected(ValueError):
    """Keep the typed owner's refusal code through CLI/HTTP adapters."""

    def __init__(self, message: str, diagnostic_code: str):
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


@contextmanager
def _locked_request(*, registry_path: Path, runtime_root: Path, goal_ref: Mapping[str, str],
                    receiver_agent_id: str, request_id: str) -> Iterator[tuple[Any, Path, dict[str, Any]]]:
    if not isinstance(request_id, str) or not BARE_SHA256_PATTERN.fullmatch(request_id):
        raise ValueError("invalid controlled request id")
    with collaboration_goal_scope(registry_path, goal_id=goal_ref["goal_id"], agents=(),
        caller_goal_ref=dict(goal_ref), require_active=False, lock_registry=True) as scope:
        if not scope.exact:
            raise ValueError("request control requires the exact source-session profile")
        registry = load_project_registry(registry_path)
        configured_root = registry.get("common_runtime_root")
        if not isinstance(configured_root, str) or Path(configured_root).expanduser().resolve() != runtime_root.resolve():
            raise ValueError("request control runtime does not match its registered source")
        decide_collaboration_lifecycle(scope, operation="request_create")
        path = _root(runtime_root) / "entries" / _hash(_target(scope.goal_id, receiver_agent_id, scope)) / (request_id + ".json")
        with _request_lock(runtime_root, request_id, scope, path.with_suffix(".lock")):
            entry = _entry(runtime_root, scope.goal_id, receiver_agent_id, request_id, scope=scope)
            yield scope, path, entry


def _actor(root: Path, registry: Path, scope: Any, entry: dict[str, Any], actor_agent_id: str | None) -> str:
    source = require_parent_context_access(root, registry, entry, entry["agent_id"], scope=scope)
    if not source["local_source"]:
        raise ValueError("request effect control is not qualified for an external source")
    if actor_agent_id == entry["agent_id"]:
        from ...agent_registry import registered_agent_ids_for_goal

        if actor_agent_id not in registered_agent_ids_for_goal(scope.goal):
            raise ValueError("the receiving Agent is no longer registered")
        return "receiver"
    if entry.get("source_kind") == "peer":
        if actor_agent_id != entry.get("source_agent_id"):
            raise ValueError("only the original requester or receiver may control this request")
        from ...agent_registry import registered_agent_ids_for_goal

        if actor_agent_id not in registered_agent_ids_for_goal(scope.goal):
            raise ValueError("the original requesting Agent is no longer registered")
        return "source"
    if actor_agent_id is not None:
        raise ValueError("an Agent cannot impersonate the original conversation owner")
    # A local operator must still resolve this request's exact, current source
    # and grant. A raw session id, Goal alias or request hash is not authority.
    from .source_grant_observation import source_context_target_authority
    from .source_chat_observation import observe_source_chat, original_delivery_facts

    route = _read(_root(root) / "roundtrips" / (entry["request_id"] + ".json"))
    session, turn = observe_source_chat(root, route)
    grant = source_context_target_authority(root, session=session, turn=turn,
        target={"goal_id": entry["goal_id"], "agent_id": entry["agent_id"]})
    decide_collaboration_lifecycle(scope, operation="original_request_inspect", record=entry, route=route,
        initial_delivery=original_delivery_facts(entry, route, turn, source_id=grant.get("source_id")))
    if grant.get("source_id") != entry["source_id"]:
        raise ValueError("request source grant no longer matches its original principal")
    return "source"


def _execute(*, registry_path: Path, runtime_root: Path, goal_ref: Mapping[str, str],
             receiver_agent_id: str, request_id: str, actor_agent_id: str | None,
             operation: str, parameters: Mapping[str, Any], execute: bool,
             resolved_amendment: Mapping[str, Any] | None = None,
             terminal_receipt: Mapping[str, Any] | None = None, include_entry: bool = False,
             requested_basis: Mapping[str, Any] | None = None) -> dict[str, Any]:
    with _locked_request(registry_path=registry_path, runtime_root=runtime_root, goal_ref=goal_ref,
                         receiver_agent_id=receiver_agent_id, request_id=request_id) as (scope, path, entry):
        role = _actor(runtime_root, registry_path, scope, entry, actor_agent_id)
        decision, error = _receipt(runtime_root, "decisions", entry)
        read, read_error = _receipt(runtime_root, "reads", entry)
        source = {"actor": role, "actor_agent_id": actor_agent_id, "observed_at": _now(),
            "goal_active": goal_accepts_collaboration(scope.goal),
            "legacy_assessment": decision or None,
            "legacy_assessment_unavailable": bool(error),
            "legacy_read": read or None, "legacy_read_unavailable": bool(read_error),
            "amendment": dict(resolved_amendment) if resolved_amendment is not None else None,
            "terminal": dict(terminal_receipt) if terminal_receipt is not None else None,
            "requested_basis": dict(requested_basis) if requested_basis is not None else None}
        try:
            plan = effect_runtime_result("collaboration.request.control", {
                "entry": entry, "input": {**parameters, "operation": operation}, "source": source})
        except EffectRuntimeRejected as exc:
            raise RequestControlRejected(str(exc), exc.diagnostic_code) from exc
        if plan["changed"] and execute:
            _write(path, plan["entry"])
            if _read(path) != plan["entry"]:
                raise ValueError("request control write readback is unavailable; retry the original operation")
        return {"ok": True, "executed": bool(execute and plan["changed"]),
            "planned": bool(not execute and plan["changed"]), "replayed": plan.get("replayed", False),
            "result": plan.get("result"), "view": plan["view"], "execution_authority_granted": False,
            **({"_entry": plan.get("entry", entry), "_legacy_assessment": decision or None,
                "_legacy_assessment_unavailable": bool(error),
                "_legacy_read": read or None, "_legacy_read_unavailable": bool(read_error),
                "_request_reference": plan.get("verified_request_basis")} if include_entry else {})}


def request_amendment_snapshot(*, registry_path: Path, runtime_root: Path, goal_ref: Mapping[str, str],
                               receiver_agent_id: str, request_id: str, actor_agent_id: str | None,
                               requested_basis: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Internal authenticated snapshot; never expose the source entry as status."""
    return _execute(registry_path=registry_path, runtime_root=runtime_root, goal_ref=goal_ref,
        receiver_agent_id=receiver_agent_id, request_id=request_id, actor_agent_id=actor_agent_id,
        operation="inspect", parameters={}, execute=False, include_entry=True, requested_basis=requested_basis)


def execute_request_control(*, registry_path: Path, runtime_root: Path, goal_ref: Mapping[str, str],
                            receiver_agent_id: str, request_id: str, actor_agent_id: str | None,
                            operation: str, parameters: Mapping[str, Any] | None = None,
                            execute: bool = False) -> dict[str, Any]:
    if operation not in {"inspect", "revise", "cancel", "read", "assess"}:
        raise ValueError("request control operation is unavailable at this entrypoint")
    params = dict(parameters or {})
    if set(params) & {"operation", "source", "entry", "terminal", "amendment"}:
        raise ValueError("request parameters cannot supply source-owned facts")
    return _execute(registry_path=registry_path, runtime_root=runtime_root, goal_ref=goal_ref,
        receiver_agent_id=receiver_agent_id, request_id=request_id, actor_agent_id=actor_agent_id,
        operation=operation, parameters=params, execute=execute)
