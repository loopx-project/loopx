from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from collections.abc import Callable
from dataclasses import dataclass
from heapq import merge
from itertools import islice
from pathlib import Path
from typing import Any

from .file_lock import exclusive_run_index_lock
from .authority import goal_authority_registry_summary
from .control_plane import compact_control_plane_policy
from .control_plane.goals.activation import (
    GoalActivationState,
    goal_activation_state,
    normalize_goal_activation_state,
)
from .control_plane.quota.monitor_poll import QUOTA_MONITOR_POLL_CLASSIFICATION
from .control_plane.quota.slot_accounting import (
    QUOTA_SLOT_SPENT_CLASSIFICATION,
    QUOTA_SLOT_VOIDED_CLASSIFICATION,
)
from .control_plane.projects.registry_codec import (
    decode_registry_snapshot as decode_registry_snapshot,
    load_registry,
)
from .control_plane.runtime.run_artifacts import (
    next_run_artifact_paths,
    reserve_run_artifact_paths,
    run_file_stem,
)
from .control_plane.runtime.run_context_retention import (
    goal_semantic_history_from_runs,
    latest_runs_with_agent_context,
)
from .control_plane.runtime.run_index_duplicates import (
    classify_index_duplicate_records,
    duplicate_repair_decision,
    index_identity,
)
from .control_plane.runtime.run_index_rebuild import (
    apply_reviewed_collision_rebuild,
    build_collision_rebuild_plan,
    collision_review_groups,
    validate_reviewed_collision_plan,
)
from .control_plane.runtime.time import chronology_key, now_local_iso
from .doctor import PROMOTION_READINESS_CLASSIFICATIONS
from .execution_profile import compact_execution_profile
from .explore_graph import compact_explore_graph_policy
from .paths import registry_project_root, resolve_runtime_root
from .presentation.markdown import (
    markdown_code,
    markdown_table_row,
    markdown_table_separator,
)
from .quota import goal_quota_with_spend_ledger
from .registry import registry_goals

STATUS_NEUTRAL_CLASSIFICATIONS = {
    QUOTA_SLOT_SPENT_CLASSIFICATION,
    QUOTA_SLOT_VOIDED_CLASSIFICATION,
    QUOTA_MONITOR_POLL_CLASSIFICATION,
    *PROMOTION_READINESS_CLASSIFICATIONS,
}
AGENT_LANE_PROGRESS_SCOPE = "agent_lane"
REGISTRY_ATTENTION_FIELDS = (
    "waiting_on",
    "attention_status",
    "operator_question",
    "recommended_action",
    "next_handoff_condition",
)


@dataclass(frozen=True, slots=True)
class RunIndexAudit:
    goal_id: str
    index_path: Path
    raw_index_records: int
    unique_runs: int
    legacy_runtime_goal: bool


@dataclass(frozen=True, slots=True)
class RunHistoryAudit:
    registry_path: Path
    runtime_root: Path
    goal_id: str | None
    activation_state_filter: str | None
    include_runtime_goals: bool
    goal_count: int
    run_count: int
    goals: tuple[RunIndexAudit, ...]

    def matches(
        self,
        *,
        registry_path: Path,
        runtime_root: Path,
        goal_id: str | None,
        activation_state_filter: GoalActivationState | str | None,
        include_runtime_goals: bool,
    ) -> bool:
        normalized_activation = (
            normalize_goal_activation_state(activation_state_filter).value
            if activation_state_filter is not None
            else None
        )
        return (
            self.registry_path == registry_path.expanduser().resolve()
            and self.runtime_root == runtime_root.expanduser().resolve()
            and self.goal_id == (str(goal_id or "").strip() or None)
            and self.activation_state_filter == normalized_activation
            and self.include_runtime_goals is include_runtime_goals
        )


@dataclass(frozen=True, slots=True)
class StatusHistoryCollection:
    status_history: dict[str, Any]
    contract_audit: RunHistoryAudit


@dataclass(frozen=True, slots=True)
class RunIndexSnapshot:
    records: list[dict[str, Any]]
    raw_count: int
    digest: str | None


def now_local() -> str:
    return now_local_iso()


_chronology_key = chronology_key


def unique_run_paths(runs_dir: Path, generated_at: str) -> tuple[Path, Path]:
    return next_run_artifact_paths(runs_dir, run_file_stem(generated_at))


