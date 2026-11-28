"""Signal-monitor observation, snapshot persistence, and follow-up re-arming."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from functools import partial
import inspect
import math
from pathlib import Path
from typing import NamedTuple, Protocol

from ...control_plane.runtime.time import now_utc
from ..issue_fix.github_public import _REPO_PATTERN
from .steward_commands import NativeStewardCommandError, native_command
from .steward_monitor_snapshots import SIGNAL_MONITOR_SNAPSHOT_SCHEMA, read_committed_signal_snapshot
from .steward_monitor_turns import (
    FOLLOWUP_TARGET_PREFIX,
    StewardAcquireLeaseFn,
    StewardControllerProtocol,
    StewardNativeCommandFn,
    StewardReadTodosFn,
    StewardReleaseLeaseFn,
    _coerce_dict,
    is_exhausted_zero_write_followup_todo,
    read_steward_monitor_scope,
)


class _SessionScopeFn(Protocol):
    def __call__(self, actor: Mapping[str, object]) -> list[str] | None: ...


def _github_repo_slug(task_repository: object) -> str | None:
    """Extract an owner/repo slug from a canonical git:github.com task repository."""
    if not isinstance(task_repository, str):
        return None
    raw = task_repository.strip()
    if not raw.startswith("git:github.com/"):
        return None
    slug = raw.removeprefix("git:github.com/")
    return slug if _REPO_PATTERN.fullmatch(slug) else None


def _signal_scope_uses_stalled_age(signal_scope: Mapping[str, object] | None) -> bool:
    """Return True when signal_scope filters PRs by positive min_stalled_hours."""
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


def _accepts_backfill_comments(fn: Callable[..., object]) -> bool:
    """Return True if an injected PR observer accepts the backfill_comments keyword."""
    params = inspect.signature(fn).parameters
    return "backfill_comments" in params or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values()
    )


def _read_pending_inbox_items(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    include_inbox: bool,
) -> list[dict[str, object]] | None:
    if not include_inbox:
        return []
    from ...control_plane.collaboration.inbox import pending as inbox_pending

    try:
        inbox_page = inbox_pending(runtime_root, goal_id=goal_id, agent_id=agent_id)
        return [
            {"request_id": row["request_id"], "source_id": row.get("source_id")}
            for row in inbox_page["items"]
            if isinstance(row, dict) and row.get("request_id")
        ]
    except (OSError, ValueError):
        return None


def _read_repository_snapshot(
    todo: Mapping[str, object],
    signal_scope: Mapping[str, object] | None,
    observe_github_prs: Callable[..., dict[str, object]],
    core: Callable[[str, Mapping[str, object]], Mapping[str, object]],
) -> dict[str, object] | None:
    repo_slug = _github_repo_slug(todo.get("task_repository"))
    if not repo_slug:
        return None
    if signal_scope is None:
        return observe_github_prs(repo_slug)

    def select_backfill(source: str, items: Sequence[Mapping[str, object]], remaining_seconds: float) -> set[int]:
        latest_observation = now_utc() + timedelta(milliseconds=math.ceil(remaining_seconds * 1000))
        planned = core(f"collaboration.steward.monitor.{source}_backfill.plan", {
            "signal_scope": signal_scope,
            "repository_items": items,
            "observed_at": latest_observation.isoformat(),
        }).get("pr_numbers")
        if not isinstance(planned, list) or any(type(number) is not int or number <= 0 for number in planned):
            raise TypeError(f"invalid Steward {source} backfill plan")
        return set(planned)

    parameters = inspect.signature(observe_github_prs).parameters
    options: dict[str, object] = {}
    if "select_review_backfill" in parameters:
        options["select_review_backfill"] = partial(select_backfill, "review")
    if _signal_scope_uses_stalled_age(signal_scope) and _accepts_backfill_comments(observe_github_prs):
        options["backfill_comments"] = True
        if "select_comment_backfill" in parameters:
            options["select_comment_backfill"] = partial(select_backfill, "comment")
    return observe_github_prs(repo_slug, **options)


def _previous_repository_snapshot(snapshot: Mapping[str, object] | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    repository = _coerce_dict(snapshot.get("repository_snapshot"))
    items = repository.get("items")
    if repository.get("status") == "unavailable" or not isinstance(items, list):
        return None
    numbers = [_coerce_dict(row).get("number") for row in items]
    if any(type(number) is not int or not 0 < number < 2**53 for number in numbers):
        return None
    return repository if len(set(numbers)) == len(numbers) else None


def _build_signal_snapshot(
    *,
    goal_id: str,
    todo: Mapping[str, object],
    signal_scope: Mapping[str, object] | None,
    observed_at: str,
    observation: Mapping[str, object],
    repo_snapshot: dict[str, object] | None,
    inbox_items: list[dict[str, object]],
) -> dict[str, object]:
    persisted_repo_snapshot = repo_snapshot
    if isinstance(repo_snapshot, Mapping) and "scoped_repository_items" in observation:
        raw_items = observation["scoped_repository_items"]
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise TypeError("scoped_repository_items must be a sequence")
        persisted_repo_snapshot = {**repo_snapshot, "items": list(raw_items)}
    return {
        "schema_version": SIGNAL_MONITOR_SNAPSHOT_SCHEMA,
        "goal_id": goal_id,
        "todo_id": todo["todo_id"],
        "target_key": todo.get("target_key"),
        "monitor_text": todo.get("text"),
        "task_repository": todo.get("task_repository"),
        **({"signal_scope": signal_scope} if signal_scope is not None else {}),
        "result_hash": observation["result_hash"],
        "material_change": observation["material_change"],
        "observed_at": observed_at,
        **({"repository_observation_incomplete": True}
           if observation.get("repository_observation_incomplete") is True else {}),
        "repository_snapshot": persisted_repo_snapshot,
        "inbox_items": inbox_items,
    }


def _build_followup_successor_args(
    actor: Mapping[str, object],
    todo: Mapping[str, object],
    *,
    target_key: str,
    agent_id: str,
    read_todos: StewardReadTodosFn,
) -> list[str]:
    followup_key = f"{FOLLOWUP_TARGET_PREFIX}{todo['todo_id']}"
    if any(
        row.get("target_key") == followup_key and row.get("status") == "open"
        for row in read_todos(actor)
    ):
        return []
    summary = " ".join(str(todo.get("text") or target_key).split())[:180]
    priority = str(todo.get("priority") or "P1")
    return [
        "--next-agent-todo",
        f"[{target_key}] 信号变更跟进：{summary}",
        "--next-action-kind",
        "triage",
        "--next-continuation-policy",
        "same_agent_non_delivery",
        "--next-claimed-by",
        agent_id,
        "--next-target-key",
        followup_key,
        "--next-agent-priority",
        priority,
    ]


class SignalMonitorCollection(NamedTuple):
    observation: dict[str, object]
    successor_args: list[str]
    snapshot: dict[str, object]


def collect_signal_monitor(
    controller: StewardControllerProtocol,
    *,
    actor: Mapping[str, object],
    context: Mapping[str, object],
    todo: Mapping[str, object],
    session_scope_for_actor: _SessionScopeFn,
    read_todos: StewardReadTodosFn,
    observe_github_prs: Callable[..., dict[str, object]],
) -> SignalMonitorCollection:
    """Evaluate a scoped signal monitor and prepare its candidate snapshot."""
    target_key = str(todo.get("target_key") or "")
    goal_id = str(actor["goal_id"])
    agent_id = str(actor["agent_id"])
    runtime_root = controller.coordination_runtime_root
    bindings = controller.project_contexts.conversation_bindings
    signal_scope = read_steward_monitor_scope(runtime_root, goal_id, target_key)
    include_portfolio = (
        True if signal_scope is None else signal_scope.get("include_portfolio", True) is True
    )
    include_inbox = (
        True if signal_scope is None else signal_scope.get("include_inbox", True) is True
    )
    portfolio = (
        bindings._core(
            "collaboration.steward.monitor.observation",
            {
                "context": _coerce_dict(context),
                "goal_ids": session_scope_for_actor(actor),
                "actor_goal_id": goal_id,
                "previous_hash": None,
            },
        )
        if include_portfolio
        else None
    )
    inbox_items = _read_pending_inbox_items(
        runtime_root, goal_id=goal_id, agent_id=agent_id, include_inbox=include_inbox
    )
    repo_snapshot = _read_repository_snapshot(todo, signal_scope, observe_github_prs, bindings._core)
    previous_repository_snapshot = (
        _previous_repository_snapshot(
            read_committed_signal_snapshot(runtime_root, controller.registry_path, goal_id, todo, signal_scope)
        )
        if repo_snapshot is not None and (include_portfolio or include_inbox)
        else None
    )
    observed_at = now_utc().isoformat()
    signal_request: dict[str, object] = {
        "portfolio_observation": portfolio,
        "inbox_items": inbox_items,
        "repository_snapshot": repo_snapshot,
        "previous_hash": todo.get("result_hash"),
    }
    if previous_repository_snapshot is not None:
        signal_request["previous_repository_snapshot"] = previous_repository_snapshot
    if signal_scope is not None:
        signal_request["signal_scope"] = signal_scope
        signal_request["observed_at"] = observed_at
    observation = bindings._core(
        "collaboration.steward.monitor.signal_observation", signal_request
    )
    if observation.get("observation_incomplete") is True:
        raise RuntimeError("steward monitor external signal source temporarily unavailable")
    if observation.get("repository_observation_incomplete") is True:
        repo_snapshot = previous_repository_snapshot
    snapshot = _build_signal_snapshot(
        goal_id=goal_id,
        todo=todo,
        signal_scope=signal_scope,
        observed_at=observed_at,
        observation=observation,
        repo_snapshot=repo_snapshot,
        inbox_items=inbox_items or [],
    )
    successor_args = (
        _build_followup_successor_args(
            actor,
            todo,
            target_key=target_key,
            agent_id=agent_id,
            read_todos=read_todos,
        )
        if observation.get("material_change")
        else []
    )
    return SignalMonitorCollection(dict(observation), successor_args, snapshot)


def rearm_exhausted_followup_if_needed(
    controller: StewardControllerProtocol,
    *,
    actor: Mapping[str, object],
    monitor_todo: Mapping[str, object],
    poll_turn_instance_id: str,
    read_todos: StewardReadTodosFn,
    acquire_lease: StewardAcquireLeaseFn,
    release_lease: StewardReleaseLeaseFn,
    run_command: StewardNativeCommandFn = native_command,
) -> bool:
    """Touch an exhausted zero-write follow-up Todo so later monitor ticks re-dispatch."""
    followup_key = f"{FOLLOWUP_TARGET_PREFIX}{monitor_todo['todo_id']}"
    followup = next(
        (
            row
            for row in read_todos(actor)
            if row.get("target_key") == followup_key and row.get("status") == "open"
        ),
        None,
    )
    if followup is None or not is_exhausted_zero_write_followup_todo(
        controller.store, actor, followup
    ):
        return False
    rearm_key = f"rearm-followup.{poll_turn_instance_id}"
    rearm_note = f"rearm-followup:{poll_turn_instance_id}"
    if str(followup.get("note") or "") == rearm_note:
        return False
    conflicts = {"todo_lease_conflict", "version_mismatch"}
    lease: dict[str, object] | None = None
    try:
        raw_lease = acquire_lease(actor, followup, rearm_key)["lease"]
        if not isinstance(raw_lease, Mapping):
            raise ValueError("followup task lease unavailable")
        lease = _coerce_dict(raw_lease)
        run_command(
            controller,
            [
                "todo",
                "update",
                "--goal-id",
                str(actor["goal_id"]),
                "--role",
                "agent",
                "--agent-id",
                str(actor["agent_id"]),
                "--todo-id",
                followup["todo_id"],
                "--update-operation-id",
                rearm_key,
                "--note",
                rearm_note,
                "--task-lease-idempotency-key",
                rearm_key,
                "--task-lease-expected-version",
                str(lease["version"]),
                "--execute",
            ],
        )
    except NativeStewardCommandError as exc:
        if exc.payload.get("error_code") in conflicts:
            return False
        raise
    finally:
        if lease is not None:
            try:
                release_lease(actor, followup, lease)
            except NativeStewardCommandError as exc:
                # A renewed lease belongs to its current generation. Preserve the update's outcome.
                if exc.payload.get("error_code") not in conflicts:
                    raise
    return True
