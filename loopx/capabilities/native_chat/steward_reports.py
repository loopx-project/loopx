"""Calendar/report IO on the original Chat worker, using the native owners.

Subscription configuration belongs to configure-goal, window admission to
periodic-report, tasks/settlement to LoopX Turn, and private delivery to the
bound provider. This adapter creates no scheduler, executor or business store.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
import sys
from typing import Any

from ...agent_registry import load_goal_from_registry
from ...file_lock import exclusive_file_lock
from ..periodic_report.cadence import report_cadence_next_due_at
from ..periodic_report.cadence_journal import admit_cadence_window, cadence_intent, read_cadence_journal
from ..periodic_report.goal_configuration import configuration_summary, normalize_configuration
from ..periodic_report.machine_defaults import resolve_active_goal_periodic_report_subscription
from ..periodic_report.private_generation import (
    attempt_directory, freeze_private_report, prepare_private_report, report_prompt,
)
from .steward_commands import native_command
from .steward_managed_mode import StewardManagedMode

PRESET = "portfolio-progress"


class PendingStewardReportError(ValueError):
    pass


class StewardReports:
    def __init__(self, controller: Any) -> None:
        self.controller = controller
        self.bindings = controller.project_contexts.conversation_bindings
        self.managed = StewardManagedMode(controller, self.bindings)
        self.root = controller.coordination_runtime_root

    @staticmethod
    def route_ref(binding: dict[str, Any], actor: dict[str, Any]) -> str:
        return f"steward-private:{binding['binding_id']}:{actor['source_ref']}"

    def selected(self, binding_id: str) -> dict[str, Any]:
        session = self.managed._session(binding_id, None)
        saved = session["steward_context"]
        selected = self.bindings.resolve(binding_id=binding_id, session=session,
            include_observation=True, origin="web", source_ref=saved["source_ref"],
            sender_ref=saved["operator_ref"], private_human_message=False, session_context=saved)
        actor = selected["binding"].get("managed_actor")
        if not actor or actor["session_id"] != session["session_id"]:
            raise ValueError("prepare the original steward before configuring reports")
        return {**selected, "actor": actor, "session": session}

    def subscription(self, binding_id: str, *, expected_revision: str | None = None) -> dict[str, Any] | None:
        selected = self.selected(binding_id)
        actor, binding = selected["actor"], selected["binding"]
        goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
        subscription = resolve_active_goal_periodic_report_subscription(goal)
        if (subscription is None or subscription.get("profile_preset") != PRESET
                or subscription.get("route_ref") != self.route_ref(binding, actor)
                or expected_revision is not None and subscription["effective_revision"] != expected_revision):
            return None
        return subscription

    def _published_window(self, window: dict[str, Any]) -> bool:
        from ..periodic_report.incremental import read_periodic_report_publication_cursor
        from ..periodic_report.post_writeback_hook import evaluate_periodic_report_trigger_evaluation_intent
        cursor = read_periodic_report_publication_cursor(runtime_root=self.root,
            goal_id=window["goal_id"], agent_id=window["agent_id"])
        trigger = evaluate_periodic_report_trigger_evaluation_intent(cadence_intent(window))
        return bool(cursor and trigger["selected_trigger_id"] in cursor["covered_trigger_ids"])

    def _known_generation_failure(self, actor: dict[str, Any], window: dict[str, Any]) -> dict[str, str] | None:
        from .steward_turn import report_turn
        folder = attempt_directory(self.root, window)
        if (not (folder / "editorial-response.json").is_file()
                or (folder / "private-generation.json").exists() or (folder / "private-delivery.json").exists()):
            return None
        turn = report_turn(self.controller.store, actor, window)
        request = (turn or {}).get("loopx_request") or {}
        native = ((turn or {}).get("response") or {}).get("native_loopx") or {}
        if (turn and turn.get("status") == "completed" and native.get("status") == "failed"
                and request.get("actor") == actor and request.get("work_kind") == "report"
                and request.get("report_window_id") == window["window_id"]
                and request.get("report_subscription_revision") == window["subscription_revision"]):
            return {"turn_id": turn["turn_id"], "todo_id": request["todo_id"]}
        return None

    def _cancel_failed_window(self, actor: dict[str, Any], window: dict[str, Any],
                              failure: dict[str, str], *, cadence_locked: bool = False) -> None:
        from ..periodic_report.cadence_journal import (
            _retire_failed_cadence_window_locked, retire_failed_cadence_window,
        )
        if self._known_generation_failure(actor, window) != failure:
            raise ValueError("the original failed report changed before cancellation")
        native_command(self.controller, ["todo", "update", "--goal-id", actor["goal_id"],
            "--todo-id", failure["todo_id"], "--agent-id", actor["agent_id"],
            "--update-operation-id", "cancel.report." + window["window_id"], "--status", "blocked",
            "--clear-resume-when", "--reason",
            "Owner cancelled a known failed report window; retain the failed task, costs and evidence, not completed or delivered.", "--execute"])
        retire = _retire_failed_cadence_window_locked if cadence_locked else retire_failed_cadence_window
        retire(runtime_root=self.root, goal_id=actor["goal_id"],
            expected_window_id=window["window_id"], source_receipt_id=failure["turn_id"])

    def configure(self, binding_id: str, *, operation: str, schedule: dict[str, Any] | None = None,
                  expected_plan_id: str | None = None, execute: bool = False) -> dict[str, Any]:
        from ...configure_goal import configure_goal

        selected = self.selected(binding_id)
        binding, actor, session = selected["binding"], selected["actor"], selected["session"]
        provider = getattr(self.controller, "steward_report_provider", None)
        if provider is None:
            raise ValueError("the original private report provider is unavailable")
        route = provider.route_proof(binding, actor)
        with self.controller.lock, exclusive_file_lock(self.bindings.path, operation="configure_steward_report"):
            current = self.selected(binding_id)
            if current["binding"] != binding:
                raise ValueError("the original steward authorization changed")
            goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
            before = configuration_summary(goal)
            effective_schedule = schedule if operation == "enable" else (before or {}).get("schedule")
            if effective_schedule is None:
                raise ValueError("select the daily or weekly report time and first date")
            proposed = normalize_configuration({"enabled": operation == "enable", "profile_preset": PRESET,
                "route_ref": self.route_ref(binding, actor), "timezone": effective_schedule["timezone"],
                "schedule": effective_schedule})
            active_proposed = resolve_active_goal_periodic_report_subscription(
                {**goal, "control_plane": {"periodic_report": {**proposed, "enabled": True}}})
            journal = read_cadence_journal(runtime_root=self.root, goal_id=actor["goal_id"])
            pending = None
            failure = None
            if journal:
                failure = self._known_generation_failure(actor, journal["window"])
                pending = {"subscription_revision": journal["window"]["subscription_revision"],
                    "prepared": (attempt_directory(self.root, journal["window"]) / "editorial-request.json").exists(),
                    "verified_publication": self._published_window(journal["window"]),
                    "retired": bool(journal.get("retirement")),
                    "failure_ref": failure["turn_id"] if failure else None}
            plan = self.bindings._core("collaboration.steward.report.plan", {
                **current, "workspace_path": str(self.managed._workspace(session)),
                "goal": self.managed._goal(actor["goal_id"]),
                "goal_active": bool(active_proposed), "pending_report": pending,
                "proposed_subscription_revision": active_proposed["effective_revision"] if active_proposed else None,
                "configuration": before, "proposed": proposed, "operation": operation, "route": route})
            if plan.get("gate") == "report_window_pending":
                raise PendingStewardReportError("the original frozen report still requires verified private delivery")
            if not execute:
                return {"ok": True, "applied": False, "plan": plan}
            if expected_plan_id != plan["plan_id"]:
                raise ValueError("refresh the original report preview before applying")
            result = configure_goal(registry_path=self.controller.registry_path, goal_id=actor["goal_id"],
                periodic_report_configuration=plan["configuration"], execute=True)
            if result.get("ok") is not True:
                raise ValueError("native report configuration was not committed")
            if configuration_summary(load_goal_from_registry(self.controller.registry_path, actor["goal_id"])) != proposed:
                raise ValueError("native report configuration readback changed")
            if plan["retires_failed_window"]:
                self._cancel_failed_window(actor, journal["window"], failure)
            getattr(self.controller, "_steward_monitor_next_check", {}).pop(binding_id, None)
            receipt = {"ok": True, "applied": True, "plan": plan, "report": self.health(binding_id),
                "model_invoked": False, "external_writes_performed": False}
        if operation == "cancel":
            # Stop only this original report, outside the configuration fence;
            # unrelated user/monitor work is never interrupted by this setting.
            current_session = self.controller.store.load_session(actor["session_id"])
            turn_id = (current_session or {}).get("active_turn_id")
            turn = self.controller.store.load_turn(actor["session_id"], turn_id) if turn_id else None
            request = (turn or {}).get("loopx_request") or {}
            if request.get("work_kind") == "report" and request.get("actor") == actor:
                try:
                    stopped = self.controller.interrupt_turn(session_id=actor["session_id"], turn_id=turn_id)
                    receipt["stop_status"] = stopped.get("status", "unconfirmed")
                except (OSError, ValueError, RuntimeError):
                    receipt["stop_status"] = "unconfirmed"
        return receipt

    def delivery_current(self, binding_id: str, window: dict[str, Any]) -> bool:
        """Recheck the exact original read grant before every outbound part."""
        from ...chat_manager_context import manager_authorization_scope_id_for_registry
        from ..periodic_report.pending_intent import _canonical_digest
        if self.subscription(binding_id, expected_revision=window["subscription_revision"]) is None:
            return False
        selected = self.selected(binding_id)
        session = selected["session"]
        folder = attempt_directory(self.root, window)
        expected = json.loads((folder / "editorial-request.json").read_text(encoding="utf-8"))
        source = json.loads((folder / "source.json").read_text(encoding="utf-8"))
        frozen_ids = source.get("goal_ids")
        current_ids = self.bindings.steward_scope(session)
        if (expected.get("request_digest") != _canonical_digest(
                {k: v for k, v in expected.items() if k != "request_digest"})
                or expected.get("source_artifact_sha256") != _canonical_digest(source)
                or not isinstance(frozen_ids, list) or not all(isinstance(value, str) for value in frozen_ids)
                or current_ids is None or not set(frozen_ids).issubset(current_ids)):
            return False
        def scope_id(ids):
            return manager_authorization_scope_id_for_registry(self.controller.registry_path,
                ids, runtime_root=self.root, channel_id=session["channel_id"])
        if "authorization_goal_ids" in expected:
            authority_ids = expected["authorization_goal_ids"]
            if (not isinstance(authority_ids, list)
                    or any(not isinstance(value, str) or not value for value in authority_ids)
                    or not set(frozen_ids).issubset(authority_ids)
                    or not set(authority_ids).issubset(current_ids)):
                return False
            return scope_id(authority_ids) == expected["authorization_scope_id"]
        # Old requests froze the whole authorization hash, but only recorded
        # readable Goals. Full-coverage history can still tolerate scope growth.
        # Partial history without an identity witness requires the entire live
        # scope to match exactly; never infer missing identities or rewrite it.
        return (scope_id(frozen_ids) == expected["authorization_scope_id"]
            or scope_id(current_ids) == expected["authorization_scope_id"])

    def health(self, binding_id: str) -> dict[str, Any]:
        """Content-free metadata, never a source/provider verification receipt."""
        try:
            binding = next(row for row in self.bindings.read()["bindings"] if row["binding_id"] == binding_id)
            actor = binding.get("managed_actor")
            if not actor:
                return {"available": True, "enabled": False, "configured": False}
            goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
            config = configuration_summary(goal)
            if not config or config.get("profile_preset") != PRESET:
                return {"available": True, "enabled": False, "configured": False}
            journal = read_cadence_journal(runtime_root=self.root, goal_id=actor["goal_id"])
            generation, delivery = None, None
            if journal:
                folder = attempt_directory(self.root, journal["window"])
                for name in ["private-generation.json", "private-delivery.json"]:
                    path = folder / name
                    if path.is_file():
                        value = json.loads(path.read_text(encoding="utf-8"))
                        if name == "private-generation.json":
                            generation = value["generation_id"]
                        else:
                            delivery = value.get("delivery_status", "pending")
                if journal.get("retirement"):
                    delivery = "generation_cancelled"
                elif self._known_generation_failure(actor, journal["window"]):
                    delivery = "generation_failed"
            active = resolve_active_goal_periodic_report_subscription(goal)
            return {"available": True, "configured": True, "enabled": bool(active),
                "schedule": config.get("schedule"), "next_due_at": report_cadence_next_due_at(
                    config.get("schedule"), now=datetime.now(timezone.utc)) if config.get("enabled") else None,
                "retained_window_due_at": journal["window"]["due_at"] if journal else None,
                "generation_id": generation, "delivery_status": delivery or "not_generated",
                "publication_verified": bool(journal and self._published_window(journal["window"])),
                "destination": "original_owner_private_chat_full_body"}
        except (OSError, ValueError, TypeError, KeyError, StopIteration):
            return {"available": False, "error_code": "steward_report_unavailable"}

    def current_window(self, binding_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        subscription = self.subscription(binding_id)
        if subscription is None:
            return None, None
        actor = self.selected(binding_id)["actor"]
        journal = read_cadence_journal(runtime_root=self.root, goal_id=actor["goal_id"])
        return subscription, journal["window"] if journal else None

    def frontier(self, binding: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        """Called only by the original host pump after its idle/cache checks."""
        return self._frontier(binding["binding_id"], binding["managed_actor"])

    def _submit(self, owner_key: str, actor: dict[str, Any], window: dict[str, Any], todo: dict[str, Any]):
        from .steward_turn import StewardTurnHost
        folder = attempt_directory(self.root, window)
        validator = [sys.executable, "-c", "from pathlib import Path; from loopx.capabilities.periodic_report.private_generation import validate_private_generation; "
            + f"validate_private_generation(Path({str(folder)!r}))"]
        return StewardTurnHost(self.controller).submit(owner_key,
            turn_instance_id="report." + window["window_id"], todo_id=todo["todo_id"],
            work_kind="report", report_window=window, validation_command=validator)

    def _frontier(self, owner_key: str, actor: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        from .steward_monitor import StewardMonitor
        from .steward_turn import StewardTurnHost

        subscription = self.subscription(owner_key)
        if subscription is None or subscription.get("schedule") is None:
            return None, False
        admitted = admit_cadence_window(runtime_root=self.root, goal_id=actor["goal_id"],
            agent_id=actor["agent_id"], subscription=subscription, now=datetime.now(timezone.utc),
            subscription_resolver=lambda: self.subscription(owner_key))
        if admitted["status"] != "pending":
            return None, False
        window = admitted["window"]
        rows = [row for row in StewardMonitor(self.controller).todos(actor) if row.get("target_key") == window["window_id"]]
        if len(rows) > 1:
            raise ValueError("report window has conflicting native tasks")
        if not rows:
            native_command(self.controller, ["todo", "add", "--goal-id", actor["goal_id"],
                "--role", "agent", "--operation-id", window["window_id"],
                "--text", (f"[P1] Author the configured original Session report ending {window['due_at']}; retain evidence and coverage gaps. Generation is separate from verified delivery."
                    if actor.get("schema_version") == "loopx_managed_session_actor_v0" else
                    f"[P1] Author the authorized portfolio report ending {window['due_at']}; retain counterevidence and coverage gaps. Generation is separate from verified private delivery."),
                "--claimed-by", actor["agent_id"], "--action-kind", "generate_periodic_report",
                "--target-key", window["window_id"], "--continuation-policy", "same_agent_non_delivery",
                "--execute"])
            rows = [row for row in StewardMonitor(self.controller).todos(actor) if row.get("target_key") == window["window_id"]]
        folder = attempt_directory(self.root, window)
        if len(rows) == 1 and (rows[0]["status"] == "done"
                or rows[0]["status"] == "open" and (folder / "private-generation.json").exists()):
            StewardTurnHost(self.controller).recover_report_settlement(actor, window, rows[0])
            return None, False
        if len(rows) != 1 or rows[0]["status"] != "open":
            return None, False
        return self._submit(owner_key, actor, window, rows[0])

    def prepare(self, request: dict[str, Any], adapter: Any, *, authorization_scope_id: str) -> str:
        from ..manager_context.inspection import TOOL_NAME
        _, window = self.current_window(request["actor"]["goal_id"].removeprefix("steward-agent-"))
        if not window or window["window_id"] != request["report_window_id"]:
            raise ValueError("the admitted original report window is unavailable")
        reader = getattr(adapter.session, "read_tool_handler", None)
        if reader is None or not authorization_scope_id:
            raise ValueError("the original scoped report reader is unavailable")
        session = self.controller.store.load_session(request["actor"]["session_id"])
        from ...chat_manager_context import manager_authorization_scope_id_for_registry
        authority_ids = self.bindings.steward_scope(session)
        if (authority_ids is None or manager_authorization_scope_id_for_registry(
                self.controller.registry_path, authority_ids, runtime_root=self.root,
                channel_id=session["channel_id"]) != authorization_scope_id):
            raise ValueError("the original report authorization changed before collection")
        rules = self.managed._workspace(session) / "REPORTING.md"
        if rules.is_symlink():
            raise ValueError("the original reporting criteria changed source")
        reporting = rules.read_text(encoding="utf-8") if rules.exists() else "Use the existing overview-to-depth report contract and preserve all coverage gaps."
        frozen = prepare_private_report(root=self.root, window=window, read=lambda query: reader(TOOL_NAME, query),
            authorization_scope_id=authorization_scope_id, reporting=reporting,
            authorization_goal_ids=authority_ids)
        return report_prompt(frozen, attempt_directory(self.root, window))

    def freeze(self, request: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        _, owner_key = report_service(self.controller, request["actor"])
        _, window = self.current_window(owner_key)
        if not window or window["window_id"] != request["report_window_id"]:
            raise ValueError("the original report was paused, cancelled or changed")
        response = candidate.pop("editorial_response", None)
        if not isinstance(response, dict):
            raise ValueError("the original reporting Agent returned no editorial response")
        return freeze_private_report(root=self.root, window=window, response=response)


def report_service(controller: Any, actor: dict[str, Any]):
    """Select the existing source/return adapter, never another execution actor."""
    if actor.get("schema_version") == "loopx_managed_session_actor_v0":
        return ManagedSessionReports(controller), actor["session_id"]
    return StewardReports(controller), actor["goal_id"].removeprefix("steward-agent-")


@contextmanager
def session_report_configuration_guard(controller: Any, goal_id: str, proposed: dict[str, Any] | None):
    """Keep the existing settings transaction and native window in one ordering.

    Preparing source artifacts takes the same Goal-local calendar lock. A
    changed plan cannot strand a frozen undelivered report. Clearing a known
    failed generation retires only that attempt after a verified settings apply;
    previews and pauses retain it. Other destinations keep their old path.
    """
    from ..periodic_report.goal_configuration import managed_session_report_destination
    from ..periodic_report.cadence_journal import cadence_journal_path
    current = configuration_summary(load_goal_from_registry(controller.registry_path, goal_id) or {}) or {}
    if (not managed_session_report_destination(current) and not managed_session_report_destination(proposed or {})
            and (current or proposed is not None)):
        yield
        return
    with exclusive_file_lock(cadence_journal_path(controller.coordination_runtime_root, goal_id),
                            operation="configure_session_report"):
        current = configuration_summary(load_goal_from_registry(controller.registry_path, goal_id) or {}) or {}
        destination = managed_session_report_destination(proposed or {})
        if proposed and proposed.get("enabled") and destination:
            actor = ManagedSessionReports(controller).selected(destination)["actor"]
            if actor["goal_id"] != goal_id:
                raise ValueError("select the original managed Agent Session in this Goal")
            if proposed.get("profile_preset") == "session-task":
                from .session_calendar_tasks import SessionCalendarTasks
                SessionCalendarTasks(controller).source_task(actor, proposed.get("task") or {})
        journal = read_cadence_journal(runtime_root=controller.coordination_runtime_root, goal_id=goal_id)
        cancellation = None
        if journal and journal["publication"] is None and not journal.get("retirement"):
            window = journal["window"]
            reports = ManagedSessionReports(controller)
            folder = attempt_directory(controller.coordination_runtime_root, window)
            if window["profile_ref"]["profile_id"] == "session_task":
                from .session_calendar_tasks import SessionCalendarTasks
                reports = SessionCalendarTasks(controller)
            if folder.exists() and not reports._published_window(window):
                before = {k: v for k, v in current.items() if k != "enabled"}
                after = {k: v for k, v in (proposed or {}).items() if k != "enabled"}
                if before != after or not current and proposed is None:
                    actor = reports._frozen_actor(window)
                    failure = reports._known_generation_failure(actor, window) if actor else None
                    if (proposed is not None or not failure) and (current or proposed is not None):
                        if window["profile_ref"]["profile_id"] == "session_task":
                            raise PendingStewardReportError("This calendar task is unfinished or its result is unconfirmed. Pause or restore its original plan; only a known failed independent validation can be cleared.")
                        raise PendingStewardReportError("The original frozen report still requires verified delivery. Pause or restore the same plan; clear a known failed generation before changing its schedule or conversation.")
                    if failure:
                        cancellation = actor, window, failure
        def after_verified_apply():
            if configuration_summary(load_goal_from_registry(controller.registry_path, goal_id)) is not None:
                raise ValueError("the original report configuration was not cleared")
            if reports._frozen_actor(cancellation[1]) != cancellation[0]:
                raise ValueError("the original failed report actor changed before cancellation")
            reports._cancel_failed_window(*cancellation, cadence_locked=True)
        yield after_verified_apply if cancellation else None


class ManagedSessionReports(StewardReports):
    """Local source and Core return adapter for the shared report/Turn lifecycle.

    An explicit Goal configuration pins one existing Agent Session. Its native
    calendar, generation Task, frozen artifacts and publication cursor remain
    the existing periodic-report owners. No private steward binding is needed.
    """
    def selected(self, session_id: str) -> dict[str, Any]:
        from pathlib import Path
        from .steward_managed_mode import ManagedSessionTurn, managed_session_identity
        session = self.controller.store.load_session(session_id)
        if not session or not session.get("managed_agent_id"):
            raise ValueError("select the original registered Agent Session")
        request = ManagedSessionTurn(self.controller)._request(session_id, session["managed_agent_id"], "report-identity")
        actor = request["actor"]
        managed_session_identity(self.controller, goal_id=actor["goal_id"], agent_id=actor["agent_id"],
            workspace=Path(actor["workspace_path"]), session=session)
        return {"session": session, "actor": actor}

    def subscription(self, session_id: str, *, expected_revision: str | None = None) -> dict[str, Any] | None:
        actor = self.selected(session_id)["actor"]
        goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
        # Only an explicit Goal override can choose this exact Session; a
        # machine default cannot implicitly enroll another Goal/conversation.
        config = configuration_summary(goal)
        if not config or config.get("profile_preset") != "weekly-progress" or config.get("route_ref") != "managed-session:" + session_id:
            return None
        subscription = resolve_active_goal_periodic_report_subscription(goal)
        if subscription and expected_revision is not None and subscription["effective_revision"] != expected_revision:
            return None
        return subscription

    def _submit(self, owner_key: str, actor: dict[str, Any], window: dict[str, Any], todo: dict[str, Any]):
        from .steward_managed_mode import ManagedSessionTurn
        return ManagedSessionTurn(self.controller).submit(owner_key, agent_id=actor["agent_id"],
            operation_id="report." + window["window_id"], todo_id=todo["todo_id"], report_window=window)

    def frontier(self, session_id: str) -> tuple[dict[str, Any] | None, bool]:
        from .steward_monitor import StewardMonitor
        from .steward_turn import StewardTurnHost
        actor = self.selected(session_id)["actor"]
        subscription, window = self.current_window(session_id)
        if subscription is None:
            return None, False
        if window and (attempt_directory(self.root, window) / "private-generation.json").exists():
            todo = next((row for row in StewardMonitor(self.controller).todos(actor)
                if row.get("target_key") == window["window_id"]), None)
            if todo:
                StewardTurnHost(self.controller).recover_report_settlement(actor, window, todo)
                self.publish(session_id, window)
        return self._frontier(session_id, actor)

    def prepare(self, request: dict[str, Any], adapter: Any, *, authorization_scope_id: str | None = None) -> str:
        from pathlib import Path
        from ...control_plane.todos.list_readback import list_goal_todos
        from ..periodic_report.pending_intent import _canonical_digest
        from ..periodic_report.private_generation import prepare_session_report
        from ..periodic_report.cadence_journal import cadence_journal_path
        actor = self.selected(request["actor"]["session_id"])["actor"]
        _, window = self.current_window(actor["session_id"])
        if actor != request["actor"] or not window or window["window_id"] != request["report_window_id"]:
            raise ValueError("the admitted original Session report changed")

        def read():
            active = list_goal_todos(registry_path=self.controller.registry_path, goal_id=actor["goal_id"],
                runtime_root_arg=str(self.root), limit=None)
            history = list_goal_todos(registry_path=self.controller.registry_path, goal_id=actor["goal_id"],
                runtime_root_arg=str(self.root), role="agent", status="done", read_scope="completed_history", limit=None)
            rows = {row["todo_id"]: row for row in [*active["todos"], *history["todos"]]}
            source = {"goal_id": actor["goal_id"], "observed_at": datetime.now(timezone.utc).isoformat(),
                "rows": [rows[key] for key in sorted(rows)], "complete_native_task_read": True,
                "artifact_contents_independently_read": False, "as_of_window_reconstruction": False}
            return {**source, "source_revision": _canonical_digest(source)}

        rules = Path(actor["workspace_path"]) / "REPORTING.md"
        if rules.is_symlink():
            raise ValueError("the original reporting criteria changed source")
        reporting = rules.read_text(encoding="utf-8") if rules.exists() else "Report this Goal's evidence-backed outcomes, current work, failures and next actions; preserve unknown times and verification gaps."
        with exclusive_file_lock(cadence_journal_path(self.root, actor["goal_id"]), operation="freeze_session_report_source"):
            if self.subscription(actor["session_id"], expected_revision=window["subscription_revision"]) is None:
                raise ValueError("the original report configuration changed before collection")
            frozen = prepare_session_report(root=self.root, window=window, actor=actor, read=read, reporting=reporting)
        return report_prompt(frozen, attempt_directory(self.root, window))

    def _frozen_actor(self, window: dict[str, Any]) -> dict[str, Any] | None:
        from ..periodic_report.pending_intent import _canonical_digest
        path = attempt_directory(self.root, window) / "editorial-request.json"
        if not path.is_file():
            return None
        frozen = json.loads(path.read_text(encoding="utf-8"))
        saved = frozen.get("managed_session_actor")
        if not isinstance(saved, dict):
            return None
        actor = self.selected(saved.get("session_id"))["actor"]
        return actor if (actor == saved and frozen.get("window") == window
            and actor["goal_id"] == window["goal_id"] and actor["agent_id"] == window["agent_id"]
            and frozen.get("request_digest") == _canonical_digest({k: v for k, v in frozen.items() if k != "request_digest"})) else None

    def delivery_current(self, session_id: str, window: dict[str, Any]) -> bool:
        return (self.subscription(session_id, expected_revision=window["subscription_revision"]) is not None
            and self._frozen_actor(window) == self.selected(session_id)["actor"])

    def publish(self, session_id: str, window: dict[str, Any]) -> bool:
        """Read the original native commit, then publish/read back one Core message.

        Core's explicit message identity reconciles an unknown append; a lost
        publication-cursor response is recovered from that same full body.
        There is no external send and no second Chat/model Turn.
        """
        import hashlib
        from ..periodic_report.private_generation import validate_private_generation
        from ..periodic_report.incremental import commit_periodic_report_publication_cursor
        from .steward_turn import report_turn
        actor = self.selected(session_id)["actor"]
        turn = report_turn(self.controller.store, actor, window)
        if not turn:
            return False
        request = turn.get("loopx_request") or {}
        if (request.get("actor") != actor or request.get("report_window_id") != window["window_id"]
                or request.get("report_subscription_revision") != window["subscription_revision"]):
            raise ValueError("the original report return changed identity")
        if not any(row["kind"] == "native_loopx.result" and row["payload"].get("status") == "committed"
                for row in self.controller.store.events_after(session_id, turn["turn_id"], None)):
            return False
        folder = attempt_directory(self.root, window)
        from ..periodic_report.cadence_journal import cadence_journal_path
        from ...control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission, capture_first_party_host_goal_ref
        with exclusive_file_lock(cadence_journal_path(self.root, actor["goal_id"]), operation="publish_session_report"):
            admission = FirstPartyHostGoalAdmission.for_plan(registry_path=self.controller.registry_path,
                goal_id=actor["goal_id"], planned_goal_ref=capture_first_party_host_goal_ref(
                    registry_path=self.controller.registry_path, goal_id=actor["goal_id"]))
            with self.controller.lock, admission.launch_lifetime(operation="return_session_report"), exclusive_file_lock(
                    folder / "delivery.lock", operation="return_session_report"):
                generation = validate_private_generation(folder)
                if not self.delivery_current(session_id, window):
                    return False
                body = (folder / "report.md").read_text(encoding="utf-8")
                message_id = "managed." + turn["turn_id"] + ".completed"
                messages = self.controller.store.messages(session_id)
                existing = next((row for row in messages if row["message_id"] == message_id), None)
                # A retained earlier partial/failed outer answer stays immutable.
                # Only the newly recovered native commit permits the full report.
                if existing and existing.get("text") != body:
                    message_id = "report." + window["window_id"]
                self.controller.store.append_message(session_id, role="agent", text=body,
                    turn_id=turn["turn_id"], message_id=message_id)
                readback = [row for row in self.controller.store.messages(session_id) if row["message_id"] == message_id]
                if (len(readback) != 1 or readback[0]["text"] != body or readback[0]["role"] != "agent"
                        or readback[0]["turn_id"] != turn["turn_id"] or not self.delivery_current(session_id, window)):
                    raise ValueError("the complete original report message did not verify")
                candidate = json.loads((folder / "publication-candidate.json").read_text(encoding="utf-8"))
                identity = [session_id, window["window_id"], generation["generation_id"], message_id, generation["body_sha256"]]
                commit_periodic_report_publication_cursor(runtime_root=self.root, candidate=candidate,
                    publication_id="session-report-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                    delivered_at=readback[0]["created_at"], covered_until=window["due_at"])
                return True

    def health(self, session_id: str) -> dict[str, Any]:
        try:
            actor = self.selected(session_id)["actor"]
            goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
            config = configuration_summary(goal)
            if not config or config.get("route_ref") != "managed-session:" + session_id:
                return {"available": True, "configured": False, "enabled": False}
            journal = read_cadence_journal(runtime_root=self.root, goal_id=actor["goal_id"])
            window = journal["window"] if journal else None
            folder = attempt_directory(self.root, window) if window else None
            from .steward_turn import report_turn
            turn = report_turn(self.controller.store, actor, window) if window else None
            native = ((turn or {}).get("response") or {}).get("native_loopx") or {}
            failed = bool(turn and (turn["status"] in {"failed", "interrupted", "timed_out"}
                or native.get("status") == "failed"))
            published = bool(window and self._published_window(window))
            returned = []
            if published and turn and folder and (folder / "report.md").is_file():
                body = (folder / "report.md").read_text(encoding="utf-8")
                returned = [row for row in self.controller.store.messages(session_id)
                    if row["role"] == "agent" and row["turn_id"] == turn["turn_id"] and row["text"] == body]
            verified = published and len(returned) == 1
            return {"available": True, "configured": True, "enabled": bool(self.subscription(session_id)),
                "scheduled": bool(config.get("schedule")),
                "schedule": config.get("schedule"), "next_due_at": report_cadence_next_due_at(
                    config.get("schedule"), now=datetime.now(timezone.utc)) if config.get("enabled") else None,
                "retained_window_due_at": window["due_at"] if window else None,
                "generation_complete": bool(folder and (folder / "private-generation.json").exists()),
                "execution_failed": failed,
                "publication_verified": verified,
                "report_message_id": returned[0]["message_id"] if verified else None}
        except (OSError, ValueError, KeyError, TypeError):
            return {"available": False, "error_code": "managed_session_report_unavailable"}
