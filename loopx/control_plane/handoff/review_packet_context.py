from __future__ import annotations

from typing import Any

from ..runtime.public_safety import compact_text


from ..todos.todo_semantics import todo_priority_rank
HANDOFF_MONITOR_TASK_CLASSES = {"blocker", "continuous_monitor", "monitor", "user_gate"}
HANDOFF_ADVANCEMENT_TASK_CLASSES = {"advancement_task", "execution_task", "delivery_task"}
HANDOFF_MONITOR_MARKERS = (
    "monitor",
    "observation",
    "readiness",
    "watch",
    "poll",
    "dependency monitor",
    "观察",
    "监控",
    "等待",
)


def compact_packet_text(value: str, limit: int = 180) -> str:
    return compact_text(str(value), limit=limit)


def handoff_todo_priority_rank(text: str) -> int:
    return todo_priority_rank({"text": text})


def handoff_todo_task_rank(item: dict[str, Any], text: str) -> int:
    task_class = str(item.get("task_class") or "").strip().lower()
    if task_class in HANDOFF_ADVANCEMENT_TASK_CLASSES:
        return 0
    if task_class in HANDOFF_MONITOR_TASK_CLASSES:
        return 1
    lowered = text.lower()
    if any(marker in lowered for marker in HANDOFF_MONITOR_MARKERS):
        return 1
    return 0


def handoff_todo_rank(item: dict[str, Any], text: str, ordinal: int) -> tuple[int, int, int]:
    index = item.get("index")
    stable_index = index if isinstance(index, int) else ordinal
    return (
        handoff_todo_task_rank(item, text),
        handoff_todo_priority_rank(text),
        stable_index,
    )


def open_todo_texts(todos: Any, *, limit: int = 3, rank_for_handoff: bool = False) -> list[str]:
    if not isinstance(todos, dict):
        return []
    items = todos.get("items") if isinstance(todos.get("items"), list) else []
    if not items and isinstance(todos.get("first_open_items"), list):
        items = todos.get("first_open_items") or []
    result: list[str] = []
    ranked_result: list[tuple[tuple[int, int, int], str]] = []
    for ordinal, item in enumerate(items):
        if not isinstance(item, dict) or item.get("done"):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            claimed_by = str(item.get("claimed_by") or "").strip()
            display_text = text
            if claimed_by:
                display_text = f"{display_text} claimed_by={claimed_by}"
            if rank_for_handoff:
                ranked_result.append(
                    (handoff_todo_rank(item, text, ordinal), compact_packet_text(display_text))
                )
                continue
            result.append(compact_packet_text(display_text))
            if len(result) >= limit:
                return result
    if rank_for_handoff:
        return [text for _, text in sorted(ranked_result, key=lambda ranked: ranked[0])[:limit]]
    return result


def todo_text_from_project_asset(item: dict[str, Any] | None, key: str) -> str | None:
    items = todo_texts_from_project_asset(item, key, limit=1)
    return items[0] if items else None


def todo_texts_from_project_asset(
    item: dict[str, Any] | None,
    key: str,
    *,
    limit: int = 3,
) -> list[str]:
    if not isinstance(item, dict):
        return []
    project_asset = item.get("project_asset") if isinstance(item.get("project_asset"), dict) else {}
    summary = project_asset.get(key) if isinstance(project_asset.get(key), dict) else {}
    rank_for_handoff = key == "agent_todos"
    summary_items = open_todo_texts(summary, limit=limit, rank_for_handoff=rank_for_handoff)
    if summary_items:
        return summary_items
    next_text = str(summary.get("next") or "").strip()
    if next_text:
        return [compact_packet_text(next_text)]
    return open_todo_texts(item.get(key), limit=limit, rank_for_handoff=rank_for_handoff)


