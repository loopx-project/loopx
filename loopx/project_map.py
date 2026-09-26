from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .authority import compact_authority_registry
from .feedback import validate_local_control_text, validate_public_safe_text
from .history import load_registry
from .paths import DEFAULT_PROJECT_GOALS, LEGACY_PROJECT_GOALS, rel_or_abs, resolve_runtime_root
from .state_refresh import (
    derive_recommended_action,
    extract_section_lines,
    now_local,
    parse_frontmatter,
    resolve_goal_state,
    run_file_stem,
)
from .runtime import validate_goal_id_path_segment
from .control_plane.runtime.shared_runtime_material_projection import (
    finalize_material_projection,
    prepare_material_projection_route,
)


DEFAULT_PROJECT_MAP_CLASSIFICATION = "read_only_project_map"
READ_ONLY_MAP_ADAPTER_KIND = "read_only_project_map_v0"
READ_ONLY_MAP_ADAPTER_STATUSES = {
    "connected",
    "connected-read-only",
    "read-only-map-ready",
}
READ_ONLY_MAP_DRY_RUN_PREVIEW_STATUSES = {
    "planned",
}
READ_ONLY_MAP_OPT_IN_GATE = "read_only_map_opt_in"
PROJECT_INVENTORY_PATHS = (
    "README.md",
    "AGENTS.md",
    ".loopx/registry.json",
    ".loopx/goals",
    "docs",
    "tests",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
)
STATE_SECTIONS = (
    "Authority Sources",
    "Operating Contract",
    "Work Clusters",
    "Validation Surfaces",
    "Private/Public Boundary",
    "Next Action",
    "Progress Ledger",
)


def adapter_supports_read_only_map(adapter_kind: str | None) -> bool:
    if not adapter_kind:
        return False
    return adapter_kind == READ_ONLY_MAP_ADAPTER_KIND or adapter_kind.endswith("_read_only_map_v0")


