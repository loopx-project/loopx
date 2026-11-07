"""Compose an explicitly requested refresh/spend through the existing CLI owner.

This adapter has no admission or accounting policy. Only a verified refresh's
canonical settlement_owed command may execute, with its original identity.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..control_plane.quota.settlement import (
    SettlementIdentity, attach_settlement_progress, read_heartbeat_settlement, settlement_result_payload,
)


def settle_refreshed_turn(
    payload: dict[str, Any], *, registry_path: Path, runtime_root: Path,
    goal_id: str, agent_id: str, todo_id: str | None,
    replan_obligation_id: str | None, turn_instance_id: str,
    goal_ref: Mapping[str, str] | None,
    delivery_workspace_path: Path | None = None,
) -> None:
    """Follow one owed step, then verify its exact receipts; never collect a guard.

    A failed/ambiguous child is not retried here. The original refresh or owed
    command remains replayable under the shared exactly-once settlement owner.
    """
    if payload.get("ok") is not True or payload.get("dry_run"):
        return
    owed = payload.get("settlement_owed")
    settled_replay = not isinstance(owed, dict) and payload.get("settlement_progress", {}).get("state") == "settled"
    if not isinstance(owed, dict) and not settled_replay:
        return  # Missing checkpoints and typed no-spend closeout.
    identity = payload.get("settlement_identity")
    expected = SettlementIdentity(goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
        replan_obligation_id=replan_obligation_id, turn_instance_id=turn_instance_id).as_dict()
    if (not isinstance(identity, dict)
            or any(identity.get(k) != v for k, v in expected.items() if k != "schema_version")
            or isinstance(owed, dict) and (owed.get("schema_version") != "turn_settlement_owed_v0"
                or owed.get("kind") != "quota_spend"
                or any(owed.get(k) != v for k, v in expected.items() if k != "schema_version"))):
        payload.update(ok=False, error="refresh settlement command does not match its original identity")
        return
    error = None
    spent: dict[str, Any] = {}
    # A settled refresh replay still needs all three receipts below, but no child.
    if not settled_replay:
        try:
            workspace = delivery_workspace_path.expanduser().resolve() if delivery_workspace_path else None
            command = shlex.split(owed["command"])
            # Validate the owner-rendered argv through the actual CLI parser too:
            # a projector bug must not debit a different valid Turn before readback.
            from ..cli import build_parser
            args = build_parser().parse_args(["--format", "json", *command[1:]])
            if (args.command != "quota" or args.quota_command != "spend-slot"
                    or not args.execute or args.slots != 1
                    or Path(args.registry).expanduser().resolve() != registry_path.resolve()
                    or Path(args.runtime_root).expanduser().resolve() != runtime_root.resolve()
                    or args.source != payload["settlement_progress"]["quota_spend_source"]
                    or getattr(args, "goal_instance_id", None) != (goal_ref or {}).get("goal_instance_id")
                    or any(getattr(args, key, None) != value for key, value in (
                        ("goal_id", goal_id), ("agent_id", agent_id), ("todo_id", todo_id),
                        ("replan_obligation_id", replan_obligation_id), ("turn_instance_id", turn_instance_id)))):
                raise ValueError("owed quota command does not preserve the exact original binding")
            # Keep the validated producing cwd for accounting, but pin imports to
            # this CLI's package: the delivered worktree may contain another LoopX.
            # -P excludes the child cwd from Python's initial module search path.
            # Retain the complete CLI guards, hooks and rollout receipts; neither
            # ambient PATH nor a lower-level debit may replace that adapter.
            entry = (
                "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                "import runpy; runpy.run_module('loopx.cli', run_name='__main__')"
            )
            result = subprocess.run(
                [sys.executable, "-P", "-c", entry, str(Path(__file__).resolve().parents[2]),
                 "--format", "json", *command[1:]],
                cwd=workspace,
                capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
            )
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise ValueError("quota settlement CLI returned a non-object result")
            spent = value
            if result.returncode or spent.get("ok") is not True:
                error = str(spent.get("error") or spent.get("reason") or "quota settlement CLI failed")
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired, SystemExit) as exc:
            error = str(exc)
    try:
        readback = read_heartbeat_settlement(
            runtime_root, registry_path=registry_path, goal_ref=goal_ref,
            goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
            replan_obligation_id=replan_obligation_id, turn_instance_id=turn_instance_id,
            include_record_bodies=False,
        )
    except Exception as exc:
        payload["ok"] = False
        payload["error"] = f"Writeback retained; exact quota settlement readback failed: {exc}. Retry the original identity."
        return
    if readback is None:
        payload["ok"] = False
        payload["error"] = error or "exact settlement readback unavailable; retry the original identity"
        return
    attach_settlement_progress(payload, readback, registry_path=registry_path,
        runtime_root=runtime_root, goal_ref=goal_ref)
    payload["quota_spend"] = {key: spent[key] for key in (
        "ok", "appended", "idempotent_replay", "json_path", "rollout_event",
        "reward_memory_ingest", "error", "error_code",
    ) if key in spent}
    payload["settlement_result"] = settlement_result_payload(readback.settlement)
    if payload["settlement_progress"]["state"] == "spend_required":
        payload.update(ok=False, error=error or "exact quota receipt is uncommitted; recover the returned original settlement_owed")
    elif readback.settlement.failure is not None or spent and spent.get("ok") is not True:
        payload["ok"] = False
        payload["error"] = error or readback.settlement.failure.reason
    elif error:
        # Receipt readback, not child transport success, proves commitment.
        payload["quota_spend"]["transport_warning"] = error


def present_settled_refresh(payload: dict[str, Any], *, runtime_root: Path) -> dict[str, Any]:
    """Keep controls/receipts inline; retain successful diagnostic snapshots.

    This opt-in output adapter runs after the original owners and exact receipt
    readback. Errors, pending recovery and unknown fields remain fully visible.
    It neither changes a result nor certifies model-context retention.
    """
    progress = payload.get("settlement_progress")
    if (payload.get("ok") is not True or payload.get("dry_run")
            or not isinstance(progress, dict) or progress.get("state") != "settled"
            or payload.get("settlement_owed") is not None):
        return payload
    from .quota_capture import budgeted_settlement_result
    from ..command_invocation import current_loopx_cli_argv
    from ..control_plane.turn_driver.native_context_profile import NATIVE_CONTEXT_PAGE_OUTPUT_BYTES

    supporting = ["registry", "runtime_root", "project", "json_path", "markdown_path", "index_path",
                  "health_check", "state", "registry_goal", "todo_source"]
    # Only fully delivered projection diagnostics are supporting material.
    # Failed, pending, unknown or warning-bearing owners stay inline unchanged.
    outbox = payload.get("projection_outbox")
    if (isinstance(outbox, dict) and outbox.get("schema_version") == "loopx_todo_projection_delivery_v0"
            and set(outbox) <= {"schema_version", "provider_revision", "observed_provider_revision", "status",
                "next_action", "delivery_attempts", "source", "goal_id", "state_file", "project", "source_authority",
                "todo_count", "changed", "executed", "source_sha256", "rendered_sha256", "narrative_sha256",
                "section_record_sha256", "active_fields_omitted", "omitted_active_records", "parse_render_parity",
                "narrative_preserved", "legacy_fallback_used"}
            and outbox.get("status") in {"delivered", "current"} and outbox.get("next_action") == "finish"
            and outbox.get("parse_render_parity") is True and outbox.get("narrative_preserved") is True
            and outbox.get("legacy_fallback_used") is False):
        supporting.append("projection_outbox")
    # These are completed IO snapshots from the existing typed owners. Keep
    # their complete source in the same result capture, not another RPC or
    # state/permission decision. Unknown keys and nonterminal results stay inline.
    route = payload.get("runtime_projection_route")
    if (isinstance(route, dict) and set(route) <= {"schema_version", "route_id", "status",
            "projection_required", "declaration_source", "match_count", "source_mirror_match_count",
            "conflict_count", "unreadable_target_count", "source_registry_sha256_16",
            "source_runtime_sha256_16", "target_runtime_sha256_16", "projection_enabled",
            "projection_marker_field"}
            and route.get("schema_version") == "runtime_projection_route_v0"
            and route.get("status") == "single_runtime" and route.get("declaration_source") == "source_runtime_fallback"
            and route.get("projection_required") is False
            and route.get("projection_enabled") is False
            and all(type(route.get(key)) is int and route[key] == 0 for key in (
                "match_count", "source_mirror_match_count", "conflict_count", "unreadable_target_count"))):
        supporting.append("runtime_projection_route")
    shared = payload.get("shared_runtime_projection")
    if (isinstance(shared, dict) and set(shared) <= {"ok", "status", "dry_run",
            "raw_artifacts_copied", "recommended_action_copied"}
            and shared.get("ok") is True and shared.get("status") == "disabled"
            and shared.get("dry_run") is False and shared.get("raw_artifacts_copied") is False
            and shared.get("recommended_action_copied") is False):
        supporting.append("shared_runtime_projection")
    explore = payload.get("explore_graph_sync")
    if (isinstance(explore, dict) and set(explore) <= {"ok", "schema_version", "goal_id", "enabled",
            "trigger", "external_sink_delivery_authorized", "status", "delivery_postcondition"}
            and explore.get("schema_version") == "loopx_explore_graph_activation_v0"
            and explore.get("ok") is True and explore.get("enabled") is False
            and explore.get("status") == "disabled" and explore.get("external_sink_delivery_authorized") is False
            and explore.get("delivery_postcondition") == {"schema_version": "loopx_explore_graph_delivery_postcondition_v0",
                "required": False, "satisfied": True, "disposition": "not_required", "retry_required": False,
                "blocks_delivery": False, "required_action": None}):
        supporting.append("explore_graph_sync")
    recommendation = payload.get("recommended_action_resolution")
    if (isinstance(recommendation, dict)
            and set(recommendation) <= {"schema_version", "recommended_action", "recommended_action_source",
                "authority", "settlement_alignment", "todo_id", "selection_binding", "claim_required_before_work"}
            and recommendation.get("schema_version") == "refresh_recommendation_v0"
            and recommendation.get("settlement_alignment") == "exact"
            and recommendation.get("recommended_action") == payload.get("recommended_action")
            and recommendation.get("recommended_action_source") == payload.get("recommended_action_source")):
        # This completed Turn's recommendation is an audit snapshot; continuing
        # work still needs the fresh guard already required by the plan.
        supporting.extend(("recommended_action", "recommended_action_source", "recommended_action_resolution"))
    sync = payload.get("global_sync")
    if (isinstance(sync, dict) and set(sync) <= {"enabled", "global_registry", "synced_goal_ids", "wrote"}
            and sync.get("enabled") is False and sync.get("wrote") is False
            and sync.get("synced_goal_ids") == []):
        supporting.append("global_sync")
    for field, schema in (
        ("goal_channel_blocked_notice_sync", "loopx_goal_channel_blocked_notice_delivery_v0"),
        ("goal_channel_gate_sync", "loopx_goal_channel_gate_auto_delivery_v0"),
    ):
        sink = payload.get(field)
        if (isinstance(sink, dict) and set(sink) <= {"schema_version", "ok", "enabled", "status",
                "external_write_performed", "readback_verified", "delivery_postcondition"}
                and sink.get("schema_version") == schema and sink.get("ok") is True
                and sink.get("enabled") is False and sink.get("status") == "not_configured"
                and sink.get("external_write_performed") is False
                and sink.get("delivery_postcondition") == {"satisfied": True, "blocks_delivery": False}):
            supporting.append(field)
    return budgeted_settlement_result(payload, runtime_root=runtime_root,
        output_bytes=NATIVE_CONTEXT_PAGE_OUTPUT_BYTES, supporting_fields=tuple(supporting),
        reader_argv=current_loopx_cli_argv())
