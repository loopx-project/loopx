"""Original-Session app-server transport for the existing governed Turn driver.

No scheduler, task store or message consumer is created here. The caller
selects native work; the existing Turn command owns fresh admission, validation,
writeback, quota and scheduler handoff. Provider metadata uses the existing
read-only native control client. Chat retains its own execution fence.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ...chat_runtime import ChatRuntimeAdapter
    from ...chat_store import ChatSessionStore

from ...control_plane.goals.activation import goal_is_stopped
from ...file_lock import exclusive_file_lock
from .report_failure_proof import (
    build_report_quota_request,
    build_report_timeout_request,
    is_report_failure_contradicted,
    observe_report_failure,
    verify_report_window_failure,
)
from .steward_managed_mode import ManagedSessionTurn, StewardManagedMode
from .steward_monitor import (
    STEWARD_BACKGROUND_CHANNEL_PREFIX as STEWARD_BACKGROUND_CHANNEL_PREFIX,
    STEWARD_BACKGROUND_SESSION_PREFIX as STEWARD_BACKGROUND_SESSION_PREFIX,
    ensure_steward_background_session,
    lookup_steward_monitor_turn,
    steward_background_channel_id,
    steward_background_session_id,
)
from .steward_turn_common import (
    MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS as MONITOR_RECOVERY_RETRY_COOLDOWN_SECONDS,
    MonitorRestartCarrier,
    StewardBindingsProtocol,
    StewardControllerProtocol,
    StewardManagedProtocol,
    StewardStoreProtocol,
    _FILE_LOCK_TIMEOUT_ERROR_RE as _FILE_LOCK_TIMEOUT_ERROR_RE,
    _POST_CONTEXT_WATCH_ERRORS as _POST_CONTEXT_WATCH_ERRORS,
    _REPORT_VALIDATOR_PREFIX as _REPORT_VALIDATOR_PREFIX,
    _USER_ACTION_DEDUPE_KEY as _USER_ACTION_DEDUPE_KEY,
    _WORKER_EXITED_ERROR as _WORKER_EXITED_ERROR,
    append_bounded_ellipsis,
    as_dict,
    as_dict_list,
    as_mapping,
    as_optional_dict,
    as_optional_str,
    iso_timestamp_seconds,
    is_zero_effect_replan_selection_deferral as _is_zero_effect_replan_selection_deferral,
    monitor_store_for,
    native_steward_client_id as native_steward_client_id,
    normalize_report_kernel_candidate as _normalize_report_kernel_candidate,
    normalize_steward_advancement_kernel_candidate as _normalize_steward_advancement_kernel_candidate,
    report_turn as report_turn,
    steward_monitor_for,
    same_report_validator_command as _same_report_validator_command,
    split_bounded_evidence_items,
    split_evidence_text_chunks,
)

from .steward_turn_context import (
    apply_steward_user_actions,
    governance_replan_context,
    normalize_steward_actions,
    steward_followup_context,
)
from .steward_turn_execution import execute_steward_turn
from .steward_turn_recovery import (
    build_replay_runner,
    cleared_replan_guard,
    frozen_report_contract_recovery,
    has_recoverable_stopped_report,
    monitor_restart_basis,
    monitor_restart_carrier,
    monitor_restart_request,
    monitor_selection_failed_before_poll,
    recover_report_settlement,
    report_continuation_basis,
    report_launch_recovery,
    report_selection_recovery,
    settle_stopped_report_candidate,
    stopped_report_candidate_recovery,
)

_iso_timestamp_seconds = iso_timestamp_seconds
_append_bounded_ellipsis = append_bounded_ellipsis
_split_bounded_evidence_items = split_bounded_evidence_items
_split_evidence_text_chunks = split_evidence_text_chunks


class StewardTurnHost:
    """Host bridge preserving original-Session identity across admission, recovery, and execution."""

    def __init__(self, controller: StewardControllerProtocol) -> None:
        self.controller: StewardControllerProtocol = controller
        self.store: StewardStoreProtocol = controller.store
        self.bindings: StewardBindingsProtocol | None = controller.project_contexts.conversation_bindings
        self.managed: StewardManagedProtocol = cast(StewardManagedProtocol, StewardManagedMode(controller, self.bindings))

    @staticmethod
    def background_session_id(binding_id: str) -> str:
        return str(steward_background_session_id(binding_id))

    @staticmethod
    def background_channel_id(binding_id: str) -> str:
        return str(steward_background_channel_id(binding_id))

    def _lookup_monitor_turn(
        self, binding_id: str, actor: Mapping[str, object], client_id: str
    ) -> dict[str, object] | None:
        return as_optional_dict(
            lookup_steward_monitor_turn(monitor_store_for(self.store), binding_id, str(actor["session_id"]), client_id)
        )

    def _ensure_background_session(
        self, binding_id: str, parent_session: Mapping[str, object]
    ) -> dict[str, object]:
        return as_dict(ensure_steward_background_session(monitor_store_for(self.store), binding_id, parent_session))

    def _monitor_selection_failed_before_poll(self, turn: Mapping[str, object]) -> bool:
        return monitor_selection_failed_before_poll(self.store, turn)

    def _observe(self, actor: Mapping[str, object]) -> dict[str, object] | None:
        """Verify provider identity before entering publication/effect fences."""
        if actor.get("schema_version") == "loopx_managed_session_actor_v0":
            return None
        if self.bindings is None:
            raise ValueError("the original steward binding is unavailable")
        session = self.store.load_session(str(actor["session_id"]))
        if not session or not session.get("steward_context"):
            raise ValueError("the original steward Session is unavailable")
        saved = as_mapping(session["steward_context"])
        return self.bindings.resolve(
            binding_id=str(saved["binding_id"]),
            session=session,
            include_observation=True,
            origin="web",
            source_ref=str(saved["source_ref"]),
            sender_ref=str(saved["operator_ref"]),
            private_human_message=False,
            session_context=dict(saved),
        )

    def _admit(
        self,
        request: dict[str, object],
        *,
        turn: dict[str, object] | None = None,
        observed: dict[str, object] | None = None,
        carrier_session: dict[str, object] | None = None,
        prepare_context: bool = False,
        settlement_recovery: dict[str, object] | None = None,
        frozen_contract_recovery: dict[str, object] | None = None,
        launch_recovery: dict[str, object] | None = None,
        selection_recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if self.controller.closed.is_set():
            raise ValueError("the original Chat host is stopped")
        actor = as_mapping(request["actor"])
        if actor.get("schema_version") == "loopx_managed_session_actor_v0":
            return as_dict(
                ManagedSessionTurn(self.controller).admit(
                    request,
                    turn=turn,
                    settlement_recovery=settlement_recovery,
                    frozen_contract_recovery=frozen_contract_recovery,
                    selection_recovery=selection_recovery,
                )
            )
        if self.bindings is None:
            raise ValueError("the original steward binding is unavailable")
        observed = observed if observed is not None else self._observe(actor)
        if observed is None:
            raise ValueError("the original steward observation is unavailable")
        session = self.store.load_session(str(actor["session_id"]))
        if not session or not session.get("steward_context"):
            raise ValueError("the original steward Session is unavailable")
        saved = as_mapping(session["steward_context"])
        current = self.bindings.read()
        binding = next(
            (row for row in as_dict_list(current.get("bindings")) if row.get("binding_id") == saved.get("binding_id")),
            None,
        )
        if binding != observed.get("binding"):
            raise ValueError("the original steward authorization changed during identity verification")
        # Identity IO may have waited.
        selected: dict[str, object] = {
            **self.bindings._resolve_observed(
                current,
                binding or {},
                as_dict(observed.get("observation")),
                {
                    "source_ref": saved["source_ref"],
                    "sender_ref": saved["operator_ref"],
                    "private_human_message": False,
                    "origin": "web",
                    "session_context": saved,
                    "session": self.bindings._session_observation(session),
                },
            ),
            "observation": observed.get("observation"),
        }
        from ...agent_registry import load_goal_from_registry

        goal_id = str(actor["goal_id"])
        goal = as_optional_dict(load_goal_from_registry(cast(Path, self.controller.registry_path), goal_id))
        canonical_actor = as_optional_dict(as_mapping(selected.get("binding")).get("managed_actor"))
        monitor = (
            next(
                (
                    row
                    for row in as_dict_list(steward_monitor_for(self.controller).todos(dict(canonical_actor)))
                    if row.get("todo_id") == request.get("todo_id") and row.get("task_class") == "continuous_monitor"
                ),
                None,
            )
            if canonical_actor is not None and request.get("work_kind") == "monitor" and request.get("todo_id")
            else None
        )
        if request.get("work_kind") == "monitor" and carrier_session is None:
            bg_sid = self.background_session_id(str(saved["binding_id"]))
            if turn is not None and turn.get("session_id") == bg_sid:
                carrier_session = self.store.load_session(bg_sid)
                if carrier_session is None:
                    raise ValueError("the steward background watch Session is unavailable")
            elif turn is None:
                existing_bg = self.store.load_session(bg_sid)
                if existing_bg is not None and isinstance(existing_bg.get("steward_background_context"), Mapping):
                    carrier_session = existing_bg
        report: dict[str, object] = {}
        if request.get("work_kind") == "report":
            from .steward_reports import StewardReports

            subscription, window = StewardReports(self.controller).current_window(str(saved["binding_id"]))
            todos = (
                as_dict_list(steward_monitor_for(self.controller).todos(dict(canonical_actor)))
                if canonical_actor
                else []
            )
            report = {
                "report_window": window,
                "report_subscription": subscription,
                "report_todo": next((row for row in todos if row.get("todo_id") == request.get("todo_id")), None),
            }
        scope: dict[str, str] | None = None
        observed_scope_id: str | None = None
        if turn is not None:
            from ...chat_manager_context import manager_authorization_scope_id_for_registry

            goal_ids = self.bindings.steward_scope(session)
            if goal_ids is None:
                raise ValueError("the original portfolio scope is unavailable")

            default_channel_id = session["channel_id"]

            def scope_id(ids: list[str], *, channel_id: object = default_channel_id) -> str:
                return str(
                    manager_authorization_scope_id_for_registry(
                        cast(Path, self.controller.registry_path),
                        ids,
                        runtime_root=self.controller.coordination_runtime_root,
                        channel_id=channel_id,
                    )
                )

            scope = {
                "current": scope_id(goal_ids),
                "without_actor": scope_id([value for value in goal_ids if value != actor["goal_id"]]),
            }
            observed_scope_id = (
                scope_id(goal_ids, channel_id=carrier_session["channel_id"])
                if carrier_session is not None
                else scope["current"]
            )
        admitted = self.bindings._core(
            "collaboration.steward.managed_turn.admit",
            {
                "binding": selected["binding"],
                "session": session,
                "observation": selected["observation"],
                "workspace_path": str(self.managed._workspace(session)),
                "queued_turn_count": len(self.store.queued_turns(str(session["session_id"]))),
                "goal": self.managed._goal(goal_id),
                "goal_active": bool(goal and goal.get("status") == "active" and not goal_is_stopped(goal)),
                "request": request,
                "turn": turn,
                "scope": scope,
                "monitor_todo": monitor,
                **(
                    {
                        "carrier_session": carrier_session,
                        "carrier_queued_turn_count": len(self.store.queued_turns(str(carrier_session["session_id"]))),
                    }
                    if carrier_session is not None
                    else {}
                ),
                **({"report_settlement_recovery": settlement_recovery} if settlement_recovery is not None else {}),
                **({"report_frozen_contract_recovery": frozen_contract_recovery} if frozen_contract_recovery is not None else {}),
                **({"report_launch_recovery": launch_recovery} if launch_recovery is not None else {}),
                **({"report_selection_recovery": selection_recovery} if selection_recovery is not None else {}),
                **self.report_quota_basis(request),
                **self.report_timeout_basis(request),
                **self.monitor_restart_basis(request),
                **({"preparing_model_context": True} if prepare_context else {}),
                **report,
            },
        )
        if admitted.get("refresh_original_scope") and scope is not None:
            self.store.update_session(str(session["session_id"]), manager_authorization_scope_id=scope["current"])
            if carrier_session is not None and observed_scope_id:
                self.store.update_session(str(carrier_session["session_id"]), manager_authorization_scope_id=observed_scope_id)
        return {**admitted, "observed_scope_id": observed_scope_id if scope else None}

    def monitor_restart_basis(self, request: Mapping[str, object]) -> dict[str, object]:
        """Read original worker/journals without dispatching or repairing effects."""
        return monitor_restart_basis(self, request)

    @staticmethod
    def monitor_restart_request(
        original: Mapping[str, object], *, now: float | None = None
    ) -> dict[str, object] | None:
        """Prepare correlation only; current typed admission decides recovery."""
        return monitor_restart_request(original, now=now)

    def monitor_restart_carrier(
        self, original: Mapping[str, object], client_id: str
    ) -> MonitorRestartCarrier | None:
        """Find the latest carrier for the same observation; typed admission proves a safe restart."""
        return monitor_restart_carrier(self, original, client_id)

    def _report_continuation_basis(
        self, request: Mapping[str, object], *, recovery_key: str, basis_key: str
    ) -> dict[str, object]:
        return report_continuation_basis(self, request, recovery_key=recovery_key, basis_key=basis_key)

    def report_quota_basis(self, request: Mapping[str, object]) -> dict[str, object]:
        return self._report_continuation_basis(
            request, recovery_key="report_quota_recovery", basis_key="report_quota_basis"
        )

    def report_timeout_basis(self, request: Mapping[str, object]) -> dict[str, object]:
        return self._report_continuation_basis(
            request, recovery_key="report_timeout_recovery", basis_key="report_timeout_basis"
        )

    def report_failure_observation(self, original: Mapping[str, object]) -> dict[str, object] | None:
        """Read the original native failure without starting or resuming work."""
        return as_optional_dict(observe_report_failure(self.controller, self.store, original))

    def report_window_failure_verified(
        self, original: Mapping[str, object], window: dict[str, object], *, generation_locked: bool = False
    ) -> bool:
        """Query live provider history first; fall back to retained proof only if unavailable."""
        return bool(
            verify_report_window_failure(
                self.controller, self.store, original, window, generation_locked=generation_locked
            )
        )

    def report_failure_contradicted(self, original: Mapping[str, object], window: dict[str, object]) -> bool:
        """Return True when a prior live observation contradicted this Turn's failure proof."""
        return bool(is_report_failure_contradicted(self.controller.coordination_runtime_root, original, window))

    def report_quota_request(
        self, original: Mapping[str, object], *, report_window: dict[str, object] | None = None
    ) -> dict[str, object] | None:
        """Observe quota recovery, never resume or rewrite the failed native Turn."""
        fallback = (
            None
            if getattr(self.report_failure_observation, "__func__", None) is _DEFAULT_REPORT_FAILURE_OBSERVATION
            else self.report_failure_observation
        )
        return as_optional_dict(
            build_report_quota_request(
                self.controller, self.store, original, report_window=report_window, fallback_observe_fn=fallback
            )
        )

    def report_timeout_request(self, original: Mapping[str, object]) -> dict[str, object] | None:
        """Observe idle Antigravity timeout for one bounded isolated-cascade retry."""
        return as_optional_dict(build_report_timeout_request(original, observe_fn=self.report_failure_observation))

    def report_selection_recovery(
        self, original: dict[str, object], *, probe_turn: dict[str, object] | None = None
    ) -> dict[str, object] | None:
        """The typed owner qualifies this observation before any new worker."""
        return report_selection_recovery(self, original, probe_turn=probe_turn)

    def _cleared_replan_guard(self, actor: Mapping[str, object]) -> dict[str, object] | None:
        return cleared_replan_guard(self, actor)

    def _report_launch_recovery(self, original: dict[str, object]) -> dict[str, object] | None:
        return report_launch_recovery(self, original)

    def _report_validator(
        self, request: Mapping[str, object]
    ) -> Callable[[Mapping[str, object], Mapping[str, object]], dict[str, object]]:
        """Validate with the owning installed package, outside model execution."""

        def validate(_plan: Mapping[str, object], _result: Mapping[str, object]) -> dict[str, object]:
            from ..periodic_report.private_generation import attempt_directory, validate_private_generation
            from .steward_reports import report_service

            reports, owner_key = report_service(self.controller, dict(as_mapping(request["actor"])))
            try:
                subscription, window = reports.current_window(owner_key)
                if (
                    not subscription
                    or not window
                    or window["window_id"] != request["report_window_id"]
                    or window["subscription_revision"] != request["report_subscription_revision"]
                ):
                    raise ValueError("the original report window changed")
                validate_private_generation(attempt_directory(reports.root, window))
                if not reports.delivery_current(owner_key, window):
                    raise ValueError("the original report authorization changed")
            except (OSError, ValueError, RuntimeError):
                return {
                    "status": "failed",
                    "validator_kind": "callback",
                    "summary": "independent original report validation failed",
                    "recovery_kind": "repair_required",
                }
            return {
                "status": "passed",
                "validator_kind": "callback",
                "summary": "independent original report validation passed",
            }

        return validate

    def _report_fence(self, request: Mapping[str, object]) -> AbstractContextManager[object]:
        actor = as_mapping(request["actor"])
        if actor.get("schema_version") == "loopx_managed_session_actor_v0":
            from ...attached_session import managed_session_goal_commit

            return managed_session_goal_commit(
                store=cast("ChatSessionStore", self.store),
                registry_path=cast(Path, self.controller.registry_path),
                session_id=str(actor["session_id"]),
            )()
        if self.bindings is None:
            raise ValueError("the original steward binding is unavailable")
        return cast(
            AbstractContextManager[object],
            exclusive_file_lock(self.bindings.path, operation="original_report_effect"),
        )

    def _task_lease_proof(
        self, request: Mapping[str, object], lease: Mapping[str, object] | None
    ) -> dict[str, object]:
        from .steward_commands import native_command

        actor = as_mapping(request["actor"])
        observed = as_optional_dict(
            as_mapping(
                native_command(
                    self.controller,
                    ["task-lease", "inspect", "--goal-id", str(actor["goal_id"]), "--todo-id", str(request["todo_id"])],
                )
            ).get("lease")
        )
        if (
            not lease
            or not observed
            or observed.get("status") != "active"
            or any(observed.get(key) != lease.get(key) for key in ("owner", "idempotency_key", "lease_epoch", "version"))
        ):
            raise ValueError("the original task execution lease changed")
        return {
            "task_lease_idempotency_key": observed["idempotency_key"],
            "task_lease_expected_version": observed["version"],
        }

    @contextmanager
    def _task_execution_lease(
        self,
        request: Mapping[str, object],
        turn_id: str,
        *,
        recovery: bool = False,
        admission_retry: bool = False,
        claim_args: list[str] | None = None,
        retain_for_retry: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, object]]:
        """Both generation and cached validation use the original Todo owner."""
        from .steward_commands import acquire_steward_task_lease, native_command

        actor = as_mapping(request["actor"])
        goal_id = str(actor["goal_id"])
        lease = as_dict(
            acquire_steward_task_lease(
                self.controller,
                request,
                turn_id,
                recovery=recovery,
                admission_retry=admission_retry,
                claim_args=claim_args,
            )
        )
        try:
            yield lease
        finally:
            if retain_for_retry is None or not retain_for_retry():
                try:
                    retained = as_optional_dict(
                        as_mapping(
                            native_command(
                                self.controller,
                                ["task-lease", "inspect", "--goal-id", goal_id, "--todo-id", str(request["todo_id"])],
                            )
                        ).get("lease")
                    )
                    if retained and retained.get("status") == "active":
                        proof = self._task_lease_proof(request, lease)
                        native_command(
                            self.controller,
                            [
                                "task-lease", "release", "--goal-id", goal_id, "--todo-id", str(request["todo_id"]),
                                "--owner", str(actor["agent_id"]),
                                "--idempotency-key", str(proof["task_lease_idempotency_key"]),
                                "--expected-version", str(proof["task_lease_expected_version"]),
                            ],
                        )
                except (OSError, ValueError, RuntimeError):
                    self.store.append_event(
                        str(actor["session_id"]),
                        turn_id,
                        kind="native_loopx.task_lease_release_unconfirmed",
                        payload={"todo_id": request["todo_id"], "lease_epoch": lease["lease_epoch"]},
                    )

    def _frozen_report_contract_recovery(
        self,
        actor: Mapping[str, object],
        window: Mapping[str, object],
        original: Mapping[str, object],
        *,
        key: str,
        generation: Mapping[str, object],
        root: Path,
    ) -> tuple[dict[str, object], dict[str, object]] | None:
        return frozen_report_contract_recovery(
            self,
            actor,
            window,
            original,
            key=key,
            generation=generation,
            root=root,
            normalize_report_candidate=_normalize_report_kernel_candidate,
        )

    def _stopped_report_candidate_recovery(
        self,
        actor: Mapping[str, object],
        window: Mapping[str, object],
        original: Mapping[str, object],
        *,
        key: str,
        folder: Path,
        root: Path,
    ) -> tuple[dict[str, object], dict[str, object]] | None:
        return stopped_report_candidate_recovery(self, actor, window, original, key=key, folder=folder, root=root)

    def has_recoverable_stopped_report(
        self, actor: Mapping[str, object], window: Mapping[str, object], todo: Mapping[str, object]
    ) -> bool:
        """Read-only gate check before acquiring the recovery fence."""
        return has_recoverable_stopped_report(self, actor, window, todo, report_turn_fn=report_turn)

    @staticmethod
    def _build_replay_runner(
        normalized_candidate: Mapping[str, object]
    ) -> Callable[[Mapping[str, object], object], dict[str, object]]:
        return build_replay_runner(normalized_candidate)

    def _settle_stopped_report_candidate(
        self,
        *,
        actor: Mapping[str, object],
        window: Mapping[str, object],
        todo: Mapping[str, object],
        original: Mapping[str, object],
        request: dict[str, object],
        observed: dict[str, object] | None,
        reports: object,
        owner_key: str,
        folder: Path,
        original_key: str,
        stopped_proof: dict[str, object],
        raw_candidate: dict[str, object],
    ) -> dict[str, object]:
        return settle_stopped_report_candidate(
            self,
            actor=actor,
            window=window,
            todo=todo,
            original=original,
            request=request,
            observed=observed,
            reports=reports,
            owner_key=owner_key,
            folder=folder,
            original_key=original_key,
            stopped_proof=stopped_proof,
            raw_candidate=raw_candidate,
            normalize_report_candidate=_normalize_report_kernel_candidate,
        )

    def recover_report_settlement(
        self, actor: dict[str, object], window: dict[str, object], todo: dict[str, object]
    ) -> None:
        """Resume only cached, validated native effects of the original report."""
        recover_report_settlement(self, actor, window, todo, report_turn_fn=report_turn)

    def _submission_binding(self, binding_id: str) -> dict[str, object] | None:
        if self.bindings is None:
            return None
        return next(
            (row for row in as_dict_list(self.bindings.read().get("bindings")) if row.get("binding_id") == binding_id),
            None,
        )

    def _prepare_submission(
        self,
        binding: dict[str, object] | None,
        *,
        turn_instance_id: str,
        todo_id: str | None,
        validation_command: Sequence[str] | None,
        work_kind: str,
        monitor_deadline: str | None,
        report_window: dict[str, object] | None,
        quota_observation: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Read original request/recovery identity without accepting any work."""
        if binding is None:
            raise ValueError("the original steward binding is unavailable")
        actor = as_optional_dict(binding.get("managed_actor"))
        if not actor:
            raise ValueError("prepare the original steward identity first")
        session_id = str(actor["session_id"])
        request: dict[str, object] = {
            "operation": "native_turn",
            "host": "codex-app-server",
            "actor": actor,
            "turn_instance_id": turn_instance_id,
            "todo_id": todo_id,
            "work_kind": work_kind,
            "validation_command": list(validation_command) if validation_command is not None else None,
        }
        if work_kind == "report":
            if not report_window:
                raise ValueError("a report requires its original admitted calendar window")
            request.update(
                report_window_id=report_window["window_id"],
                report_subscription_revision=report_window["subscription_revision"],
            )
        client_id = native_steward_client_id(actor, turn_instance_id)
        existing = (
            self._lookup_monitor_turn(str(binding["binding_id"]), actor, client_id)
            if work_kind == "monitor"
            else self.store.turn_for_client(session_id, client_id)
        )
        if quota_observation is not None:
            if binding != quota_observation["binding"]:
                raise ValueError("the original steward authorization changed during quota observation")
            original = as_mapping(quota_observation["original"])
            if existing is None or any(
                existing.get(key) != original.get(key)
                for key in (
                    "turn_id", "client_turn_id", "status", "loopx_request", "response",
                    "upstream_turn_id", "delta_count", "error_code", "error", "loopx_execution",
                )
            ):
                raise ValueError("the original report changed during quota observation")
        launch_recovery: dict[str, object] | None = None
        selection_recovery: dict[str, object] | None = None
        if existing is not None:
            frozen = {
                key: value
                for key, value in as_mapping(existing.get("loopx_request")).items()
                if key not in {"monitor_lease_version", "monitor_deadline"}
            }
            frozen.setdefault("work_kind", "native_turn")
            comparable_frozen = (
                {**frozen, "validation_command": request["validation_command"]}
                if work_kind == "report"
                and _same_report_validator_command(frozen.get("validation_command"), request.get("validation_command"))
                else frozen
            )
            if (
                comparable_frozen != request
                or work_kind == "monitor"
                and as_mapping(existing.get("loopx_request")).get("monitor_deadline") != monitor_deadline
            ):
                raise ValueError("native steward request identity conflict")
            if work_kind == "report":
                assert report_window is not None
                retained = report_turn(self.store, actor, report_window)
                if retained is not None and retained["turn_id"] != existing["turn_id"]:
                    return {"turn": retained}
                if quota_observation is None:
                    return {"turn": existing, "quota_probe": {"original": existing, "binding": binding}}
                quota_request = as_optional_dict(quota_observation.get("request"))
                if quota_request is not None:
                    if _same_report_validator_command(
                        quota_request.get("validation_command"), request.get("validation_command")
                    ):
                        quota_request = {**quota_request, "validation_command": request["validation_command"]}
                    request = quota_request
                    client_id = native_steward_client_id(actor, str(request["turn_instance_id"]))
                    existing = None
        if existing is not None:
            recovery = (
                self.monitor_restart_carrier(existing, client_id)
                if work_kind == "monitor" and not self._monitor_selection_failed_before_poll(existing)
                else None
            )
            if recovery is not None:
                if "turn" in recovery:
                    return {"turn": recovery["turn"]}
                request = as_dict(recovery["request"])
                client_id = str(recovery["client_id"])
                existing = None
        if existing is not None:
            monitor_retry = work_kind == "monitor" and self._monitor_selection_failed_before_poll(existing)
            if not monitor_retry and work_kind != "report":
                return {"turn": existing}
            client_id = native_steward_client_id(actor, turn_instance_id + ".admission-retry")
            retry = (
                self._lookup_monitor_turn(str(binding["binding_id"]), actor, client_id)
                if work_kind == "monitor"
                else self.store.turn_for_client(session_id, client_id)
            )
            if retry is not None:
                if work_kind != "report":
                    return {"turn": retry}
                selection_recovery = self.report_selection_recovery(existing, probe_turn=retry)
                if selection_recovery is None or "probe" not in selection_recovery:
                    return {"turn": retry}
                client_id = native_steward_client_id(actor, turn_instance_id + ".admission-resume")
                resumed = self.store.turn_for_client(session_id, client_id)
                if resumed is not None:
                    return {"turn": resumed}
            if work_kind == "report" and selection_recovery is None:
                selection_recovery = self.report_selection_recovery(existing)
                if selection_recovery is None:
                    launch_recovery = self._report_launch_recovery(existing)
                    if launch_recovery is None:
                        return {"turn": existing}
                    request["resume_turn_key"] = as_mapping(as_mapping(existing.get("response")).get("native_loopx")).get(
                        "resume_turn_key"
                    )
        return {
            "request": request,
            "client_id": client_id,
            "launch_recovery": launch_recovery,
            "selection_recovery": selection_recovery,
        }

    def submit(
        self,
        binding_id: str,
        *,
        turn_instance_id: str,
        todo_id: str | None = None,
        validation_command: Sequence[str] | None = None,
        work_kind: str = "native_turn",
        monitor_deadline: str | None = None,
        report_window: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], bool]:
        """Internal host entrypoint; busy conversations retain work with caller."""
        controller = self.controller

        def inspect_submission(quota_observation: dict[str, object] | None = None) -> dict[str, object]:
            binding = self._submission_binding(binding_id)
            inspected = self._prepare_submission(
                binding,
                turn_instance_id=turn_instance_id,
                todo_id=todo_id,
                validation_command=validation_command,
                work_kind=work_kind,
                monitor_deadline=monitor_deadline,
                report_window=report_window,
                quota_observation=quota_observation,
            )
            if binding != self._submission_binding(binding_id):
                raise ValueError("the original steward authorization changed during submission inspection")
            return inspected

        prepared = inspect_submission()
        quota_observation: dict[str, object] | None = None
        if "quota_probe" in prepared:
            quota_observation = as_dict(prepared["quota_probe"])
            original_turn = as_mapping(quota_observation["original"])
            quota_observation["request"] = (
                self.report_quota_request(original_turn, report_window=report_window)
                or self.report_timeout_request(original_turn)
            )
            prepared = inspect_submission(quota_observation)
        if "turn" in prepared:
            return as_dict(prepared["turn"]), False
        observed = self._observe(as_mapping(as_mapping(prepared["request"])["actor"]))
        retained: dict[str, object] | None = None
        if work_kind == "monitor":
            from .steward_commands import native_command

            if not todo_id or not monitor_deadline:
                raise ValueError("a native watch requires its original Todo and deadline")
            actor_goal = str(as_mapping(as_mapping(prepared["request"])["actor"])["goal_id"])
            retained = as_optional_dict(
                as_mapping(
                    native_command(controller, ["task-lease", "inspect", "--goal-id", actor_goal, "--todo-id", todo_id])
                ).get("lease")
            )
        if self.bindings is None:
            raise ValueError("the original steward binding is unavailable")
        with controller.lock, exclusive_file_lock(self.bindings.path, operation="admit_native_steward_turn"):
            current = self._prepare_submission(
                self._submission_binding(binding_id),
                turn_instance_id=turn_instance_id,
                todo_id=todo_id,
                validation_command=validation_command,
                work_kind=work_kind,
                monitor_deadline=monitor_deadline,
                report_window=report_window,
                quota_observation=quota_observation,
            )
            if "turn" in current:
                return as_dict(current["turn"]), False
            if current["request"] != prepared["request"] or current["client_id"] != prepared["client_id"]:
                raise ValueError("the original native steward submission changed during identity verification")
            request = as_dict(current["request"])
            client_id = str(current["client_id"])
            actor = as_mapping(request["actor"])
            session_id = str(actor["session_id"])
            session = self.store.load_session(session_id)
            if not session or not session.get("steward_context"):
                raise ValueError("the original steward Session is unavailable")
            carrier = self._ensure_background_session(binding_id, session) if work_kind == "monitor" else None
            if work_kind == "monitor":
                request.update(
                    monitor_deadline=monitor_deadline,
                    monitor_lease_version=retained["version"] if retained else 0,
                )
            admitted = self._admit(
                request,
                observed=observed,
                carrier_session=carrier,
                launch_recovery=as_optional_dict(current.get("launch_recovery")),
                selection_recovery=as_optional_dict(current.get("selection_recovery")),
            )
            target_session = carrier if carrier is not None else session
            target_session_id = str(target_session["session_id"])
            accepted = self.store.accept_managed_turn(
                target_session_id,
                client_turn_id=client_id,
                message="Advance the currently admitted native LoopX work.",
                display_message=(
                    "检查授权工作状态。" if work_kind == "monitor"
                    else "生成本期授权项目日报。" if work_kind == "report"
                    else "推进管家的 LoopX 任务。"
                ),
                origin="native_loopx",
                loopx_execution=True,
                loopx_request=request,
            )
            try:
                if (
                    not admitted["observation_only"]
                    and work_kind != "report"
                    and actor.get("schema_version") == "loopx_steward_managed_actor_v0"
                    and session.get("steward_context")
                ):
                    from ..manager_context import register_ingress

                    register_ingress(
                        self.controller.coordination_runtime_root,
                        session_id=session_id,
                        client_turn_id=client_id,
                        channel=str(session["channel_id"]),
                        sender_id=str(as_mapping(session["steward_context"])["operator_ref"]),
                        message=str(accepted.turn["message"]),
                        source_id=f"native_steward:{actor['goal_id']}:{request['turn_instance_id']}",
                        origin="native_loopx",
                    )
                if admitted["observation_only"]:
                    from .steward_monitor import StewardObservationAdapter

                    adapter: object = StewardObservationAdapter(as_optional_str(target_session.get("upstream_thread_id")))
                else:
                    adapter = controller._ensure_adapter_locked(
                        session,
                        work_dir=Path(str(admitted["workspace_path"])),
                        objective="Maintain the original steward's authorized monitoring work.",
                        accepted_turn_id=str(accepted.turn["turn_id"]),
                    )
                controller._start_accepted_turn_worker(
                    session_id=target_session_id,
                    turn_id=str(accepted.turn["turn_id"]),
                    message=str(accepted.turn["message"]),
                    attachments=[],
                    adapter=cast("ChatRuntimeAdapter", adapter),
                    loopx_execution=True,
                )
            except Exception as exc:
                controller._fail_queue_preparation(target_session_id, str(accepted.turn["turn_id"]), exc)
                raise
            return accepted.turn, accepted.created

    def _steward_followup_context(
        self,
        actor: Mapping[str, object],
        session_id: str,
        request: Mapping[str, object],
        authority: Mapping[str, object],
    ) -> dict[str, object]:
        return steward_followup_context(self, actor, session_id, request, authority)

    def _governance_replan_context(
        self, actor: Mapping[str, object], authority: Mapping[str, object]
    ) -> dict[str, object]:
        return governance_replan_context(self, actor, authority)

    @staticmethod
    def _normalize_steward_actions(
        candidate: Mapping[str, object], context: Mapping[str, object]
    ) -> dict[str, object]:
        return normalize_steward_actions(candidate, context)

    def _apply_steward_user_actions(
        self, actor: Mapping[str, object], actions: Sequence[Mapping[str, str]], turn_instance_id: str
    ) -> list[dict[str, object]]:
        return apply_steward_user_actions(self, actor, actions, turn_instance_id)

    def execute(
        self,
        *,
        session_id: str,
        turn_id: str,
        adapter: object,
        event_sink: object,
        context: dict[str, object],
    ) -> dict[str, object]:
        """Run in the original Chat worker, through all native settlement owners."""
        return execute_steward_turn(
            self,
            session_id=session_id,
            turn_id=turn_id,
            adapter=adapter,
            event_sink=event_sink,
            context=context,
            normalize_advancement=_normalize_steward_advancement_kernel_candidate,
            normalize_report=_normalize_report_kernel_candidate,
            is_replan_deferral=_is_zero_effect_replan_selection_deferral,
        )


_DEFAULT_REPORT_FAILURE_OBSERVATION = StewardTurnHost.report_failure_observation
