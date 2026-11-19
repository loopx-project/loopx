"""Open a Chat session on the channel that owns its executor and model.

A Chat entry point (the Codex App / dashboard Chat server, a CLI surface, or a
future transport) translates a request into a session; it does not decide which
executor runs the conversation. Each channel resolves that itself through its
own owner, so an entry point can never disagree with the readback the same
channel publishes:

* the steward channel resolves through :func:`chat_manager.open_manager_session`
  -- its explicit configuration, else its shipped default -- and its transcript
  is one conversation across whatever executor it currently resolves;
* a Goal-scoped channel runs on ``DEFAULT_GOAL_AGENT_ID`` when the caller makes
  no explicit pick;
* an ordinary project channel resolves an exact host workspace reference through
  Core and shares its managed Session without creating a Goal or steward scope.

An explicit pick is the caller's own choice and always travels.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from ..chat_manager import open_manager_session

if TYPE_CHECKING:
    from ..chat_action_store import ChatActionStore
    from ..chat_runtime import ChatRuntimeController
    from ..chat_store import ChatSessionStore

# The executor a Goal-scoped session runs on when the caller makes no explicit
# pick. The steward channel does not use this: it resolves its own default.
DEFAULT_GOAL_AGENT_ID = "codex"


def require_regular_session_paths(chat_store: ChatSessionStore, session_id: str) -> None:
    """A redirected or incomplete reservation cannot identify an original."""
    directory, metadata = chat_store._session_dir(session_id), chat_store._session_path(session_id)
    if (directory.is_symlink() or metadata.is_symlink()
            or directory.exists() and not directory.is_dir()
            or metadata.exists() and not metadata.is_file()):
        raise ValueError("The original Session reservation is unavailable; restore its regular metadata")


def read_goal_creation_session(
    action_store: ChatActionStore,
    chat_store: ChatSessionStore | None,
    *,
    proposal_id: str,
    session_id: str,
    goal_id: str,
    agent_id: str,
    workspace: Path,
    goal_instance_id: str | None,
    expected_managed_agent: str | None = None,
) -> dict[str, Any]:
    """Read the original metadata against the creation's fresh checkpoint."""
    from ..control_plane.collaboration import conversation_scope

    if chat_store is not None:
        require_regular_session_paths(chat_store, session_id)
    session = chat_store.load_session(session_id) if chat_store is not None else None
    scope = conversation_scope(session) if session is not None else {}
    if (session is None or session.get("session_id") != session_id or session.get("goal_id") != goal_id
            or session.get("agent_id") != agent_id
            or scope.get("goal_ids") != [goal_id] or scope.get("kind") != "owner_goal"):
        raise ValueError("The original first Session is unavailable; restore it before retrying")
    if expected_managed_agent is not None and session.get("managed_agent_id") != expected_managed_agent:
        raise ValueError("The original registered Agent Session identity changed")
    recorded_proposal = action_store.load(proposal_id)
    if recorded_proposal is None:
        raise ValueError("The original creation checkpoint is unavailable")
    recorded = ((recorded_proposal.get("checkpoint") or {}).get("steps") or {}).get("first_session_opened") or {}
    for field in ("channel_id", "managed_agent_id", "goal_instance_id"):
        if field in recorded and recorded[field] != session.get(field):
            raise ValueError("The original first Session route changed; restore its recorded identity")
    if session.get("managed_agent_id") is not None and (
        session["managed_agent_id"] != agent_id
        or session.get("goal_instance_id") != goal_instance_id
        or session.get("managed_workspace_path") != str(workspace)
    ):
        raise ValueError("The original registered Agent Session identity changed")
    return session


def recover_goal_creation_session(
    chat_store: ChatSessionStore,
    *,
    client_turn_id: str,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any] | None:
    """Recover a unique published original, preferring its original client Turn."""
    from ..control_plane.collaboration import conversation_scope

    candidates = []
    for directory in chat_store.sessions_root.iterdir():
        if not directory.is_dir() and not directory.is_symlink():
            continue
        require_regular_session_paths(chat_store, directory.name)
        if not chat_store._session_path(directory.name).is_file():
            raise ValueError("The original Session reservation is unconfirmed; restore its metadata")
        row = chat_store.load_session(directory.name)
        if row is None or row.get("session_id") != directory.name:
            raise ValueError("Original Session discovery is unreadable; restore it before retrying")
        if (row.get("goal_id") == goal_id and row.get("agent_id") == agent_id
                and conversation_scope(row).get("kind") == "owner_goal"):
            candidates.append(row)
    matches = [row for row in candidates if chat_store.turn_for_client(row["session_id"], client_turn_id) is not None]
    if len(matches) > 1 or not matches and len(candidates) > 1:
        raise ValueError("The original first Session is ambiguous; restore its recorded operation")
    if matches:
        return matches[0]
    if candidates:
        raise ValueError("The original first Session opening is unconfirmed; restore its exact checkpoint")
    return None


