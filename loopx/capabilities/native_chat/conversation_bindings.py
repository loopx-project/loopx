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

from ...chat_store import _atomic_write_json, _read_json
from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...file_lock import exclusive_file_lock


class ChatConversationBindings:
    def __init__(self, *, root: Path, project_contexts: Any,
                 observe: Callable[[str], dict[str, Any]]) -> None:
        self.path = root / "conversation-bindings.json"
        self.projects = project_contexts
        self.observe = observe

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
                  executor_endpoint_id: str, context_kind: str = "project") -> dict[str, Any]:
        observation = self.observe(transport_ref)
        candidate = {
            "schema_version": "loopx_chat_conversation_binding_v0",
            "binding_id": uuid.uuid4().hex[:24], "transport_ref": transport_ref,
            "provider_ref": observation["provider_ref"], "operator_ref": observation["operator_ref"],
            "context_kind": context_kind, "project_ref": project_ref,
            "executor_endpoint_id": executor_endpoint_id,
            "grant": "workspace_read" if context_kind == "project" else "portfolio_read", "enabled": True,
            **({"goal_ids": []} if context_kind == "steward" else {}),
        }
        with exclusive_file_lock(self.path, operation="configure_chat_conversation_binding"):
            current = self.read()
            previous = next((row for row in current["bindings"] if row["transport_ref"] == transport_ref), None)
            if (context_kind == "steward" and previous and all(previous.get(key) == candidate.get(key)
                    for key in ["context_kind", "project_ref", "executor_endpoint_id", "provider_ref", "operator_ref"])):
                candidate["goal_ids"] = previous["goal_ids"]
            result = self._core("collaboration.conversation.binding", {
                "current": current, "expected_revision": current.get("revision"), "operation": "configure",
                "binding": candidate, "observation": observation, "available_projects": self.projects.available(),
            })
            if result["changed"]:
                _atomic_write_json(self.path, result["state"])
            readback = self.read()
            if readback != result["state"]:
                raise OSError("conversation binding publication did not verify")
            return next(row for row in readback["bindings"] if row["transport_ref"] == transport_ref)

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
        return self._core("collaboration.conversation.bound_context", {
            "current": current, "binding_id": binding_id, "source_ref": source_ref, "sender_ref": sender_ref,
            "private_human_message": private_human_message, "observation": self.observe(row["transport_ref"]),
            "available_projects": self.projects.available(),
            **({"session_context": session_context} if session_context is not None else {}),
        })

    def session_context(self, saved: dict[str, Any]) -> dict[str, Any]:
        return self.resolve(binding_id=saved["binding_id"], source_ref=saved["source_ref"],
                            sender_ref=saved["operator_ref"], private_human_message=True, session_context=saved)

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
