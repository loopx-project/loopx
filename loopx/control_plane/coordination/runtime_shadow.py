"""Source snapshots and management adapters for bounded file-shadow evidence.

The legacy Todo and lease stores remain canonical. Bootstrap binds one complete
source snapshot; subsequent candidate mutations belong to the durable outbox.
"""

from __future__ import annotations

import json
import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .local_authority_shadow_projection import source_effect_runtime_result as effect_runtime_result
from .coordination_state_contract_generated import (
    COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA as RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
    COORDINATION_RUNTIME_SHADOW_COMMIT_REQUEST_SCHEMA as RUNTIME_SHADOW_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA as RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_INSPECT_RESULT_SCHEMA,
    COORDINATION_RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA as RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_QUALIFY_RESULT_SCHEMA,
    COORDINATION_RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA as RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA,
    COORDINATION_RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA as RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA_VERSION,
    COORDINATION_RUNTIME_SHADOW_TODO_READ_RESULT_SCHEMA,
    LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA,
    LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
    LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
)


RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION = "loopx_coordination_runtime_shadow_config_v0"
RUNTIME_SHADOW_METHOD = "coordination.runtime_shadow.commit"
RUNTIME_SHADOW_INSPECT_METHOD = "coordination.runtime_shadow.inspect"
RUNTIME_SHADOW_BOOTSTRAP_METHOD = "coordination.runtime_shadow.bootstrap"
RUNTIME_SHADOW_ROLLBACK_METHOD = "coordination.runtime_shadow.rollback"
RUNTIME_SHADOW_QUALIFY_METHOD = "coordination.runtime_shadow.qualify"
RUNTIME_SHADOW_TODO_READ_METHOD = "coordination.runtime_shadow.todo_read_candidate"
LOCAL_AUTHORITY_PROMOTION_REVIEW_METHOD = "coordination.local_authority.promotion_review"


@dataclass(frozen=True)
class CoordinationRuntimeShadowConfig:
    enabled: bool
    provider: str | None
    reason_code: str


def local_authority_shadow_summary(goal: Mapping[str, Any] | None) -> dict[str, Any]:
    """Recognize retained config without granting it a writer or capture lineage."""
    coordination = goal.get("coordination") if isinstance(goal, Mapping) else None
    if not isinstance(coordination, Mapping) or "authority_shadow" not in coordination:
        return {"enabled": False, "mode": None, "status": "disabled"}
    raw = coordination["authority_shadow"]
    valid = (isinstance(raw, Mapping) and set(raw) == {"schema_version", "mode"}
             and raw.get("schema_version") == LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA
             and raw.get("mode") == "file_one_way")
    return {"enabled": False, "mode": raw.get("mode") if isinstance(raw, Mapping) else None,
            "status": "retired" if valid else "invalid", "configured": True,
            "replacement": "coordination_runtime_shadow"}


def validate_local_authority_shadow_change(enable_file: bool, clear: bool) -> None:
    if enable_file and clear:
        raise ValueError("--local-authority-shadow-file cannot be combined with --clear-local-authority-shadow")
    if enable_file:
        raise ValueError(
            "local_authority_shadow_retired: post-commit observation is retired; "
            "clear it with --clear-local-authority-shadow; explicitly configure "
            "--coordination-runtime-shadow-file and run coordination-shadow bootstrap "
            "before transaction-bound capture. Retained observations are not migration evidence."
        )


def apply_local_authority_shadow_change(goal: dict[str, Any], enable_file: bool, clear: bool) -> None:
    validate_local_authority_shadow_change(enable_file, clear)
    if not clear:
        return
    coordination = goal.get("coordination")
    if isinstance(coordination, dict):
        coordination.pop("authority_shadow", None)
        if not coordination:
            goal.pop("coordination", None)


def coordination_shadow_summaries(
    goal: Mapping[str, Any] | None,
) -> dict[str, dict[str, object]]:
    """Report the active capture configuration and any retired observation setting."""

    return {
        "local_authority_shadow": local_authority_shadow_summary(
            goal
        ),
        "coordination_runtime_shadow": coordination_runtime_shadow_summary(goal),
    }


