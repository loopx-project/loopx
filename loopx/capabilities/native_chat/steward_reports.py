"""Calendar/report IO on the original Chat worker, using the native owners.

Subscription configuration belongs to configure-goal, window admission to
periodic-report, tasks/settlement to LoopX Turn, and private delivery to the
bound provider. This adapter creates no scheduler, executor or business store.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
        from .steward_turn import native_steward_client_id
        folder = attempt_directory(self.root, window)
        if (not (folder / "editorial-response.json").is_file()
                or (folder / "private-generation.json").exists() or (folder / "private-delivery.json").exists()):
            return None
        turn = self.controller.store.turn_for_client(actor["session_id"],
            native_steward_client_id(actor, "report." + window["window_id"]))
        request = (turn or {}).get("loopx_request") or {}
        native = ((turn or {}).get("response") or {}).get("native_loopx") or {}
        if (turn and turn.get("status") == "completed" and native.get("status") == "failed"
                and request.get("actor") == actor and request.get("report_window_id") == window["window_id"]):
            return {"turn_id": turn["turn_id"], "todo_id": request["todo_id"]}
        return None

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
                from ..periodic_report.cadence_journal import retire_failed_cadence_window
                key = "cancel.report." + journal["window"]["window_id"]
                native_command(self.controller, ["todo", "update", "--goal-id", actor["goal_id"],
                    "--todo-id", failure["todo_id"], "--agent-id", actor["agent_id"],
                    "--update-operation-id", key, "--status", "blocked", "--clear-resume-when", "--reason",
                    "Owner cancelled a known failed report window; retain the failed task, costs and evidence, not completed or delivered.", "--execute"])
                retire_failed_cadence_window(runtime_root=self.root, goal_id=actor["goal_id"],
                    expected_window_id=journal["window"]["window_id"], source_receipt_id=failure["turn_id"])
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
        if self.subscription(binding_id, expected_revision=window["subscription_revision"]) is None:
            return False
        selected = self.selected(binding_id)
        session = selected["session"]
        expected = json.loads((attempt_directory(self.root, window) / "editorial-request.json").read_text())
        current = manager_authorization_scope_id_for_registry(self.controller.registry_path,
            self.bindings.steward_scope(session), runtime_root=self.root, channel_id=session["channel_id"])
        return current == expected["authorization_scope_id"]

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
                        value = json.loads(path.read_text())
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
        from .steward_monitor import StewardMonitor
        from .steward_turn import StewardTurnHost

        actor = binding["managed_actor"]
        subscription = self.subscription(binding["binding_id"])
        if subscription is None or subscription.get("schedule") is None:
            return None, False
        admitted = admit_cadence_window(runtime_root=self.root, goal_id=actor["goal_id"],
            agent_id=actor["agent_id"], subscription=subscription, now=datetime.now(timezone.utc),
            subscription_resolver=lambda: self.subscription(binding["binding_id"]))
        if admitted["status"] != "pending":
            return None, False
        window = admitted["window"]
        rows = [row for row in StewardMonitor(self.controller).todos(actor) if row.get("target_key") == window["window_id"]]
        if len(rows) > 1:
            raise ValueError("report window has conflicting native tasks")
        if not rows:
            native_command(self.controller, ["todo", "add", "--goal-id", actor["goal_id"],
                "--role", "agent", "--operation-id", window["window_id"],
                "--text", f"[P1] Author the authorized portfolio report ending {window['due_at']}; retain counterevidence and coverage gaps. Generation is separate from verified private delivery.",
                "--claimed-by", actor["agent_id"], "--action-kind", "generate_periodic_report",
                "--target-key", window["window_id"], "--continuation-policy", "same_agent_non_delivery",
                "--execute"])
            rows = [row for row in StewardMonitor(self.controller).todos(actor) if row.get("target_key") == window["window_id"]]
        if len(rows) == 1 and rows[0]["status"] == "done":
            StewardTurnHost(self.controller).recover_report_settlement(actor, window, rows[0])
            return None, False
        if len(rows) != 1 or rows[0]["status"] != "open":
            return None, False
        folder = attempt_directory(self.root, window)
        validator = [sys.executable, "-c", "from pathlib import Path; from loopx.capabilities.periodic_report.private_generation import validate_private_generation; "
            + f"validate_private_generation(Path({str(folder)!r}))"]
        return StewardTurnHost(self.controller).submit(binding["binding_id"],
            turn_instance_id="report." + window["window_id"], todo_id=rows[0]["todo_id"],
            work_kind="report", report_window=window, validation_command=validator)

    def prepare(self, request: dict[str, Any], adapter: Any, *, authorization_scope_id: str) -> str:
        from ..manager_context.inspection import TOOL_NAME
        _, window = self.current_window(request["actor"]["goal_id"].removeprefix("steward-agent-"))
        if not window or window["window_id"] != request["report_window_id"]:
            raise ValueError("the admitted original report window is unavailable")
        reader = getattr(adapter.session, "read_tool_handler", None)
        if reader is None or not authorization_scope_id:
            raise ValueError("the original scoped report reader is unavailable")
        session = self.controller.store.load_session(request["actor"]["session_id"])
        rules = self.managed._workspace(session) / "REPORTING.md"
        if rules.is_symlink():
            raise ValueError("the original reporting criteria changed source")
        reporting = rules.read_text(encoding="utf-8") if rules.exists() else "Use the existing overview-to-depth report contract and preserve all coverage gaps."
        frozen = prepare_private_report(root=self.root, window=window, read=lambda query: reader(TOOL_NAME, query),
            authorization_scope_id=authorization_scope_id, reporting=reporting)
        return report_prompt(frozen, attempt_directory(self.root, window))

    def freeze(self, request: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        _, window = self.current_window(request["actor"]["goal_id"].removeprefix("steward-agent-"))
        if not window or window["window_id"] != request["report_window_id"]:
            raise ValueError("the original report was paused, cancelled or changed")
        response = candidate.pop("editorial_response", None)
        if not isinstance(response, dict):
            raise ValueError("the original reporting Agent returned no editorial response")
        return freeze_private_report(root=self.root, window=window, response=response)
