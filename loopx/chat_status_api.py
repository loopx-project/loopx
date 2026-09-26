"""Loopback status route owned by the Chat HTTP surface."""

from __future__ import annotations

import errno
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from .chat import redact_local_paths
from .chat_goal_subagent_api import goal_subagent_configuration_enabled
from .chat_workspace_directory import workspace_goal_directory
from .codex_app_thread_activity import codex_thread_observers
from .control_plane.agents.host_thread_activity import attach_host_thread_activity
from .control_plane.effect_runtime import (
    EffectRuntimePermanentIOError,
    EffectRuntimeRemoteError,
    EffectRuntimeStartupError,
)
from .control_plane.runtime.public_safety import validate_public_safe_value
from .feedback import validate_goal_id
from .history import load_registry
from .registry import registry_goals
from .status import collect_status
from .status_server import parse_goal_activation_filter


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


class ChatStatusRequestMixin:
    """Serve the full dashboard status contract through the Chat origin."""

    server: Any

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _delivery_review(self) -> None:
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
            if views is not None or requested_goals is not None or delivery_review:
                registry = load_registry(self.server.registry_path)
                if goal_id and goal_id not in {
                    str(g.get("id")) for g in registry_goals(registry)
                }:
                    self._send_error("Goal is no longer registered.", status=404)
                    return
                directory = workspace_goal_directory(
                    registry, selected_goal_id=goal_id
                )
                if views == ["workspace-directory"]:
                    self._send_json(directory)
                    return
            projection = collect_status(
                registry_path=self.server.registry_path,
                runtime_root_override=self.server.runtime_root_override,
                scan_roots=self.server.scan_roots,
                limit=self.server.limit,
                goal_id=goal_id,
                include_public_boundary_scan=False,
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
                attach_host_thread_activity(
                    projection, observers=codex_thread_observers()
                )
            if delivery_review:
                if projection.get("ok") is not True:
                    self._send_error("Delivery review sources are unavailable.", status=503)
                    return
                item = next((row for row in projection.get("attention_queue", {}).get("items", [])
                             if row.get("goal_id") == goal_id), {})
                goal = next((row for row in projection.get("run_history", {}).get("goals", [])
                             if row.get("id") == goal_id), {})
                projection = {
                    "ok": True,
                    "goal_id": goal_id,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "graph": item.get("task_graph_projection"),
                    "acceptance": goal.get("acceptance_observation"),
                }
                validate_public_safe_value(projection)
            protected_paths = [self.server.registry_path, *self.server.scan_roots]
            projection = json.loads(
                redact_local_paths(
                    json.dumps(projection, ensure_ascii=False),
                    protected_paths=protected_paths,
                )
            )
        except Exception as exc:  # noqa: BLE001 - do not expose local projection failures.
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
