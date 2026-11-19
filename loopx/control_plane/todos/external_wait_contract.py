from __future__ import annotations

from typing import Any


MONITOR_ADVANCEMENT_AUTHORING_SCHEMA_VERSION = (
    "monitor_advancement_authoring_v0"
)


def build_monitor_advancement_authoring_contract(
    *,
    monitor_todo_id: str | None = None,
    material_change_scope: str | None = None,
    successor_todo_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Project the cross-command monitor-to-advancement authoring contract."""

    raw_target = str(monitor_todo_id or "").strip()
    target_id, separator, target_scope = raw_target.partition("@")
    monitor_ref = target_id.strip() or "<monitor_todo_id>"
    scope_ref = (
        str(
            material_change_scope
            if material_change_scope is not None
            else (target_scope if separator else "")
        )
        .strip()
        .lower()
        or None
    )
    successors = [
        str(item).strip()
        for item in (successor_todo_ids or [])
        if str(item).strip()
    ]
    required_args = [
        "--material-change",
        *(["--material-change-scope"] if scope_ref else []),
        "--next-agent-todo",
        "--next-action-kind",
    ]
    return {
        "schema_version": MONITOR_ADVANCEMENT_AUTHORING_SCHEMA_VERSION,
        "monitor": {
            "task_class": "continuous_monitor",
            "execution": "observe_only",
        },
        "material_change": {
            "command": "loopx quota monitor-poll",
            "required_args": required_args,
            **({"material_change_scope": scope_ref} if scope_ref else {}),
            "next_agent_todo_effect": "emit_independent_open_advancement_task",
        },
        "waiting_todo": {
            "status": "open",
            "task_class": "advancement_task",
            "resume_when": (
                f"monitor_changed:{monitor_ref}@{scope_ref}"
                if scope_ref
                else f"monitor_changed:{monitor_ref}"
            ),
            "successor_todo_ids": successors,
            "successor_requirement": "independent_runnable_advancement_task",
            "runnable_state": "excluded_until_resume_condition_satisfied",
        },
    }


class TodoExternalWaitAuthoringError(ValueError):
    """Typed public diagnostic for an invalid external-wait transition."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        monitor_todo_id: str | None = None,
        material_change_scope: str | None = None,
        successor_todo_ids: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.authoring_contract = (
            build_monitor_advancement_authoring_contract(
                monitor_todo_id=monitor_todo_id,
                material_change_scope=material_change_scope,
                successor_todo_ids=successor_todo_ids,
            )
            if monitor_todo_id
            else None
        )
