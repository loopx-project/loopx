"""Supporting diagnostics shared by active and settled quota packets."""
from __future__ import annotations

from typing import Any, cast


from ..agents.agent_scope import _action_scope_tokens_from_text
from ..runtime.decision_freshness import decision_freshness_warning as _decision_freshness_warning
from ..runtime.promotion_readiness import promotion_readiness_warning as _promotion_readiness_warning
from ..todos.user_gate import build_gate_prompt as _build_gate_prompt
from .recent_runs import goal_latest_runs as _goal_latest_runs


def _recent_reward_lessons(status_payload: dict[str, Any], *, goal_id: str) -> list[dict[str, Any]]:
    lessons: list[dict[str, Any]] = []
    for run in _goal_latest_runs(status_payload, goal_id=goal_id):
        reward = cast(dict[str, Any], run.get("human_reward")) if isinstance(run.get("human_reward"), dict) else {}
        lesson = cast(dict[str, Any], reward.get("lesson")) if isinstance(reward.get("lesson"), dict) else {}
        if not lesson:
            continue
        lessons.append(
            {
                "generated_at": run.get("generated_at"),
                "decision": reward.get("decision"),
                "reward": reward.get("reward"),
                "kind": lesson.get("kind"),
                "summary": lesson.get("summary"),
                "avoid": cast(list[Any], lesson.get("avoid")) if isinstance(lesson.get("avoid"), list) else [],
                "prefer": cast(list[Any], lesson.get("prefer")) if isinstance(lesson.get("prefer"), list) else [],
            }
        )
    return lessons


def _reward_lesson_projection_warning(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    recommended_action: str | None,
) -> dict[str, Any] | None:
    action = str(recommended_action or "").strip()
    if not action:
        return None
    action_lower = action.lower()
    action_tokens = _action_scope_tokens_from_text(action)
    matches: list[dict[str, Any]] = []
    for lesson in _recent_reward_lessons(status_payload, goal_id=goal_id):
        for avoid in lesson.get("avoid") or []:
            avoid_text = str(avoid or "").strip()
            if not avoid_text:
                continue
            avoid_tokens = _action_scope_tokens_from_text(avoid_text)
            exact_match = avoid_text.lower() in action_lower
            if not exact_match and not avoid_tokens:
                continue
            token_overlap = sorted(action_tokens & avoid_tokens)
            if not exact_match and len(token_overlap) < min(2, len(avoid_tokens)):
                continue
            matches.append(
                {
                    "generated_at": lesson.get("generated_at"),
                    "decision": lesson.get("decision"),
                    "kind": lesson.get("kind"),
                    "summary": lesson.get("summary"),
                    "avoid": avoid_text,
                    "token_overlap": token_overlap[:5],
                }
            )
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


def _dict_field(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    return cast(dict[str, Any], payload.get(key)) if isinstance(payload.get(key), dict) else None


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
