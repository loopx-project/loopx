"""Loopback status route owned by the Chat HTTP surface."""

from __future__ import annotations

import errno
import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..chat import redact_local_paths
from .goal_subagent_api import goal_subagent_configuration_enabled
from .workspace_directory import workspace_goal_directory
from ..codex_app_thread_activity import (
    codex_delivery_expectations,
    codex_thread_observers,
)
from ..control_plane.agents.host_thread_activity import (
    attach_host_delivery_windows,
    attach_host_thread_activity,
)
from ..control_plane.effect_runtime import (
    EffectRuntimePermanentIOError,
    EffectRuntimeRemoteError,
    EffectRuntimeStartupError,
    effect_runtime_request_scope,
)
from ..control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
from ..control_plane.runtime.public_safety import (
    public_safe_compact_text,
    validate_public_safe_value,
)
from ..control_plane.todos.contract import (
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_status,
    todo_done_for_status,
)
from ..control_plane.work_items.task_graph import build_goal_task_map
from ..control_plane.projection_envelope_facts import seal_projection_envelope, source_fact
from ..control_plane.runtime.time import now_utc_iso
from ..feedback import validate_goal_id
from ..history import load_registry
from ..control_plane.projects.registry_codec import load_project_registry
from ..registry import registry_goals, resolve_state_file
from ..state_projection import build_active_state_structured_projection
from ..status import collect_status
from ..status_server import (
    DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH,
    DEFAULT_EXTENSION_PROJECTION_PATH,
    ExtensionPresentationRequestMixin,
    parse_goal_activation_filter,
)
from ..control_plane.todos.list_readback import list_goal_todos


def goal_read_runtime_root(server: Any) -> str | None:
    """Keep registry source routing; Chat storage is only an unbound fallback."""
    registry = _discovery_registry(server.registry_path)
    if registry.get("common_runtime_root"):
        # Passing the coordinator's resolved root as an explicit override
        # suppresses the collector's per-Goal source_registry routing. A
        # global directory may contain Goals owned by different runtimes.
        return None
    controller = getattr(server, "runtime_controller", None)
    root = getattr(controller, "coordination_runtime_root", None)
    return str(root) if root is not None else server.runtime_root_override


def _goal_task_map(server: Any, goal_id: str) -> dict[str, Any] | None:
    runtime_root = goal_read_runtime_root(server)
    source_read_at = now_utc_iso()
    try:
        listed = list_goal_todos(
            registry_path=server.registry_path,
            goal_id=goal_id,
            runtime_root_arg=str(runtime_root) if runtime_root else None,
        )
    except (OSError, ValueError, LocalCoordinationAuthorityUnavailable):
        # An unreadable Todo source leaves the rest of the review usable.
        return None
    todos = [row for row in listed.get("todos") or [] if isinstance(row, dict)]
    return build_goal_task_map(
        goal_id=goal_id,
        todos=todos,
        source_truncated=int(listed.get("todo_count") or 0) > len(todos),
        public_safe_compact_text=public_safe_compact_text,
        normalize_todo_status=normalize_todo_status,
        todo_done_for_status=todo_done_for_status,
        todo_status_open=TODO_STATUS_OPEN,
        monitor_task_class=TODO_TASK_CLASS_MONITOR,
        source_read_at=source_read_at,
    )


def _discovery_registry(registry_path: Path) -> dict[str, Any]:
    """Read the registry for the workspace directory; a missing file is an empty workspace.

    A fresh install has no registry until the first Goal is created. Discovery
    reports that as zero registered Goals rather than a projection failure, so
    the home page can show its first-Goal state. Unreadable or incompatible
    registries still raise.
    """

    try:
        return load_project_registry(registry_path)
    except FileNotFoundError:
        return {}


def _discovery_contract(registry_path: Path) -> dict[str, Any]:
    """The source facts a discovery read can state without opening runtime state.

    A fresh install has no registry until the first Goal is created. The full
    status reports that as a warning (allow_missing_registry); the directory
    states the same fact in the same words, so the first-Goal home can show why
    the workspace is empty. The caller redacts paths like every status response.
    """

    warnings: list[str] = []
    if not registry_path.exists():
        warnings.append(f"registry file does not exist: {registry_path}")
    return {
        "ok": True,
        "summary": {"errors": 0, "warnings": len(warnings), "checks": 0},
        "errors": [],
        "warnings": warnings,
        "checks": [],
    }


