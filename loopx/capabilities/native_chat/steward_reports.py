"""Calendar/report IO on the original Chat worker, using the native owners.

Subscription configuration belongs to configure-goal, window admission to
periodic-report, tasks/settlement to LoopX Turn, and private delivery to the
bound provider. This adapter creates no scheduler, executor or business store.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from ...chat_store import ChatSessionStore

from ...agent_registry import load_goal_from_registry
from ...file_lock import exclusive_file_lock, try_exclusive_file_lock
from ..periodic_report.cadence import report_cadence_next_due_at
from ..periodic_report.cadence_journal import admit_cadence_window, cadence_intent, read_cadence_journal
from ..periodic_report.goal_configuration import configuration_summary, normalize_configuration
from ..periodic_report.machine_defaults import resolve_active_goal_periodic_report_subscription
from ..periodic_report.private_generation import (
    attempt_directory,
    freeze_private_report,
    prepare_private_report,
    report_prompt,
)
from .steward_commands import native_command
from .steward_managed_mode import ManagedSessionTurn, StewardManagedMode
from .steward_turn_common import (
    StewardBindingsProtocol,
    StewardControllerProtocol,
    StewardManagedProtocol,
    StewardStoreProtocol,
    as_dict,
    as_dict_list,
    as_mapping,
    as_optional_dict,
)

PRESET = "portfolio-progress"


class PendingStewardReportError(ValueError):
    pass


class _ReportsStoreProtocol(StewardStoreProtocol, Protocol):
    def queued_turns(
        self, session_id: str, *, strict: bool = False, include_reserved: bool = False,
    ) -> list[dict[str, object]]: ...

    def append_message(
        self, session_id: str, *, role: str, text: str, turn_id: str, message_id: str,
    ) -> object: ...


class _ReportsManagedProtocol(StewardManagedProtocol, Protocol):
    def _session(self, binding_id: str, actor: Mapping[str, object] | None) -> dict[str, object]: ...


class _InterruptControllerProtocol(Protocol):
    def interrupt_turn(self, *, session_id: str, turn_id: str) -> dict[str, object]: ...


class _ReportRouteProviderProtocol(Protocol):
    def route_proof(self, binding: Mapping[str, object], actor: Mapping[str, object]) -> dict[str, object]: ...


class _CalendarSourceTaskProtocol(Protocol):
    def source_task(self, actor: dict[str, object], task: dict[str, object]) -> object: ...


class _WindowGuardTargetProtocol(Protocol):
    def _published_window(self, window: dict[str, object]) -> bool: ...
    def _frozen_actor(self, window: dict[str, object]) -> dict[str, object] | None: ...
    def _known_generation_failure(self, actor: dict[str, object], window: dict[str, object]) -> dict[str, str] | None: ...
    def _cancel_failed_window(
        self, actor: dict[str, object], window: dict[str, object], failure: dict[str, str], *, cadence_locked: bool = False,
    ) -> None: ...


class _StewardReportsBase:
    def __init__(self, controller: StewardControllerProtocol) -> None:
        self.controller: StewardControllerProtocol = controller
        self.bindings: StewardBindingsProtocol = cast(
            StewardBindingsProtocol, controller.project_contexts.conversation_bindings,
        )
        self.managed: _ReportsManagedProtocol = cast(
            _ReportsManagedProtocol, StewardManagedMode(controller, self.bindings),
        )
        self.root: Path = controller.coordination_runtime_root

    @staticmethod
    def route_ref(binding: Mapping[str, object], actor: Mapping[str, object]) -> str:
        return f"steward-private:{binding['binding_id']}:{actor['source_ref']}"

    def selected(self, owner_key: str) -> dict[str, object]: raise NotImplementedError
    def subscription(self, owner_key: str, *, expected_revision: str | None = None, expected_actor: dict[str, object] | None = None) -> dict[str, object] | None: raise NotImplementedError
    def _submit(self, owner_key: str, actor: dict[str, object], window: dict[str, object], todo: dict[str, object]) -> tuple[dict[str, object], bool]: raise NotImplementedError
    def prepare(self, request: Mapping[str, object], adapter: object, *, authorization_scope_id: str) -> str: raise NotImplementedError
    def delivery_current(self, owner_key: str, window: dict[str, object]) -> bool: raise NotImplementedError
    def publish(self, session_id: str, window: Mapping[str, object]) -> bool: raise NotImplementedError

    @staticmethod
    def _read_reporting_rules(rules: Path, default_text_fn: Callable[[], str]) -> str:
        if rules.is_symlink():
            raise ValueError("the original reporting criteria changed source")
        return rules.read_text(encoding="utf-8") if rules.exists() else default_text_fn()

    @staticmethod
    def _build_health_schedule(config: Mapping[str, object]) -> tuple[dict[str, object] | None, object]:
        schedule_cfg = as_optional_dict(config.get("schedule"))
        next_due = (
            report_cadence_next_due_at(schedule_cfg, now=datetime.now(timezone.utc))
            if config.get("enabled") and schedule_cfg is not None
            else None
        )
        return schedule_cfg, next_due

    @staticmethod
    def _has_attempt_source_only(folder: Path) -> bool:
        if any(path.is_symlink() or not path.is_file() for path in (folder / "source.json", folder / "editorial-request.json")):
            return False
        for name in (
            "editorial-response.json", "generation-bundle.json", "report.md",
            "publication-candidate.json", "private-generation.json", "private-delivery.json",
        ):
            try:
                (folder / name).lstat()
            except FileNotFoundError:
                continue
            return False
        return True

    def _matched_report_parts(
        self, actor: Mapping[str, object], window: Mapping[str, object], turn: dict[str, object] | None,
    ) -> tuple[Mapping[str, object], Mapping[str, object]] | None:
        if not turn:
            return None
        request = as_mapping(turn.get("loopx_request"))
        if (
            request.get("actor") != actor
            or request.get("work_kind") != "report"
            or request.get("report_window_id") != window["window_id"]
            or request.get("report_subscription_revision") != window["subscription_revision"]
        ):
            return None
        return request, as_mapping(as_mapping(turn.get("response")).get("native_loopx"))

    def _published_window(self, window: dict[str, object]) -> bool:
        from ..periodic_report.incremental import read_periodic_report_publication_cursor
        from ..periodic_report.post_writeback_hook import evaluate_periodic_report_trigger_evaluation_intent

        cursor = as_optional_dict(read_periodic_report_publication_cursor(runtime_root=self.root, goal_id=str(window["goal_id"]), agent_id=str(window["agent_id"])))
        trigger = evaluate_periodic_report_trigger_evaluation_intent(cadence_intent(window))
        covered = cursor.get("covered_trigger_ids") if cursor else None
        return bool(cursor and isinstance(covered, list) and trigger["selected_trigger_id"] in covered)

    def _known_generation_failure(self, actor: dict[str, object], window: dict[str, object]) -> dict[str, str] | None:
        from .steward_turn import report_turn

        folder = attempt_directory(self.root, window)
        if not (folder / "editorial-response.json").is_file() or (folder / "private-generation.json").exists() or (folder / "private-delivery.json").exists():
            return None
        turn = report_turn(self.controller.store, actor, window)
        parts = self._matched_report_parts(actor, window, turn)
        if turn and parts and turn.get("status") == "completed" and parts[1].get("status") == "failed":
            return {"turn_id": str(turn["turn_id"]), "todo_id": str(parts[0]["todo_id"])}
        return None

    def _cancellable_failed_window(self, actor: dict[str, object], window: dict[str, object]) -> dict[str, str] | None:
        """Identify a terminal failed report attempt that owner cancel may retire."""
        known = self._known_generation_failure(actor, window)
        if known is not None:
            return known
        from .steward_turn import report_turn

        if not self._has_attempt_source_only(attempt_directory(self.root, window)):
            return None
        turn = report_turn(self.controller.store, actor, window)
        parts = self._matched_report_parts(actor, window, turn)
        if not turn or not parts:
            return None
        request, native = parts
        failure = as_mapping(native.get("host_failure"))
        todo_id = request.get("todo_id")
        if (
            turn.get("status") == "completed" and native.get("status") == "failed"
            and native.get("result_kind") == "host_failure" and failure.get("retryable") is False
            and native.get("effects") == {"host_invoked": True, "state_written": False, "quota_spent": False, "scheduler_acknowledged": False}
            and isinstance(todo_id, str) and bool(todo_id)
        ):
            return {"turn_id": str(turn["turn_id"]), "todo_id": todo_id}
        return None

    def _execution_failed(self, actor: dict[str, object], window: dict[str, object]) -> bool:
        """Observe the current attempt; this grants no cancellation or retry."""
        from .steward_turn import report_turn

        turn = report_turn(self.controller.store, actor, window)
        parts = self._matched_report_parts(actor, window, turn)
        return bool(
            turn and parts and (turn.get("status") in {"failed", "interrupted", "timed_out"} or parts[1].get("status") == "failed")
        )

    def _failed_without_generation(
        self, actor: dict[str, object], window: dict[str, object], *,
        generation_locked: bool = False, require_recovery_exhausted: bool = False,
    ) -> bool:
        """Read a terminal report failure under the original execution fence.

        The cadence owner holds the generation fence. This proves only that
        the old window has no report to return, with no claim about model/tool
        effects and no authorization to retry the old Turn.
        """
        from ...chat_store import _read_jsonl
        from ...control_plane.turn_driver.journal_store import load_turn_journal, turn_journal_path
        from ...control_plane.turn_driver.turn_journal_runtime import interpret_turn_journal_projection
        from .steward_turn import StewardTurnHost, report_turn

        store = cast(_ReportsStoreProtocol, self.controller.store)
        if not self._has_attempt_source_only(attempt_directory(self.root, window)):
            return False
        sid = str(actor["session_id"])
        turn = report_turn(store, actor, window)
        if not turn or turn.get("status") != "completed" or not turn.get("upstream_turn_id"):
            return False
        turn_id = str(turn["turn_id"])
        with try_exclusive_file_lock(
            store.turn_execution_lock_path(sid, turn_id), operation="observe_failed_report_window", preserve_holder=True,
        ) as ownership:
            session = store.load_session(sid)
            if (
                ownership is None or not session or session.get("active_turn_id")
                or store.queued_turns(sid, strict=True, include_reserved=True)
                or report_turn(store, actor, window) != turn
            ):
                return False
            parts = self._matched_report_parts(actor, window, turn)
            if not parts:
                return False
            request, result = parts
            receipt = as_mapping(result.get("receipt"))
            failure = as_mapping(result.get("host_failure"))
            operation = "report." + str(window["window_id"])
            if actor.get("schema_version") == "loopx_managed_session_actor_v0":
                managed_req = as_mapping(ManagedSessionTurn(self.controller)._request(sid, str(actor["agent_id"]), operation))
                operation = str(managed_req["turn_instance_id"])
            allowed_instances = (
                {operation + ".quota-recovery", operation + ".timeout-recovery"}
                if require_recovery_exhausted
                else {operation, operation + ".quota-recovery", operation + ".timeout-recovery"}
            )
            if (
                turn.get("origin") != "native_loopx" or turn.get("loopx_execution") is not True
                or turn.get("session_id") != sid
                or request.get("turn_instance_id") not in allowed_instances or not request.get("todo_id")
                or result.get("status") != "failed" or result.get("result_kind") != "host_failure"
                or failure.get("retryable") is not False or receipt.get("ok") is not True
                or receipt.get("failed_phase") != "host_execute" or receipt.get("completed_phases") != []
                or result.get("effects") != {
                    "host_invoked": True, "state_written": False,
                    "quota_spent": False, "scheduler_acknowledged": False,
                }
            ):
                return False
            events = _read_jsonl(store._event_path(sid, turn_id), raise_on_error=True, strict=True)
            results = [event["payload"] for event in events if event["kind"] == "native_loopx.result"]
            if results != [dict(result)]:
                return False
            goal_id, agent_id = str(actor["goal_id"]), str(actor["agent_id"])
            key = str(result["resume_turn_key"])
            path = turn_journal_path(self.root, goal_id=goal_id, turn_key=key)
            with try_exclusive_file_lock(path, operation="observe_failed_report_turn", preserve_holder=True) as locked:
                journal = as_optional_dict(load_turn_journal(path)) if locked is not None else None
                if (
                    not journal or journal.get("status") != "failed"
                    or journal.get("receipt") != receipt or journal.get("host_failure") != failure
                ):
                    return False
                plan = as_mapping(journal.get("plan"))
                settlement_plan = as_mapping(as_mapping(plan.get("transaction")).get("settlement_plan"))
                identity = as_mapping(settlement_plan.get("identity"))
                if identity.get("todo_id") != request["todo_id"] or identity.get("turn_instance_id") != request["turn_instance_id"]:
                    return False
                inspection = interpret_turn_journal_projection(journal, goal_id=goal_id, agent_id=agent_id, turn_key=key)
                return bool(
                    inspection.get("journal_consistent") is True
                    and inspection.get("completed_phases") == []
                    and StewardTurnHost(self.controller).report_window_failure_verified(turn, window, generation_locked=generation_locked)
                )

    def _cancel_failed_window(
        self, actor: dict[str, object], window: dict[str, object], failure: dict[str, str], *, cadence_locked: bool = False,
    ) -> None:
        from ..periodic_report.cadence_journal import _retire_failed_cadence_window_locked, retire_failed_cadence_window

        if self._cancellable_failed_window(actor, window) != failure:
            raise ValueError("the original failed report changed before cancellation")
        native_command(
            self.controller,
            [
                "todo", "update", "--goal-id", str(actor["goal_id"]), "--todo-id", failure["todo_id"],
                "--agent-id", str(actor["agent_id"]), "--update-operation-id", "cancel.report." + str(window["window_id"]),
                "--status", "blocked", "--clear-resume-when", "--reason",
                "Owner cancelled a known failed report window; retain the failed task, costs and evidence, not completed or delivered.",
                "--execute",
            ],
        )
        retire = _retire_failed_cadence_window_locked if cadence_locked else retire_failed_cadence_window
        retire(
            runtime_root=self.root, goal_id=str(actor["goal_id"]),
            expected_window_id=str(window["window_id"]), source_receipt_id=failure["turn_id"],
        )

    def current_window(self, binding_id: str) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        subscription = self.subscription(binding_id)
        if subscription is None:
            return None, None
        actor = as_mapping(self.selected(binding_id)["actor"])
        journal = as_optional_dict(read_cadence_journal(runtime_root=self.root, goal_id=str(actor["goal_id"])))
        return subscription, as_dict(journal["window"]) if journal else None

    def _frontier(self, owner_key: str, actor: dict[str, object]) -> tuple[dict[str, object] | None, bool]:
        from .steward_monitor import StewardMonitor
        from .steward_turn import StewardTurnHost

        subscription = self.subscription(owner_key)
        if subscription is None or subscription.get("schedule") is None:
            return None, False
        actor_goal_id, actor_agent_id = str(actor["goal_id"]), str(actor["agent_id"])
        admitted = as_mapping(
            admit_cadence_window(
                runtime_root=self.root, goal_id=actor_goal_id, agent_id=actor_agent_id,
                subscription=subscription, now=datetime.now(timezone.utc),
                subscription_resolver=lambda: self.subscription(owner_key, expected_actor=actor),
                can_replace_failed_window=lambda win: self._failed_without_generation(actor, win, generation_locked=True),
                can_replace_exhausted_failed_window=lambda win: self._failed_without_generation(
                    actor, win, generation_locked=True, require_recovery_exhausted=True,
                ),
            )
        )
        if admitted.get("status") != "pending":
            return None, False
        window = as_dict(admitted["window"])
        window_id = str(window["window_id"])
        rows = [row for row in as_dict_list(StewardMonitor(self.controller).todos(actor)) if row.get("target_key") == window_id]
        if len(rows) > 1:
            raise ValueError("report window has conflicting native tasks")
        if not rows:
            native_command(
                self.controller,
                [
                    "todo", "add", "--goal-id", actor_goal_id, "--role", "agent", "--operation-id", window_id,
                    "--text", (
                        f"[P1] Author the configured original Session report ending {window['due_at']}; retain evidence and coverage gaps. Generation is separate from verified delivery."
                        if actor.get("schema_version") == "loopx_managed_session_actor_v0"
                        else f"[P1] Author the authorized portfolio report ending {window['due_at']}; retain counterevidence and coverage gaps. Generation is separate from verified private delivery."
                    ),
                    "--claimed-by", actor_agent_id, "--action-kind", "generate_periodic_report",
                    "--target-key", window_id, "--continuation-policy", "same_agent_non_delivery", "--execute",
                ],
            )
            rows = [row for row in as_dict_list(StewardMonitor(self.controller).todos(actor)) if row.get("target_key") == window_id]
        folder = attempt_directory(self.root, window)
        if len(rows) == 1 and (
            rows[0]["status"] == "done"
            or rows[0]["status"] == "open"
            and ((folder / "private-generation.json").exists() or StewardTurnHost(self.controller).has_recoverable_stopped_report(actor, window, rows[0]))
        ):
            StewardTurnHost(self.controller).recover_report_settlement(actor, window, rows[0])
            return None, False
        if len(rows) != 1 or rows[0]["status"] != "open":
            return None, False
        return self._submit(owner_key, actor, window, rows[0])

    def freeze(self, request: Mapping[str, object], candidate: dict[str, object]) -> dict[str, object]:
        _, owner_key = report_service(self.controller, as_mapping(request["actor"]))
        _, window = self.current_window(owner_key)
        if not window or window["window_id"] != request["report_window_id"]:
            raise ValueError("the original report was paused, cancelled or changed")
        response = candidate.pop("editorial_response", None)
        if not isinstance(response, dict):
            raise ValueError("the original reporting Agent returned no editorial response")
        return as_dict(freeze_private_report(root=self.root, window=window, response=response, require_current_window=True))


class StewardReports(_StewardReportsBase):
    def selected(self, binding_id: str) -> dict[str, object]:
        session = as_dict(self.managed._session(binding_id, None))
        saved = as_dict(session["steward_context"])
        selected = self.bindings.resolve(
            binding_id=binding_id, session=session, include_observation=True, origin="web",
            source_ref=str(saved["source_ref"]), sender_ref=str(saved["operator_ref"]),
            private_human_message=False, session_context=saved,
        )
        actor = as_optional_dict(as_mapping(selected["binding"]).get("managed_actor"))
        if not actor or actor.get("session_id") != session["session_id"]:
            raise ValueError("prepare the original steward before configuring reports")
        return {**selected, "actor": actor, "session": session}

    def subscription(
        self, binding_id: str, *, expected_revision: str | None = None, expected_actor: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        selected = self.selected(binding_id)
        actor, binding = as_mapping(selected["actor"]), as_mapping(selected["binding"])
        if expected_actor is not None and actor != expected_actor:
            return None
        goal = load_goal_from_registry(cast(Path, self.controller.registry_path), str(actor["goal_id"]))
        subscription = as_optional_dict(resolve_active_goal_periodic_report_subscription(goal))
        if (
            subscription is None or subscription.get("profile_preset") != PRESET
            or subscription.get("route_ref") != self.route_ref(binding, actor)
            or expected_revision is not None and subscription.get("effective_revision") != expected_revision
        ):
            return None
        return subscription

    def configure(
        self, binding_id: str, *, operation: str, schedule: dict[str, object] | None = None,
        expected_plan_id: str | None = None, execute: bool = False,
    ) -> dict[str, object]:
        from ...configure_goal import configure_goal

        selected = self.selected(binding_id)
        binding, actor, session = as_dict(selected["binding"]), as_dict(selected["actor"]), as_dict(selected["session"])
        provider = cast(_ReportRouteProviderProtocol | None, getattr(self.controller, "steward_report_provider", None))
        if provider is None:
            raise ValueError("the original private report provider is unavailable")
        route = provider.route_proof(binding, actor)
        actor_goal_id = str(actor["goal_id"])
        with self.controller.lock, exclusive_file_lock(self.bindings.path, operation="configure_steward_report"):
            current = self.selected(binding_id)
            if current["binding"] != binding:
                raise ValueError("the original steward authorization changed")
            goal = load_goal_from_registry(cast(Path, self.controller.registry_path), actor_goal_id)
            before = as_optional_dict(configuration_summary(goal or {}))
            effective_schedule = schedule if operation == "enable" else as_optional_dict((before or {}).get("schedule"))
            if effective_schedule is None:
                raise ValueError("select the daily or weekly report time and first date")
            proposed = as_dict(normalize_configuration({
                "enabled": operation == "enable", "profile_preset": PRESET,
                "route_ref": self.route_ref(binding, actor),
                "timezone": effective_schedule["timezone"], "schedule": effective_schedule,
            }))
            active_proposed = as_optional_dict(resolve_active_goal_periodic_report_subscription(
                {**(goal or {}), "control_plane": {"periodic_report": {**proposed, "enabled": True}}}
            ))
            journal = as_optional_dict(read_cadence_journal(runtime_root=self.root, goal_id=actor_goal_id))
            pending: dict[str, object] | None = None
            failure: dict[str, str] | None = None
            if journal:
                journal_window = as_dict(journal["window"])
                failure = self._cancellable_failed_window(actor, journal_window)
                pending = {
                    "subscription_revision": journal_window["subscription_revision"],
                    "prepared": (attempt_directory(self.root, journal_window) / "editorial-request.json").exists(),
                    "verified_publication": self._published_window(journal_window),
                    "retired": bool(journal.get("retirement")),
                    "failure_ref": failure["turn_id"] if failure else None,
                }
            plan = as_dict(self.bindings._core("collaboration.steward.report.plan", {
                **current, "workspace_path": str(self.managed._workspace(session)),
                "goal": self.managed._goal(actor_goal_id), "goal_active": bool(active_proposed),
                "pending_report": pending,
                "proposed_subscription_revision": active_proposed["effective_revision"] if active_proposed else None,
                "configuration": before, "proposed": proposed, "operation": operation, "route": route,
            }))
            if plan.get("gate") == "report_window_pending":
                raise PendingStewardReportError("the original frozen report still requires verified private delivery")
            if not execute:
                return {"ok": True, "applied": False, "plan": plan}
            if expected_plan_id != plan["plan_id"]:
                raise ValueError("refresh the original report preview before applying")
            result = as_mapping(configure_goal(
                registry_path=cast(Path, self.controller.registry_path), goal_id=actor_goal_id,
                periodic_report_configuration=as_mapping(plan["configuration"]), execute=True,
            ))
            if result.get("ok") is not True:
                raise ValueError("native report configuration was not committed")
            if configuration_summary(load_goal_from_registry(cast(Path, self.controller.registry_path), actor_goal_id) or {}) != proposed:
                raise ValueError("native report configuration readback changed")
            if plan["retires_failed_window"] and journal and failure is not None:
                self._cancel_failed_window(actor, as_dict(journal["window"]), failure)
            next_check = getattr(self.controller, "_steward_monitor_next_check", None)
            if isinstance(next_check, dict):
                next_check.pop(binding_id, None)
            receipt: dict[str, object] = {
                "ok": True, "applied": True, "plan": plan, "report": self.health(binding_id),
                "model_invoked": False, "external_writes_performed": False,
            }
        if operation == "cancel":
            # Stop only this original report, outside the configuration fence;
            # unrelated user/monitor work is never interrupted by this setting.
            actor_session_id = str(actor["session_id"])
            current_session = self.controller.store.load_session(actor_session_id)
            raw_turn_id = (current_session or {}).get("active_turn_id")
            turn_id = str(raw_turn_id) if isinstance(raw_turn_id, str) and raw_turn_id else None
            turn = self.controller.store.load_turn(actor_session_id, turn_id) if turn_id else None
            request = as_mapping((turn or {}).get("loopx_request"))
            if turn_id and request.get("work_kind") == "report" and request.get("actor") == actor:
                try:
                    interrupt_ctrl = cast(_InterruptControllerProtocol, self.controller)
                    stopped = as_mapping(interrupt_ctrl.interrupt_turn(session_id=actor_session_id, turn_id=turn_id))
                    receipt["stop_status"] = stopped.get("status", "unconfirmed")
                except (OSError, ValueError, RuntimeError):
                    receipt["stop_status"] = "unconfirmed"
        return receipt

    def delivery_current(self, binding_id: str, window: dict[str, object]) -> bool:
        """Recheck the exact original read grant before every outbound part."""
        from ...chat_manager_context import manager_authorization_scope_id_for_registry
        from ..periodic_report.pending_intent import _canonical_digest

        subscription, current = self.current_window(binding_id)
        if subscription is None or subscription["effective_revision"] != window["subscription_revision"] or current != window:
            return False
        selected = self.selected(binding_id)
        session = as_dict(selected["session"])
        folder = attempt_directory(self.root, window)
        expected = as_mapping(json.loads((folder / "editorial-request.json").read_text(encoding="utf-8")))
        source = as_mapping(json.loads((folder / "source.json").read_text(encoding="utf-8")))
        frozen_ids = source.get("goal_ids")
        current_ids = self.bindings.steward_scope(session)
        if (
            expected.get("request_digest") != _canonical_digest({k: v for k, v in expected.items() if k != "request_digest"})
            or expected.get("source_artifact_sha256") != _canonical_digest(dict(source))
            or not isinstance(frozen_ids, list) or not all(isinstance(value, str) for value in frozen_ids)
            or current_ids is None or not set(frozen_ids).issubset(current_ids)
        ):
            return False
        typed_frozen_ids = [str(value) for value in frozen_ids]

        def scope_id(ids: list[str]) -> str:
            return str(
                manager_authorization_scope_id_for_registry(
                    cast(Path, self.controller.registry_path), ids, runtime_root=self.root, channel_id=session["channel_id"],
                )
            )

        if "authorization_goal_ids" in expected:
            authority_ids = expected["authorization_goal_ids"]
            if (
                not isinstance(authority_ids, list)
                or any(not isinstance(value, str) or not value for value in authority_ids)
                or not set(typed_frozen_ids).issubset(authority_ids)
                or not set(authority_ids).issubset(current_ids)
            ):
                return False
            return scope_id([str(value) for value in authority_ids]) == expected["authorization_scope_id"]
        # Old requests froze the whole authorization hash, but only recorded
        # readable Goals. Full-coverage history can still tolerate scope growth.
        # Partial history without an identity witness requires the entire live
        # scope to match exactly; never infer missing identities or rewrite it.
        return scope_id(typed_frozen_ids) == expected["authorization_scope_id"] or scope_id(current_ids) == expected["authorization_scope_id"]

    def health(self, binding_id: str) -> dict[str, object]:
        """Content-free metadata, never a source/provider verification receipt."""
        try:
            binding = next(row for row in as_dict_list(self.bindings.read().get("bindings")) if row.get("binding_id") == binding_id)
            actor = as_optional_dict(binding.get("managed_actor"))
            if not actor:
                return {"available": True, "enabled": False, "configured": False}
            actor_goal_id = str(actor["goal_id"])
            goal = load_goal_from_registry(cast(Path, self.controller.registry_path), actor_goal_id)
            config = as_optional_dict(configuration_summary(goal or {}))
            if not config or config.get("profile_preset") != PRESET:
                return {"available": True, "enabled": False, "configured": False}
            journal = as_optional_dict(read_cadence_journal(runtime_root=self.root, goal_id=actor_goal_id))
            generation: object = None
            delivery: object = None
            if journal:
                journal_window = as_dict(journal["window"])
                folder = attempt_directory(self.root, journal_window)
                for name in ("private-generation.json", "private-delivery.json"):
                    path = folder / name
                    if path.is_file():
                        value = as_mapping(json.loads(path.read_text(encoding="utf-8")))
                        generation = value["generation_id"] if name == "private-generation.json" else generation
                        if name != "private-generation.json":
                            delivery = value.get("delivery_status", "pending")
                if journal.get("retirement"):
                    delivery = "generation_cancelled"
                elif self._known_generation_failure(actor, journal_window):
                    delivery = "generation_failed"
            active = resolve_active_goal_periodic_report_subscription(goal)
            schedule_cfg, next_due = self._build_health_schedule(config)
            return {
                "available": True, "configured": True, "enabled": bool(active), "schedule": schedule_cfg,
                "next_due_at": next_due,
                "retained_window_due_at": as_mapping(journal["window"])["due_at"] if journal else None,
                "generation_id": generation, "delivery_status": delivery or "not_generated",
                "execution_failed": bool(journal and self._execution_failed(actor, as_dict(journal["window"]))),
                "publication_verified": bool(journal and self._published_window(as_dict(journal["window"]))),
                "destination": "original_owner_private_chat_full_body",
            }
        except (OSError, ValueError, TypeError, KeyError, StopIteration):
            return {"available": False, "error_code": "steward_report_unavailable"}

    def frontier(self, binding: dict[str, object]) -> tuple[dict[str, object] | None, bool]:
        """Called only by the original host pump after its idle/cache checks."""
        return self._frontier(str(binding["binding_id"]), as_dict(binding["managed_actor"]))

    def _submit(
        self, owner_key: str, actor: dict[str, object], window: dict[str, object], todo: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        from .steward_turn import StewardTurnHost

        folder = attempt_directory(self.root, window)
        validator = [
            sys.executable, "-c",
            "from pathlib import Path; from loopx.capabilities.periodic_report.private_generation import validate_private_generation; "
            + f"validate_private_generation(Path({str(folder)!r}))",
        ]
        return StewardTurnHost(self.controller).submit(
            owner_key, turn_instance_id="report." + str(window["window_id"]),
            todo_id=str(todo["todo_id"]), work_kind="report", report_window=window, validation_command=validator,
        )

    def prepare(self, request: Mapping[str, object], adapter: object, *, authorization_scope_id: str) -> str:
        from ..manager_context.inspection import TOOL_NAME

        request_actor = as_mapping(request["actor"])
        _, window = self.current_window(str(request_actor["goal_id"]).removeprefix("steward-agent-"))
        if not window or window["window_id"] != request["report_window_id"]:
            raise ValueError("the admitted original report window is unavailable")
        session_obj = getattr(adapter, "session", None)
        reader_raw = getattr(session_obj, "read_tool_handler", None) if session_obj is not None else getattr(adapter, "_steward_report_reader", None)
        if not callable(reader_raw) or not authorization_scope_id:
            raise ValueError("the original scoped report reader is unavailable")
        reader = cast(Callable[[str, dict[str, object]], dict[str, object]], reader_raw)
        session = self.controller.store.load_session(str(request_actor["session_id"]))
        if session is None:
            raise ValueError("the original steward Session is unavailable")
        from ...chat_manager_context import manager_authorization_scope_id_for_registry

        authority_ids = self.bindings.steward_scope(session)
        if (
            authority_ids is None
            or manager_authorization_scope_id_for_registry(
                cast(Path, self.controller.registry_path), authority_ids,
                runtime_root=self.root, channel_id=session["channel_id"],
            ) != authorization_scope_id
        ):
            raise ValueError("the original report authorization changed before collection")
        from ...chat_manager import manager_reporting_text

        reporting = self._read_reporting_rules(Path(self.managed._workspace(session)) / "REPORTING.md", manager_reporting_text)
        frozen = prepare_private_report(
            root=self.root, window=window, read=lambda query: reader(TOOL_NAME, query),
            authorization_scope_id=authorization_scope_id, reporting=reporting, authorization_goal_ids=authority_ids,
        )
        return str(report_prompt(frozen, attempt_directory(self.root, window)))


def report_service(
    controller: StewardControllerProtocol, actor: Mapping[str, object],
) -> tuple[_StewardReportsBase, str]:
    """Select the existing source/return adapter, never another execution actor."""
    if actor.get("schema_version") == "loopx_managed_session_actor_v0":
        return ManagedSessionReports(controller), str(actor["session_id"])
    return StewardReports(controller), str(actor["goal_id"]).removeprefix("steward-agent-")


@contextmanager
def session_report_configuration_guard(
    controller: StewardControllerProtocol, goal_id: str, proposed: Mapping[str, object] | None,
) -> Iterator[Callable[[], None] | None]:
    """Keep the existing settings transaction and native window in one ordering.

    Preparing source artifacts takes the same Goal-local calendar lock. A
    changed plan cannot strand a frozen undelivered report. Clearing a known
    failed generation retires only that attempt after a verified settings apply;
    previews and pauses retain it. Other destinations keep their old path.
    """
    from ..periodic_report.cadence_journal import cadence_journal_path
    from ..periodic_report.goal_configuration import managed_session_report_destination

    reg_path = cast(Path, controller.registry_path)
    current = as_dict(configuration_summary(load_goal_from_registry(reg_path, goal_id) or {}) or {})
    if (
        not managed_session_report_destination(current)
        and not managed_session_report_destination(dict(proposed or {}))
        and (current or proposed is not None)
    ):
        yield None
        return
    with exclusive_file_lock(cadence_journal_path(controller.coordination_runtime_root, goal_id), operation="configure_session_report"):
        current = as_dict(configuration_summary(load_goal_from_registry(reg_path, goal_id) or {}) or {})
        destination = managed_session_report_destination(dict(proposed or {}))
        if proposed and proposed.get("enabled") and destination:
            actor = as_mapping(ManagedSessionReports(controller).selected(str(destination))["actor"])
            if actor["goal_id"] != goal_id:
                raise ValueError("select the original managed Agent Session in this Goal")
            if proposed.get("profile_preset") == "session-task":
                from .session_calendar_tasks import SessionCalendarTasks

                cast(_CalendarSourceTaskProtocol, SessionCalendarTasks(controller)).source_task(
                    dict(actor), as_dict(proposed.get("task") or {}),
                )
        journal = as_optional_dict(read_cadence_journal(runtime_root=controller.coordination_runtime_root, goal_id=goal_id))
        cancellation: tuple[dict[str, object], dict[str, object], dict[str, str]] | None = None
        report_target: _WindowGuardTargetProtocol = ManagedSessionReports(controller)
        if journal and journal.get("publication") is None and not journal.get("retirement"):
            window = as_dict(journal["window"])
            profile_ref = as_mapping(window.get("profile_ref"))
            folder = attempt_directory(controller.coordination_runtime_root, window)
            if profile_ref.get("profile_id") == "session_task":
                from .session_calendar_tasks import SessionCalendarTasks

                task_reports = SessionCalendarTasks(controller)
                report_target = cast(_WindowGuardTargetProtocol, task_reports)
                cancellable_reader: Callable[[dict[str, object], dict[str, object]], dict[str, str] | None] = task_reports._known_generation_failure
            else:
                managed_reports = ManagedSessionReports(controller)
                report_target = managed_reports
                cancellable_reader = managed_reports._cancellable_failed_window
            if folder.exists() and not report_target._published_window(window):
                before = {k: v for k, v in current.items() if k != "enabled"}
                after = {k: v for k, v in (proposed or {}).items() if k != "enabled"}
                if before != after or not current and proposed is None:
                    frozen_actor = report_target._frozen_actor(window)
                    failure = cancellable_reader(frozen_actor, window) if frozen_actor else None
                    if (proposed is not None or not failure) and (current or proposed is not None):
                        if profile_ref.get("profile_id") == "session_task":
                            raise PendingStewardReportError("This calendar task is unfinished or its result is unconfirmed. Pause or restore its original plan; only a known failed independent validation can be cleared.")
                        raise PendingStewardReportError("The original frozen report still requires verified delivery. Pause or restore the same plan; clear a known failed generation before changing its schedule or conversation.")
                    if frozen_actor is not None and failure:
                        cancellation = frozen_actor, window, failure

        def after_verified_apply() -> None:
            assert cancellation is not None
            target_actor, target_window, target_failure = cancellation
            if configuration_summary(load_goal_from_registry(reg_path, goal_id) or {}) is not None:
                raise ValueError("the original report configuration was not cleared")
            if report_target._frozen_actor(target_window) != target_actor:
                raise ValueError("the original failed report actor changed before cancellation")
            report_target._cancel_failed_window(target_actor, target_window, target_failure, cadence_locked=True)

        yield after_verified_apply if cancellation else None


class ManagedSessionReports(_StewardReportsBase):
    """Local source and Core return adapter for the shared report/Turn lifecycle.

    An explicit Goal configuration pins one existing Agent Session. Its native
    calendar, generation Task, frozen artifacts and publication cursor remain
    the existing periodic-report owners. No private steward binding is needed.
    """

    def selected(self, session_id: str) -> dict[str, object]:
        from .steward_managed_mode import managed_session_identity

        session = self.controller.store.load_session(session_id)
        if not session or not session.get("managed_agent_id"):
            raise ValueError("select the original registered Agent Session")
        request = as_mapping(ManagedSessionTurn(self.controller)._request(session_id, str(session["managed_agent_id"]), "report-identity"))
        actor = as_dict(request["actor"])
        managed_session_identity(
            self.controller, goal_id=str(actor["goal_id"]), agent_id=str(actor["agent_id"]),
            workspace=Path(str(actor["workspace_path"])), session=session,
        )
        return {"session": session, "actor": actor}

    def subscription(
        self, session_id: str, *, expected_revision: str | None = None, expected_actor: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        actor = as_mapping(self.selected(session_id)["actor"])
        if expected_actor is not None and actor != expected_actor:
            return None
        goal = load_goal_from_registry(cast(Path, self.controller.registry_path), str(actor["goal_id"]))
        # Only an explicit Goal override can choose this exact Session; a
        # machine default cannot implicitly enroll another Goal/conversation.
        config = as_optional_dict(configuration_summary(goal or {}))
        if not config or config.get("profile_preset") != "weekly-progress" or config.get("route_ref") != "managed-session:" + session_id:
            return None
        subscription = as_optional_dict(resolve_active_goal_periodic_report_subscription(goal))
        if subscription and expected_revision is not None and subscription.get("effective_revision") != expected_revision:
            return None
        return subscription

    def _submit(
        self, owner_key: str, actor: dict[str, object], window: dict[str, object], todo: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        return ManagedSessionTurn(self.controller).submit(
            owner_key, agent_id=str(actor["agent_id"]), operation_id="report." + str(window["window_id"]),
            todo_id=str(todo["todo_id"]), report_window=window,
        )

    def frontier(self, session_id: str) -> tuple[dict[str, object] | None, bool]:
        from .steward_monitor import StewardMonitor
        from .steward_turn import StewardTurnHost

        actor = as_dict(self.selected(session_id)["actor"])
        subscription, window = self.current_window(session_id)
        if subscription is None:
            return None, False
        if window:
            window_id = str(window["window_id"])
            todo = next((row for row in as_dict_list(StewardMonitor(self.controller).todos(actor)) if row.get("target_key") == window_id), None)
            if todo and (
                (attempt_directory(self.root, window) / "private-generation.json").exists()
                or StewardTurnHost(self.controller).has_recoverable_stopped_report(actor, window, todo)
            ):
                StewardTurnHost(self.controller).recover_report_settlement(actor, window, todo)
                self.publish(session_id, window)
        return self._frontier(session_id, actor)

    def prepare(self, request: Mapping[str, object], adapter: object, *, authorization_scope_id: str | None = None) -> str:
        from ...control_plane.todos.list_readback import list_goal_todos
        from ..periodic_report.cadence_journal import cadence_journal_path
        from ..periodic_report.pending_intent import _canonical_digest
        from ..periodic_report.private_generation import prepare_session_report

        request_actor = as_mapping(request["actor"])
        actor = as_dict(self.selected(str(request_actor["session_id"]))["actor"])
        actor_session_id, actor_goal_id = str(actor["session_id"]), str(actor["goal_id"])
        _, window = self.current_window(actor_session_id)
        if actor != request_actor or not window or window["window_id"] != request["report_window_id"]:
            raise ValueError("the admitted original Session report changed")

        def read() -> dict[str, object]:
            reg_path = cast(Path, self.controller.registry_path)
            active = as_mapping(list_goal_todos(registry_path=reg_path, goal_id=actor_goal_id, runtime_root_arg=str(self.root), limit=None))
            history = as_mapping(list_goal_todos(registry_path=reg_path, goal_id=actor_goal_id, runtime_root_arg=str(self.root), role="agent", status="done", read_scope="completed_history", limit=None))
            rows = {str(row["todo_id"]): row for row in [*as_dict_list(active.get("todos")), *as_dict_list(history.get("todos"))]}
            source = {
                "goal_id": actor_goal_id, "observed_at": datetime.now(timezone.utc).isoformat(),
                "rows": [rows[key] for key in sorted(rows)], "complete_native_task_read": True,
                "artifact_contents_independently_read": False, "as_of_window_reconstruction": False,
            }
            return {**source, "source_revision": _canonical_digest(source)}

        from ...chat_manager import session_reporting_text

        reporting = self._read_reporting_rules(Path(str(actor["workspace_path"])) / "REPORTING.md", session_reporting_text)
        with exclusive_file_lock(cadence_journal_path(self.root, actor_goal_id), operation="freeze_session_report_source"):
            if self.subscription(actor_session_id, expected_revision=str(window["subscription_revision"])) is None:
                raise ValueError("the original report configuration changed before collection")
            frozen = prepare_session_report(root=self.root, window=window, actor=actor, read=read, reporting=reporting)
        return str(report_prompt(frozen, attempt_directory(self.root, window)))

    def _frozen_actor(self, window: dict[str, object]) -> dict[str, object] | None:
        from ..periodic_report.pending_intent import _canonical_digest

        path = attempt_directory(self.root, window) / "editorial-request.json"
        if not path.is_file():
            return None
        frozen = as_mapping(json.loads(path.read_text(encoding="utf-8")))
        saved = frozen.get("managed_session_actor")
        if not isinstance(saved, dict):
            return None
        actor = as_dict(self.selected(str(saved.get("session_id")))["actor"])
        return (
            actor
            if (
                actor == saved and frozen.get("window") == window
                and actor["goal_id"] == window["goal_id"] and actor["agent_id"] == window["agent_id"]
                and frozen.get("request_digest") == _canonical_digest({k: v for k, v in frozen.items() if k != "request_digest"})
            )
            else None
        )

    def delivery_current(self, session_id: str, window: dict[str, object]) -> bool:
        subscription, current = self.current_window(session_id)
        return (
            subscription is not None and subscription["effective_revision"] == window["subscription_revision"]
            and current == window and self._frozen_actor(window) == self.selected(session_id)["actor"]
        )

    def publish(self, session_id: str, window: Mapping[str, object]) -> bool:
        """Read the original native commit, then publish/read back one Core message.

        Core's explicit message identity reconciles an unknown append; a lost
        publication-cursor response is recovered from that same full body.
        There is no external send and no second Chat/model Turn.
        """
        import hashlib

        from ...control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission, capture_first_party_host_goal_ref
        from ..periodic_report.cadence_journal import cadence_journal_path
        from ..periodic_report.incremental import commit_periodic_report_publication_cursor
        from ..periodic_report.private_generation import validate_private_generation
        from .steward_turn import report_turn

        typed_window = dict(window)
        actor = as_mapping(self.selected(session_id)["actor"])
        actor_goal_id = str(actor["goal_id"])
        turn = report_turn(self.controller.store, actor, typed_window)
        if not turn:
            return False
        request = as_mapping(turn.get("loopx_request"))
        window_id = str(typed_window["window_id"])
        if (
            request.get("actor") != actor or request.get("report_window_id") != window_id
            or request.get("report_subscription_revision") != typed_window["subscription_revision"]
        ):
            raise ValueError("the original report return changed identity")
        turn_id = str(turn["turn_id"])
        if not any(
            row.get("kind") == "native_loopx.result" and as_mapping(row.get("payload")).get("status") == "committed"
            for row in self.controller.store.events_after(session_id, turn_id, None)
        ):
            return False
        folder = attempt_directory(self.root, typed_window)
        reg_path = cast(Path, self.controller.registry_path)
        store = cast(_ReportsStoreProtocol, self.controller.store)
        with exclusive_file_lock(cadence_journal_path(self.root, actor_goal_id), operation="publish_session_report"):
            admission = FirstPartyHostGoalAdmission.for_plan(
                registry_path=reg_path, goal_id=actor_goal_id,
                planned_goal_ref=capture_first_party_host_goal_ref(registry_path=reg_path, goal_id=actor_goal_id),
            )
            with self.controller.lock, admission.launch_lifetime(operation="return_session_report"), exclusive_file_lock(
                folder / "delivery.lock", operation="return_session_report"
            ):
                generation = as_mapping(validate_private_generation(folder))
                if not self.delivery_current(session_id, typed_window):
                    return False
                body = (folder / "report.md").read_text(encoding="utf-8")
                config = as_optional_dict(configuration_summary(load_goal_from_registry(reg_path, actor_goal_id) or {}))
                if config and config.get("review_required") is True:
                    from ..periodic_report.review import request_report_review

                    request_report_review(
                        registry_path=reg_path, runtime_root=self.root, goal_id=actor_goal_id,
                        agent_id=str(actor["agent_id"]), session_id=session_id,
                        session_store=cast("ChatSessionStore", self.controller.store), execute=True,
                        bundle=json.loads((folder / "generation-bundle.json").read_text(encoding="utf-8")),
                    )
                message_id = "managed." + turn_id + ".completed"
                existing = next((row for row in store.messages(session_id) if row.get("message_id") == message_id), None)
                # A retained earlier partial/failed outer answer stays immutable.
                # Only the newly recovered native commit permits the full report.
                if existing and existing.get("text") != body:
                    message_id = "report." + window_id
                store.append_message(session_id, role="agent", text=body, turn_id=turn_id, message_id=message_id)
                readback = [row for row in store.messages(session_id) if row.get("message_id") == message_id]
                if (
                    len(readback) != 1 or readback[0].get("text") != body or readback[0].get("role") != "agent"
                    or readback[0].get("turn_id") != turn_id or not self.delivery_current(session_id, typed_window)
                ):
                    raise ValueError("the complete original report message did not verify")
                candidate = json.loads((folder / "publication-candidate.json").read_text(encoding="utf-8"))
                identity = [session_id, window_id, generation["generation_id"], message_id, generation["body_sha256"]]
                commit_periodic_report_publication_cursor(
                    runtime_root=self.root, candidate=candidate,
                    publication_id="session-report-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                    delivered_at=str(readback[0]["created_at"]), covered_until=str(typed_window["due_at"]),
                )
                return True

    def health(self, session_id: str) -> dict[str, object]:
        try:
            actor = as_mapping(self.selected(session_id)["actor"])
            actor_goal_id = str(actor["goal_id"])
            reg_path = cast(Path, self.controller.registry_path)
            goal = load_goal_from_registry(reg_path, actor_goal_id)
            config = as_optional_dict(configuration_summary(goal or {}))
            if not config or config.get("route_ref") != "managed-session:" + session_id:
                return {"available": True, "configured": False, "enabled": False}
            journal = as_optional_dict(read_cadence_journal(runtime_root=self.root, goal_id=actor_goal_id))
            window = as_dict(journal["window"]) if journal else None
            folder = attempt_directory(self.root, window) if window else None
            from .steward_turn import report_turn

            turn = report_turn(self.controller.store, actor, window) if window else None
            failed = bool(window and self._execution_failed(dict(actor), window))
            published = bool(window and self._published_window(window))
            returned: list[dict[str, object]] = []
            if published and turn and folder and (folder / "report.md").is_file():
                body = (folder / "report.md").read_text(encoding="utf-8")
                expected_turn_id = str(turn["turn_id"])
                returned = [
                    row for row in self.controller.store.messages(session_id)
                    if row.get("role") == "agent" and row.get("turn_id") == expected_turn_id and row.get("text") == body
                ]
            verified = published and len(returned) == 1
            schedule_cfg, next_due = self._build_health_schedule(config)
            return {
                "available": True, "configured": True, "enabled": bool(self.subscription(session_id)),
                "scheduled": bool(schedule_cfg), "schedule": schedule_cfg, "next_due_at": next_due,
                "retained_window_due_at": window["due_at"] if window else None,
                "generation_complete": bool(folder and (folder / "private-generation.json").exists()),
                "execution_failed": failed, "publication_verified": verified,
                "report_message_id": returned[0]["message_id"] if verified else None,
            }
        except (OSError, ValueError, KeyError, TypeError):
            return {"available": False, "error_code": "managed_session_report_unavailable"}