def validate_coordination_shadow_changes(
    local_enable_file: bool,
    local_clear: bool,
    runtime_enable_file: bool,
    runtime_clear: bool,
) -> None:
    """Reject retired activation before any registry mutation."""

    validate_local_authority_shadow_change(
        local_enable_file, local_clear
    )
    validate_coordination_runtime_shadow_change(runtime_enable_file, runtime_clear)


def apply_coordination_shadow_changes(
    goal: dict[str, Any],
    local_enable_file: bool,
    local_clear: bool,
    runtime_enable_file: bool,
    runtime_clear: bool,
) -> None:
    """Clear retired settings and configure the transaction-bound shadow independently."""

    apply_local_authority_shadow_change(
        goal, local_enable_file, local_clear
    )
    apply_coordination_runtime_shadow_change(goal, runtime_enable_file, runtime_clear)


def coordination_runtime_shadow_summary(
    goal: Mapping[str, Any] | None,
) -> dict[str, object]:
    """Project the transaction-bound shadow configuration for operators."""

    config = resolve_coordination_runtime_shadow_config(goal)
    return {
        "enabled": config.enabled,
        "provider": config.provider,
        "status": "enabled" if config.enabled else config.reason_code,
    }


def validate_coordination_runtime_shadow_change(
    enable_file: bool,
    clear: bool,
) -> None:
    if enable_file and clear:
        raise ValueError(
            "--coordination-runtime-shadow-file cannot be combined with "
            "--clear-coordination-runtime-shadow"
        )


def apply_coordination_runtime_shadow_change(
    goal: dict[str, Any],
    enable_file: bool,
    clear: bool,
) -> None:
    """Apply the explicit transaction-bound file-shadow opt-in."""

    if not enable_file and not clear:
        return
    coordination = (
        goal.get("coordination") if isinstance(goal.get("coordination"), dict) else {}
    )
    if clear:
        coordination.pop("runtime_shadow", None)
    else:
        coordination["runtime_shadow"] = {
            "enabled": True,
            "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
            "provider": "file_v0",
        }
    if coordination:
        goal["coordination"] = coordination
    else:
        goal.pop("coordination", None)


def resolve_coordination_runtime_shadow_config(
    goal: Mapping[str, Any] | None,
) -> CoordinationRuntimeShadowConfig:
    """Resolve one explicit file-shadow opt-in without changing legacy defaults."""

    if not isinstance(goal, Mapping):
        return CoordinationRuntimeShadowConfig(False, None, "goal_missing")
    coordination = goal.get("coordination")
    if not isinstance(coordination, Mapping):
        return CoordinationRuntimeShadowConfig(False, None, "configuration_absent")
    configured = coordination.get("runtime_shadow")
    if configured is None:
        return CoordinationRuntimeShadowConfig(False, None, "configuration_absent")
    if not isinstance(configured, Mapping):
        return CoordinationRuntimeShadowConfig(False, None, "configuration_invalid")
    if configured.get("enabled") is not True:
        return CoordinationRuntimeShadowConfig(False, None, "explicitly_disabled")
    if configured.get("schema_version") != RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION:
        return CoordinationRuntimeShadowConfig(False, None, "schema_mismatch")
    provider = configured.get("provider")
    if provider != "file_v0":
        return CoordinationRuntimeShadowConfig(False, None, "provider_unsupported")
    return CoordinationRuntimeShadowConfig(True, provider, "explicit_opt_in")


RuntimeInvoker = Callable[..., object]

def load_task_lease_runtime_shadow_records(
    *,
    runtime_root: Path,
    goal_id: str,
) -> list[dict[str, object]]:
    """Read complete legacy lease records for a source snapshot."""

    lease_directory = runtime_root / "goals" / goal_id / "task-leases"
    if not lease_directory.exists():
        return []
    records: list[dict[str, object]] = []
    for path in sorted(lease_directory.glob("*.json")):
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.json", path.name) is None:
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError(f"task lease is not an object: {path.name}")
        todo_id = value.get("todo_id")
        if not isinstance(todo_id, str) or not todo_id:
            raise ValueError(f"task lease omits todo_id: {path.name}")
        from .local_authority_shadow_projection import compact_lease
        records.append(compact_lease(value, goal_id=goal_id, file_stem=path.stem))
    records.sort(key=lambda item: str(item["todo_id"]))
    return records


