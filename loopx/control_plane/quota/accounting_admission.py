from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ...file_lock import (
    cross_runtime_lock_witness,
    exclusive_cross_runtime_file_lock,
    exclusive_file_lock,
    exclusive_mutation_file_lock,
    exclusive_run_index_lock,
)
from ..projects.registry_codec import SOURCE_SESSION_PROFILE_ID


QUOTA_SOURCE_ADMISSION_SCHEMA = "loopx_quota_source_admission_v0"
QUOTA_NATIVE_ADMISSION_SCHEMA = "loopx_quota_native_admission_v0"
_held_native_admission: ContextVar[tuple[str, dict[str, Any]] | None] = ContextVar(
    "loopx_held_native_accounting_admission", default=None)


def _borrow_native_admission(
    *, root: Path, registry: Path | None, goal_id: str, planned: dict[str, str] | None,
    agent_id: str | None, publication: bool,
) -> dict[str, Any] | None:
    held = _held_native_admission.get()
    if held is None:
        return None
    held_goal, admission = held
    sources = admission["registry_sources"]
    if (held_goal != goal_id or admission["runtime_root"] != str(root)
            or admission["planned_goal_ref"] != planned or registry is None
            or str(registry) not in {source["path"] for source in sources}
            or (agent_id is not None and admission.get("agent_id") != agent_id)):
        raise RuntimeError("Nested native accounting does not match its held resource scope.")
    if publication and not any(lock["role"] == "authority_maintenance" for lock in admission["locks"]):
        raise RuntimeError("Nested native publication requires its maintenance and receipt resources.")
    # A source launch read can borrow the already-held source subset of a
    # mirror admission. TS still verifies its complete bytes, route and owner.
    selected = sources if str(registry) == admission["registry_path"] else [
        source for source in sources if source["path"] == str(registry)]
    paths = {source["path"] for source in selected}
    return {**admission, "registry_path": str(registry),
        "registry_sources": [dict(source) for source in selected],
        "locks": [dict(lock) for lock in admission["locks"]
            if lock["role"] != "registry_source" or lock["target"] in paths]}


def _borrow_native_read_admission(
    *, runtime_root: Path, registry_path: Path | None, goal_id: str,
    goal_ref: Mapping[str, Any] | None, agent_id: str | None,
) -> dict[str, Any] | None:
    """Borrow actual exact native metadata for a read-only projection.

    Missing internal read context can use its enclosing resources. An explicit
    context still has to match, and ordinary accounting never uses this seam.
    """
    held = _held_native_admission.get()
    if held is None:
        return None
    admission = held[1]
    if admission["planned_goal_ref"] is None:
        return None
    root = runtime_root.expanduser().resolve()
    registry = (registry_path or Path(admission["registry_path"])).expanduser().resolve()
    planned = _planned_goal_ref(
        goal_ref if goal_ref is not None else admission["planned_goal_ref"], goal_id=goal_id)
    borrowed = _borrow_native_admission(
        root=root, registry=registry, goal_id=goal_id, planned=planned,
        agent_id=agent_id if agent_id is not None else admission.get("agent_id"), publication=False)
    if borrowed is None:
        return None
    return {**borrowed, "locks": [dict(lock) for lock in borrowed["locks"]
        if lock["role"] in {"run_index", "registry_source"}]}


def _require_held_native_registry_resources(
    *, runtime_root: Path, registry_path: Path, goal_id: str,
    goal_ref: Mapping[str, Any] | None, source_admission: Mapping[str, Any],
    source_registry: Path, agent_id: str | None = None,
) -> None:
    """Match offered registry/index facts to resources this caller still holds.

    Qualification and source-state checks stay with the existing owners. This
    resource verifier cannot derive a business Ref or release its caller's locks.
    """
    native_agent = source_admission.get("agent_id")
    if not isinstance(goal_ref, Mapping) or (native_agent is not None and native_agent != agent_id):
        raise RuntimeError("The native writer does not match its explicit GoalRef and Agent.")
    root = runtime_root.expanduser().resolve()
    invoked = registry_path.expanduser().resolve()
    registries = list(dict.fromkeys((source_registry.expanduser().resolve(), invoked)))
    borrowed = _borrow_native_admission(
        root=root, registry=invoked, goal_id=goal_id, planned=dict(goal_ref),
        agent_id=native_agent, publication=False)
    targets = [root / "goals" / goal_id / "runs" / "index.jsonl", *registries]
    if (borrowed is None or borrowed != dict(source_admission)
            or [witness["path"] for witness in borrowed["registry_sources"]] != [str(path) for path in registries]):
        raise RuntimeError("The native writer lost its enclosing resource scope.")
    metadata_locks = [witness for witness in borrowed["locks"]
        if witness["role"] in {"run_index", "registry_source"}]
    if [(witness["role"], witness["target"]) for witness in metadata_locks] != [
            ("run_index" if index == 0 else "registry_source", str(path))
            for index, path in enumerate(targets)]:
        raise RuntimeError("The native writer source and index resources changed.")
    for path, witness in zip(targets, metadata_locks):
        if cross_runtime_lock_witness(path) != {key: witness[key] for key in ("target", "pid", "token")}:
            raise RuntimeError("The native writer resource owner changed.")


