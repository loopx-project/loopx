"""Codec-owned destination registry IO for the typed prepared-transfer stage.

This is the internal composition seam for a verified transfer input, not a
portable-artifact verifier or an activation API. It never invokes fresh Goal
bootstrap on populated history. The source context is retained as private review
input; the typed owner installs only a stopped, unbound destination read model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import __version__
from ..effect_runtime import effect_runtime_result
from ..projects.registry_codec import load_project_registry, project_registry_transaction


def require_goal_transfer_registry_write(
    *, registry_path: Path, before: dict[str, Any], after: dict[str, Any],
    registry_witness: dict[str, Any],
) -> None:
    """Adapt an existing codec transaction; never parse or decide fence state."""
    from ..coordination.legacy_writer_fence import legacy_coordination_writer_fence_path

    # This is an IO-only fast path. Source preparation takes the same registry
    # lock, so it cannot install a new fence between this observation and save.
    roots = {value["common_runtime_root"] for value in (before, after)
             if isinstance(value.get("common_runtime_root"), str) and Path(value["common_runtime_root"]).is_absolute()}
    goals = [goal for value in (before, after) for goal in
             (value["goals"] if isinstance(value.get("goals"), list) else [])]
    if not any(isinstance(goal, dict) and isinstance(goal.get("id"), str)
               and legacy_coordination_writer_fence_path(runtime_root=Path(root), goal_id=goal["id"]).exists()
               for root in roots for goal in goals):
        return
    if registry_witness["path"] != str(registry_path.expanduser().resolve()):
        raise ValueError("Registry transfer witness route changed")
    result = effect_runtime_result("coordination.planned_transfer.registry_write_check", {
        "before": before, "after": after, "registry_witness": registry_witness,
    })
    if result != {"allowed": True}:
        raise ValueError("Registry transfer guard did not admit this mutation")


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
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.prepare_authority",
        _registered_preparation_request(registry_path, goal_id, operation_id, expected_preview_sha256),
        timeout=300.0, retry_safe=False)
    return result


def _registered_preparation_request(
    registry_path: Path, goal_id: str, operation_id: str, expected_preview_sha256: str,
) -> dict[str, Any]:
    context = read_goal_transfer_context(registry_path=registry_path, goal_id=goal_id)
    preview = preview_registered_goal_transfer(registry_path=registry_path, goal_id=goal_id, operation_id=operation_id)
    # The native execution compares this reviewed digest again under its actual
    # maintenance/source locks; a preview read here cannot waive stale inputs.
    return {
        "runtime_root": context["source_context"]["runtime_root"], "goal_id": goal_id,
        "operation_id": operation_id, "expected_provider_revision": preview["provider_revision"],
        "expected_writer_fence_sha256": preview["original_writer_fence_sha256"],
        "registered_source_preview_sha256": expected_preview_sha256, "execute": True, **context,
    }


def create_planned_transfer_recipient(*, destination: Path) -> dict[str, Any]:
    """Keep the private key at the recipient; only its public descriptor returns."""
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.create_recipient", {
        "destination": str(destination.expanduser().absolute()), "execute": True,
    }, timeout=60.0, retry_safe=False)
    return result


def export_registered_goal_transfer(
    *, registry_path: Path, goal_id: str, operation_id: str, expected_preview_sha256: str,
    recipient: dict[str, Any], destination: Path, materials: list[tuple[Path, str]],
) -> dict[str, Any]:
    """Pin original authority and selected bytes into a recipient-bound draft.

    This internal composition still does not qualify source Host/effect drain,
    material completeness, security review or destination admission. It avoids
    exporting a raw runtime tree or treating source config as a current grant.
    """
    request = _registered_preparation_request(registry_path, goal_id, operation_id, expected_preview_sha256)
    request.update(source_package_version=__version__, protected_transport={
        "destination": str(destination.expanduser().absolute()), "recipient": recipient,
        "materials": [{"path": str(path.expanduser().absolute()), "sha256": digest} for path, digest in materials],
    })
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.prepare_authority", request,
        timeout=300.0, retry_safe=False)
    return result


def _protected_input(directory: Path, private_key: Path, expected_artifact_sha256: str) -> dict[str, Any]:
    return {"directory": str(directory.expanduser().absolute()),
        "private_key_path": str(private_key.expanduser().absolute()),
        "expected_artifact_sha256": expected_artifact_sha256, "target_package_version": __version__}


def preview_protected_goal_transfer(
    *, directory: Path, private_key: Path, expected_artifact_sha256: str,
) -> dict[str, Any]:
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.preview_protected",
        _protected_input(directory, private_key, expected_artifact_sha256), timeout=300.0, retry_safe=True)
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
    return _stage_goal_transfer("coordination.planned_transfer.stage_target", {
        "source_preparation": source_preparation, "source_context": source_context,
        "archive": str(archive.expanduser().absolute()),
    }, project, runtime_root, provider)


def stage_protected_goal_transfer(
    *, directory: Path, private_key: Path, expected_artifact_sha256: str,
    project: Path, runtime_root: Path, provider: str,
    expected_workspace_preview_sha256: str | None = None,
    material_destinations: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Consume only destination-local protected input, remaining stopped."""
    payload: dict[str, Any] = {"protected_input": _protected_input(directory, private_key, expected_artifact_sha256)}
    if expected_workspace_preview_sha256 is not None or material_destinations is not None:
        payload.update(workspace_mapping=material_destinations,
            expected_workspace_preview_sha256=expected_workspace_preview_sha256)
    return _stage_goal_transfer("coordination.planned_transfer.stage_protected", payload, project, runtime_root, provider)


def inspect_staged_goal_transfer_history(
    *, runtime_root: Path, goal_id: str, operation_id: str,
) -> dict[str, Any]:
    """Read the original settlement content; this grants no target execution.

    The historical debit index stays in its immutable carrier until the target
    accounting owner adopts it. A missing live index must not imply zero usage.
    """
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.inspect_target_history", {
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id, "operation_id": operation_id,
    }, timeout=300.0, retry_safe=True)
    return result


def preview_protected_goal_transfer_target(
    *, directory: Path, private_key: Path, expected_artifact_sha256: str,
    project: Path, runtime_root: Path, provider: str,
    material_destinations: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Review actual target material paths; no authority or execution is admitted."""
    _, target, registry, witness = _read_transfer_destination(project, runtime_root, provider)
    result: dict[str, Any] = effect_runtime_result("coordination.planned_transfer.preview_target", {
        "protected_input": _protected_input(directory, private_key, expected_artifact_sha256),
        "target": target, "destination_registry": registry, "registry_witness": witness,
        "workspace_mapping": material_destinations,
    }, timeout=300.0, retry_safe=True)
    return result


def _stage_goal_transfer(
    method: str, payload: dict[str, Any], project: Path, runtime_root: Path, provider: str,
) -> dict[str, Any]:
    registry_path, target, registry, witness = _read_transfer_destination(project, runtime_root, provider)
    result: dict[str, Any] = effect_runtime_result(method, {
        **payload, "target": target,
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


def _read_transfer_destination(
    project: Path, runtime_root: Path, provider: str,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
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
    return registry_path, target, registry, witness


if __name__ == "__main__":
    # Fixed native codec reader, with no write action or interpreter override.
    # The invoking native owner holds the maintenance/state/registry locks.
    import json
    import sys

    if len(sys.argv) != 3:
        raise ValueError("Registered Goal context reader requires a registry and Goal id")
    print(json.dumps(_read_goal_transfer_snapshot(registry_path=Path(sys.argv[1]), goal_id=sys.argv[2]), ensure_ascii=False))
