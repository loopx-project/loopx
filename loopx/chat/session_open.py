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

from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any

from ..attached_session import (
    managed_session_goal_commit,
    resume_attached_agent_session,
    select_current_attached_session,
)
from ..capabilities.manager_runtime import manager_runtime_session_fields
from ..capabilities.native_chat import codex_context, project_context as project_context_policy
from ..capabilities.native_chat.codex_adapter import (
    CodexAppServerAdapter,
    NativeHistoryRestoration,
    NativeWorkingContinuation,
)
from ..capabilities.native_chat.machine_defaults import codex_profile_home, load_codex_chat_defaults
from ..capabilities.native_chat.model_settings import (
    normalize_agent_model_id,
    normalize_agent_reasoning_effort,
    session_model_settings_pending,
)
from ..capabilities.native_chat.steward_managed_mode import (
    prepare_managed_session_route,
    require_managed_agent_session,
)
from ..capabilities.steward_executor import allocation as alloc
from .acp import ACPStdioAdapter
from ..chat_agent import CodexChatAgentError, agent_endpoint_error
from ..chat_codex_goal import UPSTREAM_MODE as CODEX_GOAL_CHAT_MODE
from ..chat_manager import (
    MANAGER_AGENT_GOAL_ID,
    MANAGER_CONTEXT_VERSION,
    is_manager_channel,
    manager_agent_objective,
    manager_workspace,
    open_manager_session,
    steward_machine_defaults,
)
from .providers import ClaudeCodeAdapter, direct_model_from_environment
from ..chat_store import (
    CHAT_SESSION_MODE_ATTACHED,
    TERMINAL_TURN_STATES,
    _opaque_id,
    utc_now,
)
from ..control_plane.collaboration import conversation_scope
from ..control_plane.turn_driver.execution_profile import managed_execution_profile
from ..control_plane.turn_driver.host_binding import MANAGED_TURN_HOST
from ..file_lock import exclusive_cross_runtime_file_lock
from ..kiro_cli_goal_mode import KIRO_CLI_CHAT_AGENT_ID, kiro_cli_chat_command
from .coordination import PROJECT_CONTEXT_VERSION

if TYPE_CHECKING:
    from ..attached_session import ManagedSessionGoalCommit
    from ..chat_action_store import ChatActionStore
    from ..chat_runtime import AttachmentPayload, ChatRuntimeAdapter, ChatRuntimeController, _TurnEventBuffer
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
        project_contexts = getattr(controller, "project_contexts", None)
        bindings = project_contexts.conversation_bindings if project_contexts is not None else None
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


def _supports_live_model_id(adapter: object) -> bool:
    return isinstance(adapter, (ACPStdioAdapter, CodexAppServerAdapter)) or (
        getattr(adapter, "adapter_kind", None) == "antigravity_ls"
    )


