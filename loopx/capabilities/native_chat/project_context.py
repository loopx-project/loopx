"""Filesystem observations for the shared project conversation owner.

Only explicitly configured Chat workspace roots are eligible. This is neither
a Goal registry nor a transport-owned Session authority. Each use observes the
roots again, so a missing root or retargeted symlink cannot retain a Session's grant.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result


PROJECT_CONVERSATION_OBJECTIVE = (
    "Have an ordinary conversation about the selected workspace. Preserve this "
    "Session's context. The workspace grant permits reading only; it does not "
    "authorize edits, a LoopX Goal, scheduling, delegation or portfolio discovery. "
    "Do not create an implicit Goal or borrow the global manager identity."
)


class ChatProjectContexts:
    def __init__(self, roots: list[Path]) -> None:
        # Remember the owner's spelling as well as its initial canonical target.
        # A later symlink retarget must not redirect an accepted Session.
        self.roots = [(root.expanduser().absolute(), root.expanduser().resolve()) for root in roots]
        self.conversation_bindings: Any | None = None

    def available(self) -> list[dict[str, str]]:
        contexts = {}
        for declared, canonical in self.roots:
            if not declared.is_dir() or declared.resolve() != canonical:
                continue
            ref = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()[:24]
            contexts[ref] = {"kind": "project_workspace", "project_ref": ref,
                             "workspace_path": str(canonical), "audience": "local_owner",
                             "grant": "workspace_read"}
        return list(contexts.values())

    def resolve(self, project_ref: str, *, session_context: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return effect_runtime_result("collaboration.project.context", {
                "project_ref": project_ref, "available": self.available(),
                **({"session_context": session_context} if session_context is not None else {}),
            })
        except EffectRuntimeRejected as exc:
            raise ValueError(str(exc)) from exc

    def session_context(self, session: dict[str, Any]) -> dict[str, Any]:
        steward = session.get("steward_context")
        if isinstance(steward, dict):
            if self.conversation_bindings is None:
                raise ValueError("bound steward authority is unavailable")
            selected = self.conversation_bindings.session_context(steward)
            if session.get("goal_id") != "loopx-manager" or session.get("channel_id") != selected["channel_id"]:
                raise ValueError("steward audience mismatch")
            from ...chat_manager import MANAGER_AGENT_OBJECTIVE
            return {"project": Path(selected["context"]["workspace_path"]), "objective": MANAGER_AGENT_OBJECTIVE,
                    "title": "Steward"}
        saved = session.get("project_context")
        if not isinstance(saved, dict) or session.get("goal_id") is not None:
            raise ValueError("invalid ordinary project Session")
        if saved.get("audience") == "bound_owner":
            if self.conversation_bindings is None:
                raise ValueError("bound project conversation authority is unavailable")
            selected = self.conversation_bindings.session_context(saved)
        else:
            selected = self.resolve(str(saved.get("project_ref") or ""), session_context=saved)
        if session.get("channel_id") != selected["channel_id"]:
            raise ValueError("project conversation channel mismatch")
        return {"project": Path(selected["context"]["workspace_path"]),
                "objective": PROJECT_CONVERSATION_OBJECTIVE,
                "title": Path(selected["context"]["workspace_path"]).name}

    def open_bound(self, binding_id: str, source: dict[str, Any], *, executor: str, channel_id: str | None) -> dict[str, Any]:
        if self.conversation_bindings is None:
            raise ValueError("bound conversation authority is unavailable")
        selected = self.conversation_bindings.resolve(binding_id=binding_id, **source)
        if selected["binding"]["executor_endpoint_id"] != executor:
            raise ValueError("executor does not match the conversation grant")
        if channel_id is not None and channel_id != selected["channel_id"]:
            raise ValueError("bound conversation channel mismatch")
        steward = selected["binding"]["context_kind"] == "steward"
        session = {"goal_id": "loopx-manager" if steward else None, "channel_id": selected["channel_id"],
                   "project_context": None if steward else selected["context"],
                   "steward_context": selected["context"] if steward else None}
        return {**session, **self.session_context(session)}

    @staticmethod
    def initialize_bound_scope(store, session):
        steward = session.get("steward_context")
        if not steward:
            return session
        from ...chat_manager_context import manager_authorization_scope_id
        return store.update_session(session["session_id"], manager_authorization_scope_id=manager_authorization_scope_id(
            steward["goal_ids"], runtime_root=store.root.parent, channel_id=session["channel_id"]))
