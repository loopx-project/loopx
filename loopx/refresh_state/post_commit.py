"""Post-record metadata attachment, usage booking, dry-run packet, and global sync."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Protocol

from ..control_plane.goals.checkpoint_context_io import commit_checkpoint_run
from ..control_plane.goals.goal_frontier.outcome_continuity import (
    acceptance_gaps_from_outcome_checkpoint,
    outcome_vision_checkpoint_from_run,
)
from ..control_plane.quota.codex_session_usage import (
    book_codex_session_usage,
    usage_booking_lock_target,
)
from ..control_plane.quota.settlement import SettlementIdentity
from ..control_plane.quota.usage_collector import ingest_usage_into_run_record
from ..control_plane.runtime.local_state_write_correctness import (
    build_local_state_write_correctness_dry_run_packet,
)
from ..control_plane.runtime.runtime_projection_route import (
    compact_runtime_projection_route,
)
from ..control_plane.runtime.shared_runtime_refresh_projection import (
    build_shared_runtime_projection,
    write_shared_runtime_projection,
)
from ..file_lock import exclusive_file_lock
from ..global_registry import sync_project_registry_to_global

REPAIR_NOOP_SCHEMA_VERSION = "repair_noop_v0"


class BookCodexSessionUsageFn(Protocol):
    def __call__(
        self,
        record: dict[str, object],
        rollout_path: Path,
        index_path: Path,
        *,
        index_record: dict[str, object] | None = None,
    ) -> object: ...


class IngestUsageIntoRunRecordFn(Protocol):
    def __call__(
        self,
        record: dict[str, object],
        measurement: dict[str, object] | None = None,
        *,
        index_record: dict[str, object] | None = None,
    ) -> object: ...


def attach_post_record_qualification_metadata(
    *,
    record: dict[str, object],
    goal_id: str,
    normalized_agent_id: str,
    agent_vision: Mapping[str, object] | None,
    existing_agent_vision: Mapping[str, object] | None,
    vision_checkpoint: Mapping[str, object],
    checkpoint_supplement: bool,
    prior_writeback_run: Mapping[str, object] | None,
    canonical_snapshot: Mapping[str, object] | None,
    blocked_retry: Mapping[str, object] | None,
    delivery_workspace_causality: Mapping[str, str] | None,
    refresh_recovery: Mapping[str, object] | None,
    settlement_workspace_requirement: Mapping[str, object] | None,
    autonomous_replan_recorded: bool,
    repair_delta_contract: Mapping[str, object] | None,
    autonomous_replan_frontier_identity: str | None,
    requested_classification: str,
    classification: str,
    replan_semantic_delta: Mapping[str, object] | None,
    runtime_projection_route: Mapping[str, object],
    sync_global: bool,
) -> dict[str, object]:
    """Attach diagnostic continuity, ACK metadata, and compact projection route."""
    outcome_checkpoint = outcome_vision_checkpoint_from_run(
        record,
        goal_id=goal_id,
        agent_id=normalized_agent_id or None,
    )
    vision_source = agent_vision or existing_agent_vision
    outcome_gaps = acceptance_gaps_from_outcome_checkpoint(
        dict(vision_source) if vision_source is not None else None,
        outcome_checkpoint,
    )
    if outcome_gaps:
        record["vision_checkpoint"] = {
            **dict(vision_checkpoint),
            "acceptance_gaps": outcome_gaps,
        }
    source_snapshot = (
        prior_writeback_run.get("todo_source")
        if checkpoint_supplement and prior_writeback_run is not None
        else canonical_snapshot
    )
    snapshot_keys = (
        "source_authority",
        "store_identity",
        "provider_revision",
        "cursor",
    )
    if isinstance(source_snapshot, Mapping) and all(
        isinstance(source_snapshot.get(key), str) and source_snapshot[key]
        for key in snapshot_keys
    ):
        # Retain the source actually consumed by planning, before append.
        # A later optional hook must never substitute the current Todo head.
        record["todo_source"] = {
            key: str(source_snapshot[key]) for key in snapshot_keys
        }
    if blocked_retry is not None:
        record["blocked_retry"] = dict(blocked_retry)
    if delivery_workspace_causality:
        record["delivery_workspace_causality"] = dict(delivery_workspace_causality)
    if refresh_recovery:
        record["refresh_recovery"] = dict(refresh_recovery)
    if settlement_workspace_requirement:
        record["settlement_workspace_requirement"] = dict(
            settlement_workspace_requirement
        )
    if autonomous_replan_recorded:
        existing_ack = record.get("autonomous_replan_ack")
        ack: dict[str, object] = (
            dict(existing_ack)
            if isinstance(existing_ack, Mapping)
            else {
                "schema_version": "autonomous_replan_ack_v0",
                "recorded": False,
                "source": "refresh_state",
                "delta_contract": (
                    dict(repair_delta_contract)
                    if repair_delta_contract is not None
                    else None
                ),
            }
        )
        ack["requested"] = True
        if autonomous_replan_frontier_identity:
            ack["frontier_identity"] = autonomous_replan_frontier_identity
        if requested_classification != classification:
            ack["requested_classification"] = requested_classification
            record["autonomous_replan_noop"] = {
                "schema_version": REPAIR_NOOP_SCHEMA_VERSION,
                "classification": classification,
                "requested_classification": requested_classification,
                "reason": "autonomous replan ACK requested without a machine-visible repair delta",
            }
        record["autonomous_replan_ack"] = ack
    if replan_semantic_delta:
        existing_ack = record.get("autonomous_replan_ack")
        ack_with_delta: dict[str, object] = (
            dict(existing_ack)
            if isinstance(existing_ack, Mapping)
            else {
                "schema_version": "autonomous_replan_ack_v0",
                "recorded": True,
                "source": "refresh_state_semantic_delta",
            }
        )
        ack_with_delta["semantic_delta"] = dict(replan_semantic_delta)
        record["autonomous_replan_ack"] = ack_with_delta
    compact_route = dict(
        compact_runtime_projection_route(dict(runtime_projection_route))
    )
    compact_route["projection_enabled"] = bool(sync_global)
    compact_route["projection_marker_field"] = "shared_runtime_projection"
    record["runtime_projection_route"] = compact_route
    return compact_route


def attach_refresh_usage_to_projections(
    *,
    usage_booking_guard: ExitStack,
    record: dict[str, object],
    index_record: dict[str, object],
    payload: dict[str, object],
    runs_dir: Path,
    index_path: Path,
    normalized_agent_id: str,
    dry_run: bool,
    usage_codex_session: Path | None,
    usage_measurement: Mapping[str, object] | None,
    book_codex_session_usage_fn: BookCodexSessionUsageFn = book_codex_session_usage,
    ingest_usage_into_run_record_fn: IngestUsageIntoRunRecordFn = ingest_usage_into_run_record,
) -> None:
    """Book Codex session or inline usage into the refresh record and payload."""
    if usage_codex_session is not None:
        if not dry_run:
            runs_dir.mkdir(parents=True, exist_ok=True)
            usage_booking_guard.enter_context(
                exclusive_file_lock(
                    usage_booking_lock_target(runs_dir),
                    agent_id=normalized_agent_id or None,
                    operation="refresh-state-usage-booking",
                )
            )
        book_codex_session_usage_fn(
            record, usage_codex_session, index_path, index_record=index_record
        )
    elif usage_measurement is not None:
        ingest_usage_into_run_record_fn(
            record, dict(usage_measurement), index_record=index_record
        )
    record_usage = record.get("usage")
    if isinstance(record_usage, Mapping):
        payload["usage"] = dict(record_usage)


def commit_checkpoint_supplement_run(
    *,
    runtime_root: Path,
    registry_path: Path,
    state_file: Path,
    identity: SettlementIdentity,
    refresh_retry: Mapping[str, object],
    record: dict[str, object],
    index_record: dict[str, object],
    payload: dict[str, object],
    render_markdown_fn: Callable[[Mapping[str, object]], str],
    goal_ref: Mapping[str, str] | None,
    source_admission: Mapping[str, object] | None,
) -> None:
    """Commit a missing-checkpoint supplement run and reconcile projection fields."""
    saved = commit_checkpoint_run(
        runtime_root=runtime_root,
        registry_path=registry_path,
        state_file=state_file,
        identity=identity,
        refresh_retry=refresh_retry,
        record=record,
        index_record=index_record,
        markdown=render_markdown_fn(payload) + "\n",
        goal_ref=goal_ref,
        source_admission=source_admission,
    )
    for projection in (record, index_record, payload):
        existing_vc = projection.get("vision_checkpoint")
        if isinstance(existing_vc, dict):
            existing_vc["read_context"] = saved["context"]
    for projection in (index_record, payload):
        projection.update(
            {key: saved[key] for key in ("json_path", "markdown_path")}
        )
    if saved["replayed"]:
        payload.update(appended=False, idempotent_replay=True)


def build_refresh_dry_run_packet(
    *,
    goal_id: str,
    writer_id: str,
    classification: str,
    state_text: str,
    sync_global: bool,
    route_status: str,
    shared_runtime_root: Path | None,
) -> dict[str, object]:
    """Construct the dry-run write-correctness packet for a planned refresh."""
    expected_write_scopes = ["runtime_history"]
    if sync_global and route_status in {"resolved", "single_runtime"}:
        expected_write_scopes.append("global_registry")
    if shared_runtime_root:
        expected_write_scopes.append("shared_runtime_projection")
    patch_parts = [f"append refresh-state run classification={classification}"]
    if sync_global and route_status in {"resolved", "single_runtime"}:
        patch_parts.append("sync public-safe registry projection")
    elif sync_global:
        patch_parts.append(
            f"block global sync on {route_status} runtime projection route"
        )
    if shared_runtime_root:
        patch_parts.append("project compact refresh to registered shared runtime")
    packet = build_local_state_write_correctness_dry_run_packet(
        goal_id=goal_id,
        writer_id=writer_id,
        write_class="refresh_state",
        state_text=state_text,
        target_refs={
            "state_file_ref": "registry.goal.state_file",
            "run_history_ref": "runtime.goal.runs",
            "index_ref": "runtime.goal.runs.index",
            "global_registry_ref": (
                "runtime.registry.global"
                if sync_global and route_status in {"resolved", "single_runtime"}
                else None
            ),
            "shared_runtime_projection_ref": (
                "shared_runtime.goal.runs.index" if shared_runtime_root else None
            ),
        },
        patch_summary="; ".join(patch_parts),
        expected_write_scopes=expected_write_scopes,
        lease_ref=None,
        projection_status_surface=f"refresh-state dry-run: {classification}",
    )
    return dict(packet)


def sync_refresh_global_and_shared_projection(
    *,
    payload: dict[str, object],
    record: dict[str, object],
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    dry_run: bool,
    sync_global: bool,
    route_status: str,
    shared_runtime_root: Path | None,
    global_sync_runtime_root: Path | None,
    compact_route: Mapping[str, object],
) -> None:
    """Sync public-safe global registry metadata and compact shared-runtime projections."""
    if sync_global and route_status in {"missing", "ambiguous"}:
        payload["ok"] = False
        payload["partial_write"] = not dry_run
        payload["global_sync"] = {
            "ok": False,
            "enabled": False,
            "wrote": False,
            "reason": f"runtime projection route is {route_status}",
            "route_status": route_status,
        }
        payload["shared_runtime_projection"] = {
            "ok": False,
            "status": f"route_{route_status}",
            "dry_run": dry_run,
            "raw_artifacts_copied": False,
            "recommended_action_copied": False,
            "runtime_projection_route_id": compact_route.get("route_id"),
        }
        return
    if sync_global:
        global_sync = sync_project_registry_to_global(
            registry_path=registry_path,
            runtime_root_override=str(global_sync_runtime_root or runtime_root),
            goal_id=goal_id,
            dry_run=dry_run,
        )
        payload["global_sync"] = global_sync
        if shared_runtime_root and global_sync.get("ok"):
            projection_record, projection_index = build_shared_runtime_projection(
                record=record,
            )
            try:
                payload["shared_runtime_projection"] = (
                    write_shared_runtime_projection(
                        shared_runtime_root=shared_runtime_root,
                        goal_id=goal_id,
                        record=projection_record,
                        index_record=projection_index,
                        dry_run=dry_run,
                    )
                )
            except OSError as exc:
                payload["ok"] = False
                payload["partial_write"] = not dry_run
                payload["shared_runtime_projection"] = {
                    "ok": False,
                    "status": "write_failed",
                    "dry_run": dry_run,
                    "shared_runtime_root": str(shared_runtime_root),
                    "raw_artifacts_copied": False,
                    "recommended_action_copied": False,
                    "error": str(exc),
                }
        elif shared_runtime_root:
            payload["ok"] = False
            payload["partial_write"] = not dry_run
            payload["shared_runtime_projection"] = {
                "ok": False,
                "status": "blocked_by_global_sync",
                "dry_run": dry_run,
                "shared_runtime_root": str(shared_runtime_root),
                "raw_artifacts_copied": False,
                "recommended_action_copied": False,
            }
        else:
            payload["shared_runtime_projection"] = {
                "ok": True,
                "status": "not_required",
                "dry_run": dry_run,
                "raw_artifacts_copied": False,
                "recommended_action_copied": False,
            }
        return
    payload["global_sync"] = {
        "enabled": False,
        "global_registry": str(runtime_root / "registry.global.json"),
        "synced_goal_ids": [],
        "wrote": False,
    }
    payload["shared_runtime_projection"] = {
        "ok": True,
        "status": "disabled",
        "dry_run": dry_run,
        "raw_artifacts_copied": False,
        "recommended_action_copied": False,
    }
