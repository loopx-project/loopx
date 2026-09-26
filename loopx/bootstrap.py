from __future__ import annotations

import re
from pathlib import Path

from .registry import find_registry_goal
from .control_plane.coordination.legacy_writer_fence import legacy_todo_write_transaction, require_legacy_state_replacement_allowed
from .control_plane.coordination.runtime_shadow_writer_adapter import require_runtime_shadow_capture_prepared, begin_todo_runtime_shadow_capture, settle_todo_runtime_shadow_capture
from .control_plane.projects.registry_codec import (
    load_project_registry,
    project_registry_transaction,
    require_runtime_compatible_project_registry,
)
from typing import Any

from .control_plane.runtime.time import now_local_iso
from .control_plane.runtime.public_safety import public_safe_compact_text
from .control_plane.todos.active_state_editing import (
    TODO_SECTION_HEADINGS,
    atomic_write_state_text,
    insertion_anchor,
    section_bounds,
)
from .control_plane.todos.handoff_mode import (
    HANDOFF_MODE_LEGACY,
    goal_handoff_mode,
)
from .execution_profile import (
    build_execution_profile,
    compact_execution_profile,
    execution_profile_summary,
)
from .global_registry import sync_project_registry_to_global
from .install_contract import (
    ARCHIVE_FALLBACK_INSTALL_COMMAND,
    DEFAULT_INSTALL_REPAIR_COMMAND,
)
from .orchestration import (
    DEFAULT_ORCHESTRATION_MODE,
    MULTI_SUBAGENT_ORCHESTRATION_MODE,
)
from .paths import (
    registered_goal_state_file,
    rel_or_abs,
    require_single_goal_state_route,
    resolve_runtime_root,
)
from .control_plane.goals.active_state_metadata import markdown_blockquote, markdown_frontmatter_string
from .registry_writability import probe_registry_write_path


DEFAULT_OBJECTIVE = "Improve this project through bounded, verified goal segments."
DEFAULT_DOMAIN = "project-goal-control-plane"
DEFAULT_NEXT_ACTION = "Initial routing is owned by the connected domain adapter."


def slugify_goal_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "project-goal"


def default_goal_id(project: Path) -> str:
    return f"{slugify_goal_id(project.name)}-goal"


def derive_goal_display_name(goal_text: str | None) -> str | None:
    """Derive a public-safe display title from user-supplied goal text."""

    return public_safe_compact_text(goal_text, limit=132)


def now_iso() -> str:
    return now_local_iso()


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = load_project_registry(path)
    require_runtime_compatible_project_registry(
        payload,
        operation="bootstrap",
    )
    return payload


def resolve_project_path(project: Path, path: Path | None) -> Path | None:
    if path is None:
        return None
    path = path.expanduser()
    return path if path.is_absolute() else project / path


def render_authority_sources(project: Path, goal_doc: Path | None) -> str:
    if not goal_doc:
        return "- No explicit goal document was provided during bootstrap."
    return f"- Primary goal document: `{rel_or_abs(goal_doc, project)}`"


def repair_missing_todo_source_sections(state_text: str) -> tuple[str, list[str]]:
    """Add missing durable todo sources without replacing existing state content."""

    lines = state_text.splitlines()
    added_roles: list[str] = []
    for role in ("user", "agent"):
        if section_bounds(lines, role) is not None:
            continue
        anchor = insertion_anchor(lines, role)
        section = [f"## {TODO_SECTION_HEADINGS[role]}", ""]
        if anchor > 0 and lines[anchor - 1].strip():
            section.insert(0, "")
        lines[anchor:anchor] = section
        added_roles.append(role)
    if not added_roles:
        return state_text, []
    trailing_newline = "\n" if state_text.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline, added_roles