def build_todo_runtime_shadow_projection(
    *,
    goal_id: str,
    todos: object,
    leases: object = None,
    handoff_mode: str = "hard_lease",
) -> dict[str, object]:
    """Build the complete source projection using the capture partition rules."""

    from .local_authority_shadow_projection import project_coordination_source

    return project_coordination_source({
        "kind": "snapshot", "goal_id": goal_id, "handoff_mode": handoff_mode,
        "read_model_schema": "loopx_todo_canonical_read_record_v0",
        "todos": todos, "leases": [] if leases is None else leases,
    })


def capture_todo_archive_dependencies(todos: list[dict[str, Any]], state_text: str) -> list[dict[str, Any]]:
    """Use the same bounded capture for bootstrap and subsequent writer outbox."""
    from ..todos.active_state_todo_parser import parse_todo_source
    from ..todos.contract import normalize_todo_task_class
    from ..todos.todo_summary import structured_todo_item, canonical_todo_read_record

    _, archived, _ = parse_todo_source(state_text)
    # No prose or wide diagnostics cross the selection transport budget.
    capture_fields = ("todo_id", "role", "task_class", "status", "done", "archive_state", "resume_when",
        "decision_scope", "decision_outcome", "global_gate", "blocks_agent", "bound_agent", "goal_bound",
        "successor_todo_ids", "superseded_by", "unblocks_todo_id")
    archive_facts = []
    for item in archived:
        facts = {key: item[key] for key in capture_fields if key in item}
        # Keep the compatibility read class separate from recorded authority.
        # Do not transport private prose or infer a missing role from it.
        if item.get("role") == "agent" and item.get("task_class") is None:
            facts["legacy_task_class"] = normalize_todo_task_class(None,
                text=str(item.get("text") or ""), action_kind=item.get("action_kind"))
        archive_facts.append(facts)
    capture = effect_runtime_result("todo.archive.capture_dependencies", {
        "schema_version": "todo_archive_dependency_capture_request_v2",
        "active": [{key: item[key] for key in capture_fields if key in item} for item in todos],
        "archived": archive_facts,
    })
    if not isinstance(capture, dict) or capture.get("schema_version") != "todo_archive_dependency_capture_result_v1":
        raise ValueError("invalid archived dependency capture result")
    result = list(todos)
    for selected in capture["records"]:
        item = archived[selected["index"]]
        result.append(canonical_todo_read_record(structured_todo_item(
            {**item, "task_class": selected["task_class"]}, role=selected["role"],
            source_section=item["source_section"], archive_state="archive")))
    return result


