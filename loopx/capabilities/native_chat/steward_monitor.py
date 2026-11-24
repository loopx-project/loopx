"""Native monitor Todo + the existing Chat host, without a second scheduler.

The Todo owns enable/pause, cadence and observation state. The existing host's
cheap wake pump checks its due projection; the original Chat worker performs
the observation and the native monitor-poll owner settles it without a model
or quota spend. Reports remain a separate periodic-report integration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import logging
import math
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Mapping

from ...agent_registry import load_goal_from_registry
from ...chat_store import _atomic_write_json
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.scheduler.monitor_todo import monitor_todo_is_due
from ...control_plane.todos.list_readback import list_goal_todos
from ...control_plane.runtime.time import now_utc
from ...control_plane.goals.activation import goal_is_stopped
from ...file_lock import exclusive_file_lock
from ..issue_fix.repository_snapshot import _REPO_PATTERN
from .github_pr_evidence import observe_github_repository_prs
from .steward_commands import native_command
from .steward_managed_mode import StewardManagedMode

TARGET = "steward-portfolio-watch"
FOLLOWUP_TARGET_PREFIX = "steward-monitor-followup:"
STEWARD_BACKGROUND_SESSION_PREFIX = "steward.background."
STEWARD_BACKGROUND_CHANNEL_PREFIX = "manager.external.steward.background."
ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS = 60
ADVANCEMENT_ADMISSION_RETRY_SUFFIX = ".admission-retry"
ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX = ".precommit-retry"
_LOG = logging.getLogger(__name__)


def steward_background_session_id(binding_id: str) -> str:
    return f"{STEWARD_BACKGROUND_SESSION_PREFIX}{binding_id}"


def steward_background_channel_id(binding_id: str) -> str:
    return f"{STEWARD_BACKGROUND_CHANNEL_PREFIX}{binding_id}"


def actor_steward_binding_id(actor: Mapping[str, Any]) -> str | None:
    if actor.get("schema_version") != "loopx_steward_managed_actor_v0":
        return None
    goal_id = str(actor.get("goal_id") or "")
    if not goal_id.startswith("steward-agent-"):
        return None
    return goal_id.removeprefix("steward-agent-") or None


def lookup_steward_monitor_turn(
    store: Any, binding_id: str | None, fallback_session_id: str, client_id: str,
) -> dict[str, Any] | None:
    if binding_id is not None:
        bg_sid = steward_background_session_id(binding_id)
        if store.load_session(bg_sid) is not None:
            found = store.turn_for_client(bg_sid, client_id)
            if found is not None:
                return found
    return store.turn_for_client(fallback_session_id, client_id)


def load_steward_monitor_turn_by_id(
    store: Any, actor: Mapping[str, Any], turn_id: str,
) -> dict[str, Any] | None:
    binding_id = actor_steward_binding_id(actor)
    if binding_id is not None:
        bg_sid = steward_background_session_id(binding_id)
        if store.load_session(bg_sid) is not None:
            found = store.load_turn(bg_sid, turn_id)
            if found is not None:
                return found
    return store.load_turn(actor["session_id"], turn_id)


def ensure_steward_background_session(
    store: Any, binding_id: str, parent_session: Mapping[str, Any],
) -> dict[str, Any]:
    from ...chat_store import utc_now

    saved = parent_session.get("steward_context")
    if not isinstance(saved, Mapping):
        raise ValueError("the original steward Session is unavailable")
    bg_sid = steward_background_session_id(binding_id)
    if store.load_session(bg_sid) is None:
        try:
            store.create_session(
                session_id=bg_sid,
                goal_id="loopx-manager",
                agent_id=str(parent_session["agent_id"]),
                adapter_kind=str(parent_session["adapter_kind"]),
                upstream_thread_id=f"steward-watch-{binding_id}",
                channel_id=steward_background_channel_id(binding_id),
                executor_endpoint_id=parent_session.get("executor_endpoint_id"),
                steward_context=None,
            )
        except FileExistsError:
            pass
    parent_scope_id = parent_session.get("manager_authorization_scope_id")
    expected_bg_context = dict(saved)
    with store._locked_session_path(bg_sid, "ensure_steward_background_session") as path:
        carrier = store.load_session(bg_sid)
        if carrier is None or carrier.get("status") == "closed":
            raise ValueError("the steward background watch Session is unavailable")
        dirty = False
        if carrier.get("steward_background_context") != expected_bg_context:
            carrier["steward_background_context"] = expected_bg_context
            dirty = True
        if isinstance(parent_scope_id, str) and parent_scope_id and carrier.get("manager_authorization_scope_id") != parent_scope_id:
            carrier["manager_authorization_scope_id"] = parent_scope_id
            dirty = True
        elif parent_scope_id is None and "manager_authorization_scope_id" in carrier:
            carrier.pop("manager_authorization_scope_id", None)
            dirty = True
        if dirty:
            carrier["updated_at"] = utc_now()
            _atomic_write_json(path, carrier, preserve_mode=True)
        return carrier


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


def advancement_turn_id(todo: dict[str, Any]) -> str:
    """Stable Turn instance identity per Todo revision for Steward follow-ups."""
    stamp = todo.get("updated_at") or todo.get("created_at") or "0"
    note = str(todo.get("note") or "")
    basis: list[Any] = [todo["todo_id"], stamp]
    if note.startswith("rearm-followup:"):
        basis.append(note)
    return "advancement." + hashlib.sha256(json.dumps(
        basis, sort_keys=True).encode()).hexdigest()[:40]


def _zero_write_advancement_response(
    turn: Mapping[str, Any],
    *,
    now: float | None = None,
) -> Mapping[str, Any] | None:
    """Return native_loopx payload only when a completed Turn wrote zero Steward outputs and passed cooldown."""
    from .steward_turn import _iso_timestamp_seconds

    if turn.get("status") != "completed":
        return None
    resp = turn.get("response")
    if not isinstance(resp, Mapping):
        return None
    if (resp.get("steward_owner_report_queued")
            or resp.get("context_handoff")
            or resp.get("steward_user_action_receipts")):
        return None
    nl = resp.get("native_loopx")
    if not isinstance(nl, Mapping) or nl.get("ok") is not False:
        return None
    completed_ts = _iso_timestamp_seconds(turn.get("completed_at"))
    if completed_ts is None:
        return None
    current_ts = time.time() if now is None else now
    if current_ts - completed_ts < ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS:
        return None
    return nl


def _is_precommit_retryable_advancement_turn(
    turn: Mapping[str, Any],
    *,
    now: float | None = None,
) -> bool:
    """Allow one delayed retry only when zero kernel/Steward writes occurred."""
    nl = _zero_write_advancement_response(turn, now=now)
    if nl is None or nl.get("status") != "failed":
        return False
    if nl.get("result_kind") not in {"validation_failed", "host_failure"}:
        return False
    receipt = nl.get("receipt")
    commit_eligibility = (receipt.get("commit_eligibility") if isinstance(receipt, Mapping) else None) or {}
    effects = nl.get("effects") or {}
    if not isinstance(commit_eligibility, Mapping) or not isinstance(effects, Mapping):
        return False
    if not (commit_eligibility.get("writeback") is False or effects.get("state_written") is False):
        return False
    return not (
        effects.get("state_written") is True
        or effects.get("quota_spent") is True
        or effects.get("scheduler_acknowledged") is True
    )


def _is_zero_effect_selection_deferred_advancement_turn(
    turn: Mapping[str, Any],
    *,
    now: float | None = None,
) -> bool:
    """Allow bounded retry only for zero-effect pre-host selection deferrals."""
    nl = _zero_write_advancement_response(turn, now=now)
    if nl is None or nl.get("error_code") != "turn_todo_selection_deferred":
        return False
    refusal = nl.get("selection_rejection")
    if not isinstance(refusal, Mapping) or refusal.get("state") != "deferred":
        return False
    effects = nl.get("effects")
    if not isinstance(effects, Mapping):
        return False
    return not any(
        effects.get(key) is not False
        for key in ("host_invoked", "state_written", "quota_spent", "scheduler_acknowledged")
    )


def resolve_advancement_turn_id(
    store: Any,
    actor: Mapping[str, Any],
    todo: dict[str, Any],
    *,
    now: float | None = None,
) -> str | None:
    """Resolve the active attempt id or bounded post-cooldown deferral/pre-commit retries."""
    from .steward_turn import native_steward_client_id

    base = advancement_turn_id(todo)
    existing = store.turn_for_client(
        actor["session_id"],
        native_steward_client_id(actor, base),
    )
    if existing is None:
        return base
    primary_token = base
    if _is_zero_effect_selection_deferred_advancement_turn(existing, now=now):
        primary_token = base + ADVANCEMENT_ADMISSION_RETRY_SUFFIX
        existing = store.turn_for_client(
            actor["session_id"],
            native_steward_client_id(actor, primary_token),
        )
        if existing is None:
            return primary_token
    if not _is_precommit_retryable_advancement_turn(existing, now=now):
        return None
    retry_token = primary_token + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX
    retry_existing = store.turn_for_client(
        actor["session_id"],
        native_steward_client_id(actor, retry_token),
    )
    if retry_existing is None:
        return retry_token
    if _is_zero_effect_selection_deferred_advancement_turn(retry_existing, now=now):
        retry_admission_token = retry_token + ADVANCEMENT_ADMISSION_RETRY_SUFFIX
        if store.turn_for_client(
            actor["session_id"],
            native_steward_client_id(actor, retry_admission_token),
        ) is None:
            return retry_admission_token
    return None


def is_exhausted_zero_write_followup_todo(
    store: Any,
    actor: Mapping[str, Any],
    todo: dict[str, Any],
    *,
    now: float | None = None,
) -> bool:
    """Return True when an open monitor follow-up exhausted its per-revision retries with zero writes."""
    from .steward_turn import native_steward_client_id

    if (
        todo.get("status") != "open"
        or todo.get("task_class") != "advancement_task"
        or not str(todo.get("target_key") or "").startswith(FOLLOWUP_TARGET_PREFIX)
    ):
        return False
    if resolve_advancement_turn_id(store, actor, todo, now=now) is not None:
        return False
    base = advancement_turn_id(todo)
    suffixes = (
        "",
        ADVANCEMENT_ADMISSION_RETRY_SUFFIX,
        ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX,
        ADVANCEMENT_ADMISSION_RETRY_SUFFIX + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX,
        ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX + ADVANCEMENT_ADMISSION_RETRY_SUFFIX,
    )
    found_any = False
    for suffix in suffixes:
        turn = store.turn_for_client(
            actor["session_id"],
            native_steward_client_id(actor, base + suffix),
        )
        if turn is None:
            continue
        found_any = True
        if not (
            _is_precommit_retryable_advancement_turn(turn, now=now)
            or _is_zero_effect_selection_deferred_advancement_turn(turn, now=now)
        ):
            return False
    return found_any


def _github_repo_slug(task_repository: Any) -> str | None:
    if not isinstance(task_repository, str):
        return None
    raw = task_repository.strip()
    if not raw.startswith("git:github.com/"):
        return None
    slug = raw.removeprefix("git:github.com/")
    return slug if _REPO_PATTERN.fullmatch(slug) else None


def _observe_github_repository_prs(
    repo_slug: str, *, backfill_comments: bool = False
) -> dict[str, Any]:
    """Read bounded public/authorized open PR signal metadata without side effects."""
    return observe_github_repository_prs(
        repo_slug, runner=subprocess.run, backfill_comments=backfill_comments
    )


def _signal_scope_uses_stalled_age(signal_scope: Mapping[str, Any] | None) -> bool:
    if not isinstance(signal_scope, Mapping):
        return False
    pr_filter = signal_scope.get("pr_filter")
    if not isinstance(pr_filter, Mapping):
        return False
    min_stalled_hours = pr_filter.get("min_stalled_hours")
    return (
        isinstance(min_stalled_hours, (int, float))
        and not isinstance(min_stalled_hours, bool)
        and math.isfinite(float(min_stalled_hours))
        and float(min_stalled_hours) > 0
    )


def _accepts_backfill_comments(fn: Callable[..., Any]) -> bool:
    params = inspect.signature(fn).parameters
    return "backfill_comments" in params or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values()
    )


_MONITOR_TARGET_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")


def steward_monitor_snapshot_path(runtime_root: Path, goal_id: str, todo_id: str) -> Path:
    return Path(runtime_root).expanduser().resolve() / "steward-monitors" / goal_id / f"{todo_id}.latest.json"


def steward_monitor_scope_path(runtime_root: Path, goal_id: str, target_key: str) -> Path:
    safe_goal = str(goal_id or "").strip()
    safe_key = str(target_key or "").strip()
    if not safe_goal or "/" in safe_goal or "\\" in safe_goal or ".." in safe_goal:
        raise ValueError("invalid monitor scope goal_id")
    if not _MONITOR_TARGET_KEY_RE.fullmatch(safe_key):
        raise ValueError("invalid monitor scope target_key")
    return Path(runtime_root).expanduser().resolve() / "steward-monitors" / safe_goal / "scopes" / f"{safe_key}.json"


def steward_monitor_delivery_dir(runtime_root: Path, goal_id: str) -> Path:
    safe_goal = str(goal_id or "").strip()
    if not safe_goal or "/" in safe_goal or "\\" in safe_goal or ".." in safe_goal:
        raise ValueError("invalid monitor delivery goal_id")
    return Path(runtime_root).expanduser().resolve() / "steward-monitors" / safe_goal / "deliveries"


def normalize_steward_monitor_scope(signal_scope: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if signal_scope is None:
        return None
    normalized = effect_runtime_result(
        "collaboration.steward.monitor.signal_scope.normalize",
        {"signal_scope": dict(signal_scope)},
    ).get("signal_scope")
    return dict(normalized) if isinstance(normalized, Mapping) else None


def write_steward_monitor_scope(
    runtime_root: Path, goal_id: str, target_key: str, signal_scope: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = normalize_steward_monitor_scope(signal_scope)
    if normalized is None:
        raise ValueError("signal_scope cannot be empty")
    path = steward_monitor_scope_path(runtime_root, goal_id, target_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, normalized)
    return normalized


def read_steward_monitor_scope(runtime_root: Path, goal_id: str, target_key: str) -> dict[str, Any] | None:
    if not target_key or not _MONITOR_TARGET_KEY_RE.fullmatch(str(target_key)):
        return None
    path = steward_monitor_scope_path(runtime_root, goal_id, target_key)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("invalid persisted steward monitor scope")
    return normalize_steward_monitor_scope(raw)


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

    def due_monitors(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        self.current(actor)
        rows = [
            row for row in self.todos(actor)
            if row.get("task_class") == "continuous_monitor"
            and row.get("claimed_by") == actor["agent_id"]
            and isinstance(row.get("target_key"), str)
            and row["target_key"]
            and not row["target_key"].startswith("managed-session-watch.")
            and not row["target_key"].startswith(FOLLOWUP_TARGET_PREFIX)
            and monitor_todo_is_due(row)
        ]
        return sorted(rows, key=lambda row: (str(row.get("next_due_at") or ""), str(row["todo_id"])))

    def runnable_advancement_todos(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [
            row for row in self.todos(actor)
            if row.get("status") == "open"
            and row.get("task_class") == "advancement_task"
            and row.get("action_kind") != "generate_periodic_report"
            and row.get("claimed_by") == actor["agent_id"]
            and isinstance(row.get("target_key"), str)
            and row["target_key"].startswith(FOLLOWUP_TARGET_PREFIX)
            and row.get("resume_ready") is not False
        ]
        prio = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
        return sorted(rows, key=lambda row: (
            prio.get(str(row.get("priority") or "P1"), 9),
            str(row.get("created_at") or ""),
            str(row["todo_id"]),
        ))

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
        observed = host._observe(request["actor"])
        with self.controller.lock, exclusive_file_lock(self.bindings.path, operation="commit_steward_monitor"):
            admitted = host._admit(request, observed=observed,
                turn=self.controller.store.load_turn(turn["session_id"], turn["turn_id"]))
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
            next_agent_todo=getattr(args, "next_agent_todo", None),
            next_action_kind=getattr(args, "next_action_kind", None),
            next_task_repository=getattr(args, "next_task_repository", None),
            next_required_capabilities=getattr(args, "next_required_capabilities", None),
            next_continuation_policy=getattr(args, "next_continuation_policy", None),
            next_target_key=getattr(args, "next_target_key", None),
            next_claimed_by=getattr(args, "next_claimed_by", None),
            next_agent_priority=getattr(args, "next_agent_priority", None),
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

    def _collect_signal_monitor(self, actor: dict[str, Any], context: dict[str, Any], todo: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        target_key = str(todo.get("target_key") or "")
        signal_scope = read_steward_monitor_scope(
            self.controller.coordination_runtime_root, actor["goal_id"], target_key,
        )
        include_portfolio = True if signal_scope is None else signal_scope.get("include_portfolio", True) is True
        include_inbox = True if signal_scope is None else signal_scope.get("include_inbox", True) is True
        portfolio = (
            self.bindings._core("collaboration.steward.monitor.observation", {
                "context": context, "goal_ids": self.bindings.steward_scope(self.controller.store.load_session(actor["session_id"])),
                "actor_goal_id": actor["goal_id"], "previous_hash": None})
            if include_portfolio else None
        )
        from ...control_plane.collaboration.inbox import pending as inbox_pending
        if include_inbox:
            try:
                inbox_raw = inbox_pending(self.controller.coordination_runtime_root,
                    goal_id=actor["goal_id"], agent_id=actor["agent_id"])
                inbox_items = [{"request_id": row["request_id"], "source_id": row.get("source_id")}
                               for row in inbox_raw if isinstance(row, dict) and row.get("request_id")]
            except (OSError, ValueError):
                inbox_items = []
        else:
            inbox_items = []
        repo_slug = _github_repo_slug(todo.get("task_repository"))
        backfill_comments = _signal_scope_uses_stalled_age(signal_scope)
        if not repo_slug:
            repo_snapshot = None
        elif backfill_comments and _accepts_backfill_comments(_observe_github_repository_prs):
            repo_snapshot = _observe_github_repository_prs(repo_slug, backfill_comments=True)
        else:
            repo_snapshot = _observe_github_repository_prs(repo_slug)
        observed_at = now_utc().isoformat()
        signal_request: dict[str, Any] = {
            "portfolio_observation": portfolio,
            "inbox_items": inbox_items,
            "repository_snapshot": repo_snapshot,
            "previous_hash": todo.get("result_hash"),
        }
        if signal_scope is not None:
            signal_request["signal_scope"] = signal_scope
            signal_request["observed_at"] = observed_at
        observation = self.bindings._core("collaboration.steward.monitor.signal_observation", signal_request)
        if observation.get("observation_incomplete") is True:
            raise RuntimeError("steward monitor external signal source temporarily unavailable")
        persisted_repo_snapshot = repo_snapshot
        if isinstance(repo_snapshot, Mapping) and "scoped_repository_items" in observation:
            persisted_repo_snapshot = {**repo_snapshot, "items": list(observation["scoped_repository_items"])}
        snapshot_path = steward_monitor_snapshot_path(
            self.controller.coordination_runtime_root, actor["goal_id"], todo["todo_id"])
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(snapshot_path, {
            "schema_version": "steward_signal_monitor_snapshot_v0",
            "goal_id": actor["goal_id"],
            "todo_id": todo["todo_id"],
            "target_key": todo.get("target_key"),
            "monitor_text": todo.get("text"),
            "task_repository": todo.get("task_repository"),
            **({"signal_scope": signal_scope} if signal_scope is not None else {}),
            "result_hash": observation["result_hash"],
            "material_change": observation["material_change"],
            "observed_at": observed_at,
            "repository_snapshot": persisted_repo_snapshot,
            "inbox_items": inbox_items,
        })
        followup_key = f"{FOLLOWUP_TARGET_PREFIX}{todo['todo_id']}"
        existing_followup = any(
            row.get("target_key") == followup_key and row.get("status") == "open"
            for row in self.todos(actor)
        )
        successor_args: list[str] = []
        if observation["material_change"] and not existing_followup:
            summary = " ".join(str(todo.get("text") or todo["target_key"]).split())[:180]
            priority = str(todo.get("priority") or "P1")
            successor_args = [
                "--next-agent-todo", f"[{todo['target_key']}] 信号变更跟进：{summary}",
                "--next-action-kind", "triage",
                "--next-continuation-policy", "same_agent_non_delivery",
                "--next-claimed-by", actor["agent_id"],
                "--next-target-key", followup_key,
                "--next-agent-priority", priority,
            ]
        return observation, successor_args

    def _rearm_exhausted_followup_if_needed(
        self, actor: dict[str, Any], monitor_todo: dict[str, Any], poll_turn_instance_id: str,
    ) -> bool:
        followup_key = f"{FOLLOWUP_TARGET_PREFIX}{monitor_todo['todo_id']}"
        followup = next(
            (
                row for row in self.todos(actor)
                if row.get("target_key") == followup_key and row.get("status") == "open"
            ),
            None,
        )
        if followup is None or not is_exhausted_zero_write_followup_todo(
            self.controller.store, actor, followup,
        ):
            return False
        rearm_key = f"rearm-followup.{poll_turn_instance_id}"
        rearm_note = f"rearm-followup:{poll_turn_instance_id}"
        if str(followup.get("note") or "") == rearm_note:
            return False
        lease = self._acquire(actor, followup, rearm_key)["lease"]
        try:
            native_command(self.controller, [
                "todo", "update",
                "--goal-id", actor["goal_id"],
                "--role", "agent",
                "--agent-id", actor["agent_id"],
                "--todo-id", followup["todo_id"],
                "--update-operation-id", rearm_key,
                "--note", rearm_note,
                "--task-lease-idempotency-key", rearm_key,
                "--task-lease-expected-version", str(lease["version"]),
                "--execute",
            ])
        finally:
            self._release(actor, followup, lease)
        return True

    def observe(self, *, host: Any, request: dict[str, Any], turn: dict[str, Any],
                context: dict[str, Any], todo: dict[str, Any]) -> dict[str, Any]:
        actor = request["actor"]
        route = ["--goal-id", actor["goal_id"], "--agent-id", actor["agent_id"],
            "--todo-id", todo["todo_id"], "--turn-instance-id", request["turn_instance_id"],
            "--available-capability", "filesystem", "--available-capability", "external_evidence_poll",
            "--runtime-profile", "codex_app_server"]
        host._admit(request, turn=turn)
        successor_args: list[str] = []
        if actor.get("schema_version") == "loopx_managed_session_actor_v0":
            from .conversation_bindings import ChatConversationBindings
            observation = ChatConversationBindings._core("collaboration.session.monitor.observation", {
                "goal": self.managed._goal(actor["goal_id"]), "actor": actor,
                "todos": self.todos(actor), "previous_hash": todo.get("result_hash")})
        elif todo.get("target_key") != TARGET:
            observation, successor_args = self._collect_signal_monitor(actor, context, todo)
        else:
            observation = self.bindings._core("collaboration.steward.monitor.observation", {
                "context": context, "goal_ids": self.bindings.steward_scope(self.controller.store.load_session(actor["session_id"])),
                "actor_goal_id": actor["goal_id"], "previous_hash": todo.get("result_hash")})
        # The native poll owner evaluates fresh gate/due/capability facts. This
        # is an observation in the original Chat Turn, not a request to choose
        # the watch as a heartbeat's advancement task.
        # Freeze admission's generation. A pause/re-enable between collection
        # and acquisition must not turn an old observation into fresh work.
        # A new carrier cannot borrow an old acquire receipt after that lease
        # expired or was released. The native poll identity stays unchanged.
        recovery = request.get("monitor_restart_recovery")
        lease_key = "monitor-recovery." + recovery["original_turn_id"] if recovery else request["turn_instance_id"]
        lease = self._acquire(actor, todo, lease_key,
            expected_version=request["monitor_lease_version"])["lease"]
        try:
            result = self._commit_poll(host, request, turn, context, ["quota", "monitor-poll", *route,
                "--target-key", todo["target_key"], "--result-hash", observation["result_hash"],
                "--task-lease-idempotency-key", lease_key,
                "--task-lease-expected-version", str(lease["version"]), "--execute",
                *(["--material-change"] if observation["material_change"] else []),
                *successor_args])
        finally:
            self._release(actor, todo, lease)
        rearmed_followup = False
        if (
            actor.get("schema_version") != "loopx_managed_session_actor_v0"
            and todo.get("target_key") != TARGET
            and not successor_args
        ):
            rearmed_followup = self._rearm_exhausted_followup_if_needed(
                actor, todo, request["turn_instance_id"],
            )
        if successor_args or rearmed_followup:
            binding_id = actor["goal_id"].removeprefix("steward-agent-")
            getattr(self.controller, "_steward_monitor_next_check", {}).pop(binding_id, None)
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
    fg_checked = getattr(controller, "_steward_monitor_fg_idle_checked", None)
    if fg_checked is None:
        fg_checked = controller._steward_monitor_fg_idle_checked = {}
    for binding in bindings.read()["bindings"] if bindings is not None else []:
        if cancelled():
            break
        actor = binding.get("managed_actor")
        if binding.get("enabled") is not True or not actor:
            continue
        try:
            parent_session = controller.store.load_session(actor["session_id"])
            if not parent_session or parent_session.get("status") == "closed":
                continue
            human_idle = (
                not parent_session.get("active_turn_id")
                and not controller.store.queued_turns(actor["session_id"])
            )
            binding_id = binding["binding_id"]
            bg_session_id = StewardTurnHost.background_session_id(binding_id)
            bg_session = controller.store.load_session(bg_session_id)
            if (
                bg_session is not None
                and bg_session.get("active_turn_id")
                and callable(getattr(controller, "recover_exited_turn", None))
            ):
                controller.recover_exited_turn(bg_session_id)
                bg_session = controller.store.load_session(bg_session_id)
            if bg_session is not None and bg_session.get("status") == "closed":
                bg_idle = False
            else:
                bg_idle = (
                    bg_session is None
                    or (
                        not bg_session.get("active_turn_id")
                        and not controller.store.queued_turns(bg_session_id)
                    )
                )
            if not human_idle and not bg_idle:
                continue
            now_mono = time.monotonic()
            bypass_fg_throttle = (
                human_idle
                and binding_id in cache
                and fg_checked.get(binding_id) is False
            )
            if not bypass_fg_throttle and cache.get(binding_id, 0) > now_mono:
                continue
            # An ephemeral wake hint only, never authority. Configuration clears
            # it immediately; external edits are observed within a minute. Every
            # actual dispatch rechecks native state, original grant and provider.
            cache[binding_id] = now_mono + 60
            fg_checked[binding_id] = human_idle
            # The existing pump also observes the optional report frontier.
            # No second consumer/timer is installed. Closed/not-due calendars
            # never invoke a model, and original user work wins the idle check.
            if human_idle:
                goal = load_goal_from_registry(controller.registry_path, actor["goal_id"])
                report_config = (goal or {}).get("control_plane", {}).get("periodic_report", {})
                if report_config.get("enabled") is True and report_config.get("profile_preset") == "portfolio-progress":
                    from .steward_reports import StewardReports
                    try:
                        accepted, created = StewardReports(controller).frontier(binding)
                        if created:
                            changed.append(accepted)
                            if bg_idle:
                                cache.pop(binding_id, None)
                            continue
                    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                        _LOG.warning("An original steward report is unavailable; its window and other work are retained")
            if bg_idle:
                due = service.due_monitors(actor)
                if due:
                    dispatched = False
                    for todo in due:
                        try:
                            accepted, created = StewardTurnHost(controller).submit(
                                binding_id,
                                turn_instance_id=monitor_turn_id(todo),
                                todo_id=todo["todo_id"],
                                work_kind="monitor",
                                monitor_deadline=todo["next_due_at"],
                            )
                        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                            _LOG.warning(
                                "A native steward monitor is unavailable; original work retained"
                            )
                            continue
                        if created:
                            changed.append(accepted)
                            dispatched = True
                            break
                        if isinstance(accepted, dict) and accepted.get("status") in {"queued", "running"}:
                            dispatched = True
                            break
                    if dispatched:
                        if human_idle:
                            fg_checked[binding_id] = False
                        continue
            advancement_dispatched = False
            if human_idle:
                for adv in service.runnable_advancement_todos(actor):
                    turn_id_token = resolve_advancement_turn_id(controller.store, actor, adv)
                    if turn_id_token is None:
                        continue
                    accepted, created = StewardTurnHost(controller).submit(binding_id,
                        turn_instance_id=turn_id_token, todo_id=adv["todo_id"], work_kind="native_turn")
                    if created:
                        changed.append(accepted)
                    advancement_dispatched = True
                    break
            if not advancement_dispatched:
                all_monitors = [row for row in service.todos(actor)
                    if row.get("task_class") == "continuous_monitor" and row.get("status") == "open"
                    and isinstance(row.get("target_key"), str)
                    and not row["target_key"].startswith("managed-session-watch.")
                    and not row["target_key"].startswith(FOLLOWUP_TARGET_PREFIX)
                    and row.get("next_due_at")]
                if all_monitors:
                    soonest = min(all_monitors, key=lambda row: datetime.fromisoformat(row["next_due_at"].replace("Z", "+00:00")))
                    due_dt = datetime.fromisoformat(soonest["next_due_at"].replace("Z", "+00:00"))
                    remaining = (due_dt - datetime.now(timezone.utc)).total_seconds()
                    if remaining > 0:
                        cache[binding_id] = time.monotonic() + min(60, remaining)
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
            goal = load_goal_from_registry(controller.registry_path, session["goal_id"])
            report_config = (goal or {}).get("control_plane", {}).get("periodic_report", {})
            if (report_config.get("enabled") is True and report_config.get("profile_preset") == "session-task"
                    and report_config.get("route_ref") == "managed-session:" + session["session_id"]):
                from .session_calendar_tasks import SessionCalendarTasks
                try:
                    accepted, created = SessionCalendarTasks(controller).frontier(session["session_id"])
                    if created:
                        changed.append(accepted)
                        continue
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    _LOG.warning("A managed calendar task is unavailable; its original window and work are retained")
            if (report_config.get("enabled") is True and report_config.get("profile_preset") == "weekly-progress"
                    and report_config.get("route_ref") == "managed-session:" + session["session_id"]):
                from .steward_reports import ManagedSessionReports
                try:
                    accepted, created = ManagedSessionReports(controller).frontier(session["session_id"])
                    if created:
                        changed.append(accepted)
                        continue
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    _LOG.warning("A managed Session report is unavailable; its original window and other work are retained")
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
