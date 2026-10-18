"""Operator configuration and cold per-Agent observations; never peer receipts."""
from __future__ import annotations

from typing import Any
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..chat import redact_local_paths
from ..control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
from ..control_plane.goals.direction_runtime import execute_goal_direction
from ..control_plane.goals.source_session_registry_state import current_goal_ref
from ..control_plane.projects.registry_codec import load_project_registry
from .goal_source_routing import source_registry_for_goal
from ..feedback import validate_goal_id

GOAL_DIRECTION_PATH = "/api/chat/goal-direction"


class GoalDirectionRequestMixin:
    server: Any
    path: str

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _read_json(self, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
        raise NotImplementedError

    def _direction_route(self, goal_id: Any) -> tuple[Path, dict[str, str]]:
        if not isinstance(goal_id, str):
            raise ValueError("Choose one registered Goal")
        validate_goal_id(goal_id)
        selected = getattr(self.server, "selected_goal_id", None)
        if selected and selected != goal_id:
            raise ValueError("Goal is outside this workspace")
        source = source_registry_for_goal(self.server.registry_path, goal_id)
        ref, _ = current_goal_ref(load_project_registry(source), goal_id=goal_id, require_active=False)
        return source, ref

    def _direction_inspect(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) - {"goal_id", "agent_id", "operation_id", "goal_instance_id"} or len(query.get("goal_id", [])) != 1:
                raise ValueError("One Goal is required")
            if any(len(values) != 1 or not values[0] for values in query.values()):
                raise ValueError("Direction query fields must occur once")
            source, ref = self._direction_route(query["goal_id"][0])
            if "operation_id" in query or "goal_instance_id" in query:
                if not {"operation_id", "goal_instance_id"} <= set(query) or "agent_id" in query:
                    raise ValueError("Historical operator lookup requires its exact operation and Goal instance")
                result = execute_goal_direction(registry_path=source, goal_id=ref["goal_id"], goal_instance_id=query["goal_instance_id"][0],
                    operation="receipt", parameters={"receipt_operation_id": query["operation_id"][0]})
            else:
                result = execute_goal_direction(registry_path=source, goal_id=ref["goal_id"], goal_instance_id=ref["goal_instance_id"],
                    operation="inspect", agent_id=query["agent_id"][0] if "agent_id" in query else None)
            self._send_json(result)
        except (OSError, TypeError, ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
            self._send_error("Direction source is unavailable. Use the registered Goal source and retry its current basis.", status=400)

    def _direction_configure(self) -> None:
        try:
            body = self._read_json(max_bytes=256 * 1024)
            required = {"goal_id", "goal_instance_id", "operation", "operation_id", "parameters", "execute"}
            if set(body) - {"preview_digest"} != required or type(body["execute"]) is not bool or not isinstance(body["parameters"], dict):
                raise ValueError("Direction operator fields are invalid")
            if body["operation"] not in {"configure", "disable"}:
                raise ValueError("Browser cannot act or acknowledge material consumption as an Agent")
            source, ref = self._direction_route(body["goal_id"])
            if body["goal_instance_id"] != ref["goal_instance_id"]:
                raise ValueError("Goal instance changed; inspect its current basis")
            expected = {"expected_provider_revision", "materials"} if body["operation"] == "configure" else {"expected_provider_revision"}
            if set(body["parameters"]) != expected or not isinstance(body["operation_id"], str):
                raise ValueError("Direction parameters are unsupported")
            params = {**body["parameters"], "operation_id": body["operation_id"]}
            if body["execute"] and body["operation"] == "configure":
                if not isinstance(body.get("preview_digest"), str):
                    raise ValueError("Review the material declaration before applying it")
                params["expected_authority_digest"] = body["preview_digest"]
            result = execute_goal_direction(registry_path=source, goal_id=ref["goal_id"], goal_instance_id=ref["goal_instance_id"],
                operation=body["operation"], parameters=params, execute=body["execute"])
            # No source path or body is returned. A write route has no Agent id,
            # so even a complete operator read cannot create a peer usage receipt.
            self._send_json(result)
        except (OSError, TypeError, ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
            self._send_error(redact_local_paths(str(exc)), status=400)
