from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .authority import authority_registry_summary
from .control_plane import compact_control_plane_policy, control_plane_policy_summary
from .control_plane.goals.contract_health import contract_error_diagnostic
from .control_plane.projects.registry_codec import load_registry
from .execution_profile import compact_execution_profile, execution_profile_summary
from .explore_graph import compact_explore_graph_policy
from .orchestration import compact_orchestration_policy, orchestration_policy_summary
from .presentation.markdown import markdown_code, markdown_table_row, markdown_table_separator
from .quota import goal_quota_config


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("registry root must be a JSON object")
    return payload


def resolve_state_file(repo: Path, state_file: str | None) -> Path | None:
    if not state_file:
        return None
    path = Path(state_file).expanduser()
    return path if path.is_absolute() else repo / path


def find_registry_goal(registry: dict[str, Any], goal_id: str) -> dict[str, Any] | None:
    for goal in registry_goals(registry):
        if str(goal.get("id") or "") == goal_id:
            return goal
    return None


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    preserve_mode: bool = False,
) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if preserve_mode and path.exists() else None
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def stable_path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path.expanduser())


def registry_goals(registry: dict[str, Any]) -> list[dict[str, Any]]:
    goals = registry.get("goals")
    if not isinstance(goals, list):
        return []
    return [goal for goal in goals if isinstance(goal, dict) and goal.get("id")]


PRIVATE_REGISTRY_PARTS = {".loopx", ".local", ".codex", "runtime"}
PUBLIC_FIXTURE_PARTS = {"examples"}
LOCAL_PATH_RE = re.compile(r"(^|[\"':\\s])(?:/Users/|/private/|/var/folders/|/tmp/|~[/\\])")
PRIVATE_REGISTRY_TEXT_RE = re.compile(
    "|".join(
        [
            "la" + "rk" + "office",
            "docs" + r"\." + "internal",
            "Bear" + "er",
            "Author" + "ization",
            "tok" + "en",
            "pass" + "word",
            "sec" + "ret",
        ]
    ),
    re.I,
)


def _definitely_outside_git_worktree(probe_dir: Path) -> bool:
    if os.environ.get("GIT_DIR") or os.environ.get("GIT_WORK_TREE"):
        return False
    try:
        current = probe_dir.resolve(strict=True)
    except OSError:
        return False
    return not any(os.path.lexists(parent / ".git") for parent in (current, *current.parents))


def _registry_git_probe(path: Path) -> dict[str, Any]:
    probe_dir = path if path.is_dir() else path.parent

    def unavailable() -> dict[str, Any]:
        return {
            "available": False,
            "probe_status": "git_unavailable",
            "inside_worktree": False,
            "tracked": False,
            "ignored": False,
            "worktree_root_recorded": False,
        }

    def run_git(*args: str, cwd: Path = probe_dir) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=1.5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    # Git discovers a normal repository only through an ancestor `.git`
    # marker. Non-Git LoopX projects are common, and status can inspect the
    # same registry more than once per command, so avoid a guaranteed-failing
    # subprocess while retaining the Git path for worktrees, submodules, and
    # explicit environment overrides.
    if _definitely_outside_git_worktree(probe_dir):
        if shutil.which("git") is None:
            return unavailable()
        return {
            "available": True,
            "probe_status": "ok",
            "inside_worktree": False,
            "tracked": False,
            "ignored": False,
            "worktree_root_recorded": False,
        }

    root = run_git("rev-parse", "--show-toplevel")
    if root is None:
        return unavailable()
    inside = root.returncode == 0
    tracked = False
    ignored = False
    if inside:
        root_path = Path(root.stdout.strip())
        try:
            rel_path = os.path.relpath(path.resolve(), root_path)
        except (OSError, ValueError):
            rel_path = path.name
        tracked_result = run_git("ls-files", "--error-unmatch", "--", rel_path, cwd=root_path)
        ignored_result = run_git("check-ignore", "-q", "--", rel_path, cwd=root_path)
        tracked = tracked_result is not None and tracked_result.returncode == 0
        ignored = ignored_result is not None and ignored_result.returncode == 0
    return {
        "available": True,
        "probe_status": "ok",
        "inside_worktree": inside,
        "tracked": tracked,
        "ignored": ignored,
        "worktree_root_recorded": False,
    }