def compact_authority_sources(goal: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not goal or not isinstance(goal.get("authority_sources"), list):
        return []
    sources: list[dict[str, Any]] = []
    for source in goal.get("authority_sources") or []:
        if not isinstance(source, dict):
            continue
        sources.append(
            {
                "kind": str(source.get("kind") or "unknown"),
                "role": str(source.get("role") or "unknown"),
                "declares_path": bool(source.get("path")),
                "declares_url": bool(source.get("url")),
            }
        )
    return sources


def file_kind(path: Path) -> str:
    return "dir" if path.is_dir() else "file" if path.is_file() else "missing"


def collect_project_inventory(project: Path | None, *, goal_id: str | None = None, state_file: Path | None = None) -> dict[str, Any]:
    if project is None:
        return {
            "repo_exists": False,
            "files_present": 0,
            "files_checked": len(PROJECT_INVENTORY_PATHS),
            "checks": [],
        }
    selected_goal_root = DEFAULT_PROJECT_GOALS
    if state_file is not None:
        try:
            relative_state = state_file.relative_to(project)
        except ValueError:
            relative_state = None
        if relative_state is not None and relative_state.parts[:2] == LEGACY_PROJECT_GOALS.parts:
            selected_goal_root = LEGACY_PROJECT_GOALS
    checks = []
    for rel_path in PROJECT_INVENTORY_PATHS:
        if rel_path == str(DEFAULT_PROJECT_GOALS):
            rel_path = str(selected_goal_root)
        path = project / rel_path
        checks.append(
            {
                "path": rel_path,
                "exists": path.exists(),
                "kind": file_kind(path),
                "role": "project_surface",
            }
        )
    if goal_id:
        goal_state_dir = project / selected_goal_root / goal_id
        checks.append(
            {
                "path": str(selected_goal_root / goal_id),
                "exists": goal_state_dir.exists(),
                "kind": file_kind(goal_state_dir),
                "role": "goal_state_dir",
                "goal_id": goal_id,
            }
        )
    if state_file:
        checks.append(
            {
                "path": rel_or_abs(state_file, project),
                "exists": state_file.exists(),
                "kind": file_kind(state_file),
                "role": "active_state_file",
                "goal_id": goal_id,
            }
        )
    registry_check = next(
        (item for item in checks if item.get("path") == ".loopx/registry.json"),
        None,
    )
    goal_dir_check = next(
        (item for item in checks if item.get("role") == "goal_state_dir"),
        None,
    )
    active_state_check = next(
        (item for item in checks if item.get("role") == "active_state_file"),
        None,
    )
    return {
        "repo_exists": project.exists(),
        "files_present": sum(1 for item in checks if item["exists"]),
        "files_checked": len(checks),
        "project_registry_exists": bool(registry_check and registry_check.get("exists")),
        "goal_state_dir_exists": bool(goal_dir_check and goal_dir_check.get("exists")) if goal_id else None,
        "active_state_file_exists": bool(active_state_check and active_state_check.get("exists"))
        if state_file
        else None,
        "checks": checks,
    }


def collect_state_sections(state_text: str) -> dict[str, Any]:
    sections = {}
    for heading in STATE_SECTIONS:
        lines = extract_section_lines(state_text, heading, limit=8)
        sections[heading] = {
            "present": bool(lines),
            "line_count": len(lines),
            "sample": lines,
        }
    return {
        "sections_found": sum(1 for item in sections.values() if item["present"]),
        "sections_checked": len(sections),
        "sections": sections,
    }


def build_project_map_record(
    *,
    goal_id: str,
    project: Path | None,
    state_file: Path,
    state_text: str,
    classification: str,
    recommended_action: str,
    generated_at: str,
    registry_goal: dict[str, Any],
) -> dict[str, Any]:
    adapter = registry_goal.get("adapter") if isinstance(registry_goal.get("adapter"), dict) else {}
    authority_sources = compact_authority_sources(registry_goal)
    authority_source_count = len(authority_sources)
    if not authority_sources and isinstance(registry_goal.get("authority_source_count"), int):
        authority_source_count = int(registry_goal.get("authority_source_count") or 0)
    authority_registry = compact_authority_registry(registry_goal, project=project)
    inventory = collect_project_inventory(project, goal_id=goal_id, state_file=state_file)
    state_sections = collect_state_sections(state_text)
    state_exists = state_file.exists()
    health_check = (
        f"repo {1 if inventory.get('repo_exists') else 0}/1; "
        f"state_file {1 if state_exists else 0}/1; "
        f"project_registry {1 if inventory.get('project_registry_exists') else 0}/1; "
        f"goal_state_dir {1 if inventory.get('goal_state_dir_exists') else 0}/1; "
        f"sections {state_sections['sections_found']}/{state_sections['sections_checked']}; "
        f"files {inventory['files_present']}/{inventory['files_checked']}; "
        f"authority_registry {1 if authority_registry.get('declared') else 0}/1"
    )
    return {
        "generated_at": generated_at,
        "goal_id": goal_id,
        "classification": classification,
        "recommended_action": recommended_action,
        "health_check": health_check,
        "state": {
            "path": str(state_file),
            "frontmatter": parse_frontmatter(state_text),
        },
        "registry_goal": {
            "present": True,
            "domain": registry_goal.get("domain"),
            "status": registry_goal.get("status"),
            "adapter": {
                "kind": adapter.get("kind"),
                "status": adapter.get("status"),
            },
            "authority_source_count": authority_source_count,
            "authority_registry": authority_registry,
            "guard_count": len(registry_goal.get("guards") or []),
            "next_probe_declared": bool(registry_goal.get("next_probe")),
        },
        "authority_sources": authority_sources,
        "authority_registry": authority_registry,
        "state_map": state_sections,
        "project_inventory": inventory,
    }


def derive_residual_risks(record: dict[str, Any], *, opt_in_required: bool) -> list[str]:
    risks: list[str] = []
    registry_goal = record.get("registry_goal") if isinstance(record.get("registry_goal"), dict) else {}
    authority_registry = (
        registry_goal.get("authority_registry") if isinstance(registry_goal.get("authority_registry"), dict) else {}
    )
    state_map = record.get("state_map") if isinstance(record.get("state_map"), dict) else {}
    inventory = record.get("project_inventory") if isinstance(record.get("project_inventory"), dict) else {}

    if opt_in_required:
        risks.append("planned_adapter_requires_controller_opt_in")
    if not registry_goal.get("authority_source_count"):
        risks.append("authority_sources_not_declared")
    if authority_registry.get("required") and not authority_registry.get("declared"):
        risks.append("authority_registry_required_but_not_declared")
    if authority_registry.get("declared"):
        if authority_registry.get("path") and authority_registry.get("path_exists") is False:
            risks.append("authority_registry_path_missing")
        default_count = int(authority_registry.get("default_entry_count") or 0)
        checked_count = int(authority_registry.get("default_entries_checked") or 0)
        present_count = int(authority_registry.get("default_entries_present") or 0)
        if default_count and checked_count and present_count < default_count:
            risks.append("authority_registry_default_entries_missing")
        if int(authority_registry.get("deprecated_source_count") or 0) > 0:
            risks.append("authority_registry_deprecated_sources_seen")
        if str(authority_registry.get("conflict_risk") or "unknown").lower() in {"medium", "high"}:
            risks.append("authority_registry_conflict_risk")

    sections = state_map.get("sections") if isinstance(state_map.get("sections"), dict) else {}
    missing_sections = [
        heading for heading, item in sections.items() if isinstance(item, dict) and not item.get("present")
    ]
    if missing_sections:
        risks.append("state_sections_missing:" + ",".join(missing_sections[:4]))

    if not inventory.get("repo_exists"):
        risks.append("project_repo_missing")

    checks = inventory.get("checks") if isinstance(inventory.get("checks"), list) else []
    missing_paths = {str(item.get("path")) for item in checks if isinstance(item, dict) and not item.get("exists")}
    missing_roles = {str(item.get("role")) for item in checks if isinstance(item, dict) and not item.get("exists")}
    goal_id = str(record.get("goal_id") or "")
    if ".loopx/registry.json" in missing_paths:
        risks.append("project_local_registry_not_detected")
    if ".loopx/goals" in missing_paths or ".codex/goals" in missing_paths:
        risks.append("project_goal_root_not_detected")
    if "goal_state_dir" in missing_roles:
        risks.append(f"project_goal_state_dir_not_detected:{goal_id}" if goal_id else "project_goal_state_dir_not_detected")
    if (
        ".loopx/registry.json" in missing_paths
        or ".loopx/goals" in missing_paths
        or ".codex/goals" in missing_paths
        or "goal_state_dir" in missing_roles
    ):
        risks.append("project_local_goal_state_not_detected")
    if "README.md" in missing_paths and "docs" in missing_paths:
        risks.append("project_context_surface_sparse")
    validation_markers = {"tests", "package.json", "pyproject.toml", "requirements.txt"}
    if validation_markers.issubset(missing_paths):
        risks.append("standard_validation_surface_not_detected")

    return risks


def compact_project_map(record: dict[str, Any]) -> dict[str, Any]:
    registry_goal = record.get("registry_goal") if isinstance(record.get("registry_goal"), dict) else {}
    adapter = registry_goal.get("adapter") if isinstance(registry_goal.get("adapter"), dict) else {}
    authority_registry = (
        registry_goal.get("authority_registry") if isinstance(registry_goal.get("authority_registry"), dict) else {}
    )
    state_map = record.get("state_map") if isinstance(record.get("state_map"), dict) else {}
    inventory = record.get("project_inventory") if isinstance(record.get("project_inventory"), dict) else {}
    return {
        "adapter_kind": adapter.get("kind"),
        "adapter_status": adapter.get("status"),
        "authority_source_count": registry_goal.get("authority_source_count"),
        "authority_registry_declared": authority_registry.get("declared"),
        "authority_registry_path_exists": authority_registry.get("path_exists"),
        "authority_registry_default_entry_count": authority_registry.get("default_entry_count"),
        "authority_registry_default_entries_present": authority_registry.get("default_entries_present"),
        "topic_authority_count": authority_registry.get("topic_authority_count"),
        "project_material_count": authority_registry.get("project_material_count"),
        "project_material_repository_count": authority_registry.get("project_material_repository_count"),
        "project_material_owner_review_required_count": authority_registry.get(
            "project_material_owner_review_required_count"
        ),
        "project_material_stale_count": authority_registry.get("project_material_stale_count"),
        "project_material_current_authority_count": authority_registry.get(
            "project_material_current_authority_count"
        ),
        "authority_registry_conflict_risk": authority_registry.get("conflict_risk"),
        "guard_count": registry_goal.get("guard_count"),
        "sections_found": state_map.get("sections_found"),
        "sections_checked": state_map.get("sections_checked"),
        "project_registry_exists": inventory.get("project_registry_exists"),
        "goal_state_dir_exists": inventory.get("goal_state_dir_exists"),
        "active_state_file_exists": inventory.get("active_state_file_exists"),
        "files_present": inventory.get("files_present"),
        "files_checked": inventory.get("files_checked"),
        "residual_risk_count": len(record.get("residual_risks") or []),
    }


def latest_operator_gate(runtime_root: Path, goal_id: str, *, gate: str = READ_ONLY_MAP_OPT_IN_GATE) -> dict[str, Any] | None:
    runs_dir = runtime_root / "goals" / goal_id / "runs"
    index_path = runs_dir / "index.jsonl"
    if index_path.exists():
        lines = index_path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            operator_gate = record.get("operator_gate") if isinstance(record, dict) else None
            if isinstance(operator_gate, dict) and operator_gate.get("gate") == gate:
                return operator_gate

    for path in sorted(runs_dir.glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        operator_gate = record.get("operator_gate") if isinstance(record, dict) else None
        if isinstance(operator_gate, dict) and operator_gate.get("gate") == gate:
            return operator_gate
    return None


def render_read_only_project_map_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Read-Only Project Map",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- appended: `{payload.get('appended')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- classification: `{payload.get('classification')}`",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- health_check: `{payload.get('health_check')}`",
        f"- opt_in_required: `{payload.get('opt_in_required')}`",
    ]
    operator_gate = payload.get("operator_gate") if isinstance(payload.get("operator_gate"), dict) else {}
    if operator_gate:
        lines.append(f"- operator_gate: `{operator_gate.get('gate')}:{operator_gate.get('decision')}`")
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)

    global_sync = payload.get("global_sync") if isinstance(payload.get("global_sync"), dict) else {}
    if global_sync:
        lines.extend(
            [
                f"- global_registry: `{global_sync.get('global_registry')}`",
                f"- global_sync_wrote: `{global_sync.get('wrote')}`",
            ]
        )

    lines.extend(["", "## Recommended Action", str(payload.get("recommended_action") or "")])

    project_map = payload.get("project_map") if isinstance(payload.get("project_map"), dict) else {}
    if project_map:
        lines.extend(
            [
                "",
                "## Compact Map",
                f"- adapter: `{project_map.get('adapter_kind')}:{project_map.get('adapter_status')}`",
                f"- authority_sources: `{project_map.get('authority_source_count')}`",
                (
                    "- authority_registry: "
                    f"`declared={project_map.get('authority_registry_declared')} "
                    f"path_exists={project_map.get('authority_registry_path_exists')} "
                    "default_entries="
                    f"{project_map.get('authority_registry_default_entries_present')}/"
                    f"{project_map.get('authority_registry_default_entry_count')} "
                    f"topic_authority={project_map.get('topic_authority_count')} "
                    f"conflict_risk={project_map.get('authority_registry_conflict_risk')}`"
                ),
                (
                    "- project_materials: "
                    f"`total={project_map.get('project_material_count')} "
                    f"repositories={project_map.get('project_material_repository_count')} "
                    "owner_review_required="
                    f"{project_map.get('project_material_owner_review_required_count')} "
                    f"stale={project_map.get('project_material_stale_count')}`"
                ),
                f"- guards: `{project_map.get('guard_count')}`",
                f"- state_sections: `{project_map.get('sections_found')}/{project_map.get('sections_checked')}`",
                (
                    "- project_goal_state: "
                    f"`registry={project_map.get('project_registry_exists')} "
                    f"goal_dir={project_map.get('goal_state_dir_exists')} "
                    f"active_state={project_map.get('active_state_file_exists')}`"
                ),
                f"- project_inventory: `{project_map.get('files_present')}/{project_map.get('files_checked')}`",
                f"- residual_risks: `{project_map.get('residual_risk_count')}`",
            ]
        )

    risks = payload.get("residual_risks") if isinstance(payload.get("residual_risks"), list) else []
    lines.extend(["", "## Residual Risks"])
    if risks:
        for risk in risks:
            lines.append(f"- `{risk}`")
    else:
        lines.append("- none detected in bounded map")

    inventory = payload.get("project_inventory") if isinstance(payload.get("project_inventory"), dict) else {}
    checks = inventory.get("checks") if isinstance(inventory.get("checks"), list) else []
    if checks:
        lines.extend(["", "## Project Inventory"])
        for item in checks:
            if not isinstance(item, dict):
                continue
            lines.append(f"- `{item.get('path')}`: {item.get('kind')}")

    state_map = payload.get("state_map") if isinstance(payload.get("state_map"), dict) else {}
    sections = state_map.get("sections") if isinstance(state_map.get("sections"), dict) else {}
    if sections:
        lines.extend(["", "## State Sections"])
        for heading, item in sections.items():
            if not isinstance(item, dict):
                continue
            lines.append(f"- `{heading}`: present={item.get('present')} lines={item.get('line_count')}")
    return "\n".join(lines)


