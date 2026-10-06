from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import subprocess
from typing import Any

from ...repository_identity import (
    normalize_repository_identity,
    resolve_project_identity,
)
from ..todos.contract import normalize_todo_claimed_by, normalize_todo_task_repository
from .delivery_workspace import (
    build_delivery_workspace_snapshot,
    normalize_delivery_workspace_snapshot,
)

AGENT_WORKSPACE_GUARD_SCHEMA_VERSION = "agent_workspace_guard_v1"
PEER_WRITE_ACTION_KINDS = {
    "fix",
    "implement",
    "rebuild",
    "repair",
    "writeback",
}


def _is_same_or_child_path(path: Path, root: Path) -> bool:
    try:
        current = path.expanduser().resolve()
        target = root.expanduser().resolve()
    except OSError:
        return False
    return current == target or target in current.parents


def _git_command_output(path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            timeout=1.5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _git_worktree_root(path: Path) -> Path | None:
    output = _git_command_output(path, "rev-parse", "--show-toplevel")
    if not output:
        return None
    try:
        return Path(output).expanduser().resolve()
    except OSError:
        return None


def _git_common_dir(root: Path) -> Path | None:
    """Read from the root resolved in this observation, never a cached root."""
    output = _git_command_output(root, "rev-parse", "--git-common-dir")
    if not output:
        return None
    common = Path(output).expanduser()
    if not common.is_absolute():
        common = root / common
    try:
        return common.resolve()
    except OSError:
        return None


def _git_dir(root: Path) -> Path | None:
    """Read from the root resolved in this observation, never a cached root."""
    output = _git_command_output(root, "rev-parse", "--git-dir")
    if not output:
        return None
    git_dir = Path(output).expanduser()
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    try:
        return git_dir.resolve()
    except OSError:
        return None


def _git_repository_identity(path: Path) -> str | None:
    remote = _git_command_output(path, "config", "--get", "remote.origin.url")
    if not remote:
        return None
    try:
        return normalize_repository_identity(remote)
    except ValueError:
        return None


def _capture_local_goal_workspace(
    path: Path, *, local_goal_id: str | None, local_project_root: Path | None,
    peer_independent_worktree_required: bool, repository_source: str | None,
) -> dict[str, Any] | None:
    if peer_independent_worktree_required or not local_goal_id:
        return None
    if local_project_root is not None and not _is_same_or_child_path(
        path, local_project_root
    ):
        return None
    try:
        workspace_identity = resolve_project_identity(
            path,
            loopx_project_id=local_goal_id,
        )
    except ValueError:
        return None
    if not workspace_identity.startswith("loopx:"):
        return None
    return build_delivery_workspace_snapshot(
        workspace_identity=workspace_identity,
        identity_kind="local_goal",
        repository_source=repository_source or "goal_id_fallback",
        workspace_kind="local_goal_workspace",
        peer_independent_worktree_required=False,
    )


def _git_origin_is_absent(path: Path) -> bool:
    """Only Git's missing-key result permits local fallback; errors do not."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "config", "--get", "remote.origin.url"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=1.5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 1


def capture_delivery_workspace(
    current_path: Path | None = None,
    *,
    peer_independent_worktree_required: bool = False,
    local_goal_id: str | None = None,
    local_project_root: Path | None = None,
    repository_source: str | None = None,
) -> dict[str, Any] | None:
    """Capture a compact, credential-free delivery workspace identity.

    Git deliveries bind to their canonical repository identity and, when HEAD
    exists, an opaque digest of its content-addressed revision. A registered local
    goal without an origin may instead bind to its stable LoopX goal identity. The
    snapshot intentionally excludes local paths, branch names and raw commits.
    """

    path = current_path or Path.cwd()
    current_root = _git_worktree_root(path)
    if current_root is None:
        return _capture_local_goal_workspace(
            path, local_goal_id=local_goal_id, local_project_root=local_project_root,
            peer_independent_worktree_required=peer_independent_worktree_required,
            repository_source=repository_source,
        )
    # Resolve the root once per observation; keep layout, origin and HEAD reads
    # fresh. Re-querying the same root inside both layout helpers adds two
    # subprocess waits without making this multi-command snapshot atomic.
    current_common = _git_common_dir(current_root)
    current_git_dir = _git_dir(current_root)
    task_repository = _git_repository_identity(path)
    workspace_revision = _git_command_output(path, "rev-parse", "HEAD")
    if (
        task_repository is None
        and current_common is not None
        and current_git_dir == current_common
        and local_project_root is not None
        and current_root == local_project_root.expanduser().resolve()
        and _git_origin_is_absent(path)
    ):
        return _capture_local_goal_workspace(
            path, local_goal_id=local_goal_id, local_project_root=local_project_root,
            peer_independent_worktree_required=peer_independent_worktree_required,
            repository_source=repository_source,
        )
    if (
        not task_repository
        or current_common is None
        or current_git_dir is None
    ):
        return None
    workspace_revision_digest = (
        sha256(f"{task_repository}\0{workspace_revision}".encode()).hexdigest()
        if workspace_revision is not None
        else None
    )
    workspace_kind = (
        "independent_git_worktree"
        if current_git_dir != current_common
        else "canonical_checkout"
    )
    return build_delivery_workspace_snapshot(
        workspace_identity=task_repository,
        identity_kind="git_repository",
        repository_source=repository_source or "current_git_origin",
        workspace_kind=workspace_kind,
        peer_independent_worktree_required=peer_independent_worktree_required,
        workspace_revision_digest=workspace_revision_digest,
    )


def delivery_workspace_identity(value: Any) -> str | None:
    snapshot = normalize_delivery_workspace_snapshot(value)
    if snapshot is None:
        return None
    return str(snapshot["workspace_identity"])


def delivery_workspace_repository(value: Any) -> str | None:
    snapshot = normalize_delivery_workspace_snapshot(value)
    if snapshot is None or snapshot.get("identity_kind") != "git_repository":
        return None
    try:
        return normalize_todo_task_repository(snapshot.get("task_repository"))
    except (TypeError, ValueError):
        return None


def build_delivery_workspace_guard(
    delivery_run: dict[str, Any],
    *,
    agent_id: str | None = None,
    current_path: Path | None = None,
) -> dict[str, Any] | None:
    """Validate quota accounting against its accountable delivery workspace.

    Legacy delivery runs without a snapshot return ``None`` so the existing
    selected-todo workspace guard remains the fail-closed fallback.
    """

    snapshot = (
        delivery_run.get("delivery_workspace")
        if isinstance(delivery_run.get("delivery_workspace"), dict)
        else {}
    )
    normalized_snapshot = normalize_delivery_workspace_snapshot(snapshot)
    if normalized_snapshot is None:
        return None
    if normalized_snapshot["identity_kind"] == "local_goal":
        return None
    task_repository = str(normalized_snapshot["task_repository"])

    current = capture_delivery_workspace(current_path)
    current_repository = delivery_workspace_repository(current) or ""
    current_workspace = str((current or {}).get("workspace_kind") or "")
    recorded_workspace = str(normalized_snapshot.get("workspace_kind") or "")
    peer_independent_worktree_required = bool(
        normalized_snapshot.get("peer_independent_worktree_required")
    )
    if not current:
        current_workspace = "not_git_worktree"
    elif current_repository != task_repository:
        current_workspace = "foreign_git_worktree"
    elif (
        recorded_workspace == "independent_git_worktree"
        and current_workspace != "independent_git_worktree"
    ):
        current_workspace = "canonical_checkout"
    elif (
        peer_independent_worktree_required
        and recorded_workspace != "independent_git_worktree"
    ):
        current_workspace = "delivery_not_recorded_from_independent_worktree"
    else:
        return None

    return {
        "schema_version": AGENT_WORKSPACE_GUARD_SCHEMA_VERSION,
        "source": "quota.spend_slot.delivery_workspace",
        "action": "return_to_delivery_worktree",
        "current_workspace": current_workspace,
        "required_workspace": (
            "accountable_delivery_independent_git_worktree"
            if recorded_workspace == "independent_git_worktree"
            or peer_independent_worktree_required
            else "accountable_delivery_git_checkout"
        ),
        "blocks_delivery": True,
        "agent_id": normalize_todo_claimed_by(agent_id),
        "repository_source": "delivery_run.delivery_workspace.task_repository",
        "task_repository": task_repository,
        "delivery_run_generated_at": delivery_run.get("generated_at"),
        "delivery_run_classification": delivery_run.get("classification"),
        "reason": (
            "quota spend workspace does not match the latest unspent accountable "
            "delivery workspace"
        ),
        "required_action": (
            "run quota spend-slot from the workspace that produced the latest "
            "unspent accountable delivery"
        ),
    }


def _peer_candidate_items(agent_todo_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(agent_todo_summary, dict):
        return []
    for key in (
        "active_next_action_executable_items",
        "executable_backlog_items",
        "first_executable_items",
    ):
        items = agent_todo_summary.get(key)
        if isinstance(items, list) and items:
            return [item for item in items if isinstance(item, dict)]
    return []


def _peer_work_requires_isolated_workspace(
    workspace_guard_policy: dict[str, Any],
    agent_todo_summary: dict[str, Any] | None,
    *,
    selected_todo: dict[str, Any] | None = None,
    local_goal_workspace: bool = False,
) -> bool:
    explicit = workspace_guard_policy.get("peer_independent_worktree_required")
    if explicit is not None:
        return explicit is True
    if local_goal_workspace:
        return False
    candidate = (
        selected_todo
        if isinstance(selected_todo, dict) and selected_todo
        else next(iter(_peer_candidate_items(agent_todo_summary)), None)
    )
    if not isinstance(candidate, dict):
        return False
    if candidate.get("required_write_scopes"):
        return True
    if str(candidate.get("task_class") or "").strip().lower() == "continuous_monitor":
        return False
    action_kind = str(candidate.get("action_kind") or "").strip().lower()
    return action_kind in PEER_WRITE_ACTION_KINDS or action_kind.startswith(
        tuple(f"{prefix}_" for prefix in PEER_WRITE_ACTION_KINDS)
    )


def observe_goal_local_workspace(
    goal: dict[str, Any],
    selected_todo: dict[str, Any] | None,
    allowed_write_scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Reuse the registered workspace identity and project its existing grants."""
    empty: dict[str, Any] = {}
    if not selected_todo or selected_todo.get("task_repository"):
        return empty
    repo = goal.get("repo") or goal.get("project") or goal.get("root")
    goal_id = goal.get("goal_id") or goal.get("id")
    if not repo or not goal_id:
        return empty
    root = Path(str(repo)).expanduser()
    if not root.is_absolute():
        return empty
    snapshot = capture_delivery_workspace(
        root, local_goal_id=str(goal_id), local_project_root=root
    )
    if not snapshot or snapshot.get("identity_kind") != "local_goal":
        return empty
    boundary = goal.get("coordination") or {}
    raw_scopes = boundary.get("write_scope") if isinstance(boundary, dict) else None
    scopes = allowed_write_scopes if allowed_write_scopes is not None else raw_scopes
    if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
        return empty
    from ..quota.settlement_workspace_causality import project_goal_write_scopes

    # Physical identity is resolved above. Scope authority stays relative to the
    # registered spelling, including an explicit symlink alias of that root.
    return {"workspace": snapshot, **project_goal_write_scopes(str(root), scopes)}


def build_agent_workspace_guard(
    goal: dict[str, Any],
    agent_identity: dict[str, Any] | None,
    *,
    agent_todo_summary: dict[str, Any] | None = None,
    selected_todo: dict[str, Any] | None = None,
    current_path: Path | None = None,
    local_workspace: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(agent_identity, dict):
        return None
    workspace_guard_policy = (
        goal.get("workspace_guard_policy")
        if isinstance(goal.get("workspace_guard_policy"), dict)
        else {}
    )
    if len(agent_identity.get("registered_agents") or []) <= 1:
        return None
    current_path = current_path or Path.cwd()
    candidate = (
        selected_todo
        if isinstance(selected_todo, dict) and selected_todo
        else next(iter(_peer_candidate_items(agent_todo_summary)), {})
    )
    local = (
        local_workspace
        if local_workspace is not None
        else observe_goal_local_workspace(goal, candidate)
    )
    # Local declarations are relative to the registered Goal target, not the
    # caller cwd. Causal accounting still names the actual delivery workspace.
    local_admitted = (local.get("workspace") or {}).get("identity_kind") == "local_goal"
    if not _peer_work_requires_isolated_workspace(
        workspace_guard_policy,
        agent_todo_summary,
        selected_todo=selected_todo,
        local_goal_workspace=local_admitted,
    ):
        return None
    task_repository = normalize_todo_task_repository(candidate.get("task_repository"))
    current_workspace = ""
    repository_source = "goal.repo"
    if task_repository:
        repository_source = "selected_todo.task_repository"
        current_root = _git_worktree_root(current_path)
        current_common = _git_common_dir(current_root) if current_root else None
        current_git_dir = _git_dir(current_root) if current_root else None
        current_repository = (
            _git_repository_identity(current_path) if current_root else None
        )
        if current_root is None:
            current_workspace = "not_git_worktree"
        elif current_repository != task_repository:
            current_workspace = "foreign_git_worktree"
        elif (
            current_common is None
            or current_git_dir is None
            or current_git_dir == current_common
        ):
            current_workspace = "canonical_checkout"
    else:
        repo_value = goal.get("repo") or goal.get("project") or goal.get("root")
        if not repo_value:
            return None
        repo_path = Path(str(repo_value)).expanduser()
        if not repo_path.is_absolute():
            return None
        if _is_same_or_child_path(current_path, repo_path):
            current_workspace = "canonical_checkout"
        else:
            canonical_root = _git_worktree_root(repo_path) or repo_path
            current_root = _git_worktree_root(current_path)
            canonical_common = _git_common_dir(canonical_root)
            current_common = _git_common_dir(current_root) if current_root else None
            if current_root is None:
                current_workspace = "not_git_worktree"
            elif (
                canonical_common is None
                or current_common is None
                or current_common != canonical_common
            ):
                current_workspace = "foreign_git_worktree"
            elif current_root == canonical_root:
                current_workspace = "canonical_checkout"
    if not current_workspace:
        return None
    payload = {
        "schema_version": AGENT_WORKSPACE_GUARD_SCHEMA_VERSION,
        "source": "quota.should-run",
        "action": "move_to_independent_worktree",
        "current_workspace": current_workspace,
        "required_workspace": "independent_git_worktree",
        "blocks_delivery": True,
        "agent_id": agent_identity.get("agent_id"),
        "repository_source": repository_source,
        "reason": (
            "peer delivery with repository writes is not running from an independent "
            "worktree; normal delivery must move before repository edits"
        ),
        "required_action": (
            "create or switch to an independent git worktree/branch for this peer lane, "
            "then rerun quota should-run with the same --agent-id before editing files"
        ),
    }
    if task_repository:
        payload["task_repository"] = task_repository
    return payload
