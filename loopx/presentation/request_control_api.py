"""Source-owner request controls on the conversation that created the request."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..chat import redact_local_paths
from ..feedback import validate_goal_id
from ..control_plane.collaboration import conversation_scope
from ..control_plane.collaboration.goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle
from ..control_plane.collaboration.inbox import _entry, _read, _root
from ..control_plane.collaboration.request_amendment import control_request
from ..control_plane.collaboration.source_chat_observation import is_source_chat_host
from ..control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
from ..control_plane.goals.goal_ref_validation import exact_goal_ref
from ..control_plane.projects.registry_codec import load_project_registry
from ..capabilities.manager_context import target_authority
from ..capabilities.todo_replan_cadence.machine_defaults import resolve_todo_replan_cadence_goal
from ..control_plane.collaboration.source_chat_observation import original_delivery_facts
from .goal_source_routing import source_registry_for_goal

REQUEST_CONTROL_PATH = "/api/chat/request-control"
_ROUTING = {"goal_id", "goal_instance_id", "agent_id", "request_id", "session_id", "turn_id"}


def _operator_payload(value: Any) -> Any:
    """Preserve the authorized editable brief; redact diagnostic locations."""
    if isinstance(value, str):
        return redact_local_paths(value)
    if isinstance(value, list):
        return [_operator_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: item if key == "brief" else _operator_payload(item) for key, item in value.items()}
    return value


class RequestControlMixin:
    server: Any
    path: str

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raise NotImplementedError

    def _send_error(self, message: str, **kwargs: Any) -> None:
        raise NotImplementedError

    def _read_json(self, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
        raise NotImplementedError

    def _request_control_source(self, body: dict[str, Any]) -> dict[str, Any]:
        if any(not isinstance(body.get(key), str) or not body[key] for key in _ROUTING):
            raise ValueError("original conversation and exact request identities are required")
        validate_goal_id(body["goal_id"])
        selected = getattr(self.server, "selected_goal_id", None)
        if selected and selected != body["goal_id"]:
            raise ValueError("request is outside this Goal workspace")
        registry = source_registry_for_goal(self.server.registry_path, body["goal_id"])
        configured = load_project_registry(registry).get("common_runtime_root")
        if not isinstance(configured, str) or not configured:
            raise ValueError("request source runtime is unavailable")
        root = Path(configured).expanduser().resolve()
        ref = exact_goal_ref(body["goal_id"], body["goal_instance_id"])
        store = self.server.chat_store
        session = store.load_session(body["session_id"])
        turn = store.load_turn(body["session_id"], body["turn_id"])
        if not session or not turn or not conversation_scope(session, origin=turn.get("origin"))["private_conversation"]:
            raise ValueError("request control requires its original private conversation")
        with collaboration_goal_scope(registry, goal_id=body["goal_id"], agents=(), caller_goal_ref=ref) as scope:
            row = _entry(root, body["goal_id"], body["agent_id"], body["request_id"], scope=scope)
            route = _read(_root(root) / "roundtrips" / (body["request_id"] + ".json"))
            if (row.get("source_kind") == "peer" or route.get("session_id") != session["session_id"]
                    or route.get("client_turn_id") != turn["client_turn_id"]
                    or not is_source_chat_host(root, route, store)):
                raise ValueError("browser cannot control another source or impersonate a requesting Agent")
            grant = target_authority(root, session=session, turn=turn,
                target={"goal_id": body["goal_id"], "agent_id": body["agent_id"]})
            decide_collaboration_lifecycle(scope, operation="original_request_inspect", record=row, route=route,
                initial_delivery=original_delivery_facts(row, route, turn, source_id=grant["source_id"]))
        return dict(registry_path=registry, runtime_root=root, goal_ref=ref, receiver_agent_id=body["agent_id"],
            request_id=body["request_id"], actor_agent_id=None)

    def _request_control_read(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if set(query) != _ROUTING or any(len(value) != 1 for value in query.values()):
                raise ValueError("request routing fields are required exactly once")
            result = control_request(**self._request_control_source({key: values[0] for key, values in query.items()}), operation="inspect",
                replan_configuration_reader=resolve_todo_replan_cadence_goal)
            self._send_json(_operator_payload(result))
        except (OSError, ValueError, TypeError, KeyError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
            self._send_error(redact_local_paths(str(exc)), status=400, error_code="request_control_unavailable")

    def _request_control_write(self) -> None:
        try:
            body = self._read_json(max_bytes=128000)
            if (set(body) != _ROUTING | {"operation", "parameters", "execute"}
                    or body["operation"] not in {"revise", "cancel", "recover"}
                    or not isinstance(body["parameters"], dict) or type(body["execute"]) is not bool):
                raise ValueError("only original source correction, cancellation or recovery is accepted here")
            result = control_request(**self._request_control_source(body), operation=body["operation"],
                replan_configuration_reader=resolve_todo_replan_cadence_goal,
                parameters=body["parameters"], execute=body["execute"])
            self._send_json(_operator_payload(result))
        except (OSError, ValueError, TypeError, KeyError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
            code = getattr(exc, "diagnostic_code", "request_control_unavailable")
            self._send_error(redact_local_paths(str(exc)), status=409 if code == "request_basis_changed" else 400, error_code=code)