def _planned_goal_ref(
    value: Mapping[str, Any] | None,
    *,
    goal_id: str,
) -> dict[str, str] | None:
    if value is None:
        return None
    from ..goals.source_session_registry_state import exact_goal_ref

    planned = exact_goal_ref(
        str(value.get("goal_id") or ""),
        str(value.get("goal_instance_id") or ""),
    )
    if planned["goal_id"] != goal_id:
        raise ValueError("quota GoalRef does not match goal_id")
    return planned


def _uses_source_profile(
    registry_path: Path | None,
    goal_id: str,
) -> bool:
    if registry_path is None or not registry_path.is_file():
        return False
    from ..projects.registry_codec import load_project_registry

    return load_project_registry(registry_path).get("profile_id") == SOURCE_SESSION_PROFILE_ID


@contextmanager
def _native_accounting_admission(
    *, root: Path, registry: Path, goal_id: str, planned: dict[str, str] | None,
    operation: str, publication: bool,
) -> Iterator[dict[str, Any]]:
    from ..coordination.shadow_management import shadow_maintenance_lock_target
    from ..goals.first_party_host_admission import _legacy_source_registry
    from ..projects.registry_codec import load_project_registry, project_registry_transaction
    from ...rollout_event_log import rollout_event_log_path

    # Discover only resources here. The TS owner verifies the locked source
    # route, actual registry role, exact Goal metadata, Agent and runtime.
    source = _legacy_source_registry(load_project_registry(registry), goal_id, registry)
    sources = list(dict.fromkeys([*([source] if source is not None else []), registry]))
    index = root / "goals" / goal_id / "runs" / "index.jsonl"
    maintenance = shadow_maintenance_lock_target(root, goal_id)
    receipt = rollout_event_log_path(root, goal_id)
    with exclusive_run_index_lock(index, operation=operation), ExitStack() as locks:
        registry_sources = []
        for path in sources:
            transaction = locks.enter_context(project_registry_transaction(path, operation=operation))
            registry_sources.append(transaction.source_read_witness())
        if publication:
            locks.enter_context(exclusive_mutation_file_lock(maintenance, timeout_seconds=0, operation=operation))
            locks.enter_context(exclusive_cross_runtime_file_lock(receipt, timeout_seconds=0, operation=operation))
        yield {"schema_version": QUOTA_NATIVE_ADMISSION_SCHEMA, "registry_path": str(registry),
            "planned_goal_ref": planned, "runtime_root": str(root),
            "registry_sources": registry_sources,
            "locks": [{"role": "run_index", **cross_runtime_lock_witness(index)},
                *({"role": "registry_source", **cross_runtime_lock_witness(path)} for path in sources),
                *([{"role": "authority_maintenance", **cross_runtime_lock_witness(maintenance)},
                   {"role": "heartbeat_receipt", **cross_runtime_lock_witness(receipt)}] if publication else [])]}


@contextmanager
def quota_accounting_admission(
    *,
    runtime_root: Path,
    registry_path: Path | None,
    goal_id: str,
    goal_ref: Mapping[str, Any] | None,
    operation: str,
    lock_legacy_index: bool = True,
    handoff_legacy_index: bool = False,
    native_agent_id: str | None = None,
    native_publication: bool = False,
) -> Iterator[dict[str, Any] | None]:
    """Hold quota's index/source locks until the TypeScript owner adopts them."""

    root = runtime_root.expanduser().resolve()
    registry = registry_path.expanduser().resolve() if registry_path else None
    planned = _planned_goal_ref(goal_ref, goal_id=goal_id)
    borrowed = _borrow_native_admission(root=root, registry=registry, goal_id=goal_id,
        planned=planned, agent_id=native_agent_id, publication=native_publication)
    if borrowed is not None:
        yield borrowed
        return
    source_profile = _uses_source_profile(registry, goal_id)
    if not source_profile:
        if planned is not None or native_agent_id is not None:
            if registry is None:
                raise ValueError("native quota GoalRef requires its registry")
            with _native_accounting_admission(root=root, registry=registry, goal_id=goal_id,
                    planned=planned, operation=operation, publication=native_publication) as admission:
                if native_agent_id is not None:
                    admission["agent_id"] = native_agent_id
                token = _held_native_admission.set((goal_id, admission))
                try:
                    yield admission
                finally:
                    _held_native_admission.reset(token)
            return
        if not lock_legacy_index:
            yield None
            return
        index_path = root / "goals" / goal_id / "runs" / "index.jsonl"
        lock = (
            exclusive_run_index_lock
            if handoff_legacy_index
            else exclusive_file_lock
        )
        with lock(index_path, operation=operation):
            yield None
        return
    if planned is None or registry is None:
        raise ValueError("source-session quota commit requires an exact GoalRef")

    from ..goals.first_party_host_admission import source_goal_authority
    from ..goals.source_session_registry_state import guard_path

    index_path = root / "goals" / goal_id / "runs" / "index.jsonl"
    source_target = guard_path(registry, goal_id)
    with exclusive_run_index_lock(index_path, operation=operation):
        with exclusive_cross_runtime_file_lock(
            source_target,
            operation=operation,
        ):
            yield {
                "schema_version": QUOTA_SOURCE_ADMISSION_SCHEMA,
                "profile_id": SOURCE_SESSION_PROFILE_ID,
                "registry_path": str(registry),
                "planned_goal_ref": planned,
                "authority": source_goal_authority(registry, goal_id),
                "locks": [
                    {
                        "role": "run_index",
                        **cross_runtime_lock_witness(index_path),
                    },
                    {
                        "role": "source_guard",
                        **cross_runtime_lock_witness(source_target),
                    },
                ],
            }
