"""Expose enrolled Turn progress through existing status consumer fields."""
from pathlib import Path
from typing import Any

from ..goals.checkpoint_context_io import pending_first_delivery_progress
from ..quota.states import quota_item_is_paused


def attach_first_delivery_status(payload: dict[str, Any], *, runtime_root: Path, agent_id: str | None) -> None:
    for item in payload.get("attention_queue", {}).get("items", []):
        goal_id = item.get("goal_id")
        if not goal_id:
            continue
        progress = pending_first_delivery_progress(runtime_root, goal_id, agent_id)
        if progress is None:
            continue
        item["first_delivery_progress"] = progress
        if quota_item_is_paused(item) or item.get("requires_user_action") is True:
            continue
        action = progress["next_action"]
        item["recommended_action"] = action
        for field in ("project_asset", "goal_channel_projection"):
            projection = item.get(field)
            if isinstance(projection, dict):
                projection["next_action"] = action
