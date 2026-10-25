"""Calendar IO for ordinary native Tasks in an existing managed Session.

The existing Goal configuration, cadence journal, Todo, Turn, validator and
Core conversation are the owners. This adapter adds no worker or task store.
An explicit task declaration is required; conversation prose is never parsed
here as consent, a schedule or an executable command.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re

from ...agent_registry import load_goal_from_registry
from ...control_plane.digest_envelope import sha256_envelope
from ...control_plane.todos.contract import compact_todo_text, decision_scope_metadata_value
from ...control_plane.todos.completion_validation_store import read_completion_validation_declaration
from ...file_lock import exclusive_file_lock
from ...registry import atomic_write_json
from ..periodic_report.cadence import report_cadence_next_due_at
from ..periodic_report.cadence_journal import (
    admit_cadence_window, cadence_journal_path, read_cadence_journal,
    record_cadence_task_completion,
)
from ..periodic_report.goal_configuration import configuration_summary
from ..periodic_report.machine_defaults import resolve_active_goal_periodic_report_subscription
from ..periodic_report.pending_intent import _canonical_digest
from ..periodic_report.private_generation import attempt_directory
from .steward_commands import native_command
from .steward_reports import ManagedSessionReports


class SessionCalendarTasks(ManagedSessionReports):
    def subscription(self, session_id, *, expected_revision=None):
        actor = self.selected(session_id)["actor"]
        goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
        config = configuration_summary(goal)
        if (not config or config.get("profile_preset") != "session-task"
                or config.get("route_ref") != "managed-session:" + session_id):
            return None
        subscription = resolve_active_goal_periodic_report_subscription(goal)
        if subscription and expected_revision is not None and subscription["effective_revision"] != expected_revision:
            return None
        return subscription

    def _definition(self, window):
        path = attempt_directory(self.root, window) / "task.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("window") != window or value.get("digest") != _canonical_digest(
                {k: v for k, v in value.items() if k != "digest"})):
            raise ValueError("the frozen calendar task changed")
        actor = value.get("actor")
        if not isinstance(actor, dict) or self.selected(actor.get("session_id"))["actor"] != actor:
            raise ValueError("the original calendar task actor changed")
        return value

    def _frozen_actor(self, window):
        value = self._definition(window)
        return value["actor"] if value else None

    def _todo(self, actor, window):
        from .steward_monitor import StewardMonitor
        from ...control_plane.todos.list_readback import list_goal_todos
        rows = [row for row in StewardMonitor(self.controller).todos(actor)
            if row.get("target_key") == window["window_id"]]
        if not rows:
            # Active-list absence never permits another execution. A finished
            # task may already be in the canonical completed-history view.
            history = list_goal_todos(registry_path=self.controller.registry_path, goal_id=actor["goal_id"],
                runtime_root_arg=str(self.root), role="agent", status="done", read_scope="completed_history", limit=None)
            rows = [row for row in history["todos"] if row.get("target_key") == window["window_id"]]
        if len(rows) > 1:
            raise ValueError("the calendar window has conflicting original Tasks")
        return rows[0] if rows else None

    def source_task(self, actor, task):
        """Read a completed native Task; configuration supplies no host argv."""
        from ...control_plane.todos.list_readback import list_goal_todos
        row = list_goal_todos(registry_path=self.controller.registry_path, goal_id=actor["goal_id"],
            runtime_root_arg=str(self.root), todo_id=task["source_todo_id"], role="agent").get("todo")
        if row is None:
            row = list_goal_todos(registry_path=self.controller.registry_path, goal_id=actor["goal_id"],
                runtime_root_arg=str(self.root), todo_id=task["source_todo_id"], role="agent",
                status="done", read_scope="completed_history").get("todo")
        if (not row or row.get("status") != "done" or row.get("claimed_by") != actor["agent_id"]
                or row.get("task_class") != "advancement_task" or row.get("completion_validation_required") is not True
                or row.get("action_kind") == "generate_periodic_report"
                or re.fullmatch(r"cadence_[a-f0-9]{64}", row.get("target_key") or "")
                or actor["agent_id"] in row.get("excluded_agents", [])):
            raise ValueError("select this Agent's existing completed Task with independent argv validation")
        declaration = read_completion_validation_declaration(runtime_root=self.root,
            goal_id=actor["goal_id"], todo_id=row["todo_id"], expected_digest=row["completion_validation_sha256"])
        if not declaration or not declaration.get("validation_command_argv"):
            raise ValueError("the existing source task has no independent argv validation")
        return row, declaration

    def admission(self, request):
        actor = request["actor"]
        subscription, window = self.current_window(actor["session_id"])
        if (not subscription or not window or window["window_id"] != request["schedule_window_id"]
                or subscription["effective_revision"] != window["subscription_revision"]):
            raise ValueError("the original calendar task subscription changed")
        definition = self._definition(window)
        todo = self._todo(actor, window)
        if not definition or not todo:
            raise ValueError("the original finite calendar task is unavailable")
        source_task, validation = self.source_task(actor, subscription["task"])
        declaration = read_completion_validation_declaration(runtime_root=self.root,
            goal_id=actor["goal_id"], todo_id=todo["todo_id"],
            expected_digest=todo.get("completion_validation_sha256"))
        if not declaration or declaration != validation or declaration != definition["validation"]:
            raise ValueError("the original calendar task's independent validation changed")
        return {"subscription": subscription, "window": window, "definition": definition, "todo": todo, "source_task": source_task}

    def _completion(self, window):
        """Task DONE alone is insufficient: read the original native commit/body."""
        definition = self._definition(window)
        if not definition:
            return None
        actor = definition["actor"]
        todo = self._todo(actor, window)
        completed_key = (todo or {}).get("completion_turn_key")
        client = ("managed." + completed_key.removeprefix("managed-session.")
            if isinstance(completed_key, str) and completed_key.startswith("managed-session.")
            else "managed.task." + window["window_id"])
        turn = self.controller.store.turn_for_client(actor["session_id"], client)
        request = (turn or {}).get("loopx_request") or {}
        response = (turn or {}).get("response") or {}
        if (not todo or not turn or turn["status"] != "completed" or todo["status"] != "done"
                or request.get("work_kind") != "scheduled_task" or request.get("actor") != actor
                or request.get("todo_id") != todo["todo_id"] or request.get("schedule_window_id") != window["window_id"]
                or request.get("schedule_subscription_revision") != window["subscription_revision"]
                or todo.get("completion_turn_key") != request.get("turn_instance_id")
                or (response.get("native_loopx") or {}).get("status") != "committed"):
            return None
        if not any(row["kind"] == "native_loopx.result" and row["payload"].get("status") == "committed"
                for row in self.controller.store.events_after(actor["session_id"], turn["turn_id"], None)):
            return None
        body = response.get("message")
        rows = [row for row in self.controller.store.messages(actor["session_id"])
            if row["message_id"] == "managed." + turn["turn_id"] + ".completed"]
        if (not isinstance(body, str) or not body.strip() or len(rows) != 1
                or rows[0]["role"] != "agent" or rows[0]["turn_id"] != turn["turn_id"] or rows[0]["text"] != body):
            return None
        return {"todo_id": todo["todo_id"], "turn_instance_id": request["turn_instance_id"],
            "message_id": rows[0]["message_id"], "message_sha256": sha256_envelope(body.encode("utf-8")),
            "completed_at": rows[0]["created_at"]}

    def _published_window(self, window):
        # Compatibility with the shared configuration fence; this remains a
        # Task completion, never an invented report/provider publication.
        return self._completion(window) is not None

    def _known_generation_failure(self, actor, window):
        turn = self.controller.store.turn_for_client(actor["session_id"], "managed.task." + window["window_id"])
        request = (turn or {}).get("loopx_request") or {}
        native = ((turn or {}).get("response") or {}).get("native_loopx") or {}
        todo = self._todo(actor, window)
        validation, receipt, effects = native.get("validation") or {}, native.get("receipt") or {}, native.get("effects") or {}
        if (turn and turn["status"] == "completed" and native.get("status") == "failed"
                and todo and todo.get("status") in {"open", "blocked"}
                and native.get("result_kind") == "validation_failed" and native.get("validation_stage") == "task_postcondition"
                and validation.get("schema_version") == "loopx_turn_task_validation_v0"
                and validation.get("ok") is False and validation.get("validator_kind") == "command"
                and receipt.get("ok") is True and receipt.get("failed_phase") == "validation"
                and receipt.get("completed_phases") == ["host_execute", "typed_result"]
                and receipt.get("lineage") == {"goal_id": actor["goal_id"], "agent_id": actor["agent_id"], "todo_id": todo["todo_id"]}
                and effects.get("state_written") is False and effects.get("quota_spent") is False
                and request.get("work_kind") == "scheduled_task" and request.get("actor") == actor
                and request.get("todo_id") == todo["todo_id"]
                and request.get("schedule_window_id") == window["window_id"]
                and request.get("schedule_subscription_revision") == window["subscription_revision"]):
            return {"turn_id": turn["turn_id"], "todo_id": request["todo_id"]}
        return None

    def _cancel_failed_window(self, actor, window, failure, *, cadence_locked=False):
        from ..periodic_report.cadence_journal import (
            _retire_failed_cadence_window_locked, retire_failed_cadence_window,
        )
        if self._known_generation_failure(actor, window) != failure:
            raise ValueError("the original failed calendar task changed before cancellation")
        native_command(self.controller, ["todo", "update", "--goal-id", actor["goal_id"],
            "--todo-id", failure["todo_id"], "--agent-id", actor["agent_id"],
            "--update-operation-id", "cancel.task." + window["window_id"], "--status", "blocked",
            "--clear-resume-when", "--reason",
            "Owner cancelled this known failed calendar task; retain original work, effects and failure evidence, not completion.", "--execute"])
        retire = _retire_failed_cadence_window_locked if cadence_locked else retire_failed_cadence_window
        retire(runtime_root=self.root, goal_id=actor["goal_id"], expected_window_id=window["window_id"],
            source_receipt_id=failure["turn_id"], reason="owner_cancelled_failed_task")

    def frontier(self, session_id):
        from .steward_managed_mode import ManagedSessionTurn
        actor = self.selected(session_id)["actor"]
        subscription = self.subscription(session_id)
        if not subscription or not subscription.get("schedule"):
            return None, False
        journal = read_cadence_journal(runtime_root=self.root, goal_id=actor["goal_id"])
        if (journal and not journal.get("task_completion")
                and journal["window"]["profile_ref"]["profile_id"] == "session_task"):
            receipt = self._completion(journal["window"])
            if receipt:
                record_cadence_task_completion(runtime_root=self.root, goal_id=actor["goal_id"],
                    expected_window_id=journal["window"]["window_id"], receipt=receipt)
        admitted = admit_cadence_window(runtime_root=self.root, goal_id=actor["goal_id"],
            agent_id=actor["agent_id"], subscription=subscription, now=datetime.now(timezone.utc),
            subscription_resolver=lambda: self.subscription(session_id))
        if admitted["status"] != "pending":
            return None, False
        window = admitted["window"]
        with exclusive_file_lock(cadence_journal_path(self.root, actor["goal_id"]), operation="prepare_calendar_task"):
            current = self.subscription(session_id, expected_revision=window["subscription_revision"])
            live_journal = read_cadence_journal(runtime_root=self.root, goal_id=actor["goal_id"])
            if current is None or not live_journal or live_journal["window"] != window:
                return None, False
            source_task, validation = self.source_task(actor, current["task"])
            path = attempt_directory(self.root, window) / "task.json"
            definition = self._definition(window)
            if definition is None:
                text = compact_todo_text(f"[{source_task.get('priority') or 'P1'}] Scheduled task for {window['start_at']} to {window['due_at']}: {source_task.get('title') or source_task['text']}")
                definition = {"actor": actor, "window": window, "task": current["task"], "todo_text": text,
                    "source_task": source_task, "validation": validation}
                definition["digest"] = _canonical_digest(definition)
                atomic_write_json(path, definition)
            todo = self._todo(actor, window)
            if todo is None:
                argv = ["todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
                    "--operation-id", window["window_id"], "--text", definition["todo_text"],
                    "--claimed-by", actor["agent_id"],
                    "--priority", source_task.get("priority") or "P1", "--task-class", "advancement_task",
                    "--target-key", window["window_id"], "--continuation-policy", "same_agent_non_delivery",
                    "--validation-command-json", json.dumps(definition["validation"]["validation_command_argv"]), "--execute"]
                for key, flag in {"action_kind": "--action-kind", "task_domain": "--task-domain", "task_repository": "--task-repository",
                        "capability_binding_ref": "--capability-binding-ref"}.items():
                    if source_task.get(key):
                        argv += [flag, source_task[key]]
                for key, flag in {"required_write_scopes": "--required-write-scope", "required_capabilities": "--required-capability",
                        "target_capabilities": "--target-capability",
                        "excluded_agents": "--excluded-agent"}.items():
                    for item in source_task.get(key, []):
                        argv += [flag, item]
                for scope in source_task.get("required_decision_scopes", []):
                    argv += ["--required-decision-scope", decision_scope_metadata_value(scope)]
                for key, flag in {"validation_label": "--validation-label", "validation_timeout_seconds": "--validation-timeout-seconds"}.items():
                    if validation.get(key) is not None:
                        argv += [flag, str(validation[key])]
                native_command(self.controller, argv)
                todo = self._todo(actor, window)
        if not todo or todo["status"] != "open":
            return None, False
        return ManagedSessionTurn(self.controller).submit(session_id, agent_id=actor["agent_id"],
            operation_id="task." + window["window_id"], todo_id=todo["todo_id"], scheduled_window=window)

    def health(self, session_id):
        configured = False
        try:
            actor = self.selected(session_id)["actor"]
            goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
            config = configuration_summary(goal)
            if not config or config.get("profile_preset") != "session-task" or config.get("route_ref") != "managed-session:" + session_id:
                return {"available": True, "configured": False, "enabled": False}
            configured = True
            journal = read_cadence_journal(runtime_root=self.root, goal_id=actor["goal_id"])
            receipt = self._completion(journal["window"]) if journal else None
            client = ("managed." + receipt["turn_instance_id"].removeprefix("managed-session.")
                if receipt else "managed.task." + journal["window"]["window_id"] if journal else None)
            turn = self.controller.store.turn_for_client(session_id, client) if client else None
            native = ((turn or {}).get("response") or {}).get("native_loopx") or {}
            return {"available": True, "configured": True, "enabled": bool(self.subscription(session_id)),
                "schedule": config.get("schedule"), "task": config.get("task"),
                "next_due_at": report_cadence_next_due_at(config.get("schedule"), now=datetime.now(timezone.utc)) if config.get("enabled") else None,
                "retained_window_due_at": journal["window"]["due_at"] if journal else None,
                "completion_verified": bool(receipt), "completion": receipt,
                "task_id": (turn or {}).get("loopx_request", {}).get("todo_id"),
                "turn_id": (turn or {}).get("turn_id"), "native_result_status": native.get("status"),
                "result_kind": native.get("result_kind")}
        except (OSError, ValueError, KeyError, TypeError):
            return {"available": False, "configured": configured, "error_code": "managed_calendar_task_unavailable"}
