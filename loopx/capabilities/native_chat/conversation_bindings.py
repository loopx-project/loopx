"""Private IO for Core-owned conversation context and audience grants.

Provider observations contain opaque App/owner references, not credentials.
The typed owner validates configuration, replacement, revocation, and use.
This file stores no conversation text and creates no Goal or model thread.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
import uuid
import hashlib

from ...chat_store import _atomic_write_json, _read_json
from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...file_lock import exclusive_file_lock


class ChatConversationBindings:
    def __init__(self, *, root: Path, project_contexts: Any,
                 observe: Callable[[str], dict[str, Any]]) -> None:
        self.path = root / "conversation-bindings.json"
        self.projects = project_contexts
        self.observe = observe
        self.controller: Any | None = None

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": "loopx_chat_conversation_bindings_v0", "revision": 0, "bindings": []}
        # An unreadable existing grant is not an empty configuration to overwrite.
        return _read_json(self.path)

    @staticmethod
    def _core(operation: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = effect_runtime_result(operation, params)
        except EffectRuntimeRejected as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(result, dict):
            raise ValueError("conversation authority returned an invalid result")
        return result

    def configure(self, *, transport_ref: str, project_ref: str,
                  executor_endpoint_id: str, context_kind: str = "project",
                  project_grant: str | None = None, goal_scope: str | None = None) -> dict[str, Any]:
        if goal_scope is not None and (context_kind != "steward" or goal_scope not in {"selected", "all_registered"}):
            raise ValueError("select a supported steward Goal scope")
        if project_grant is None:
            project_grant = self.projects.workspace_grant if context_kind == "project" and executor_endpoint_id == "codex" else "workspace_read"
        if project_grant not in {"workspace_read", "workspace_write"} or (context_kind != "project" and project_grant != "workspace_read"):
            raise ValueError("workspace write authorization is only available for project Chat")
        observation = self.observe(transport_ref)
        candidate = {
            "schema_version": "loopx_chat_conversation_binding_v0",
            "binding_id": uuid.uuid4().hex[:24], "transport_ref": transport_ref,
            "provider_ref": observation["provider_ref"], "operator_ref": observation["operator_ref"],
            "context_kind": context_kind, "project_ref": project_ref,
            "executor_endpoint_id": executor_endpoint_id,
            "grant": project_grant if context_kind == "project" else "portfolio_read", "enabled": True,
            **({"goal_ids": [], "goal_scope": goal_scope or "all_registered"} if context_kind == "steward" else {}),
        }
        with exclusive_file_lock(self.path, operation="configure_chat_conversation_binding"):
            current = self.read()
            previous = next((row for row in current["bindings"] if row["transport_ref"] == transport_ref), None)
            if previous and all(previous.get(key) == candidate.get(key) for key in ["context_kind", "project_ref", "executor_endpoint_id", "provider_ref", "operator_ref", "grant"]):
                if previous.get("agent_targets"):
                    candidate["agent_targets"] = previous["agent_targets"]
            if (context_kind == "steward" and previous and all(previous.get(key) == candidate.get(key)
                    for key in ["context_kind", "project_ref", "executor_endpoint_id", "provider_ref", "operator_ref"])):
                candidate["goal_ids"] = previous["goal_ids"]
                # Existing installations retain their exact audience and scope
                # until the local operator explicitly changes it.
                if goal_scope is None:
                    candidate.pop("goal_scope", None)
                    if "goal_scope" in previous:
                        candidate["goal_scope"] = previous["goal_scope"]
                candidate["binding_id"] = previous["binding_id"]
            result = self._core("collaboration.conversation.binding", {
                "current": current, "expected_revision": current.get("revision"), "operation": "configure",
                "binding": candidate, "observation": observation, "available_projects": self.projects.available(),
            })
            proposed = next(row for row in result["state"]["bindings"] if row["transport_ref"] == transport_ref)
            channels = self.delivery_channels(proposed) if goal_scope is not None else []
            for channel in channels:
                self.ensure_delivery_scope({"binding": proposed, "channel_id": channel}, execute=False)
            # Publish the advertised scope only after every known source policy
            # has applied and verified. On IO failure the old binding remains;
            # retrying the explicit scope operation converges any earlier source
            # writes without making ordinary admission undo a manual restriction.
            for channel in channels:
                self.ensure_delivery_scope({"binding": proposed, "channel_id": channel}, if_absent=False)
            if result["changed"]:
                _atomic_write_json(self.path, result["state"])
            readback = self.read()
            if readback != result["state"]:
                raise OSError("conversation binding publication did not verify")
            binding = next(row for row in readback["bindings"] if row["transport_ref"] == transport_ref)
            return binding

    def disconnect(self, binding_id: str, *, expected_revision: int) -> dict[str, Any]:
        with exclusive_file_lock(self.path, operation="disconnect_chat_conversation_binding"):
            result = self._core("collaboration.conversation.binding", {
                "current": self.read(), "expected_revision": expected_revision,
                "operation": "disconnect", "binding_id": binding_id,
            })
            if result["changed"]:
                _atomic_write_json(self.path, result["state"])
            if self.read() != result["state"]:
                raise OSError("conversation binding revocation did not verify")
            return dict(result["state"])

    def resolve(self, *, binding_id: str, source_ref: str, sender_ref: str,
                private_human_message: bool, session_context: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.read()
        row = next((item for item in current.get("bindings", []) if item.get("binding_id") == binding_id), None)
        if row is None:
            raise ValueError("conversation binding is no longer authorized")
        registry_scope = {}
        if row.get("goal_scope") == "all_registered":
            registry_scope["available_goal_ids"] = self.goal_scope_ids(row)
        return self._core("collaboration.conversation.bound_context", {
            "current": current, "binding_id": binding_id, "source_ref": source_ref, "sender_ref": sender_ref,
            "private_human_message": private_human_message, "observation": self.observe(row["transport_ref"]),
            "available_projects": self.projects.available(),
            **registry_scope,
            **({"session_context": session_context} if session_context is not None else {}),
        })

    def goal_scope_ids(self, binding: dict[str, Any]) -> list[str]:
        if binding.get("goal_scope") != "all_registered":
            return list(binding.get("goal_ids", []))
        from ...registry import load_registry
        if self.controller is None or self.controller.registry_path is None:
            raise ValueError("the steward registry is unavailable")
        return [goal["id"] for goal in load_registry(self.controller.registry_path).get("goals", [])]

    def delivery_channels(self, binding: dict[str, Any]) -> list[str]:
        if self.controller is None:
            return []
        from ..manager_context import POLICY_SCHEMA, _root
        path = _root(self.controller.coordination_runtime_root) / "policy.json"
        if not path.exists():
            return []
        policy = _read_json(path)
        if policy.get("schema_version") != POLICY_SCHEMA or not isinstance(policy.get("sources"), dict):
            raise ValueError("invalid manager policy")
        prefix = f"manager.external.native.{binding['binding_id']}."
        return [channel for channel, source in policy["sources"].items()
                if channel.startswith(prefix) and isinstance(source, dict)
                and binding["operator_ref"] in source.get("sender_ids", [])]

    def ensure_delivery_scope(self, selected: dict[str, Any], *, if_absent: bool = True, execute: bool = True) -> None:
        binding = selected["binding"]
        if binding.get("goal_scope") not in {"all_registered", "selected"} or self.controller is None:
            return
        from ..manager_context import configure_delivery_scope
        configure_delivery_scope(self.controller.coordination_runtime_root,
            channel=selected["channel_id"], local_delivery_scope=binding["goal_scope"],
            sender_id=binding["operator_ref"], execute=execute, if_absent=if_absent)

    def session_context(self, saved: dict[str, Any]) -> dict[str, Any]:
        return self.resolve(binding_id=saved["binding_id"], source_ref=saved["source_ref"],
                            sender_ref=saved["operator_ref"], private_human_message=True, session_context=saved)

    def manager_runtime_owner(self, saved: dict[str, Any], channel_id: str) -> bool:
        """Fresh Core audience proof; saved Session text is never authority."""
        selected = self.session_context(saved)
        if selected["channel_id"] != channel_id:
            raise ValueError("bound manager audience changed")
        return selected["owner_manager_audience"] is True

    def steward_scope(self, session: dict[str, Any]) -> list[str] | None:
        saved = session.get("steward_context")
        if not isinstance(saved, dict):
            return None
        selected = self.session_context(saved)
        if session.get("goal_id") != "loopx-manager" or session.get("channel_id") != selected["channel_id"]:
            raise ValueError("bound steward audience changed")
        return list(selected["context"]["goal_ids"])

    def adopt_created_goal(self, *, binding_id: str, source: dict[str, Any], proposal: dict[str, Any],
                          goal: dict[str, Any]) -> None:
        selected = self.resolve(binding_id=binding_id, **source)
        with exclusive_file_lock(self.path, operation="adopt_steward_created_goal"):
            current = self.read()
            result = self._core("collaboration.conversation.binding", {
                "current": current, "expected_revision": current["revision"], "operation": "adopt_created_goal",
                "binding_id": binding_id, "context": selected["context"], "proposal": proposal,
                "goal": {"goal_id": goal["id"], "workspace_path": str(Path(goal["repo"]).resolve()),
                         "creation_operation_id": goal.get("creation_operation_id")},
            })
            if result["changed"]:
                _atomic_write_json(self.path, result["state"])
            if self.read() != result["state"]:
                raise OSError("steward scope publication did not verify")

    def agent_observation(self, session_id: str) -> dict[str, Any]:
        """Read only the explicitly selected canonical attached Session/Goal.

        No host history, remote thread database or prior identity is discovered.
        """
        from ...attached_session import require_current_attached_session, _require_bound_host
        from ...registry import find_registry_goal, load_registry
        if self.controller is None or self.controller.registry_path is None:
            raise ValueError("the attached Session registry is unavailable")
        session = require_current_attached_session(store=self.controller.store,
            registry_path=self.controller.registry_path, session_id=session_id)
        registry = load_registry(self.controller.registry_path)
        goal = find_registry_goal(registry, str(session.get("goal_id") or ""))
        if goal is None or not goal.get("repo"):
            raise ValueError("the exact Agent workspace is unavailable")
        _require_bound_host(goal=goal, agent_id=session["agent_id"], host_surface=session["host_surface"],
                            host_session_id=session["upstream_thread_id"])
        return {"session": {key: session.get(key) for key in ["session_id", "goal_id", "goal_instance_id",
                    "agent_id", "executor_endpoint_id", "session_mode", "status", "external_conversation_binding_id"]},
                "goal": {"goal_id": goal["id"], "workspace_path": str(Path(goal["repo"]).resolve()),
                    "registered_agents": (goal.get("coordination") or {}).get("registered_agents", [])},
                "host_binding_verified": True, "host_ref": _host_ref(session),
                "host_audience_binding_ids": _host_audiences(self.controller.store, session)}

    def agent_candidates(self, binding_id: str) -> list[dict[str, Any]]:
        row = next(item for item in self.read()["bindings"] if item["binding_id"] == binding_id)
        if row["context_kind"] != "project" or self.controller is None:
            return []
        project = next((item for item in self.projects.available() if item["project_ref"] == row["project_ref"]), None)
        if project is None:
            return []
        candidates = []
        for session in self.controller.store.list_sessions():
            if session.get("session_mode") != "attached_host" or session.get("status") == "closed":
                continue
            try:
                observed = self.agent_observation(session["session_id"])
                if (observed["session"]["status"] in {"ready", "busy"}
                        and observed["goal"]["workspace_path"] == project["workspace_path"]
                        and observed["session"].get("external_conversation_binding_id") in {None, binding_id}
                        and all(owner == binding_id for owner in observed["host_audience_binding_ids"])):
                    candidates.append(observed["session"])
            except (ValueError, OSError, KeyError):
                continue
        return candidates[:16]

    def change_agent_target(self, *, binding_id: str, expected_revision: int,
                            session_id: str | None = None, target_ref: str | None = None) -> dict[str, Any]:
        with exclusive_file_lock(self.path, operation="configure_conversation_agent_target"):
            current = self.read()
            row = next(item for item in current["bindings"] if item["binding_id"] == binding_id)
            params = {"current": current, "expected_revision": expected_revision, "binding_id": binding_id,
                      "observation": self.observe(row["transport_ref"]), "available_projects": self.projects.available()}
            if session_id is not None:
                observed = self.agent_observation(session_id)
                target = {key: observed["session"].get(key) for key in ["session_id", "goal_id", "goal_instance_id",
                    "agent_id", "executor_endpoint_id"]}
                params.update(operation="grant_agent_target", target={"target_ref": uuid.uuid4().hex[:24], "host_ref": observed["host_ref"], **target},
                              target_observation=observed)
            else:
                params.update(operation="revoke_agent_target", target_ref=target_ref)
            result = self._core("collaboration.conversation.binding", params)
            if result["changed"]:
                if session_id is not None:
                    self.controller.store.update_session(session_id, external_conversation_binding_id=binding_id)
                _atomic_write_json(self.path, result["state"])
            if self.read() != result["state"]:
                raise OSError("Agent target grant publication did not verify")
            return result["state"]

    def resolve_agent_target(self, selected: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        return self._core("collaboration.conversation.agent_target", {"binding": selected["binding"],
            "context": selected["context"], "target": target,
            "target_observation": self.agent_observation(target["session_id"])})


def validate_external_agent_turn(*, store: Any, session: dict[str, Any],
                                 goal: dict[str, Any] | None, turn: dict[str, Any]) -> None:
    """Called under the grant and canonical queue fences before host claim.

    The owner-local grant persists independently of a live provider connection.
    Admission and result delivery also verify the App/owner with the provider.
    """
    saved = turn.get("external_agent_target")
    if saved is None:
        return
    from ...attached_session import _require_bound_host
    if not isinstance(saved, dict) or turn.get("origin") != "lark" or goal is None:
        raise ValueError("the attached external audience is unavailable")
    context, target = saved["context"], saved["target"]
    if not Path(goal["repo"]).is_dir():
        raise ValueError("the attached Agent workspace is unavailable")
    state = _read_json(store.root / "conversation-bindings.json")
    selected = next((row for row in state["bindings"] if row["binding_id"] == context["binding_id"]), None)
    if selected is None:
        raise ValueError("the Agent audience grant was revoked")
    _require_bound_host(goal=goal, agent_id=session["agent_id"], host_surface=session["host_surface"],
                        host_session_id=session["upstream_thread_id"])
    ChatConversationBindings._core("collaboration.conversation.agent_target", {"binding": selected,
        "context": context, "target": target, "target_observation": {"session": session,
            "goal": {"goal_id": goal["id"], "workspace_path": str(Path(goal["repo"]).resolve()),
                     "registered_agents": (goal.get("coordination") or {}).get("registered_agents", [])},
            "host_binding_verified": True, "host_ref": _host_ref(session),
            "host_audience_binding_ids": _host_audiences(store, session)}})


def _host_ref(session: dict[str, Any]) -> str:
    return hashlib.sha256(f"{session['host_surface']}\0{session['upstream_thread_id']}".encode()).hexdigest()[:24]


def _host_audiences(store: Any, session: dict[str, Any]) -> list[str]:
    """Content-free stamps in this canonical store, including closed Sessions."""
    owners = set()
    for row in store.list_sessions():
        candidate = store.load_session(row["session_id"])
        if (candidate and candidate.get("host_surface") == session["host_surface"]
                and candidate.get("upstream_thread_id") == session["upstream_thread_id"]
                and candidate.get("external_conversation_binding_id")):
            owners.add(candidate["external_conversation_binding_id"])
    return sorted(owners)
