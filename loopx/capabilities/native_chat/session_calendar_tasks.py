"""Calendar IO for ordinary native Tasks in an existing managed Session.

The existing Goal configuration, cadence journal, Todo, Turn, validator and
Core conversation are the owners. This adapter adds no worker or task store.
An explicit task declaration is required; conversation prose is never parsed
here as consent, a schedule or an executable command.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import cast

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
from .steward_turn_common import as_dict, as_dict_list, as_mapping, as_optional_dict, steward_monitor_for


class SessionCalendarTasks(ManagedSessionReports):
    def subscription(
        self,
        session_id: str,
        *,
        expected_revision: str | None = None,
        expected_actor: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        actor = as_mapping(self.selected(session_id)["actor"])
        if expected_actor is not None and actor != expected_actor:
            return None
        goal = load_goal_from_registry(cast(Path, self.controller.registry_path), str(actor["goal_id"]))
        config = as_optional_dict(configuration_summary(goal or {}))
        if (not config or config.get("profile_preset") != "session-task"
                or config.get("route_ref") != "managed-session:" + session_id):
            return None
        subscription = as_optional_dict(resolve_active_goal_periodic_report_subscription(goal or {}))
        if subscription and expected_revision is not None and subscription["effective_revision"] != expected_revision:
            return None
        return subscription

    def _definition(self, window: Mapping[str, object]) -> dict[str, object] | None:
        path = attempt_directory(self.root, dict(window)) / "task.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("window") != window or value.get("digest") != _canonical_digest(
                {k: v for k, v in value.items() if k != "digest"})):
            raise ValueError("the frozen calendar task changed")
        actor = value.get("actor")
        if not isinstance(actor, dict) or self.selected(cast(str, actor.get("session_id")))["actor"] != actor:
            raise ValueError("the original calendar task actor changed")
        return {str(k): v for k, v in value.items()}

    def _frozen_actor(self, window: dict[str, object]) -> dict[str, object] | None:
        value = self._definition(window)
        return as_dict(value["actor"]) if value else None

    def _todo(self, actor: Mapping[str, object], window: Mapping[str, object]) -> dict[str, object] | None:
        from ...control_plane.todos.list_readback import list_goal_todos
        rows: list[dict[str, object]] = [
            dict(row) for row in steward_monitor_for(self.controller).todos(actor)
            if row.get("target_key") == window["window_id"]
        ]
        if not rows:
            # Active-list absence never permits another execution. A finished
            # task may already be in the canonical completed-history view.
            history = as_mapping(list_goal_todos(
                registry_path=cast(Path, self.controller.registry_path), goal_id=str(actor["goal_id"]),
                runtime_root_arg=str(self.root), role="agent", status="done", read_scope="completed_history", limit=None,
            ))
            rows = [row for row in as_dict_list(history["todos"]) if row.get("target_key") == window["window_id"]]
        if len(rows) > 1:
            raise ValueError("the calendar window has conflicting original Tasks")
        return rows[0] if rows else None

    def source_task(
        self, actor: Mapping[str, object], task: Mapping[str, object],
    ) -> tuple[dict[str, object] | None, dict[str, object]]:
        """Read a completed native Task; configuration supplies no host argv."""
        from ...control_plane.todos.list_readback import list_goal_todos
        reg_path = cast(Path, self.controller.registry_path)
        goal_id = str(actor["goal_id"])
        source_todo_id = str(task["source_todo_id"])
        row = as_optional_dict(list_goal_todos(
            registry_path=reg_path, goal_id=goal_id,
            runtime_root_arg=str(self.root), todo_id=source_todo_id, role="agent",
        ).get("todo"))
        if row is None:
            row = as_optional_dict(list_goal_todos(
                registry_path=reg_path, goal_id=goal_id,
                runtime_root_arg=str(self.root), todo_id=source_todo_id, role="agent",
                status="done", read_scope="completed_history",
            ).get("todo"))
        return row, self._source_validation(actor, row)

    @staticmethod
    def _source_candidate(actor: Mapping[str, object], row: Mapping[str, object] | None) -> bool:
        if not row:
            return False
        excluded = row.get("excluded_agents")
        excluded_list = excluded if isinstance(excluded, Sequence) and not isinstance(excluded, (str, bytes)) else ()
        target_key = row.get("target_key")
        return bool(row.get("status") == "done" and row.get("claimed_by") == actor["agent_id"]
                and row.get("task_class") == "advancement_task" and row.get("completion_validation_required") is True
                and row.get("action_kind") != "generate_periodic_report"
                and not re.fullmatch(r"cadence_[a-f0-9]{64}", target_key if isinstance(target_key, str) else "")
                and actor["agent_id"] not in excluded_list)

    def _source_validation(self, actor: Mapping[str, object], row: Mapping[str, object] | None) -> dict[str, object]:
        if not self._source_candidate(actor, row) or row is None:
            raise ValueError("select this Agent's existing completed Task with independent argv validation")
        expected_digest = row["completion_validation_sha256"]
        declaration = as_optional_dict(read_completion_validation_declaration(
            runtime_root=self.root,
            goal_id=str(actor["goal_id"]),
            todo_id=str(row["todo_id"]),
            expected_digest=expected_digest if isinstance(expected_digest, str) else None,
        ))
        if not declaration or not declaration.get("validation_command_argv"):
            raise ValueError("the existing source task has no independent argv validation")
        return declaration

    def source_options(self, session_id: str) -> dict[str, object]:
        """Discover references through the same source admission, without enrolling work.

        Completed-history reads include active and archived native Tasks. The
        canonical owner drains its snapshot pages before projecting this view;
        no hot-list limit or private second index determines membership here.
        """
        from ...control_plane.todos.list_readback import list_goal_todos
        try:
            actor = as_mapping(self.selected(session_id)["actor"])
            snapshot = as_mapping(list_goal_todos(
                registry_path=cast(Path, self.controller.registry_path), goal_id=str(actor["goal_id"]),
                runtime_root_arg=str(self.root), role="agent", status="done", read_scope="completed_history", limit=None,
            ))
            sources: list[dict[str, object]] = []
            for row in as_dict_list(snapshot["todos"]):
                if not self._source_candidate(actor, row):
                    continue
                # An unreadable or changed declaration must fail the read instead
                # of turning a source-coverage failure into an empty valid catalog.
                validation = self._source_validation(actor, row)
                sources.append({"source_todo_id": row["todo_id"], "title": row.get("title") or row["text"],
                    "completion_validation_sha256": row["completion_validation_sha256"],
                    "validation_label": validation.get("validation_label"),
                    "archived": row.get("archive_state") == "archive"})
            return {"schema_version": "managed_session_task_sources_v0", "session_id": session_id,
                "goal_id": actor["goal_id"], "agent_id": actor["agent_id"], "read_only": True,
                "sources": sources, "authority_read": snapshot["authority_read"]}
        except OSError as exc:
            raise RuntimeError("Original managed Task sources are temporarily unreadable") from exc

    def admission(self, request: Mapping[str, object]) -> dict[str, object]:
        actor = as_mapping(request["actor"])
        subscription, window = self.current_window(str(actor["session_id"]))
        if (not subscription or not window or window["window_id"] != request["schedule_window_id"]
                or subscription["effective_revision"] != window["subscription_revision"]):
            raise ValueError("the original calendar task subscription changed")
        definition = self._definition(window)
        todo = self._todo(actor, window)
        if not definition or not todo:
            raise ValueError("the original finite calendar task is unavailable")
        source_task, validation = self.source_task(actor, as_mapping(subscription["task"]))
        expected_digest = todo.get("completion_validation_sha256")
        declaration = as_optional_dict(read_completion_validation_declaration(
            runtime_root=self.root,
            goal_id=str(actor["goal_id"]),
            todo_id=str(todo["todo_id"]),
            expected_digest=expected_digest if isinstance(expected_digest, str) else None,
        ))
        if not declaration or declaration != validation or declaration != definition["validation"]:
            raise ValueError("the original calendar task's independent validation changed")
        return {"subscription": subscription, "window": window, "definition": definition, "todo": todo, "source_task": source_task}

    def _completion(self, window: Mapping[str, object]) -> dict[str, str] | None:
        """Task DONE alone is insufficient: read the original native commit/body."""
        definition = self._definition(window)
        if not definition:
            return None
        actor = as_mapping(definition["actor"])
        todo = self._todo(actor, window)
        completed_key = todo.get("completion_turn_key") if todo else None
        client = ("managed." + completed_key.removeprefix("managed-session.")
            if isinstance(completed_key, str) and completed_key.startswith("managed-session.")
            else "managed.task." + str(window["window_id"]))
        session_id = str(actor["session_id"])
        turn = as_optional_dict(self.controller.store.turn_for_client(session_id, client))
        request = as_mapping((turn or {}).get("loopx_request"))
        response = as_mapping((turn or {}).get("response"))
        native = as_mapping(response.get("native_loopx"))
        if (not todo or not turn or turn["status"] != "completed" or todo["status"] != "done"
                or request.get("work_kind") != "scheduled_task" or request.get("actor") != actor
                or request.get("todo_id") != todo["todo_id"] or request.get("schedule_window_id") != window["window_id"]
                or request.get("schedule_subscription_revision") != window["subscription_revision"]
                or todo.get("completion_turn_key") != request.get("turn_instance_id")
                or native.get("status") != "committed"):
            return None
        turn_id = str(turn["turn_id"])
        results = [row["payload"] for row in self.controller.store.events_after(session_id, turn_id, None)
            if row["kind"] == "native_loopx.result"]
        if results != [response["native_loopx"]]:
            return None
        body = response.get("message")
        # Tolerant transcript rendering cannot establish a unique complete
        # return: an unreadable record could contain a conflicting reply.
        rows = [row for row in self.controller.store.messages(session_id, strict=True)
            if row["message_id"] == "managed." + turn_id + ".completed"]
        if (not isinstance(body, str) or not body.strip() or len(rows) != 1
                or rows[0]["role"] != "agent" or rows[0]["turn_id"] != turn_id or rows[0]["text"] != body):
            return None
        return cast(dict[str, str], {"todo_id": todo["todo_id"], "turn_instance_id": request["turn_instance_id"],
            "message_id": rows[0]["message_id"], "message_sha256": sha256_envelope(body.encode("utf-8")),
            "completed_at": rows[0]["created_at"]})

    def _published_window(self, window: dict[str, object]) -> bool:
        # Compatibility with the shared configuration fence; this remains a
        # Task completion, never an invented report/provider publication.
        return self._completion(window) is not None

    def _known_generation_failure(
        self, actor: dict[str, object], window: dict[str, object],
    ) -> dict[str, str] | None:
        turn = as_optional_dict(self.controller.store.turn_for_client(
            str(actor["session_id"]), "managed.task." + str(window["window_id"]),
        ))
        request = as_mapping((turn or {}).get("loopx_request"))
        native = as_mapping(as_mapping((turn or {}).get("response")).get("native_loopx"))
        todo = self._todo(actor, window)
        validation = as_mapping(native.get("validation"))
        receipt = as_mapping(native.get("receipt"))
        effects = as_mapping(native.get("effects"))
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
            return {"turn_id": str(turn["turn_id"]), "todo_id": str(request["todo_id"])}
        return None

    def _cancel_failed_window(
        self, actor: dict[str, object], window: dict[str, object], failure: dict[str, str], *, cadence_locked: bool = False,
    ) -> None:
        from ..periodic_report.cadence_journal import (
            _retire_failed_cadence_window_locked, retire_failed_cadence_window,
        )
        if self._known_generation_failure(actor, window) != failure:
            raise ValueError("the original failed calendar task changed before cancellation")
        goal_id = str(actor["goal_id"])
        agent_id = str(actor["agent_id"])
        native_command(self.controller, ["todo", "update", "--goal-id", goal_id,
            "--todo-id", failure["todo_id"], "--agent-id", agent_id,
            "--update-operation-id", "cancel.task." + str(window["window_id"]), "--status", "blocked",
            "--clear-resume-when", "--reason",
            "Owner cancelled this known failed calendar task; retain original work, effects and failure evidence, not completion.", "--execute"])
        retire = _retire_failed_cadence_window_locked if cadence_locked else retire_failed_cadence_window
        retire(runtime_root=self.root, goal_id=goal_id, expected_window_id=str(window["window_id"]),
            source_receipt_id=failure["turn_id"], reason="owner_cancelled_failed_task")

    def frontier(self, session_id: str) -> tuple[dict[str, object] | None, bool]:
        from .steward_managed_mode import ManagedSessionTurn
        actor = as_dict(self.selected(session_id)["actor"])
        goal_id = str(actor["goal_id"])
        agent_id = str(actor["agent_id"])
        subscription = self.subscription(session_id)
        if not subscription or not subscription.get("schedule"):
            return None, False
        journal = as_optional_dict(read_cadence_journal(runtime_root=self.root, goal_id=goal_id))
        if journal and not journal.get("task_completion"):
            journal_window = as_dict(journal["window"])
            if as_mapping(journal_window["profile_ref"]).get("profile_id") == "session_task":
                receipt = self._completion(journal_window)
                if receipt:
                    record_cadence_task_completion(runtime_root=self.root, goal_id=goal_id,
                        expected_window_id=str(journal_window["window_id"]), receipt=receipt)
        admitted = as_mapping(admit_cadence_window(runtime_root=self.root, goal_id=goal_id,
            agent_id=agent_id, subscription=subscription, now=datetime.now(timezone.utc),
            subscription_resolver=lambda: self.subscription(session_id)))
        if admitted["status"] != "pending":
            return None, False
        window = as_dict(admitted["window"])
        window_id = str(window["window_id"])
        with exclusive_file_lock(cadence_journal_path(self.root, goal_id), operation="prepare_calendar_task"):
            current = self.subscription(session_id, expected_revision=str(window["subscription_revision"]))
            live_journal = as_optional_dict(read_cadence_journal(runtime_root=self.root, goal_id=goal_id))
            if current is None or not live_journal or live_journal["window"] != window:
                return None, False
            source_task, validation = self.source_task(actor, as_mapping(current["task"]))
            if source_task is None:
                raise ValueError("select this Agent's existing completed Task with independent argv validation")
            path = attempt_directory(self.root, window) / "task.json"
            definition = self._definition(window)
            if definition is None:
                priority = source_task.get("priority")
                priority_label = str(priority) if priority else "P1"
                text = compact_todo_text(f"[{priority_label}] Scheduled task for {window['start_at']} to {window['due_at']}: {source_task.get('title') or source_task['text']}")
                definition = {"actor": actor, "window": window, "task": current["task"], "todo_text": text,
                    "source_task": source_task, "validation": validation}
                definition["digest"] = _canonical_digest(definition)
                atomic_write_json(path, definition)
            todo = self._todo(actor, window)
            if todo is None:
                def_validation = as_mapping(definition["validation"])
                priority = source_task.get("priority")
                argv: list[str] = ["todo", "add", "--goal-id", goal_id, "--role", "agent",
                    "--operation-id", window_id, "--text", str(definition["todo_text"]),
                    "--claimed-by", agent_id,
                    "--priority", str(priority) if priority else "P1", "--task-class", "advancement_task",
                    "--target-key", window_id, "--continuation-policy", "same_agent_non_delivery",
                    "--validation-command-json", json.dumps(def_validation["validation_command_argv"]), "--execute"]
                for key, flag in {"action_kind": "--action-kind", "task_domain": "--task-domain", "task_repository": "--task-repository",
                        "capability_binding_ref": "--capability-binding-ref"}.items():
                    val = source_task.get(key)
                    if val:
                        argv += [flag, str(val)]
                for key, flag in {"required_write_scopes": "--required-write-scope", "required_capabilities": "--required-capability",
                        "target_capabilities": "--target-capability",
                        "excluded_agents": "--excluded-agent"}.items():
                    items = source_task.get(key)
                    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
                        for item in items:
                            argv += [flag, str(item)]
                scopes = source_task.get("required_decision_scopes")
                if isinstance(scopes, Sequence) and not isinstance(scopes, (str, bytes)):
                    for scope in scopes:
                        scope_value = decision_scope_metadata_value(cast(str | Mapping[str, object], scope))
                        if scope_value is not None:
                            argv += ["--required-decision-scope", scope_value]
                for key, flag in {"validation_label": "--validation-label", "validation_timeout_seconds": "--validation-timeout-seconds"}.items():
                    if validation.get(key) is not None:
                        argv += [flag, str(validation[key])]
                native_command(self.controller, argv)
                todo = self._todo(actor, window)
        if not todo or todo["status"] != "open":
            return None, False
        submitted, created = ManagedSessionTurn(self.controller).submit(
            session_id, agent_id=agent_id,
            operation_id="task." + window_id, todo_id=str(todo["todo_id"]), scheduled_window=window,
        )
        return as_optional_dict(submitted), bool(created)

    def health(self, session_id: str) -> dict[str, object]:
        configured = False
        try:
            actor = as_mapping(self.selected(session_id)["actor"])
            goal_id = str(actor["goal_id"])
            goal = load_goal_from_registry(cast(Path, self.controller.registry_path), goal_id)
            config = as_optional_dict(configuration_summary(goal or {}))
            if not config or config.get("profile_preset") != "session-task" or config.get("route_ref") != "managed-session:" + session_id:
                return {"available": True, "configured": False, "enabled": False}
            configured = True
            journal = as_optional_dict(read_cadence_journal(runtime_root=self.root, goal_id=goal_id))
            journal_window = as_dict(journal["window"]) if journal else None
            receipt = self._completion(journal_window) if journal_window else None
            client = ("managed." + str(receipt["turn_instance_id"]).removeprefix("managed-session.")
                if receipt else "managed.task." + str(journal_window["window_id"]) if journal_window else None)
            turn = as_optional_dict(self.controller.store.turn_for_client(session_id, client)) if client else None
            request = as_mapping((turn or {}).get("loopx_request"))
            native = as_mapping(as_mapping((turn or {}).get("response")).get("native_loopx"))
            schedule_raw = config.get("schedule")
            schedule_cfg = dict(schedule_raw) if isinstance(schedule_raw, Mapping) else None
            return {"available": True, "configured": True, "enabled": bool(self.subscription(session_id)),
                "schedule": schedule_raw, "task": config.get("task"),
                "next_due_at": report_cadence_next_due_at(schedule_cfg, now=datetime.now(timezone.utc)) if config.get("enabled") and schedule_cfg is not None else None,
                "retained_window_due_at": journal_window["due_at"] if journal_window else None,
                "completion_verified": bool(receipt), "completion": receipt,
                "task_id": request.get("todo_id"),
                "turn_id": (turn or {}).get("turn_id"), "native_result_status": native.get("status"),
                "result_kind": native.get("result_kind")}
        except (OSError, ValueError, KeyError, TypeError):
            return {"available": False, "configured": configured, "error_code": "managed_calendar_task_unavailable"}