def _iter_registry_values(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = [(prefix, value)]
    if isinstance(value, dict):
        for key, item in value.items():
            item_prefix = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_iter_registry_values(item, item_prefix))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            item_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            rows.extend(_iter_registry_values(item, item_prefix))
    return rows


def registry_private_markers(payload: dict[str, Any]) -> list[dict[str, str]]:
    markers: list[dict[str, str]] = []
    for key_path, value in _iter_registry_values(payload):
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            continue
        key_name = key_path.split(".")[-1].lower()
        text = str(value)
        if text.startswith("/path/to/"):
            continue
        if key_name in {
            "repo",
            "source_registry",
            "json_path",
            "markdown_path",
            "index_path",
            "common_runtime_root",
            "global_registry",
        } and (Path(text).is_absolute() or LOCAL_PATH_RE.search(text)):
            markers.append({"path": key_path, "kind": "local_path"})
            continue
        if key_name in {"url", "doc_url", "wiki_url", "document_id", "wiki_node_token", "source_ref"}:
            markers.append({"path": key_path, "kind": "raw_source_reference"})
            continue
        if LOCAL_PATH_RE.search(text):
            markers.append({"path": key_path, "kind": "local_path"})
            continue
        if PRIVATE_REGISTRY_TEXT_RE.search(text):
            markers.append({"path": key_path, "kind": "private_text"})
    return markers[:25]


def _registry_role(payload: dict[str, Any]) -> str:
    return str(payload.get("registry_role") or payload.get("role") or "")


def classify_registry_boundary(path: Path, payload: dict[str, Any], markers: list[dict[str, str]]) -> str:
    parts = set(path.expanduser().parts)
    role = _registry_role(payload)
    if role == "global-local" or path.name == "registry.global.json":
        return "shared_local_registry"
    if PRIVATE_REGISTRY_PARTS.intersection(parts):
        return "project_local_private_registry"
    if PUBLIC_FIXTURE_PARTS.intersection(parts) and "example" in path.name:
        return "public_example_fixture"
    if role in {"public-safe-projection", "public_projection", "public"}:
        return "public_safe_projection"
    if markers:
        return "local_private_registry"
    return "public_safe_projection"


def _boundary_kind_from_classification(classification: str) -> str:
    return {
        "project_local_private_registry": "project_local_private_registry",
        "shared_local_registry": "global_local_private_registry",
        "public_example_fixture": "public_fixture_registry_projection",
        "public_safe_projection": "public_safe_registry_projection",
        "local_private_registry": "local_private_registry",
    }.get(classification, "unknown_registry_boundary")