def _status_access_denied(error: BaseException) -> bool:
    """Recognize permission causes without reclassifying unrelated I/O failures."""
    current: BaseException | None = error
    seen: set[int] = set()
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, EffectRuntimeRemoteError):
            return (
                isinstance(current, EffectRuntimePermanentIOError)
                and current.diagnostic_code == "io_permission_denied"
            )
        if isinstance(current, OSError):
            winerror = getattr(current, "winerror", None)
            if winerror is not None:
                # Windows also maps sharing/lock violations to PermissionError.
                return type(winerror) is int and winerror in {5, 65}
            if isinstance(current, PermissionError) or current.errno in {
                errno.EACCES, errno.EPERM,
            }:
                return True
        if current.__cause__ is not None:
            current = current.__cause__
        elif (
            isinstance(current, EffectRuntimeStartupError)
            and current.diagnostic_code in {
                "runtime_launch_failed", "runtime_request_failed",
            }
            and not current.__suppress_context__
        ):
            current = current.__context__
        else:
            break
    return False


class ChatStatusRequestMixin(ExtensionPresentationRequestMixin):
    """Serve the full dashboard status contract through the Chat origin."""

    server: Any

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _delivery_review(self) -> None:
        with effect_runtime_request_scope():
            self._status(delivery_review=True)

    def _status(self, *, delivery_review: bool = False) -> None:
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        query_error_code = "invalid_goal_activation"
        try:
            activation_state_filter = parse_goal_activation_filter(query)
            query_error_code = "invalid_workspace_query"
            views = query.get("view")
            if views is not None and (delivery_review or views != ["workspace-directory"]):
                raise ValueError("unsupported workspace status view")
            if delivery_review and not self._require_loopback_origin():
                return
            requested_goals = query.get("goal_id")
            goal_id = self.server.selected_goal_id
            if requested_goals is not None:
                if len(requested_goals) != 1 or not requested_goals[0].strip():
                    raise ValueError("one nonempty goal_id is required")
                requested_goal = requested_goals[0]
                validate_goal_id(requested_goal)
                if goal_id and requested_goal != goal_id:
                    raise ValueError("goal_id is outside this workspace")
                goal_id = requested_goal
            if delivery_review and not goal_id:
                raise ValueError("delivery review requires one goal_id")
        except ValueError as exc:
            self._send_error(
                str(exc),
                status=400,
                error_code=query_error_code,
            )
            return
        try:
            status_runtime_root = goal_read_runtime_root(self.server)
            registry = {}
            if views is not None or requested_goals is not None or delivery_review:
                # Discovery reads only metadata. Full status/runtime reads keep
                # their existing lifecycle-only profile refusal.
                registry = (_discovery_registry(self.server.registry_path) if views == ["workspace-directory"]
                    else load_registry(self.server.registry_path))
                directory = workspace_goal_directory(registry, selected_goal_id=goal_id)
                if goal_id and not directory["goals"]:
                    self._send_error("Goal is no longer registered.", status=404)
                    return
                if views == ["workspace-directory"]:
                    directory["contract"] = _discovery_contract(self.server.registry_path)
                    # Discovery stays metadata-only: redact with the roots this
                    # server knows, without requiring the full runtime wiring.
                    runtime_root = getattr(self.server, "runtime_root", None)
                    directory = json.loads(
                        redact_local_paths(
                            json.dumps(directory, ensure_ascii=False),
                            protected_paths=[
                                self.server.registry_path,
                                *(getattr(self.server, "scan_roots", None) or []),
                                *([runtime_root] if runtime_root else []),
                            ],
                        )
                    )
                    self._send_json(directory)
                    return
            linked = None
            if goal_id and registry.get("goal_source_links"):
                from ..control_plane.status.goal_source import SourceScopeError
                from ..presentation.goal_source_routing import goal_source_catalog
                catalog = goal_source_catalog(self.server)
                if delivery_review and catalog.resolve(goal_id, registry):
                    self._send_error("Execution review belongs to the original Goal runtime.", status=403)
                    return
                try:
                    linked = catalog.status(goal_id, registry)
                except SourceScopeError:
                    self._send_error("The original Goal is no longer available in this source.",
                                     status=409)
                    return
                except (OSError, subprocess.SubprocessError):
                    self._send_error("The original Goal source is unavailable; its link is preserved.",
                                     status=503, error_code="goal_source_unavailable")
                    return
            projection = linked if linked is not None else collect_status(
                registry_path=self.server.registry_path,
                # The Chat service already starts on an absent registry as an
                # empty workspace (serve_chat); its status reports the same
                # fact as a warning, so a fresh install is not a failed source.
                allow_missing_registry=True,
                runtime_root_override=status_runtime_root,
                limit=self.server.limit,
                goal_id=goal_id,
                include_zcode_goal_eligibility=not delivery_review,
                include_task_graph=delivery_review,
                activation_state_filter=activation_state_filter,
                include_goal_subagent_configuration=(
                    goal_subagent_configuration_enabled(self.server)
                ),
            )
            if requested_goals is not None or delivery_review:
                latest = workspace_goal_directory(
                    load_registry(self.server.registry_path)
                )
                if latest["registry_revision"] != directory["registry_revision"]:
                    self._send_error(
                        "Goal directory changed; refresh the workspace.", status=409
                    )
                    return
                projection["workspace_registry_revision"] = directory[
                    "registry_revision"
                ]
            if not delivery_review:
                projection["local_dashboard_api"] = {
                    **(projection.get("local_dashboard_api") or {}),
                    "source": "chat",
                    "status_url": "/status.json",
                    "presentation_surfaces_url": DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH,
                    "presentation_detail_url": DEFAULT_EXTENSION_PROJECTION_PATH,
                }
                if linked is None:
                    attach_host_thread_activity(
                        projection, observers=codex_thread_observers(
                            cache_root=self.server.runtime_root / "observation-cache" / "codex-activity",
                        )
                    )
                    attach_host_delivery_windows(
                        projection,
                        expectations=codex_delivery_expectations,
                    )
            if delivery_review:
                if projection.get("ok") is not True:
                    self._send_error("Delivery review sources are unavailable.", status=503)
                    return
                item = next((row for row in projection.get("attention_queue", {}).get("items", [])
                             if row.get("goal_id") == goal_id), {})
                goal = next((row for row in projection.get("run_history", {}).get("goals", [])
                             if row.get("id") == goal_id), {})
                goal_map = _goal_task_map(self.server, goal_id)
                observed_at = now_utc_iso()
                components = [item.get("task_graph_projection"), goal_map, goal.get("acceptance_observation")]
                upstream = [value for value in [projection.get("projection_envelope"),
                    goal_map.get("projection_envelope") if goal_map else None] if isinstance(value, dict)]
                sources = []
                if not isinstance(projection.get("projection_envelope"), dict):
                    sources.append(source_fact("status", read_status="not_read"))
                if goal_map is None:
                    sources.append(source_fact("goal_todos", read_status="unreadable"))
                missing = sum(value is None for value in components)
                envelope = seal_projection_envelope(
                    projection="delivery_review", observed_at=observed_at,
                    sources=sources, upstream=upstream,
                    coverage={"scope": "selected_goal_delivery", "expected_count": 3,
                        "included_count": 3 - missing,
                        "omitted": [{"reason": "unavailable_components", "count": missing}] if missing else []},
                )
                projection = {
                    "ok": True,
                    "goal_id": goal_id,
                    "observed_at": observed_at,
                    "projection_envelope": envelope,
                    "graph": item.get("task_graph_projection"),
                    "goal_map": goal_map,
                    "acceptance": goal.get("acceptance_observation"),
                }
            protected_paths = [
                self.server.registry_path,
                *self.server.scan_roots,
                self.server.runtime_root,
                *([status_runtime_root] if status_runtime_root else []),
            ]
            projection = json.loads(
                redact_local_paths(
                    json.dumps(projection, ensure_ascii=False),
                    protected_paths=protected_paths,
                )
            )
            if delivery_review:
                # Validate the outbound view, after removing owner-local paths.
                validate_public_safe_value(projection)
        except Exception as exc:  # noqa: BLE001 - do not expose local projection failures.
            # The storage owner already exposes inspection and reviewed import.
            # Preserve its typed refusal, not private paths or migration payloads.
            if isinstance(exc, LocalCoordinationAuthorityUnavailable) and exc.code == "canonical_authority_required":
                self._send_error(
                    "This Goal needs a storage upgrade. Open its storage settings to review recovery.",
                    status=503,
                    error_code=exc.code,
                )
                return
            self._send_error(
                "LoopX status could not be projected for the workspace.",
                status=500,
                error_code=(
                    "workspace_status_access_denied"
                    if requested_goals is not None
                    and views is None
                    and _status_access_denied(exc)
                    else None
                ),
            )
            return
        self._send_json(projection)


