from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..control_plane.projects.registry_codec import mutate_project_registry
from ..control_plane.runtime.public_safety import validate_public_safe_value
from ..history import load_registry
from ..registry import find_registry_goal
from .manifest import EXTERNAL_CAPABILITY_PROFILE_SCHEMA_VERSION
from .runtime import (
    _execute_extension_runtime_binding,
    extension_catalog_entries,
    resolve_extension_runtime_binding,
)

EXTERNAL_CAPABILITY_INVOCATION_SCHEMA_VERSION = (
    "loopx_external_domain_capability_invocation_v0"
)
GOAL_EXTERNAL_CAPABILITY_BINDING_SCHEMA_VERSION = (
    "loopx_goal_external_capability_binding_v0"
)
GOAL_EXTERNAL_CAPABILITY_BINDING_RECEIPT_SCHEMA_VERSION = (
    "loopx_goal_external_capability_binding_receipt_v0"
)
_RESULT_FIELDS = {
    "schema_version",
    "invocation_id",
    "status",
    "observations",
    "domain_state_mutations",
    "domain_transition_receipts",
    "transition_proposals",
    "effect_receipt",
    "follow_up",
}


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): deepcopy(item) for key, item in value.items()}


def _token(value: object, label: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(f"{label} is required")
    return token


def _canonical_digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "external capability payload must be JSON serializable"
        ) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _operation_from_profile(
    profile: Mapping[str, Any], operation_id: str
) -> dict[str, Any]:
    if profile.get("schema_version") != EXTERNAL_CAPABILITY_PROFILE_SCHEMA_VERSION:
        raise ValueError("external capability integration profile is invalid")
    operations = profile.get("operations")
    if not isinstance(operations, list):
        raise ValueError(
            "external capability integration profile operations are invalid"
        )
    matching = [
        dict(item)
        for item in operations
        if isinstance(item, Mapping) and item.get("id") == operation_id
    ]
    if len(matching) != 1:
        raise ValueError(
            f"external capability profile does not declare operation `{operation_id}`"
        )
    return matching[0]


def resolve_external_capability_binding(
    *,
    state_file: str | Path,
    capability_id: str,
    operation: str,
) -> dict[str, Any]:
    """Resolve one ready extension-owned capability and its snapshotted profile."""

    matches: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    for entry in extension_catalog_entries(state_file=state_file):
        provider = entry.get("provider")
        if not isinstance(provider, Mapping) or provider.get("ready") is not True:
            continue
        capabilities = entry.get("capabilities")
        if not isinstance(capabilities, list):
            continue
        for capability in capabilities:
            if not isinstance(capability, Mapping):
                continue
            if capability.get("id") != capability_id:
                continue
            profile = capability.get("integration_profile")
            if not isinstance(profile, Mapping):
                continue
            matches.append((str(provider.get("id") or ""), capability, profile))
    if not matches:
        raise ValueError(
            f"no enabled, doctor-ready external provider exposes `{capability_id}`"
        )
    if len(matches) != 1:
        providers = [extension_id for extension_id, _, _ in matches]
        raise ValueError(
            f"multiple enabled, doctor-ready external providers expose "
            f"`{capability_id}`: {providers}"
        )
    extension_id, matched_capability, profile = matches[0]
    operation_profile = _operation_from_profile(profile, operation)
    permission = _token(
        operation_profile.get("required_permission"),
        "external capability required_permission",
    )
    protocol = _token(profile.get("protocol"), "external capability protocol")
    runtime_binding = resolve_extension_runtime_binding(
        extension_id,
        state_file=state_file,
        protocol=protocol,
        permission=permission,
    )
    return {
        **runtime_binding,
        "capability_id": capability_id,
        "operation": deepcopy(operation_profile),
        "integration_profile_digest": _token(
            matched_capability.get("integration_profile_digest"),
            "external capability integration_profile_digest",
        ),
    }