def render_state_markdown(
    *,
    project: Path,
    goal_id: str,
    adapter_kind: str,
    objective: str,
    updated_at: str,
    goal_doc: Path | None,
    execution_profile: dict[str, Any] | None,
    handoff_mode: str = HANDOFF_MODE_LEGACY,
) -> str:
    safe_objective = markdown_frontmatter_string(objective)
    profile_summary = execution_profile_summary(execution_profile)
    next_action = DEFAULT_NEXT_ACTION
    # ``handoff_mode`` travels in the state front matter (RFC shared-goal
    # authority, Appendix B). Legacy is the absent default and is never
    # materialized, so an untouched goal keeps its byte-for-byte shape.
    handoff_mode_line = (
        f"handoff_mode: {handoff_mode}\n" if handoff_mode != HANDOFF_MODE_LEGACY else ""
    )
    state_text = f"""---
status: active
owner_mode: goal
objective: {safe_objective}
updated_at: {updated_at}
adapter_id: {goal_id}
{handoff_mode_line}---

# Active Goal State

## Objective

{markdown_blockquote(objective)}

## Authority Sources

{render_authority_sources(project, goal_doc)}

## Operating Contract

- Treat this file as the durable goal state for future agent ticks.
- Treat the authority sources above as the first context to inspect before acting.
- Read current project evidence before choosing the next action.
- Run a bounded progress segment when useful; it does not have to be one tiny step.
- Keep private evidence, credentials, local paths, and raw logs out of public commits.
- End each tick with changed files, validation, residual risk, and the next action.

## Execution Profile

- `{profile_summary}`
- Repeated small-scale follow-through should expand the next delivery batch or report a blocker before spending quota.

## Non-Goals

- Do not perform irreversible production operations without explicit approval.
- Do not publish private project evidence.
- Do not optimize for activity if no useful artifact or decision can be produced.

## User Todo / Owner Review Reading Queue

## Agent Todo

## Next Action

- {next_action}

## Recent User Feedback

- Initialized by `loopx bootstrap`.

## Progress Ledger

- Created the initial goal state and registry connection.
"""
    return state_text


def relative_state_file(project: Path, state_file: Path) -> str:
    return rel_or_abs(state_file, project)


def build_goal_entry(
    *,
    project: Path,
    goal_id: str,
    domain: str,
    role: str,
    parent_goal_id: str | None,
    state_file: Path,
    goal_doc: Path | None,
    adapter_kind: str,
    adapter_status: str,
    next_probe: str | None,
    spawn_allowed: bool,
    max_children: int,
    allowed_domains: list[str],
    write_scope: list[str],
    execution_profile: dict[str, Any] | None,
    display_name: str | None = None,
) -> dict[str, Any]:
    authority_sources = []
    if goal_doc:
        authority_sources.append(
            {
                "kind": "goal_doc",
                "path": rel_or_abs(goal_doc, project),
                "role": "primary_goal_document",
            }
        )
    adapter = {
        "kind": adapter_kind,
        "status": adapter_status,
    }
    return {
        "id": goal_id,
        **({"display_name": display_name} if display_name else {}),
        "domain": domain,
        "status": "active",
        "role": role,
        "parent_goal_id": parent_goal_id,
        "repo": str(project),
        "state_file": relative_state_file(project, state_file),
        "authority_sources": authority_sources,
        "adapter": adapter,
        "spawn_policy": {
            "mode": (
                MULTI_SUBAGENT_ORCHESTRATION_MODE
                if spawn_allowed and max(0, max_children) > 0
                else DEFAULT_ORCHESTRATION_MODE
            ),
            "allowed": spawn_allowed,
            "max_children": max(0, max_children),
            "allowed_domains": allowed_domains,
        },
        "coordination": {
            "write_scope": write_scope,
            "requires_parent_approval": [
                "write",
                "publish",
                "production-action",
            ],
        },
        "execution_profile": compact_execution_profile(execution_profile),
        "next_probe": next_probe
        or f"loopx --registry .loopx/registry.json check --scan-root {project}",
        "guards": [
            "read-only by default",
            "do not mutate production systems without explicit user approval",
            "keep private evidence out of public commits",
        ],
    }


def merge_goal(registry: dict[str, Any], goal_entry: dict[str, Any], *, force: bool) -> tuple[dict[str, Any], str]:
    goals = registry.get("goals")
    if not isinstance(goals, list):
        goals = []
    merged: list[Any] = []
    action = "appended"
    replaced = False
    for item in goals:
        if isinstance(item, dict) and item.get("id") == goal_entry["id"]:
            if force:
                merged.append(goal_entry)
                action = "replaced"
            else:
                merged.append(item)
                action = "kept-existing"
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(goal_entry)
    registry["goals"] = merged
    return registry, action