def inspect_registry_boundary(path: Path) -> dict[str, Any]:
    expanded = path.expanduser()
    if not expanded.exists():
        return {
            "ok": False,
            "schema_version": "loopx_registry_boundary_v0",
            "path_label": expanded.name,
            "path_recorded": False,
            "exists": False,
            "error": "registry file does not exist",
        }
    payload = load_registry(expanded)
    markers = registry_private_markers(payload)
    classification = classify_registry_boundary(expanded, payload, markers)
    git = _registry_git_probe(expanded)
    should_be_gitignored = classification in {
        "project_local_private_registry",
        "shared_local_registry",
        "local_private_registry",
        "public_safe_projection",
    }
    github_push_allowed = classification == "public_example_fixture" and not markers
    risks: list[str] = []
    if markers and classification in {"public_safe_projection", "public_example_fixture"}:
        risks.append("public_registry_contains_private_markers")
    if git.get("tracked") and not github_push_allowed:
        risks.append("registry_tracked_but_not_push_allowed")
    if should_be_gitignored and git.get("inside_worktree") and not git.get("ignored") and not git.get("tracked"):
        risks.append("registry_should_be_gitignored")
    if classification == "shared_local_registry":
        risks.append("global_registry_is_local_control_plane_only")
    if classification == "public_safe_projection" and not github_push_allowed:
        risks.append("public_safe_projection_is_not_runtime_publish_artifact")

    goal_count = len(registry_goals(payload))
    authority_source_count = 0
    for goal in registry_goals(payload):
        sources = goal.get("authority_sources")
        if isinstance(sources, list):
            authority_source_count += len(sources)
        elif isinstance(goal.get("authority_source_count"), int):
            authority_source_count += int(goal.get("authority_source_count") or 0)

    blocking_risks = {"public_registry_contains_private_markers", "registry_tracked_but_not_push_allowed"}
    return {
        "ok": not any(risk in blocking_risks for risk in risks),
        "schema_version": "loopx_registry_boundary_v0",
        "source_schema_version": payload.get("schema_version"),
        "path_label": expanded.name,
        "path_recorded": False,
        "exists": True,
        "registry_role": _registry_role(payload) or None,
        "classification": classification,
        "boundary_kind": _boundary_kind_from_classification(classification),
        "goal_count": goal_count,
        "authority_source_count": authority_source_count,
        "private_marker_count": len(markers),
        "private_markers": markers,
        "git": git,
        "should_be_gitignored": should_be_gitignored,
        "github_push_allowed": github_push_allowed,
        "recommended_storage": (
            "ignored local control-plane file"
            if should_be_gitignored
            else "tracked public fixture/contract only"
        ),
        "publication_policy": (
            "do_not_push_runtime_registry"
            if not github_push_allowed
            else "tracked_example_fixture_allowed"
        ),
        "risks": risks,
    }