def read_exact_original_session(
    controller: ChatRuntimeController,
    *,
    session_id: str,
    selected_scope: Mapping[str, object],
    agent_id: str,
    work_dir: Path,
    requested_model: str | None = None,
    requested_effort: str | None = None,
) -> dict[str, Any] | None:
    """Read one exact opening under the caller's existing Session opening lock."""
    from ..capabilities.native_chat.steward_managed_mode import require_managed_agent_session
    from ..chat_store import CHAT_SESSION_MODE_MANAGED, RESUMABLE_SESSION_STATES
    from ..control_plane.collaboration import conversation_scope

    require_regular_session_paths(controller.store, session_id)
    original = controller.store.load_session(session_id)
    if original is not None:
        if (original.get("session_id") != session_id
                or original.get("goal_id") != selected_scope.get("goal_id") or original.get("agent_id") != agent_id
                or original.get("executor_endpoint_id") != agent_id
                or original.get("channel_id") != selected_scope.get("channel_id")
                or original.get("managed_agent_id") != selected_scope.get("managed_agent_id")
                or original.get("goal_instance_id") != selected_scope.get("goal_instance_id")
                or original.get("session_mode") != CHAT_SESSION_MODE_MANAGED
                or conversation_scope(original)["kind"] != "owner_goal"
                or original.get("status") not in RESUMABLE_SESSION_STATES
                or requested_model and original.get("agent_model_id") != requested_model
                or requested_effort and original.get("agent_reasoning_effort") != requested_effort):
            raise ValueError("the original Goal Session opening identity changed")
        require_managed_agent_session(controller, original, work_dir)
        return original
    if controller.store._session_dir(session_id).exists():
        raise ValueError("the original Goal Session is unreadable; restore it before retrying")
    return None


def open_chat_session(
    *,
    controller: Any,
    context_kind: str,
    goal_id: str | None,
    work_dir: Path,
    objective: str,
    mode: str,
    requested_endpoint: str = "",
    agent_model_id: str | None = "",
    agent_reasoning_effort: str | None = None,
    project_ref: str | None = None,
    managed_agent_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return ``(session, resumed)`` for one entry-point request."""

    if managed_agent_id is not None and context_kind != "goal":
        raise ValueError("a registered managed Agent requires its local Goal conversation")

    if context_kind == "manager":
        # The App's persisted source choice owns the default entry on the
        # server too. API callers must not fork a local manager when the
        # selected private conversation is unavailable.
        bindings = controller.project_contexts.conversation_bindings
        if bindings is not None:
            session = bindings.app_steward_session()
            if session is not None:
                if mode != "resume_latest":
                    raise ValueError("Continue the selected original steward conversation; start a new conversation from its original source.")
                if any(requested and requested != session.get(field) for requested, field in (
                    (requested_endpoint, "executor_endpoint_id"),
                    (agent_model_id, "agent_model_id"),
                    (agent_reasoning_effort, "agent_reasoning_effort"),
                )):
                    raise ValueError("Change the selected steward's model in Settings before continuing its original conversation.")
                return session, True
        return open_manager_session(
            controller=controller,
            goal_id=goal_id,
            work_dir=work_dir,
            executor_endpoint_id=requested_endpoint or None,
            mode=mode,
            agent_model_id=agent_model_id or None,
            **({"agent_reasoning_effort": agent_reasoning_effort} if agent_reasoning_effort is not None else {}),
        )
    if context_kind == "project":
        if goal_id is not None or not project_ref:
            raise ValueError("ordinary project conversation requires project_ref and no Goal")
        return controller.open_session(
            goal_id=None, agent_id=requested_endpoint or DEFAULT_GOAL_AGENT_ID,
            work_dir=work_dir, objective=objective, mode=mode, project_ref=project_ref,
            agent_model_id=agent_model_id or None,
            **({"agent_reasoning_effort": agent_reasoning_effort} if agent_reasoning_effort is not None else {}),
        )
    if context_kind != "goal":
        raise ValueError("unknown conversation context")
    return controller.open_session(
        goal_id=goal_id,
        agent_id=requested_endpoint or DEFAULT_GOAL_AGENT_ID,
        work_dir=work_dir,
        objective=objective,
        mode=mode,
        channel_id=f"goal.{goal_id}",
        agent_goal_id=goal_id,
        **({"managed_agent_id": managed_agent_id} if managed_agent_id is not None else {}),
        agent_model_id=agent_model_id or None,
        **({"agent_reasoning_effort": agent_reasoning_effort} if agent_reasoning_effort is not None else {}),
    )


def goal_creation_display_message(store: ChatSessionStore, *, session_id: str, client_turn_id: str, objective: str) -> str:
    """Show confirmed intent on creation; preserve the original display on retry.

    The execution request remains unchanged. An old recorded message is never
    rewritten using the Goal's later objective or a new presentation default.
    """
    turn = store.turn_for_client(session_id, client_turn_id)
    if turn is None:
        return objective
    messages = [row for row in store.messages(session_id, strict=True)
        if row.get("turn_id") == turn["turn_id"] and row.get("role") == "user"]
    if len(messages) != 1 or not isinstance(messages[0].get("text"), str):
        raise ValueError("The original first Turn display is unavailable; restore its recorded message")
    return messages[0]["text"]
