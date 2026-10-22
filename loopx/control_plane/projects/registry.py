from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_cross_runtime_file_lock
from ...control_plane.runtime.time import now_local_iso
from ...paths import (
    registered_goal_state_file,
    require_single_goal_state_route,
    resolve_runtime_root,
    select_default_runtime_root,
)
from ..runtime.document_io import atomic_write_state_text as _atomic_write_text
from ..coordination.local_authority import (
    CanonicalAuthorityRequired,
    read_canonical_todos_if_promoted,
    require_canonical_authority,
)
from ..coordination.goal_storage_initialization import initialize_goal_storage_target
from ..goals.source_session_services import (
    FreshSourceSessionRegistration,
    RecreateGoalRequest,
    SessionBindingRequest,
    commit_project_session_binding,
    commit_project_session_unbinding,
    recreate_goal_instance,
    register_fresh_source_session_project,
    resolve_source_session_project,
)
from ...repository_identity import normalize_repository_identity
from .contract import validate_project_record_bindings
from .registration_state import (
    registration_state_matches,
    registration_state_updated_at,
    render_registration_state,
)
from .registry_codec import (
    load_project_registry,
    mutate_project_registry,
    project_registry_transaction,
)

PROJECT_KINDS = ("work", "personal")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


def _identifier(value: str, *, field: str) -> str:
    compact = str(value or "").strip()
    if not _SAFE_ID.fullmatch(compact):
        raise ValueError(f"{field} must be a stable identifier")
    return compact


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _registry_records(
    registry: dict[str, Any],
    *,
    field: str,
    identity: str,
) -> list[dict[str, Any]]:
    if field not in registry:
        return []
    records = registry[field]
    if not isinstance(records, list):
        raise TypeError(f"registry {field} must be a list")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise TypeError(f"registry {field} entries must be JSON objects")
        record_id = str(record.get(identity) or "").strip()
        if not record_id:
            raise ValueError(f"registry {field} entry is missing {identity}")
        if record_id in seen:
            raise ValueError(f"duplicate {field} {identity}: {record_id}")
        seen.add(record_id)
    return records


