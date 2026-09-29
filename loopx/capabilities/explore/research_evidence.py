"""Transport for the Explore research contract; TypeScript owns its rules."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.effect_runtime import EffectRuntimeRemoteError, effect_runtime_result
from ...control_plane.runtime.public_safety import validate_public_safe_value
from ...control_plane.work_items.progress_observation import normalize_progress_observation
from ...control_plane.work_items.progress_result import PROGRESS_OBSERVATION_SCHEMA_VERSION
from ...file_lock import exclusive_file_lock


def _research_result(method: str, params: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = effect_runtime_result(method, params)
    except EffectRuntimeRemoteError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(result, dict):
        raise ValueError("Explore research runtime returned an invalid result")
    return result


def normalize_research_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    validate_public_safe_value(value, path="research_observation")
    raw = dict(value)
    progress = raw.get("progress")
    if not isinstance(progress, Mapping):
        raise ValueError("research observation requires a progress object")
    if progress.get("schema_version") != PROGRESS_OBSERVATION_SCHEMA_VERSION:
        raise ValueError("research progress requires its explicit schema_version")
    if "evidence_ids" in progress and not isinstance(progress["evidence_ids"], list):
        raise ValueError("research progress evidence_ids must be an array")
    raw["progress"] = normalize_progress_observation(progress)
    if set(progress) - set(raw["progress"]) - {"fingerprint"}:
        raise ValueError("research progress contains unknown or empty fields")
    return _research_result("explore.research.normalize", {"observation": raw})


def project_research_frontier(projection: Mapping[str, Any], *, candidate_sources: list[dict[str, Any]]) -> dict[str, Any]:
    return _research_result("explore.research.frontier", {
        "goal_id": projection["goal_id"], "nodes": projection["nodes"], "edges": projection["edges"],
        "candidate_sources": candidate_sources,
    })


def validate_research_append(
    events: list[dict[str, Any]], event: Mapping[str, Any], *, proposal: Mapping[str, Any] | None = None,
) -> None:
    """Protect typed evidence when callers use the existing node/batch writers."""
    observation = event.get("research_observation")
    if not observation:
        return
    from .result_log import build_explore_result_projection
    for previous in events:
        if previous.get("research_observation") == observation:
            if dict(event) != previous:
                raise ValueError("research observation replay cannot rewrite its node revision; use explore observe")
            return
    projection = proposal or build_explore_result_projection([*events, event], goal_id=str(event["goal_id"]))
    _research_result("explore.research.validate_attribution", {
        "observation": observation, "nodes": projection["nodes"], "edges": projection["edges"],
    })


def append_research_observation(
    path: Path, *, goal_id: str, observation: Mapping[str, Any], agent_id: str | None = None,
) -> dict[str, Any]:
    # Use the existing append-only node revision transport. Lock attribution,
    # replay and append together so an input update cannot slip between them.
    import json
    from .result_log import (
        build_explore_node_event, build_explore_result_projection, load_explore_result_events_strict,
    )

    canonical = normalize_research_observation(observation)
    with exclusive_file_lock(path):
        events = load_explore_result_events_strict(path, goal_id=goal_id)
        projection = build_explore_result_projection(events, goal_id=goal_id)
        node = next((node for node in projection["nodes"] if node["node_id"] == canonical["explore_node_id"]), None)
        if node is None:
            raise ValueError("explore_node_id must reference an existing same-goal node")
        # Exact replay is read-only even after inputs are invalidated. It must
        # never refresh old evidence against a newer input revision.
        if any(event.get("research_observation") == canonical for event in events):
            return {"ok": True, "replayed": True, "written": False, "observation": canonical}
        _research_result("explore.research.validate_attribution", {
            "observation": canonical, "nodes": projection["nodes"], "edges": projection["edges"],
        })
        event = build_explore_node_event(
            goal_id=goal_id, title=node["title"], node_id=node["node_id"], node_kind=node["node_kind"],
            status=node["status"], summary=node["summary"], blocked_reason=node["blocked_reason"],
            parent_id=node["parent_id"] or None, agent_id=agent_id or node["agent_id"] or None,
            evidence_refs=node["evidence_refs"], tags=node["tags"], supersedes=node["supersedes"] or None,
            research_observation=canonical,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return {"ok": True, "written": True, "replayed": False, "observation": canonical}