def inspect_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "ok": False,
            "registry": str(path),
            "error": "registry file does not exist",
        }

    payload = load_registry(path)
    goals = payload.get("goals") or []
    if not isinstance(goals, list):
        raise ValueError("goals must be a list")

    inspected_goals: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    problems: list[str] = []
    problem_diagnostics: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    repo_goal_ids: dict[str, list[str]] = {}
    state_goal_ids: dict[str, list[str]] = {}

    def add_problem(
        code: str,
        message: str,
        *,
        goal_id: str | None = None,
        goal_ids: list[str] | None = None,
    ) -> None:
        problems.append(message)
        problem_diagnostics.append(
            contract_error_diagnostic(
                code=code,
                message=message,
                goal_id=goal_id,
                goal_ids=goal_ids,
            )
        )

    for raw_goal in goals:
        if not isinstance(raw_goal, dict):
            continue
        goal_id = str(raw_goal.get("id") or "")
        if not goal_id:
            continue
        repo_text = str(raw_goal.get("repo") or "")
        repo = Path(repo_text).expanduser() if repo_text else None
        if repo:
            repo_goal_ids.setdefault(stable_path_key(repo), []).append(goal_id)
            state_file = resolve_state_file(repo, raw_goal.get("state_file"))
            if state_file:
                state_goal_ids.setdefault(stable_path_key(state_file), []).append(goal_id)

    for raw_goal in goals:
        if not isinstance(raw_goal, dict):
            add_problem("registry_goal_not_object", "non-object goal entry")
            continue

        goal_id = str(raw_goal.get("id") or "")
        status = str(raw_goal.get("status") or "unknown")
        repo_text = str(raw_goal.get("repo") or "")
        repo = Path(repo_text).expanduser() if repo_text else None
        state_file = resolve_state_file(repo, raw_goal.get("state_file")) if repo else None
        repo_ids = repo_goal_ids.get(stable_path_key(repo), []) if repo else []
        adapter = raw_goal.get("adapter") if isinstance(raw_goal.get("adapter"), dict) else {}
        spawn_policy = raw_goal.get("spawn_policy") if isinstance(raw_goal.get("spawn_policy"), dict) else {}
        orchestration = compact_orchestration_policy(spawn_policy)
        execution_profile = compact_execution_profile(raw_goal.get("execution_profile"))
        control_plane = compact_control_plane_policy(raw_goal.get("control_plane"))
        authority_sources = raw_goal.get("authority_sources")
        if not isinstance(authority_sources, list):
            authority_sources = []
        authority_registry = authority_registry_summary(raw_goal)
        quota = goal_quota_config(raw_goal)

        status_counts[status] = status_counts.get(status, 0) + 1
        if not goal_id:
            add_problem("registry_goal_missing_id", "goal entry missing id")
        elif goal_id in seen_ids:
            add_problem(
                "registry_duplicate_goal_id",
                f"duplicate goal id: {goal_id}",
            )
        seen_ids.add(goal_id)

        if not repo:
            add_problem(
                "registry_goal_missing_repo",
                f"{goal_id or '<missing>'}: missing repo",
                goal_id=goal_id or None,
            )
        if not raw_goal.get("domain"):
            add_problem(
                "registry_goal_missing_domain",
                f"{goal_id or '<missing>'}: missing domain",
                goal_id=goal_id or None,
            )
        if not raw_goal.get("state_file"):
            add_problem(
                "registry_goal_missing_state_file",
                f"{goal_id or '<missing>'}: missing state_file",
                goal_id=goal_id or None,
            )
        if not adapter.get("kind"):
            add_problem(
                "registry_goal_missing_adapter_kind",
                f"{goal_id or '<missing>'}: missing adapter.kind",
                goal_id=goal_id or None,
            )

        inspected_goals.append(
            {
                "id": goal_id,
                "domain": raw_goal.get("domain"),
                "status": status,
                "role": raw_goal.get("role") or "controller",
                "parent_goal_id": raw_goal.get("parent_goal_id"),
                "repo": repo_text,
                "repo_exists": bool(repo and repo.exists()),
                "repo_goal_count": len(repo_ids),
                "repo_goal_ids": repo_ids,
                "state_file": raw_goal.get("state_file"),
                "state_file_abs": stable_path_key(state_file) if state_file else None,
                "state_file_exists": bool(state_file and state_file.exists()),
                "adapter_kind": adapter.get("kind"),
                "adapter_status": adapter.get("status"),
                "authority_sources": authority_sources,
                "authority_source_count": len(authority_sources),
                "authority_registry": authority_registry,
                "quota": quota,
                "execution_profile": execution_profile,
                "waiting_on": raw_goal.get("waiting_on"),
                "attention_status": raw_goal.get("attention_status"),
                "operator_question": raw_goal.get("operator_question"),
                "recommended_action": raw_goal.get("recommended_action"),
                "next_handoff_condition": raw_goal.get("next_handoff_condition"),
                "explore_graph": compact_explore_graph_policy(raw_goal.get("explore_graph"), spawn_policy.get("explore_harness")) if raw_goal.get("explore_graph") is not None or (raw_goal.get("spawn_policy") or {}).get("explore_harness") else None,
                "orchestration": orchestration,
                "orchestration_mode": orchestration.get("mode"),
                "spawn_allowed": spawn_policy.get("allowed"),
                "max_children": spawn_policy.get("max_children"),
                "control_plane": control_plane,
                "next_probe": raw_goal.get("next_probe"),
                "guards": raw_goal.get("guards") or [],
            }
        )

    for state_file, goal_ids in sorted(state_goal_ids.items()):
        unique_goal_ids = sorted(set(goal_ids))
        if len(unique_goal_ids) > 1:
            add_problem(
                "registry_state_file_shared",
                "state_file shared by multiple goals: "
                f"{', '.join(unique_goal_ids)} -> {state_file}",
                goal_ids=unique_goal_ids,
            )

    return {
        "ok": not problems,
        "registry": str(path),
        "schema_version": payload.get("schema_version"),
        "updated_at": payload.get("updated_at"),
        "common_runtime_root": payload.get("common_runtime_root"),
        "goal_count": len(inspected_goals),
        "status_counts": status_counts,
        "problems": problems,
        "problem_diagnostics": problem_diagnostics,
        "goals": inspected_goals,
    }


