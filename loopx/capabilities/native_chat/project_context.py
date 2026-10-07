"""Filesystem observations for the shared project conversation owner.

Only explicitly configured Chat workspace roots are eligible. This is neither
a Goal registry nor a transport-owned Session authority. Each use observes the
roots again, so a missing root or retargeted symlink cannot retain a Session's grant.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...presentation.answer_instruction import conversation_answer_instruction


PROJECT_CONVERSATION_OBJECTIVE = (
    "Have an ordinary conversation about the selected workspace. Preserve this "
    "Session's context. The workspace grant permits reading only; it does not "
    "authorize edits, a LoopX Goal, scheduling, delegation or portfolio discovery. "
    "Do not create an implicit Goal or borrow the global manager identity."
)

PROJECT_WORK_OBJECTIVE = (
    "Carry out the owner's explicit requests in the selected workspace, preserving "
    "this Session's context. The explicit workspace write grant permits bounded "
    "file edits and project workflows. Read and follow the workspace AGENTS.md "
    "and applicable project skills. Use existing typed owners for durable state; "
    "do not bypass material authority, intake, ranking or readback gates. The grant "
    "does not create a LoopX Goal, scheduling, delegation or portfolio access. "
    "Do not create an implicit Goal or borrow the global manager identity."
)


class ChatProjectContexts:
    def __init__(self, roots: list[Path], *, workspace_grant: str = "workspace_write",
                 filesystem_scope: str = "host_default") -> None:
        if workspace_grant not in {"workspace_read", "workspace_write"}:
            raise ValueError("unsupported project workspace grant")
        self.workspace_grant = workspace_grant
        if filesystem_scope not in {"host_default", "workspace_only"}:
            raise ValueError("unsupported project filesystem scope")
        self.filesystem_scope = filesystem_scope
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
                             "grant": self.workspace_grant,
                             **({"filesystem_scope": "workspace_only"}
                                if self.filesystem_scope == "workspace_only" else {})}
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
        objective = PROJECT_WORK_OBJECTIVE if selected["context"]["grant"] == "workspace_write" else PROJECT_CONVERSATION_OBJECTIVE
        return {"project": Path(selected["context"]["workspace_path"]),
                "objective": objective + " " + conversation_answer_instruction(),
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
    def initialize_bound_scope(store, session, *, runtime_root=None):
        steward = session.get("steward_context")
        if not steward:
            return session
        from ...chat_manager_context import manager_authorization_scope_id
        return store.update_session(session["session_id"], manager_authorization_scope_id=manager_authorization_scope_id(
            steward["goal_ids"], runtime_root=runtime_root or store.root.parent, channel_id=session["channel_id"]))


def coordination_runtime_root(registry_path: Path | None, chat_root: Path) -> Path:
    """Registered work/inboxes follow the registry; Chat keeps its own storage.

    A Chat storage override must not create another Goal/Agent inbox authority.
    Fixtures and ordinary project Chat without a registry keep their local root.
    """
    if registry_path is None or not registry_path.exists():
        return chat_root
    from ...control_plane.projects.registry_codec import load_project_registry
    from ...paths import resolve_runtime_root
    # Reading the registered storage root is lifecycle metadata observation.
    # Session/Turn admission still enforces the runtime profile and Goal lifetime.
    registry = load_project_registry(registry_path)
    return resolve_runtime_root(registry, registry_path=registry_path) if registry.get("common_runtime_root") else chat_root


def validate_project_native_executor(agent_id: str, project_context: Mapping[str, object]) -> None:
    if agent_id == "codex":
        return
    if project_context.get("filesystem_scope") == "workspace_only":
        raise ValueError("the selected executor cannot enforce workspace-only filesystem access")
    if project_context.get("grant") == "workspace_write":
        raise ValueError("the selected executor cannot enforce workspace write authorization")


def validate_project_executor_scope(
    agent_id: str,
    project_context: Mapping[str, object],
    capabilities: Callable[[], list[dict[str, object]]],
    capability: Mapping[str, object] | None = None,
) -> None:
    validate_project_native_executor(agent_id, project_context)
    if project_context.get("grant") != "workspace_read":
        return
    selected = capability or next(
        (row for row in capabilities() if row["agent_id"] == agent_id), None
    )
    if selected is None:
        raise ValueError(f"unknown Agent endpoint: {agent_id}")
    if selected.get("trust_scope") != "read_only":
        raise ValueError(
            "the selected Agent cannot enforce a read-only project workspace"
        )
