"""Supporting diagnostics shared by active and settled quota packets."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, cast

from ..runtime.decision_freshness import decision_freshness_warning as _decision_freshness_warning
from ..runtime.promotion_readiness import promotion_readiness_warning as _promotion_readiness_warning
from ..todos.contract import (
    normalize_explicit_todo_task_class,
    normalize_required_capabilities,
    normalize_required_write_scopes,
    normalize_target_capabilities,
    normalize_todo_action_kind,
    normalize_todo_claimed_by,
    normalize_todo_decision_scope,
    normalize_todo_id,
    normalize_todo_id_list,
    normalize_todo_required_decision_scopes,
)
from ..todos.user_gate import build_gate_prompt as _build_gate_prompt
from .recent_runs import goal_latest_runs as _goal_latest_runs

_LEADING_ACTION_PREFIX_RE = re.compile(
    r"^(?:\[[^\]]+\]\s*|todo_[a-z0-9_-]+:\s*|p[0-4]:\s*|(?:agent|user|owner|codex)\s*:\s*)+",
    re.IGNORECASE,
)
_BARE_PRIORITY_RE = re.compile(r"^\[?p[0-4]\]?$", re.IGNORECASE)
_LESSON_STRUCTURED_KEYS = (
    "todo_id",
    "todo_ids",
    "claimed_by",
    "agent_id",
    "task_class",
    "capability_id",
    "capability_ids",
    "required_capabilities",
    "work_lane",
    "action_kind",
    "target_key",
    "scope_key",
    "scope_keys",
    "target_scopes",
    "decision_scope",
    "target",
    "scope",
)


def _normalized_phrase(value: object) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _normalized_action_body(value: object) -> str:
    collapsed = " ".join(str(value or "").strip().split())
    stripped = _LEADING_ACTION_PREFIX_RE.sub("", collapsed).strip()
    return (stripped or collapsed).casefold()


def _string_tokens(value: object) -> set[str]:
    items: Iterable[object] = value if isinstance(value, (list, tuple, set)) else (value,)
    return {text for item in items if (text := str(item or "").strip().lower())}


def _decision_scope_tokens(value: object) -> set[str]:
    tokens: set[str] = set()
    if normalized := normalize_todo_decision_scope(value):
        tokens.add(f"{normalized['kind']}:{normalized['granularity']}:{normalized['scope_key']}")
        tokens.add(normalized["scope_key"])
    for entry in normalize_todo_required_decision_scopes(value):
        tokens.add(f"{entry['kind']}:{entry['granularity']}:{entry['scope_key']}")
        tokens.add(entry["scope_key"])
    return tokens


@dataclass(frozen=True, slots=True)
class _SelectionScopeContext:
    todo_ids: frozenset[str]
    agent_ids: frozenset[str]
    task_classes: frozenset[str]
    capability_ids: frozenset[str]
    work_lane_fields: dict[str, str]
    action_kinds: frozenset[str]
    target_keys: frozenset[str]
    scope_keys: frozenset[str]

    @property
    def has_structured_targets(self) -> bool:
        return bool(
            self.todo_ids
            or self.task_classes
            or self.capability_ids
            or self.work_lane_fields
            or self.action_kinds
            or self.target_keys
            or self.scope_keys
        )


def _extract_selection_scope_context(
    sources: Sequence[Mapping[str, object] | None],
    *,
    work_lane: Mapping[str, object] | None,
) -> _SelectionScopeContext:
    todo_ids: set[str] = set()
    agent_ids: set[str] = set()
    task_classes: set[str] = set()
    capability_ids: set[str] = set()
    action_kinds: set[str] = set()
    target_keys: set[str] = set()
    scope_keys: set[str] = set()

    def collect_row(row: Mapping[str, object]) -> None:
        for key in ("todo_id", "selected_todo_id"):
            if todo_id := normalize_todo_id(row.get(key)):
                todo_ids.add(todo_id)
        for key in ("claimed_by", "agent_id"):
            if agent_id := normalize_todo_claimed_by(row.get(key)):
                agent_ids.add(agent_id)
        for key in ("task_class", "lane"):
            if task_class := normalize_explicit_todo_task_class(row.get(key)):
                task_classes.add(task_class)
        for key in ("capability_id", "capability_ids"):
            capability_ids.update(_string_tokens(row.get(key)))
        capability_ids.update(normalize_required_capabilities(row.get("required_capabilities")))
        capability_ids.update(normalize_target_capabilities(row.get("target_capabilities")))
        if action_kind := normalize_todo_action_kind(row.get("action_kind")):
            action_kinds.add(action_kind)
        for key in ("target_key", "route_id", "route_key"):
            target_keys.update(_string_tokens(row.get(key)))
        scope_keys.update(normalize_required_write_scopes(row.get("required_write_scopes")))
        scope_keys.update(_decision_scope_tokens(row.get("decision_scope")))
        scope_keys.update(_decision_scope_tokens(row.get("required_decision_scopes")))
        for key in ("scope_key", "scope_keys", "target_scopes"):
            scope_keys.update(_string_tokens(row.get(key)))

    for source in sources:
        if not isinstance(source, Mapping):
            continue
        collect_row(source)
        monitor_due_items = source.get("monitor_due_items")
        if isinstance(monitor_due_items, list):
            for due_item in monitor_due_items:
                if isinstance(due_item, Mapping):
                    collect_row(due_item)

    work_lane_fields: dict[str, str] = {}
    if isinstance(work_lane, Mapping):
        collect_row(work_lane)
        for key in ("lane", "next_lane", "obligation", "monitor_kind", "selected_todo_id"):
            value = str(work_lane.get(key) or "").strip().lower()
            if value:
                work_lane_fields[key] = value

    return _SelectionScopeContext(
        todo_ids=frozenset(todo_ids),
        agent_ids=frozenset(agent_ids),
        task_classes=frozenset(task_classes),
        capability_ids=frozenset(capability_ids),
        work_lane_fields=work_lane_fields,
        action_kinds=frozenset(action_kinds),
        target_keys=frozenset(target_keys),
        scope_keys=frozenset(scope_keys),
    )


def _merged_lesson_scope(lesson: Mapping[str, object]) -> dict[str, object]:
    merged: dict[str, object] = dict(lesson)
    for nested_key in ("target", "scope"):
        nested = lesson.get(nested_key)
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                if key not in merged and value is not None:
                    merged[key] = value
    return merged


def _match_lesson_structured_scope(
    lesson: Mapping[str, object],
    context: _SelectionScopeContext,
) -> tuple[bool, dict[str, object]]:
    """Evaluate structured scope fields on a lesson against the active selection."""
    fields = _merged_lesson_scope(lesson)
    lesson_agents = {
        agent
        for key in ("claimed_by", "agent_id")
        if (agent := normalize_todo_claimed_by(fields.get(key)))
    }
    if lesson_agents and not (lesson_agents & context.agent_ids):
        return True, {}

    lesson_todo_ids = set(normalize_todo_id_list(fields.get("todo_id"))) | set(
        normalize_todo_id_list(fields.get("todo_ids"))
    )
    lesson_task_class = normalize_explicit_todo_task_class(fields.get("task_class"))
    lesson_capabilities = (
        _string_tokens(fields.get("capability_id"))
        | _string_tokens(fields.get("capability_ids"))
        | set(normalize_required_capabilities(fields.get("required_capabilities")))
    )
    lesson_work_lane = fields.get("work_lane")
    lesson_action_kind = normalize_todo_action_kind(fields.get("action_kind"))
    lesson_target_keys = _string_tokens(fields.get("target_key"))
    lesson_scope_keys = (
        _string_tokens(fields.get("scope_key"))
        | _string_tokens(fields.get("scope_keys"))
        | _string_tokens(fields.get("target_scopes"))
        | _decision_scope_tokens(fields.get("decision_scope"))
    )

    if not (
        lesson_todo_ids
        or lesson_task_class
        or lesson_capabilities
        or lesson_work_lane is not None
        or lesson_action_kind
        or lesson_target_keys
        or lesson_scope_keys
    ):
        return False, {}

    matched: dict[str, object] = {}
    if lesson_agents:
        matched["claimed_by"] = sorted(lesson_agents & context.agent_ids)[0]
    set_checks: tuple[tuple[str, set[str], frozenset[str]], ...] = (
        ("todo_id", lesson_todo_ids, context.todo_ids),
        ("capability_id", lesson_capabilities, context.capability_ids),
        ("target_key", lesson_target_keys, context.target_keys),
        ("scope_key", lesson_scope_keys, context.scope_keys),
    )
    for field_name, required_set, available_set in set_checks:
        if required_set:
            overlap = sorted(required_set & available_set)
            if not overlap:
                return True, {}
            matched[field_name] = overlap[0]
    for field_name, required_value, available_set in (
        ("task_class", lesson_task_class, context.task_classes),
        ("action_kind", lesson_action_kind, context.action_kinds),
    ):
        if required_value:
            if required_value not in available_set:
                return True, {}
            matched[field_name] = required_value
    if lesson_work_lane is not None:
        if isinstance(lesson_work_lane, Mapping):
            expected_pairs = {
                str(key): str(val).strip().lower()
                for key, val in lesson_work_lane.items()
                if val is not None and str(val).strip()
            }
            if not expected_pairs or any(
                context.work_lane_fields.get(key) != val
                for key, val in expected_pairs.items()
            ):
                return True, {}
            matched["work_lane"] = expected_pairs
        else:
            lane_value = str(lesson_work_lane or "").strip().lower()
            if not lane_value or lane_value not in {
                context.work_lane_fields.get("lane"),
                context.work_lane_fields.get("obligation"),
            }:
                return True, {}
            matched["work_lane"] = lane_value

    return True, matched


def _recent_reward_lessons(status_payload: Mapping[str, object], *, goal_id: str) -> list[dict[str, object]]:
    lessons: list[dict[str, object]] = []
    for run in _goal_latest_runs(cast(dict[str, Any], status_payload), goal_id=goal_id):
        raw_reward = run.get("human_reward")
        reward = raw_reward if isinstance(raw_reward, Mapping) else {}
        raw_lesson = reward.get("lesson")
        lesson = raw_lesson if isinstance(raw_lesson, Mapping) else {}
        if not lesson:
            continue
        raw_avoid = lesson.get("avoid")
        raw_prefer = lesson.get("prefer")
        entry: dict[str, object] = {
            "generated_at": run.get("generated_at"),
            "decision": reward.get("decision"),
            "reward": reward.get("reward"),
            "kind": lesson.get("kind"),
            "summary": lesson.get("summary"),
            "avoid": raw_avoid if isinstance(raw_avoid, list) else [],
            "prefer": raw_prefer if isinstance(raw_prefer, list) else [],
        }
        for key in _LESSON_STRUCTURED_KEYS:
            if key in lesson and lesson[key] is not None:
                entry[key] = lesson[key]
        lessons.append(entry)
    return lessons


def _match_unstructured_avoid(
    lesson: Mapping[str, object],
    avoid_text: str,
    *,
    context: _SelectionScopeContext,
    structured_keys: frozenset[str],
    action_body: str,
) -> dict[str, object] | None:
    base: dict[str, object] = {
        "generated_at": lesson.get("generated_at"),
        "decision": lesson.get("decision"),
        "kind": lesson.get("kind"),
        "summary": lesson.get("summary"),
        "avoid": avoid_text,
    }
    if (avoid_todo_id := normalize_todo_id(avoid_text)) and avoid_todo_id in context.todo_ids:
        return {**base, "match_kind": "structured_todo_id", "matched_scope": {"todo_id": avoid_todo_id}}
    if avoid_text.lower() in structured_keys:
        return {**base, "match_kind": "structured_scope"}
    avoid_norm = _normalized_phrase(avoid_text)
    if (
        action_body
        and len(avoid_norm) >= 2
        and not _BARE_PRIORITY_RE.fullmatch(avoid_norm)
        and avoid_norm in action_body
    ):
        return {**base, "match_kind": "exact_phrase"}
    return None


def _reward_lesson_projection_warning(
    status_payload: Mapping[str, object],
    *,
    goal_id: str,
    recommended_action: str | None,
    selected_todo: Mapping[str, object] | None = None,
    work_lane: Mapping[str, object] | None = None,
    agent_identity: Mapping[str, object] | None = None,
    agent_lane_next_action: Mapping[str, object] | None = None,
    item: Mapping[str, object] | None = None,
    project_asset: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    action = str(recommended_action or "").strip()
    context = _extract_selection_scope_context(
        (selected_todo, agent_lane_next_action, agent_identity, item, project_asset),
        work_lane=work_lane,
    )
    if not action and not context.has_structured_targets:
        return None
    action_body = _normalized_action_body(action)
    structured_keys = context.action_kinds | context.capability_ids | context.target_keys | context.scope_keys
    matches: list[dict[str, object]] = []
    for lesson in _recent_reward_lessons(status_payload, goal_id=goal_id):
        has_structured, matched_scope = _match_lesson_structured_scope(lesson, context)
        if has_structured:
            if matched_scope:
                raw_avoid = lesson.get("avoid")
                avoid_values = [
                    str(v).strip()
                    for v in (raw_avoid if isinstance(raw_avoid, list) else ())
                    if str(v or "").strip()
                ]
                matches.append(
                    {
                        "generated_at": lesson.get("generated_at"),
                        "decision": lesson.get("decision"),
                        "kind": lesson.get("kind"),
                        "summary": lesson.get("summary"),
                        "avoid": avoid_values[0] if avoid_values else str(lesson.get("summary") or ""),
                        "match_kind": "structured_scope",
                        "matched_scope": matched_scope,
                    }
                )
            continue
        raw_avoid = lesson.get("avoid")
        for avoid in raw_avoid if isinstance(raw_avoid, list) else ():
            avoid_text = str(avoid or "").strip()
            if not avoid_text:
                continue
            if match := _match_unstructured_avoid(
                lesson,
                avoid_text,
                context=context,
                structured_keys=structured_keys,
                action_body=action_body,
            ):
                matches.append(match)
    if not matches:
        return None
    return {
        "schema_version": "reward_lesson_projection_warning_v0",
        "source": "run_history.human_reward.lesson",
        "goal_id": goal_id,
        "message": (
            "recommended_action overlaps a recent human_reward lesson avoid rule; "
            "rebase the route or update the affected todo/next action before continuing"
        ),
        "recommended_action": action,
        "match_count": len(matches),
        "matches": matches[:3],
    }


def _attach_truthy_fields(payload: dict[str, Any], **fields: Any) -> None:
    payload.update({key: value for key, value in fields.items() if value})


def _dict_field(payload: Mapping[str, object], key: str) -> dict[str, Any] | None:
    value = payload.get(key)
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _attach_quota_supporting_projections(
    payload: dict[str, Any],
    *,
    status_payload: dict[str, Any],
    item: dict[str, Any],
    project_asset: dict[str, Any],
    goal_id: str,
    selected_recommended_action: Any,
    state: str,
    user_todo_summary: dict[str, Any] | None,
    should_run: bool,
    state_action_projection_warning: dict[str, Any] | None,
    next_action_warning: dict[str, Any] | None,
    replan_obligation: dict[str, Any] | None,
    work_lane: Mapping[str, object] | None = None,
    notify_gate: bool = True,
) -> None:
    _attach_truthy_fields(
        payload,
        stale_latest_run_warning=_dict_field(item, "stale_latest_run_warning"),
        state_action_projection_warning=state_action_projection_warning,
        next_action_projection_warning=next_action_warning,
        backlog_hygiene_warning=_dict_field(item, "backlog_hygiene_warning"),
        completed_todo_archive_warning=_dict_field(item, "completed_todo_archive_warning"),
        autonomous_replan_obligation=replan_obligation,
        dreaming_proposal=_dict_field(item, "dreaming_proposal"),
        dreaming_lane_badge=_dict_field(item, "dreaming_lane_badge"),
        interface_budget_cadence=_dict_field(project_asset, "interface_budget_cadence"),
        decision_freshness_warning=_decision_freshness_warning(status_payload, goal_id=goal_id),
        promotion_readiness_warning=_promotion_readiness_warning(status_payload),
        reward_lesson_projection_warning=_reward_lesson_projection_warning(
            status_payload,
            goal_id=goal_id,
            recommended_action=selected_recommended_action,
            selected_todo=_dict_field(payload, "selected_todo"),
            work_lane=work_lane if work_lane is not None else _dict_field(payload, "work_lane"),
            agent_identity=_dict_field(payload, "agent_identity"),
            agent_lane_next_action=_dict_field(payload, "agent_lane_next_action"),
            item=item,
            project_asset=project_asset,
        ),
    )
    if state == "operator_gate" and (
        gate_prompt := _build_gate_prompt(item, user_todo_summary=user_todo_summary)
    ):
        payload["gate_prompt"] = gate_prompt
        if notify_gate:
            payload["notify_user_on_gate"] = True
    _attach_truthy_fields(
        payload, next_handoff_condition=item.get("next_handoff_condition"),
        agent_command=item.get("agent_command") if should_run else None,
    )