def render_registry_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Registry",
        "",
        f"- registry: `{payload.get('registry')}`",
        f"- ok: `{payload.get('ok')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)

    lines.extend(
        [
            f"- schema_version: `{payload.get('schema_version')}`",
            f"- updated_at: `{payload.get('updated_at')}`",
            f"- common_runtime_root: `{payload.get('common_runtime_root')}`",
            f"- goals: `{payload.get('goal_count')}`",
            "",
            markdown_table_row(
                [
                    "goal",
                    "role",
                    "parent",
                    "domain",
                    "status",
                    "repo_exists",
                    "repo_goals",
                    "state_exists",
                    "spawn",
                    "adapter",
                    "next_probe",
                ]
            ),
            markdown_table_separator(11),
        ]
    )
    for goal in payload.get("goals") or []:
        adapter = f"{goal.get('adapter_kind')}:{goal.get('adapter_status')}"
        spawn = orchestration_policy_summary(
            goal.get("orchestration") if isinstance(goal.get("orchestration"), dict) else None
        )
        authority_suffix = ""
        if goal.get("authority_source_count"):
            authority_suffix = f" authorities={goal.get('authority_source_count')}"
        authority_registry = goal.get("authority_registry") if isinstance(goal.get("authority_registry"), dict) else {}
        if authority_registry.get("declared"):
            default_count = authority_registry.get("default_entry_count")
            topic_count = authority_registry.get("topic_authority_count")
            authority_suffix += f" authority_registry=defaults:{default_count},topics:{topic_count}"
        quota = goal.get("quota") if isinstance(goal.get("quota"), dict) else {}
        quota_suffix = f" quota={quota.get('compute')}" if quota else ""
        execution_profile = (
            goal.get("execution_profile")
            if isinstance(goal.get("execution_profile"), dict)
            else None
        )
        execution_suffix = (
            f" execution_profile={execution_profile_summary(execution_profile)}"
            if execution_profile
            else ""
        )
        control_plane = (
            goal.get("control_plane")
            if isinstance(goal.get("control_plane"), dict)
            else None
        )
        control_plane_suffix = (
            f" control_plane={control_plane_policy_summary(control_plane)}"
            if control_plane
            else ""
        )
        lines.append(
            markdown_table_row(
                [
                    markdown_code(goal.get("id")),
                    goal.get("role"),
                    goal.get("parent_goal_id") or "",
                    goal.get("domain"),
                    goal.get("status"),
                    goal.get("repo_exists"),
                    goal.get("repo_goal_count"),
                    goal.get("state_file_exists"),
                    spawn,
                    f"{adapter}{authority_suffix}{quota_suffix}{execution_suffix}{control_plane_suffix}",
                    goal.get("next_probe"),
                ]
            )
        )

    problems = payload.get("problems") or []
    if problems:
        lines.extend(["", "## Problems"])
        lines.extend(f"- {item}" for item in problems)
    return "\n".join(lines)


def render_registry_boundary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Registry Boundary",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- path_label: `{payload.get('path_label')}`",
        f"- path_recorded: `{payload.get('path_recorded')}`",
        f"- exists: `{payload.get('exists')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)
    git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
    lines.extend(
        [
            f"- boundary_kind: `{payload.get('boundary_kind')}`",
            f"- classification: `{payload.get('classification')}`",
            f"- registry_role: `{payload.get('registry_role')}`",
            f"- goals: `{payload.get('goal_count')}`",
            f"- authority_sources: `{payload.get('authority_source_count')}`",
            f"- private_markers: `{payload.get('private_marker_count')}`",
            f"- should_be_gitignored: `{payload.get('should_be_gitignored')}`",
            f"- github_push_allowed: `{payload.get('github_push_allowed')}`",
            f"- publication_policy: `{payload.get('publication_policy')}`",
            f"- git_tracked: `{git.get('tracked')}`",
            f"- git_ignored: `{git.get('ignored')}`",
            f"- worktree_root_recorded: `{git.get('worktree_root_recorded')}`",
        ]
    )
    risks = payload.get("risks") if isinstance(payload.get("risks"), list) else []
    if risks:
        lines.extend(["", "## Risks"])
        lines.extend(f"- `{risk}`" for risk in risks)
    markers = payload.get("private_markers") if isinstance(payload.get("private_markers"), list) else []
    if markers:
        lines.extend(["", "## Private Markers"])
        for marker in markers:
            if not isinstance(marker, dict):
                continue
            lines.append(f"- `{marker.get('path')}`: {marker.get('kind')}")
    return "\n".join(lines)
