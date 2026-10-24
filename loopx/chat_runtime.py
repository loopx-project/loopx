"""Runtime-neutral orchestration for durable LoopX Chat sessions."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Protocol

from .chat_manager import (
    MANAGER_AGENT_GOAL_ID, MANAGER_CONTEXT_VERSION,
    is_manager_channel, manager_agent_objective, manager_model_config,
    manager_workspace, manager_skill_text, operator_credential_pair, operator_credential_resolution,
    manager_answer_readback,
    manager_session_model_allocation,
)
from .chat_coordination import PROJECT_COORDINATION_GUIDANCE, PROJECT_CONTEXT_VERSION, apply_context_handoff
from .capabilities.native_chat import codex_context, project_context as project_context_policy
from .capabilities.native_chat.codex_adapter import CodexAppServerAdapter
from .capabilities.native_chat.machine_defaults import codex_profile_home, load_codex_chat_defaults
from .capabilities.native_chat.turn_release import TurnReleaseWakeup
from .control_plane.collaboration import conversation_scope
from .capabilities.manager_runtime import (
    load_bound_manager_runtime_profile, manager_runtime_session_fields,
)
from .capabilities.manager_context.team_plan import (
    TeamPlanProjector,
    offer_team_plan_confirmation,
    team_plan_admission_context,
)
from .capabilities.steward_executor import load_effective_steward_executor_defaults
from .capabilities.steward_executor import allocation as alloc
from .chat_acp import ACPStdioAdapter
from .chat_agent import CodexChatAgentError, CodexChatTimeoutError, agent_endpoint_error
from .chat_codex_goal import (
    CodexGoalDriver,
    UPSTREAM_MODE as CODEX_GOAL_CHAT_MODE,
    parse_native_goal_command,
    validate_goal_chat,
)
from .chat_dsh import DshChatAdapter
from .chat_endpoint_catalog import builtin_chat_endpoints
from .chat_endpoints import AgentEndpointRegistry
from .control_plane.turn_driver.host_binding import MANAGED_TURN_HOST
from .control_plane.turn_driver.execution_profile import managed_execution_profile
from .control_plane.chat_turn_acceptance import managed_turn_recovery_allowed
from .file_lock import LOCK_HOLDER_DEAD, lock_holder_liveness, try_exclusive_file_lock
from .kiro_cli_goal_mode import (
    KIRO_CLI_BIN,
    KIRO_CLI_CHAT_AGENT_ID,
    kiro_cli_chat_command,
)
from .chat_store import (
    CHAT_SESSION_MODE_ATTACHED,
    CHAT_SESSION_MODE_MANAGED,
    TERMINAL_TURN_STATES,
    ChatSessionStore,
    normalize_agent_model_id,
    utc_now,
)
from .chat_providers import ClaudeCodeAdapter, codex_model_options, direct_model_from_environment
from .capabilities.native_chat.model_settings import normalize_agent_reasoning_effort
from .attached_session import (
    ManagedSessionGoalCommit,
    enqueue_attached_agent_turn,
    managed_session_goal_commit, require_current_attached_session,
    resume_attached_agent_session,
    select_current_attached_session,
)


EventSink = Callable[[str, dict[str, Any]], None]

# These steering failures occur before the provider receives a message. A
# fresh ingress identity can safely retry the same draft after recovery.
STEERING_NOT_DELIVERED_CODES = frozenset({
    "attached_session_live_steering_unavailable",
    "live_steering_turn_mismatch",
    "live_steering_requires_active_turn",
    "live_steering_session_not_attached",
    "live_steering_turn_not_started",
})

# The opaque payload an adapter forwards to its provider. Named here so the
# worker signature that carries it between methods does not open another
# module-level `Any`.
AttachmentPayload = dict[str, Any]

# Bounded overload continuation: one original dispatch plus at most this many
# effect-free retries, each reserved in the Turn event ledger before its backoff
# so the remaining budget survives restart/compaction.
TRANSPORT_RETRY_MAX_RETRIES = 3
TRANSPORT_RETRY_BACKOFF_SECONDS = 2.0

SERVER_RESTARTED_MESSAGE = (
    "LoopX Chat restarted before this turn finished. "
    "Your conversation is saved; this turn was not retried automatically. "
    "You can continue with a follow-up in this session."
)


class ChatTurnAcceptanceUnavailableError(Exception):
    """The durable acceptance attempt can be retried with the same request."""


class ChatRuntimeAdapter(Protocol):
    @property
    def upstream_thread_id(self) -> str: ...

    def capabilities(self) -> dict[str, Any]: ...
    def start_turn(self, message: str, event_sink: EventSink) -> dict[str, Any]: ...
    def interrupt_turn(self, turn_id: str | None = None) -> None: ...
    def close_session(self) -> None: ...
    def healthcheck(self) -> bool: ...


class _TurnEventBuffer:
    """Keep live events readable in memory and checkpoint them in bounded batches."""

    def __init__(
        self,
        *,
        store: ChatSessionStore,
        session_id: str,
        turn_id: str,
        event_flush_interval_sec: float = 0.05,
        metadata_checkpoint_interval_sec: float = 0.5,
    ) -> None:
        self.store = store
        self.session_id = session_id
        self.turn_id = turn_id
        self.event_flush_interval_sec = event_flush_interval_sec
        self.metadata_checkpoint_interval_sec = metadata_checkpoint_interval_sec
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.closed = False
        self.last_checkpoint_at = time.monotonic()
        turn = store.load_turn(session_id, turn_id) or {}
        self.progress: dict[str, Any] = {
            "last_activity_at": turn.get("last_activity_at") or utc_now(),
            "first_event_at": turn.get("first_event_at"),
            "delta_count": int(turn.get("delta_count") or 0),
        }
        self.metadata_dirty = False
        self.flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self.flush_thread.start()

    def _flush_loop(self) -> None:
        while not self.stop_event.wait(self.event_flush_interval_sec):
            try:
                self.store.flush_events(self.session_id, self.turn_id)
            except Exception:
                # Pending rows remain queued; retry after the normal flush interval.
                continue

    def _checkpoint_locked(self, *, force: bool = False) -> None:
        if not self.metadata_dirty:
            return
        now = time.monotonic()
        if not force and now - self.last_checkpoint_at < self.metadata_checkpoint_interval_sec:
            return
        changes = dict(self.progress)
        if not changes.get("first_event_at"):
            changes.pop("first_event_at", None)
        self.store.update_turn(
            self.session_id,
            self.turn_id,
            expected_statuses={"starting", "running"} if "status" in changes else None,
            **changes,
        )
        self.store.update_session(
            self.session_id,
            last_activity_at=str(changes["last_activity_at"]),
        )
        self.metadata_dirty = False
        self.last_checkpoint_at = now

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        with self.lock:
            if self.closed:
                return
            now = utc_now()
            self.progress["last_activity_at"] = now
            if not self.progress.get("first_event_at"):
                self.progress["first_event_at"] = now
            if kind in {"answer.delta", "assistant.delta"}:
                self.progress["delta_count"] = int(self.progress.get("delta_count") or 0) + 1
            if kind == "turn.started":
                self.progress["status"] = "running"
                self.progress["upstream_turn_id"] = payload.get("upstream_turn_id")
            self.metadata_dirty = True
            self.store.append_event(
                self.session_id,
                self.turn_id,
                kind=kind,
                payload=payload,
                buffered=True,
            )
            self._checkpoint_locked(force=kind in {"turn.started", "native_goal.status"})

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.stop_event.set()
        self.flush_thread.join(timeout=max(1.0, self.event_flush_interval_sec * 4))
        with self.lock:
            self.store.flush_events(self.session_id, self.turn_id)
            self._checkpoint_locked(force=True)


class ChatRuntimeController:
    def __init__(
        self,
        *,
        store: ChatSessionStore,
        codex_bin: str,
        claude_bin: str = "claude",
        kiro_cli_bin: str = KIRO_CLI_BIN,
        startup_timeout_sec: float | None = None,
        idle_timeout_sec: float = 180.0,
        hard_timeout_sec: float = 900.0,
        endpoint_registry: AgentEndpointRegistry | None = None,
        registry_path: Path | None = None,
        manager_scope_resolver: Callable[[dict[str, Any]], list[str] | None] | None = None,
        project_contexts: project_context_policy.ChatProjectContexts | None = None,
    ) -> None:
        self.store = store
        self.registry_path = registry_path
        from .capabilities.native_chat.project_context import coordination_runtime_root
        self.coordination_runtime_root = coordination_runtime_root(registry_path, store.root.parent)
        self.manager_scope_resolver = manager_scope_resolver
        self.project_contexts = project_contexts or project_context_policy.ChatProjectContexts([])
        self.codex_bin = codex_bin
        # Capture once; the service's startup environment is not session identity.
        self.codex_home = Path(
            os.environ.get("LOOPX_CHAT_CODEX_HOME")
            or os.environ.get("CODEX_HOME")
            or "~/.codex"
        ).expanduser().resolve()
        self.claude_bin, self.kiro_cli_bin = claude_bin, kiro_cli_bin
        self.acp_startup_timeout_sec = startup_timeout_sec
        self.startup_timeout_sec = 30.0 if startup_timeout_sec is None else startup_timeout_sec
        self.idle_timeout_sec = idle_timeout_sec
        self.hard_timeout_sec = hard_timeout_sec
        self.endpoint_registry = endpoint_registry or AgentEndpointRegistry(store.root)
        self.adapters: dict[str, ChatRuntimeAdapter] = {}
        self.cancelled_turns: set[tuple[str, str]] = set()
        self.turn_event_buffers: dict[tuple[str, str], _TurnEventBuffer] = {}
        self.turn_done_events: dict[tuple[str, str], threading.Event] = {}
        self.turn_release_wakeup = TurnReleaseWakeup()
        self.lock = threading.RLock()
        self.session_open_locks: dict[tuple[str, str, str], threading.Lock] = {}
        self.session_adapter_locks: dict[str, threading.Lock] = {}
        self.session_queue_workers: set[str] = set()
        self.session_queue_threads: dict[str, threading.Thread] = {}
        self.session_queue_wakeups: set[str] = set()
        self.closed = threading.Event()
        from .chat_loopx_mode import ChatLoopXMode
        self.loopx_mode = ChatLoopXMode(self)
        # Optional projection of an admitted steward team preview into the typed
        # action surface. It is injected by the host that owns that surface, so a
        # controller without one still answers; the projection is what makes an
        # admitted preview reachable as a card the owner can confirm.
        self.team_plan_projector: TeamPlanProjector | None = None

    def manager_runtime_profile(
        self, channel_id: str = "manager", *,
        steward_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return load_bound_manager_runtime_profile(
            self.store.root.parent, channel_id=channel_id,
            bindings=self.project_contexts.conversation_bindings, steward_context=steward_context,
        )

    def steward_executor_defaults(self) -> dict[str, Any]:
        """Return this machine's configured steward executor, model and effort.

        The machine configuration is the operator's persistent choice for this
        machine, and the controller already owns the runtime root it lives in, so
        every steward entry point reads the same document instead of deriving a
        second answer from its own environment.
        """

        return load_effective_steward_executor_defaults(self.store.root.parent)

    def capabilities(self) -> list[dict[str, Any]]:
        builtins = builtin_chat_endpoints(
            codex_bin=self.codex_bin,
            claude_bin=self.claude_bin,
            kiro_cli_bin=self.kiro_cli_bin,
            runtime_root=self.store.root.parent,
        )
        return [*builtins, *(endpoint.public_summary() for endpoint in self.endpoint_registry.list())]

    def inspect_agent_model_options(self, agent_id: str, *, work_dir: Path, goal_id: str | None = None,
                                    agent_model_id: str | None = None) -> dict[str, Any]:
        """Read model choices from one selected, available ACP endpoint.

        This is intentionally not a PATH scan: the controller launches only an
        endpoint already present in the built-in catalog or owner-local registry.
        The temporary upstream session is closed before returning.
        """
        capability = next((row for row in self.capabilities() if row["agent_id"] == agent_id), None)
        if capability is None or not capability.get("available"):
            raise agent_endpoint_error(agent_id, reason=str((capability or {}).get("unavailable_reason") or ""))
        if agent_id == "codex":
            defaults = load_codex_chat_defaults(self.coordination_runtime_root) if goal_id else {}
            home = codex_profile_home(self.codex_home, defaults)
            models = codex_model_options(self.codex_bin, codex_home=home)
            if models is None:
                raise CodexChatAgentError("The selected Codex account did not return model choices. Check Chat account settings, then retry; existing conversations are unchanged.",
                                          error_code="provider_unavailable", gate=None)
            return {"agent_id": agent_id, "model": models, "supported": True}
        if agent_id == "claude-code":
            return {
                "agent_id": agent_id,
                "supported": True,
                "model": {
                    "id": "model", "label": "Model", "selected": "sonnet",
                    "options": [
                        {"value": "sonnet", "label": "Sonnet"},
                        {"value": "opus", "label": "Opus"},
                        {"value": "fable", "label": "Fable"},
                    ],
                },
            }
        if str(capability.get("adapter_kind")) != "acp":
            return {"agent_id": agent_id, "model": None, "supported": False}
        adapter = self._start_adapter(
            agent_id=agent_id,
            work_dir=work_dir,
            goal_id=None,
            objective="",
            agent_config={"model": agent_model_id} if agent_model_id else None,
        )
        try:
            return {"agent_id": agent_id, "model": adapter.model_options(),
                    "reasoning_effort": adapter.reasoning_options(), "supported": True}
        finally:
            adapter.close_session()

    @staticmethod
    def _observed_model_settings(adapter: ChatRuntimeAdapter, requested_effort: str | None = None,
                                 requested_model: str | None = None) -> dict[str, Any]:
        settings = adapter.model_settings if isinstance(adapter, (ACPStdioAdapter, CodexAppServerAdapter)) else {}
        if requested_model and isinstance(adapter, (ACPStdioAdapter, CodexAppServerAdapter)) and adapter.current_model_id != requested_model:
            raise CodexChatAgentError("The Agent did not confirm the requested model; no message was sent.",
                                      error_code="unsupported_configuration", gate=None)
        if requested_effort and settings.get("agent_reasoning_effort") != requested_effort:
            raise CodexChatAgentError(
                "The Agent did not confirm the requested reasoning effort; no message was sent.",
                error_code="unsupported_configuration", gate=None,
            )
        return settings

    @staticmethod
    def _managed_upstream_mode(session: dict[str, Any]) -> str:
        if session.get("agent_id") == "codex":
            return CODEX_GOAL_CHAT_MODE if session.get("upstream_mode") == CODEX_GOAL_CHAT_MODE else "chat"
        return str(session.get("upstream_mode") or "default")

    @staticmethod
    def _session_objective(
        *,
        goal_id: str | None,
        objective: str,
        project_coordination: bool = False,
    ) -> str:
        """Compose stable guidance; each provider restores history separately."""

        return objective + ("\n" + PROJECT_COORDINATION_GUIDANCE if project_coordination else "") + (
            "\n" + manager_skill_text() if goal_id == MANAGER_AGENT_GOAL_ID else ""
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
        executor_model: Mapping[str, str | None] | None = None,
        project_context: dict[str, str] | None = None,
        agent_config: dict[str, str] | None = None,
        codex_home_override: Path | None = None,
        read_only_review: bool = False,
    ) -> ChatRuntimeAdapter:
        if project_context is not None:
            project_context_policy.validate_project_native_executor(agent_id, project_context)
        if (
            manager_runtime is not None
            and manager_runtime.get("runtime_profile") == "trusted_owner"
            and agent_id != "codex"
        ):
            raise CodexChatAgentError(
                "The trusted owner manager profile requires the Codex endpoint.",
                error_code="manager_runtime_endpoint_unsupported",
                gate={
                    "kind": "host_tool_gate",
                    "summary": (
                        "The selected manager Agent cannot enforce the trusted owner "
                        "runtime profile."
                    ),
                    "next_action": (
                        "Select the Codex Agent or change Manager runtime to restricted."
                    ),
                },
            )
        if agent_id == "codex":
            from .capabilities.manager_context.inspection import READ_TOOL, CONTEXT_READ_TOOL
            from .chat_loopx_mode import TOOL as COLLABORATION_TOOL
            manager_profile = (
                dict(manager_runtime or self.manager_runtime_profile())
                if goal_id == MANAGER_AGENT_GOAL_ID
                else None
            )
            if manager_profile is not None:
                objective = manager_agent_objective(
                    str(manager_profile["runtime_profile"])
                )
            model_config = (
                executor_model or manager_model_config(
                    endpoint=agent_id,
                    machine_defaults=self.steward_executor_defaults(),
                )
                if goal_id == MANAGER_AGENT_GOAL_ID and not execution_mode
                else executor_model or {}
            )
            if agent_config and agent_config.get("model"):
                model_config = {**model_config, "model": agent_config["model"]}
            if agent_config:
                model_config = {**model_config, **{key: agent_config[key] for key in ("reasoning_effort", "service_tier") if agent_config.get(key)}}
            return CodexAppServerAdapter.start(
                codex_bin=self.codex_bin,
                codex_home=codex_home_override or self.codex_home,
                work_dir=work_dir,
                goal_id=goal_id,
                objective=self._session_objective(
                    goal_id=goal_id, objective=objective,
                    project_coordination=project_coordination and not execution_mode,
                ),
                history=history,
                resume_thread_id=resume_thread_id,
                startup_timeout_sec=self.startup_timeout_sec,
                idle_timeout_sec=self.idle_timeout_sec,
                hard_timeout_sec=self.hard_timeout_sec,
                execution_mode=execution_mode,
                runtime_profile=(
                    str(manager_profile["runtime_profile"])
                    if manager_profile is not None
                    else "restricted"
                ),
                sandbox=(
                    str(manager_profile["sandbox"])
                    if manager_profile is not None
                    else None
                ),
                project_context=project_context,
                read_only_review=read_only_review,
                model=model_config.get("model"),
                reasoning_effort=model_config.get("reasoning_effort"),
                service_tier=model_config.get("service_tier"),
                dynamic_tools=(
                    [READ_TOOL] if goal_id == MANAGER_AGENT_GOAL_ID
                    else [CONTEXT_READ_TOOL, *([COLLABORATION_TOOL] if loopx_tools else [])]
                ) if not execution_mode and not read_only_review and (
                    goal_id == MANAGER_AGENT_GOAL_ID or project_coordination
                ) else None,
            )
        if agent_id == "claude-code":
            return ClaudeCodeAdapter.start(
                claude_bin=self.claude_bin,
                work_dir=work_dir,
                resume_thread_id=resume_thread_id,
                tool_scope="read_only",
                context_summary=f"{goal_id}: {objective}".strip() if goal_id is not None else objective.strip(),
                model=agent_config.get("model") if agent_config else None,
            )
        if agent_id == MANAGED_TURN_HOST:
            # The managed host has no interactive session transport, so this
            # channel holds one bounded segment per turn on the resolved managed
            # execution profile, authenticated by the operator credential.
            operator_environ = operator_credential_resolution(self)["environ"]
            profile = managed_execution_profile(operator_environ)
            model, reasoning_effort = str(profile["model"]), str(profile["reasoning_effort"])
            if goal_id == MANAGER_AGENT_GOAL_ID:
                manager_config = executor_model or manager_model_config(
                    operator_environ, endpoint=agent_id,
                    machine_defaults=self.steward_executor_defaults(),
                )
                model, reasoning_effort = str(manager_config["model"]), str(manager_config["reasoning_effort"])
            return DshChatAdapter(
                objective=self._session_objective(
                    goal_id=goal_id, objective=objective,
                    project_coordination=project_coordination and not execution_mode,
                ),
                work_dir=work_dir,
                provider=str(profile["provider"]),
                model=model,
                reasoning_effort=reasoning_effort,
                timeout_sec=self.hard_timeout_sec,
                # A segment is fresh, so this adapter carries the visible history
                # itself instead of relying on a host session to remember it.
                history=list(history or []),
                credential=operator_credential_pair(self),
            )
        if agent_id in {"anthropic-api", "openai-api"}:
            return direct_model_from_environment(
                provider="anthropic" if agent_id == "anthropic-api" else "openai",
                work_dir=work_dir,
                session_id=resume_thread_id,
                history=history,
            )
        if agent_id == KIRO_CLI_CHAT_AGENT_ID:
            return ACPStdioAdapter.start(
                command=kiro_cli_chat_command(self.kiro_cli_bin),
                work_dir=work_dir,
                resume_thread_id=resume_thread_id,
                startup_timeout_sec=self.acp_startup_timeout_sec,
                idle_timeout_sec=self.idle_timeout_sec,
                hard_timeout_sec=self.hard_timeout_sec,
                execution_mode=execution_mode,
                config_values=agent_config,
            )
        endpoint = self.endpoint_registry.get(agent_id)
        if endpoint is not None:
            return ACPStdioAdapter.start(
                command=endpoint.command,
                work_dir=work_dir,
                agent_work_dir=endpoint.mapped_work_dir(work_dir),
                resume_thread_id=resume_thread_id,
                startup_timeout_sec=self.acp_startup_timeout_sec,
                idle_timeout_sec=self.idle_timeout_sec,
                hard_timeout_sec=self.hard_timeout_sec,
                execution_mode=execution_mode,
                config_values=agent_config,
            )
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
        if capability and capability.get("available") and agent_model_id and agent_id not in {"codex", "claude-code"} and capability.get("adapter_kind") != "acp":
            raise CodexChatAgentError("This Agent does not offer model selection.",
                                      error_code="unsupported_configuration", gate=None)
        if agent_reasoning_effort is not None:
            agent_reasoning_effort = normalize_agent_reasoning_effort(agent_reasoning_effort)
            if capability and capability.get("available") and agent_id != "codex" and capability.get("adapter_kind") != "acp":
                raise CodexChatAgentError("This Agent does not offer reasoning choices.",
                                          error_code="unsupported_configuration", gate=None)
        if mode not in {"resume_latest", "new"}:
            raise ValueError("mode must be resume_latest or new")
        selected_channel = channel_id or f"goal.{goal_id}"
        project_context = None
        steward_context = None
        if conversation_binding_id is not None:
            if goal_id is not None or agent_goal_id is not None or project_ref is not None:
                raise ValueError("bound project Chat cannot substitute a Goal or workspace")
            if source_context is None:
                raise ValueError("bound conversation authority is unavailable")
            context = self.project_contexts.open_bound(conversation_binding_id, source_context, executor=agent_id, channel_id=channel_id)
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
        if project_context is not None:
            project_context_policy.validate_project_executor_scope(agent_id, project_context, self.capabilities, capability)
        manager_runtime = (
            self.manager_runtime_profile(selected_channel, steward_context=steward_context)
            if is_manager_channel(selected_channel)
            else None
        )
        if is_manager_channel(selected_channel):
            assert manager_runtime is not None
            work_dir = manager_workspace(
                self.store.root,
                selected_channel,
                runtime_profile=str(manager_runtime["runtime_profile"]),
            )
            objective = manager_agent_objective(
                str(manager_runtime["runtime_profile"])
            )
            agent_goal_id = MANAGER_AGENT_GOAL_ID
            if selected_channel == "manager":
                goal_id = MANAGER_AGENT_GOAL_ID
        route_goal_id = "*" if is_manager_channel(selected_channel) else goal_id
        route_key = (route_goal_id, agent_id, selected_channel)
        with self.lock:
            route_lock = self.session_open_locks.setdefault(route_key, threading.Lock())
        with route_lock:
            latest = None
            if not is_manager_channel(selected_channel) and project_context is None:
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
                        "Managed Chat execution is not qualified for the "
                        "source-session profile.",
                        error_code="source_session_managed_chat_unsupported",
                        gate=None,
                    )
            if mode == "resume_latest":
                latest = self.store.latest_session(
                    goal_id=None if is_manager_channel(selected_channel) else goal_id,
                    agent_id=agent_id,
                    channel_id=selected_channel,
                )
                # Reuse only the same typed project identity. A changed host grant
                # starts a new Session while the old context and history remain intact.
                if latest is not None and project_context is not None and latest.get("project_context") != project_context:
                    latest = None
                if latest is not None and goal_instance_id is not None and latest.get("goal_instance_id") != goal_instance_id:
                    latest = None
                if latest is not None and (latest.get("active_turn_id") or latest.get("upstream_mode") == CODEX_GOAL_CHAT_MODE or latest.get("session_mode") == CHAT_SESSION_MODE_ATTACHED):
                    if ((agent_reasoning_effort and latest.get("agent_reasoning_effort") != agent_reasoning_effort)
                        or (agent_model_id and latest.get("agent_model_id") != agent_model_id)):
                        raise CodexChatAgentError("The existing execution keeps its settings. Change the next conversation after it finishes, or use its original host.",
                                                  error_code="unsupported_configuration", gate=None)
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
            if (agent_id == "codex" and goal_id != MANAGER_AGENT_GOAL_ID
                and conversation_scope({"channel_id": selected_channel, "goal_id": goal_id})["kind"] == "owner_goal"
                and not (latest and (latest.get("active_turn_id") or latest.get("upstream_mode") == CODEX_GOAL_CHAT_MODE))):
                codex_defaults = load_codex_chat_defaults(self.coordination_runtime_root)
                selected_codex_home = codex_profile_home(self.codex_home, codex_defaults)
                if latest is not None and (latest.get("codex_chat_configuration") or {}) != codex_defaults:
                    # New defaults get a new ordinary Session. Resolve them only
                    # after attached, active and Agent Loop bindings are preserved;
                    # even an unavailable new profile cannot interrupt that work.
                    latest = None
            if capability is None:
                raise agent_endpoint_error(agent_id)
            if not capability["available"]:
                raise agent_endpoint_error(
                    agent_id,
                    reason=str(capability.get("unavailable_reason") or ""),
                )
            if latest is not None:
                self._ensure_adapter(latest, work_dir=work_dir, objective=objective)
                return self.store.load_session(latest["session_id"]) or latest, True
            adapter = self._start_adapter(
                agent_id=agent_id,
                work_dir=work_dir,
                goal_id=agent_goal_id or goal_id,
                objective=objective,
                execution_mode=selected_channel.startswith("task."),
                project_coordination=conversation_scope({"channel_id": selected_channel, "goal_id": goal_id})["kind"] == "owner_goal",
                manager_runtime=manager_runtime,
                project_context=project_context,
                executor_model=alloc.manager_executor_model(manager_executor_allocation),
                agent_config={**{key: value for key, value in codex_defaults.items() if value is not None},
                              **({"model": agent_model_id} if agent_model_id else {}),
                              **({"reasoning_effort": agent_reasoning_effort} if agent_reasoning_effort else {})} or None,
                codex_home_override=selected_codex_home,
            )
            try:
                observed_settings = self._observed_model_settings(adapter, agent_reasoning_effort, agent_model_id)
                persisted = self.store.create_session(
                    goal_id=goal_id,
                    goal_instance_id=goal_instance_id,
                    agent_id=agent_id,
                    executor_endpoint_id=agent_id,
                    adapter_kind=str(capability["adapter_kind"]),
                    upstream_thread_id=adapter.upstream_thread_id,
                    upstream_mode="chat" if agent_id == "codex" else "default",
                    channel_id=selected_channel,
                    codex_home=str(codex_context.home_for_project(selected_codex_home, project_context)) if agent_id == "codex" else None,
                    project_context=project_context,
                    steward_context=steward_context,
                    agent_model_id=adapter.current_model_id if isinstance(adapter, (ACPStdioAdapter, CodexAppServerAdapter)) else None,
                    requested_agent_model_id=agent_model_id,
                    requested_agent_reasoning_effort=agent_reasoning_effort,
                    codex_chat_configuration=codex_defaults or None,
                    **observed_settings,
                )
            except Exception:
                adapter.close_session()
                raise
            persisted = self.project_contexts.initialize_bound_scope(self.store, persisted, runtime_root=self.coordination_runtime_root)
            if is_manager_channel(selected_channel):
                assert manager_runtime is not None
                persisted = self.store.update_session(
                    persisted["session_id"],
                    manager_context_version=MANAGER_CONTEXT_VERSION,
                    **alloc.manager_executor_session_fields(manager_executor_allocation),
                    **manager_runtime_session_fields(manager_runtime),
                )
            elif conversation_scope(persisted)["kind"] == "owner_goal":
                persisted = self.store.update_session(persisted["session_id"],
                    coordination_context_version=PROJECT_CONTEXT_VERSION)
            with self.lock:
                self.adapters[persisted["session_id"]] = adapter
            return persisted, False

    def _session_adapter_lock(self, session_id: str) -> threading.Lock:
        with self.lock:
            return self.session_adapter_locks.setdefault(session_id, threading.Lock())

    def _check_codex_home(self, session: dict[str, Any]) -> Path:
        if session.get("agent_id") != "codex" or session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
            return self.codex_home
        selected = codex_context.session_base_home(self.codex_home, session)
        codex_context.require_session_home(selected, session)
        return selected

    def _ensure_adapter(
        self,
        session: dict[str, Any],
        *,
        work_dir: Path,
        objective: str,
    ) -> ChatRuntimeAdapter:
        session_id = str(session["session_id"])
        with self._session_adapter_lock(session_id):
            return self._ensure_adapter_locked(
                session,
                work_dir=work_dir,
                objective=objective,
            )

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
        commit = goal_commit or nullcontext
        session_id = str(session["session_id"])
        current_session = self.store.load_session(session_id)
        if current_session is None or current_session.get("status") == "closed":
            raise KeyError("chat session was not found")
        session = current_session
        if session.get("steward_context") is not None:
            self.project_contexts.session_context(session)
        if session.get("project_context") is not None:
            context = self.project_contexts.session_context(session)
            project_context_policy.validate_project_executor_scope(str(session["agent_id"]), session["project_context"], self.capabilities)
            work_dir, objective = context["project"], context["objective"]
        manager_runtime = (
            self.manager_runtime_profile(str(session.get("channel_id") or "manager"),
                                         steward_context=session.get("steward_context"))
            if is_manager_channel(session.get("channel_id"))
            else None
        )
        if is_manager_channel(session.get("channel_id")):
            assert manager_runtime is not None
            work_dir = manager_workspace(
                self.store.root,
                str(session["channel_id"]),
                runtime_profile=str(manager_runtime["runtime_profile"]),
            )
            objective = manager_agent_objective(
                str(manager_runtime["runtime_profile"])
            )
        selected_codex_home = self._check_codex_home(session)
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
        reusable: ChatRuntimeAdapter | None = None
        # An owner-edited model takes effect between Turns. Keep a running Turn
        # and its binding intact; accepted queued work has not started upstream.
        model_allocation = None
        if manager_runtime is not None and (
            not session.get("active_turn_id")
            or (session.get("active_turn_id") == accepted_turn_id
                and (self.store.load_turn(session_id, str(accepted_turn_id)) or {}).get("status") == "queued")
        ):
            model_allocation = manager_session_model_allocation(self, session)
        legacy_project_context = (conversation_scope(session)["kind"] == "owner_goal"
            and session.get("coordination_context_version") != PROJECT_CONTEXT_VERSION
            # A context/tool refresh cannot discard a native Goal and its usage.
            # Keep this binding; a new Session explicitly selects new tools.
            and session.get("upstream_mode") != CODEX_GOAL_CHAT_MODE)
        # Refresh only the queued recovery's auth/profile; keep its native thread.
        recovery: dict[str, Any] = {}
        if self.project_contexts.conversation_bindings is not None:
            from .capabilities.native_chat.external_conversations import ChatExternalConversations
            recovery = ChatExternalConversations(self).queued_recovery_context(session, active, accepted_turn_id)
        with self.lock:
            current = self.adapters.get(session_id)
            manager_profile_changed = bool(
                manager_runtime is not None
                and (
                    session.get("manager_runtime_profile") is not None
                    or manager_runtime.get("runtime_profile") != "restricted"
                )
                and any(
                    session.get(key) != value
                    for key, value in manager_runtime_session_fields(manager_runtime).items()
                )
            )
            if (
                current is not None
                and current.healthcheck()
                and not manager_profile_changed
                and not recovery.get("quota_attempt")
                and (isinstance(current, CodexAppServerAdapter) and current.session.read_only_review) == bool(recovery.get("read_only_review"))
                and model_allocation is None
            ):
                reusable = current
            elif current is not None:
                current.close_session()
                self.adapters.pop(session_id, None)
        if reusable is not None:
            # Apply context/tool migrations only when opening an upstream session.
            # A healthy in-process adapter may own a Turn; never replace it here.
            if (
                manager_runtime is not None
                and session.get("manager_runtime_profile") is None
            ):
                self.store.update_session(
                    session_id,
                    **manager_runtime_session_fields(manager_runtime),
                )
            return reusable
        with commit():
            if accepted_turn_id is not None:
                active = self.store.load_turn(session_id, accepted_turn_id)
                if (active_id != accepted_turn_id or active is None
                    or active.get("status") != "queued"):
                    raise ValueError("accepted chat turn is no longer queued and active")
            else:
                self.store.update_session(
                    session_id,
                    status="resuming",
                    last_error_code=None,
                )
            active_turn_id = None if accepted_turn_id is not None else interrupted_turn_id or active_id
            if active_turn_id is not None:
                active = self.store.load_turn(session_id, str(active_turn_id))
                if active and active.get("status") not in TERMINAL_TURN_STATES:
                    failed = self.store.update_turn(
                        session_id,
                        str(active_turn_id),
                        expected_statuses={"queued", "starting", "running", "interrupting"},
                        status="failed",
                        error_code="server_restarted",
                        error=SERVER_RESTARTED_MESSAGE,
                        completed_at=utc_now(),
                    )
                    if failed is not None:
                        self.store.append_event(
                            session_id,
                            str(active_turn_id),
                            kind="turn.failed",
                            payload={"error_code": "server_restarted", "message": failed["error"]},
                        )
        native_recovery = None
        try:
            stored_messages = self.store.messages(session_id)
            history = [
                {
                    "role": "assistant" if item.get("role") == "agent" else "user",
                    "content": str(item.get("text") or ""),
                }
                for item in stored_messages
                if item.get("role") in {"user", "agent"}
                and (
                    accepted_turn_id is None
                    or item.get("turn_id") != accepted_turn_id
                )
            ]
            legacy_manager_context = (
                is_manager_channel(session.get("channel_id"))
                and session.get("upstream_mode") != CODEX_GOAL_CHAT_MODE
                and (
                    session.get("manager_context_version") != MANAGER_CONTEXT_VERSION
                    or manager_profile_changed
                    or model_allocation is not None
                )
            )
            legacy_codex_goal_thread = (
                session.get("agent_id") == "codex"
                and session.get("upstream_mode") not in {"chat", CODEX_GOAL_CHAT_MODE}
            )
            retry_failed_claude_session = (
                session.get("agent_id") == "claude-code"
                and (
                    session.get("last_error_code") == "provider_unavailable"
                    or bool(stored_messages and stored_messages[-1].get("role") == "error")
                )
            )
            adapter = self._start_adapter(
                agent_id=str(session["agent_id"]),
                work_dir=work_dir,
                goal_id=(
                    MANAGER_AGENT_GOAL_ID
                    if is_manager_channel(session.get("channel_id"))
                    else session["goal_id"]
                ),
                objective=objective,
                resume_thread_id=(
                    None
                    if legacy_codex_goal_thread or retry_failed_claude_session or legacy_manager_context or legacy_project_context
                    else str(session["upstream_thread_id"])
                ),
                history=(
                    history
                    if legacy_codex_goal_thread or retry_failed_claude_session or legacy_manager_context or legacy_project_context
                    or session.get("agent_id")
                    in {"anthropic-api", "openai-api", MANAGED_TURN_HOST}
                    else None
                ),
                execution_mode=str(session.get("channel_id") or "").startswith("task."),
                project_coordination=conversation_scope(session)["kind"] == "owner_goal",
                loopx_tools=session.get("loopx_tools") is True,
                executor_model=(alloc.manager_executor_model(model_allocation)
                    if model_allocation is not None else alloc.restored_executor_model(session)),
                manager_runtime=manager_runtime,
                project_context=session.get("project_context"),
                agent_config={**(session.get("codex_chat_configuration") or {}), **({"model": requested_model} if (requested_model := (
                    session.get("requested_agent_model_id", session.get("agent_model_id"))
                    if session.get("agent_id") == "codex" else session.get("agent_model_id")
                )) else {}), **({"reasoning_effort": session["requested_agent_reasoning_effort"]}
                               if session.get("requested_agent_reasoning_effort") else {})} or None,
                codex_home_override=selected_codex_home,
                read_only_review=bool(recovery.get("read_only_review")),
            )
            try:
                # Recovery reads back the provider-restored configuration.
                observed_settings = self._observed_model_settings(adapter)
                if session.get("upstream_mode") == CODEX_GOAL_CHAT_MODE:
                    if not isinstance(adapter, CodexAppServerAdapter):
                        raise CodexChatAgentError("Native Goal continuation requires a Codex adapter.", error_code="native_goal_adapter_mismatch", gate=None)
                    native_recovery = self.loopx_mode.observe_recovery(adapter, commit)
            except Exception:
                adapter.close_session()
                raise
        except Exception as exc:
            with commit():
                self.store.update_session(
                    session_id,
                    status="busy" if accepted_turn_id is not None else "resume_failed",
                    active_turn_id=accepted_turn_id,
                    last_error_code="resume_failed",
                )
            gate = exc.gate if isinstance(exc, CodexChatAgentError) else None
            raise CodexChatAgentError(
                "The previous Agent conversation could not be restored.",
                error_code="resume_failed",
                gate=gate,
            ) from exc
        try:
            with commit():
                self.loopx_mode.commit_recovery(session_id, native_recovery)
                with self.lock:
                    self.adapters[session_id] = adapter
                if isinstance(adapter, (ACPStdioAdapter, CodexAppServerAdapter)):
                    self.store.update_session(session_id, agent_model_id=adapter.current_model_id,
                        **observed_settings)
                if session.get("agent_id") == "codex" and session.get("codex_home") is None:
                    # A legacy session is bound only after successful upstream resume,
                    # not when a service happens to start in a new environment.
                    self.store.update_session(session_id, codex_home=str(codex_context.home_for_project(selected_codex_home, session.get("project_context"))))
                if is_manager_channel(session.get("channel_id")):
                    assert manager_runtime is not None
                    changes = {
                        "manager_context_version": MANAGER_CONTEXT_VERSION,
                        **manager_runtime_session_fields(manager_runtime),
                        **alloc.manager_executor_session_fields(model_allocation),
                    }
                    if legacy_manager_context and adapter.upstream_thread_id != session.get("upstream_thread_id"):
                        # Preserve provenance for read-only checks of failed
                        # native requests after this history migration. Publish
                        # the native binding and representation version together;
                        # neither a crash nor a later resume may mark the old
                        # overfull thread as having adopted the new format.
                        changes["upstream_context_history"] = [
                            *(session.get("upstream_context_history") or []),
                            {"thread_id": session["upstream_thread_id"],
                             "manager_context_version": session.get("manager_context_version")},
                        ]
                        changes["upstream_thread_id"] = adapter.upstream_thread_id
                    if session["channel_id"] == "manager":
                        changes["goal_id"] = MANAGER_AGENT_GOAL_ID
                    self.store.update_session(session_id, **changes)
                elif (conversation_scope(session)["kind"] == "owner_goal"
                      and session.get("upstream_mode") != CODEX_GOAL_CHAT_MODE):
                    self.store.update_session(session_id, coordination_context_version=PROJECT_CONTEXT_VERSION)
                self.store.restore_managed_session_if_idle(
                    session_id,
                    upstream_thread_id=adapter.upstream_thread_id,
                    upstream_mode=self._managed_upstream_mode(session),
                )
        except Exception:
            with self.lock:
                owns_adapter = self.adapters.get(session_id) is adapter
                if owns_adapter:
                    self.adapters.pop(session_id, None)
            adapter.close_session()
            raise
        return adapter

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
    ) -> tuple[dict[str, Any], bool]:
        commit = _goal_commit or nullcontext
        session = self.store.load_session(session_id)
        if session is None:
            raise KeyError("chat session was not found")
        if session.get("steward_context") is not None:
            raise ValueError("bound steward Chat requires its external source admission")
        if session.get("project_context") is not None:
            if session["project_context"].get("audience") in {"bound_owner", "bound_group"}:
                raise ValueError("bound project Chat requires its external source admission")
            context = self.project_contexts.session_context(session)
            project_context_policy.validate_project_executor_scope(str(session["agent_id"]), session["project_context"], self.capabilities)
            work_dir, objective = context["project"], context["objective"]
            if loopx_execution:
                raise ValueError("ordinary project conversations do not authorize LoopX execution")
        if parse_native_goal_command(message) is not None:
            validate_goal_chat(session, attachments)
        if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
            if attachments:
                raise ValueError("attached host session queue does not yet accept attachments")
            return enqueue_attached_agent_turn(
                store=self.store,
                registry_path=self.registry_path,
                session_id=session_id,
                client_turn_id=client_turn_id,
                message=message,
                origin="web",
            )
        with self._session_adapter_lock(session_id):
            session = self.store.load_session(session_id)
            if session is None or session.get("status") == "closed":
                raise KeyError("chat session was not found")
            self._check_codex_home(session)
            try:
                with commit():
                    if loopx_execution and session.get("loopx_tools") is not True:
                        session = self.loopx_mode.activate_tools(session, work_dir=work_dir, objective=objective)
                    accepted = self.store.accept_managed_turn(
                        session_id,
                        client_turn_id=client_turn_id,
                        message=message,
                        attachments=attachments,
                        display_message=(
                            {
                                "start": "开启 LoopX 模式，持续推进当前 Goal。",
                                "wake": "成员结果已验收，继续推进 LoopX 模式。",
                            }.get(str((loopx_request or {}).get("operation")), "恢复 LoopX 模式。")
                            if loopx_execution
                            else None
                        ),
                        loopx_execution=loopx_execution,
                        loopx_request=loopx_request,
                    )
            except OSError as exc:
                raise ChatTurnAcceptanceUnavailableError(
                    "Chat turn acceptance is temporarily unavailable."
                ) from exc
            if accepted.dispatch_required:
                adapter = self._ensure_adapter_locked(
                    session,
                    work_dir=work_dir,
                    objective=objective,
                    accepted_turn_id=str(accepted.turn["turn_id"]),
                    goal_commit=commit,
                )
        if accepted.dispatch_reason == "already_started":
            self.resume_session(
                session_id=session_id,
                work_dir=work_dir,
                objective=objective,
            )
            current = self.store.load_turn(
                session_id,
                str(accepted.turn["turn_id"]),
            )
            return current or accepted.turn, accepted.created
        if accepted.dispatch_required:
            try:
                with commit():
                    self._start_accepted_turn_worker(
                        session_id=session_id,
                        turn_id=str(accepted.turn["turn_id"]),
                        message=message,
                        attachments=attachments or [],
                        adapter=adapter,
                        loopx_execution=loopx_execution,
                    )
            except Exception:
                with self.lock:
                    owns_adapter = self.adapters.get(session_id) is adapter
                    if owns_adapter:
                        self.adapters.pop(session_id, None)
                if owns_adapter:
                    adapter.close_session()
                raise
        return accepted.turn, accepted.created

    def _start_accepted_turn_worker(
        self,
        *,
        session_id: str,
        turn_id: str,
        message: str,
        attachments: list[AttachmentPayload],
        adapter: ChatRuntimeAdapter,
        loopx_execution: bool,
    ) -> bool:
        key = (session_id, turn_id)
        done_event = threading.Event()
        with self.lock:
            if key in self.turn_done_events:
                return False
            self.turn_done_events[key] = done_event
        worker = threading.Thread(
            target=self._run_turn,
            kwargs={
                "session_id": session_id,
                "turn_id": turn_id,
                "message": message,
                "attachments": attachments,
                "adapter": adapter,
                "loopx_execution": loopx_execution,
                "done_event": done_event,
            },
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            with self.lock:
                if self.turn_done_events.get(key) is done_event:
                    self.turn_done_events.pop(key, None)
            done_event.set()
            self.turn_release_wakeup.notify()
            raise
        return True

    def steer_active_turn(
        self,
        *,
        session_id: str,
        client_ingress_id: str,
        message: str,
        expected_turn_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Steer the exact active Codex Turn with durable ingress deduplication."""

        session = self.store.load_session(session_id)
        if session is None or session.get("status") == "closed":
            raise KeyError("chat session was not found")
        if session.get("steward_context") is not None:
            raise ValueError("bound steward Chat requires its external source admission")
        if session.get("project_context") is not None:
            if session["project_context"].get("audience") in {"bound_owner", "bound_group"}:
                raise ValueError("bound project steering requires its external source admission")
            self.project_contexts.session_context(session)
        if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
            session = require_current_attached_session(
                store=self.store,
                registry_path=self.registry_path,
                session_id=session_id,
            )
        receipt, created = self.store.create_ingress_receipt(
            session_id,
            client_ingress_id=client_ingress_id,
            mode="live_steering",
            message=message,
            expected_turn_id=expected_turn_id,
        )
        if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
            capabilities = session.get("attached_capabilities")
            capabilities = capabilities if isinstance(capabilities, dict) else {}
            if capabilities.get("live_steering") is not True:
                if created:
                    self.store.update_ingress_receipt(
                        session_id,
                        client_ingress_id,
                        status="failed",
                        error_code="attached_session_live_steering_unavailable",
                    )
                raise RuntimeError("attached_session_live_steering_unavailable")
        if not created:
            if receipt.get("status") == "delivered":
                delivered_turn_id = str(receipt.get("active_turn_id") or "")
                turn = self.store.load_turn(session_id, delivered_turn_id)
                if turn is None:
                    raise RuntimeError("live_steering_turn_missing")
                return turn, False
            if receipt.get("status") == "failed" and receipt.get("error_code") in STEERING_NOT_DELIVERED_CODES:
                raise RuntimeError(str(receipt["error_code"]))
            raise RuntimeError("live_steering_delivery_unresolved")
        active_turn_id = str(session.get("active_turn_id") or "")
        if expected_turn_id is not None and active_turn_id != expected_turn_id:
            self.store.update_ingress_receipt(
                session_id, client_ingress_id, status="failed",
                error_code="live_steering_turn_mismatch",
            )
            raise RuntimeError("live_steering_turn_mismatch")
        if not active_turn_id:
            self.store.update_ingress_receipt(
                session_id,
                client_ingress_id,
                status="failed",
                error_code="live_steering_requires_active_turn",
            )
            raise RuntimeError("live_steering_requires_active_turn")
        with self.lock:
            adapter = self.adapters.get(session_id)
        if not isinstance(adapter, CodexAppServerAdapter) or not adapter.healthcheck():
            self.store.update_ingress_receipt(
                session_id,
                client_ingress_id,
                status="failed",
                error_code="live_steering_session_not_attached",
            )
            raise RuntimeError("live_steering_session_not_attached")
        upstream_turn_id = ""
        deadline = time.monotonic() + min(5.0, self.startup_timeout_sec)
        while time.monotonic() < deadline:
            turn = self.store.load_turn(session_id, active_turn_id)
            current_session = self.store.load_session(session_id) or {}
            if (current_session.get("active_turn_id") != active_turn_id
                    or (turn or {}).get("status") not in {"queued", "starting", "running"}):
                self.store.update_ingress_receipt(
                    session_id, client_ingress_id, status="failed",
                    error_code="live_steering_turn_mismatch",
                )
                raise RuntimeError("live_steering_turn_mismatch")
            upstream_turn_id = str((turn or {}).get("upstream_turn_id") or "")
            if upstream_turn_id:
                break
            time.sleep(0.02)
        if not upstream_turn_id:
            self.store.update_ingress_receipt(
                session_id,
                client_ingress_id,
                status="failed",
                error_code="live_steering_turn_not_started",
            )
            raise RuntimeError("live_steering_turn_not_started")
        try:
            adapter.steer_turn(message, upstream_turn_id)
        except Exception:
            self.store.update_ingress_receipt(
                session_id,
                client_ingress_id,
                status="failed",
                error_code="live_steering_rejected",
            )
            raise
        self.store.append_message(
            session_id,
            role="user",
            text=message,
            turn_id=active_turn_id,
        )
        self.store.append_event(
            session_id,
            active_turn_id,
            kind="turn.steered",
            payload={"client_ingress_id": client_ingress_id},
        )
        self.store.update_ingress_receipt(
            session_id,
            client_ingress_id,
            status="delivered",
            active_turn_id=active_turn_id,
            error_code=None,
        )
        turn = self.store.load_turn(session_id, active_turn_id)
        if turn is None:
            raise RuntimeError("live_steering_turn_missing")
        return turn, True

    def enqueue_turn(
        self,
        *,
        session_id: str,
        client_turn_id: str,
        message: str,
        attachments: list[AttachmentPayload] | None = None,
        work_dir: Path | None,
        objective: str,
        origin: str = "external",
        external_agent_target: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Persist a bounded same-Session Turn and dispatch it in FIFO order."""

        from .capabilities.native_chat.queued_admission import enqueue_turn
        return enqueue_turn(self, session_id=session_id, client_turn_id=client_turn_id,
            message=message, attachments=attachments, work_dir=work_dir, objective=objective,
            origin=origin, external_agent_target=external_agent_target)

    def resume_session_queue(
        self,
        *,
        session_id: str,
        work_dir: Path,
        objective: str,
    ) -> None:
        # Admission must not read session files: callers can hold their own
        # lifecycle fence here. The worker validates the session before effects.
        with self.lock:
            if self.closed.is_set():
                return
            if session_id in self.session_queue_workers:
                self.session_queue_wakeups.add(session_id)
                return
            worker = threading.Thread(
                target=self._drain_session_queue,
                kwargs={
                    "session_id": session_id,
                    "work_dir": work_dir,
                    "objective": objective,
                },
                daemon=True,
            )
            self.session_queue_workers.add(session_id)
            self.session_queue_threads[session_id] = worker
            worker.start()

    def _drain_session_queue(
        self,
        *,
        session_id: str,
        work_dir: Path,
        objective: str,
    ) -> None:
        try:
            while not self.closed.is_set():
                session = self.store.load_session(session_id)
                if (
                    self.closed.is_set()
                    or session is None
                    or session.get("status") == "closed"
                    or session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED
                ):
                    return
                active_turn_id = str(session.get("active_turn_id") or "")
                if active_turn_id:
                    with self.lock:
                        attached = self.adapters.get(session_id)
                    if attached is None or not attached.healthcheck():
                        try:
                            self._ensure_adapter(
                                session,
                                work_dir=work_dir,
                                objective=objective,
                            )
                        except Exception as exc:
                            self._fail_queue_preparation(session_id, active_turn_id, exc)
                        else:
                            continue
                    active = self.store.load_turn(session_id, active_turn_id)
                    if active and active.get("status") not in TERMINAL_TURN_STATES:
                        self.closed.wait(0.05)
                        continue
                    self.store.release_active_turn(session_id, active_turn_id)
                refreshed = self.store.load_session(session_id)
                if refreshed is None:
                    return
                with self.lock:
                    self.session_queue_wakeups.discard(session_id)
                turn = self.store.claim_next_queued_turn(session_id)
                if turn is None:
                    with self.lock:
                        if session_id in self.session_queue_wakeups:
                            continue
                        worker = self.session_queue_threads.get(session_id)
                        if worker is threading.current_thread():
                            self.session_queue_workers.discard(session_id)
                            self.session_queue_threads.pop(session_id, None)
                    return
                turn_id = str(turn["turn_id"])
                try:
                    # An empty queue needs no provider preparation. Claim real
                    # work first, keeping the existing enqueue/retirement fence
                    # above and the Turn's execution/stop checks below.
                    adapter = self._ensure_adapter(
                        self.store.load_session(session_id) or refreshed,
                        work_dir=work_dir,
                        objective=objective,
                    )
                except Exception as exc:
                    # The claimed request still owns a terminal outcome when
                    # workspace preparation or adapter restoration fails.
                    self._fail_queue_preparation(session_id, turn_id, exc)
                    continue
                done_event = threading.Event()
                with self.lock:
                    self.turn_done_events[(session_id, turn_id)] = done_event
                self._run_turn(
                    session_id=session_id,
                    turn_id=turn_id,
                    message=str(turn.get("message") or ""),
                    attachments=turn.get("attachments") or [],
                    adapter=adapter,
                    done_event=done_event,
                )
        finally:
            with self.lock:
                worker = self.session_queue_threads.get(session_id)
                if worker is threading.current_thread():
                    self.session_queue_workers.discard(session_id)
                    self.session_queue_threads.pop(session_id, None)
                    self.session_queue_wakeups.discard(session_id)

    def _fail_queue_preparation(
        self, session_id: str, turn_id: str, error: Exception,
    ) -> None:
        key = (session_id, turn_id)
        done_event = threading.Event()
        with self.lock:
            current = self.turn_done_events.get(key)
            owns_done_event = current is None
            if owns_done_event:
                self.turn_done_events[key] = done_event
            else:
                done_event = current
        logging.getLogger(__name__).error(
            "Chat queue runtime preparation failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        try:
            self._fail_turn(
                session_id, turn_id,
                error.error_code if isinstance(error, CodexChatAgentError) else "runtime_unavailable",
                str(error) if isinstance(error, CodexChatAgentError) else (
                    "The Agent runtime could not be prepared. Check the runtime installation "
                    "and configuration, then retry."
                ),
                status="failed",
                gate=error.gate if isinstance(error, CodexChatAgentError) else None,
                expected_statuses={"queued", "starting", "running"},
            )
        finally:
            done_event.set()
            self.turn_release_wakeup.notify()
            if owns_done_event:
                with self.lock:
                    if self.turn_done_events.get(key) is done_event:
                        self.turn_done_events.pop(key, None)

    def recover_exited_turn(self, session_id: str, turn_id: str | None = None) -> None:
        """Reconcile a killed local worker without starting a provider or replay.

        The execution lock records its host and PID for the worker's lifetime.
        No record, a live/foreign holder, or an attached host proves no loss.
        """
        session = self.store.load_session(session_id)
        if not session:
            return
        turn_id = turn_id or session.get("active_turn_id")
        if not turn_id:
            return
        path = self.store.turn_execution_lock_path(session_id, turn_id)
        if lock_holder_liveness(path)[0] != LOCK_HOLDER_DEAD:
            return
        # Take the execution fence without consuming its death evidence, so
        # dependency failures remain retryable. Recheck under the fence: a
        # replacement may have changed the holder since the first liveness read.
        with try_exclusive_file_lock(path, preserve_holder=True) as ownership:
            if ownership is None or lock_holder_liveness(path)[0] != LOCK_HOLDER_DEAD:
                return
            session = self.store.load_session(session_id)
            turn = self.store.load_turn(session_id, turn_id)
            if managed_turn_recovery_allowed(
                session, turn, managed=bool(session and session.get("session_mode") == CHAT_SESSION_MODE_MANAGED),
                owner_dead=True,
            ):
                self._fail_turn(
                    session_id, turn_id, "server_restarted", SERVER_RESTARTED_MESSAGE,
                    status="failed", expected_statuses={"starting", "running", "interrupting"},
                )
            else:
                # An earlier recovery may have persisted the exact failed result
                # and then lost its event, message or claim write. The typed
                # owner rejects `failed`, so only this narrow completion may
                # finish that result; it never reopens or replays the Turn.
                self._complete_recovered_terminalization(
                    session_id, turn_id, session, turn,
                )

    def _complete_recovered_terminalization(
        self,
        session_id: str,
        turn_id: str,
        session: dict[str, Any] | None,
        turn: dict[str, Any] | None,
    ) -> None:
        """Idempotently finish a recovery that already persisted `failed`.

        `_fail_turn` writes the state first and then the terminal event, error
        message and claim release. A transient store error in those follow-up
        writes leaves the exact recovery result partially applied, and the
        typed owner no longer admits the Turn. Repair only that exact result.
        """
        if (
            session is None
            or turn is None
            or session.get("session_mode") != CHAT_SESSION_MODE_MANAGED
            or turn.get("status") != "failed"
            or turn.get("error_code") != "server_restarted"
        ):
            return
        message = SERVER_RESTARTED_MESSAGE
        if not any(
            event.get("kind") == "turn.failed"
            and (event.get("payload") or {}).get("error_code") == "server_restarted"
            for event in self.store.events_after(session_id, turn_id, None)
        ):
            self.store.append_event(
                session_id,
                turn_id,
                kind="turn.failed",
                payload={"error_code": "server_restarted", "message": message},
            )
        if not any(
            item.get("turn_id") == turn_id
            and item.get("role") == "error"
            and item.get("text") == message
            for item in self.store.messages(session_id)
        ):
            self.store.append_message(
                session_id, role="error", text=message, turn_id=turn_id,
            )
        if session.get("active_turn_id") == turn_id:
            self.store.release_active_turn(
                session_id,
                turn_id,
                last_activity_at=str(turn.get("completed_at") or utc_now()),
                last_error_code="server_restarted",
            )

    def _run_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        message: str,
        attachments: list[AttachmentPayload],
        adapter: ChatRuntimeAdapter,
        done_event: threading.Event | None = None,
        loopx_execution: bool = False,
    ) -> None:
        key = (session_id, turn_id)
        if done_event is None:
            with self.lock:
                done_event = self.turn_done_events.get(key)
                if done_event is None:
                    done_event = threading.Event()
                    self.turn_done_events[key] = done_event
        # Everything runs inside the terminal release. The start fact is the
        # first durable write and is not guaranteed to land: if it raises, the
        # Turn was never dispatched, so the single-flight guard must be given
        # back here rather than left holding the key, which would make every
        # later dispatch of this same Turn a silent no-op.
        try:
            with try_exclusive_file_lock(
                self.store.turn_execution_lock_path(session_id, turn_id),
                agent_id="loopx-chat", operation="execute_chat_turn",
            ) as ownership:
                if ownership is None:
                    return
                started_turn = self.store.update_turn(
                    session_id,
                    turn_id,
                    expected_statuses={"queued"},
                    status="starting",
                    started_at=utc_now(),
                )
                if started_turn is None:
                    with self.lock:
                        self.cancelled_turns.discard(key)
                    return
                from .control_plane.effect_runtime import effect_runtime_request_scope

                # Reuse one code revision across this worker's typed decisions.
                # This caches no Session, Turn or authority facts: each checkpoint
                # still reads current state, including after a long provider wait.
                with effect_runtime_request_scope():
                    retry_binding: dict[str, str] = {}
                    while self._run_started_turn(
                        session_id=session_id, turn_id=turn_id, message=message,
                        attachments=attachments, adapter=adapter,
                        loopx_execution=loopx_execution, retry_binding=retry_binding,
                    ):
                        pass
        finally:
            done_event.set()
            self.turn_release_wakeup.notify()
            with self.lock:
                if self.turn_done_events.get(key) is done_event:
                    self.turn_done_events.pop(key, None)

    def _run_started_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        message: str,
        attachments: list[AttachmentPayload],
        adapter: ChatRuntimeAdapter,
        loopx_execution: bool,
        retry_binding: dict[str, str],
    ) -> bool | None:
        """The body of a Turn whose `queued -> starting` fact is already durable.

        `_run_turn` owns the single-flight release, so nothing here needs the
        done event.
        """
        key = (session_id, turn_id)
        event_buffer = _TurnEventBuffer(
            store=self.store,
            session_id=session_id,
            turn_id=turn_id,
        )
        with self.lock:
            self.turn_event_buffers[(session_id, turn_id)] = event_buffer

        def execution_ended() -> bool:
            from .control_plane.chat_turn_acceptance import managed_turn_execution_allowed

            with self.lock:
                if key in self.cancelled_turns:
                    self.cancelled_turns.discard(key)
                    return True
            # The persisted stop also fences a late reader after its in-memory
            # marker is consumed; it cannot borrow a newer Turn's claim.
            return not managed_turn_execution_allowed(
                self.store.load_session(session_id),
                self.store.load_turn(session_id, turn_id),
            )

        def event_sink(kind: str, payload: dict[str, Any]) -> None:
            if kind == "turn.started" and payload.get("upstream_turn_id"):
                # Bind even if stop won the starting race. This is identity
                # evidence only: it must not revive an interrupting Core Turn.
                self.store.update_turn(
                    session_id, turn_id,
                    expected_statuses={"starting", "running", "interrupting"},
                    upstream_turn_id=payload["upstream_turn_id"],
                )
            if kind == "agent.model_config":
                self.store.update_session(session_id, agent_model_id=payload.get("model_id"),
                    **({"agent_reasoning_effort": payload["agent_reasoning_effort"]} if "agent_reasoning_effort" in payload else {}))
                return
            with self.lock:
                if (session_id, turn_id) in self.cancelled_turns:
                    return
            event_buffer.emit(kind, payload)
            if kind == "native_goal.status":
                self.store.update_session(session_id, native_goal=payload)

        try:
            if execution_ended():
                return
            session = self.store.load_session(session_id) or {}
            if session.get("project_context") is not None:
                self.project_contexts.session_context(session)
                project_context_policy.validate_project_executor_scope(str(session["agent_id"]), session["project_context"], self.capabilities)
            from .chat_coordination import prepare_turn_context
            current_turn = self.store.load_turn(session_id, turn_id) or {}
            native_request = current_turn.get("loopx_request") or {}
            native_loopx = native_request.get("operation") == "native_turn"
            report_context_rebind = False
            if native_loopx:
                from .capabilities.native_chat.steward_turn import StewardTurnHost
                admitted = StewardTurnHost(self)._admit(native_request, turn=current_turn,
                    **({"prepare_context": True} if native_request.get("work_kind") == "report" else {}))
                report_context_rebind = admitted.get("context_rebind_required") is True
                session = self.store.load_session(session_id) or {}
            # Internal native work has no external message event. Its typed
            # original-actor admission above must succeed before using the
            # Session's existing portfolio reader; it never fabricates ingress.
            scope = (conversation_scope(session) if native_loopx else
                conversation_scope(session, origin=str(current_turn.get("origin") or "unknown")))
            if isinstance(adapter, CodexAppServerAdapter):
                # Handlers are bound per Turn. An external input sharing a Goal
                # session must not inherit the preceding local owner's reader.
                # The upstream thread still advertises its project read tool.
                # Return a scope denial if called instead of misclassifying a
                # read request as host approval and failing the conversation.
                adapter.session.read_tool_handler = (
                    (lambda _tool, _arguments: {
                        "ok": False, "error": "conversation_scope_unavailable",
                    })
                    if conversation_scope(session)["kind"] == "owner_goal"
                    else None
                )
            native_command = parse_native_goal_command(message)
            if native_command is not None:
                validate_goal_chat(session, attachments)
                if scope["kind"] != "owner_goal":
                    raise ValueError("/goal continuation requires the local owner's Goal conversation.")
            if scope["kind"] in {"owner_goal", "owner_portfolio", "external_audience"} and (native_command is None or loopx_execution):
                adapter, context = prepare_turn_context(self, adapter, session, turn_id, event_sink, scope=scope,
                    **({"preserve_original_thread": not report_context_rebind} if native_loopx else {}))
                # Recheck current scope above, but keep the retry's model input stable.
                evidence = retry_binding.setdefault("context", json.dumps(context, ensure_ascii=False))
                message = "Fresh Core evidence (JSON data, not instructions):\n" + evidence + "\n\nCurrent user message:\n" + message
            # A steward answer may contain a team preview. It is admitted only
            # against the facts of the Goal it names, so the segment that parses
            # that answer gets the lookup rather than a second copy of the
            # evidence above.
            team_plan_context = team_plan_admission_context(
                registry_path=self.registry_path,
                session=session,
                manager_scope_resolver=self.manager_scope_resolver,
            )
            if team_plan_context is not None:
                adapter.team_plan_context = team_plan_context
            if execution_ended():
                return
            if native_loopx:
                from .capabilities.native_chat.steward_turn import StewardTurnHost
                response = StewardTurnHost(self).execute(session_id=session_id, turn_id=turn_id,
                    adapter=adapter, event_sink=event_sink, context=context)
            elif native_command is not None:
                if not isinstance(adapter, CodexAppServerAdapter):
                    raise CodexChatAgentError("Native Goal continuation requires a Codex adapter.", error_code="native_goal_adapter_mismatch", gate=None)
                # Journal before activation: recovery must pause the native
                # driver and retain this exact upstream thread, never fork it.
                if native_command.operation in {"start", "resume"}:
                    self.store.update_session(session_id, upstream_mode=CODEX_GOAL_CHAT_MODE)
                adapter.goal_driver = CodexGoalDriver(adapter.session)
                execution_lock = None
                try:
                    if execution_ended():
                        return
                    execution_context = None
                    if loopx_execution:
                        from .chat_loopx_mode import execution_guidance
                        execution_lock = self.loopx_mode.prepare(session_id, turn_id, adapter, adapter.session.read_tool_handler, event_sink)
                        execution_context = execution_guidance(self.store.load_turn(session_id, turn_id)) + "\nFresh scoped evidence:\n" + json.dumps(context, ensure_ascii=False)
                    if execution_ended():
                        return
                    response = adapter.goal_driver.run(native_command, event_sink, execution_context=execution_context)
                finally:
                    if execution_lock is not None:
                        execution_lock.__exit__(None, None, None)
                    adapter.goal_driver = None
            else:
                from .usage_goal import observe_goal_execution
                if isinstance(adapter, CodexAppServerAdapter):
                    binding = self._transport_retry_binding(session_id, turn_id, adapter)
                    if "sha256" in retry_binding and binding != retry_binding["sha256"]:
                        raise CodexChatAgentError(
                            "The original request configuration changed; transport recovery stopped.",
                            error_code="host_gate", gate=None,
                        )
                    retry_binding["sha256"] = binding
                # Manager/native/external conversations have different execution
                # and waiting boundaries. Do not invent Goal timing for them.
                observed_goal = str(session.get("goal_id") or "") if scope["kind"] == "owner_goal" else ""
                with observe_goal_execution(self.store.root.parent, observed_goal, host=str(session.get("agent_id") or "unknown")):
                    if attachments:
                        if not isinstance(adapter, CodexAppServerAdapter):
                            raise ValueError("image attachments currently require the Codex Agent endpoint")
                        response = adapter.start_turn_with_attachments(message, event_sink, attachments)
                    else:
                        response = adapter.start_turn(message, event_sink)
            if execution_ended():
                event_buffer.close()
                return
            if response.get("context_handoff") is not None:
                if scope["kind"] not in {"owner_goal", "owner_portfolio", "external_audience"}:
                    raise ValueError("This workspace conversation cannot hand off to a Goal or Agent.")
                response = apply_context_handoff(
                    self, scope, session,
                    self.store.load_turn(session_id, turn_id) or {}, response, context,
                    execution_allowed=lambda: not execution_ended())
            response = offer_team_plan_confirmation(
                store=self.store,
                session=session,
                session_id=session_id,
                turn_id=turn_id,
                response=response,
                projector=self.team_plan_projector,
            )
            # The owner manager channel states its answer shape in the answer
            # itself, so the shape is recorded with the turn instead of being
            # re-derived by every reader.
            response = manager_answer_readback(
                response, channel=str(session.get("channel_id") or "")
            )
            event_buffer.close()
            if execution_ended():
                return
            completed = utc_now()
            completed_turn = self.store.update_turn(
                session_id,
                turn_id,
                expected_statuses={"starting", "running"},
                status="completing",
                response=response,
                completed_at=completed,
                last_activity_at=completed,
            )
            if completed_turn is None:
                return
            if adapter.upstream_thread_id != str((self.store.load_session(session_id) or {}).get("upstream_thread_id") or ""):
                self.store.update_session(session_id, upstream_thread_id=adapter.upstream_thread_id)
            self.store.finalize_managed_turn_completion(
                session_id,
                turn_id,
            )
        except CodexChatTimeoutError as exc:
            event_buffer.close()
            if execution_ended():
                return
            try:
                adapter.interrupt_turn()
            except Exception:
                pass
            self._fail_turn(session_id, turn_id, exc.error_code, str(exc), status="timed_out")
        except CodexChatAgentError as exc:
            event_buffer.close()
            if execution_ended():
                return
            if (not loopx_execution and parse_native_goal_command(message) is None
                    and isinstance(adapter, CodexAppServerAdapter)
                    and exc.error_code in {"host_gate", "upstream_transport_failure", "server_overloaded"}
                    and self._prepare_transport_retry(
                        session_id, turn_id, adapter, retry_binding, exc.native_error_info,
                        routing_timeout=exc.native_routing_timeout,
                    )):
                return True
            if execution_ended():
                return
            self._fail_turn(session_id, turn_id, exc.error_code, str(exc), status="failed", gate=exc.gate)
            if not adapter.healthcheck():
                with self.lock:
                    if self.adapters.get(session_id) is adapter:
                        self.adapters.pop(session_id, None)
                        self.store.update_session(
                            session_id,
                            status="stale",
                            last_error_code="transport_disconnected",
                        )
        except Exception as exc:  # noqa: BLE001 - preserve compact runtime failure.
            event_buffer.close()
            if execution_ended():
                return
            self._fail_turn(session_id, turn_id, "runtime_error", str(exc), status="failed")
        finally:
            event_buffer.close()
            with self.lock:
                self.turn_event_buffers.pop(key, None)

    def _transport_retry_binding(
        self, session_id: str, turn_id: str, adapter: CodexAppServerAdapter,
    ) -> str:
        """Compare the original request and current grants/config, not prose."""
        session = self.store.load_session(session_id) or {}
        turn = self.store.load_turn(session_id, turn_id) or {}
        self._check_codex_home(session)
        if session.get("project_context") is not None or session.get("steward_context") is not None:
            self.project_contexts.session_context(session)
        if session.get("project_context") is not None:
            project_context_policy.validate_project_executor_scope(
                str(session["agent_id"]), session["project_context"], self.capabilities,
            )
        manager = (self.manager_runtime_profile(
            str(session.get("channel_id") or "manager"),
            steward_context=session.get("steward_context"),
        ) if is_manager_channel(session.get("channel_id")) else None)
        native = adapter.session
        facts = {
            "session": {key: value for key, value in session.items() if key not in {
                "status", "active_turn_id", "last_activity_at", "last_error_code", "updated_at",
            }},
            "request": {key: turn.get(key) for key in (
                "turn_id", "client_turn_id", "host_claim_id", "message", "attachments", "origin",
            )},
            "native": {key: getattr(native, key) for key in (
                "thread_id", "model", "reasoning_effort", "sandbox", "permissions_profile",
                "runtime_profile", "project_context", "execution_mode",
            )},
            "manager": manager,
            "allocation": manager_session_model_allocation(self, session) if manager else None,
        }
        return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _prepare_transport_retry(
        self, session_id: str, turn_id: str, adapter: CodexAppServerAdapter,
        retry_binding: dict[str, str], native_error_info: dict[str, Any] | str | None,
        *, routing_timeout: bool = False,
    ) -> bool:
        """A bounded same-worker retry chain, reserved durably before each backoff.

        The existing queued CAS and Core active claim own execution. The event
        ledger retains every attempt and fixes the remaining budget; a new worker
        never reopens starting/terminal Turns or resets this allowance, and other
        adapters never decide whether to replay a model request.
        """
        from .control_plane.chat_turn_acceptance import managed_turn_execution_allowed

        def current() -> bool:
            with self.lock:
                attached = self.adapters.get(session_id) is adapter
            return (attached and not self.closed.is_set() and adapter.healthcheck()
                    and managed_turn_execution_allowed(
                        self.store.load_session(session_id), self.store.load_turn(session_id, turn_id),
                    ) and retry_binding.get("sha256") == self._transport_retry_binding(session_id, turn_id, adapter)
                    and not any(row.get("kind") == "turn.steered"
                                for row in self.store.events_after(session_id, turn_id, None)))

        try:
            events = self.store.events_after(session_id, turn_id, None)
            attempt = len([row for row in events if row.get("kind") == "transport.retry_planned"]) + 1
            if ((not isinstance(native_error_info, dict) and native_error_info != "serverOverloaded"
                 and not (native_error_info == "other" and routing_timeout))
                    or attempt > TRANSPORT_RETRY_MAX_RETRIES or not current()):
                return False
            original = str((self.store.load_turn(session_id, turn_id) or {}).get("upstream_turn_id") or "")
            proof = adapter.session.transport_retry_readback(original) if original else None
            if (proof is None or proof["codex_error_info"] != native_error_info or not current()
                    or (native_error_info == "other" and proof.get("pre_execution_routing_timeout") is not True)):
                return False
            delay = TRANSPORT_RETRY_BACKOFF_SECONDS * attempt
            self.store.append_event(session_id, turn_id, kind="transport.retry_planned", payload={
                "attempt": attempt, "upstream_turn_id": original,
                "binding_sha256": retry_binding["sha256"], "readback": proof,
                "not_before": (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(),
            })
            label = ("模型暂时繁忙" if native_error_info == "serverOverloaded"
                     else "上游路由暂时不可用" if native_error_info == "other" else "连接暂时中断")
            self.store.append_event(session_id, turn_id, kind="agent.phase", payload={
                "phase": "retrying",
                "label": f"{label}，已核验本轮未执行工具；正在原会话继续（第 {attempt}/{TRANSPORT_RETRY_MAX_RETRIES} 次重试）。",
            })
            if self.closed.wait(delay) or not current():
                return False
            # Stop/config changes during the final native read also win. A tool
            # or new input appearing late cannot borrow the earlier proof.
            return adapter.session.transport_retry_readback(original) == proof and current()
        except (CodexChatAgentError, KeyError, OSError, TypeError, ValueError):
            return False

    def _fail_turn(
        self,
        session_id: str,
        turn_id: str,
        error_code: str,
        message: str,
        *,
        status: str,
        gate: dict[str, Any] | None = None,
        expected_statuses: set[str] | None = None,
    ) -> None:
        completed = utc_now()
        failed = self.store.update_turn(
            session_id,
            turn_id,
            expected_statuses=expected_statuses if expected_statuses is not None else {"starting", "running"},
            status=status,
            error_code=error_code,
            error=message,
            completed_at=completed,
            last_activity_at=completed,
        )
        if failed is None:
            return
        payload: dict[str, Any] = {"error_code": error_code, "message": message}
        if gate:
            payload["gate"] = gate
        self.store.append_event(session_id, turn_id, kind="turn.failed", payload=payload)
        self.store.append_message(session_id, role="error", text=message, turn_id=turn_id)
        self.store.release_active_turn(
            session_id,
            turn_id,
            last_activity_at=completed,
            last_error_code=error_code,
        )

    def interrupt_turn(self, *, session_id: str, turn_id: str) -> dict[str, Any]:
        with self._session_adapter_lock(session_id):
            turn = self.store.load_turn(session_id, turn_id)
            if turn is None:
                raise KeyError("chat turn was not found")
            session = self.store.load_session(session_id)
            if (
                session
                and session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED
                and session.get("active_turn_id") == turn_id
            ):
                raise CodexChatAgentError(
                    "The attached host does not expose interrupt control to LoopX Chat.",
                    error_code="attached_session_interrupt_unavailable",
                    gate={
                        "kind": "host_tool_gate",
                        "summary": "The active Turn is owned by the attached host.",
                        "next_action": "Stop the Turn in the attached host, then retry.",
                    },
                )
            prepared = self.store.prepared_managed_turn_request(
                session_id,
                turn_id=turn_id,
            )
            if prepared is not None:
                accepted = self.store.accept_managed_turn(
                    session_id,
                    client_turn_id=str(prepared["client_turn_id"]),
                    message=str(prepared["message"]),
                    attachments=prepared.get("attachments"),
                    origin=str(prepared["origin"]),
                    display_message=str(prepared["display_message"]),
                    loopx_execution=bool(prepared["loopx_execution"]),
                    loopx_request=prepared.get("loopx_request"),
                )
                turn = accepted.turn
            if turn.get("status") in TERMINAL_TURN_STATES:
                return turn
            # Persist stop intent before changing status or contacting native
            # IO, so a service crash cannot turn an interrupted stop into an
            # automatic review of the work the user just cancelled.
            self.store.append_event(session_id, turn_id, kind="turn.interrupt_requested", payload={})
            interrupting = self.store.update_turn(
                session_id,
                turn_id,
                expected_statuses={"queued", "starting", "running", "interrupting"},
                status="interrupting",
            )
        if interrupting is None:
            current = self.store.load_turn(session_id, turn_id)
            if current is None:
                raise KeyError("chat turn was not found")
            if current.get("status") == "completing":
                return self.store.finalize_turn_completion(
                    session_id,
                    turn_id,
                ) or current
            return current
        session = self.store.load_session(session_id)
        target_is_active = bool(session and session.get("active_turn_id") == turn_id)
        with self.lock:
            adapter = self.adapters.get(session_id)
            event_buffer = self.turn_event_buffers.get((session_id, turn_id))
            done_event = self.turn_done_events.get((session_id, turn_id))
            self.cancelled_turns.add((session_id, turn_id))
        try:
            if target_is_active:
                with self._session_adapter_lock(session_id):
                    upstream_turn_id = str((self.store.load_turn(session_id, turn_id) or {}).get("upstream_turn_id") or "")
                    if isinstance(adapter, CodexAppServerAdapter) and adapter.goal_driver is None:
                        # Never fall back to the preceding native Turn while a
                        # turn/start request is still binding the current one.
                        deadline = time.monotonic() + adapter.session.response_timeout_sec
                        while not upstream_turn_id and done_event is not None and not done_event.is_set():
                            if time.monotonic() >= deadline:
                                raise TimeoutError("native Turn identity was not confirmed")
                            done_event.wait(timeout=0.02)
                            upstream_turn_id = str((self.store.load_turn(session_id, turn_id) or {}).get("upstream_turn_id") or "")
                        if upstream_turn_id:
                            adapter.interrupt_turn(upstream_turn_id)
                    elif adapter is not None:
                        adapter.interrupt_turn(upstream_turn_id or None)
                    elif upstream_turn_id or turn.get("status") in {"running", "interrupting"}:
                        raise RuntimeError("active Turn transport is unavailable")
            if done_event is not None and not done_event.wait(timeout=5.0):
                raise TimeoutError("Turn worker has not acknowledged stop")
        except Exception as exc:
            message = "停止尚未确认；当前会话仍保留执行屏障，请重试停止。"
            self.store.update_turn(session_id, turn_id, expected_statuses={"interrupting"},
                                   error_code="runtime_error", error=message)
            self.store.append_message(session_id, role="error", text=message, turn_id=turn_id)
            raise CodexChatAgentError(message, gate=None, error_code="runtime_error") from exc
        if event_buffer is not None:
            event_buffer.close()
        completed = utc_now()
        updated = self.store.update_turn(
            session_id,
            turn_id,
            expected_statuses={"interrupting"},
            status="interrupted",
            error_code=None,
            error=None,
            completed_at=completed,
            last_activity_at=completed,
        )
        with self.lock:
            self.cancelled_turns.discard((session_id, turn_id))
        if updated is None:
            current = self.store.load_turn(session_id, turn_id)
            if current is None:
                raise KeyError("chat turn was not found")
            return current
        self.store.append_event(session_id, turn_id, kind="turn.interrupted", payload={})
        self.store.append_message(
            session_id,
            role="agent",
            text="已中断。你可以在当前会话继续发送消息。",
            turn_id=turn_id,
        )
        if target_is_active:
            self.store.release_active_turn(
                session_id,
                turn_id,
                last_activity_at=completed,
                last_error_code=None,
            )
        return updated

    def wait_for_turn(self, *, session_id: str, turn_id: str, timeout_sec: float = 920.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        while True:
            if (turn := self.store.load_turn(session_id, turn_id)) is None:
                raise KeyError("chat turn was not found")
            with self.lock:
                done_event = self.turn_done_events.get((session_id, turn_id))
            remaining = deadline - time.monotonic()
            if turn.get("status") in TERMINAL_TURN_STATES:
                if done_event is None or done_event.is_set():
                    return turn
                if remaining <= 0:
                    raise TimeoutError("chat turn wait timed out")
                done_event.wait(remaining)
                continue
            if remaining <= 0:
                raise TimeoutError("chat turn wait timed out")
            (done_event.wait if done_event else time.sleep)(remaining if done_event else min(0.02, remaining))

    def close_session(self, session_id: str) -> bool:
        with self._session_adapter_lock(session_id):
            session = self.store.load_session(session_id)
            if session is None:
                return False
            if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
                return self.store.close_attached_session(session_id)
            closed = self.store.close_managed_session(session_id)
            if not closed:
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
            session = self.store.load_session(session_id)
            if session is None or session.get("status") == "closed":
                raise KeyError("chat session was not found")
            self._check_codex_home(session)
            if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
                return resume_attached_agent_session(
                    store=self.store,
                    registry_path=self.registry_path,
                    session_id=session_id,
                )
            with self.lock:
                current = self.adapters.get(session_id)
                adapter_healthy = current is not None and current.healthcheck()
            with goal_commit():
                session, active_turn_preserved = self.store.prepare_managed_session_resume(
                    session_id, preserve_active_turn=adapter_healthy,
                )
            if active_turn_preserved:
                return session
            interrupted_turn_id = str(session.get("active_turn_id") or "") or None
            adapter = self._ensure_adapter_locked(
                session,
                work_dir=work_dir,
                objective=objective,
                interrupted_turn_id=interrupted_turn_id,
                goal_commit=goal_commit,
            )
            try:
                with goal_commit():
                    return self.store.restore_managed_session_if_idle(
                        session_id, upstream_thread_id=adapter.upstream_thread_id,
                        upstream_mode=self._managed_upstream_mode(session))
            except Exception:
                with self.lock:
                    owns_adapter = self.adapters.get(session_id) is adapter
                    if owns_adapter:
                        self.adapters.pop(session_id, None)
                if owns_adapter:
                    adapter.close_session()
                raise

    def close(self) -> None:
        self.closed.set()
        with self.lock:
            adapters = list(self.adapters.values())
            event_buffers = list(self.turn_event_buffers.values())
            done_events = list(self.turn_done_events.values())
            queue_threads = list(self.session_queue_threads.values())
            self.adapters.clear()
            self.turn_event_buffers.clear()
        for done_event in done_events:
            done_event.wait(timeout=0.5)
        for event_buffer in event_buffers:
            event_buffer.close()
        for adapter in adapters:
            adapter.close_session()
        for done_event in done_events:
            done_event.wait(timeout=1.0)
        for queue_thread in queue_threads:
            queue_thread.join(timeout=1.0)
