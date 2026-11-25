"""Native monitor configuration and pause/enable mutation helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Protocol

from ...agent_registry import load_goal_from_registry
from ...control_plane.goals.activation import goal_is_stopped
from ...control_plane.runtime.time import now_utc
from ...file_lock import exclusive_file_lock
from .steward_commands import native_command
from .steward_managed_mode import StewardManagedMode
from .steward_monitor_turns import (
    TARGET,
    StewardAcquireLeaseFn,
    StewardControllerProtocol,
    StewardNativeCommandFn,
    StewardReadTodosFn,
    StewardReleaseLeaseFn,
    StewardTodoRecord,
    _coerce_dict,
)


class _CurrentMonitorFn(Protocol):
    def __call__(
        self,
        actor: Mapping[str, object],
        *,
        todos: Sequence[StewardTodoRecord] | None = None,
    ) -> StewardTodoRecord | None: ...


class _ClearHintFn(Protocol):
    def __call__(self, binding_id: str) -> None: ...


def _apply_configure_mutation(
    controller: StewardControllerProtocol,
    *,
    binding_id: str,
    actor: Mapping[str, object],
    plan: Mapping[str, object],
    plan_id: str,
    todo: Mapping[str, object] | None,
    operation: str,
    cadence_minutes: int,
    current_monitor: _CurrentMonitorFn,
    acquire_lease: StewardAcquireLeaseFn,
    release_lease: StewardReleaseLeaseFn,
    clear_next_check_hint: _ClearHintFn,
    run_command: StewardNativeCommandFn = native_command,
) -> dict[str, object]:
    args = [
        "todo",
        "update" if todo else "add",
        "--goal-id",
        str(actor["goal_id"]),
        "--role",
        "agent",
        "--execute",
    ]
    lease: dict[str, object] = {}
    if todo:
        args += ["--agent-id", str(actor["agent_id"]), "--todo-id", str(todo["todo_id"])]
        if operation == "pause":
            if todo["status"] == "blocked":
                return {
                    "ok": True,
                    "applied": True,
                    "plan": plan,
                    "monitor": todo,
                    "model_invoked": False,
                }
            receipt = run_command(
                controller,
                [
                    *args,
                    "--update-operation-id",
                    "pause." + plan_id,
                    "--status",
                    "blocked",
                    "--clear-resume-when",
                    "--reason",
                    "Owner paused native monitoring.",
                ],
            )
            clear_next_check_hint(binding_id)
            return {
                "ok": True,
                "applied": True,
                "plan": plan,
                "receipt": receipt,
                "monitor": current_monitor(actor),
                "model_invoked": False,
            }
        if todo["status"] == "blocked":
            run_command(
                controller,
                [
                    *args,
                    "--update-operation-id",
                    "resume." + plan_id,
                    "--status",
                    "open",
                    "--clear-resume-when",
                    "--reason",
                    "Owner enabled native monitoring.",
                ],
            )
        key = "configure." + plan_id
        raw_lease = acquire_lease(actor, todo, key)["lease"]
        if not isinstance(raw_lease, Mapping):
            raise ValueError("task lease unavailable")
        lease = _coerce_dict(raw_lease)
        args += [
            "--update-operation-id",
            key,
            "--status",
            "open",
            "--reason",
            "Owner configured the native monitor cadence.",
            "--task-lease-idempotency-key",
            key,
            "--task-lease-expected-version",
            str(lease["version"]),
        ]
    else:
        args += [
            "--text",
            "Observe the original steward's authorized local Goal portfolio; retain coverage gaps and original requests.",
            "--operation-id",
            "steward-monitor." + binding_id,
            "--task-class",
            "continuous_monitor",
            "--action-kind",
            "local_runtime_observe",
            "--continuation-policy",
            "same_agent_non_delivery",
            "--claimed-by",
            str(actor["agent_id"]),
            "--required-capability",
            "filesystem",
            "--target-key",
            TARGET,
            "--watch-only",
        ]
    if operation == "enable":
        due = now_utc() + timedelta(minutes=cadence_minutes) if todo else now_utc()
        args += ["--cadence", f"{cadence_minutes}m", "--next-due-at", due.isoformat()]
    try:
        receipt = run_command(controller, args)
    finally:
        if todo:
            release_lease(actor, todo, lease)
    clear_next_check_hint(binding_id)
    return {
        "ok": True,
        "applied": True,
        "plan": plan,
        "receipt": receipt,
        "monitor": current_monitor(actor),
        "model_invoked": False,
    }


def configure_steward_monitor(
    controller: StewardControllerProtocol,
    *,
    managed: StewardManagedMode,
    binding_id: str,
    operation: str,
    cadence_minutes: int = 30,
    expected_plan_id: str | None = None,
    execute: bool = False,
    read_todos: StewardReadTodosFn,
    current_monitor: _CurrentMonitorFn,
    acquire_lease: StewardAcquireLeaseFn,
    release_lease: StewardReleaseLeaseFn,
    clear_next_check_hint: _ClearHintFn,
    run_command: StewardNativeCommandFn = native_command,
) -> dict[str, object]:
    """Preview or mutate the original steward's continuous monitor Todo."""
    bindings = controller.project_contexts.conversation_bindings
    session = managed._session(binding_id, None)
    saved = _coerce_dict(session["steward_context"])
    selected = bindings.resolve(
        binding_id=binding_id,
        session=session,
        include_observation=True,
        origin="web",
        source_ref=saved["source_ref"],
        sender_ref=saved["operator_ref"],
        private_human_message=False,
        session_context=saved,
    )
    with (
        controller.lock,
        exclusive_file_lock(bindings.path, operation="configure_steward_monitor"),
    ):
        raw_bindings = bindings.read()["bindings"]
        if not isinstance(raw_bindings, Sequence) or isinstance(raw_bindings, (str, bytes)):
            raise TypeError("bindings payload must be a sequence")
        binding_rows = raw_bindings
        binding = next(
            _coerce_dict(row)
            for row in binding_rows
            if isinstance(row, Mapping) and row.get("binding_id") == binding_id
        )
        if binding != selected["binding"]:
            raise ValueError("the original steward authorization changed")
        actor_raw = binding.get("managed_actor")
        if not isinstance(actor_raw, Mapping) or not actor_raw:
            raise ValueError("prepare the original steward first")
        actor = _coerce_dict(actor_raw)
        goal = load_goal_from_registry(controller.registry_path, str(actor["goal_id"]))
        loaded_session = controller.store.load_session(str(session["session_id"]))
        plan = bindings._core(
            "collaboration.steward.monitor.plan",
            {
                "binding": binding,
                "session": _coerce_dict(loaded_session)
                if loaded_session is not None
                else None,
                "observation": selected["observation"],
                "workspace_path": str(managed._workspace(session)),
                "goal": managed._goal(str(actor["goal_id"])),
                "goal_active": bool(
                    goal
                    and goal.get("status") == "active"
                    and not goal_is_stopped(goal)
                ),
                "operation": operation,
                "cadence_minutes": cadence_minutes,
                "todos": read_todos(actor),
            },
        )
        if not execute:
            return {"ok": True, "applied": False, "plan": plan}
        if expected_plan_id != plan["plan_id"]:
            raise ValueError("refresh the current native monitor preview")
        plan_id = str(plan["plan_id"])
        raw_todo = plan["todo"]
        todo = _coerce_dict(raw_todo) if isinstance(raw_todo, Mapping) else None
        if operation == "pause" and todo is None:
            return {"ok": True, "applied": True, "plan": plan}
        if (
            operation == "enable"
            and todo
            and todo["status"] == "open"
            and todo.get("cadence") == f"{cadence_minutes}m"
        ):
            return {
                "ok": True,
                "applied": True,
                "plan": plan,
                "monitor": todo,
                "model_invoked": False,
            }
        return _apply_configure_mutation(
            controller,
            binding_id=binding_id,
            actor=actor,
            plan=plan,
            plan_id=plan_id,
            todo=todo,
            operation=operation,
            cadence_minutes=cadence_minutes,
            current_monitor=current_monitor,
            acquire_lease=acquire_lease,
            release_lease=release_lease,
            clear_next_check_hint=clear_next_check_hint,
            run_command=run_command,
        )
