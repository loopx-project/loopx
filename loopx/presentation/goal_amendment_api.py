"""Operator-only amendment configuration/readback in the existing Goal settings.

Peer proposal/adoption/commit calls remain Agent CLI operations. A browser cannot
claim to be a peer simply by supplying an Agent id.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..chat import redact_local_paths
from ..feedback import validate_goal_id
from ..capabilities.todo_replan_cadence.machine_defaults import resolve_todo_replan_cadence_goal
from ..control_plane.goals.goal_amendment_runtime import execute_goal_amendment
from ..control_plane.goals.source_session_registry_state import current_goal_ref
from ..control_plane.projects.registry_codec import load_project_registry
from .goal_source_routing import source_registry_for_goal

CHAT_GOAL_AMENDMENT_PATH = "/api/chat/goal-amendment"



def _public(value: dict[str, Any]) -> dict[str, Any]:
    # Full canonical work is used by the owning replan reader, not dumped into
    # the settings page or treated as a second editable work list.
    result = dict(value)
    result.pop("work_items", None)
    if isinstance(result.get("current_projection"), dict):
        result["current_projection"] = _public(result["current_projection"])
    def redact(item: Any) -> Any:
        if isinstance(item, str):
            return redact_local_paths(item)
        if isinstance(item, list):
            return [redact(child) for child in item]
        if isinstance(item, dict):
            return {key: redact(child) for key, child in item.items()}
        return item
    return {key: redact(item) for key, item in result.items()}


class GoalAmendmentRequestMixin:
    server: Any
    path: str

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _read_json(self, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
        raise NotImplementedError

    def _amendment_source(self, goal_id: Any) -> Path:
        if not isinstance(goal_id, str):
            raise ValueError("Choose one registered Goal")
        validate_goal_id(goal_id)
        selected = getattr(self.server, "selected_goal_id", None)
        if selected and selected != goal_id:
            raise ValueError("Goal is outside this workspace")
        return source_registry_for_goal(self.server.registry_path, goal_id)

    def _amendment_inspect(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) - {"goal_id", "operation_id", "goal_instance_id"} or len(query.get("goal_id", [])) != 1 or not query["goal_id"][0].strip():
                raise ValueError("goal_id is required exactly once")
            goal_id = query["goal_id"][0].strip()
            source = self._amendment_source(goal_id)
            registry = load_project_registry(source)
            goal_ref, _ = current_goal_ref(registry, goal_id=goal_id, require_active=False)
            if "operation_id" in query or "goal_instance_id" in query:
                if len(query.get("operation_id", [])) != 1 or len(query.get("goal_instance_id", [])) != 1:
                    raise ValueError("Historical lookup requires one operation and exact Goal instance")
                result = execute_goal_amendment(registry_path=source, goal_id=goal_id, goal_instance_id=query["goal_instance_id"][0],
                    operation="receipt", actor_agent_id=None, parameters={"receipt_operation_id": query["operation_id"][0]},
                    replan_configuration_reader=resolve_todo_replan_cadence_goal)
            else:
                result = execute_goal_amendment(registry_path=source, goal_id=goal_ref["goal_id"],
                    goal_instance_id=goal_ref["goal_instance_id"], operation="inspect", actor_agent_id=None,
                    replan_configuration_reader=resolve_todo_replan_cadence_goal)
            self._send_json(_public(result))
        except (OSError, TypeError, ValueError) as exc:
            self._send_error(redact_local_paths(str(exc)), status=400, error_code="goal_amendment_unavailable")

    def _amendment_configure(self) -> None:
        try:
            body = self._read_json(max_bytes=256 * 1024)
            required = {"goal_id", "goal_instance_id", "operation_id", "parameters", "execute"}
            if not isinstance(body, dict) or not required <= set(body) or set(body) - required - {"preview_digest"} or not isinstance(body["parameters"], dict) or type(body["execute"]) is not bool:
                raise ValueError("Goal root configuration fields are invalid")
            if any(not isinstance(body[name], str) for name in ("goal_id", "goal_instance_id", "operation_id")):
                raise ValueError("Goal and operation identities must be strings")
            goal_id = body["goal_id"]
            source = self._amendment_source(goal_id)
            parameters = body["parameters"]
            if set(parameters) != {"permission_scope", "terminal_conditions", "policy", "expected_provider_revision"}:
                raise ValueError("Only initial root policy fields are accepted here")
            common = dict(registry_path=source, goal_id=goal_id, goal_instance_id=str(body["goal_instance_id"]), actor_agent_id=None,
                replan_configuration_reader=resolve_todo_replan_cadence_goal)
            current = execute_goal_amendment(**common, operation="inspect")
            if current.get("ok") is not True:
                self._send_json(_public(current))
                return
            original = current.get("source_commitments")
            if not isinstance(original, dict):
                raise ValueError("The original Goal commitments are unavailable; a source-owner migration is required")
            root = {"objective": original["objective"], "non_goals": original["non_goals"], "acceptance": original["acceptance"],
                "stop_conditions": [original["stop_condition"]], "permission_scope": parameters["permission_scope"],
                "terminal_conditions": parameters["terminal_conditions"]}
            request = {"root_intent": root, "policy": parameters["policy"], "expected_provider_revision": parameters["expected_provider_revision"],
                "operation_id": str(body["operation_id"])}
            result = execute_goal_amendment(**common, operation="configure", parameters=request)
            if result.get("ok") is not True:
                self._send_json(_public(result))
                return
            if body["execute"]:
                if not body.get("preview_digest") or result.get("intent_digest") != body["preview_digest"]:
                    raise ValueError("Root configuration changed or is unavailable; inspect and preview it again")
                result = execute_goal_amendment(**common, operation="configure", parameters=request, execute=True)
            self._send_json(_public(result))
        except (KeyError, OSError, TypeError, ValueError) as exc:
            self._send_error(redact_local_paths(str(exc)), status=400, error_code="goal_amendment_configuration_failed")