def _goal_binding(
    value: Mapping[str, Any],
    *,
    capability_id: str,
    operation: str,
    provider_binding: Mapping[str, Any],
    expected_goal_id: str | None = None,
) -> dict[str, Any]:
    binding = _mapping(value, "Goal capability binding")
    expected_fields = {
        "schema_version",
        "goal_id",
        "capability_id",
        "operations",
        "provider",
    }
    if set(binding) != expected_fields:
        raise ValueError("Goal capability binding fields are invalid")
    if binding.get("schema_version") != GOAL_EXTERNAL_CAPABILITY_BINDING_SCHEMA_VERSION:
        raise ValueError(
            "Goal capability binding must use "
            f"{GOAL_EXTERNAL_CAPABILITY_BINDING_SCHEMA_VERSION}"
        )
    goal_id = _token(binding.get("goal_id"), "Goal capability binding goal_id")
    if expected_goal_id is not None and goal_id != expected_goal_id:
        raise ValueError("Goal capability binding goal_id does not match request")
    if binding.get("capability_id") != capability_id:
        raise ValueError("Goal capability binding capability_id does not match request")
    operations = binding.get("operations")
    normalized_operations = _normalized_operations(operations)
    if operation not in normalized_operations:
        raise ValueError("operation is not enabled for this Goal")
    provider = _mapping(binding.get("provider"), "Goal capability binding provider")
    expected_provider = {
        "extension_id": _token(
            provider_binding.get("extension_id"), "active provider extension_id"
        ),
        "revision": _token(
            provider_binding.get("revision"), "active provider revision"
        ),
        "profile_digest": _token(
            provider_binding.get("integration_profile_digest"),
            "active provider profile_digest",
        ),
    }
    if provider != expected_provider:
        raise ValueError(
            "Goal capability binding does not match the active provider revision/profile"
        )
    normalized = {
        "schema_version": GOAL_EXTERNAL_CAPABILITY_BINDING_SCHEMA_VERSION,
        "goal_id": goal_id,
        "capability_id": capability_id,
        "operations": normalized_operations,
        "provider": expected_provider,
    }
    return {**normalized, "binding_digest": _canonical_digest(normalized)}