class ChatSessionLifecycleMixin:
    """Session opening and runtime adapter lifecycle for ChatRuntimeController."""

    if TYPE_CHECKING:
        store: ChatSessionStore
        registry_path: Path | None
        coordination_runtime_root: Path
        project_contexts: project_context_policy.ChatProjectContexts
        codex_home: Path
        claude_bin: str
        kiro_cli_bin: str
        acp_startup_timeout_sec: float | None
        idle_timeout_sec: float | None
        hard_timeout_sec: float | None
        endpoint_registry: Any
        adapters: dict[str, ChatRuntimeAdapter]
        compacting_history: set[str]
        turn_event_buffers: dict[tuple[str, str], _TurnEventBuffer]
        session_open_locks: dict[tuple[str, str, str], threading.Lock]
        lock: threading.RLock
        loopx_mode: Any

        def manager_runtime_profile(self, channel_id: str = "manager", *, steward_context: dict[str, Any] | None = None) -> dict[str, Any]: ...
        def steward_executor_defaults(self) -> dict[str, Any]: ...
        def capabilities(self) -> list[dict[str, Any]]: ...
        def _session_objective(self, *, goal_id: str | None, objective: str, project_coordination: bool = False) -> str: ...
        def _observed_model_settings(self, adapter: ChatRuntimeAdapter, requested_effort: str | None, requested_model: str | None) -> dict[str, Any]: ...
        def _session_adapter_lock(self, session_id: str) -> threading.Lock: ...
        def _has_live_turn_worker(self, session: dict[str, Any], turn: dict[str, Any] | None) -> bool: ...
        def _load_open_session(self, session_id: str) -> dict[str, Any]: ...
        def _validate_project_session(self, session: Mapping[str, Any]) -> tuple[Path, str]: ...
        def _drop_owned_adapter(self, session_id: str, adapter: ChatRuntimeAdapter, *, always_close: bool = False) -> None: ...
        def submit_turn(
            self,
            *,
            session_id: str,
            client_turn_id: str,
            message: str,
            attachments: list[AttachmentPayload] | None = None,
            work_dir: Path,
            objective: str,
            loopx_execution: bool = False,
            loopx_request: dict[str, object] | None = None,
            _goal_commit: ManagedSessionGoalCommit | None = None,
            display_message: str | None = None,
        ) -> tuple[dict[str, Any], bool]: ...

    def _resolve_manager_channel(
        self, channel_id: str | None, steward_context: dict[str, Any] | None, work_dir: Path, objective: str,
    ) -> tuple[dict[str, Any] | None, Path, str]:
        if not is_manager_channel(channel_id):
            return None, work_dir, objective
        runtime = self.manager_runtime_profile(str(channel_id or "manager"), steward_context=steward_context)
        runtime_profile = str(runtime["runtime_profile"])
        return (
            runtime,
            manager_workspace(self.store.root, str(channel_id), runtime_profile=runtime_profile),
            manager_agent_objective(runtime_profile),
        )

    def _start_acp_adapter(
        self, *, command: Sequence[str], work_dir: Path, resume_thread_id: str | None,
        execution_mode: bool, agent_config: dict[str, str] | None, agent_work_dir: Path | None = None,
    ) -> ChatRuntimeAdapter:
        return ACPStdioAdapter.start(
            command=command, work_dir=work_dir,
            **({"agent_work_dir": agent_work_dir} if agent_work_dir is not None else {}),
            resume_thread_id=resume_thread_id, startup_timeout_sec=self.acp_startup_timeout_sec,
            idle_timeout_sec=self.idle_timeout_sec, hard_timeout_sec=self.hard_timeout_sec,
            execution_mode=execution_mode, config_values=agent_config,
        )

    def _start_adapter(
        self,
        *,
        agent_id: str,
        work_dir: Path,
        goal_id: str | None,
        objective: str,
        resume_thread_id: str | None = None,
        history: list[dict[str, Any]] | None = None,
        execution_mode: bool = False,
        manager_runtime: Mapping[str, Any] | None = None,
        project_coordination: bool = False,
        loopx_tools: bool = False,
        check_preparation: bool = False,
        managed_read_default: bool = False,
        executor_model: Mapping[str, str | None] | None = None,
        project_context: dict[str, str] | None = None,
        agent_config: dict[str, str] | None = None,
        codex_home_override: Path | None = None,
        read_only_review: bool = False,
        metadata_review_context: dict[str, Any] | None = None,
        answer_review_context: dict[str, Any] | None = None,
        history_check: Callable[[], None] | None = None,
        working_continuation: NativeWorkingContinuation | None = None,
    ) -> ChatRuntimeAdapter:
        if project_context is not None:
            project_context_policy.validate_project_native_executor(agent_id, project_context)
        if manager_runtime is not None and manager_runtime.get("runtime_profile") == "trusted_owner" and agent_id not in {"codex", "antigravity"}:
            raise CodexChatAgentError(
                "The trusted owner manager profile requires the Codex or Antigravity endpoint.",
                error_code="manager_runtime_endpoint_unsupported",
                gate={"kind": "host_tool_gate",
                      "summary": "The selected manager Agent cannot enforce the trusted owner runtime profile.",
                      "next_action": "Select the Codex or Antigravity Agent or change Manager runtime to restricted."})
        if agent_id in {"codex", "antigravity"}:
            from ..capabilities.native_chat.model_settings import native_adapter_configuration
            manager_profile, objective, model_config = native_adapter_configuration(
                self, agent_id=agent_id, goal_id=goal_id, objective=objective,
                manager_runtime=manager_runtime, executor_model=executor_model,
                agent_config=agent_config, execution_mode=execution_mode)
            from ..capabilities.native_chat.codex_adapter import native_runtime_context, start_runtime_adapter
            native_context = native_runtime_context(
                self, goal_id=goal_id, objective=objective, manager_profile=manager_profile,
                project_coordination=project_coordination, execution_mode=execution_mode)
        if agent_id == "antigravity":
            from ..capabilities.native_chat.antigravity_adapter import AntigravityAdapter
            if execution_mode or project_context is not None or read_only_review:
                raise CodexChatAgentError(
                    "Antigravity supports Chat and Steward; use Codex for governed workspace execution.",
                    gate=None, error_code="unsupported_configuration")
            return AntigravityAdapter.start(
                work_dir=work_dir, **native_context, resume_thread_id=resume_thread_id,
                history=history, executor_model=model_config,
                idle_timeout_sec=self.idle_timeout_sec, hard_timeout_sec=self.hard_timeout_sec)
        if agent_id == "codex":
            return start_runtime_adapter(
                self, work_dir=work_dir, goal_id=goal_id, native_context=native_context,
                manager_profile=manager_profile, model_config=model_config, history=history,
                resume_thread_id=resume_thread_id, execution_mode=execution_mode, project_context=project_context,
                project_coordination=project_coordination, loopx_tools=loopx_tools, check_preparation=check_preparation,
                managed_read_default=managed_read_default, codex_home_override=codex_home_override,
                read_only_review=read_only_review, metadata_review_context=metadata_review_context,
                answer_review_context=answer_review_context, history_check=history_check,
                working_continuation=working_continuation)
        if agent_id == "claude-code":
            return ClaudeCodeAdapter.start(
                claude_bin=self.claude_bin, work_dir=work_dir, resume_thread_id=resume_thread_id, tool_scope="read_only",
                context_summary=f"{goal_id}: {objective}".strip() if goal_id is not None else objective.strip(),
                model=agent_config.get("model") if agent_config else None)
        if agent_id == MANAGED_TURN_HOST:
            # The managed host has no interactive session transport, so this
            # channel holds one bounded segment per turn on the resolved managed
            # execution profile, authenticated by the operator credential.
            from .. import chat_runtime as _runtime

            operator_environ = _runtime.operator_credential_resolution(self)["environ"]
            profile = managed_execution_profile(operator_environ)
            model, reasoning_effort = str(profile["model"]), str(profile["reasoning_effort"])
            if goal_id == MANAGER_AGENT_GOAL_ID:
                manager_config = executor_model or _runtime._steward_dsh_model_config(self, operator_environ, agent_id)
                model, reasoning_effort = str(manager_config["model"]), str(manager_config["reasoning_effort"])
            return _runtime.DshChatAdapter(
                objective=self._session_objective(
                    goal_id=goal_id, objective=objective, project_coordination=project_coordination and not execution_mode),
                work_dir=work_dir, provider=str(profile["provider"]), model=model, reasoning_effort=reasoning_effort,
                timeout_sec=self.hard_timeout_sec if self.hard_timeout_sec is not None else 900.0,
                # A segment is fresh, so this adapter carries the visible history
                # itself instead of relying on a host session to remember it.
                history=list(history or []), credential=_runtime.operator_credential_pair(self))
        if agent_id in {"anthropic-api", "openai-api"}:
            return direct_model_from_environment(
                provider="anthropic" if agent_id == "anthropic-api" else "openai",
                work_dir=work_dir, session_id=resume_thread_id, history=history)
        if agent_id == KIRO_CLI_CHAT_AGENT_ID:
            return self._start_acp_adapter(
                command=kiro_cli_chat_command(self.kiro_cli_bin), work_dir=work_dir,
                resume_thread_id=resume_thread_id, execution_mode=execution_mode, agent_config=agent_config)
        endpoint = self.endpoint_registry.get(agent_id)
        if endpoint is not None:
            return self._start_acp_adapter(
                command=endpoint.command, work_dir=work_dir, agent_work_dir=endpoint.mapped_work_dir(work_dir),
                resume_thread_id=resume_thread_id, execution_mode=execution_mode, agent_config=agent_config)
        raise ValueError(f"unknown Agent endpoint: {agent_id}")

    def open_session(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        work_dir: Path,
        objective: str,
        mode: str,
        channel_id: str | None = None,
        agent_goal_id: str | None = None,
        goal_instance_id: str | None = None,
        managed_agent_id: str | None = None,
        session_id: str | None = None,
        manager_executor_allocation: Mapping[str, Any] | None = None,
        project_ref: str | None = None,
        conversation_binding_id: str | None = None,
        source_context: dict[str, Any] | None = None,
        agent_model_id: str | None = None,
        agent_reasoning_effort: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if agent_model_id:
            agent_model_id = normalize_agent_model_id(agent_model_id)
        capability = next((item for item in self.capabilities() if item["agent_id"] == agent_id), None)
        if (
            capability
            and capability.get("available")
            and agent_model_id
            and agent_id not in {"codex", "antigravity", "claude-code"}
            and capability.get("adapter_kind") != "acp"
        ):
            raise CodexChatAgentError("This Agent does not offer model selection.",
                                      error_code="unsupported_configuration", gate=None)
        if agent_reasoning_effort is not None:
            agent_reasoning_effort = normalize_agent_reasoning_effort(agent_reasoning_effort)
            if (
                capability
                and capability.get("available")
                and agent_id not in {"codex", "antigravity"}
                and capability.get("adapter_kind") != "acp"
            ):
                raise CodexChatAgentError("This Agent does not offer reasoning choices.",
                                          error_code="unsupported_configuration", gate=None)
        if mode not in {"resume_latest", "new"}:
            raise ValueError("mode must be resume_latest or new")
        if session_id is not None and mode != "new":
            raise ValueError("an exact Session opening requires mode=new")
        selected_channel = channel_id or f"goal.{goal_id}"
        project_context = None
        steward_context = None
        if conversation_binding_id is not None:
            if goal_id is not None or agent_goal_id is not None or project_ref is not None:
                raise ValueError("bound project Chat cannot substitute a Goal or workspace")
            if source_context is None:
                raise ValueError("bound conversation authority is unavailable")
            context = self.project_contexts.open_bound(
                conversation_binding_id, source_context, executor=agent_id, channel_id=channel_id,
            )
            goal_id, selected_channel = context["goal_id"], context["channel_id"]
            project_context, steward_context = context["project_context"], context["steward_context"]
            work_dir, objective = context["project"], context["objective"]
        elif project_ref is not None:
            if goal_id is not None or agent_goal_id is not None:
                raise ValueError("ordinary project conversations cannot carry a Goal")
            selected = self.project_contexts.resolve(project_ref)
            if channel_id is not None and channel_id != selected["channel_id"]:
                raise ValueError("project conversation channel mismatch")
            project_context = selected["context"]
            selected_channel = selected["channel_id"]
            context = self.project_contexts.session_context({
                "goal_id": None, "channel_id": selected_channel, "project_context": project_context,
            })
            work_dir, objective = context["project"], context["objective"]
        elif goal_id is None:
            raise ValueError("goal_id or an authorized project_ref is required")
        selected_scope = prepare_managed_session_route(
            self, goal_id=goal_id, executor=agent_id, workspace=work_dir,
            channel_id=selected_channel, goal_instance_id=goal_instance_id,
            managed_agent_id=managed_agent_id, project_context=project_context, steward_context=steward_context,
        )
        selected_channel, goal_instance_id = selected_scope["channel_id"], selected_scope["goal_instance_id"]
        if project_context is not None:
            project_context_policy.validate_project_executor_scope(agent_id, project_context, self.capabilities, capability)
        manager_runtime, work_dir, objective = self._resolve_manager_channel(
            selected_channel, steward_context, work_dir, objective,
        )
        if manager_runtime is not None:
            agent_goal_id = MANAGER_AGENT_GOAL_ID
            if selected_channel == "manager":
                goal_id = MANAGER_AGENT_GOAL_ID
        route_goal_id = "*" if manager_runtime is not None else goal_id
        route_key = (route_goal_id, agent_id, selected_channel)
        if session_id is not None:
            if not isinstance(session_id, str) or session_id != session_id.strip() or session_id in {".", ".."}:
                raise ValueError("session_id must retain its exact non-empty opaque identity")
            session_id = _opaque_id(session_id, field="session_id")
            if (
                project_context is not None
                or steward_context is not None
                or manager_runtime is not None
                or goal_id is None
                or agent_goal_id is not None and agent_goal_id != goal_id
                or conversation_scope(selected_scope)["kind"] != "owner_goal"
            ):
                raise ValueError("an exact Session opening requires its original local Goal route")
        with self.lock:
            route_lock = self.session_open_locks.setdefault(route_key, threading.Lock())
        with route_lock, (
            exclusive_cross_runtime_file_lock(
                self.store.root / "session-open-locks" / session_id, operation="open_original_goal_session",
            )
            if session_id is not None
            else nullcontext()
        ):
            if session_id is not None:
                original = read_exact_original_session(
                    self, session_id=session_id, selected_scope=selected_scope,
                    agent_id=agent_id, work_dir=work_dir,
                    requested_model=agent_model_id, requested_effort=agent_reasoning_effort,
                )
                if original is not None:
                    return original, True
            latest = None
            if manager_runtime is None and project_context is None:
                exact_attached, strict_profile = select_current_attached_session(
                    store=self.store,
                    registry_path=self.registry_path,
                    goal_id=goal_id,
                    agent_id=agent_id,
                    channel_id=selected_channel,
                )
                if strict_profile:
                    if mode == "resume_latest" and exact_attached is not None:
                        if ((agent_reasoning_effort and exact_attached.get("agent_reasoning_effort") != agent_reasoning_effort)
                                or (agent_model_id and exact_attached.get("agent_model_id") != agent_model_id)):
                            raise CodexChatAgentError("Change attached conversation settings in its original host.",
                                                      error_code="unsupported_configuration", gate=None)
                        return exact_attached, True
                    raise CodexChatAgentError(
                        "Managed Chat execution is not qualified for the source-session profile.",
                        error_code="source_session_managed_chat_unsupported",
                        gate=None,
                    )
            if mode == "resume_latest" and session_id is None:
                latest = self.store.latest_session(
                    goal_id=None if manager_runtime is not None else goal_id,
                    agent_id=agent_id,
                    channel_id=selected_channel,
                )
                # Reuse only the same typed project identity. A changed host grant
                # starts a new Session while the old context and history remain intact.
                if latest is not None and project_context is not None and latest.get("project_context") != project_context:
                    latest = None
                if latest is not None and (
                    (goal_instance_id is not None and latest.get("goal_instance_id") != goal_instance_id)
                    or managed_agent_id is not None
                    and (latest.get("managed_agent_id") != managed_agent_id or latest.get("goal_instance_id") != goal_instance_id)
                ):
                    latest = None
                if latest is not None and (
                    latest.get("active_turn_id")
                    or latest.get("upstream_mode") == CODEX_GOAL_CHAT_MODE
                    or latest.get("session_mode") == CHAT_SESSION_MODE_ATTACHED
                ):
                    if ((agent_reasoning_effort and latest.get("agent_reasoning_effort") != agent_reasoning_effort)
                            or (agent_model_id and latest.get("agent_model_id") != agent_model_id)):
                        raise CodexChatAgentError(
                            "The existing execution keeps its settings. Change the next conversation after it finishes, or use its original host.",
                            error_code="unsupported_configuration", gate=None,
                        )
                if latest is not None and agent_model_id and latest.get("agent_model_id") != agent_model_id:
                    # A model is a session binding for ACP.  Never silently
                    # retarget an existing conversation when the user chose a
                    # different one; a new session makes that boundary visible.
                    latest = None
                if latest is not None and agent_reasoning_effort and latest.get("agent_reasoning_effort") != agent_reasoning_effort:
                    latest = None
                if latest is not None and latest.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
                    return latest, True
            codex_defaults = {}
            selected_codex_home = self.codex_home
            if (
                agent_id == "codex"
                and goal_id != MANAGER_AGENT_GOAL_ID
                and conversation_scope(selected_scope)["kind"] == "owner_goal"
                and not (latest and (latest.get("active_turn_id") or latest.get("upstream_mode") == CODEX_GOAL_CHAT_MODE))
            ):
                codex_defaults = load_codex_chat_defaults(self.store.root.parent)
                selected_codex_home = codex_profile_home(self.codex_home, codex_defaults)
                if latest is not None and (latest.get("codex_chat_configuration") or {}) != codex_defaults:
                    # New defaults get a new ordinary Session. Resolve them only
                    # after attached, active and Agent Loop bindings are preserved;
                    # even an unavailable new profile cannot interrupt that work.
                    latest = None
            if capability is None:
                raise agent_endpoint_error(agent_id)
            if not capability["available"]:
                raise agent_endpoint_error(agent_id, reason=str(capability.get("unavailable_reason") or ""))
            if latest is not None:
                self._ensure_adapter(latest, work_dir=work_dir, objective=objective)
                return self.store.load_session(latest["session_id"]) or latest, True
            adapter = self._start_adapter(
                agent_id=agent_id,
                work_dir=work_dir,
                goal_id=agent_goal_id or goal_id,
                objective=objective,
                execution_mode=selected_channel.startswith("task."),
                project_coordination=conversation_scope(selected_scope)["kind"] == "owner_goal",
                check_preparation=managed_agent_id is not None,
                managed_read_default=managed_agent_id is not None,
                manager_runtime=manager_runtime,
                project_context=project_context,
                executor_model=alloc.manager_executor_model(manager_executor_allocation),
                agent_config={
                    **{key: value for key, value in codex_defaults.items() if value is not None},
                    **({"model": agent_model_id} if agent_model_id else {}),
                    **({"reasoning_effort": agent_reasoning_effort} if agent_reasoning_effort else {}),
                } or None,
                codex_home_override=selected_codex_home,
            )
            try:
                require_managed_agent_session(self, selected_scope, work_dir)
                observed_settings = self._observed_model_settings(adapter, agent_reasoning_effort, agent_model_id)
                persisted = self.store.create_session(
                    **({"session_id": session_id} if session_id is not None else {}),
                    goal_id=goal_id,
                    goal_instance_id=goal_instance_id,
                    **({"managed_agent_id": managed_agent_id, "managed_workspace_path": str(work_dir.resolve())}
                       if managed_agent_id is not None else {}),
                    agent_id=agent_id,
                    executor_endpoint_id=agent_id,
                    adapter_kind=str(capability["adapter_kind"]),
                    upstream_thread_id=adapter.upstream_thread_id,
                    upstream_mode="chat" if agent_id == "codex" else "default",
                    channel_id=selected_channel,
                    codex_home=str(codex_context.home_for_project(selected_codex_home, project_context))
                    if agent_id == "codex" else None,
                    project_context=project_context,
                    steward_context=steward_context,
                    agent_model_id=getattr(adapter, "current_model_id", None) if _supports_live_model_id(adapter) else None,
                    requested_agent_model_id=agent_model_id,
                    requested_agent_reasoning_effort=agent_reasoning_effort,
                    codex_chat_configuration=codex_defaults or None,
                    **observed_settings,
                )
            except Exception:
                adapter.close_session()
                raise
            persisted = self.project_contexts.initialize_bound_scope(
                self.store, persisted, runtime_root=self.coordination_runtime_root,
            )
            if manager_runtime is not None:
                persisted = self.store.update_session(
                    persisted["session_id"],
                    manager_context_version=MANAGER_CONTEXT_VERSION,
                    **alloc.manager_executor_session_fields(manager_executor_allocation),
                    **manager_runtime_session_fields(manager_runtime),
                )
            elif conversation_scope(persisted)["kind"] == "owner_goal":
                persisted = self.store.update_session(
                    persisted["session_id"], coordination_context_version=PROJECT_CONTEXT_VERSION,
                )
            with self.lock:
                self.adapters[persisted["session_id"]] = adapter
            return persisted, False

    def _ensure_adapter(
        self,
        session: dict[str, Any],
        *,
        work_dir: Path,
        objective: str,
    ) -> ChatRuntimeAdapter:
        session_id = str(session["session_id"])
        with self._session_adapter_lock(session_id):
            return self._ensure_adapter_locked(session, work_dir=work_dir, objective=objective)

    def _ensure_adapter_locked(
        self,
        session: dict[str, Any],
        *,
        work_dir: Path,
        objective: str,
        interrupted_turn_id: str | None = None,
        accepted_turn_id: str | None = None,
        goal_commit: ManagedSessionGoalCommit | None = None,
    ) -> ChatRuntimeAdapter:
        from .. import chat_runtime as _runtime

        commit = goal_commit or nullcontext
        session_id = str(session["session_id"])
        session = self._load_open_session(session_id)
        require_managed_agent_session(self, session, work_dir)
        if session.get("project_context") is not None:
            work_dir, objective = self._validate_project_session(session)
        elif session.get("steward_context") is not None:
            self.project_contexts.session_context(session)
        manager_runtime, work_dir, objective = self._resolve_manager_channel(
            session.get("channel_id"), session.get("steward_context"), work_dir, objective)
        selected_codex_home = codex_context.checked_session_home(self.codex_home, session)
        if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
            raise CodexChatAgentError(
                "The attached host Session must be served by its existing host bridge.",
                error_code="attached_session_requires_host_bridge",
            )
        # Session open and resume preserve the same admitted, unstarted request.
        active_id = session.get("active_turn_id")
        active = self.store.load_turn(session_id, str(active_id)) if active_id else None
        if accepted_turn_id is None and active is not None and active.get("status") == "queued":
            accepted_turn_id = str(active["turn_id"])
        settings_changed = bool(
            session_model_settings_pending(session)
            and (
                not active
                or active.get("status") in TERMINAL_TURN_STATES
                or active.get("status") == "queued" and active_id == accepted_turn_id
            )
        )
        reusable: ChatRuntimeAdapter | None = None
        # An owner-edited model takes effect between Turns. Keep a running Turn
        # and its binding intact; accepted queued work has not started upstream.
        model_allocation = None
        if manager_runtime is not None and (
            not session.get("active_turn_id")
            or (
                session.get("active_turn_id") == accepted_turn_id
                and (self.store.load_turn(session_id, str(accepted_turn_id)) or {}).get("status") == "queued"
            )
        ):
            model_allocation = _runtime.manager_session_model_allocation(self, session)
        legacy_project_context = (
            conversation_scope(session)["kind"] == "owner_goal"
            and session.get("coordination_context_version") != PROJECT_CONTEXT_VERSION
            # A context/tool refresh cannot discard a native Goal and its usage.
            # Keep this binding; a new Session explicitly selects new tools.
            and session.get("upstream_mode") != CODEX_GOAL_CHAT_MODE
        )
        # Refresh only the queued recovery's auth/profile; keep its native thread.
        recovery: dict[str, Any] = {}
        if self.project_contexts.conversation_bindings is not None:
            from ..capabilities.native_chat.external_conversations import ChatExternalConversations
            recovery = ChatExternalConversations(self).queued_recovery_context(session, active, accepted_turn_id)
        quota_route = None
        metadata_fallback = recovery.get("metadata_review") is True and recovery.get("metadata_executor") == "codex"
        quota_fallback = bool(recovery.get("quota_attempt") and recovery.get("quota_executor") == "codex")
        answer_fallback = recovery.get("answer_review") is True and recovery.get("answer_executor") == "codex"
        if (
            manager_runtime is not None
            and active
            and active_id == accepted_turn_id
            and active.get("status") == "queued"
            and not active.get("upstream_turn_id")
            and active.get("origin") != "native_loopx"
            and (not recovery or metadata_fallback or quota_fallback or answer_fallback)
            and ("-recovery-" not in str(active.get("client_turn_id")) or metadata_fallback or quota_fallback or answer_fallback)
            and session.get("upstream_mode") in {"default", "chat"}
        ):
            quota_route = _runtime.manager_session_quota_route(self, session)
            history_on_current_codex = recovery.get("history_review") is True and session.get("agent_id") == "codex"
            if history_on_current_codex:
                quota_route = None  # This answer cannot change executor or account policy.
            if (
                (metadata_fallback or quota_fallback or answer_fallback)
                and not history_on_current_codex
                and (not quota_route or quota_route["allocation"]["executor_endpoint"] != "codex")
            ):
                raise CodexChatAgentError("The reserved recovery no longer has verified Codex capacity.",
                                          error_code="manager_authorization_unavailable", gate=None)
            if quota_route and quota_route["allocation"]["executor_endpoint"] == session.get("agent_id"):
                quota_route = None
            if quota_route is not None:
                model_allocation = quota_route["allocation"]

        def check_route_configuration() -> None:
            allocation = quota_route["allocation"] if quota_route else session.get("manager_executor_allocation") or {}
            if allocation.get("allocation_reason") != "flexible_availability_fallback" and not quota_route:
                return
            defaults = steward_machine_defaults(self) or {}
            if (
                defaults.get("status") != "ready"
                or defaults.get("selection_policy") != "flexible"
                or defaults.get("executor_endpoint") not in {"antigravity", "codex"}
                or set(defaults.get("eligible_endpoints") or []) != {"antigravity", "codex"}
                or defaults.get("configuration_revision") != allocation.get("configuration_revision")
            ):
                raise CodexChatAgentError("The steward endpoint pool changed; no model Turn was dispatched.",
                                          error_code="manager_authorization_unavailable", gate=None)

        if active and active.get("status") == "queued" and active_id == accepted_turn_id:
            check_route_configuration()
        selected_agent = model_allocation["executor_endpoint"] if quota_route else session["agent_id"]
        executor_model = (
            alloc.manager_executor_model(model_allocation)
            if model_allocation is not None
            else alloc.restored_executor_model(session)
        )
        last_model = session.get("agent_model_id")
        requested_model = last_model
        if session["agent_id"] == "codex":
            # In a Steward binding the allocation supplies the default. Old
            # observed settings are only a fallback for unallocated Sessions.
            requested_model = session.get(
                "requested_agent_model_id",
                None if manager_runtime is not None and executor_model is not None else last_model,
            )
        agent_config = executor_model if quota_route else {
            **(session.get("codex_chat_configuration") or {}),
            **({"model": requested_model} if requested_model else {}),
            **({"reasoning_effort": session["requested_agent_reasoning_effort"]}
               if session.get("requested_agent_reasoning_effort") else {}),
        }
        native_account_epoch = None
        if selected_agent == "codex" and (
            active
            and active_id == accepted_turn_id
            and active.get("status") == "queued"
            and not active.get("upstream_turn_id")
            and active.get("origin") != "native_loopx"
            or not active and session.get("parked_native_working")
        ):
            from ..capabilities.native_chat.codex_auth import conversation_account_epoch
            native_account_epoch = conversation_account_epoch(
                self.coordination_runtime_root, selected_codex_home,
                **({"consumer": "steward", "model": (agent_config or {}).get("model")
                    or (executor_model or {}).get("model") or last_model}
                   if manager_runtime is not None else {}),
            )
        native_quota_refresh = bool(
            active
            and active_id == accepted_turn_id
            and active.get("status") == "queued"
            and active.get("origin") == "native_loopx"
            and active.get("loopx_execution") is True
            and (active.get("loopx_request") or {}).get("work_kind") == "report"
            and isinstance((active.get("loopx_request") or {}).get("report_quota_recovery"), dict)
        )
        working_provider = None
        with self.lock:
            current = self.adapters.get(session_id)
            manager_profile_changed = NativeHistoryRestoration.manager_profile_changed(session, manager_runtime)
            if (
                current is not None
                and current.healthcheck()
                and not manager_profile_changed
                and not recovery.get("history_review")
                and not recovery.get("quota_attempt")
                and not (answer_fallback and session.get("parked_native_working"))
                and not native_quota_refresh
                and (native_account_epoch is None or getattr(current, "_native_account_epoch", None) == native_account_epoch)
                and (isinstance(current, CodexAppServerAdapter) and current.session.read_only_review) == bool(recovery.get("read_only_review"))
                and model_allocation is None
                and not settings_changed
            ):
                reusable = current
            elif current is None and self._has_live_turn_worker(session, active):
                # A native observation needs no model adapter. Absence of that
                # transport cannot prove that its original Chat worker exited.
                raise ValueError("the original Chat worker is still active; read its current Turn")
            elif current is not None:
                may_park_working = bool(
                    answer_fallback
                    and isinstance(current, CodexAppServerAdapter)
                    and not current.session.read_only_review
                    and current.upstream_thread_id == session.get("upstream_thread_id")
                )
                if (
                    recovery.get("history_review") is True
                    or recovery.get("read_only_review") and recovery.get("unfinished_work")
                    or may_park_working
                ) and current.healthcheck():
                    if not isinstance(current, CodexAppServerAdapter) or not current.answer_review_replaceable():
                        raise CodexChatAgentError("The original adapter still owns background work; answer review held.",
                                                  error_code="host_gate", gate=None)
                    if may_park_working and getattr(current, "_native_account_epoch", None) == native_account_epoch:
                        working_provider = current.session.native_model_provider
                current.close_session()
                self.adapters.pop(session_id, None)
        if reusable is not None:
            # Apply context/tool migrations only when opening an upstream session.
            # A healthy in-process adapter may own a Turn; never replace it here.
            if manager_runtime is not None and session.get("manager_runtime_profile") is None:
                self.store.update_session(session_id, **manager_runtime_session_fields(manager_runtime))
            return reusable
        with commit():
            if accepted_turn_id is not None:
                active = self.store.load_turn(session_id, accepted_turn_id)
                if active_id != accepted_turn_id or active is None or active.get("status") != "queued":
                    raise ValueError("accepted chat turn is no longer queued and active")
            else:
                self.store.update_session(session_id, status="resuming", last_error_code=None)
            active_turn_id = None if accepted_turn_id is not None else interrupted_turn_id or active_id
            if active_turn_id is not None:
                active = self.store.load_turn(session_id, str(active_turn_id))
                if active and active.get("status") not in TERMINAL_TURN_STATES:
                    failed = self.store.update_turn(
                        session_id, str(active_turn_id),
                        expected_statuses={"queued", "starting", "running", "interrupting"},
                        status="failed", error_code="server_restarted",
                        error=_runtime.SERVER_RESTARTED_MESSAGE, completed_at=utc_now())
                    if failed is not None:
                        self.store.append_event(
                            session_id, str(active_turn_id), kind="turn.failed",
                            payload={"error_code": "server_restarted", "message": failed["error"]})
        native_recovery = None
        history_restore = NativeHistoryRestoration(
            self, session, accepted_turn_id, commit, check_route_configuration, manager_runtime is not None)
        try:
            same_working_route = (
                manager_runtime is not None and session.get("steward_context") is not None
                and selected_agent == session["agent_id"] == "codex"
                and session.get("manager_context_version") == MANAGER_CONTEXT_VERSION
                and session.get("upstream_mode") == "chat" and not manager_profile_changed
                and not settings_changed and not quota_route and not quota_fallback)
            cold_working = (
                current is None and answer_fallback and native_account_epoch is not None
                and session.get("answer_review") is not True and bool(session.get("native_working_checkpoint")))
            checkpoint_available = working_provider or cold_working or session.get("parked_native_working")
            stored_messages = history_restore.capture(
                strict=bool(same_working_route and model_allocation is None and checkpoint_available),
            )
            history = history_restore.messages(stored_messages)
            if (same_working_route and model_allocation is not None and not recovery
                    and not session.get("answer_review") and not session.get("parked_native_working")):
                history_restore.resume_working_model(codex_home=selected_codex_home)
            legacy_manager_context = history_restore.manager_migration(
                refresh=(manager_profile_changed
                         or (model_allocation is not None and history_restore.working_continuation is None)
                         or quota_fallback or answer_fallback))
            legacy_provider_context = history_restore.provider_migration(stored_messages)
            if (manager_runtime is not None and same_working_route and model_allocation is None and checkpoint_available
                    and not legacy_provider_context and not legacy_project_context):
                history_restore.prepare_working_context(
                    stored_messages, work_dir=work_dir, codex_home=selected_codex_home,
                    runtime=manager_runtime, account_epoch=native_account_epoch,
                    working_provider=working_provider, cold_working=cold_working,
                    answer_review=answer_fallback, read_only_review=bool(recovery.get("read_only_review")))
            working_continuation = history_restore.working_continuation
            native_resume = (
                None if working_continuation or legacy_provider_context or legacy_manager_context or legacy_project_context
                else str(session["upstream_thread_id"]))
            # Isolated answer review receives verified results at dispatch;
            # importing unrelated conversation history needlessly compacts it.
            native_history = (
                None if answer_fallback or working_continuation else history
                if legacy_provider_context or legacy_manager_context or legacy_project_context
                or session.get("agent_id") in {"anthropic-api", "openai-api", MANAGED_TURN_HOST} else None)
            if (selected_agent == "codex" and accepted_turn_id is not None
                    and not (self.store.load_turn(session_id, accepted_turn_id) or {}).get("native_context_preparation")):
                # Save the actual routing inputs before native IO. This is an
                # observation of the existing choice, never execution evidence.
                method = ("thread/fork" if working_continuation and working_continuation.suffix
                          else "thread/resume" if working_continuation or native_resume else "thread/start")
                self.store.update_turn(
                    session_id, accepted_turn_id, expected_statuses={"queued"},
                    native_context_preparation={
                        "schema_version": "codex_native_context_preparation_v0", "planned_method": method,
                        "source_thread_id": working_continuation.thread_id if working_continuation else session["upstream_thread_id"],
                        "history_message_count": len(working_continuation.suffix) if working_continuation else len(native_history or []),
                        "native_checkpoint_requested": method != "thread/start",
                        "working_checkpoint_available": bool(checkpoint_available or working_continuation),
                        "manager_context_version": session.get("manager_context_version"),
                        "required_manager_context_version": MANAGER_CONTEXT_VERSION,
                        "manager_context_migration": legacy_manager_context, "provider_context_migration": legacy_provider_context,
                        "project_context_migration": legacy_project_context, "manager_profile_changed": manager_profile_changed,
                        "settings_changed": settings_changed, "executor_reallocated": model_allocation is not None,
                        "isolated_answer_review": answer_fallback})
            adapter = self._start_adapter(
                agent_id=str(selected_agent), work_dir=work_dir,
                goal_id=MANAGER_AGENT_GOAL_ID if manager_runtime is not None else session["goal_id"],
                objective=objective, resume_thread_id=native_resume, history=native_history,
                execution_mode=str(session.get("channel_id") or "").startswith("task."),
                project_coordination=conversation_scope(session)["kind"] == "owner_goal",
                loopx_tools=session.get("loopx_tools") is True,
                managed_read_default=session.get("managed_agent_id") is not None,
                executor_model=executor_model,
                manager_runtime=manager_runtime, project_context=session.get("project_context"),
                agent_config=agent_config or None,
                codex_home_override=selected_codex_home, read_only_review=bool(recovery.get("read_only_review")),
                metadata_review_context=session.get("steward_context") if metadata_fallback else None,
                answer_review_context=session.get("steward_context") if answer_fallback else None,
                history_check=history_restore.check,
                **({"working_continuation": working_continuation} if working_continuation is not None else {}))
            try:
                # Recovery reads back the provider-restored configuration.
                expected_model = session.get("requested_agent_model_id") if settings_changed else None
                if (selected_agent == "codex" and not quota_route and model_allocation is not None
                        and model_allocation.get("configured_model")):
                    expected_model = (agent_config or {}).get("model") or model_allocation["model"]
                observed_settings = self._observed_model_settings(
                    adapter,
                    session.get("requested_agent_reasoning_effort") if settings_changed else None,
                    expected_model)
                if session.get("upstream_mode") == CODEX_GOAL_CHAT_MODE:
                    if not isinstance(adapter, CodexAppServerAdapter):
                        raise CodexChatAgentError("Native Goal continuation requires a Codex adapter.",
                                                  error_code="native_goal_adapter_mismatch", gate=None)
                    native_recovery = self.loopx_mode.observe_recovery(adapter, commit)
            except Exception:
                adapter.close_session()
                raise
        except Exception as exc:
            history_restore.finish()
            error_code = exc.error_code if isinstance(exc, CodexChatAgentError) else None
            preserve_error = error_code == "model_auth_unavailable" or (settings_changed and error_code == "unsupported_configuration")
            if not history_restore.invalidated:
                with commit():
                    self.store.update_session(
                        session_id,
                        status="busy" if accepted_turn_id is not None else "ready" if settings_changed else "resume_failed",
                        active_turn_id=accepted_turn_id, last_error_code=error_code if preserve_error else "resume_failed")
            if preserve_error:
                raise
            gate = exc.gate if isinstance(exc, CodexChatAgentError) else None
            raise CodexChatAgentError("The previous Agent conversation could not be restored.",
                                      error_code="resume_failed", gate=gate) from exc
        try:
            if history_restore.checked:
                history_restore.check()
            with commit():
                if quota_route:
                    check_route_configuration()
                self.loopx_mode.commit_recovery(session_id, native_recovery)
                if native_account_epoch is not None:
                    adapter._native_account_epoch = native_account_epoch
                with self.lock:
                    self.adapters[session_id] = adapter
                if not quota_route and _supports_live_model_id(adapter):
                    self.store.update_session(session_id, agent_model_id=adapter.current_model_id, **observed_settings)
                if session.get("agent_id") == "codex" and session.get("codex_home") is None:
                    # A legacy session is bound only after successful upstream resume,
                    # not when a service happens to start in a new environment.
                    self.store.update_session(
                        session_id, codex_home=str(codex_context.home_for_project(selected_codex_home, session.get("project_context"))))
                if manager_runtime is not None:
                    changes = {
                        "manager_context_version": MANAGER_CONTEXT_VERSION,
                        **manager_runtime_session_fields(manager_runtime),
                        **alloc.manager_executor_session_fields(model_allocation)}
                    if answer_fallback or session.get("answer_review") is True:
                        changes["answer_review"] = answer_fallback
                    if history_restore.parked_working is not None or session.get("parked_native_working") is not None:
                        changes.update(parked_native_working=history_restore.parked_working,
                                       expected_upstream_thread_id=session["upstream_thread_id"])
                    if legacy_manager_context and adapter.upstream_thread_id != session.get("upstream_thread_id"):
                        # Preserve provenance for read-only checks of failed
                        # native requests after this history migration. Publish
                        # the native binding and representation version together;
                        # neither a crash nor a later resume may mark the old
                        # overfull thread as having adopted the new format.
                        changes["upstream_context_history"] = [
                            *(session.get("upstream_context_history") or []),
                            {"thread_id": session["upstream_thread_id"],
                             "manager_context_version": session.get("manager_context_version"),
                             **({"agent_id": session["agent_id"], "agent_model_id": session.get("agent_model_id")}
                                if quota_route else {})}]
                        changes["upstream_thread_id"] = adapter.upstream_thread_id
                    if quota_route:
                        # Publish the actual executor and its model together;
                        # old native identity remains append-only evidence.
                        changes.update(
                            agent_id=selected_agent, executor_endpoint_id=selected_agent,
                            adapter_kind="codex_app_server" if selected_agent == "codex" else "antigravity_ls",
                            upstream_mode="chat" if selected_agent == "codex" else "default",
                            agent_model_id=adapter.current_model_id, **observed_settings,
                            manager_quota_block=quota_route["blocked_primary"], metadata_review=metadata_fallback,
                            answer_review_admission=answer_fallback, quota_recovery=quota_fallback,
                            expected_upstream_thread_id=session["upstream_thread_id"])
                        if selected_agent == "codex":
                            changes["codex_home"] = str(selected_codex_home)
                    if session["channel_id"] == "manager":
                        changes["goal_id"] = MANAGER_AGENT_GOAL_ID
                    # An idle Close may have won while native preparation was
                    # finishing. It must not acquire the replacement identity.
                    self.store.update_session(session_id, expected_statuses={session["status"], "resuming"}, **changes)
                elif conversation_scope(session)["kind"] == "owner_goal" and session.get("upstream_mode") != CODEX_GOAL_CHAT_MODE:
                    self.store.update_session(session_id, coordination_context_version=PROJECT_CONTEXT_VERSION)
                self.store.restore_managed_session_if_idle(
                    session_id, upstream_thread_id=adapter.upstream_thread_id,
                    upstream_mode=("chat" if selected_agent == "codex" else "default")
                    if quota_route else codex_context.managed_upstream_mode(session))
                if (isinstance(adapter, CodexAppServerAdapter) and manager_runtime is not None
                        and native_account_epoch is not None and not recovery.get("metadata_review")
                        and not recovery.get("answer_review") and not recovery.get("read_only_review")):
                    history_restore.remember_working_checkpoint(
                        adapter, work_dir=work_dir, codex_home=selected_codex_home,
                        runtime=manager_runtime, account_epoch=native_account_epoch)
        except Exception:
            history_restore.finish()
            self._drop_owned_adapter(session_id, adapter, always_close=True)
            raise
        history_restore.finish()
        return adapter

    def close_session(self, session_id: str) -> bool:
        with self.lock:
            compacting = session_id in self.compacting_history
        session = self.store.load_session(session_id) if compacting else None
        # The store atomically rejects any live/queued Turn. For an idle
        # managed Session, make Close visible to in-flight native restoration
        # before waiting for its adapter lock and transport cleanup.
        preclosed = bool(session and session.get("session_mode") != CHAT_SESSION_MODE_ATTACHED
            and self.store.close_managed_session(session_id))
        with self._session_adapter_lock(session_id):
            session = self.store.load_session(session_id)
            if session is None:
                return False
            if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
                return self.store.close_attached_session(session_id)
            if not preclosed and not self.store.close_managed_session(session_id):
                return False
            with self.lock:
                adapter = self.adapters.pop(session_id, None)
                event_buffers = [
                    buffer
                    for (buffer_session_id, _), buffer in self.turn_event_buffers.items()
                    if buffer_session_id == session_id
                ]
            for event_buffer in event_buffers:
                event_buffer.close()
            if adapter is not None:
                adapter.close_session()
            return True

    def resume_session(self, *, session_id: str, work_dir: Path, objective: str) -> dict[str, Any]:
        goal_commit = managed_session_goal_commit(
            store=self.store, registry_path=self.registry_path, session_id=session_id,
        )
        with goal_commit():
            prepared = self.store.prepared_managed_turn_request(
                session_id, include_accepted_queued=True,
            )
        if prepared is not None:
            self.submit_turn(
                session_id=session_id,
                client_turn_id=str(prepared["client_turn_id"]),
                message=str(prepared["message"]),
                attachments=prepared.get("attachments"),
                work_dir=work_dir,
                objective=objective,
                loopx_execution=bool(prepared["loopx_execution"]),
                loopx_request=prepared.get("loopx_request"),
                _goal_commit=goal_commit,
            )
            restored = self.store.load_session(session_id)
            if restored is None:
                raise KeyError("chat session was not found")
            return restored
        with self._session_adapter_lock(session_id):
            session = self._load_open_session(session_id)
            require_managed_agent_session(self, session, work_dir)
            codex_context.checked_session_home(self.codex_home, session)
            if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
                return resume_attached_agent_session(store=self.store, registry_path=self.registry_path, session_id=session_id)
            with self.lock:
                current = self.adapters.get(session_id)
                adapter_healthy = current is not None and current.healthcheck()
                active = self.store.load_turn(session_id, session["active_turn_id"]) if session.get("active_turn_id") else None
                preserve_active = adapter_healthy or self._has_live_turn_worker(session, active)
            with goal_commit():
                session, active_turn_preserved = self.store.prepare_managed_session_resume(
                    session_id, preserve_active_turn=preserve_active)
            if active_turn_preserved:
                return session
            interrupted_turn_id = str(session.get("active_turn_id") or "") or None
            adapter = self._ensure_adapter_locked(
                session, work_dir=work_dir, objective=objective,
                interrupted_turn_id=interrupted_turn_id, goal_commit=goal_commit)
            try:
                with goal_commit():
                    return self.store.restore_managed_session_if_idle(
                        session_id, upstream_thread_id=adapter.upstream_thread_id,
                        upstream_mode=codex_context.managed_upstream_mode(session))
            except Exception:
                self._drop_owned_adapter(session_id, adapter)
                raise