def build_runtime_shadow_source_snapshot(
    *, goal: Mapping[str, Any], runtime_root: Path, state_path: Path,
    registry_path: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    """Bind the supplied Goal and every derived fact to one registry observation."""
    from ...agent_registry import registered_agent_ids_for_goal
    from ...history import load_registry
    from ...registry import find_registry_goal
    from .authority_source_capture import authority_registry_source
    from .shadow_management import ShadowManagementError

    with authority_registry_source(registry_path) as witness:
        registry = load_registry(registry_path)
        current = find_registry_goal(registry, str(goal["id"]))
        if current is None or current != dict(goal):
            raise ShadowManagementError("source_registry_changed_retry")
        projection, snapshot = _build_runtime_shadow_source_snapshot(
            goal=current, runtime_root=runtime_root, state_path=state_path,
            registry_path=registry_path, registry=registry,
        )
        snapshot["registry_source"] = {
            **witness, "registered_agents": registered_agent_ids_for_goal(current),
        }
    return projection, snapshot


def _build_runtime_shadow_source_snapshot(
    *, goal: Mapping[str, Any], runtime_root: Path, state_path: Path,
    registry_path: Path, registry: dict[str, Any],
) -> tuple[dict[str, object], dict[str, object]]:
    """Project exactly the bytes carried by one ephemeral source precondition.

    TS takes the shared source locks and verifies every byte/inventory before
    publishing a baseline or a bounded qualification result.
    """
    from ...rollout_event_log import ROLLOUT_EVENT_SCHEMA_VERSION, rollout_event_log_path
    from ...paths import resolve_runtime_root
    from ...state_refresh import resolve_goal_state
    from ..status.active_state_projection import state_event_log_candidates
    from ..todos.active_state_todo_parser import parse_active_state_todos
    from ..todos.handoff_mode import goal_handoff_mode
    from .local_authority_shadow_projection import canonical_bytes, compact_lease
    from .shadow_management import ShadowManagementError

    goal_id = str(goal["id"])
    state_path = state_path.expanduser().resolve()
    state_bytes = state_path.read_bytes()
    state_text = state_bytes.decode("utf-8")
    evidence: list[dict[str, object]] = []

    def read_evidence(path: Path) -> bytes | None:
        path = path.expanduser().resolve()
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            data = None
        evidence.append({"path": str(path), "bytes_sha256": None if data is None else "sha256:" + hashlib.sha256(data).hexdigest()})
        return data

    rollout_bytes = read_evidence(rollout_event_log_path(runtime_root, goal_id))
    rollout_events: list[dict[str, Any]] = []
    for line in (rollout_bytes or b"").decode("utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("schema_version") == ROLLOUT_EVENT_SCHEMA_VERSION:
            rollout_events.append(value)

    # Freeze every candidate, including absent paths. Native source locks verify
    # these same bytes before publishing the baseline; never project a later read.
    from ..goals.active_state_event_projection import active_state_event_projection_fields
    from ..goals.path_resolution import resolve_goal_local_path
    from .local_authority_shadow_adapter import todo_partition_projector
    event_paths = list(dict.fromkeys(path.resolve() for path in state_event_log_candidates(dict(goal), state_path=state_path)))
    event_texts = {path: (None if (data := read_evidence(path)) is None else data.decode("utf-8")) for path in event_paths}
    event_fields = active_state_event_projection_fields(dict(goal), state_path=state_path,
        resolve_goal_local_path=resolve_goal_local_path, parse_active_state_todos=parse_active_state_todos,
        item_limit=None, rollout_events=rollout_events, event_log_texts=event_texts)
    if event_fields.get("state_event_projection_warning"):
        raise ShadowManagementError("event_source_invalid")
    todos = todo_partition_projector(goal, state_path=state_path, rollout_events=rollout_events,
        event_fields=event_fields)(state_text)["todos"]
    leases: list[dict[str, Any]] = []
    inventory: list[dict[str, object]] = []
    for path in sorted((runtime_root / "goals" / goal_id / "task-leases").glob("*.json")):
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.json", path.name) is None:
            continue
        data = path.read_bytes()
        leases.append(compact_lease(json.loads(data), goal_id=goal_id, file_stem=path.stem))
        inventory.append({"name": path.name, "bytes_sha256": "sha256:" + hashlib.sha256(data).hexdigest()})
    projection = build_todo_runtime_shadow_projection(goal_id=goal_id, todos=todos, leases=leases,
        handoff_mode=goal_handoff_mode(state_text))
    registered_root = resolve_runtime_root(registry, None, registry_path=registry_path)
    _, _, registered_state = resolve_goal_state(registry=registry, goal_id=goal_id,
        project_override=None, state_file_override=None)
    return projection, {"state_path": str(state_path), "registered_runtime_root": str(registered_root.expanduser().absolute()),
        "registered_state_path": str(registered_state.expanduser().resolve()),
        "state_bytes_sha256": "sha256:" + hashlib.sha256(state_bytes).hexdigest(),
        "lease_inventory": inventory, "projection_sha256": hashlib.sha256(canonical_bytes(projection)).hexdigest(),
        "evidence_files": evidence, "event_log_paths": [str(path) for path in event_paths]}


def dispatch_coordination_runtime_shadow(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    operation_id: str,
    event_kind: str,
    source_version: str,
    projection: Mapping[str, Any],
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Mirror a committed mutation, isolating all shadow failures from truth."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": "loopx_coordination_runtime_shadow_dispatch_v0",
            "status": "disabled",
            "reason_code": config.reason_code,
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }

    request = {
        "schema_version": RUNTIME_SHADOW_REQUEST_SCHEMA_VERSION,
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "operation_id": operation_id,
        "event_kind": event_kind,
        "source_version": source_version,
        "projection": dict(projection),
    }
    try:
        result = runtime_invoker(RUNTIME_SHADOW_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": "loopx_coordination_runtime_shadow_dispatch_v0",
            "status": "failed",
            "reason_code": "shadow_runtime_unavailable",
            "reason": str(exc),
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": "loopx_coordination_runtime_shadow_dispatch_v0",
            "status": "failed",
            "reason_code": "shadow_runtime_result_invalid",
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    return dict(result)


def bootstrap_coordination_runtime_shadow(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    operation_id: str,
    source_version: str,
    projection: Mapping[str, Any],
    source_snapshot: Mapping[str, Any] | None = None,
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Import one legacy baseline into an empty shadow without promoting it."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
            "status": "disabled",
            "reason_code": config.reason_code,
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    request = {
        "schema_version": RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA_VERSION,
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "source_snapshot": dict(source_snapshot or {}),
        "operation_id": operation_id,
        "source_version": source_version,
        "projection": dict(projection),
    }
    try:
        result = runtime_invoker(RUNTIME_SHADOW_BOOTSTRAP_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_bootstrap_runtime_unavailable",
            "reason": str(exc),
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_bootstrap_runtime_result_invalid",
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    return dict(result)


def rollback_coordination_runtime_shadow(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    operation_id: str,
    expected_provider_revision: str | None = None,
    expected_bootstrap_operation_id: str | None = None,
    projection: Mapping[str, Any] | None = None,
    source_snapshot: Mapping[str, Any] | None = None,
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Quarantine one revision-fenced pre-promotion file shadow lineage."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA,
            "status": "disabled",
            "reason_code": config.reason_code,
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    request = {
        "schema_version": RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA_VERSION,
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "source_snapshot": dict(source_snapshot or {}),
        "operation_id": operation_id,
        "expected_provider_revision": expected_provider_revision,
        "expected_bootstrap_operation_id": expected_bootstrap_operation_id,
        "projection": dict(projection or {}),
    }
    try:
        result = runtime_invoker(RUNTIME_SHADOW_ROLLBACK_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_rollback_runtime_unavailable",
            "reason": str(exc),
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_rollback_runtime_result_invalid",
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    return dict(result)


def inspect_coordination_runtime_shadow(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    projection: Mapping[str, Any],
    source_snapshot: Mapping[str, Any] | None = None,
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Read parity evidence without allowing the shadow to drive decisions."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_INSPECT_RESULT_SCHEMA,
            "status": "disabled",
            "reason_code": config.reason_code,
            "parity_matches": False,
            "bootstrap_required": False,
            "decision_read_from_shadow": False,
        }
    request = {
        "schema_version": RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA_VERSION,
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "source_snapshot": dict(source_snapshot or {}),
        "projection": dict(projection),
    }
    try:
        result = runtime_invoker(RUNTIME_SHADOW_INSPECT_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_INSPECT_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_runtime_unavailable",
            "reason": str(exc),
            "parity_matches": False,
            "bootstrap_required": False,
            "decision_read_from_shadow": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_INSPECT_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_runtime_result_invalid",
            "parity_matches": False,
            "bootstrap_required": False,
            "decision_read_from_shadow": False,
        }
    return dict(result)


def qualify_coordination_runtime_shadow(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    projection: Mapping[str, Any],
    minimum_operations: int,
    required_event_kinds: list[str],
    source_snapshot: Mapping[str, Any] | None = None,
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Qualify coverage across a shadow lineage without serving from it."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_QUALIFY_RESULT_SCHEMA,
            "status": "disabled",
            "reason_code": config.reason_code,
            "qualified": False,
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    request = {
        "schema_version": RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA_VERSION,
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "source_snapshot": dict(source_snapshot or {}),
        "projection": dict(projection),
        "minimum_operations": minimum_operations,
        "required_event_kinds": list(required_event_kinds),
    }
    try:
        result = runtime_invoker(RUNTIME_SHADOW_QUALIFY_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_QUALIFY_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_qualification_runtime_unavailable",
            "reason": str(exc),
            "qualified": False,
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_QUALIFY_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_qualification_runtime_result_invalid",
            "qualified": False,
            "primary_writeback_preserved": True,
            "decision_read_from_shadow": False,
        }
    return dict(result)


def review_local_coordination_authority_promotion(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    operation_id: str,
    projection: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    minimum_operations: int,
    required_event_kinds: list[str],
    handoff_mode_migration: str | None = None,
    registered_agents: list[str] | None = None,
    execute: bool,
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Preview or atomically apply the reviewed whole-Goal coordination-authority cutover."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
            "status": "disabled",
            "executed": False,
            "reason_code": config.reason_code,
            "legacy_writer_fenced": False,
            "legacy_fallback_used": False,
        }
    request = {
        "schema_version": LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "operation_id": operation_id,
        "projection": dict(projection),
        "source_snapshot": dict(source_snapshot),
        "minimum_operations": minimum_operations,
        "required_event_kinds": list(required_event_kinds),
        **(
            {
                "handoff_mode_migration": handoff_mode_migration,
                "registered_agents": list(registered_agents or []),
            }
            if handoff_mode_migration is not None
            else {}
        ),
        "execute": execute,
    }
    try:
        result = runtime_invoker(LOCAL_AUTHORITY_PROMOTION_REVIEW_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
            "status": "failed",
            "executed": False,
            "reason_code": "promotion_review_runtime_unavailable",
            "reason": str(exc),
            "legacy_writer_fenced": False,
            "legacy_fallback_used": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA,
            "status": "failed",
            "executed": False,
            "reason_code": "promotion_review_runtime_result_invalid",
            "legacy_writer_fenced": False,
            "legacy_fallback_used": False,
        }
    return dict(result)


def read_coordination_runtime_shadow_todo_candidate(
    *,
    goal: Mapping[str, Any] | None,
    runtime_root: Path,
    goal_id: str,
    todo_id: str,
    projection: Mapping[str, Any],
    source_snapshot: Mapping[str, Any] | None = None,
    runtime_invoker: RuntimeInvoker = effect_runtime_result,
) -> dict[str, object]:
    """Read one parity-matched file Todo as pre-promotion evidence only."""

    config = resolve_coordination_runtime_shadow_config(goal)
    if not config.enabled:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_TODO_READ_RESULT_SCHEMA,
            "status": "disabled",
            "reason_code": config.reason_code,
            "read_candidate_qualified": False,
            "decision_read_from_shadow": False,
        }
    request = {
        "schema_version": RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA_VERSION,
        "runtime_root": str(runtime_root.expanduser().absolute()),
        "goal_id": goal_id,
        "source_snapshot": dict(source_snapshot or {}),
        "todo_id": todo_id,
        "projection": dict(projection),
    }
    try:
        result = runtime_invoker(RUNTIME_SHADOW_TODO_READ_METHOD, request)
    except Exception as exc:
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_TODO_READ_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_todo_read_runtime_unavailable",
            "reason": str(exc),
            "read_candidate_qualified": False,
            "decision_read_from_shadow": False,
        }
    if not isinstance(result, Mapping):
        return {
            "schema_version": COORDINATION_RUNTIME_SHADOW_TODO_READ_RESULT_SCHEMA,
            "status": "failed",
            "reason_code": "shadow_todo_read_runtime_result_invalid",
            "read_candidate_qualified": False,
            "decision_read_from_shadow": False,
        }
    return dict(result)