def _normalized_operations(values: object) -> list[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= 32:
        raise ValueError("Goal capability binding operations are invalid")
    operations = [_token(item, "Goal capability binding operation") for item in values]
    if len(operations) != len(set(operations)):
        raise ValueError("Goal capability binding operations must be unique")
    return operations


def build_goal_external_capability_binding(
    *,
    state_file: str | Path,
    goal_id: str,
    capability_id: str,
    operations: list[str],
) -> dict[str, Any]:
    """Pin one Goal capability to the exact ready provider revision and profile."""

    normalized_goal_id = _token(goal_id, "Goal capability binding goal_id")
    normalized_capability_id = _token(
        capability_id, "Goal capability binding capability_id"
    )
    normalized_operations = _normalized_operations(operations)
    provider_bindings = [
        resolve_external_capability_binding(
            state_file=state_file,
            capability_id=normalized_capability_id,
            operation=operation,
        )
        for operation in normalized_operations
    ]
    first = provider_bindings[0]
    provider = {
        "extension_id": _token(first.get("extension_id"), "provider extension_id"),
        "revision": _token(first.get("revision"), "provider revision"),
        "profile_digest": _token(
            first.get("integration_profile_digest"), "provider profile_digest"
        ),
    }
    for binding in provider_bindings[1:]:
        current = {
            "extension_id": _token(
                binding.get("extension_id"), "provider extension_id"
            ),
            "revision": _token(binding.get("revision"), "provider revision"),
            "profile_digest": _token(
                binding.get("integration_profile_digest"),
                "provider profile_digest",
            ),
        }
        if current != provider:
            raise ValueError(
                "Goal capability operations do not resolve to one provider revision"
            )
    binding = {
        "schema_version": GOAL_EXTERNAL_CAPABILITY_BINDING_SCHEMA_VERSION,
        "goal_id": normalized_goal_id,
        "capability_id": normalized_capability_id,
        "operations": normalized_operations,
        "provider": provider,
    }
    return {**binding, "binding_digest": _canonical_digest(binding)}


def _goal_capability_bindings(goal: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = goal.get("external_capability_bindings")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("Goal external_capability_bindings must be a list")
    bindings: list[dict[str, Any]] = []
    capability_ids: set[str] = set()
    for index, value in enumerate(raw):
        binding = _mapping(value, f"Goal external_capability_bindings[{index}]")
        capability_id = _token(
            binding.get("capability_id"),
            f"Goal external_capability_bindings[{index}].capability_id",
        )
        if capability_id in capability_ids:
            raise ValueError(
                f"Goal has duplicate external capability binding `{capability_id}`"
            )
        capability_ids.add(capability_id)
        bindings.append(binding)
    return bindings


def load_goal_external_capability_binding(
    *,
    registry_path: str | Path,
    goal_id: str,
    capability_id: str,
) -> dict[str, Any]:
    """Load one durable Goal-scoped external capability binding."""

    path = Path(registry_path).expanduser()
    if not path.is_file():
        raise ValueError(f"LoopX registry does not exist: {path}")
    requested_goal_id = _token(goal_id, "goal_id")
    goal = find_registry_goal(load_registry(path), requested_goal_id)
    if goal is None:
        raise ValueError(f"LoopX registry does not contain Goal `{requested_goal_id}`")
    containing_goal_id = _token(goal.get("id"), "registry Goal id")
    if containing_goal_id != requested_goal_id:
        raise ValueError("registry Goal id does not match requested goal_id")
    normalized_capability_id = _token(capability_id, "capability_id")
    matching = [
        binding
        for binding in _goal_capability_bindings(goal)
        if binding.get("capability_id") == normalized_capability_id
    ]
    if not matching:
        raise ValueError(
            f"Goal `{requested_goal_id}` does not enable external capability "
            f"`{normalized_capability_id}`"
        )
    selected = matching[0]
    binding_goal_id = _token(
        selected.get("goal_id"),
        "Goal external capability binding goal_id",
    )
    if binding_goal_id != containing_goal_id:
        raise ValueError(
            "Goal external capability binding goal_id does not match containing Goal"
        )
    return selected


def bind_external_capability_to_goal(
    *,
    registry_path: str | Path,
    state_file: str | Path,
    goal_id: str,
    capability_id: str,
    operations: list[str],
    execute: bool = False,
) -> dict[str, Any]:
    """Preview or atomically persist one exact Goal-scoped provider binding."""

    path = Path(registry_path).expanduser()
    candidate_with_digest = build_goal_external_capability_binding(
        state_file=state_file,
        goal_id=goal_id,
        capability_id=capability_id,
        operations=operations,
    )
    binding_digest = str(candidate_with_digest.pop("binding_digest"))

    def prepare(registry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        goal = find_registry_goal(registry, str(candidate_with_digest["goal_id"]))
        if goal is None:
            raise ValueError(
                "LoopX registry does not contain Goal "
                f"`{candidate_with_digest['goal_id']}`"
            )
        existing = _goal_capability_bindings(goal)
        matching = [
            item
            for item in existing
            if item.get("capability_id") == candidate_with_digest["capability_id"]
        ]
        changed = matching != [candidate_with_digest]
        merged = [
            item
            for item in existing
            if item.get("capability_id") != candidate_with_digest["capability_id"]
        ]
        merged.append(deepcopy(candidate_with_digest))
        merged.sort(key=lambda item: str(item.get("capability_id") or ""))
        receipt: dict[str, Any] = {
            "ok": True,
            "schema_version": (GOAL_EXTERNAL_CAPABILITY_BINDING_RECEIPT_SCHEMA_VERSION),
            "status": "ready" if not execute else "written",
            "dry_run": not execute,
            "executed": execute,
            "changed": changed,
            "written": False,
            "registry": str(path),
            "goal_id": candidate_with_digest["goal_id"],
            "capability_id": candidate_with_digest["capability_id"],
            "binding": deepcopy(candidate_with_digest),
            "binding_digest": binding_digest,
            "turn_required": False,
            "quota_spent": False,
        }
        if execute and not changed:
            receipt["status"] = "no_change"
        return receipt, {**goal, "external_capability_bindings": merged}

    if execute:
        if not path.is_file():
            raise ValueError(f"LoopX registry does not exist: {path}")

        def reduce(registry: dict[str, Any]) -> dict[str, Any]:
            receipt, updated_goal = prepare(registry)
            if not receipt["changed"]:
                return receipt
            goals = registry.get("goals")
            if not isinstance(goals, list):
                raise ValueError("LoopX registry goals must be a list")
            registry["goals"] = [
                updated_goal
                if isinstance(item, Mapping)
                and item.get("id") == updated_goal.get("id")
                else item
                for item in goals
            ]
            receipt["written"] = True
            return receipt

        return mutate_project_registry(
            path,
            operation="bind_external_capability_to_goal",
            reducer=reduce,
        )

    if not path.is_file():
        raise ValueError(f"LoopX registry does not exist: {path}")
    receipt, _updated_goal = prepare(load_registry(path))
    return receipt


def _provider_input(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = _mapping(value, "provider input")
    if set(payload) != {"context_refs", "input"}:
        raise ValueError("provider input must contain only context_refs and input")
    context_refs = payload.get("context_refs")
    if not isinstance(context_refs, list) or not 1 <= len(context_refs) <= 32:
        raise ValueError("provider input context_refs must contain 1 to 32 objects")
    for index, item in enumerate(context_refs):
        ref = _mapping(item, f"provider input context_refs[{index}]")
        for field in ("kind", "ref", "digest"):
            _token(ref.get(field), f"provider input context_refs[{index}].{field}")
    _mapping(payload.get("input"), "provider input input")
    return payload


def validate_external_capability_result(
    value: Mapping[str, Any],
    *,
    invocation_id: str,
    operation: Mapping[str, Any],
) -> dict[str, Any]:
    result = _mapping(value, "external capability result")
    unknown = sorted(set(result) - _RESULT_FIELDS)
    if unknown:
        raise ValueError(f"external capability result has unsupported fields {unknown}")
    expected_schema = str(operation.get("result_schema") or "")
    if result.get("schema_version") != expected_schema:
        raise ValueError(
            "external capability result schema_version does not match profile"
        )
    if result.get("invocation_id") != invocation_id:
        raise ValueError(
            "external capability result invocation_id does not match request"
        )
    if result.get("status") not in {"succeeded", "no_change"}:
        raise ValueError("external capability read-only result did not succeed")
    observations = result.get("observations")
    if not isinstance(observations, list) or len(observations) > 64:
        raise ValueError("external capability result observations are invalid")
    if operation.get("effect_class") != "read_only":
        raise ValueError("external-write capability admission is not implemented")
    for field in (
        "domain_state_mutations",
        "domain_transition_receipts",
        "transition_proposals",
    ):
        if result.get(field) != []:
            raise ValueError(
                f"read-only external capability result must leave {field} empty"
            )
    if result.get("effect_receipt") is not None:
        raise ValueError(
            "read-only external capability result cannot contain an effect receipt"
        )
    validate_public_safe_value(result, path="provider_result")
    return result


def prepare_external_capability_invocation(
    *,
    state_file: str | Path,
    capability_id: str,
    operation: str,
    provider_input: Mapping[str, Any],
    goal_binding: Mapping[str, Any] | None = None,
    registry_path: str | Path | None = None,
    goal_id: str | None = None,
    authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve and freeze one Goal-bound provider request before dispatch.

    Read-only callers omit ``authority`` and retain the original deterministic
    invocation identity. Material adapters pass the typed settlement identity;
    its effect id exclusively owns the invocation identity so one governed Turn
    cannot dispatch multiple distinct external writes under one quota receipt.
    """

    binding = resolve_external_capability_binding(
        state_file=state_file,
        capability_id=capability_id,
        operation=operation,
    )
    operation_profile = _mapping(binding.get("operation"), "operation profile")
    if goal_binding is None:
        if registry_path is None or goal_id is None:
            raise ValueError(
                "external capability invocation requires goal_id and registry_path"
            )
        goal_binding = load_goal_external_capability_binding(
            registry_path=registry_path,
            goal_id=goal_id,
            capability_id=capability_id,
        )
        expected_goal_id = _token(goal_id, "goal_id")
    elif registry_path is not None or goal_id is not None:
        raise ValueError(
            "external capability invocation accepts either a durable Goal binding "
            "or an explicit Goal binding projection, not both"
        )
    else:
        expected_goal_id = None
    goal = _goal_binding(
        goal_binding,
        capability_id=capability_id,
        operation=operation,
        provider_binding=binding,
        expected_goal_id=expected_goal_id,
    )
    provided = _provider_input(provider_input)
    normalized_authority = (
        _mapping(authority, "external capability authority")
        if authority is not None
        else None
    )
    invocation_seed = (
        {
            "settlement_effect_id": _token(
                normalized_authority.get("effect_id"),
                "external capability authority effect_id",
            )
        }
        if normalized_authority is not None
        else {
            "goal_binding_digest": goal["binding_digest"],
            "capability_id": capability_id,
            "operation": operation,
            "provider_input_digest": _canonical_digest(provided),
        }
    )
    invocation_id = (
        "capability-" + _canonical_digest(invocation_seed).split(":", 1)[1][:24]
    )
    request = {
        "schema_version": str(operation_profile.get("request_schema") or ""),
        "invocation_id": invocation_id,
        "capability_id": capability_id,
        "operation": operation,
        "goal": {
            "goal_id": goal["goal_id"],
            "capability_binding_digest": goal["binding_digest"],
        },
        "provider": {
            "extension_id": binding["extension_id"],
            "revision": binding["revision"],
            "profile_digest": binding["integration_profile_digest"],
        },
        "context_refs": provided["context_refs"],
        "input": provided["input"],
    }
    if normalized_authority is not None:
        request["authority"] = normalized_authority
    return {
        "binding": binding,
        "operation_profile": operation_profile,
        "goal": goal,
        "provider_input": provided,
        "request": request,
        "invocation_id": invocation_id,
        "request_digest": _canonical_digest(request),
    }


def invoke_external_capability(
    *,
    state_file: str | Path,
    capability_id: str,
    operation: str,
    goal_binding: Mapping[str, Any] | None = None,
    registry_path: str | Path | None = None,
    goal_id: str | None = None,
    provider_input: Mapping[str, Any],
    execute: bool = False,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Preview or run one read-only provider enabled for a durable Goal."""

    prepared = prepare_external_capability_invocation(
        state_file=state_file,
        capability_id=capability_id,
        operation=operation,
        goal_binding=goal_binding,
        registry_path=registry_path,
        goal_id=goal_id,
        provider_input=provider_input,
    )
    binding = prepared["binding"]
    assert isinstance(binding, Mapping)
    operation_profile = prepared["operation_profile"]
    assert isinstance(operation_profile, Mapping)
    if operation_profile.get("effect_class") != "read_only":
        raise ValueError(
            "material external capability invocation requires a governed Turn adapter"
        )
    goal = prepared["goal"]
    assert isinstance(goal, Mapping)
    provided = prepared["provider_input"]
    assert isinstance(provided, Mapping)
    invocation_id = str(prepared["invocation_id"])
    request = prepared["request"]
    assert isinstance(request, Mapping)
    receipt: dict[str, Any] = {
        "ok": True,
        "schema_version": EXTERNAL_CAPABILITY_INVOCATION_SCHEMA_VERSION,
        "status": "ready" if not execute else "running",
        "dry_run": not execute,
        "executed": False,
        "capability_id": capability_id,
        "operation": operation,
        "effect_class": operation_profile["effect_class"],
        "provider": {
            "extension_id": binding["extension_id"],
            "revision": binding["revision"],
            "profile_digest": binding["integration_profile_digest"],
        },
        "goal_binding": {
            "goal_id": goal["goal_id"],
            "binding_digest": goal["binding_digest"],
            "turn_required": False,
        },
        "invocation_id": invocation_id,
        "request_digest": prepared["request_digest"],
        "context_ref_count": len(provided["context_refs"]),
        "effects": {
            "provider_invoked": False,
            "external_write_performed": False,
            "loopx_state_written": False,
            "quota_spent": False,
        },
    }
    if not execute:
        return receipt
    provider_result = _execute_extension_runtime_binding(
        binding,
        request=request,
        environment=environment,
    )
    result = validate_external_capability_result(
        provider_result,
        invocation_id=invocation_id,
        operation=operation_profile,
    )
    return {
        **receipt,
        "status": str(result["status"]),
        "executed": True,
        "provider_result_digest": _canonical_digest(result),
        "provider_result": result,
        "effects": {
            **receipt["effects"],
            "provider_invoked": True,
        },
    }