def bootstrap_project(
    *,
    project: Path,
    registry_path: Path,
    runtime_root: Path | None,
    goal_id: str | None,
    objective: str,
    domain: str,
    role: str,
    parent_goal_id: str | None,
    state_file: Path | None,
    goal_doc: Path | None,
    adapter_kind: str,
    adapter_status: str,
    next_probe: str | None,
    spawn_allowed: bool,
    max_children: int,
    allowed_domains: list[str] | None,
    write_scope: list[str] | None,
    execution_minimum_scale: str | None = None,
    execution_must_include: list[str] | None = None,
    execution_small_streak_threshold: int | None = None,
    execution_outcome_markers: list[str] | None = None,
    execution_surface_only_hints: list[str] | None = None,
    execution_surface_streak_threshold: int | None = None,
    execution_outcome_must_advance: list[str] | None = None,
    execution_turn_granularity: str | None = None,
    preserve_todos: bool = False,
    display_name: str | None = None,
    force: bool,
    dry_run: bool,
    sync_global: bool,
    allow_global_route_replacement: bool = False,
) -> dict[str, Any]:
    project = project.expanduser().resolve()
    registry_path = registry_path.expanduser()
    if not registry_path.is_absolute():
        registry_path = project / registry_path
    goal_id = goal_id or default_goal_id(project)
    explicit_state_file = state_file is not None
    state_file = state_file or registered_goal_state_file(
        project, goal_id, read_json_if_exists(registry_path)
    )
    if not explicit_state_file:
        require_single_goal_state_route(project, goal_id, state_file)
    state_file = state_file.expanduser()
    if not state_file.is_absolute():
        state_file = project / state_file
    goal_doc = resolve_project_path(project, goal_doc)
    runtime_root = resolve_runtime_root(read_json_if_exists(registry_path), str(runtime_root) if runtime_root else None, registry_path=registry_path)
    updated_at = now_iso()
    execution_profile = build_execution_profile(
        minimum_scale=execution_minimum_scale,
        must_include=execution_must_include,
        small_scale_streak_threshold=execution_small_streak_threshold,
        outcome_markers=execution_outcome_markers,
        surface_only_hints=execution_surface_only_hints,
        surface_streak_threshold=execution_surface_streak_threshold,
        outcome_must_advance=execution_outcome_must_advance,
        turn_granularity=execution_turn_granularity,
    )

    registry = read_json_if_exists(registry_path)
    registry.setdefault("schema_version", "0.1")
    registry["updated_at"] = updated_at.split("T")[0]
    registry.setdefault("common_runtime_root", str(runtime_root))
    if runtime_root:
        registry["common_runtime_root"] = str(runtime_root)

    goal_entry = build_goal_entry(
        project=project,
        goal_id=goal_id,
        domain=domain,
        role=role,
        parent_goal_id=parent_goal_id,
        state_file=state_file,
        goal_doc=goal_doc,
        adapter_kind=adapter_kind,
        adapter_status=adapter_status,
        next_probe=next_probe,
        spawn_allowed=spawn_allowed,
        max_children=max_children,
        allowed_domains=allowed_domains or [],
        write_scope=write_scope or [],
        execution_profile=execution_profile,
        display_name=display_name,
    )
    registry, registry_goal_action = merge_goal(registry, goal_entry, force=force)

    state_exists = state_file.exists()
    state_action = "created"
    if state_exists and force and preserve_todos:
        state_action = "kept-existing-preserve-todos"
    elif state_exists and not force:
        state_action = "kept-existing"
    elif state_exists and force:
        state_action = "replaced"

    repaired_state_text: str | None = None
    repaired_todo_source_roles: list[str] = []
    if state_exists and state_action in {
        "kept-existing",
        "kept-existing-preserve-todos",
    }:
        repaired_state_text, repaired_todo_source_roles = repair_missing_todo_source_sections(
            state_file.read_text(encoding="utf-8")
        )
    todo_source_migration = (
        {
            "schema_version": "todo_source_section_migration_v0",
            "added_roles": repaired_todo_source_roles,
            "applied": False,
        }
        if repaired_todo_source_roles
        else None
    )

    dry_state_actions = {
        "created": "would-create",
        "kept-existing": "would-keep-existing",
        "kept-existing-preserve-todos": "would-keep-existing-preserve-todos",
        "replaced": "would-replace",
    }
    force_bootstrap_warning = None
    declared_handoff_mode = HANDOFF_MODE_LEGACY
    if state_exists and force:
        # A forced rebuild replaces todos, never the goal's handoff contract:
        # the declared mode is carried into the rewritten front matter, and an
        # invalid declaration fails closed before anything is rewritten.
        declared_handoff_mode = goal_handoff_mode(state_file.read_text(encoding="utf-8"))
        force_bootstrap_warning = {
            "kind": "force_reconnect_existing_active_state",
            "state_file": str(state_file),
            "state_action": state_action,
            "will_replace_active_state": state_action == "replaced",
            "handoff_mode": declared_handoff_mode,
            "preserve_todos_requested": bool(preserve_todos),
            "recommended_scope_migration": (
                "Use configure-goal --write-scope ... --execute to change write scope "
                "without rebuilding the active state."
            ),
            "preserve_todos_option": "--preserve-todos",
        }
    actions = [
        {"path": str(registry_path), "action": "would-write" if dry_run else "wrote", "goal": registry_goal_action},
        {
            "path": str(state_file),
            "action": dry_state_actions.get(state_action, "would-write") if dry_run else state_action,
            **({"todo_source_migration": todo_source_migration} if todo_source_migration else {}),
        },
    ]
    if sync_global:
        actions.append(
            {
                "path": str(runtime_root / "registry.global.json"),
                "action": "would-sync" if dry_run else "synced",
                "goal": goal_id,
            }
        )

    global_sync: dict[str, Any] | None = None
    global_writability: dict[str, Any] | None = None
    if sync_global and not dry_run:
        global_writability = probe_registry_write_path(runtime_root / "registry.global.json", create_parent=True)
        if not global_writability.get("ok"):
            for action in actions:
                if action.get("path") == str(runtime_root / "registry.global.json"):
                    action["action"] = "blocked-write-denied"
            global_sync = {
                "ok": False,
                "enabled": True,
                "dry_run": dry_run,
                "global_registry": str(runtime_root / "registry.global.json"),
                "synced_goal_ids": [],
                "wrote": False,
                "write_denied": True,
                "error_kind": "global_registry_write_denied",
                "global_registry_writability": global_writability,
                "requires_global_registry_repair": True,
                "requires_host_permission": bool(global_writability.get("requires_host_permission")),
                "recommended_action": global_writability.get("recommended_action"),
            }
            return {
                "ok": False,
                "dry_run": dry_run,
                "project": str(project),
                "goal_id": goal_id,
                "registry": str(registry_path),
                "state_file": str(state_file),
                "goal_doc": str(goal_doc) if goal_doc else None,
                "goal_doc_exists": bool(goal_doc and goal_doc.exists()),
                "runtime_root": str(runtime_root),
                "registry_goal_action": registry_goal_action,
                "state_action": state_action,
                "todo_source_migration": todo_source_migration,
                "force_bootstrap_warning": force_bootstrap_warning,
                "execution_profile": execution_profile,
                "global_sync": global_sync,
                "actions": actions,
                "next_commands": [
                    "Fix global registry write access, then rerun this command.",
                    "Use --no-global-sync only for an explicit local-only setup.",
                ],
                "install_repair_command": DEFAULT_INSTALL_REPAIR_COMMAND,
                "archive_fallback_install_command": ARCHIVE_FALLBACK_INSTALL_COMMAND,
                "install_repair_note": (
                    "If this local LoopX install is missing or stale, repair the PyPI distribution "
                    "and packaged workflow skills, then confirm with loopx doctor before continuing."
                ),
                "private_boundary_note": "Add .loopx/ to the project .gitignore if the goal state contains private evidence; keep .codex/goals/ ignored while legacy state remains.",
                "error": str(global_writability.get("error") or "global registry is not writable"),
            }
    shadow_capture = None
    shadow_evidence: dict[str, Any] = {}
    if not dry_run:
        with project_registry_transaction(
            registry_path,
            operation="bootstrap_registry",
            create=dict,
        ) as registry_transaction, legacy_todo_write_transaction(
            registry_path, goal_id, state_file, None, "bootstrap_state", False,
            runtime_root=runtime_root,
        ):
            current_registry = registry_transaction.payload_copy()
            current_goal = find_registry_goal(current_registry, goal_id)
            previous_root = resolve_runtime_root(current_registry, None, registry_path=registry_path)
            if previous_root != runtime_root:
                for previous_goal in current_registry.get("goals", []):
                    if isinstance(previous_goal, dict) and previous_goal.get("id"):
                        require_legacy_state_replacement_allowed(runtime_root=previous_root,
                            goal_id=str(previous_goal["id"]), goal=previous_goal)
            original = state_file.read_text(encoding="utf-8") if state_file.exists() else ""
            if force or not state_file.exists():
                require_legacy_state_replacement_allowed(runtime_root=runtime_root,
                    goal_id=goal_id, goal=current_goal)
            state_action = ("kept-existing-preserve-todos" if force and preserve_todos else "kept-existing") if state_file.exists() and (not force or preserve_todos) else "replaced" if state_file.exists() else "created"
            planned = original
            if state_action in {"created", "replaced"}:
                declared_handoff_mode = goal_handoff_mode(original) if original and force else HANDOFF_MODE_LEGACY
                planned = render_state_markdown(
                    project=project,
                    goal_id=goal_id,
                    adapter_kind=adapter_kind,
                    objective=objective,
                    updated_at=updated_at,
                    goal_doc=goal_doc,
                    execution_profile=execution_profile,
                    handoff_mode=declared_handoff_mode,
                )
            else:
                planned, repaired_todo_source_roles = repair_missing_todo_source_sections(original)
            if planned != original:
                shadow_capture = begin_todo_runtime_shadow_capture(registry_path=registry_path,
                    runtime_root=runtime_root, goal_id=goal_id, state_path=state_file,
                    write_class="bootstrap_state", original_text=original)
                shadow_capture.prepare(planned)
                require_runtime_shadow_capture_prepared(shadow_capture, runtime_root=runtime_root, goal_id=goal_id)
                atomic_write_state_text(state_file, planned)
                shadow_capture.committed()
                if todo_source_migration is not None:
                    todo_source_migration["applied"] = True
            current_registry.setdefault("schema_version", "0.1")
            current_registry["updated_at"] = updated_at.split("T")[0]
            current_registry["common_runtime_root"] = str(runtime_root)
            registry, registry_goal_action = merge_goal(current_registry, goal_entry, force=force)
            registry_transaction.commit(registry)
        if shadow_capture is not None:
            shadow_evidence = settle_todo_runtime_shadow_capture({}, registry_path=registry_path,
                runtime_root=runtime_root, goal_id=goal_id, capture=shadow_capture, emit_disabled=False)
        if sync_global:
            global_sync = sync_project_registry_to_global(
                registry_path=registry_path,
                runtime_root_override=str(runtime_root),
                goal_id=goal_id,
                dry_run=False,
                allow_route_replacement=allow_global_route_replacement,
            )

    return {
        **shadow_evidence,
        "ok": True,
        "dry_run": dry_run,
        "project": str(project),
        "goal_id": goal_id,
        "registry": str(registry_path),
        "state_file": str(state_file),
        "goal_doc": str(goal_doc) if goal_doc else None,
        "goal_doc_exists": bool(goal_doc and goal_doc.exists()),
        "runtime_root": str(runtime_root),
        "registry_goal_action": registry_goal_action,
        "state_action": state_action,
        "todo_source_migration": todo_source_migration,
        "force_bootstrap_warning": force_bootstrap_warning,
        "execution_profile": execution_profile,
        "global_sync": global_sync
        or {
            "enabled": sync_global,
            "dry_run": dry_run,
            "global_registry": str(runtime_root / "registry.global.json"),
            "synced_goal_ids": [goal_id] if sync_global else [],
            "wrote": False,
            "route_replacement_allowed": allow_global_route_replacement,
        },
        "actions": actions,
        "next_commands": [
            f"loopx --registry {relative_state_file(project, registry_path)} registry",
            f"loopx --registry {relative_state_file(project, registry_path)} status",
            f"loopx --registry {relative_state_file(project, registry_path)} check --scan-root {project}",
            f"loopx --format json --registry {runtime_root / 'registry.global.json'} quota should-run --goal-id {goal_id} --runtime-profile generic_cli",
            f"loopx --registry {relative_state_file(project, registry_path)} refresh-state --goal-id {goal_id}",
            f"loopx --registry {runtime_root / 'registry.global.json'} status",
            f"loopx --registry {relative_state_file(project, registry_path)} history --goal-id {goal_id}",
        ],
        "install_repair_command": DEFAULT_INSTALL_REPAIR_COMMAND,
        "archive_fallback_install_command": ARCHIVE_FALLBACK_INSTALL_COMMAND,
        "install_repair_note": (
            "If this local LoopX install is missing or stale, repair the PyPI distribution "
            "and packaged workflow skills, then confirm with loopx doctor before continuing."
        ),
        "private_boundary_note": "Add .loopx/ to the project .gitignore if the goal state contains private evidence; keep .codex/goals/ ignored while legacy state remains.",
    }


