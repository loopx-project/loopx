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

    def _request_inventory_scope(self, session_id):
        from ..capabilities.manager_context import evidence_goal_scope

        controller = self.server.runtime_controller
        session = self.server.chat_store.load_session(session_id)
        if not session:
            raise ValueError("original conversation is unavailable")
        scope = conversation_scope(session)
        if scope["kind"] not in {"owner_portfolio", "owner_goal"} and not scope.get("bound_steward"):
            raise ValueError("request inventory requires an authorized steward conversation")
        if session.get("steward_context") is not None:
            controller.project_contexts.session_context(session)
        selected = getattr(self.server, "selected_goal_id", None)
        if selected and scope.get("goal_ids") != [selected]:
            raise ValueError("request inventory is outside this Goal workspace")
        if scope["private_conversation"]:
            goals, core = scope["goal_ids"], None
        else:
            if controller.manager_scope_resolver is None:
                raise ValueError("current steward read scope is unavailable")
            goals = controller.manager_scope_resolver(session)
            if not isinstance(goals, list):
                raise ValueError("current steward read scope is unavailable")
            core = evidence_goal_scope(controller.coordination_runtime_root, session["channel_id"],
                registry_path=self.server.registry_path) or []
        return session, goals, core

    def _request_inventory_read(self) -> None:
        from ..control_plane.collaboration.conversation_inventory import read_conversation_requests

        try:
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            if (set(query) - {"session_id", "view", "offset"} or "session_id" not in query
                    or any(len(values) != 1 for values in query.values())):
                raise ValueError("select one original conversation")
            view = query.get("view", ["unfinished"])[0]
            if view not in {"unfinished", "history"}:
                raise ValueError("invalid request inventory view")
            session, goals, core = self._request_inventory_scope(query["session_id"][0])
            controller = self.server.runtime_controller
            def validate_source(source):
                observed, permitted, _ = self._request_inventory_scope(source["session_id"])
                if observed["channel_id"] != session["channel_id"] or permitted != goals:
                    raise ValueError("original conversation authorization changed")
            result = read_conversation_requests(controller.coordination_runtime_root, self.server.registry_path,
                self.server.chat_store, session, goal_ids=goals, core_goal_ids=core,
                validate_source_session=validate_source, history=view == "history", offset=int(query.get("offset", ["0"])[0]))
            current, current_goals, current_core = self._request_inventory_scope(session["session_id"])
            if (current["channel_id"], current.get("steward_context"), current_goals, current_core) != (
                    session["channel_id"], session.get("steward_context"), goals, core):
                raise ValueError("conversation authorization changed during read")
            self._send_json(_operator_payload(result))
        except (OSError, ValueError, TypeError, KeyError, EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
            self._send_error(redact_local_paths(str(exc)), status=400, error_code="request_inventory_unavailable")

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
