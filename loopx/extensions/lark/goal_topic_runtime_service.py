"""Worker lifecycle for the Lark Goal Topic runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
import shutil
from subprocess import Popen
import threading
import time
from typing import Any

from ...chat_manager import MANAGER_AGENT_OBJECTIVE
from ...file_lock import try_exclusive_file_lock
from ..background_runtime import HostedWorker
from .cli_resolution import LarkCliUnavailableError
from .goal_channel_contracts import bindings_for_goal
from .manager_context import session_turn_effect
from .private_steward_triage import TRIAGE_CARD_ACTION, capture_callback, profile_supports_cards
from .team_plan_confirmation import (
    active_profile_chat_ids,
    deliver_team_plan_review_cards_from_runtime,
    handle_lark_review_callback_for_profile,
)


SnapshotProvider = Callable[[], Mapping[str, Any]]
ProfilePoller = Callable[[str, threading.Event], None]
ManagerRouteReconciler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class ProfileTransport:
    cli_bin: str
    app_id: str
    consumer_lease_key: str
    callback_chats: tuple[str, ...]
    other_binding_id: str


@dataclass(frozen=True)
class ProfileWorkerFingerprint:
    transport: ProfileTransport
    owner_binding_id: str = ""


ProfileWorker = tuple[threading.Event, threading.Thread, ProfileWorkerFingerprint]
MessageConsumer = tuple[threading.Event, Popen[str], threading.Event]


class LarkGoalTopicRuntimeService:
    """Own one event-consumer worker per reusable Lark App profile."""

    def __init__(
        self,
        *,
        snapshot_provider: SnapshotProvider,
        runtime_root: str | Path,
        runtime_controller: Any,
        action_service: Any | None = None,
        private_conversations: Any | None = None,
        profile_poller: ProfilePoller | None = None,
        manager_route_reconciler: ManagerRouteReconciler | None = None,
    ) -> None:
        self.snapshot_provider = snapshot_provider
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.runtime_controller = runtime_controller
        self.action_service = action_service
        self.private_conversations = private_conversations
        self._profile_poller = profile_poller or self._poll_profile
        self.manager_route_reconciler = manager_route_reconciler
        self._lock = threading.Lock()
        self._workers: dict[str, ProfileWorker] = {}
        self._message_consumers: dict[str, MessageConsumer] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._closed = threading.Event()
        self._delivery_wakeup = threading.Event()
        self._startup_thread: threading.Thread | None = None
        self._refresh_retry: threading.Timer | None = None
        self._refresh_retry_requested = False
        self._profile_refresh_sequence = 0
        self._observed_profile_refresh_sequence = 0
        self._delivery_thread: threading.Thread | None = None
        self._delivery_worker = HostedWorker(target=self._reconcile_private, stop=self._closed,
            name="loopx-lark-private-delivery", retry_seconds=5)

    def start(self) -> None:
        """Discover existing bindings without blocking the HTTP readiness path."""

        with self._lock:
            if self._closed.is_set():
                return
            if self._startup_thread is None:
                self._startup_thread = threading.Thread(
                    target=self._refresh_on_start,
                    name="loopx-lark-startup",
                    daemon=True,
                )
                self._startup_thread.start()
            if self.private_conversations is not None:
                self._delivery_worker.start()
                self._delivery_thread = self._delivery_worker.thread

    def _reconcile_private(self) -> None:
        # Keep slow provider writes/readbacks off the scan loop. At most four
        # persisted requests run, with no executor backlog or extra listener.
        # The per-request journal lock retains ambiguous-write/no-resend safety.
        transport = self.private_conversations
        if transport is None:
            return
        pending: dict[Path, Future[int]] = {}
        attempted: dict[Path, int] = {}
        ready: dict[Path, None] = {}
        attention: Future[int] | None = None
        attention_due = 0.0
        reports: Future[int] | None = None
        reports_due = 0.0
        feedback: Future[int] | None = None
        feedback_due = 0.0
        discovery: Future[list[Path]] | None = None
        discovery_due = 0.0
        ingress: Future[list[str]] | None = None
        ingress_due = time.monotonic() + 60
        wakeup_scope = getattr(self.runtime_controller, "turn_release_wakeup", None)
        # Older/custom controllers retain polling. One local hint wakes the
        # existing scanner; it is neither a queue nor a delivery receipt.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="loopx-lark-private-reply") as pool, \
                (wakeup_scope(self._delivery_wakeup) if callable(wakeup_scope) else nullcontext()):
            def step() -> None:
                nonlocal attempted, ready, attention, attention_due, reports, reports_due
                nonlocal ingress, ingress_due, feedback, feedback_due, discovery, discovery_due
                # Clear BEFORE scanning: an arrival during the scan must also
                # wake the following wait, rather than disappear in that gap.
                self._delivery_wakeup.clear()
                if feedback is not None and feedback.done():
                    try:
                        feedback.result()
                    except Exception:
                        logging.getLogger(__name__).warning("App input reaction reconciliation is pending")
                    feedback = None
                    feedback_due = time.monotonic() + 5
                if reports is not None and reports.done():
                    try:
                        reports.result()
                    except Exception:
                        logging.getLogger(__name__).warning("Original private report delivery is pending")
                    reports = None
                    reports_due = time.monotonic() + 30
                if ingress is not None and ingress.done():
                    try:
                        for profile in ingress.result():
                            with self._lock:
                                worker = self._workers.get(profile)
                                current = self._message_consumers.get(profile)
                                if (
                                    worker is not None and worker[2].owner_binding_id
                                    and current is not None and current[0] is worker[0]
                                ):
                                    self._disconnect_message_consumer(current)
                    except Exception:
                        logging.getLogger(__name__).warning("Private Chat source observation is unavailable")
                    ingress = None
                    ingress_due = time.monotonic() + 60
                if attention is not None and attention.done():
                    try:
                        attention.result()
                    except Exception:
                        logging.getLogger(__name__).warning("Owner notification reconciliation is pending")
                    attention = None
                    attention_due = time.monotonic() + 30
                for path, future in list(pending.items()):
                    if not future.done():
                        continue
                    pending.pop(path)
                    try:
                        future.result()
                    except Exception:
                        logging.getLogger(__name__).warning("Private Chat delivery reconciliation is pending")
                if discovery is not None and discovery.done():
                    try:
                        # Keep new hints even if the separate body scan fails.
                        # A previous attempt only affects their order.
                        for path in discovery.result():
                            if path not in pending:
                                ready.setdefault(path, None)
                    except Exception:
                        logging.getLogger(__name__).warning("Private Chat historical discovery is pending")
                    discovery = None
                    discovery_due = time.monotonic() + 5
                try:
                    body_paths = getattr(transport, "pending_body_paths", None)
                    paths = body_paths() if callable(body_paths) else transport.pending_delivery_paths()
                    # Discovery can temporarily hide a durable request during
                    # backoff. Retain its fairness count until the source is
                    # actually removed, so earlier hints cannot starve later work.
                    attempted = {path: count for path, count in attempted.items() if path.exists()}
                    # Keep scheduling hints apart from lifetime attempt counts.
                    # A delivered body or an earlier held recovery can become
                    # eligible again. Retain the new hint until a slot opens.
                    ready = {path: None for path in ready if path.exists()}
                    for path in paths:
                        if path not in pending:
                            ready.setdefault(path, None)
                    owner_attention = getattr(transport, "owner_attention", None)
                    attention_ready = (owner_attention is not None and attention is None
                        and time.monotonic() >= attention_due and owner_attention.has_enabled())
                    observer = getattr(transport, "ingress", None)
                    ingress_ready = (observer is not None and ingress is None
                        and time.monotonic() >= ingress_due)
                    report_provider = getattr(transport, "steward_reports", None)
                    reports_ready = (report_provider is not None and reports is None
                        and time.monotonic() >= reports_due and report_provider.pending())
                    if report_provider is not None and reports is None and time.monotonic() >= reports_due:
                        reports_due = time.monotonic() + 30
                    recovery_path = getattr(transport, "is_recovery_path", lambda path: False)
                    # Fresh bodies take the next released slot ahead of old
                    # recovery probes, including discoveries retained while
                    # full. Attempts retain fairness within each class. This
                    # local hint never admits/replays a request or skips its
                    # original grant and provider-attempt checks.
                    recovering = {path: recovery_path(path) for path in ready}
                    active_background = sum(job is not None for job in (feedback, reports, attention, ingress, discovery))
                    due_background = sum((reports_ready, attention_ready, ingress_ready))
                    scheduled_body = False
                    for path in sorted(ready, key=lambda path: (recovering[path], attempted.get(path, 0))):
                        occupied = len(pending) + active_background
                        if occupied >= 4 or self._closed.is_set():
                            break
                        # Give an unsent body the next available slot when due
                        # checks would otherwise reserve every free slot.
                        if occupied + due_background >= 4 and (recovering[path] or scheduled_body):
                            break
                        pending[path] = pool.submit(transport.reconcile_request, path)
                        scheduled_body = scheduled_body or not recovering[path]
                        del ready[path]
                        attempted[path] = attempted.get(path, 0) + 1
                    if reports_ready and len(pending) + int(feedback is not None) + int(attention is not None or attention_ready) + int(ingress is not None or ingress_ready) + int(discovery is not None) < 4 and not self._closed.is_set():
                        reports = pool.submit(report_provider.reconcile)
                    if attention_ready and len(pending) + int(feedback is not None) + int(reports is not None) + int(ingress is not None or ingress_ready) + int(discovery is not None) < 4 and not self._closed.is_set():
                        attention = pool.submit(owner_attention.reconcile)
                    if ingress_ready and len(pending) + int(feedback is not None) + int(reports is not None) + int(attention is not None) + int(discovery is not None) < 4 and not self._closed.is_set():
                        ingress = pool.submit(getattr(transport, "observe_ingress", observer.observe), cancelled=self._closed.is_set)
                    app_feedback = getattr(transport, "reconcile_app_feedback", None)
                    # Optional feedback uses one slot in this same bounded
                    # pool and leaves capacity for canonical body deliveries.
                    if (callable(app_feedback) and feedback is None and time.monotonic() >= feedback_due
                            and len(pending) + int(reports is not None) + int(attention is not None)
                            + int(ingress is not None) + int(discovery is not None) < 3 and not self._closed.is_set()):
                        feedback = pool.submit(app_feedback)
                    # One discovery pass uses this same bounded pool. Its slow
                    # native/history observations cannot hold the scanner that
                    # discovers fresh captures and already generated answers.
                    if (callable(body_paths) and discovery is None and time.monotonic() >= discovery_due
                            and len(pending) + int(feedback is not None) + int(reports is not None)
                            + int(attention is not None) + int(ingress is not None) < 4 and not self._closed.is_set()):
                        discovery = pool.submit(transport.pending_delivery_paths)
                        discovery.add_done_callback(lambda _: self._delivery_wakeup.set())
                except Exception:
                    logging.getLogger(__name__).warning("Private Chat delivery discovery is pending")

            self._delivery_worker.repeat(step, interval_seconds=1, wait=self._delivery_wakeup.wait,
                on_error=lambda: logging.getLogger(__name__).warning(
                    "Private Chat delivery discovery is pending"))

    def _admit_private(self, profile: str, event: dict[str, Any], *,
                       expected_binding_id: str | None = None) -> dict[str, Any]:
        if self._closed.is_set():
            raise RuntimeError("Lark conversation service is closed")
        options = {"expected_binding_id": expected_binding_id} if expected_binding_id is not None else {}
        result = self.private_conversations.admit(profile, event, defer_background_stop=True, **options)
        # Admission owns persistence and authorization. Only after it returns
        # may the existing reply worker look for its durable work; duplicates
        # and denied inputs still cannot manufacture a pending journal entry.
        self._delivery_wakeup.set()
        return result

    def _handle_private_steward_callback(
        self, profile: str, event: Mapping[str, Any], action: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self.private_conversations is None or self._closed.is_set():
            raise ValueError("private Steward callback route is unavailable")
        result = capture_callback(self.private_conversations, profile, event, action)
        self._delivery_wakeup.set()
        return result

    def _refresh_on_start(self) -> None:
        while not self._closed.is_set():
            try:
                self.refresh()
                return
            except Exception:
                logging.getLogger(__name__).warning(
                    "Lark binding discovery failed; retrying in the background"
                )
            self._closed.wait(5)

    def _retry_profile_refresh(self) -> None:
        with self._lock:
            self._refresh_retry_requested = False
        try:
            retry_needed = self._refresh_profile_workers(resume_queues=False)
        except (OSError, LarkCliUnavailableError):
            retry_needed = True
        except Exception:
            logging.getLogger(__name__).warning("Lark profile refresh failed")
            retry_needed = False
        with self._lock:
            self._refresh_retry = None
            if retry_needed or self._refresh_retry_requested:
                self._schedule_profile_refresh()

    def _schedule_profile_refresh(self) -> None:
        # Called under the service lock. Only the configuration read is retried.
        if self._closed.is_set():
            return
        if self._refresh_retry is not None:
            # A foreground failure can arrive while the timer is finishing.
            self._refresh_retry_requested = True
            return
        self._refresh_retry = threading.Timer(5, self._retry_profile_refresh)
        self._refresh_retry.daemon = True
        self._refresh_retry.start()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _update_health(self, profile: str, **updates: Any) -> None:
        with self._lock:
            current = dict(
                self._health.get(
                    profile,
                    {
                        "status": "starting",
                        "event_count": 0,
                        "replied_count": 0,
                        "last_event_status": None,
                        "error_code": None,
                        "restart_count": 0,
                    },
                )
            )
            current["event_count"] = int(current.get("event_count") or 0) + int(
                updates.pop("event_count", 0) or 0
            )
            current["replied_count"] = int(current.get("replied_count") or 0) + int(
                updates.pop("replied_count", 0) or 0
            )
            current.update(updates)
            current["updated_at"] = self._now()
            self._health[profile] = current

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return content-free listener health keyed by safe profile reference."""

        with self._lock:
            return {profile: dict(health) for profile, health in self._health.items()}

    @staticmethod
    def _disconnect_message_consumer(consumer: MessageConsumer) -> bool:
        """End the current pipe under the service lock and let the worker drain."""

        stop, process, requested = consumer
        if stop.is_set():
            return False
        if not requested.is_set() and process.poll() is None:
            requested.set()
            try:
                process.terminate()
            except OSError:
                requested.clear()
                logging.getLogger(__name__).warning("Lark message consumer could not reconnect")
                return False
        return True

    @staticmethod
    def _consumer_lease_key(profile: str, config: Mapping[str, str]) -> str:
        identity = config.get("bot_app_id") or profile
        return str(config.get("consumer_ref") or hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32])

    def _listener_configs(self, snapshot: Mapping[str, Any]) -> tuple[dict[str, dict[str, str]], set[str]]:
        from . import goal_topic_runtime as runtime

        profiles = runtime._active_profile_configs(snapshot)
        legacy = {
            profile for profile, config in runtime._active_group_profile_configs(snapshot).items()
            if not config.get("bot_app_id") and not profiles[profile].get("bot_app_id")
        }
        inventory = getattr(self.private_conversations, "configured_profile_apps", None)
        unavailable: set[str] = set()
        if not callable(inventory):
            return profiles, unavailable
        apps_by_cli: dict[str, dict[str, str] | None] = {}
        for profile in sorted(legacy):
            config = profiles[profile]
            cli_bin = config["cli_bin"]
            if cli_bin not in apps_by_cli:
                try:
                    apps_by_cli[cli_bin] = inventory(cli_bin=cli_bin)
                except (OSError, LarkCliUnavailableError):
                    apps_by_cli[cli_bin] = None
            apps = apps_by_cli[cli_bin]
            if apps is None:
                unavailable.add(profile)
                continue
            app_id = apps.get(profile)
            if app_id:
                # Fence the original profile and the resolved App on both
                # sides of a private binding change.
                profiles[profile] = {
                    **config,
                    "bot_app_id": app_id,
                    "consumer_ref": self._consumer_lease_key(profile, config),
                }
        return profiles, unavailable

    @staticmethod
    def _listener_snapshot(
        snapshot: Mapping[str, Any],
        profile_configs: Mapping[str, Mapping[str, str]],
    ) -> Mapping[str, Any]:
        payload = snapshot.get("target_payload")
        targets = payload.get("targets") if isinstance(payload, Mapping) else None
        if not isinstance(targets, Mapping):
            return snapshot
        resolved = dict(targets)
        for ref, target in targets.items():
            if not isinstance(target, Mapping):
                continue
            identity = target.get("identity")
            if not isinstance(identity, Mapping) or identity.get("bot_app_id"):
                continue
            profile = str(identity.get("sender_profile") or "").strip()
            app_id = profile_configs.get(profile, {}).get("bot_app_id")
            if app_id:
                resolved[ref] = {**target, "identity": {**identity, "bot_app_id": app_id}}
        # Only the listener's view gets the observed App identity. Topic
        # bindings, sender profiles and reply destinations still come from the
        # current configuration.
        return {**snapshot, "target_payload": {**payload, "targets": resolved}}

    def _profile_worker_fingerprint(
        self,
        snapshot: Mapping[str, Any],
        profile: str,
        profile_config: Mapping[str, str],
    ) -> ProfileWorkerFingerprint:
        callback_chats = (
            tuple(active_profile_chat_ids(snapshot, profile))
            if self.action_service is not None
            else ()
        )
        app_id = str(profile_config.get("bot_app_id") or "")
        binding_id = str(profile_config.get("binding_id") or "")
        owner_binding_id = binding_id if app_id and profile_config.get("audience") == "owner" else ""
        cli_bin = str(profile_config.get("cli_bin") or "lark-cli")
        executable = shutil.which(cli_bin)
        return ProfileWorkerFingerprint(
            transport=ProfileTransport(
                # Legacy settings can name the same CLI that Chat resolved
                # to a full path when configuring the private binding.
                cli_bin=str(Path(executable).absolute()) if executable else cli_bin,
                app_id=app_id,
                consumer_lease_key=self._consumer_lease_key(profile, profile_config),
                callback_chats=callback_chats,
                other_binding_id="" if owner_binding_id else binding_id,
            ),
            owner_binding_id=owner_binding_id,
        )

    def _poll_profile(self, profile: str, stop: threading.Event) -> None:
        # Resolve through the compatibility module at call time so existing
        # integrations that patch its entrypoints keep working during the move.
        from . import goal_topic_runtime as runtime

        restart_count = 0
        try:
            while not stop.is_set():
                self._update_health(
                    profile,
                    status="starting" if restart_count == 0 else "retrying",
                    error_code=None,
                    restart_count=restart_count,
                )
                try:

                    def answer(
                        route: Mapping[str, Any], text: str
                    ) -> Mapping[str, Any]:
                        effective_route = route
                        if (
                            route.get("conversation_kind") == "manager"
                            and route.get("native_mention") is not True
                            and self.manager_route_reconciler is not None
                        ):
                            try:
                                effective_route = self.manager_route_reconciler(route)
                            except Exception as exc:
                                raise runtime.LarkGoalTopicTurnFailed(
                                    "manager_channel_route_reconcile_failed",
                                    session_turn_effect(route),
                                ) from exc
                        snapshot = self.snapshot_provider()
                        contexts = snapshot.get("goal_contexts")
                        contexts = contexts if isinstance(contexts, Mapping) else {}
                        context = contexts.get(
                            str(effective_route.get("goal_id") or "")
                        )
                        context = context if isinstance(context, Mapping) else {}
                        answer_result = runtime.answer_lark_goal_topic(
                            route=effective_route,
                            text=text,
                            work_dir=str(context.get("work_dir") or self.runtime_root),
                            objective=str(
                                context.get("objective")
                                or effective_route.get("goal_id")
                                or ""
                            ),
                            runtime_controller=self.runtime_controller,
                        )
                        if isinstance(answer_result, Mapping):
                            response_text = str(
                                answer_result.get("response_text") or ""
                            )
                            proposal_ids = list(
                                answer_result.get("proposal_ids") or []
                            )
                        else:
                            response_text = answer_result
                            proposal_ids = []
                        effect_receipt = answer_result.get("effect_receipt") if isinstance(answer_result, Mapping) else None
                        return {
                            "response_text": response_text,
                            "effect_receipt": effect_receipt or session_turn_effect(effective_route),
                            "proposal_ids": proposal_ids,
                        }

                    def deliver_proposals(
                        route: Mapping[str, Any], proposal_ids: list[str]
                    ) -> Mapping[str, Any]:
                        return deliver_team_plan_review_cards_from_runtime(
                            proposal_ids=proposal_ids,
                            manager_route=route,
                            runtime_controller=self.runtime_controller,
                            runtime_root=self.runtime_root,
                        )

                    def handle_review_callback(
                        event: Mapping[str, Any],
                    ) -> Mapping[str, Any]:
                        action = event.get("action_value")
                        if isinstance(action, Mapping) and action.get("schema_version") == TRIAGE_CARD_ACTION:
                            return self._handle_private_steward_callback(profile, event, action)
                        if self.action_service is None:
                            raise ValueError(
                                "Lark manager review callbacks require Chat actions"
                            )
                        profile_config = runtime._active_profile_configs(
                            self.snapshot_provider()
                        ).get(profile)
                        return handle_lark_review_callback_for_profile(
                            event,
                            action_service=self.action_service,
                            action_store_root=self.runtime_root
                            / "chat"
                            / "actions",
                            profile_config=profile_config,
                        )

                    snapshot = self.snapshot_provider()
                    profile_configs, unavailable = self._listener_configs(snapshot)
                    if profile in unavailable:
                        # A new subprocess must acquire locks for a fresh App
                        # observation. Only an already running pipe may stay.
                        self._update_health(
                            profile, status="retrying", error_code="lark_profile_inventory_unavailable"
                        )
                        stop.wait(5)
                        continue
                    profile_config = profile_configs.get(profile)
                    if profile_config is None:
                        break
                    # A retry can read a different App before the configuration
                    # refresh replaces this worker. Match the leases and routing
                    # to the same committed discovery.
                    with self._lock:
                        worker = self._workers.get(profile)
                        if worker is None or worker[0] is not stop:
                            break
                        expected_app = worker[2].transport.app_id
                    if str(profile_config.get("bot_app_id") or "") != expected_app:
                        self.refresh()
                        continue
                    private_profiles = snapshot.get("private_profiles")
                    private_binding = private_profiles.get(profile) if isinstance(private_profiles, Mapping) else None
                    binding_id = private_binding.get("binding_id") if isinstance(private_binding, Mapping) else None
                    reconnect_requested = threading.Event()
                    # A group-only connection must not pick up private work
                    # read before a newly added owner binding. A provider with
                    # no separate group route still owns its private admission.
                    private_admitter = self._admit_private if self.private_conversations is not None and (
                        isinstance(private_binding, Mapping)
                        or profile not in runtime._active_group_profile_configs(snapshot)
                    ) else None
                    bound_owner = (
                        private_admitter is not None
                        and isinstance(binding_id, str) and bool(binding_id)
                        and isinstance(private_binding, Mapping)
                        and private_binding.get("audience") == "owner"
                    )
                    private_steward_callbacks = (
                        self.private_conversations is not None and profile_supports_cards(profile_config)
                    )
                    if bound_owner:
                        def admit_bound_private(profile_name: str, event: dict[str, Any]) -> dict[str, Any]:
                            # An event read by the old stream cannot follow a
                            # same-App rebind while it waits for admission.
                            return self._admit_private(profile_name, event, expected_binding_id=binding_id)

                        private_admitter = admit_bound_private

                    def start_consumer(args: list[str]) -> Popen[str]:
                        process = runtime._default_process_factory(args)
                        if args[3:6] == ["event", "consume", "im.message.receive_v1"]:
                            with self._lock:
                                worker = self._workers.get(profile)
                                if worker is not None and worker[0] is stop:
                                    self._message_consumers[profile] = (stop, process, reconnect_requested)
                        return process

                    # A bot App can have several local profile aliases and Chat
                    # servers can use different runtime roots or ports. The
                    # consumer lease must therefore be machine/App scoped.
                    app_id = str(profile_config.get("bot_app_id") or "")
                    # Legacy group listeners retain their original profile
                    # lease alongside the resolved App lease.
                    digests = {self._consumer_lease_key(profile, profile_config)}
                    if app_id:
                        digests.add(hashlib.sha256(app_id.encode("utf-8")).hexdigest()[:32])
                    # A legacy/shared consumer holds both identities: wait for
                    # the old process and fence new aliases on other servers.
                    with ExitStack() as leases:
                        acquired = [leases.enter_context(try_exclusive_file_lock(
                            Path.home() / ".loopx" / "lark-consumers" / key,
                            operation="lark_event_consumer")) for key in sorted(digests)]
                        if any(lease is None for lease in acquired):
                            self._update_health(
                                profile,
                                status="standby",
                                error_code="lark_event_consumer_owned_elsewhere",
                            )
                            stop.wait(5)
                            continue

                        def listener_snapshot() -> Mapping[str, Any]:
                            current = self.snapshot_provider()
                            with self._lock:
                                # Reuse the same observations used to admit
                                # each worker. A successful refresh adds new
                                # aliases without replacing this message pipe.
                                apps = {
                                    name: {"bot_app_id": worker[2].transport.app_id}
                                    for name, worker in self._workers.items()
                                }
                            # The pipe's identity is fixed for its lifetime.
                            # Fresh target/Topic bindings still gate dispatch.
                            apps[profile] = {"bot_app_id": expected_app}
                            return self._listener_snapshot(current, apps)

                        def capture(route: Mapping[str, object]) -> dict[str, object]:
                            return runtime.capture_topic_mention(
                                route, self.runtime_controller
                            )

                        def register_callback_probe(probe: Callable[[], bool]) -> None:
                            readiness = self.private_conversations.steward_callback_readiness
                            key = (profile, app_id)
                            readiness[key] = probe
                            leases.callback(readiness.pop, key, None)

                        process_options = {"process_factory": start_consumer} if self.private_conversations is not None else {}
                        try:
                            result = runtime.stream_lark_goal_topic_profile(
                                profile=profile,
                                snapshot_provider=listener_snapshot,
                                stop=stop,
                                runtime_root=self.runtime_root,
                                answer=answer,
                                # Recheck binding eligibility per event, including
                                # events drained from an old provider connection.
                                capture_mention=capture,
                                private_admitter=private_admitter,
                                proposal_deliverer=deliver_proposals,
                                callback_availability_sink=register_callback_probe if private_steward_callbacks else None,
                                review_callback_handler=(
                                    handle_review_callback
                                    if private_steward_callbacks or (
                                        self.action_service is not None and profile in runtime._active_group_profile_configs(snapshot)
                                    )
                                    else None
                                ),
                                health_sink=lambda update: self._update_health(
                                    profile, **dict(update)
                                ),
                                **process_options,
                            )
                        finally:
                            with self._lock:
                                current = self._message_consumers.get(profile)
                                if current is not None and current[0] is stop:
                                    del self._message_consumers[profile]
                    if result.get("status") == "configuration_removed":
                        self._update_health(
                            profile,
                            status="inactive",
                            error_code="lark_route_configuration_removed",
                            restart_count=restart_count,
                        )
                        break
                    if stop.is_set():
                        break
                    restart_count += 1
                    error_code = result.get("error_code")
                    requested_disconnect = (
                        reconnect_requested.is_set()
                        and error_code == "lark_event_source_disconnected"
                    )
                    self._update_health(
                        profile,
                        status="retrying",
                        error_code=(
                            None
                            if result.get("ok") is True or requested_disconnect
                            else str(error_code or "lark_event_listener_failed")
                        ),
                        restart_count=restart_count,
                    )
                except Exception:
                    restart_count += 1
                    self._update_health(
                        profile,
                        status="retrying",
                        error_code="lark_event_listener_failed",
                        restart_count=restart_count,
                    )
                stop.wait(min(5.0, 0.25 * (2 ** min(restart_count, 4))))
            current_thread = threading.current_thread()
            with self._lock:
                current_worker = self._workers.get(profile)
                replaced_by_new_worker = (
                    current_worker is not None
                    and current_worker[1] is not current_thread
                )
            if (
                not self._closed.is_set()
                and not replaced_by_new_worker
                and self._health.get(profile, {}).get("status") != "inactive"
            ):
                self._update_health(profile, status="stopped", error_code=None)
        finally:
            current_thread = threading.current_thread()
            with self._lock:
                worker = self._workers.get(profile)
                if worker is not None and worker[1] is current_thread:
                    self._workers.pop(profile, None)
            if not self._closed.is_set():
                try:
                    reconfigured = profile in runtime._active_profile_configs(
                        self.snapshot_provider()
                    )
                except Exception:
                    reconfigured = False
                if reconfigured:
                    self.refresh()

    def refresh(self) -> None:
        if self._refresh_profile_workers():
            with self._lock:
                self._schedule_profile_refresh()

    def _refresh_profile_workers(self, *, resume_queues: bool = True) -> bool:
        """Apply known listener changes and report whether discovery must retry."""
        with self._lock:
            if self._closed.is_set():
                return False
            self._profile_refresh_sequence += 1
            sequence = self._profile_refresh_sequence
        snapshot = self.snapshot_provider()
        profile_configs, unavailable = self._listener_configs(snapshot)
        with self._lock:
            if self._closed.is_set():
                return False
            # Record discovery before queue recovery can block, so an older
            # inventory retry cannot stop a worker that still has queued events.
            self._observed_profile_refresh_sequence = max(
                self._observed_profile_refresh_sequence, sequence
            )
        desired = {
            profile: self._profile_worker_fingerprint(
                snapshot,
                profile,
                profile_config,
            )
            for profile, profile_config in profile_configs.items()
        }
        if self._closed.is_set():
            return False
        if resume_queues:
            self._resume_session_queues(snapshot)
        # A filesystem read may outlive server shutdown (for example, while
        # waiting for OS directory consent). Never start effects after close.
        with self._lock:
            if self._closed.is_set():
                return False
            if sequence < self._observed_profile_refresh_sequence:
                # Discovery can advance while this refresh is resuming queues.
                return True
            for profile in unavailable:
                replacement = desired.pop(profile)
                worker = self._workers.get(profile)
                if worker is None:
                    continue
                previous = worker[2]
                # Keep the original pipe and locks while the same group's
                # local App inventory is unavailable. Routing still checks
                # current grants; an observed removal uses the stop path.
                if (
                    previous.transport.cli_bin == replacement.transport.cli_bin
                    and previous.transport.callback_chats == replacement.transport.callback_chats
                    and not previous.transport.other_binding_id
                ):
                    desired[profile] = previous
            stale = (set(self._workers) - set(desired)) | {
                profile
                for profile in set(self._workers) & set(desired)
                if self._workers[profile][2] != desired[profile]
            }
            for profile in stale:
                stop, thread, previous = self._workers[profile]
                replacement = desired.get(profile)
                consumer = self._message_consumers.get(profile)
                if (
                    replacement is not None
                    and previous.transport == replacement.transport
                    and consumer is not None
                    and consumer[0] is stop
                ):
                    # Group routes still own their queued messages. The old
                    # private admission closure fences its revoked binding
                    # while the same worker drains under the existing App lease.
                    if self._disconnect_message_consumer(consumer):
                        self._workers[profile] = (stop, thread, replacement)
                    continue
                self._workers.pop(profile)
                stop.set()
            missing = set(desired) - set(self._workers)
            for profile in sorted(missing):
                stop = threading.Event()
                self._health[profile] = {
                    "status": "starting",
                    "event_count": 0,
                    "replied_count": 0,
                    "last_event_status": None,
                    "error_code": None,
                    "restart_count": 0,
                    "updated_at": self._now(),
                }
                thread = threading.Thread(
                    target=self._profile_poller,
                    args=(profile, stop),
                    name=f"loopx-lark-{profile}",
                    daemon=True,
                )
                self._workers[profile] = (stop, thread, desired[profile])
                thread.start()
        return bool(unavailable)

    def _resume_session_queues(self, snapshot: Mapping[str, Any]) -> None:
        binding_payloads = snapshot.get("binding_payloads")
        contexts = snapshot.get("goal_contexts")
        if isinstance(binding_payloads, Mapping) and isinstance(contexts, Mapping):
            for goal_id, payload in binding_payloads.items():
                if not isinstance(payload, Mapping):
                    continue
                for binding in bindings_for_goal(payload, str(goal_id)):
                    if self._closed.is_set():
                        return
                    raw_routing = binding.get("routing")
                    routing: Mapping[str, Any] = (
                        raw_routing if isinstance(raw_routing, Mapping) else {}
                    )
                    if routing.get("ingress_mode") != "session_queue":
                        continue
                    context = contexts.get(str(goal_id))
                    context = context if isinstance(context, Mapping) else {}
                    session_id = str(binding.get("session_id") or "")
                    work_dir = str(context.get("work_dir") or "")
                    try:
                        has_queued_turns = bool(
                            session_id
                            and self.runtime_controller.store.queued_turns(session_id)
                        )
                    except KeyError:
                        has_queued_turns = False
                    if has_queued_turns and work_dir:
                        resolved_work_dir = Path(work_dir).expanduser().resolve()
                        # Discovery is slow I/O; only cancellation and the
                        # controller's I/O-free worker admission belong here.
                        with self._lock:
                            if self._closed.is_set():
                                return
                            self.runtime_controller.resume_session_queue(
                                session_id=session_id,
                                work_dir=resolved_work_dir,
                                objective=MANAGER_AGENT_OBJECTIVE
                                if routing.get("conversation_kind") == "manager"
                                else str(context.get("objective") or goal_id),
                            )

    def active_profiles(self) -> list[str]:
        with self._lock:
            return sorted(self._workers)

    def close(self) -> None:
        self._closed.set()
        self._delivery_wakeup.set()
        with self._lock:
            if self._refresh_retry is not None:
                self._refresh_retry.cancel()
                self._refresh_retry = None
            workers = list(self._workers.values())
            self._workers.clear()
        for stop, _thread, _fingerprint in workers:
            stop.set()
        for _stop, thread, _fingerprint in workers:
            thread.join(timeout=3)
        self._delivery_worker.close(timeout=3)


__all__ = ["LarkGoalTopicRuntimeService"]