def _project_goal_records(
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    projects = _registry_records(registry, field="projects", identity="project_id")
    for project in projects:
        validate_project_record_bindings(project, source="registry")
    return projects, _registry_records(registry, field="goals", identity="id")


def _project_id_for_goal(
    registry: dict[str, Any],
    *,
    goal_id: str,
    require_active: bool,
) -> str:
    projects, goals = _project_goal_records(registry)
    goal = next((item for item in goals if item.get("id") == goal_id), None)
    if goal is None:
        raise ValueError(f"goal_id is not registered: {goal_id}")
    if require_active and goal.get("status") != "active":
        raise ValueError(f"foreground goal is not active: {goal_id}")
    project_id = str(goal.get("project_id") or "").strip()
    if not project_id:
        raise ValueError(f"foreground goal has no project_id: {goal_id}")
    if not any(item.get("project_id") == project_id for item in projects):
        raise ValueError(f"foreground goal references an unknown project_id: {project_id}")
    return project_id


def _resolve_exact_project_binding(
    projects: list[dict[str, Any]],
    *,
    binding_field: str,
    value: str,
    source: str,
) -> dict[str, Any]:
    matches = [
        project
        for project in projects
        if value in (project.get(binding_field) or [])
    ]
    resolution = (
        "resolved"
        if len(matches) == 1
        else "ambiguous"
        if len(matches) > 1
        else "unresolved"
    )
    return {
        "ok": resolution == "resolved",
        "schema_version": "loopx_project_resolution_v0",
        "resolution": resolution,
        "source": source,
        "project_id": matches[0].get("project_id") if resolution == "resolved" else None,
        "foreground_goal_id": None,
        **({"project": matches[0]} if resolution == "resolved" else {}),
    }


def _bind_project_goal_storage(
    *,
    registry: dict[str, Any],
    registry_path: Path,
    runtime_root: Path | None,
    project_id: str,
    goal_id: str,
    goal_record: dict[str, Any],
    resolve_new_goal_storage_target: Callable[[Path], dict[str, Any]],
) -> Path:
    effective_root = resolve_runtime_root(
        registry,
        str(runtime_root) if runtime_root else None,
        registry_path=registry_path,
    )
    existing_goal = next(
        (
            item
            for item in registry.get("goals", [])
            if isinstance(item, dict) and item.get("id") == goal_id
        ),
        None,
    )
    existing_target = (
        (existing_goal.get("coordination") or {}).get("storage_target")
        if existing_goal is not None
        else None
    )
    resumable_creation = (
        isinstance(existing_target, dict)
        and existing_target.get("schema_version")
        == "loopx_new_goal_storage_target_v1"
        and isinstance(existing_goal.get("creation_operation_id"), str)
        and existing_goal["creation_operation_id"] != ""
    )
    existing_authority = None
    if not resumable_creation:
        try:
            existing_authority = read_canonical_todos_if_promoted(
                runtime_root=effective_root, goal_id=goal_id,
            )
        except CanonicalAuthorityRequired:
            # A new registration enters the explicit creation transaction.
            # An existing unmigrated Goal must use its migration owner.
            if existing_goal is not None:
                raise
    if existing_goal is None and existing_authority is not None:
        raise ValueError(
            f"goal_id is already bound to canonical authority through another registry: {goal_id}"
        )
    storage_target = (
        existing_target
        if existing_goal is not None
        else resolve_new_goal_storage_target(effective_root)
    )
    if (
        existing_goal is None
        and (storage_target or {}).get("schema_version")
        != "loopx_new_goal_storage_target_v1"
    ):
        require_canonical_authority(goal_id=goal_id, operation="goal_create")
    if storage_target is not None:
        goal_record.setdefault("coordination", {})["storage_target"] = storage_target
    goal_record["creation_operation_id"] = (
        (existing_goal or {}).get("creation_operation_id")
        or "goal-create:project-register:"
        + hashlib.sha256(f"{project_id}\0{goal_id}".encode()).hexdigest()[:24]
    )
    return effective_root


def _register_project_goal_unlocked(
    *,
    registry_path: Path,
    runtime_root: Path | None,
    project_id: str,
    project_kind: str,
    knowledge_root: Path,
    goal_id: str,
    objective: str,
    non_goals: list[str],
    acceptance: list[str],
    unknowns: list[str],
    next_effect: str,
    stop_condition: str,
    repository_bindings: list[str],
    external_locator_bindings: list[str],
    resolve_new_goal_storage_target: Callable[[Path], dict[str, Any]],
    goal_instance_profile: str | None = None,
    operation_id: str | None = None,
    registered_agents: list[str] | None = None,
    canonical_storage: str | None = None,
) -> dict[str, Any]:
    project_id = _identifier(project_id, field="project_id")
    goal_id = _identifier(goal_id, field="goal_id")
    if project_kind not in PROJECT_KINDS:
        raise ValueError(f"project_kind must be one of: {', '.join(PROJECT_KINDS)}")
    objective = str(objective or "").strip()
    if not objective:
        raise ValueError("objective is required")
    non_goals = _unique(non_goals)
    acceptance = _unique(acceptance)
    unknowns = _unique(unknowns)
    if not acceptance:
        raise ValueError("at least one acceptance criterion is required")
    next_effect = str(next_effect or "").strip()
    stop_condition = str(stop_condition or "").strip()
    if not next_effect:
        raise ValueError("next_effect is required")
    if not stop_condition:
        raise ValueError("stop_condition is required")

    knowledge_root = knowledge_root.expanduser().resolve()
    registry_path = registry_path.expanduser()
    existing_registry = load_project_registry(registry_path) if registry_path.exists() else None
    state_file = registered_goal_state_file(knowledge_root, goal_id, existing_registry)
    require_single_goal_state_route(knowledge_root, goal_id, state_file)
    updated_at = now_local_iso()
    project_record = {
        "project_id": project_id,
        "project_kind": project_kind,
        "knowledge_root": str(knowledge_root),
        "repository_bindings": _unique(
            [normalize_repository_identity(value) for value in repository_bindings]
        ),
        "external_locator_bindings": _unique(external_locator_bindings),
    }
    from ...bootstrap import build_goal_entry

    goal_record = build_goal_entry(
        project=knowledge_root,
        goal_id=goal_id,
        domain=project_id,
        role="controller",
        parent_goal_id=None,
        state_file=state_file,
        goal_doc=None,
        adapter_kind="read_only_project_map_v0",
        adapter_status="connected",
        next_probe=None,
        spawn_allowed=False,
        max_children=0,
        allowed_domains=[],
        write_scope=[],
        execution_profile=None,
    )
    if registered_agents or canonical_storage is not None:
        if goal_instance_profile != "source_session_v1":
            raise ValueError("peer and canonical storage configuration requires fresh source_session_v1 registration")
        from ..goals.source_session_registration import configure_source_creation_goal
        configure_source_creation_goal(goal_record, registered_agents or [], canonical_storage, operation_id)
    goal_record["project_id"] = project_id
    goal_record["objective"] = objective
    goal_record["brief"] = {
        "non_goals": non_goals,
        "acceptance": acceptance,
        "unknowns": unknowns,
        "next_effect": next_effect,
        "stop_condition": stop_condition,
    }
    state_text = render_registration_state(
        project_id=project_id,
        goal_id=goal_id,
        objective=objective,
        non_goals=non_goals,
        acceptance=acceptance,
        unknowns=unknowns,
        next_effect=next_effect,
        stop_condition=stop_condition,
        updated_at=updated_at,
    )
    if goal_instance_profile is not None:
        if goal_instance_profile != "source_session_v1":
            raise ValueError("goal_instance_profile is unsupported")
        operation_id = _identifier(operation_id or "", field="operation_id")
        return register_fresh_source_session_project(
            FreshSourceSessionRegistration(
                registry_path=registry_path,
                runtime_root=(runtime_root or select_default_runtime_root())
                .expanduser()
                .resolve(),
                operation_id=operation_id,
                project_id=project_id,
                goal_id=goal_id,
                objective=objective,
                non_goals=non_goals,
                acceptance=acceptance,
                unknowns=unknowns,
                next_effect=next_effect,
                stop_condition=stop_condition,
                project_record=project_record,
                goal_record=goal_record,
                state_file=state_file,
            )
        )
    if operation_id is not None:
        raise ValueError("operation_id requires goal_instance_profile")

    initial_registry = existing_registry or {
        "schema_version": "0.1",
        "registry_role": "project-local",
        "common_runtime_root": str(runtime_root or select_default_runtime_root()),
    }
    effective_root = _bind_project_goal_storage(
        registry=initial_registry,
        registry_path=registry_path,
        runtime_root=runtime_root,
        project_id=project_id,
        goal_id=goal_id,
        goal_record=goal_record,
        resolve_new_goal_storage_target=resolve_new_goal_storage_target,
    )

    changed = True
    with project_registry_transaction(
        registry_path,
        operation="project_register",
        create=lambda: {
                "schema_version": "0.1",
                "registry_role": "project-local",
                "common_runtime_root": str(runtime_root or select_default_runtime_root()),
        },
    ) as transaction:
        registry = transaction.payload_copy()
        projects, goals = _project_goal_records(registry)
        existing_project = next(
            (
                item
                for item in projects
                if isinstance(item, dict) and item.get("project_id") == project_id
            ),
            None,
        )
        existing_goal = next(
            (
                item
                for item in goals
                if isinstance(item, dict) and item.get("id") == goal_id
            ),
            None,
        )
        if existing_project is not None and existing_project != project_record:
            raise ValueError(f"project_id conflicts with existing registration: {project_id}")
        if existing_goal is not None and existing_goal != goal_record:
            raise ValueError(f"goal_id conflicts with existing registration: {goal_id}")
        if existing_project is None and existing_goal is not None:
            raise ValueError(f"goal_id exists without its ProjectRecord: {goal_id}")
        if existing_project is not None and existing_goal is None:
            raise ValueError(
                f"project_id already has its first registered goal: {project_id}"
            )
        effective_root = resolve_runtime_root(registry, str(runtime_root) if runtime_root else None, registry_path=registry_path)
        if existing_project is not None and existing_goal is not None:
            # A completed canonical operation can outlive its initial display.
            # Defer source validation to the typed creation owner's request.
            changed = False
        else:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_created = False
            try:
                if not state_file.exists():
                    state_created = True
                    _atomic_write_text(state_file, state_text)
                registry["projects"] = [*projects, project_record]
                registry["goals"] = [*goals, goal_record]
                registry["updated_at"] = updated_at
                transaction.commit(registry)
            except Exception:
                if state_created:
                    state_file.unlink(missing_ok=True)
                raise
    current_registry = load_project_registry(registry_path)
    registered_goal = next(
        item
        for item in current_registry.get("goals", [])
        if isinstance(item, dict) and item.get("id") == goal_id
    )

    def validate_registration_source(existing_state: str) -> None:
        existing_updated_at = registration_state_updated_at(existing_state)
        matching_state = (
            render_registration_state(
                project_id=project_id, goal_id=goal_id, objective=objective,
                non_goals=non_goals, acceptance=acceptance, unknowns=unknowns,
                next_effect=next_effect, stop_condition=stop_condition,
                updated_at=existing_updated_at,
            ) if existing_updated_at is not None else None
        )
        if matching_state is None or not registration_state_matches(
            existing_state, matching_state, objective=objective,
        ):
            raise ValueError(f"goal state file conflicts with registration: {state_file}")

    storage_selection = initialize_goal_storage_target(
        effective_root,
        registered_goal,
        registry_path=registry_path,
        validate_source=validate_registration_source,
    )
    if storage_selection is None or storage_selection.get("authority_initialized") is not True:
        raise RuntimeError("canonical project Goal initialization did not complete")

    state_projection = None
    if not state_file.exists():
        from ..todos.provider_projection import project_current_canonical_todos

        state_projection = project_current_canonical_todos(
            registry_path=registry_path, runtime_root=effective_root,
            goal_id=goal_id, project=knowledge_root, state_file=state_file,
        )
        changed = changed or state_projection["changed"]

    return {
        "ok": True,
        "schema_version": "loopx_project_registration_v0",
        "changed": changed,
        "registry": str(registry_path),
        "project": project_record,
        "goal": goal_record,
        "state_file": str(state_file),
        "storage_selection": storage_selection,
        **({"state_projection": state_projection} if state_projection is not None else {}),
    }


def register_project_goal(
    *,
    registry_path: Path,
    runtime_root: Path | None,
    project_id: str,
    project_kind: str,
    knowledge_root: Path,
    goal_id: str,
    objective: str,
    non_goals: list[str],
    acceptance: list[str],
    unknowns: list[str],
    next_effect: str,
    stop_condition: str,
    repository_bindings: list[str],
    external_locator_bindings: list[str],
    resolve_new_goal_storage_target: Callable[[Path], dict[str, Any]],
    goal_instance_profile: str | None = None,
    operation_id: str | None = None,
    registered_agents: list[str] | None = None,
    canonical_storage: str | None = None,
) -> dict[str, Any]:
    normalized_goal_id = _identifier(goal_id, field="goal_id")
    expanded_registry = registry_path.expanduser()
    existing_registry = (
        load_project_registry(expanded_registry) if expanded_registry.exists() else None
    )
    registry = existing_registry or {
        "schema_version": "0.1",
        "registry_role": "project-local",
        "common_runtime_root": str(runtime_root or select_default_runtime_root()),
    }
    effective_root = resolve_runtime_root(
        registry,
        str(runtime_root) if runtime_root else None,
        registry_path=expanded_registry,
    )
    digest = hashlib.sha256(normalized_goal_id.encode("utf-8")).hexdigest()[:16]
    lock_target = (
        effective_root
        / "authority-transition"
        / "project-registration"
        / f"goal-{digest}"
    )
    with exclusive_cross_runtime_file_lock(
        lock_target,
        operation="project_register",
    ):
        return _register_project_goal_unlocked(
            registry_path=expanded_registry,
            runtime_root=runtime_root,
            project_id=project_id,
            project_kind=project_kind,
            knowledge_root=knowledge_root,
            goal_id=normalized_goal_id,
            objective=objective,
            non_goals=non_goals,
            acceptance=acceptance,
            unknowns=unknowns,
            next_effect=next_effect,
            stop_condition=stop_condition,
            repository_bindings=repository_bindings,
            external_locator_bindings=external_locator_bindings,
            resolve_new_goal_storage_target=resolve_new_goal_storage_target,
            goal_instance_profile=goal_instance_profile,
            operation_id=operation_id,
            registered_agents=registered_agents,
            canonical_storage=canonical_storage,
        )


def bind_session(
    *,
    registry_path: Path,
    session_id: str,
    goal_id: str,
    goal_instance_id: str | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    goal_id = _identifier(goal_id, field="goal_id")
    registry_path = registry_path.expanduser()
    if goal_instance_id is not None or operation_id is not None:
        if goal_instance_id is None or operation_id is None:
            raise ValueError(
                "goal_instance_id and operation_id are both required for exact binding"
            )
        return commit_project_session_binding(
            SessionBindingRequest(
                registry_path=registry_path,
                session_id=session_id,
                goal_id=goal_id,
                goal_instance_id=goal_instance_id,
                operation_id=_identifier(operation_id, field="operation_id"),
            )
        )

    def reduce(registry: dict[str, Any]) -> dict[str, Any]:
        project_id = _project_id_for_goal(
            registry,
            goal_id=goal_id,
            require_active=True,
        )

        bindings = _registry_records(
            registry,
            field="session_bindings",
            identity="session_id",
        )
        if any(not str(item.get("foreground_goal_id") or "").strip() for item in bindings):
            raise ValueError(
                "registry session_bindings entry is missing foreground_goal_id"
            )
        matching = [
            item
            for item in bindings
            if isinstance(item, dict) and item.get("session_id") == session_id
        ]
        if len(matching) > 1:
            raise ValueError(f"session_id has multiple foreground bindings: {session_id}")
        requested = {
            "session_id": session_id,
            "foreground_goal_id": goal_id,
        }
        if matching == [requested]:
            return {
                "ok": True,
                "schema_version": "loopx_session_binding_v0",
                "changed": False,
                "registry": str(registry_path),
                "project_id": project_id,
                "binding": requested,
            }

        registry["session_bindings"] = [
            item
            for item in bindings
            if not (isinstance(item, dict) and item.get("session_id") == session_id)
        ] + [requested]
        registry["updated_at"] = now_local_iso()
        return {
            "ok": True,
            "schema_version": "loopx_session_binding_v0",
            "changed": True,
            "registry": str(registry_path),
            "project_id": project_id,
            "binding": requested,
        }

    return mutate_project_registry(
        registry_path,
        operation="project_bind_session",
        reducer=reduce,
    )


def unbind_session(
    *,
    registry_path: Path,
    session_id: str,
    goal_id: str,
    goal_instance_id: str | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Remove one exact session-to-goal binding without touching peer sessions."""

    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    goal_id = _identifier(goal_id, field="goal_id")
    registry_path = registry_path.expanduser()
    if goal_instance_id is not None or operation_id is not None:
        if goal_instance_id is None or operation_id is None:
            raise ValueError(
                "goal_instance_id and operation_id are both required for exact unbinding"
            )
        return commit_project_session_unbinding(
            SessionBindingRequest(
                registry_path=registry_path,
                session_id=session_id,
                goal_id=goal_id,
                goal_instance_id=goal_instance_id,
                operation_id=_identifier(operation_id, field="operation_id"),
            )
        )

    def reduce(registry: dict[str, Any]) -> dict[str, Any]:
        project_id = _project_id_for_goal(
            registry,
            goal_id=goal_id,
            require_active=False,
        )
        bindings = _registry_records(
            registry,
            field="session_bindings",
            identity="session_id",
        )
        if any(not str(item.get("foreground_goal_id") or "").strip() for item in bindings):
            raise ValueError(
                "registry session_bindings entry is missing foreground_goal_id"
            )
        matching = [item for item in bindings if item.get("session_id") == session_id]
        if not matching:
            return {
                "ok": True,
                "schema_version": "loopx_session_unbinding_v0",
                "changed": False,
                "registry": str(registry_path),
                "project_id": project_id,
                "binding": None,
            }
        binding = matching[0]
        if binding.get("foreground_goal_id") != goal_id:
            raise ValueError(
                "session binding does not match expected foreground goal: "
                f"{session_id} is bound to {binding.get('foreground_goal_id')}, "
                f"not {goal_id}"
            )

        registry["session_bindings"] = [
            item for item in bindings if item.get("session_id") != session_id
        ]
        registry["updated_at"] = now_local_iso()
        return {
            "ok": True,
            "schema_version": "loopx_session_unbinding_v0",
            "changed": True,
            "registry": str(registry_path),
            "project_id": project_id,
            "binding": binding,
        }

    return mutate_project_registry(
        registry_path,
        operation="project_unbind_session",
        reducer=reduce,
    )


def recreate_goal(
    *,
    registry_path: Path,
    goal_id: str,
    goal_instance_id: str,
    operation_id: str,
    execute: bool,
) -> dict[str, Any]:
    if not execute:
        raise ValueError("recreate-goal requires --execute")
    return recreate_goal_instance(
        RecreateGoalRequest(
            registry_path=registry_path.expanduser(),
            goal_id=_identifier(goal_id, field="goal_id"),
            goal_instance_id=str(goal_instance_id or "").strip(),
            operation_id=_identifier(operation_id, field="operation_id"),
        )
    )


def resolve_project(
    *,
    registry_path: Path,
    explicit_project_id: str | None,
    session_id: str | None,
    repository: str | None,
    external_locator: str | None,
    goal_id: str | None = None,
    goal_instance_id: str | None = None,
) -> dict[str, Any]:
    registry_path = registry_path.expanduser()
    if not registry_path.exists():
        raise FileNotFoundError(f"registry file does not exist: {registry_path}")
    registry = load_project_registry(registry_path)
    if registry.get("profile_id") == "source_session_v1":
        return resolve_source_session_project(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
            goal_instance_id=goal_instance_id,
            session_id=session_id,
        )
    projects = _registry_records(
        registry,
        field="projects",
        identity="project_id",
    )
    for project in projects:
        validate_project_record_bindings(project, source="registry")
    goals = _registry_records(registry, field="goals", identity="id")

    if explicit_project_id:
        project_id = _identifier(explicit_project_id, field="project_id")
        candidates = [
            item
            for item in projects
            if isinstance(item, dict) and item.get("project_id") == project_id
        ]
        if len(candidates) == 1:
            return {
                "ok": True,
                "schema_version": "loopx_project_resolution_v0",
                "resolution": "resolved",
                "source": "explicit_project_id",
                "project_id": project_id,
                "foreground_goal_id": None,
                "project": candidates[0],
            }
        return {
            "ok": False,
            "schema_version": "loopx_project_resolution_v0",
            "resolution": "ambiguous" if candidates else "unresolved",
            "source": "explicit_project_id",
            "project_id": None,
            "foreground_goal_id": None,
        }

    if session_id:
        compact_session_id = str(session_id).strip()
        bindings = _registry_records(
            registry,
            field="session_bindings",
            identity="session_id",
        )
        if any(not str(item.get("foreground_goal_id") or "").strip() for item in bindings):
            raise ValueError(
                "registry session_bindings entry is missing foreground_goal_id"
            )
        matches = [
            item
            for item in bindings
            if isinstance(item, dict) and item.get("session_id") == compact_session_id
        ]
        if matches:
            goal_id = str(matches[0].get("foreground_goal_id") or "")
            matching_goals = [item for item in goals if item.get("id") == goal_id]
            if len(matching_goals) != 1 or matching_goals[0].get("status") != "active":
                return {
                    "ok": False,
                    "schema_version": "loopx_project_resolution_v0",
                    "resolution": "unresolved",
                    "source": "session_binding",
                    "project_id": None,
                    "foreground_goal_id": goal_id or None,
                }
            project_id = str(matching_goals[0].get("project_id") or "")
            matching_projects = [
                item for item in projects if item.get("project_id") == project_id
            ]
            if len(matching_projects) != 1:
                return {
                    "ok": False,
                    "schema_version": "loopx_project_resolution_v0",
                    "resolution": (
                        "ambiguous" if len(matching_projects) > 1 else "unresolved"
                    ),
                    "source": "session_binding",
                    "project_id": None,
                    "foreground_goal_id": goal_id,
                }
            return {
                "ok": True,
                "schema_version": "loopx_project_resolution_v0",
                "resolution": "resolved",
                "source": "session_binding",
                "project_id": project_id,
                "foreground_goal_id": goal_id,
                "project": matching_projects[0],
            }
        if not repository and not external_locator:
            return {
                "ok": False,
                "schema_version": "loopx_project_resolution_v0",
                "resolution": "unresolved",
                "source": "session_binding",
                "project_id": None,
                "foreground_goal_id": None,
            }

    if repository:
        repository_result = _resolve_exact_project_binding(
            projects,
            binding_field="repository_bindings",
            value=normalize_repository_identity(repository),
            source="repository_binding",
        )
        if repository_result["resolution"] != "unresolved" or not external_locator:
            return repository_result
    if external_locator:
        return _resolve_exact_project_binding(
            projects,
            binding_field="external_locator_bindings",
            value=str(external_locator).strip(),
            source="external_locator_binding",
        )

    return {
        "ok": False,
        "schema_version": "loopx_project_resolution_v0",
        "resolution": "unresolved",
        "source": "repository_binding" if repository else "external_locator_binding" if external_locator else None,
        "project_id": None,
        "foreground_goal_id": None,
    }
