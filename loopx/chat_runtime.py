"""Runtime-neutral orchestration for durable LoopX Chat sessions."""

from __future__ import annotations

import errno
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, NoReturn, Protocol

from .chat_manager import (
    MANAGER_AGENT_GOAL_ID,
    is_manager_channel, manager_model_config, manager_skill_text,
    operator_credential_pair as operator_credential_pair,
    operator_credential_resolution as operator_credential_resolution,
    manager_answer_readback,
    manager_session_model_allocation as manager_session_model_allocation,
    manager_session_quota_route as manager_session_quota_route,
)
from .chat.coordination import PROJECT_COORDINATION_GUIDANCE, apply_context_handoff
from .chat.session_open import ChatSessionLifecycleMixin
from .capabilities.native_chat import codex_context, project_context as project_context_policy
from .capabilities.native_chat.codex_adapter import CodexAppServerAdapter, start_attachment_turn
from .capabilities.native_chat.steward_managed_mode import require_managed_agent_session
from .capabilities.native_chat.turn_release import TurnReleaseWakeup
from .control_plane.collaboration import conversation_scope
from .capabilities.manager_runtime import load_bound_manager_runtime_profile
from .capabilities.manager_context.team_plan import (
    TeamPlanProjector,
    offer_team_plan_confirmation,
    team_plan_admission_context,
)
from .capabilities.steward_executor import load_effective_steward_executor_defaults
from .chat_agent import CodexChatAgentError, CodexChatTimeoutError
from .chat_codex_goal import (
    CodexGoalDriver,
    UPSTREAM_MODE as CODEX_GOAL_CHAT_MODE,
    parse_native_goal_command,
    validate_goal_chat,
)
from .chat_dsh import DshChatAdapter as DshChatAdapter
from .chat.endpoint_catalog import builtin_chat_endpoints
from .chat.endpoints import AgentEndpointRegistry
from .control_plane.chat_turn_acceptance import managed_turn_recovery_allowed
from .file_lock import (
    LOCK_HOLDER_DEAD, LOCK_HOLDER_LIVE, LOCK_HOLDER_RELEASED,
    _lock_id, lock_holder_host_label, lock_holder_liveness, process_is_alive, try_exclusive_file_lock,
)
from .kiro_cli_goal_mode import KIRO_CLI_BIN
from .chat_store import (
    CHAT_SESSION_MODE_ATTACHED,
    CHAT_SESSION_MODE_MANAGED,
    TERMINAL_TURN_STATES,
    ChatSessionStore,
    _opaque_id,
    utc_now,
)
from .capabilities.native_chat.model_settings import (
    configure_session_model, inspect_agent_model_options,
    observed_model_settings, session_model_settings_pending,
)
from .attached_session import (
    ManagedSessionGoalCommit,
    enqueue_attached_agent_turn,
    require_current_attached_session,
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
WORKER_EXITED_CODE = "execution_worker_exited"
BEFORE_DISPATCH_CODE = "execution_worker_exited_before_dispatch"
BEFORE_DISPATCH_MESSAGE = "The LoopX Chat worker exited before the original request was dispatched."
WORKER_EXITED_MESSAGE = (
    "The LoopX Chat execution worker exited before this turn finished. "
    "Your conversation is saved; execution may have occurred. "
    "This turn was not retried automatically."
)


class ChatTurnAcceptanceUnavailableError(Exception):
    """The durable acceptance attempt can be retried with the same request."""


class ChatSessionConfigurationRejectedError(CodexChatAgentError):
    """Configuration preparation failed before this request's Turn admission."""


def _steward_dsh_model_config(controller: ChatRuntimeController, environ: Mapping[str, str], endpoint: str) -> Mapping[str, str | None]:
    return manager_model_config(environ, endpoint=endpoint, machine_defaults=controller.steward_executor_defaults())




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
        try:
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
        except OSError as exc:
            if force or exc.errno != errno.ENOSPC:
                raise
            # An ancillary progress snapshot must not abandon a live provider.
            # Continue only after its primary event journal is durable; a full
            # journal must not accumulate an unbounded in-memory backlog.
            self.store.flush_events(self.session_id, self.turn_id)
            # Keep it dirty for the next normal checkpoint, without a hot retry
            # or another model invocation. Start/close still require persistence.
            self.last_checkpoint_at = now
            return
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


class ChatRuntimeController(ChatSessionLifecycleMixin):
    def __init__(
        self,
        *,
        store: ChatSessionStore,
        codex_bin: str,
        claude_bin: str = "claude",
        kiro_cli_bin: str = KIRO_CLI_BIN,
        startup_timeout_sec: float | None = None,
        idle_timeout_sec: float = 180.0,
        hard_timeout_sec: float | None = None,
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
        self.compacting_history: set[str] = set()
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
        self.original_task_action_store = None
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
        return inspect_agent_model_options(self, agent_id, work_dir=work_dir, goal_id=goal_id, agent_model_id=agent_model_id)

    def configure_session_model(self, session_id: str, *, model: str, effort: str | None = None) -> dict[str, Any]:
        return configure_session_model(self, session_id, model=model, effort=effort)

    _observed_model_settings = staticmethod(observed_model_settings)

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

    def _load_open_session(self, session_id: str) -> dict[str, Any]:
        session = self.store.load_session(session_id)
        if session is None or session.get("status") == "closed":
            raise KeyError("chat session was not found")
        return session

    def _validate_project_session(self, session: Mapping[str, Any]) -> tuple[Path, str]:
        context = self.project_contexts.session_context(session)
        project_context_policy.validate_project_executor_scope(
            str(session["agent_id"]), session["project_context"], self.capabilities)
        return context["project"], context["objective"]

    def _drop_owned_adapter(self, session_id: str, adapter: ChatRuntimeAdapter, *, always_close: bool = False) -> None:
        with self.lock:
            owns_adapter = self.adapters.get(session_id) is adapter
            if owns_adapter:
                self.adapters.pop(session_id, None)
        if owns_adapter or always_close:
            adapter.close_session()

    def _release_done_event(
        self,
        key: tuple[str, str],
        done_event: threading.Event,
        *,
        drop_event: bool = True,
        drop_before_notify: bool = False,
    ) -> None:
        def drop() -> None:
            with self.lock:
                if self.turn_done_events.get(key) is done_event:
                    self.turn_done_events.pop(key, None)

        if drop_event and drop_before_notify:
            drop()
        done_event.set()
        self.turn_release_wakeup.notify()
        if drop_event and not drop_before_notify:
            drop()

    def _session_adapter_lock(self, session_id: str) -> threading.Lock:
        with self.lock:
            return self.session_adapter_locks.setdefault(session_id, threading.Lock())

    def _has_live_turn_worker(self, session: dict[str, Any], turn: dict[str, Any] | None) -> bool:
        """Under the controller lock, distinguish local work from model transport."""
        if not turn or turn.get("status") not in {"starting", "running", "interrupting"}:
            return False
        done = self.turn_done_events.get((session["session_id"], turn["turn_id"]))
        return done is not None and not done.is_set()

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
    ) -> tuple[dict[str, Any], bool]:
        commit = _goal_commit or nullcontext
        session = self.store.load_session(session_id)
        if session is None:
            raise KeyError("chat session was not found")
        from .chat_attachments import require_text_document_route
        require_text_document_route(session, attachments)
        if session.get("steward_context") is not None:
            raise ValueError("bound steward Chat requires its external source admission")
        if session.get("project_context") is not None:
            if session["project_context"].get("audience") in {"bound_owner", "bound_group"}:
                raise ValueError("bound project Chat requires its external source admission")
            work_dir, objective = self._validate_project_session(session)
            if loopx_execution:
                raise ValueError("ordinary project conversations do not authorize LoopX execution")
        if parse_native_goal_command(message) is not None:
            validate_goal_chat(session, attachments)
        if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
            return enqueue_attached_agent_turn(
                store=self.store, registry_path=self.registry_path, session_id=session_id,
                client_turn_id=client_turn_id, message=message, origin="web", attachments=attachments)
        with self._session_adapter_lock(session_id):
            session = self._load_open_session(session_id)
            require_managed_agent_session(self, session, work_dir)
            codex_context.checked_session_home(self.codex_home, session)
            active = self.store.load_turn(session_id, str(session["active_turn_id"])) if session.get("active_turn_id") else None
            if (session_model_settings_pending(session)
                and (active is None or active.get("status") in TERMINAL_TURN_STATES)
                and self.store.turn_for_client(session_id, _opaque_id(client_turn_id, field="client_turn_id")) is None):
                # Existing ingress identity belongs to the acceptance/replay
                # owner, even if later settings cannot start a new Turn.
                try:
                    self._ensure_adapter_locked(session, work_dir=work_dir, objective=objective, goal_commit=commit)
                except CodexChatAgentError as exc:
                    raise ChatSessionConfigurationRejectedError(str(exc), error_code=exc.error_code, gate=exc.gate) from exc
                except (KeyError, ValueError, OSError) as exc:
                    raise ChatSessionConfigurationRejectedError(
                        "The conversation configuration could not be prepared; no message was sent.",
                        error_code="resume_failed", gate=None) from exc
                session = self.store.load_session(session_id) or session
            try:
                with commit():
                    if loopx_execution and session.get("loopx_tools") is not True:
                        session = self.loopx_mode.activate_tools(session, work_dir=work_dir, objective=objective)
                    accepted = self.store.accept_managed_turn(
                        session_id, client_turn_id=client_turn_id, message=message, attachments=attachments,
                        display_message=({"start": "开启 LoopX 模式，持续推进当前 Goal。",
                                          "wake": "成员结果已验收，继续推进 LoopX 模式。"}.get(
                                              str((loopx_request or {}).get("operation")), "恢复 LoopX 模式。")
                                         if loopx_execution else display_message),
                        loopx_execution=loopx_execution, loopx_request=loopx_request)
            except OSError as exc:
                raise ChatTurnAcceptanceUnavailableError("Chat turn acceptance is temporarily unavailable.") from exc
            if accepted.dispatch_required:
                adapter = self._ensure_adapter_locked(
                    session, work_dir=work_dir, objective=objective,
                    accepted_turn_id=str(accepted.turn["turn_id"]), goal_commit=commit)
        if accepted.dispatch_reason == "already_started":
            self.resume_session(session_id=session_id, work_dir=work_dir, objective=objective)
            return self.store.load_turn(session_id, str(accepted.turn["turn_id"])) or accepted.turn, accepted.created
        if accepted.dispatch_required:
            # The session owns the prepared adapter. A retry can select it
            # before registering its worker, so a failed dispatch attempt only
            # releases its own worker slot.
            with commit():
                self._start_accepted_turn_worker(
                    session_id=session_id, turn_id=str(accepted.turn["turn_id"]), message=message,
                    attachments=attachments or [], adapter=adapter, loopx_execution=loopx_execution)
        return accepted.turn, accepted.created

    def _start_accepted_turn_worker(
        self, *, session_id: str, turn_id: str, message: str,
        attachments: list[AttachmentPayload], adapter: ChatRuntimeAdapter, loopx_execution: bool,
    ) -> bool:
        key = (session_id, turn_id)
        done_event = threading.Event()
        with self.lock:
            if key in self.turn_done_events:
                return False
            self.turn_done_events[key] = done_event
        worker = threading.Thread(target=self._run_turn, kwargs={
            "session_id": session_id, "turn_id": turn_id, "message": message, "attachments": attachments,
            "adapter": adapter, "loopx_execution": loopx_execution, "done_event": done_event}, daemon=True)
        try:
            worker.start()
        except Exception:
            self._release_done_event(key, done_event, drop_before_notify=True)
            raise
        return True

    def _require_steering_session(self, session_id: str) -> dict[str, Any]:
        session = self._load_open_session(session_id)
        require_managed_agent_session(self, session, Path(session.get("managed_workspace_path") or "."))
        if session.get("steward_context") is not None:
            raise ValueError("bound steward Chat requires its external source admission")
        if session.get("project_context") is not None:
            if session["project_context"].get("audience") in {"bound_owner", "bound_group"}:
                raise ValueError("bound project steering requires its external source admission")
            self.project_contexts.session_context(session)
        if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
            session = require_current_attached_session(store=self.store, registry_path=self.registry_path, session_id=session_id)
        return session

    def steer_queued_turn(
        self, *, session_id: str, queued_turn_id: str, expected_turn_id: str,
        client_ingress_id: str,
    ) -> dict[str, Any]:
        """Transfer original queued input under the automatic consumer's fences.

        Only a proven non-delivery restores FIFO eligibility. A lost native
        acknowledgement keeps the original input durably reserved.
        """
        from .capabilities.native_chat.queued_admission import steer_queued_turn

        self._require_steering_session(session_id)
        return steer_queued_turn(self, session_id=session_id, queued_turn_id=queued_turn_id,
            expected_turn_id=expected_turn_id, client_ingress_id=client_ingress_id)

    def steer_active_turn(
        self, *, session_id: str, client_ingress_id: str, message: str,
        expected_turn_id: str | None = None, _queued_turn_id: str | None = None,
        origin: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Steer the exact active Codex Turn with durable ingress deduplication."""
        session = self._require_steering_session(session_id)
        with self.store.steering_delivery_lock(session_id, client_ingress_id):
            return self._steer_active_turn(session=session, session_id=session_id,
                client_ingress_id=client_ingress_id, message=message, expected_turn_id=expected_turn_id,
                _queued_turn_id=_queued_turn_id, origin=origin)

    def _steer_active_turn(
        self, *, session: Mapping[str, object], session_id: str, client_ingress_id: str,
        message: str, expected_turn_id: str | None, _queued_turn_id: str | None, origin: str | None,
    ) -> tuple[dict[str, object], bool]:
        def fail_ingress(error_code: str, *, record: bool = True) -> NoReturn:
            if record:
                self.store.update_ingress_receipt(
                    session_id, client_ingress_id, status="failed", error_code=error_code,
                )
            raise RuntimeError(error_code)

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
                fail_ingress("attached_session_live_steering_unavailable", record=created)
        if not created:
            if receipt.get("status") == "delivered":
                delivered_turn_id = str(receipt.get("active_turn_id") or "")
                turn = self.store.load_turn(session_id, delivered_turn_id)
                if turn is None:
                    raise RuntimeError("live_steering_turn_missing")
                return turn, False
            if receipt.get("status") == "failed" and receipt.get("error_code") in STEERING_NOT_DELIVERED_CODES:
                raise RuntimeError(str(receipt["error_code"]))
            from .capabilities.native_chat.queued_admission import reconcile_steering_delivery
            recovered = reconcile_steering_delivery(self, session, receipt)
            if recovered is not None:
                return recovered, False
            raise RuntimeError("live_steering_delivery_unresolved")
        active_turn_id = str(session.get("active_turn_id") or "")
        if expected_turn_id is not None and active_turn_id != expected_turn_id:
            fail_ingress("live_steering_turn_mismatch")
        if not active_turn_id:
            fail_ingress("live_steering_requires_active_turn")
        # Snapshot one adapter without taking the controller lock while a
        # binding or queue fence is held. The native call still checks health
        # and binds to the current upstream Turn.
        adapter = self.adapters.get(session_id)
        if not isinstance(adapter, CodexAppServerAdapter) or not adapter.healthcheck():
            fail_ingress("live_steering_session_not_attached")
        upstream_turn_id = ""
        deadline = time.monotonic() + min(5.0, self.startup_timeout_sec)
        while time.monotonic() < deadline:
            turn = self.store.load_turn(session_id, active_turn_id)
            current_session = self.store.load_session(session_id) or {}
            if (current_session.get("active_turn_id") != active_turn_id
                    or (turn or {}).get("status") not in {"queued", "starting", "running"}):
                fail_ingress("live_steering_turn_mismatch")
            upstream_turn_id = str((turn or {}).get("upstream_turn_id") or "")
            if upstream_turn_id:
                break
            time.sleep(0.02)
        if not upstream_turn_id:
            fail_ingress("live_steering_turn_not_started")
        from .capabilities.native_chat.queued_admission import send_native_steering
        turn = send_native_steering(self, adapter, session_id=session_id, active_turn_id=active_turn_id,
            upstream_turn_id=upstream_turn_id, client_ingress_id=client_ingress_id,
            message=message, queued_turn_id=_queued_turn_id, origin=origin)
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
                self.turn_release_wakeup.notify()
                return
            worker = threading.Thread(
                target=self._drain_session_queue,
                kwargs={"session_id": session_id, "work_dir": work_dir, "objective": objective}, daemon=True)
            self.session_queue_workers.add(session_id)
            self.session_queue_threads[session_id] = worker
            worker.start()

    def resume_creation_requests(self) -> None:
        from .capabilities.native_chat.creation_queue import resume_creation_requests
        resume_creation_requests(self)

    def _drain_session_queue(self, *, session_id: str, work_dir: Path, objective: str) -> None:
        from .capabilities.native_chat.session_queue import drain_session_queue
        drain_session_queue(self, session_id=session_id, work_dir=work_dir, objective=objective)

    def _fail_queue_preparation(self, session_id: str, turn_id: str, error: Exception) -> None:
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
            "Chat queue runtime preparation failed", exc_info=(type(error), error, error.__traceback__))
        try:
            self._fail_turn(
                session_id, turn_id,
                error.error_code if isinstance(error, CodexChatAgentError) else "runtime_unavailable",
                str(error) if isinstance(error, CodexChatAgentError) else (
                    "The Agent runtime could not be prepared. Check the runtime installation and configuration, then retry."),
                status="failed", gate=error.gate if isinstance(error, CodexChatAgentError) else None,
                expected_statuses={"queued", "starting", "running"})
        finally:
            self._release_done_event(key, done_event, drop_event=owns_done_event)

    def recover_exited_turn(self, session_id: str, turn_id: str | None = None) -> None:
        """Reconcile a lost local execution without starting a provider or replay.

        The kernel fence spans the worker, while the PID spans the service.
        A failed terminal write can end one worker without ending that PID.
        Unknown/foreign records and a held execution fence never prove loss.
        """
        session = self.store.load_session(session_id)
        if not session:
            return
        turn_id = turn_id or session.get("active_turn_id")
        if not turn_id:
            return
        path = self.store.turn_execution_lock_path(session_id, turn_id)
        state, observed = lock_holder_liveness(path)
        if (state not in {LOCK_HOLDER_DEAD, LOCK_HOLDER_LIVE, LOCK_HOLDER_RELEASED}
                or observed.get("host") != lock_holder_host_label()
                or type(observed.get("pid")) is not int or observed["pid"] <= 0
                or observed.get("lock_id") != _lock_id(path)
                or observed.get("agent_id") != "loopx-chat"
                or observed.get("operation") != "execute_chat_turn"
                or observed.get("policy") != "single_flight"):
            return
        # Take the execution fence without consuming its death evidence, so
        # dependency failures remain retryable. Recheck under the fence: a
        # replacement may have changed the holder since the first liveness read.
        with try_exclusive_file_lock(path, preserve_holder=True) as ownership:
            state, current = lock_holder_liveness(path)
            if (ownership is None or current != observed
                    or state not in {LOCK_HOLDER_DEAD, LOCK_HOLDER_LIVE, LOCK_HOLDER_RELEASED}
                    or current.get("host") != lock_holder_host_label()):
                return
            session = self.store.load_session(session_id)
            turn = self.store.load_turn(session_id, turn_id)
            if turn and turn.get("status") == "interrupting" and process_is_alive(current["pid"]):
                # Stop deliberately retires the execution worker before its
                # caller commits `interrupted`; a free fence is expected here.
                return
            managed = bool(session and session.get("session_mode") == CHAT_SESSION_MODE_MANAGED)
            if (managed and turn and turn.get("status") == "completing"
                    and not turn.get("completion_id")):
                # All response effects preceded the durable reservation. Finish its
                # idempotent transcript/events under the original execution fence;
                # the running service can recover it without a restart or replay.
                self.store.finalize_managed_turn_completion(session_id, turn_id)
                return
            if managed_turn_recovery_allowed(session, turn, managed=managed, owner_dead=True):
                code, message = (("server_restarted", SERVER_RESTARTED_MESSAGE)
                                 if state == LOCK_HOLDER_DEAD else (WORKER_EXITED_CODE, WORKER_EXITED_MESSAGE))
                statuses = {"starting", "running", "interrupting"}
                if (session and session.get("steward_context") and turn and turn.get("status") == "starting"
                        and turn.get("execution_dispatch") == "pending"):
                    code, message = BEFORE_DISPATCH_CODE, BEFORE_DISPATCH_MESSAGE
                    statuses = {"starting"}  # A concurrent stop must win over replay.
                self._fail_turn(session_id, turn_id, code, message, status="failed", expected_statuses=statuses)
            else:
                # An earlier recovery may have persisted the exact failed result
                # and then lost its event, message or claim write. The typed
                # owner rejects `failed`, so only this narrow completion may
                # finish that result; it never reopens or replays the Turn.
                self.store.complete_recovered_terminalization(
                    session_id, turn_id, session, turn,
                    recovery_messages={"server_restarted": SERVER_RESTARTED_MESSAGE,
                                       WORKER_EXITED_CODE: WORKER_EXITED_MESSAGE,
                                       BEFORE_DISPATCH_CODE: BEFORE_DISPATCH_MESSAGE,
                                       "runtime_error": WORKER_EXITED_MESSAGE},  # Legacy exact record.
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
        phase = "start"
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
                    **({"execution_dispatch": "pending"} if isinstance(adapter, CodexAppServerAdapter) else {}),
                )
                if started_turn is None:
                    with self.lock:
                        self.cancelled_turns.discard(key)
                    return
                from .control_plane.effect_runtime import effect_runtime_request_scope

                # Reuse one code revision across this worker's typed decisions.
                # This caches no Session, Turn or authority facts: each checkpoint
                # still reads current state, including after a long provider wait.
                phase = "execute_or_finalize"
                with effect_runtime_request_scope():
                    retry_binding: dict[str, str] = {}
                    while self._run_started_turn(
                        session_id=session_id, turn_id=turn_id, message=message,
                        attachments=attachments, adapter=adapter,
                        loopx_execution=loopx_execution, retry_binding=retry_binding,
                    ):
                        pass
        except OSError as exc:
            # The queue will reconcile this Turn. Keep a correlated diagnostic
            # without logging exception text (which may contain private paths).
            logging.getLogger(__name__).warning(
                "Chat worker I/O failure: turn=%s phase=%s errno=%s", turn_id, phase, exc.errno)
            raise
        finally:
            self._release_done_event(key, done_event)

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
            if kind == "turn.native_result":
                if getattr(adapter, "adapter_kind", None) != "antigravity_ls":
                    raise ValueError("native result capture is unavailable for this adapter")
                try:
                    self.store.retain_native_result(session_id, turn_id, payload)
                except OSError as exc:
                    # Supplemental retention cannot discard a valid primary
                    # answer. Missing facts still cannot authorize recovery.
                    logging.getLogger(__name__).warning("Native completion capture unavailable: %s", type(exc).__name__)
                return
            event_buffer.emit(kind, payload)
            if kind == "native_goal.status":
                self.store.update_session(session_id, native_goal=payload)

        completion_reserved = False
        try:
            if execution_ended():
                return
            session = self.store.load_session(session_id) or {}
            if session.get("project_context") is not None:
                self._validate_project_session(session)
            from .chat.coordination import prepare_turn_context
            current_turn = self.store.load_turn(session_id, turn_id) or {}
            if current_turn.get("origin") == "peer" or "collaboration_request" in current_turn:
                from .capabilities.native_chat.conversation_bindings import OriginalConversationRequests
                peer_requests = OriginalConversationRequests(self)
                peer_requests.validate_turn(session_id, current_turn)
                if not isinstance(adapter, CodexAppServerAdapter):
                    raise ValueError("original conversation peer tools are unavailable for this executor")
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
            scope = (conversation_scope(session) if native_loopx or current_turn.get("origin") == "peer" else
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
            else:
                from .capabilities.native_chat.antigravity_adapter import AntigravityAdapter
                if isinstance(adapter, AntigravityAdapter):
                    adapter._steward_report_reader = None
            native_command = None if current_turn.get("origin") == "peer" else parse_native_goal_command(message)
            if native_command is not None:
                validate_goal_chat(session, attachments)
                if scope["kind"] != "owner_goal":
                    raise ValueError("/goal continuation requires the local owner's Goal conversation.")
            if scope["kind"] in {"owner_goal", "owner_portfolio", "external_audience"} and (native_command is None or loopx_execution):
                adapter, context = prepare_turn_context(self, adapter, session, turn_id, event_sink, scope=scope,
                    **({"preserve_original_thread": not report_context_rebind} if native_loopx else {}))
                # Recheck current scope above, but keep the retry's model input stable.
                evidence = retry_binding.setdefault("context", json.dumps(context, ensure_ascii=False))
                from .capabilities.native_chat.manager_input import manager_turn_input
                message = manager_turn_input(adapter, message, evidence)
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
            # Seal against replay before invoking anything that can execute the
            # original input, even if it never reports an upstream Turn ID.
            if current_turn.get("execution_dispatch") == "pending" and self.store.update_turn(
                    session_id, turn_id, expected_statuses={"starting", "running"},
                    execution_dispatch="committed") is None:
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
                    on_resume_activated = None
                    loopx_request = current_turn.get("loopx_request")
                    delegation_wake = (
                        isinstance(loopx_request, Mapping)
                        and loopx_request.get("operation") == "wake"
                    )
                    registry_path = self.registry_path
                    if (
                        native_command.operation == "resume"
                        and not delegation_wake
                        and registry_path is not None
                    ):
                        from .agent_registry import load_goal_from_registry
                        from .control_plane.goals.activation_service import (
                            GoalActivationState,
                            goal_activation_state,
                            set_goal_activation_state,
                        )

                        registered_goal = load_goal_from_registry(
                            registry_path, session["goal_id"]
                        )
                        if (
                            registered_goal is not None
                            and goal_activation_state(registered_goal)
                            is GoalActivationState.ACTIVE
                        ):
                            def record_owner_resume() -> None:
                                activation = set_goal_activation_state(
                                    registry_path=registry_path,
                                    goal_id=session["goal_id"],
                                    state="active",
                                    reason="Explicit Codex Goal resume",
                                    actor_kind="owner",
                                    owner_resume_action=True,
                                    execute=True,
                                )
                                if not activation.get("ok"):
                                    raise CodexChatAgentError(
                                        str(
                                            activation.get("error")
                                            or "LoopX owner resume epoch could not be recorded"
                                        ),
                                        error_code="goal_owner_resume_write_failed",
                                        gate=None,
                                    )

                            on_resume_activated = record_owner_resume
                    response = adapter.goal_driver.run(
                        native_command,
                        event_sink,
                        execution_context=execution_context,
                        on_resume_activated=on_resume_activated,
                    )
                finally:
                    if execution_lock is not None:
                        execution_lock.__exit__(None, None, None)
                    adapter.goal_driver = None
            elif current_turn.get("origin") == "peer":
                response = peer_requests.execute(session_id, turn_id, adapter.session, event_sink)
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
                    if (session.get("project_context") is not None
                            and self.project_contexts.conversation_bindings is not None):
                        from .capabilities.native_chat.external_conversations import ChatExternalConversations
                        message = ChatExternalConversations(self).prepare_continuation_input(
                            session, current_turn, adapter.session, message)
                # Manager/native/external conversations have different execution
                # and waiting boundaries. Do not invent Goal timing for them.
                observed_goal = str(session.get("goal_id") or "") if scope["kind"] == "owner_goal" else ""
                with observe_goal_execution(self.store.root.parent, observed_goal, host=str(session.get("agent_id") or "unknown")):
                    if attachments:
                        response = start_attachment_turn(adapter, session, current_turn, message, event_sink, attachments)
                    else:
                        from .capabilities.native_chat.external_conversations import ChatExternalConversations
                        response = ChatExternalConversations.execute_turn(self, adapter, session, current_turn, message, event_sink)
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
            completion_reserved = True
            if adapter.upstream_thread_id != str((self.store.load_session(session_id) or {}).get("upstream_thread_id") or ""):
                self.store.update_session(session_id, upstream_thread_id=adapter.upstream_thread_id)
            if not native_loopx and scope.get("bound_steward") is True:
                try:
                    from .capabilities.steward_memory.runtime import commit_steward_memory_after_turn
                    memory_receipt = commit_steward_memory_after_turn(runtime_root=self.coordination_runtime_root, session=session, scope=scope, turn=current_turn, turn_id=turn_id, user_message=str(current_turn.get("message") or ""), response=response)
                    if memory_receipt is not None:
                        self.store.append_event(session_id, turn_id, kind="steward.memory", payload=memory_receipt)
                except Exception:  # noqa: BLE001 - memory writeback must fail open without breaking Turn completion.
                    pass
            self.store.finalize_managed_turn_completion(
                session_id,
                turn_id,
            )
        except CodexChatTimeoutError as exc:
            event_buffer.close()
            try:
                if not execution_ended():
                    adapter.interrupt_turn()
            except Exception:
                pass
            self._fail_turn(session_id, turn_id, exc.error_code, str(exc), status="timed_out")
        except CodexChatAgentError as exc:
            event_buffer.close()
            if (not loopx_execution and parse_native_goal_command(message) is None
                    and isinstance(adapter, CodexAppServerAdapter)
                    and exc.error_code in {"host_gate", "upstream_transport_failure", "server_overloaded"}
                    and self._prepare_transport_retry(
                        session_id, turn_id, adapter, retry_binding, exc.native_error_info,
                        routing_timeout=exc.native_routing_timeout,
                    )):
                return True
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
            if isinstance(exc, OSError) and (self.store.load_turn(session_id, turn_id) or {}).get("execution_dispatch") == "pending":
                # The exact execution-fence owner will reconcile this I/O loss
                # with the durable dispatch marker instead of a generic failure.
                raise
            if completion_reserved and isinstance(exc, OSError):
                # The answer already exists. Its lifecycle owner can finish
                # publication without turning this into an execution failure.
                logging.getLogger(__name__).warning(
                    "Chat worker I/O failure: turn=%s phase=publish_saved_answer errno=%s", turn_id, exc.errno)
                return
            event_buffer.close()
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
        codex_context.checked_session_home(self.codex_home, session)
        if session.get("project_context") is not None:
            self._validate_project_session(session)
        elif session.get("steward_context") is not None:
            self.project_contexts.session_context(session)
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
        from .control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError

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
        except (CodexChatAgentError, EffectRuntimeRemoteError, EffectRuntimeStartupError,
                KeyError, OSError, TypeError, ValueError):
            return False

    def _fail_turn(
        self, session_id: str, turn_id: str, error_code: str, message: str, *,
        status: str, gate: dict[str, Any] | None = None, expected_statuses: set[str] | None = None,
    ) -> None:
        # Failure recording has no provider effects. Its status CAS preserves a
        # concurrent stop/completion, and claim release is pinned to this Turn.
        # A new execution-eligibility RPC here can itself fail and strand the
        # original error and every subsequent queued request.
        completed = utc_now()
        failed = self.store.update_turn(
            session_id, turn_id,
            expected_statuses=expected_statuses if expected_statuses is not None else {"starting", "running"},
            status=status, error_code=error_code, error=message, completed_at=completed, last_activity_at=completed)
        if failed is None:
            return
        payload: dict[str, Any] = {"error_code": error_code, "message": message}
        if gate:
            payload["gate"] = gate
        self.store.append_event(session_id, turn_id, kind="turn.failed", payload=payload)
        self.store.append_message(session_id, role="error", text=message, turn_id=turn_id)
        self.store.release_active_turn(session_id, turn_id, last_activity_at=completed, last_error_code=error_code)

    def interrupt_turn(self, *, session_id: str, turn_id: str) -> dict[str, Any]:
        # A new adapter can compact history while holding the adapter lock.
        # Publish an atomic stop for a still-queued managed Turn first, so its
        # live restoration guard can abort before the user's work is started.
        # Running/attached Turns retain the existing native stop protocol.
        with self.lock:
            compacting = session_id in self.compacting_history
        session = self.store.load_session(session_id) if compacting else None
        queued_interrupt = (self.store.begin_turn_interrupt(session_id, turn_id, expected_statuses={"queued"})
            if session and session.get("session_mode") != CHAT_SESSION_MODE_ATTACHED
            and self.store.prepared_managed_turn_request(session_id, turn_id=turn_id) is None else None)
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
            # automatic review of the work the user just cancelled. The store
            # checks the status and writes the intent under the Turn lock, so a
            # Turn that completed while this call was reading it keeps its
            # terminal event history and is not relabelled as interrupted.
            interrupting = turn if queued_interrupt is not None and turn.get("status") == "interrupting" else self.store.begin_turn_interrupt(
                session_id,
                turn_id,
                expected_statuses={"queued", "starting", "running", "interrupting"},
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
                    elif upstream_turn_id or queued_interrupt is None and turn.get("status") in {"running", "interrupting"}:
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

    def close(self) -> None:
        self.closed.set()
        self.turn_release_wakeup.notify()
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
