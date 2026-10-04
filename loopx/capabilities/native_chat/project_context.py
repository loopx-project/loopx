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
        saved = session.get("project_context")
        if not isinstance(saved, dict) or session.get("goal_id") is not None:
            raise ValueError("invalid ordinary project Session")
        selected = self.resolve(str(saved.get("project_ref") or ""), session_context=saved)
        if session.get("channel_id") != selected["channel_id"]:
            raise ValueError("project conversation channel mismatch")
        return {"project": Path(selected["context"]["workspace_path"]),
                "objective": PROJECT_CONVERSATION_OBJECTIVE,
                "title": Path(selected["context"]["workspace_path"]).name}