def read_only_project_map_run(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str,
    project: Path | None,
    state_file: Path | None,
    classification: str,
    recommended_action: str | None,
    dry_run: bool,
    sync_global: bool = True,
) -> dict[str, Any]:
    safe_goal_id = validate_goal_id_path_segment(goal_id)
    validate_public_safe_text("classification", classification)
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_override)
    projection_route, compact_projection_route = prepare_material_projection_route(
        registry_path=registry_path,
        goal_id=safe_goal_id,
        source_runtime_root=runtime_root,
        sync_global=sync_global,
    )
    registry_goal, resolved_project, resolved_state_file = resolve_goal_state(
        registry=registry,
        goal_id=safe_goal_id,
        project_override=project,
        state_file_override=state_file,
    )
    if not registry_goal:
        raise ValueError(f"goal id not found in registry: {safe_goal_id}")
    adapter = registry_goal.get("adapter") if isinstance(registry_goal.get("adapter"), dict) else {}
    adapter_kind = str(adapter.get("kind") or "")
    adapter_status = str(adapter.get("status") or "")
    if not adapter_supports_read_only_map(adapter_kind):
        raise ValueError(
            f"{safe_goal_id} adapter.kind must be `{READ_ONLY_MAP_ADAPTER_KIND}` or end with `_read_only_map_v0`"
        )
    planned_dry_run_preview = adapter_status in READ_ONLY_MAP_DRY_RUN_PREVIEW_STATUSES
    operator_gate = latest_operator_gate(runtime_root, safe_goal_id)
    operator_gate_approved = isinstance(operator_gate, dict) and operator_gate.get("decision") == "approve"
    opt_in_required = planned_dry_run_preview and not operator_gate_approved
    if adapter_status not in READ_ONLY_MAP_ADAPTER_STATUSES and not (dry_run and planned_dry_run_preview):
        raise ValueError(
            f"{safe_goal_id} adapter.status must be one of {sorted(READ_ONLY_MAP_ADAPTER_STATUSES)}"
            f"; planned adapters may only run read-only-map with --dry-run for opt-in preview"
        )
    if not resolved_state_file.exists():
        raise FileNotFoundError(f"state file does not exist: {resolved_state_file}")

    state_text = resolved_state_file.read_text(encoding="utf-8")
    if recommended_action:
        action = recommended_action
    elif planned_dry_run_preview and operator_gate_approved:
        action = (
            "Report the approved read-only map dry-run to the target project agent; "
            "do not append real run history or grant write-control."
        )
    else:
        action = derive_recommended_action(state_text)
    validate_local_control_text("recommended_action", action)
    generated_at = now_local()
    record = build_project_map_record(
        goal_id=safe_goal_id,
        project=resolved_project,
        state_file=resolved_state_file,
        state_text=state_text,
        classification=classification,
        recommended_action=action,
        generated_at=generated_at,
        registry_goal=registry_goal,
    )
    record["residual_risks"] = derive_residual_risks(record, opt_in_required=opt_in_required)
    record["runtime_projection_route"] = compact_projection_route
    project_map = compact_project_map(record)

    runs_dir = runtime_root / "goals" / safe_goal_id / "runs"
    stem = run_file_stem(generated_at)
    json_path = runs_dir / f"{stem}.json"
    markdown_path = runs_dir / f"{stem}.md"
    index_path = runs_dir / "index.jsonl"
    index_record = {
        "generated_at": generated_at,
        "goal_id": safe_goal_id,
        "classification": classification,
        "recommended_action": action,
        "health_check": record["health_check"],
        "residual_risks": record["residual_risks"],
        "project_map": project_map,
        "runtime_projection_route": compact_projection_route,
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }
    payload = {
        "ok": True,
        "dry_run": dry_run,
        "appended": not dry_run,
        "opt_in_required": opt_in_required,
        "operator_gate": operator_gate,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "project": str(resolved_project) if resolved_project else None,
        "goal_id": safe_goal_id,
        "classification": classification,
        "recommended_action": action,
        "generated_at": generated_at,
        "health_check": record["health_check"],
        "residual_risks": record["residual_risks"],
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "index_path": str(index_path),
        "project_map": project_map,
        "runtime_projection_route": compact_projection_route,
        **record,
    }
    if not dry_run:
        from .history import write_reserved_run_artifacts
        write_reserved_run_artifacts(runs_dir=runs_dir, generated_at=generated_at,
            record=record, index_record=index_record, payload=payload,
            render_markdown=render_read_only_project_map_markdown)
    projection_result = finalize_material_projection(
        registry_path=registry_path,
        source_runtime_root=runtime_root,
        goal_id=safe_goal_id,
        source_row=index_record,
        projection_kind="read_only_project_map",
        route=projection_route,
        sync_global=sync_global,
        dry_run=dry_run,
    )
    payload["global_sync"] = projection_result["global_sync"]
    payload["shared_runtime_material_projection"] = projection_result[
        "shared_runtime_material_projection"
    ]
    if not projection_result["ok"]:
        payload["ok"] = False
        payload["partial_write"] = projection_result["partial_write"]
    return payload
