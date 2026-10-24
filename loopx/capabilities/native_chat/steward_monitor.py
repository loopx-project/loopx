"""Native monitor Todo + the existing Chat host, without a second scheduler.

The Todo owns enable/pause, cadence and observation state. The existing host's
cheap wake pump checks its due projection; the original Chat worker performs
the observation and the native monitor-poll owner settles it without a model
or quota spend. Reports remain a separate periodic-report integration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import time
from typing import Any

from ...agent_registry import load_goal_from_registry
from ...control_plane.scheduler.monitor_todo import monitor_todo_is_due
from ...control_plane.todos.list_readback import list_goal_todos
from ...control_plane.runtime.time import now_utc
from ...control_plane.goals.activation import goal_is_stopped
from ...file_lock import exclusive_file_lock
from .steward_commands import native_command
from .steward_managed_mode import StewardManagedMode

TARGET = "steward-portfolio-watch"
_LOG = logging.getLogger(__name__)


class StewardObservationAdapter:
    """A watch reads native state, never an old model context or a new thread."""
    observation_only = True

    def __init__(self, upstream_thread_id: str | None) -> None:
        self.upstream_thread_id = upstream_thread_id

    def healthcheck(self) -> bool:
        return True

    def close_session(self) -> None:
        pass

    def interrupt_turn(self) -> None:
        pass

    def start_turn(self, *_args, **_kwargs):
        raise ValueError("native watch observations do not invoke a model")


def monitor_turn_id(todo: dict[str, Any]) -> str:
    # The native deadline, not a wall-clock tick, names this attempt. Restart
    # observes its original Core request; it never replays an unknown attempt.
    return "monitor." + hashlib.sha256(json.dumps(
        [todo["todo_id"], todo["next_due_at"]], sort_keys=True).encode()).hexdigest()[:40]


class StewardMonitor:
    def __init__(self, controller: Any) -> None:
        self.controller = controller
        self.bindings = controller.project_contexts.conversation_bindings
        self.managed = StewardManagedMode(controller, self.bindings)

    def todos(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        result = list_goal_todos(registry_path=self.controller.registry_path,
            runtime_root_arg=str(self.controller.coordination_runtime_root),
            goal_id=actor["goal_id"], agent_id=actor["agent_id"], role="agent")
        return result["todos"]

    def current(self, actor: dict[str, Any]) -> dict[str, Any] | None:
        rows = [row for row in self.todos(actor) if row.get("target_key") == TARGET]
        if len(rows) > 1:
            raise ValueError("the original native monitor is ambiguous")
        return rows[0] if rows else None

    def _acquire(self, actor: dict[str, Any], todo: dict[str, Any], key: str,
                 *, expected_version: int | None = None) -> dict[str, Any]:
        route = ["--goal-id", actor["goal_id"], "--todo-id", todo["todo_id"]]
        if expected_version is None:
            retained = native_command(self.controller, ["task-lease", "inspect", *route]).get("lease")
            expected_version = retained["version"] if retained else 0
        return native_command(self.controller, ["task-lease", "acquire", *route,
            "--owner", actor["agent_id"], "--idempotency-key", key, "--ttl-seconds", "600",
            "--expected-version", str(expected_version)])

    def _release(self, actor: dict[str, Any], todo: dict[str, Any], lease: dict[str, Any]) -> None:
        native_command(self.controller, ["task-lease", "release", "--goal-id", actor["goal_id"],
            "--todo-id", todo["todo_id"], "--owner", actor["agent_id"],
            "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"])])

    def _commit_poll(self, host, request, turn, context, argv):
        # Configuration and revocation use this original binding fence too.
        # Keep the final scoped admission and native write in one ordering;
        # data collected under an earlier scope cannot become a new result.
        if request["actor"].get("schema_version") == "loopx_managed_session_actor_v0":
            from ...attached_session import managed_session_goal_commit
            commit = managed_session_goal_commit(store=self.controller.store,
                registry_path=self.controller.registry_path, session_id=turn["session_id"])
            with self.controller.lock, commit():
                host._admit(request, turn=self.controller.store.load_turn(turn["session_id"], turn["turn_id"]))
                return self._native_poll(argv)
        with self.controller.lock, exclusive_file_lock(self.bindings.path, operation="commit_steward_monitor"):
            admitted = host._admit(request, turn=self.controller.store.load_turn(turn["session_id"], turn["turn_id"]))
            if not context.get("authorization_scope_id") or admitted["observed_scope_id"] != context["authorization_scope_id"]:
                raise ValueError("the portfolio scope changed after observation")
            return self._native_poll(argv)

    def _native_poll(self, argv):
        """Use the native poll owner; a Chat observation is no heartbeat task.

        The original managed Turn and lease already identify this observation.
        Selecting it through heartbeat advancement qualification would reject
        a due watch whenever unrelated executable work is also available.
        """
        from ...cli import build_parser
        from ...cli_commands.lark_inbox import build_lark_operator_inbox_urgency_projector
        from ...cli_commands.quota_context import prepare_quota_command_context
        from ...control_plane.goals.first_party_host_admission import capture_first_party_host_goal_ref
        from ..explore.composition_frontier import project_live_explore_composition_frontier
        from ...quota import record_quota_monitor_poll
        from .steward_commands import NativeStewardCommandError

        args = build_parser().parse_args(["--format", "json", *argv])

        def current():
            return prepare_quota_command_context(args,
                registry_path=self.controller.registry_path,
                runtime_root_arg=str(self.controller.coordination_runtime_root),
                operator_inbox_urgency_projector_factory=build_lark_operator_inbox_urgency_projector,
                force_projection_refresh=True)

        admission = current()
        goal_ref = capture_first_party_host_goal_ref(
            registry_path=self.controller.registry_path, goal_id=args.goal_id)
        result = record_quota_monitor_poll(admission.status_payload,
            registry_path=self.controller.registry_path, goal_id=args.goal_id,
            agent_id=args.agent_id, available_capabilities=args.available_capabilities,
            scheduler_execution_context=admission.scheduler_context,
            operator_inbox_urgency_projector=admission.operator_inbox_urgency_projector,
            bounded_research_frontier_projector=project_live_explore_composition_frontier,
            status_reloader=lambda: current().status_payload,
            todo_id=args.todo_id, target_key=args.target_key,
            turn_instance_id=args.turn_instance_id,
            result_hash=args.result_hash, material_change=args.material_change,
            task_lease_idempotency_key=args.task_lease_idempotency_key,
            task_lease_expected_version=args.task_lease_expected_version,
            source="adapter", goal_ref=goal_ref, execute=True)
        if result.get("ok") is not True:
            raise NativeStewardCommandError(result)
        return result

    def configure(self, binding_id: str, *, operation: str, cadence_minutes: int = 30,
                  expected_plan_id: str | None = None, execute: bool = False) -> dict[str, Any]:
        session = self.managed._session(binding_id, None)
        saved = session["steward_context"]
        selected = self.bindings.resolve(binding_id=binding_id, session=session,
            include_observation=True, origin="web", source_ref=saved["source_ref"],
            sender_ref=saved["operator_ref"], private_human_message=False, session_context=saved)
        with self.controller.lock, exclusive_file_lock(self.bindings.path, operation="configure_steward_monitor"):
            binding = next(row for row in self.bindings.read()["bindings"] if row["binding_id"] == binding_id)
            if binding != selected["binding"]:
                raise ValueError("the original steward authorization changed")
            actor = binding.get("managed_actor")
            if not actor:
                raise ValueError("prepare the original steward first")
            goal = load_goal_from_registry(self.controller.registry_path, actor["goal_id"])
            plan = self.bindings._core("collaboration.steward.monitor.plan", {
                "binding": binding, "session": self.controller.store.load_session(session["session_id"]),
                "observation": selected["observation"], "workspace_path": str(self.managed._workspace(session)),
                "goal": self.managed._goal(actor["goal_id"]), "goal_active": bool(goal and goal.get("status") == "active" and not goal_is_stopped(goal)),
                "operation": operation, "cadence_minutes": cadence_minutes, "todos": self.todos(actor)})
            if not execute:
                return {"ok": True, "applied": False, "plan": plan}
            if expected_plan_id != plan["plan_id"]:
                raise ValueError("refresh the current native monitor preview")
            todo = plan["todo"]
            if operation == "pause" and todo is None:
                return {"ok": True, "applied": True, "plan": plan}
            if operation == "enable" and todo and todo["status"] == "open" and todo.get("cadence") == f"{cadence_minutes}m":
                return {"ok": True, "applied": True, "plan": plan, "monitor": todo, "model_invoked": False}
            args = ["todo", "update" if todo else "add", "--goal-id", actor["goal_id"],
                "--role", "agent", "--execute"]
            if todo:
                args += ["--agent-id", actor["agent_id"], "--todo-id", todo["todo_id"]]
                if operation == "pause":
                    if todo["status"] == "blocked":
                        return {"ok": True, "applied": True, "plan": plan, "monitor": todo, "model_invoked": False}
                    # This existing native lifecycle atomically pauses the Todo
                    # and retires its current execution lease, including a poll
                    # which has already read the portfolio but not committed.
                    receipt = native_command(self.controller, [*args,
                        "--update-operation-id", "pause." + plan["plan_id"], "--status", "blocked", "--clear-resume-when",
                        "--reason", "Owner paused native monitoring."])
                    getattr(self.controller, "_steward_monitor_next_check", {}).pop(binding_id, None)
                    return {"ok": True, "applied": True, "plan": plan, "receipt": receipt,
                        "monitor": self.current(actor), "model_invoked": False}
                if todo["status"] == "blocked":
                    # Resume preserves history and retires no new work. The
                    # following cadence edit obtains its own fresh generation.
                    native_command(self.controller, [*args, "--update-operation-id", "resume." + plan["plan_id"],
                        "--status", "open", "--clear-resume-when", "--reason", "Owner enabled native monitoring."])
                key = "configure." + plan["plan_id"]
                lease = self._acquire(actor, todo, key)["lease"]
                args += ["--update-operation-id", key, "--status", "open",
                    "--reason", "Owner configured the native monitor cadence.",
                    "--task-lease-idempotency-key", key, "--task-lease-expected-version", str(lease["version"])]
            else:
                args += ["--text", "Observe the original steward's authorized local Goal portfolio; retain coverage gaps and original requests.",
                    "--operation-id", "steward-monitor." + binding_id,
                    "--task-class", "continuous_monitor", "--action-kind", "local_runtime_observe",
                    "--continuation-policy", "same_agent_non_delivery", "--claimed-by", actor["agent_id"],
                    "--required-capability", "filesystem", "--target-key", TARGET, "--watch-only"]
            if operation == "enable":
                # A changed cadence or explicit resume starts a new future
                # window; it cannot alias an accepted/unknown past deadline.
                due = now_utc() + timedelta(minutes=cadence_minutes) if todo else now_utc()
                args += ["--cadence", f"{cadence_minutes}m", "--next-due-at", due.isoformat()]
            try:
                receipt = native_command(self.controller, args)
            finally:
                if todo:
                    self._release(actor, todo, lease)
            getattr(self.controller, "_steward_monitor_next_check", {}).pop(binding_id, None)
            return {"ok": True, "applied": True, "plan": plan, "receipt": receipt,
                "monitor": self.current(actor), "model_invoked": False}

    def observe(self, *, host: Any, request: dict[str, Any], turn: dict[str, Any],
                context: dict[str, Any], todo: dict[str, Any]) -> dict[str, Any]:
        actor = request["actor"]
        route = ["--goal-id", actor["goal_id"], "--agent-id", actor["agent_id"],
            "--todo-id", todo["todo_id"], "--turn-instance-id", request["turn_instance_id"],
            "--available-capability", "filesystem", "--available-capability", "external_evidence_poll",
            "--runtime-profile", "codex_app_server"]
        host._admit(request, turn=turn)
        if actor.get("schema_version") == "loopx_managed_session_actor_v0":
            from .conversation_bindings import ChatConversationBindings
            observation = ChatConversationBindings._core("collaboration.session.monitor.observation", {
                "goal": self.managed._goal(actor["goal_id"]), "actor": actor,
                "todos": self.todos(actor), "previous_hash": todo.get("result_hash")})
        else:
            observation = self.bindings._core("collaboration.steward.monitor.observation", {
                "context": context, "goal_ids": self.bindings.steward_scope(self.controller.store.load_session(actor["session_id"])),
                "actor_goal_id": actor["goal_id"], "previous_hash": todo.get("result_hash")})
        # The native poll owner evaluates fresh gate/due/capability facts. This
        # is an observation in the original Chat Turn, not a request to choose
        # the watch as a heartbeat's advancement task.
        # Freeze admission's generation. A pause/re-enable between collection
        # and acquisition must not turn an old observation into fresh work.
        lease = self._acquire(actor, todo, request["turn_instance_id"],
            expected_version=request["monitor_lease_version"])["lease"]
        try:
            result = self._commit_poll(host, request, turn, context, ["quota", "monitor-poll", *route,
                "--target-key", todo["target_key"], "--result-hash", observation["result_hash"],
                "--task-lease-idempotency-key", request["turn_instance_id"],
                "--task-lease-expected-version", str(lease["version"]), "--execute",
                *(["--material-change"] if observation["material_change"] else [])])
        finally:
            self._release(actor, todo, lease)
        self.controller.store.append_event(turn["session_id"], turn["turn_id"],
            kind="native_loopx.monitor_poll", payload={"observation": observation, "receipt": result})
        return {"schema_version": "loopx_chat_agent_response_v0", "message": "已检查授权 Goal 状态；覆盖缺口和原始请求继续保留。",
            "proposals": [], "gate": None, "native_loopx": {"ok": True, "status": "observed",
                "model_invoked": False, "quota_spent": False, "monitor": observation, "receipt": result}}


def pump_steward_monitors(controller: Any, *, cancelled=lambda: False) -> list[dict[str, Any]]:
    """One cheap frontier check in the existing host; never consume messages."""
    from .steward_turn import StewardTurnHost

    bindings = getattr(getattr(controller, "project_contexts", None), "conversation_bindings", None)
    if controller.closed.is_set():
        return []
    service, changed = StewardMonitor(controller), []
    cache = getattr(controller, "_steward_monitor_next_check", None)
    if cache is None:
        cache = controller._steward_monitor_next_check = {}
    for binding in bindings.read()["bindings"] if bindings is not None else []:
        if cancelled():
            break
        actor = binding.get("managed_actor")
        if binding.get("enabled") is not True or not actor:
            continue
        try:
            session = controller.store.load_session(actor["session_id"])
            if not session or session.get("active_turn_id") or controller.store.queued_turns(actor["session_id"]):
                continue
            if cache.get(binding["binding_id"], 0) > time.monotonic():
                continue
            # An ephemeral wake hint only, never authority. Configuration clears
            # it immediately; external edits are observed within a minute. Every
            # actual dispatch rechecks native state, original grant and provider.
            cache[binding["binding_id"]] = time.monotonic() + 60
            # The existing pump also observes the optional report frontier.
            # No second consumer/timer is installed. Closed/not-due calendars
            # never invoke a model, and original user work wins the idle check.
            goal = load_goal_from_registry(controller.registry_path, actor["goal_id"])
            report_config = (goal or {}).get("control_plane", {}).get("periodic_report", {})
            if report_config.get("enabled") is True and report_config.get("profile_preset") == "portfolio-progress":
                from .steward_reports import StewardReports
                try:
                    accepted, created = StewardReports(controller).frontier(binding)
                    if created:
                        changed.append(accepted)
                        continue
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    _LOG.warning("An original steward report is unavailable; its window and other work are retained")
            todo = service.current(actor)
            if todo is None or not monitor_todo_is_due(todo):
                if todo and todo.get("next_due_at") and todo.get("status") == "open":
                    due = datetime.fromisoformat(todo["next_due_at"].replace("Z", "+00:00"))
                    remaining = max(0, (due - datetime.now(timezone.utc)).total_seconds())
                    cache[binding["binding_id"]] = time.monotonic() + min(60, remaining)
                continue
            accepted, created = StewardTurnHost(controller).submit(binding["binding_id"],
                turn_instance_id=monitor_turn_id(todo), todo_id=todo["todo_id"], work_kind="monitor",
                monitor_deadline=todo["next_due_at"])
            if created:
                changed.append(accepted)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            # One unreadable conversation or unavailable provider must not starve
            # other native monitors; retain its original data and deadline.
            _LOG.warning("A native steward monitor is unavailable; original work retained")
    return changed + pump_managed_session_monitors(controller, cancelled=cancelled)


def pump_managed_session_monitors(controller: Any, *, cancelled=lambda: False) -> list[dict[str, Any]]:
    """Continue explicitly authored native watches through the same idle pump.

    The existing Todo target pins its original Session. Registration or merely
    opening a managed Session never enables a watch. No external binding or
    provider is needed, and no model or task is created by a due observation.
    """
    from .steward_managed_mode import ManagedSessionTurn

    changed, monitor = [], StewardMonitor(controller)
    cache = controller._steward_monitor_next_check
    for session in controller.store.list_sessions():
        if cancelled():
            break
        try:
            if (not session.get("managed_agent_id") or session.get("active_turn_id")
                    or session.get("status") == "closed" or controller.store.queued_turns(session["session_id"])):
                continue
            key = "session." + session["session_id"]
            if cache.get(key, 0) > time.monotonic():
                continue
            cache[key] = time.monotonic() + 60
            service = ManagedSessionTurn(controller)
            request = service._request(session["session_id"], session["managed_agent_id"], "preview")
            service.admit(request)
            rows = [row for row in monitor.todos(request["actor"])
                if row.get("target_key") == "managed-session-watch." + session["session_id"]]
            if len(rows) != 1:
                if rows:
                    raise ValueError("the original managed Session watch is ambiguous")
                continue
            todo = rows[0]
            if not monitor_todo_is_due(todo):
                if todo.get("status") == "open" and todo.get("next_due_at"):
                    due = datetime.fromisoformat(todo["next_due_at"].replace("Z", "+00:00"))
                    cache[key] = time.monotonic() + min(60, max(0, (due - datetime.now(timezone.utc)).total_seconds()))
                continue
            accepted, created = service.submit(session["session_id"], agent_id=session["managed_agent_id"],
                operation_id=monitor_turn_id(todo), todo_id=todo["todo_id"], monitor_deadline=todo["next_due_at"])
            if created:
                changed.append(accepted)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            _LOG.warning("A managed Session watch is unavailable; its original task and deadline are retained")
    return changed