def agent_lane_todo_text(item: dict[str, Any] | None) -> str | None:
    if not isinstance(item, dict):
        return None
    project_asset = item.get("project_asset") if isinstance(item.get("project_asset"), dict) else {}
    lane = (
        project_asset.get("agent_lane_next_action")
        if isinstance(project_asset.get("agent_lane_next_action"), dict)
        else item.get("agent_lane_next_action")
        if isinstance(item.get("agent_lane_next_action"), dict)
        else None
    )
    if not isinstance(lane, dict):
        return None
    text = str(lane.get("text") or lane.get("title") or "").strip()
    if not text:
        return None
    claimed_by = str(lane.get("claimed_by") or "").strip()
    if claimed_by:
        text = f"{text} claimed_by={claimed_by}"
    return compact_packet_text(text)


def agent_todo_texts_for_handoff(item: dict[str, Any] | None, *, limit: int = 3) -> list[str]:
    items = todo_texts_from_project_asset(item, "agent_todos", limit=limit)
    lane_text = agent_lane_todo_text(item)
    if not lane_text:
        return items
    return [lane_text, *[text for text in items if text != lane_text]][:limit]


def agent_member_from_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    project_asset = item.get("project_asset") if isinstance(item.get("project_asset"), dict) else {}
    member = (
        project_asset.get("agent_member")
        if isinstance(project_asset.get("agent_member"), dict)
        else None
    )
    if member is None and isinstance(item.get("agent_member"), dict):
        member = item["agent_member"]
    return member if isinstance(member, dict) else None


def agent_member_summary(item: dict[str, Any] | None) -> str | None:
    member = agent_member_from_item(item)
    if not member:
        return None
    claims = [
        str(claim).strip()
        for claim in (member.get("current_claims") or [])
        if str(claim).strip()
    ]
    parts = [
        f"agent={member.get('agent_id')}",
        f"agent_model={member.get('agent_model')}",
        "authority=advisory_projection",
    ]
    if member.get("profile_role"):
        parts.append(f"profile_role={member.get('profile_role')}")
    if member.get("scope_summary"):
        parts.append(f"scope={member.get('scope_summary')}")
    if member.get("worktree_policy"):
        parts.append(f"worktree_policy={member.get('worktree_policy')}")
    if claims:
        parts.append(f"claims={','.join(claims[:5])}")
    if member.get("handoff_assignment_status"):
        parts.append(f"handoff_assignment={member.get('handoff_assignment_status')}")
    return compact_packet_text(" ".join(str(part) for part in parts if part))


def project_agent_replan_context(
    goal_id: str, item: dict[str, Any] | None, status_payload: dict[str, Any],
) -> dict[str, Any] | None:
    from ..work_items.replan_context_codec import project_replan_context

    member = agent_member_from_item(item)
    if not member:
        return None
    agent_id = str(member.get("agent_id") or "").strip()
    if not agent_id:
        return None
    asset = (item or {}).get("project_asset") or {}
    acceptance = next((candidate for candidate in (
        (item or {}).get("goal_acceptance_contract"), asset.get("goal_acceptance_contract"),
        ((item or {}).get("agent_todos") or {}).get("goal_acceptance_contract"),
        (asset.get("agent_todos") or {}).get("goal_acceptance_contract"),
    ) if isinstance(candidate, dict) and candidate.get("enabled") is True), None)
    return project_replan_context(goal_id=goal_id, agent_id=agent_id, runs=(), source_status=status_payload,
                                  goal_acceptance_contract=acceptance)


def project_asset_source(item: dict[str, Any] | None) -> str:
    if isinstance(item, dict) and isinstance(item.get("project_asset"), dict):
        return "project_asset"
    return "legacy_raw_fallback"


def project_asset_source_line(source: str) -> str:
    if source == "project_asset":
        return "project_asset（owner/gate/next/stop 来自 attention_queue.project_asset）"
    return (
        "legacy/raw fallback（未收到 project_asset；summary/action/todos "
        "来自 raw queue/status 降级判断，不能当 owner/gate/stop authority）"
    )