def render_bootstrap_markdown(payload: dict[str, Any]) -> str:
    execution_profile = (
        payload.get("execution_profile")
        if isinstance(payload.get("execution_profile"), dict)
        else None
    )
    execution_profile_text = execution_profile_summary(execution_profile)
    lines = [
        "# LoopX Bootstrap",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- project: `{payload.get('project')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- state_file: `{payload.get('state_file')}`",
        f"- goal_doc: `{payload.get('goal_doc')}`",
        f"- goal_doc_exists: `{payload.get('goal_doc_exists')}`",
        f"- runtime_root: `{payload.get('runtime_root')}`",
        f"- registry_goal_action: `{payload.get('registry_goal_action')}`",
        f"- state_action: `{payload.get('state_action')}`",
        f"- execution_profile: `{execution_profile_text}`",
        f"- global_sync: `{(payload.get('global_sync') or {}).get('wrote')}`",
        "",
        "## Actions",
    ]
    for action in payload.get("actions") or []:
        lines.append(f"- `{action.get('path')}`: {action.get('action')} ({action.get('goal', '')})")

    force_warning = payload.get("force_bootstrap_warning")
    if isinstance(force_warning, dict):
        lines.extend(
            [
                "",
                "## Force Bootstrap Warning",
                f"- state_file: `{force_warning.get('state_file')}`",
                f"- will_replace_active_state: `{force_warning.get('will_replace_active_state')}`",
                f"- preserve_todos_requested: `{force_warning.get('preserve_todos_requested')}`",
                f"- recommended_scope_migration: {force_warning.get('recommended_scope_migration')}",
                f"- preserve_todos_option: `{force_warning.get('preserve_todos_option')}`",
            ]
        )

    lines.extend(["", "## Next Commands"])
    for command in payload.get("next_commands") or []:
        lines.append(f"- `{command}`")

    if payload.get("install_repair_command"):
        lines.extend(
            [
                "",
                "## Install / Update Hint",
                str(payload.get("install_repair_note") or ""),
                "```bash",
                str(payload.get("install_repair_command")),
                "```",
            ]
        )
    if payload.get("archive_fallback_install_command"):
        lines.extend(
            [
                "",
                "### Archive Fallback",
                "Use this only when an appropriate Python package environment is unavailable.",
                "```bash",
                str(payload.get("archive_fallback_install_command")),
                "```",
            ]
        )

    if payload.get("private_boundary_note"):
        lines.extend(["", "## Boundary Note", str(payload.get("private_boundary_note"))])
    return "\n".join(lines)
