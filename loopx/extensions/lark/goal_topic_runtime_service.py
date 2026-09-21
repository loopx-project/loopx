"""Worker lifecycle for the Lark Goal Topic runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
from typing import Any

from ...chat_manager import MANAGER_AGENT_OBJECTIVE
from .goal_channel_contracts import bindings_for_goal
from .manager_context import session_turn_effect
from .team_plan_confirmation import (
    active_profile_chat_ids,
    deliver_team_plan_review_cards_from_runtime,
    handle_lark_review_callback_for_profile,
)


SnapshotProvider = Callable[[], Mapping[str, Any]]
ProfilePoller = Callable[[str, threading.Event], None]
ManagerRouteReconciler = Callable[[Mapping[str, Any]], Mapping[str, Any]]
ProfileWorkerFingerprint = tuple[str, str, tuple[str, ...]]
ProfileWorker = tuple[threading.Event, threading.Thread, ProfileWorkerFingerprint]


class LarkGoalTopicRuntimeService:
    """Own one event-consumer worker per reusable Lark App profile."""

    def __init__(
        self,
        *,
        snapshot_provider: SnapshotProvider,
        runtime_root: str | Path,
        runtime_controller: Any,
        action_service: Any | None = None,
        profile_poller: ProfilePoller | None = None,
        manager_route_reconciler: ManagerRouteReconciler | None = None,
    ) -> None:
        self.snapshot_provider = snapshot_provider
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.runtime_controller = runtime_controller
        self.action_service = action_service
        self._profile_poller = profile_poller or self._poll_profile
        self.manager_route_reconciler = manager_route_reconciler
        self._lock = threading.Lock()
        self._workers: dict[str, ProfileWorker] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._closed = threading.Event()
        self._startup_thread: threading.Thread | None = None

    def start(self) -> None:
        """Discover existing bindings without blocking the HTTP readiness path."""

        with self._lock:
            if self._closed.is_set() or self._startup_thread is not None:
                return
            self._startup_thread = threading.Thread(
                target=self._refresh_on_start,
                name="loopx-lark-startup",
                daemon=True,
            )
            self._startup_thread.start()

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
        return (
            str(profile_config.get("cli_bin") or "lark-cli"),
            str(profile_config.get("bot_app_id") or ""),
            callback_chats,
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
                        return {
                            "response_text": response_text,
                            "effect_receipt": session_turn_effect(effective_route),
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
                            profile=profile,
                            profile_config=profile_config,
                        )

                    result = runtime.stream_lark_goal_topic_profile(
                        profile=profile,
                        snapshot_provider=self.snapshot_provider,
                        stop=stop,
                        runtime_root=self.runtime_root,
                        answer=answer,
                        proposal_deliverer=deliver_proposals,
                        review_callback_handler=(
                            handle_review_callback
                            if self.action_service is not None
                            else None
                        ),
                        health_sink=lambda update: self._update_health(
                            profile, **dict(update)
                        ),
                    )
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
                    self._update_health(
                        profile,
                        status="retrying",
                        error_code=(
                            None
                            if result.get("ok") is True
                            else str(
                                result.get("error_code")
                                or "lark_event_listener_failed"
                            )
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
        from . import goal_topic_runtime as runtime

        if self._closed.is_set():
            return
        snapshot = self.snapshot_provider()
        profile_configs = runtime._active_profile_configs(snapshot)
        desired = {
            profile: self._profile_worker_fingerprint(
                snapshot,
                profile,
                profile_config,
            )
            for profile, profile_config in profile_configs.items()
        }
        if self._closed.is_set():
            return
        self._resume_session_queues(snapshot)
        # A filesystem read may outlive server shutdown (for example, while
        # waiting for OS directory consent). Never start effects after close.
        with self._lock:
            if self._closed.is_set():
                return
            stale = (set(self._workers) - set(desired)) | {
                profile
                for profile in set(self._workers) & set(desired)
                if self._workers[profile][2] != desired[profile]
            }
            for profile in stale:
                stop, _thread, _fingerprint = self._workers.pop(profile)
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
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for stop, _thread, _fingerprint in workers:
            stop.set()
        for _stop, thread, _fingerprint in workers:
            thread.join(timeout=3)


__all__ = ["LarkGoalTopicRuntimeService"]
