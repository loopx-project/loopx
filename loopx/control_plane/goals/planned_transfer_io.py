"""Codec-owned destination registry IO for the typed prepared-transfer stage.

This is the internal composition seam for a verified transfer input, not a
portable-artifact verifier or an activation API. It never invokes fresh Goal
bootstrap on populated history. The source context is retained as private review
input; the typed owner installs only a stopped, unbound destination read model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..effect_runtime import effect_runtime_result
from ..projects.registry_codec import load_project_registry, project_registry_transaction


def _read_goal_transfer_snapshot(*, registry_path: Path, goal_id: str) -> dict[str, Any]:
    """Codec/byte IO only; the native Goal owner decides supported admission."""
    from ...registry import find_registry_goal, resolve_state_file
    from ..coordination.authority_source_capture import authority_registry_source

    registry_path = registry_path.expanduser().resolve()
    with authority_registry_source(registry_path) as witness:
        registry = load_project_registry(registry_path)
        goal = find_registry_goal(registry, goal_id)
        if goal is None:
            raise ValueError("Original Goal is not registered")
        state = resolve_state_file(Path(goal["repo"]), goal["state_file"])
        if state is None:
            raise ValueError("Original Goal has no state route")
        return {"source_context": {"goal": goal, "state_text": state.read_bytes().decode("utf-8"),
            "runtime_root": registry["common_runtime_root"], "registry_path": str(registry_path)},
            "registry_witness": witness, "registry_profile": registry.get("profile_id"),
            "registry_role": registry.get("registry_role")}


def read_goal_transfer_context(*, registry_path: Path, goal_id: str) -> dict[str, Any]:
    """Read codec-validated source facts; only the native owner can pin them."""
    from ..runtime.runtime_projection_route import resolve_goal_source_runtime_route

    route = resolve_goal_source_runtime_route(registry_path=registry_path, goal_id=goal_id)
    snapshot = _read_goal_transfer_snapshot(registry_path=Path(route["source_registry"]), goal_id=goal_id)
    return {key: snapshot[key] for key in ("source_context", "registry_witness")}


def preview_registered_goal_transfer(*, registry_path: Path, goal_id: str, operation_id: str) -> dict[str, Any]:
    """Resolve the normal App/CLI source and let the native owner review it.

    Do not use a runtime override to turn a global projection into source
    authority. The exact original registration and creation receipt, current
    provider revision, context and retained leases are checked by TypeScript.
    """
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.preview_source", {
        "runtime_root": context["source_context"]["runtime_root"], "goal_id": goal_id,
        "operation_id": operation_id, **context,
    }, timeout=45.0, retry_safe=True)
    return result


def prepare_registered_goal_transfer(
    *, registry_path: Path, goal_id: str, operation_id: str, expected_preview_sha256: str,
) -> dict[str, Any]:
    """Compose one reviewed source authority preparation, without user plumbing.

    This is still pre-cutover: Host/effect/material custody and destination
    admission are not established by a source authority fence. The complete
    product coordinator must keep those qualifications separate.
    """
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    preview = preview_registered_goal_transfer(registry_path=registry_path, goal_id=goal_id, operation_id=operation_id)
    # The native execution compares this reviewed digest again under its actual
    # maintenance/source locks; a preview read here cannot waive stale inputs.
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.prepare_authority", {
        "runtime_root": context["source_context"]["runtime_root"], "goal_id": goal_id,
        "operation_id": operation_id, "expected_provider_revision": preview["provider_revision"],
        "expected_writer_fence_sha256": preview["original_writer_fence_sha256"],
        "registered_source_preview_sha256": expected_preview_sha256, "execute": True, **context,
    }, timeout=300.0, retry_safe=False)
    return result


def stage_prepared_goal_transfer(
    *,
    source_preparation: dict[str, Any],
    source_context: dict[str, Any],
    archive: Path,
    project: Path,
    runtime_root: Path,
    provider: str,
) -> dict[str, Any]:
    """Stage the original stopped Goal and read back its exact registration.

    Native staging takes maintenance before its source/registry locks. Registry
    publication follows in a separate codec CAS, avoiding a registry/maintenance
    lock inversion with canonical writers. Interruption leaves the destination
    fenced; retry the same operation. No preimage is restored over new work.
    """
    project, runtime_root = project.expanduser().absolute(), runtime_root.expanduser().absolute()
    registry_path = project / ".loopx" / "registry.json"
    # Mechanical filesystem validation precedes even the registry lock's IO.
    # Do not follow a caller's linked destination into another project.
    for path in (registry_path, runtime_root):
        if path.resolve() != path:
            raise ValueError("Planned transfer destination must use a physical normalized path")
    target = {"runtime_root": str(runtime_root), "project": str(project),
              "registry_path": str(registry_path), "provider": provider}
    with project_registry_transaction(registry_path, operation="planned_transfer_stage_read", create=dict) as transaction:
        registry = transaction.payload_copy()
        witness = transaction.source_read_witness() if registry_path.exists() else {"path": str(registry_path), "sha256": None}
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.stage_target", {
        "source_preparation": source_preparation, "source_context": source_context,
        "archive": str(archive.expanduser().absolute()), "target": target,
        "destination_registry": registry, "registry_witness": witness, "execute": True,
    }, timeout=300.0, retry_safe=False)
    # The typed Goal owner owns construction and conflict policy. The codec
    # alone owns serialization, locking, compensation and independent readback.
    proposed = result.pop("destination_registry")
    with project_registry_transaction(registry_path, operation="planned_transfer_stage_publish", create=dict) as transaction:
        if transaction.payload_copy() != registry:
            raise ValueError("Destination registry changed before publication; retry the original transfer without reverting later work")
        transaction.commit(proposed)
        if load_project_registry(registry_path) != proposed:
            raise ValueError("Planned transfer registry readback changed; recover the original operation")
        return {**result, "registry_readback_verified": True}


if __name__ == "__main__":
    # Fixed native codec reader, with no write action or interpreter override.
    # The invoking native owner holds the maintenance/state/registry locks.
    import json
    import sys

    if len(sys.argv) != 3:
        raise ValueError("Registered Goal context reader requires a registry and Goal id")
    print(json.dumps(_read_goal_transfer_snapshot(registry_path=Path(sys.argv[1]), goal_id=sys.argv[2]), ensure_ascii=False))
