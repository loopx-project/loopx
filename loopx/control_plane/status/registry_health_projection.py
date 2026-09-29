"""Registry health status projections inside the `status` bounded context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...history import load_registry
from ...paths import global_registry_path
from ...registry import registry_goals
from ..goals.global_registry_health import (
    collect_global_registry_health as _collect_global_registry_health,
)
from ..goals.global_registry_shadow import attach_global_registry_shadow_finding
from ..goals.path_resolution import (
    resolve_goal_local_path,
    same_path,
)
from ..runtime.time import parse_timestamp
from ..work_items.attention_item import attention_item
from ..work_items.attention_queue import (
    merge_global_registry_findings as _merge_global_registry_findings,
)
from .dreaming_projection import compact_dreaming_lane_badge
from .monitor_display_projection import build_project_asset


SOURCE_REGISTRY_SHADOW_FINDINGS = {
    "source_registry_missing",
    "stale_source_registry",
}


def merge_global_registry_attention_findings(
    *,
    health_items: list[dict[str, Any]],
    history_items: list[dict[str, Any]],
    findings: list[Any],
    goal_id_filter: str | None,
) -> None:
    def _registry_attention_item(**kwargs: Any) -> dict[str, Any]:
        return attention_item(
            **kwargs,
            build_project_asset=build_project_asset,
            compact_dreaming_lane_badge=compact_dreaming_lane_badge,
        )

    _merge_global_registry_findings(
        health_items=health_items,
        history_items=history_items,
        findings=findings,
        goal_id_filter=goal_id_filter,
        source_registry_shadow_findings=SOURCE_REGISTRY_SHADOW_FINDINGS,
        attention_item=_registry_attention_item,
        attach_global_registry_shadow_finding=attach_global_registry_shadow_finding,
    )


def collect_global_registry_health(
    *,
    registry_path: Path,
    runtime_root: Path,
    current_registry: dict[str, Any],
) -> dict[str, Any]:
    return _collect_global_registry_health(
        registry_path=registry_path,
        runtime_root=runtime_root,
        current_registry=current_registry,
        global_registry_path=global_registry_path,
        load_registry=load_registry,
        registry_goals=registry_goals,
        same_path=same_path,
        resolve_goal_local_path=resolve_goal_local_path,
        parse_timestamp=parse_timestamp,
    )
