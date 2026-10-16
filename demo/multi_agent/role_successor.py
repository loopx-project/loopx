from __future__ import annotations

import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path

from loopx.agent_registry import registered_agent_ids_from_registry
from loopx.todos import add_goal_todo


MULTI_AGENT_ROLE_SUCCESSOR_TODOS_SCHEMA_VERSION = "multi_agent_role_successor_todos_v0"
ROLE_SUCCESSOR_SOURCE = "role_profile_todo_command_template"


def _value_at_path(payload: Mapping[str, object], path: str) -> object:
    current: object = payload
    for part in [part for part in path.split(".") if part]:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _compare_condition_value(actual: object, *, op: str, expected: object) -> bool:
    if op in {"truthy", "exists"}:
        return bool(actual)
    if op in {"falsy", "missing"}:
        return not bool(actual)
    if op in {"eq", "=="}:
        return actual == expected
    if op in {"ne", "!="}:
        return actual != expected
    if op in {"gt", "gte", "lt", "lte", ">", ">=", "<", "<="}:
        try:
            actual_number = float(actual)  # type: ignore[arg-type]
            expected_number = float(expected)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return {
            "gt": actual_number > expected_number,
            ">": actual_number > expected_number,
            "gte": actual_number >= expected_number,
            ">=": actual_number >= expected_number,
            "lt": actual_number < expected_number,
            "<": actual_number < expected_number,
            "lte": actual_number <= expected_number,
            "<=": actual_number <= expected_number,
        }[op]
    return False


def _successor_condition_met(
    condition: object,
    *,
    decision_summary: Mapping[str, object],
) -> tuple[bool, str]:
    if not condition:
        return True, "always"
    if condition == "always":
        return True, "always"
    if not isinstance(condition, Mapping):
        return False, "unsupported_successor_condition_shape"

    context: dict[str, object] = {"decision_summary": dict(decision_summary)}
    predicates = condition.get("all")
    if not isinstance(predicates, list):
        return False, "unsupported_successor_condition_shape"
    if not predicates:
        return True, "all"

    for raw_predicate in predicates:
        if not isinstance(raw_predicate, Mapping):
            return False, "unsupported_successor_predicate_shape"
        path = str(raw_predicate.get("path") or "")
        op = str(raw_predicate.get("op") or "truthy")
        expected = raw_predicate.get("value")
        actual = _value_at_path(context, path)
        if not _compare_condition_value(actual, op=op, expected=expected):
            return False, str(raw_predicate.get("fail_reason") or f"condition_failed:{path}")
    return True, "condition_met"


def _resolve_successor_target_agent(
    *,
    registry_path: Path,
    goal_id: str,
    current_agent_id: str,
    spec: Mapping[str, object],
) -> str:
    registered = registered_agent_ids_from_registry(registry_path, goal_id)
    target = str(spec.get("target_agent_id") or "").strip()
    if target == "$current_agent":
        return current_agent_id
    if target and target in registered:
        return target
    if target:
        raise ValueError(f"successor target_agent_id {target!r} is not registered for goal {goal_id!r}")
    target_role_id = str(spec.get("target_role_id") or "").strip()
    if target_role_id:
        raise ValueError(
            f"successor target_role_id {target_role_id!r} requires an explicit registered target_agent_id"
        )
    for agent_id in registered:
        if agent_id != current_agent_id:
            return agent_id
    return current_agent_id