def reserve_unique_run_paths(runs_dir: Path, generated_at: str) -> tuple[Path, Path]:
    """Atomically reserve a run JSON path for concurrent append writers."""
    return reserve_run_artifact_paths(runs_dir, run_file_stem(generated_at))


def write_reserved_run_artifacts(
    *,
    runs_dir: Path,
    generated_at: str,
    record: dict[str, Any],
    index_record: dict[str, Any],
    payload: dict[str, Any],
    render_markdown: Callable[[dict[str, Any]], str],
) -> None:
    from .control_plane.quota.usage_collector import ingest_usage_into_run_record
    from .control_plane.work_items.delivery_history import (
        require_consistent_delivery_claim,
    )

    require_consistent_delivery_claim(record)
    require_consistent_delivery_claim(index_record)

    # Producer call site for GH-C95: normalize any compact usage measurement onto
    # the durable run record and its index row before append. Fail closed here so
    # malformed or negative usage never enters run history.
    ingest_usage_into_run_record(record, index_record=index_record)
    # GH-C07: one lock per goal history index, shared with the repair path.
    index_path = runs_dir / "index.jsonl"
    with exclusive_run_index_lock(index_path, operation="history_run_append"):
        json_path, markdown_path = reserve_unique_run_paths(runs_dir, generated_at)
        index_record["json_path"] = str(json_path)
        index_record["markdown_path"] = str(markdown_path)
        payload["json_path"] = str(json_path)
        payload["markdown_path"] = str(markdown_path)
        payload["index_path"] = str(index_path)
        if isinstance(record.get("usage"), dict):
            payload["usage"] = dict(record["usage"])
        json_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(render_markdown(payload) + "\n", encoding="utf-8")
        with index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(index_record, ensure_ascii=False, allow_nan=False) + "\n")


def validate_goal_id_path_segment(goal_id: str) -> str:
    value = str(goal_id or "").strip()
    if not value:
        raise ValueError("goal id is required")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("goal id must be a single path segment")
    if Path(value).name != value:
        raise ValueError("goal id must not include path traversal")
    return value


def discover_goal_ids(
    runtime_root: Path,
    registry: dict[str, Any],
    requested_goal: str | None,
    *,
    include_runtime_goals: bool = True,
) -> list[str]:
    if requested_goal:
        return [requested_goal]

    ids = {str(goal.get("id")) for goal in registry_goals(registry)}
    goals_dir = runtime_root / "goals"
    if include_runtime_goals and goals_dir.exists():
        ids.update(path.name for path in goals_dir.iterdir() if path.is_dir())
    return sorted(ids)