def _compact_status_text(value: Any, *, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _compact_todo(item: dict[str, Any], *, protected_paths: list[Path]) -> dict[str, Any]:
    return {
        "todo_id": _compact_status_text(item.get("todo_id"), limit=100) or None,
        "role": _compact_status_text(item.get("role"), limit=40) or None,
        "status": _compact_status_text(item.get("status"), limit=40) or ("done" if item.get("done") else "open"),
        "priority": _compact_status_text(item.get("priority"), limit=60) or None,
        "text": _compact_status_text(
            redact_local_paths(str(item.get("text") or item.get("title") or ""), protected_paths=protected_paths)
        ),
        "action_kind": _compact_status_text(item.get("action_kind"), limit=100) or None,
        "task_class": _compact_status_text(item.get("task_class"), limit=100) or None,
        "claimed_by": _compact_status_text(item.get("claimed_by"), limit=100) or None,
        "evidence": _compact_status_text(
            redact_local_paths(str(item.get("evidence") or item.get("note") or ""), protected_paths=protected_paths)
        )
        or None,
    }


def _goal_todos(queue_item: dict[str, Any], *, protected_paths: list[Path]) -> list[dict[str, Any]]:
    asset = queue_item.get("project_asset") if isinstance(queue_item.get("project_asset"), dict) else {}
    todos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role in ("user", "agent"):
        group = queue_item.get(f"{role}_todos")
        if not isinstance(group, dict):
            group = asset.get(f"{role}_todos")
        items = group.get("items") if isinstance(group, dict) else []
        for raw in items if isinstance(items, list) else []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item.setdefault("role", role)
            compact = _compact_todo(item, protected_paths=protected_paths)
            key = str(compact.get("todo_id") or compact.get("text") or "")
            if key and key not in seen:
                seen.add(key)
                todos.append(compact)
    return todos[:40]


def build_chat_status_projection(
    *,
    registry: dict[str, Any],
    status_payload: dict[str, Any],
    selected_goal_id: str | None,
) -> dict[str, Any]:
    """Project queue-backed Goal status for the Chat status response."""
    from ..chat_server import _goal_public_context

    queue = status_payload.get("attention_queue")
    queue_items = queue.get("items") if isinstance(queue, dict) else []
    queue_by_goal = {
        str(item.get("goal_id") or ""): item
        for item in (queue_items if isinstance(queue_items, list) else []) if isinstance(item, dict)
    }
    goals: list[dict[str, Any]] = []
    for raw_goal in registry_goals(registry):
        goal_id = str(raw_goal.get("id") or "")
        context = _goal_public_context(registry, raw_goal)
        protected_paths = [context["project"], context["runtime_root"]]
        item = queue_by_goal.get(goal_id, {})
        asset = item.get("project_asset") if isinstance(item.get("project_asset"), dict) else {}
        todos = _goal_todos(item, protected_paths=protected_paths)
        open_todos = [todo for todo in todos if todo.get("status") not in {"done", "completed", "closed"}]
        top_todo = next((todo for todo in open_todos if todo.get("role") == "agent"), None) or (open_todos[0] if open_todos else None)
        evidence = [str(todo.get("evidence")) for todo in todos if todo.get("evidence")][:4]
        latest_validation = asset.get("latest_validation")
        if isinstance(latest_validation, dict) and latest_validation.get("summary"):
            evidence.insert(0, _compact_status_text(latest_validation.get("summary")))
        quota = item.get("quota") if isinstance(item.get("quota"), dict) else {}
        raw_gate = asset.get("gate") or item.get("status")
        raw_next_action = asset.get("next_action") or item.get("recommended_action")
        goals.append({
            "goal_id": goal_id,
            "title": context["title"],
            "objective": redact_local_paths(context["objective"], protected_paths=protected_paths),
            "status": _compact_status_text(item.get("status"), limit=100),
            "waiting_on": _compact_status_text(item.get("waiting_on"), limit=80) or None,
            "severity": _compact_status_text(item.get("severity"), limit=60),
            "gate": _compact_status_text(redact_local_paths(str(raw_gate or "clear"), protected_paths=protected_paths), limit=240),
            "next_action": _compact_status_text(
                redact_local_paths(str(raw_next_action or "Review the next bounded proposal."), protected_paths=protected_paths)
            ),
            "top_todo": top_todo,
            "todos": todos,
            "evidence": [_compact_status_text(redact_local_paths(value, protected_paths=protected_paths)) for value in evidence if value],
            "quota": {
                "state": _compact_status_text(quota.get("state"), limit=80) or None,
                "spent_slots": quota.get("spent_slots"),
                "allowed_slots": quota.get("allowed_slots"),
                "reason": _compact_status_text(quota.get("reason"), limit=240) or None,
            },
        })
    effective_selected = selected_goal_id or (goals[0]["goal_id"] if len(goals) == 1 else None)
    return {
        "ok": bool(status_payload.get("ok", True)),
        "schema_version": "loopx_chat_status_v0",
        "selected_goal_id": effective_selected,
        "goal_count": len(goals),
        "goals": goals,
    }


def build_bounded_chat_status_projection(
    *,
    registry: dict[str, Any],
    selected_goal_id: str | None,
) -> dict[str, Any]:
    """Build Chat's first-screen projection from bounded local state only."""
    items: list[dict[str, Any]] = []
    for raw_goal in registry_goals(registry):
        goal_id = str(raw_goal.get("id") or "")
        project = Path(str(raw_goal.get("repo") or ".")).expanduser().resolve()
        state_path = resolve_state_file(project, raw_goal.get("state_file"))
        state_text = ""
        if state_path is not None and state_path.is_file():
            try:
                state_text = state_path.read_text(encoding="utf-8")
            except OSError:
                state_text = ""
        structured = build_active_state_structured_projection(state_text, goal_id=goal_id)
        frontmatter = structured.get("frontmatter") if isinstance(structured.get("frontmatter"), dict) else {}
        todos = structured.get("todos") if isinstance(structured.get("todos"), dict) else {}
        user_todos = todos.get("user") if isinstance(todos.get("user"), dict) else {"items": [], "open_count": 0}
        agent_todos = todos.get("agent") if isinstance(todos.get("agent"), dict) else {"items": [], "open_count": 0}
        user_open = int(user_todos.get("open_count") or 0)
        agent_open = int(agent_todos.get("open_count") or 0)
        next_action = structured.get("next_action") if isinstance(structured.get("next_action"), dict) else {}
        next_action_text = _compact_status_text(next_action.get("first"))
        waiting_on = _compact_status_text(frontmatter.get("waiting_on"), limit=80) or (
            "user_or_controller" if user_open else ("codex" if agent_open else "")
        )
        gate = _compact_status_text(frontmatter.get("gate"), limit=120) or (
            "user_review_required" if user_open else "clear"
        )
        next_proposal = next_action_text or "Review the next bounded proposal."
        items.append({
            "goal_id": goal_id,
            "status": _compact_status_text(frontmatter.get("status"), limit=100)
            or _compact_status_text(raw_goal.get("status"), limit=100),
            "waiting_on": waiting_on or None,
            "severity": "action" if user_open or agent_open else "info",
            "recommended_action": next_proposal,
            "user_todos": user_todos,
            "agent_todos": agent_todos,
            "project_asset": {"gate": gate, "next_action": next_proposal, "user_todos": user_todos, "agent_todos": agent_todos},
        })
    return build_chat_status_projection(
        registry=registry,
        status_payload={"ok": True, "attention_queue": {"items": items}},
        selected_goal_id=selected_goal_id,
    )