class _SafeFormatDict(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_successor_command_template(
    *,
    template: object,
    goal_id: str,
    source_todo_id: str,
    target_agent_id: str,
    task_class: str,
    action_kind: str,
    text: str,
) -> str | None:
    if not template:
        return None
    if not isinstance(template, str):
        raise ValueError("successor todo_command_template must be a string")
    values: dict[str, object] = {
        "goal_id": goal_id,
        "goal_id_shell": shlex.quote(goal_id),
        "source_todo_id": source_todo_id,
        "source_todo_id_shell": shlex.quote(source_todo_id),
        "target_agent_id": target_agent_id,
        "target_agent_id_shell": shlex.quote(target_agent_id),
        "task_class": task_class,
        "task_class_shell": shlex.quote(task_class),
        "action_kind": action_kind,
        "action_kind_shell": shlex.quote(action_kind),
        "text": text,
        "text_shell": shlex.quote(text),
    }
    return template.format_map(_SafeFormatDict(values))


def _render_successor_text(
    *,
    text: str,
    goal_id: str,
    source_todo_id: str,
    target_agent_id: str,
    task_class: str,
    action_kind: str,
) -> str:
    values: dict[str, object] = {
        "goal_id": goal_id,
        "source_todo_id": source_todo_id,
        "target_agent_id": target_agent_id,
        "task_class": task_class,
        "action_kind": action_kind,
    }
    return text.format_map(_SafeFormatDict(values))


def apply_role_successor_todos(
    *,
    registry_path: Path,
    goal_id: str,
    source_todo_id: str,
    current_agent_id: str,
    role_id: str,
    action: str,
    successor_specs: Iterable[object],
    decision_summary: Mapping[str, object],
    execute: bool,
    text_source_todo_id: str | None = None,
    expected_provider_revision: str | None = None,
) -> dict[str, object]:
    """Create successors, retaining qualified historical source bindings.

    Normally one source supplies both title and lineage. A caller recovering
    an existing successor may preserve its original parent while rendering
    the same title from the independently verified evidence lineage.
    """

    specs = [dict(spec) for spec in successor_specs if isinstance(spec, Mapping)]
    if not specs:
        return {
            "schema_version": MULTI_AGENT_ROLE_SUCCESSOR_TODOS_SCHEMA_VERSION,
            "source": ROLE_SUCCESSOR_SOURCE,
            "needed": False,
            "reason": "no_role_successor_spec",
            "role_id": role_id,
            "action": action,
            "successors": [],
        }

    successors: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for index, spec in enumerate(specs):
        condition = spec.get("condition") or spec.get("when") or "always"
        condition_met, reason = _successor_condition_met(
            condition,
            decision_summary=decision_summary,
        )
        action_kind = str(spec.get("action_kind") or "advance_todo")
        task_class = str(spec.get("task_class") or "advancement_task")
        raw_text = str(spec.get("text") or f"Run {action_kind}.")
        claimed_by = _resolve_successor_target_agent(
            registry_path=registry_path,
            goal_id=goal_id,
            current_agent_id=current_agent_id,
            spec=spec,
        )
        text = _render_successor_text(
            text=raw_text,
            goal_id=goal_id,
            source_todo_id=text_source_todo_id or source_todo_id,
            target_agent_id=claimed_by,
            task_class=task_class,
            action_kind=action_kind,
        )
        todo_command = _render_successor_command_template(
            template=spec.get("todo_command_template"),
            goal_id=goal_id,
            source_todo_id=source_todo_id,
            target_agent_id=claimed_by,
            task_class=task_class,
            action_kind=action_kind,
            text=text,
        )
        successor_preview = {
            "index": index,
            "needed": condition_met,
            "executed": False,
            "condition": condition,
            "reason": reason,
            "target_agent_id": claimed_by,
            "target_role_id": spec.get("target_role_id"),
            "action_kind": action_kind,
            "task_class": task_class,
            "unblocks_todo_id": source_todo_id,
            "todo_command": todo_command,
            "source": ROLE_SUCCESSOR_SOURCE,
        }
        if not condition_met:
            skipped.append(successor_preview)
            continue
        if not execute:
            successors.append(successor_preview)
            continue
        result = add_goal_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            role="agent",
            text=text,
            task_class=task_class,
            action_kind=action_kind,
            claimed_by=claimed_by,
            unblocks_todo_id=source_todo_id,
            expected_provider_revision=expected_provider_revision,
            dry_run=False,
        )
        successors.append(
            successor_preview
            | {
                "executed": True,
                "added": bool(result.get("added")),
                "already_exists": bool(result.get("already_exists")),
                "metadata_updated": bool(result.get("metadata_updated")),
                "todo_id": result.get("todo_id"),
                "claimed_by": result.get("claimed_by") or claimed_by,
                "action_kind": result.get("action_kind") or action_kind,
                "unblocks_todo_id": result.get("unblocks_todo_id") or source_todo_id,
            }
        )
    return {
        "schema_version": MULTI_AGENT_ROLE_SUCCESSOR_TODOS_SCHEMA_VERSION,
        "source": ROLE_SUCCESSOR_SOURCE,
        "needed": bool(successors),
        "executed": bool(execute and successors),
        "role_id": role_id,
        "action": action,
        "successors": successors,
        "skipped": skipped,
        "reason": None if successors else (skipped[0]["reason"] if skipped else "no_role_successor_spec"),
    }


def first_successor_followup(successor_todos: Mapping[str, object]) -> dict[str, object]:
    successors = successor_todos.get("successors")
    if isinstance(successors, list) and successors:
        first = dict(successors[0])
        first["needed"] = True
        return first
    return {
        "needed": False,
        "reason": successor_todos.get("reason") or "no_successor",
    }