def _indexed_artifact_exists(value: Any, *, artifact_root: Path | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    path = Path(text).expanduser()
    if not path.is_absolute() and artifact_root is not None:
        path = artifact_root / path
    return path.exists()


def load_index_snapshot(
    path: Path,
    *,
    artifact_root: Path | None = None,
) -> RunIndexSnapshot:
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        return RunIndexSnapshot(records=[], raw_count=0, digest=None)

    records: list[dict[str, Any]] = []
    positions: dict[tuple[str, str, str], int] = {}
    artifact_exists: dict[tuple[str, str], bool] = {}
    raw_count = 0
    digest = hashlib.sha256()

    def artifact_is_present(value: Any) -> bool:
        text = str(value or "").strip()
        cache_key = (text, str(artifact_root or ""))
        if cache_key not in artifact_exists:
            artifact_exists[cache_key] = _indexed_artifact_exists(
                value,
                artifact_root=artifact_root,
            )
        return artifact_exists[cache_key]

    with stream:
        for encoded_line in stream:
            digest.update(encoded_line)
            line = encoded_line.decode("utf-8")
            if not line.strip():
                continue
            raw_count += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue

            key = (
                str(item.get("generated_at") or ""),
                str(item.get("json_path") or ""),
                str(item.get("markdown_path") or ""),
            )
            item = dict(item)
            item["json_exists"] = artifact_is_present(item.get("json_path"))
            item["markdown_exists"] = artifact_is_present(item.get("markdown_path"))
            if key in positions:
                records[positions[key]].update(item)
            else:
                positions[key] = len(records)
                records.append(item)
    return RunIndexSnapshot(
        records=records,
        raw_count=raw_count,
        digest=f"sha256:{digest.hexdigest()}",
    )


def load_index(
    path: Path,
    *,
    artifact_root: Path | None = None,
) -> tuple[list[dict[str, Any]], int]:
    snapshot = load_index_snapshot(path, artifact_root=artifact_root)
    return snapshot.records, snapshot.raw_count


def latest_status_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in runs:
        if str(run.get("classification") or "") in STATUS_NEUTRAL_CLASSIFICATIONS:
            continue
        if str(run.get("progress_scope") or "") == AGENT_LANE_PROGRESS_SCOPE:
            continue
        return run
    return None


def collect_history(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str | None,
    limit: int,
    include_runtime_goals: bool = True,
    activation_state_filter: GoalActivationState | str | None = None,
    agent_lane_id: str | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .capabilities.machine_configuration.builtins import (
        build_builtin_machine_configuration_registry,
        project_goal_with_builtin_machine_configuration,
    )
    from .capabilities.machine_configuration.store import read_machine_configuration

    if registry is None:
        registry = load_registry(registry_path)
    machine_configuration = read_machine_configuration(
        runtime_root,
        registry=build_builtin_machine_configuration_registry(),
    )
    goal_meta = {
        str(goal.get("id")): project_goal_with_builtin_machine_configuration(
            goal, machine_configuration
        )
        for goal in registry_goals(registry)
    }
    activation_filter = (
        normalize_goal_activation_state(activation_state_filter)
        if activation_state_filter is not None
        else None
    )
    goals: list[dict[str, Any]] = []
    recent_runs: list[dict[str, Any]] = []
    run_count = 0
    recent_limit = max(0, limit)

    for current_goal_id in discover_goal_ids(
        runtime_root,
        registry,
        goal_id,
        include_runtime_goals=include_runtime_goals and activation_filter is None,
    ):
        registry_member = current_goal_id in goal_meta
        meta = goal_meta.get(current_goal_id) or {}
        activation_state = (
            goal_activation_state(meta)
            if registry_member
            else GoalActivationState.ACTIVE
        )
        if activation_filter is not None and activation_state is not activation_filter:
            continue
        index_path = runtime_root / "goals" / current_goal_id / "runs" / "index.jsonl"
        index_snapshot = load_index_snapshot(
            index_path,
            artifact_root=registry_project_root(registry_path),
        )
        runs = index_snapshot.records
        raw_count = index_snapshot.raw_count
        runs = [
            run
            for _, run in sorted(
                enumerate(runs),
                key=lambda item: (
                    *_chronology_key(item[1].get("generated_at")),
                    item[0],
                ),
                reverse=True,
            )
        ]
        for run in runs:
            run["goal_id"] = str(run.get("goal_id") or current_goal_id)
        run_count += len(runs)
        recent_runs = list(
            islice(
                merge(
                    recent_runs,
                    runs,
                    key=lambda item: _chronology_key(item.get("generated_at")),
                    reverse=True,
                ),
                recent_limit,
            )
        )

        adapter = meta.get("adapter") if isinstance(meta.get("adapter"), dict) else {}
        quota = goal_quota_with_spend_ledger(meta, runs) if registry_member else None
        goal_record = {
            "id": current_goal_id,
            "activation_state": activation_state.value,
            "display_name": meta.get("display_name") if registry_member else None,
            "domain": meta.get("domain"),
            "status": meta.get("status") if registry_member else "legacy-runtime",
            "repo": meta.get("repo") if registry_member else None,
            "state_file": meta.get("state_file") if registry_member else None,
            "registry_member": registry_member,
            "legacy_runtime_goal": not registry_member,
            "adapter_kind": adapter.get("kind"),
            "adapter_status": adapter.get("status"),
            "coordination": meta.get("coordination") if isinstance(meta.get("coordination"), dict) else None,
            "explore_graph": compact_explore_graph_policy(meta.get("explore_graph"))
            if isinstance(meta.get("explore_graph"), dict)
            else None,
            "spawn_policy": meta.get("spawn_policy") if isinstance(meta.get("spawn_policy"), dict) else None,
            "execution_profile": compact_execution_profile(meta.get("execution_profile")) if registry_member else None,
            "control_plane": compact_control_plane_policy(meta.get("control_plane")) if registry_member else None,
            "guards": meta.get("guards") if isinstance(meta.get("guards"), list) else [],
            "next_probe": meta.get("next_probe") if registry_member else None,
            "authority_registry": goal_authority_registry_summary(meta) if registry_member else None,
            "quota": quota,
            "index_path": str(index_path),
            "index_digest": index_snapshot.digest,
            "index_exists": index_path.exists(),
            "raw_index_records": raw_count,
            "unique_runs": len(runs),
            "latest_status_run": latest_status_run(runs),
            "latest_runs": latest_runs_with_agent_context(
                runs,
                limit=limit,
                agent_lane_id=agent_lane_id,
            ),
            "semantic_history": goal_semantic_history_from_runs(runs),
        }
        if registry_member:
            for field in REGISTRY_ATTENTION_FIELDS:
                if meta.get(field):
                    goal_record[field] = meta.get(field)
        goals.append(goal_record)

    return {
        "ok": True,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "goal_filter": goal_id,
        "goal_count": len(goals),
        "run_count": run_count,
        "goals": goals,
        "runs": recent_runs,
    }


def build_run_history_audit(
    history: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str | None,
    activation_state_filter: GoalActivationState | str | None,
    include_runtime_goals: bool,
) -> RunHistoryAudit:
    normalized_activation = (
        normalize_goal_activation_state(activation_state_filter).value
        if activation_state_filter is not None
        else None
    )
    return RunHistoryAudit(
        registry_path=registry_path.expanduser().resolve(),
        runtime_root=runtime_root.expanduser().resolve(),
        goal_id=str(goal_id or "").strip() or None,
        activation_state_filter=normalized_activation,
        include_runtime_goals=include_runtime_goals,
        goal_count=int(history.get("goal_count") or 0),
        run_count=int(history.get("run_count") or 0),
        goals=tuple(
            RunIndexAudit(
                goal_id=str(item.get("id") or ""),
                index_path=Path(str(item.get("index_path") or "")),
                raw_index_records=int(item.get("raw_index_records") or 0),
                unique_runs=int(item.get("unique_runs") or 0),
                legacy_runtime_goal=bool(item.get("legacy_runtime_goal")),
            )
            for item in history.get("goals") or []
            if isinstance(item, dict)
        ),
    )


def history_for_registry_members(
    history: dict[str, Any],
    *,
    limit: int,
) -> dict[str, Any]:
    goals = [
        goal
        for goal in history.get("goals") or []
        if isinstance(goal, dict) and goal.get("registry_member") is True
    ]
    recent_limit = max(0, limit)
    recent_runs = list(
        islice(
            merge(
                *(
                    goal.get("latest_runs", [])[:recent_limit]
                    for goal in goals
                    if isinstance(goal.get("latest_runs"), list)
                ),
                key=lambda item: _chronology_key(item.get("generated_at")),
                reverse=True,
            ),
            recent_limit,
        )
    )
    return {
        **history,
        "goal_count": len(goals),
        "run_count": sum(int(goal.get("unique_runs") or 0) for goal in goals),
        "goals": goals,
        "runs": recent_runs,
    }


def collect_status_history(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str | None,
    limit: int,
    status_include_runtime_goals: bool,
    activation_state_filter: GoalActivationState | str | None = None,
    agent_lane_id: str | None = None,
    registry: dict[str, Any] | None = None,
) -> StatusHistoryCollection:
    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        limit=limit,
        include_runtime_goals=True,
        activation_state_filter=activation_state_filter,
        agent_lane_id=agent_lane_id,
        registry=registry,
    )
    audit = build_run_history_audit(
        history,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        activation_state_filter=activation_state_filter,
        include_runtime_goals=True,
    )
    status_history = history
    if (
        not status_include_runtime_goals
        and not str(goal_id or "").strip()
        and activation_state_filter is None
    ):
        status_history = history_for_registry_members(history, limit=limit)
    return StatusHistoryCollection(
        status_history=status_history,
        contract_audit=audit,
    )


def inspect_index_duplicates(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str | None,
    limit: int,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(
        registry,
        runtime_root_override,
        registry_path=registry_path,
    )
    groups: list[dict[str, Any]] = []
    checked_goal_count = 0
    raw_index_records = 0
    duplicate_row_count = 0

    for current_goal_id in discover_goal_ids(runtime_root, registry, goal_id):
        checked_goal_count += 1
        index_path = runtime_root / "goals" / current_goal_id / "runs" / "index.jsonl"
        if not index_path.exists():
            continue

        grouped: dict[tuple[str, str, str], list[tuple[int, dict[str, Any]]]] = {}
        with index_path.open(encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                raw_index_records += 1
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                key = (
                    str(item.get("generated_at") or ""),
                    str(item.get("json_path") or ""),
                    str(item.get("markdown_path") or ""),
                )
                grouped.setdefault(key, []).append((line_number, item))

        for key, records in grouped.items():
            if len(records) <= 1:
                continue
            duplicate_row_count += len(records) - 1
            generated_at, json_path, markdown_path = key
            reward_records = sum(1 for _, record in records if isinstance(record.get("human_reward"), dict))
            classifications = sorted({str(record.get("classification") or "") for _, record in records})
            health_checks = sorted({str(record.get("health_check") or "") for _, record in records if record.get("health_check")})
            duplicate_classification = classify_index_duplicate_records([record for _, record in records])
            groups.append(
                {
                    "goal_id": current_goal_id,
                    "index_path": str(index_path),
                    "generated_at": generated_at,
                    "json_path": json_path,
                    "markdown_path": markdown_path,
                    "json_exists": _indexed_artifact_exists(
                        json_path,
                        artifact_root=registry_project_root(registry_path),
                    ),
                    "markdown_exists": _indexed_artifact_exists(
                        markdown_path,
                        artifact_root=registry_project_root(registry_path),
                    ),
                    "line_numbers": [line_number for line_number, _ in records],
                    "raw_rows": len(records),
                    "duplicate_rows": len(records) - 1,
                    "duplicate_kind": duplicate_classification.get("duplicate_kind"),
                    "severity": duplicate_classification.get("severity"),
                    "classifications": classifications,
                    "health_checks": health_checks,
                    "reward_overlay_rows": reward_records,
                    "repair_hint": duplicate_classification.get("repair_hint"),
                }
            )

    severity_priority = {"warning": 0, "info": 1}
    groups.sort(
        key=lambda item: (
            severity_priority.get(str(item.get("severity")), 99),
            str(item.get("goal_id")),
            str(item.get("generated_at")),
        )
    )
    limited_groups = groups[: max(0, limit)]
    return {
        "ok": True,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "goal_filter": goal_id,
        "checked_goal_count": checked_goal_count,
        "raw_index_records": raw_index_records,
        "duplicate_group_count": len(groups),
        "duplicate_row_count": duplicate_row_count,
        "groups": limited_groups,
        "truncated": len(groups) > len(limited_groups),
        "limit": limit,
    }


def repair_index_duplicates(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str | None,
    limit: int,
    execute: bool,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(
        registry,
        runtime_root_override,
        registry_path=registry_path,
    )
    checked_goal_count = 0
    raw_index_records = 0
    removed_row_count = 0
    preserved_reward_overlay_rows = 0
    unrepaired_group_count = 0
    groups: list[dict[str, Any]] = []

    for current_goal_id in discover_goal_ids(runtime_root, registry, goal_id):
        checked_goal_count += 1
        index_path = runtime_root / "goals" / current_goal_id / "runs" / "index.jsonl"
        if not index_path.exists():
            continue

        # GH-C07: read and rewrite the index under the same lock the append
        # path takes. A dry run only reports, so it must not block writers.
        lock = (
            exclusive_run_index_lock(index_path, operation="history_index_repair")
            if execute
            else nullcontext()
        )
        with lock:
            # The index is one JSON document per LF. `json.dumps(..., ensure_ascii=False)`
            # leaves U+0085/U+2028/U+2029 in a value verbatim, and `str.splitlines()`
            # treats those as line breaks, which tore one record into two unparsable
            # fragments. Frame on LF instead.
            raw_lines = index_path.read_text(encoding="utf-8").split("\n")
            grouped: dict[tuple[str, str, str], list[tuple[int, dict[str, Any]]]] = {}
            for line_number, line in enumerate(raw_lines, start=1):
                if not line.strip():
                    continue
                raw_index_records += 1
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                grouped.setdefault(index_identity(item), []).append((line_number, item))

            remove_lines: set[int] = set()
            for records in grouped.values():
                if len(records) <= 1:
                    continue
                decision = duplicate_repair_decision(records)
                removed_lines = list(decision.get("removed_line_numbers") or [])
                if decision.get("action") == "preserve_reward_overlay":
                    preserved_reward_overlay_rows += len(records) - 1
                elif decision.get("repairable"):
                    remove_lines.update(int(line_number) for line_number in removed_lines)
                    removed_row_count += len(removed_lines)
                else:
                    unrepaired_group_count += 1

                first_record = records[0][1]
                groups.append(
                    {
                        "goal_id": current_goal_id,
                        "index_path": str(index_path),
                        "generated_at": first_record.get("generated_at"),
                        "json_path": first_record.get("json_path"),
                        "markdown_path": first_record.get("markdown_path"),
                        "action": decision.get("action"),
                        "repairable": decision.get("repairable"),
                        "line_numbers": decision.get("line_numbers"),
                        "kept_line_numbers": decision.get("kept_line_numbers"),
                        "removed_line_numbers": removed_lines,
                        "reason": decision.get("reason"),
                    }
                )

            if execute and remove_lines:
                rewritten = [
                    line
                    for line_number, line in enumerate(raw_lines, start=1)
                    if line_number not in remove_lines
                ]
                tmp_path = index_path.with_suffix(index_path.suffix + ".tmp")
                tmp_path.write_text("".join(line + "\n" for line in rewritten), encoding="utf-8")
                tmp_path.replace(index_path)

    limited_groups = groups[: max(0, limit)]
    return {
        "ok": True,
        "dry_run": not execute,
        "repaired": bool(execute and removed_row_count),
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "goal_filter": goal_id,
        "checked_goal_count": checked_goal_count,
        "raw_index_records": raw_index_records,
        "removed_row_count": removed_row_count,
        "preserved_reward_overlay_rows": preserved_reward_overlay_rows,
        "unrepaired_group_count": unrepaired_group_count,
        "groups": limited_groups,
        "truncated": len(groups) > len(limited_groups),
        "limit": limit,
    }


def rebuild_index_artifact_collisions(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str | None,
    limit: int,
    reviewed_plan: dict[str, Any] | None,
    execute: bool,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(
        registry,
        runtime_root_override,
        registry_path=registry_path,
    )
    all_groups: list[dict[str, Any]] = []
    checked_goal_count = 0
    for current_goal_id in discover_goal_ids(runtime_root, registry, goal_id):
        checked_goal_count += 1
        index_path = runtime_root / "goals" / current_goal_id / "runs" / "index.jsonl"
        if index_path.exists():
            all_groups.extend(collision_review_groups(index_path, current_goal_id))

    groups = all_groups[: max(0, limit)]
    truncated = len(groups) < len(all_groups)
    current_plan = build_collision_rebuild_plan(
        groups,
        goal_filter=goal_id,
        total_collision_group_count=len(all_groups),
        truncated=truncated,
    )
    if execute:
        if reviewed_plan is None:
            raise ValueError(
                "rebuild-index-collisions --execute requires --review-plan-json from a reviewed dry-run"
            )
        plan_sha256 = validate_reviewed_collision_plan(reviewed_plan, current_plan)
        rebuilt_indexes = apply_reviewed_collision_rebuild(
            current_plan,
            plan_sha256=plan_sha256,
        )
    else:
        rebuilt_indexes = []

    return {
        "ok": True,
        "dry_run": not execute,
        "rebuilt": bool(execute and rebuilt_indexes),
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "goal_filter": goal_id,
        "checked_goal_count": checked_goal_count,
        "collision_group_count": len(groups),
        "total_collision_group_count": len(all_groups),
        "truncated": truncated,
        "review_required": not execute and bool(groups),
        "review_plan": current_plan,
        "rebuilt_indexes": rebuilt_indexes,
        "destructive_row_deletion": False,
    }


def render_index_collision_rebuild_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "# LoopX Artifact Collision Rebuild\n\n- ok: `False`\n- error: " + str(payload.get("error"))
    plan = payload.get("review_plan") or {}
    return "\n".join(
        [
            "# LoopX Artifact Collision Rebuild",
            "",
            f"- dry_run: `{payload.get('dry_run')}`",
            f"- rebuilt: `{payload.get('rebuilt')}`",
            f"- collision_groups: `{payload.get('collision_group_count')}`",
            f"- total_collision_groups: `{payload.get('total_collision_group_count')}`",
            f"- truncated: `{payload.get('truncated')}`",
            f"- review_required: `{payload.get('review_required')}`",
            f"- plan_sha256: `{plan.get('plan_sha256')}`",
            "- destructive_row_deletion: `False`",
            "- contract: `review the exact plan, then execute with --review-plan-json`",
        ]
    )


def render_index_duplicate_repair_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "# LoopX Index Duplicate Repair\n\n- ok: `False`\n- error: " + str(payload.get("error"))

    lines = [
        "# LoopX Index Duplicate Repair",
        "",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- repaired: `{payload.get('repaired')}`",
        f"- runtime_root: `{payload.get('runtime_root')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- goal_filter: `{payload.get('goal_filter')}`",
        f"- checked_goals: `{payload.get('checked_goal_count')}`",
        f"- raw_index_records: `{payload.get('raw_index_records')}`",
        f"- removed_rows: `{payload.get('removed_row_count')}`",
        f"- preserved_reward_overlay_rows: `{payload.get('preserved_reward_overlay_rows')}`",
        f"- unrepaired_groups: `{payload.get('unrepaired_group_count')}`",
        f"- truncated: `{payload.get('truncated')}`",
        "",
        markdown_table_row(
            ["goal", "generated_at", "action", "repairable", "kept", "removed", "reason"]
        ),
        markdown_table_separator(7),
    ]
    for group in payload.get("groups") or []:
        lines.append(
            markdown_table_row(
                [
                    markdown_code(group.get("goal_id")),
                    markdown_code(group.get("generated_at")),
                    markdown_code(group.get("action")),
                    markdown_code(group.get("repairable")),
                    markdown_code(group.get("kept_line_numbers")),
                    markdown_code(group.get("removed_line_numbers")),
                    group.get("reason"),
                ]
            )
        )
    return "\n".join(lines)


def render_history_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "# LoopX Run History\n\n- ok: `False`\n- error: " + str(payload.get("error"))

    lines = [
        "# LoopX Run History",
        "",
        f"- runtime_root: `{payload.get('runtime_root')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- goal_filter: `{payload.get('goal_filter')}`",
        f"- goals: `{payload.get('goal_count')}`",
        f"- unique_runs: `{payload.get('run_count')}`",
        "",
        markdown_table_row(["generated_at", "goal", "classification", "files", "action"]),
        markdown_table_separator(5),
    ]
    for run in payload.get("runs") or []:
        lines.append(
            markdown_table_row(
                [
                    markdown_code(run.get("generated_at")),
                    markdown_code(run.get("goal_id")),
                    markdown_code(run.get("classification")),
                    f"{run.get('json_exists')}/{run.get('markdown_exists')}",
                    run.get("recommended_action"),
                ]
            )
        )

    lines.extend(["", "## Goals"])
    for goal in payload.get("goals") or []:
        lines.append(
            "- "
            f"`{goal.get('id')}`: status=`{goal.get('status')}`, "
            f"registry_member=`{goal.get('registry_member')}`, "
            f"adapter=`{goal.get('adapter_kind')}:{goal.get('adapter_status')}`, "
            f"index_exists=`{goal.get('index_exists')}`, "
            f"records=`{goal.get('raw_index_records')}`, "
            f"unique_runs=`{goal.get('unique_runs')}`"
        )
    return "\n".join(lines)


def render_index_duplicate_inspection_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "# LoopX Index Duplicate Inspection\n\n- ok: `False`\n- error: " + str(payload.get("error"))

    lines = [
        "# LoopX Index Duplicate Inspection",
        "",
        f"- runtime_root: `{payload.get('runtime_root')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- goal_filter: `{payload.get('goal_filter')}`",
        f"- checked_goals: `{payload.get('checked_goal_count')}`",
        f"- raw_index_records: `{payload.get('raw_index_records')}`",
        f"- duplicate_groups: `{payload.get('duplicate_group_count')}`",
        f"- duplicate_rows: `{payload.get('duplicate_row_count')}`",
        f"- truncated: `{payload.get('truncated')}`",
        "",
        markdown_table_row(
            ["goal", "generated_at", "kind", "severity", "rows", "classifications", "repair_hint"]
        ),
        markdown_table_separator(7),
    ]
    for group in payload.get("groups") or []:
        classifications = ", ".join(str(item) for item in group.get("classifications") or [])
        lines.append(
            markdown_table_row(
                [
                    markdown_code(group.get("goal_id")),
                    markdown_code(group.get("generated_at")),
                    markdown_code(group.get("duplicate_kind")),
                    markdown_code(group.get("severity")),
                    markdown_code(group.get("line_numbers")),
                    markdown_code(classifications),
                    group.get("repair_hint"),
                ]
            )
        )
    return "\n".join(lines)
