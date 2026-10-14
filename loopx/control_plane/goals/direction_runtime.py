"""Explicit source/session transport for the typed Goal direction owner.

Shares the existing bounded local-file snapshot reader; transient bytes are returned only
by the Agent's read call. A separate exact-offer acknowledgement records usage.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from ...agent_registry import registered_agent_ids_for_goal
from ...materials import MAX_REVIEW_MATERIAL_BYTES, resolve_review_material_path, read_bounded_material_file
from ..coordination.local_authority import local_authority_is_promoted
from ..effect_runtime import CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS, effect_runtime_result
from .goal_ref_validation import exact_goal_ref
from .source_session_registry_state import current_goal_ref
from .source_session_turn_effects import source_turn_effect_gate_path
from ..projects.registry_codec import load_project_registry, source_session_registry_transaction
from ..quota.accounting_admission import quota_accounting_admission


@contextmanager
def _source(*, registry_path: Path, goal_id: str, goal_instance_id: str, agent_id: str | None,
            historical: bool) -> Iterator[tuple[dict[str, Any], dict[str, Any], Path]]:
    requested = exact_goal_ref(goal_id, goal_instance_id)
    initial = load_project_registry(registry_path)
    configured = initial.get("common_runtime_root")
    if not isinstance(configured, str) or not configured:
        raise ValueError("Direction requires its registered runtime root")
    root = Path(configured).expanduser().resolve()
    lock_ref = current_goal_ref(initial, goal_id=goal_id, require_active=False)[0] if historical else requested
    # Same established lock order as other canonical source-owned operations.
    # This only borrows observation guards; no quota spend or worker is created.
    with quota_accounting_admission(runtime_root=root, registry_path=registry_path, goal_id=goal_id,
                                   goal_ref=lock_ref, operation="goal_direction_source") as admission:
        with source_session_registry_transaction(registry_path, operation="goal_direction_source") as transaction:
            registry = transaction.payload_copy()
            ref, goal = current_goal_ref(registry, goal_id=goal_id, require_active=not historical)
            if root != Path(str(registry.get("common_runtime_root"))).expanduser().resolve():
                raise ValueError("Direction source routing changed")
            if not local_authority_is_promoted(runtime_root=root, goal_id=goal_id):
                raise ValueError("Direction requires existing canonical authority; no provider is promoted")
            gate_path = source_turn_effect_gate_path(registry_path, goal_id)
            source = {"profile_id": registry.get("profile_id"), "current_goal_ref": ref,
                "goal_status": goal.get("status"), "registered_agents": registered_agent_ids_for_goal(goal),
                "gate": json.loads(gate_path.read_text()) if gate_path.exists() else None,
                "registry_source": transaction.source_read_witness(), "accounting_admission": admission,
                "legacy_authority_present": bool(goal.get("authority_registry") or goal.get("authority_sources"))}
            yield {"goal_ref": requested, "actor_agent_id": agent_id, "runtime_root": str(root),
                   "source": source, "observed_at": datetime.now(timezone.utc).isoformat()}, goal, root


def _read(goal: dict[str, Any], root: Path, path: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(path, str) or not path or len(path) > 4096:
        raise ValueError("One bounded local material path is required")
    resolved = resolve_review_material_path(goal=goal, raw_path=path, runtime_root=root, repo_relative=True)
    _, content_bytes, revision, _ = read_bounded_material_file(resolved, MAX_REVIEW_MATERIAL_BYTES)
    content = content_bytes.decode("utf-8")
    if not content.strip():
        raise ValueError("configured authority file is empty")
    return content, {"revision": "sha256:" + revision, "content_sha256": revision,
        "source_ref_sha256": hashlib.sha256(path.encode()).hexdigest(), "byte_count": len(content_bytes)}


def execute_goal_direction(*, registry_path: Path, goal_id: str, goal_instance_id: str,
                           operation: str, agent_id: str | None = None,
                           parameters: Mapping[str, Any] | None = None, execute: bool = False) -> dict[str, Any]:
    parameters = dict(parameters or {})
    if set(parameters) & {"source", "runtime_root", "goal_ref", "actor_agent_id", "observed_at", "dry_run", "operation"}:
        raise ValueError("Direction parameters cannot replace source-owned facts")
    with _source(registry_path=registry_path, goal_id=goal_id, goal_instance_id=goal_instance_id,
                 agent_id=agent_id, historical=operation == "receipt") as (common, goal, root):
        def rpc(op: str, fields: Mapping[str, Any], *, write: bool = False) -> dict[str, Any]:
            result = effect_runtime_result("goal.direction.execute", {**fields, **common, "operation": op, "dry_run": not write},
                                          timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS)
            if not isinstance(result, dict):
                raise TypeError("Direction owner returned an invalid result")
            return result
        if operation == "configure":
            if agent_id is not None:
                raise ValueError("Only the trusted Goal operator may declare direction materials")
            if set(parameters) - {"expected_authority_digest"} != {"materials", "expected_provider_revision", "operation_id"}:
                raise ValueError("Direction configuration fields missing or unsupported")
            materials = parameters["materials"]
            if not isinstance(materials, list) or not 1 <= len(materials) <= 64:
                raise ValueError("Direction declarations must contain 1 to 64 materials")
            admitted = rpc("prepare_configure", {"expected_provider_revision": parameters["expected_provider_revision"],
                                               "operation_id": parameters["operation_id"]})
            if admitted.get("ok") is not True:
                return admitted
            observed_materials = []
            total_bytes = 0
            for value in materials:
                if not isinstance(value, dict) or set(value) != {"material_id", "source_path", "purpose", "topics", "required", "boundary"}:
                    raise ValueError("Material declaration fields missing or unsupported")
                _, observation = _read(goal, root, value["source_path"])
                total_bytes += observation["byte_count"]
                if total_bytes > 2 * 1024 * 1024:
                    raise ValueError("Direction configuration read budget exceeded")
                observed_materials.append({key: item for key, item in value.items() if key != "source_path"} | {
                    "revision": observation["revision"], "source_ref_sha256": observation["source_ref_sha256"]})
            result = rpc(operation, {**parameters, "materials": observed_materials}, write=execute)
        elif operation == "read":
            if set(parameters) != {"material_id", "source_path", "required_revision", "authority_digest", "operation_id"}:
                raise ValueError("Material read fields missing or unsupported")
            source_path = parameters.pop("source_path")
            parameters["source_ref_sha256"] = hashlib.sha256(str(source_path).encode()).hexdigest()
            # Authoritative boundary/actor admission occurs before provider IO.
            admission = rpc("prepare_read", {key: item for key, item in parameters.items() if key != "operation_id"})
            if admission.get("ok") is not True or not execute:
                return admission
            try:
                content, observation = _read(goal, root, source_path)
                if observation["revision"] != parameters["required_revision"]:
                    raise ValueError("actual material revision changed")
            except (OSError, ValueError):
                common["source"]["material_observation"] = {"available": False}
                return rpc("offer_read", parameters, write=True)
            common["source"]["material_observation"] = observation
            result = rpc("offer_read", parameters, write=True)
            if result.get("ok") is True:
                # The offer is stored before delivery, but it is NOT a usage
                # receipt. Only an explicit same-Agent acknowledgement consumes
                # this exact content/revision offer. No body/path is persisted.
                result["content"] = content
                result["receipt_recorded"] = False
        else:
            result = rpc(operation, parameters, write=execute)
        if result.get("ok") is True and operation not in {"inspect", "receipt", "read"} and execute:
            result["current"] = rpc("inspect", {})
        return result
