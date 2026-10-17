"""Loopback-only, transient UI adapter to the existing Decision Context owner."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from ..capabilities.decision_context.profile import resolve_inline_decision_context_activation
from ..capabilities.decision_context.runtime import recall_inline_decision_context
from ..control_plane.projects.registry_codec import load_registry
from ..feedback import validate_goal_id
from ..registry import find_registry_goal
from .goal_source_routing import source_registry_for_goal

DECISION_CONTEXT_PATH = "/api/chat/decision-context"


class DecisionContextRequestMixin:
    server: Any
    path: str

    def _decision_context_request(self) -> None:
        try:
            if urlparse(self.path).query:
                raise ValueError("Use a private request body")
            body = self._read_json(max_bytes=256 * 1024)
            required = {"action", "goal_id", "agent_id", "profile"}
            allowed = required | {"query", "context_scope_ref", "max_results"}
            if not required <= set(body) or set(body) - allowed:
                raise ValueError("Unsupported fields")
            action = body["action"]
            if action not in {"inspect", "recall"}:
                raise ValueError("Unsupported operation")
            if action == "inspect" and set(body) != required:
                raise ValueError("Inspection cannot recall")
            goal_id, agent_id = body["goal_id"], body["agent_id"]
            if not isinstance(goal_id, str) or not isinstance(agent_id, str):
                raise ValueError("A Goal and Agent are required")
            validate_goal_id(goal_id)
            selected = getattr(self.server, "selected_goal_id", None)
            if selected and selected != goal_id:
                raise ValueError("Goal is outside this workspace")
            source = source_registry_for_goal(self.server.registry_path, goal_id)
            if find_registry_goal(load_registry(source), goal_id) is None:
                raise ValueError("Goal is not registered")
            profile = body["profile"]
            if profile is not None and not isinstance(profile, dict):
                raise ValueError("Profile must be an object")
            if action == "inspect":
                activation, _ = resolve_inline_decision_context_activation(
                    goal_id=goal_id, agent_id=agent_id, profile_payload=profile,
                )
                self._send_json(activation)
                return
            if not {"query", "context_scope_ref"} <= set(body) or any(
                not isinstance(body[field], str) for field in ("query", "context_scope_ref")
            ):
                raise ValueError("A bounded query and scope are required")
            self._send_json(recall_inline_decision_context(
                goal_id=goal_id,
                agent_id=agent_id,
                profile_payload=profile,
                context_scope_ref=body["context_scope_ref"],
                query=body["query"],
                # The private query never becomes the public receipt summary.
                query_summary="Decision evidence requested in the local workspace",
                observed_at=datetime.now(timezone.utc).isoformat(),
                max_results=body.get("max_results"),
                runtime_root=self.server.runtime_root,
            ))
        except (OSError, TypeError, ValueError):
            # Never echo imported credentials, provider config, query or paths.
            self._send_error(
                "Decision Context request unavailable. Check the selected Goal, Agent and profile, then retry.",
                status=400,
            )
