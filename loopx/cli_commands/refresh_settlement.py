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

from ..command_invocation import loopx_cli_argv
from ..control_plane.effect_program import (
    SETTLEMENT_RECEIPT_SCHEMA_VERSION, SettlementStepKind, decode_settlement_receipt_payload,
)
from ..control_plane.quota.settlement import (
    SettlementIdentity, attach_settlement_progress, read_heartbeat_settlement, render_owed_host_command,
    settlement_result_payload,
)


def settle_requested_refresh(payload, args, registry_path, goal_ref):
    """Keep refresh's optional settlement composition with its existing owner."""
    if not getattr(args, "settle_turn", False):
        return
    from ..control_plane.projects.registry_codec import load_registry
    from ..paths import resolve_runtime_root

    runtime_root = resolve_runtime_root(load_registry(registry_path), args.runtime_root)
    settle_refreshed_turn(payload, registry_path=registry_path, runtime_root=runtime_root,
        goal_id=args.goal_id, agent_id=args.agent_id, todo_id=args.todo_id,
        replan_obligation_id=args.replan_obligation_id,
        turn_instance_id=args.turn_instance_id, goal_ref=goal_ref,
        delivery_workspace_path=(
            Path(args.delivery_workspace_path)
            if args.delivery_workspace_path else None
        ))
    if getattr(args, "settlement_receipt", False):
        from ..control_plane.quota.error_codes import QuotaDecisionTransportError
        try:
            view = present_settled_refresh(payload, runtime_root=runtime_root)
        except QuotaDecisionTransportError as exc:
            payload.update(ok=False, error=str(exc))
            return  # Exact committed receipts stay visible; never re-admit.
        if view is not payload:
            payload.clear()
            payload.update(view)


def _has_verified_fresh_spend(
    refresh: Mapping[str, Any], child: Mapping[str, Any], identity: Mapping[str, Any],
) -> bool:
    # The pinned child already read the authoritative receipt chain after
    # append. Match it to the parent's own writeback readback before reuse.
    progress = refresh["settlement_progress"]
    source = progress["quota_spend_source"]
    effect_id = identity["effect_id"]
    if (progress.get("state") != "spend_required"
            or child.get("ok") is not True or child.get("appended") is not True
            or child.get("idempotent_replay") is not False or child.get("transaction_repaired") is not False
            or child.get("dry_run") is not False or child.get("mode") != "spend-slot"
            or child.get("settlement_identity") != identity or child.get("source") != source
            or child.get("effect_id") != f"{effect_id}#{SettlementStepKind.QUOTA_SPEND.value}"
            or child.get("settlement_owed") is not None or child.get("result_delivery_error") is not None
            or child.get("settlement_progress") != {
                "schema_version": "quota_settlement_progress_v0", "state": "settled",
                "next_step": None, "quota_spend_source": source,
            }):
        return False
    before, after = refresh.get("settlement_result"), child.get("settlement_result")
    for result in (before, after):
        if (not isinstance(result, dict) or set(result) != {"ok", "receipts", "failure"}
                or result["ok"] is not True or result["failure"] is not None
                or not isinstance(result["receipts"], list)):
            return False
    receipts = after["receipts"]
    if (len(receipts) != 3 or receipts[:-1] != before["receipts"]
            or any(not isinstance(row, dict) or row.get("schema_version") != SETTLEMENT_RECEIPT_SCHEMA_VERSION
                   for row in receipts)):
        return False
    event = child.get("rollout_event")
    if (not isinstance(event, dict) or event.get("event_kind") != "quota_spend"
            or not isinstance(event.get("event_id"), str) or not event["event_id"]):
        return False
    try:
        checked = tuple(decode_settlement_receipt_payload(row) for row in receipts)
    except RuntimeError:
        return False
    return (
        tuple(row.step_kind for row in checked)
        == (SettlementStepKind.VALIDATION, SettlementStepKind.DURABLE_WRITEBACK, SettlementStepKind.QUOTA_SPEND)
        and all(row.effect_id == effect_id and row.status == "committed" and row.source_ref for row in checked)
        and checked[-1].source_ref == f"rollout_event:{event['event_id']}"
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
            if "command_argv" in owed:
                command = owed["command_argv"]
                if (not isinstance(command, list) or not command or
                        not all(isinstance(arg, str) for arg in command) or
                        render_owed_host_command(command) != owed["command"]):
                    raise ValueError("owed quota command does not match its argument vector")
            else:
                command = shlex.split(owed["command"])
            route = loopx_cli_argv()
            if command[:len(route)] != route:
                raise ValueError("owed quota command does not match the current CLI route")
            command_args = command[len(route):]
            # Validate the owner-rendered argv through the actual CLI parser too:
            # a projector bug must not debit a different valid Turn before readback.
            from ..cli_runtime import build_cli_parser
            from .quota_registration import register_quota_command

            parser, subparsers = build_cli_parser()
            register_quota_command(subparsers)
            args = parser.parse_args(["--format", "json", *command_args])
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
                "import runpy; runpy.run_module('loopx.entrypoint', run_name='__main__')"
            )
            result = subprocess.run(
                [sys.executable, "-P", "-c", entry, str(Path(__file__).resolve().parents[2]),
                 "--format", "json", *command_args],
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
    # A refused or partially delivered child can carry recovery controls and
    # its own bounded reader. Preserve that view before attempting readback.
    payload["quota_spend"] = spent if error is not None else {key: spent[key] for key in (
        "ok", "appended", "idempotent_replay", "json_path", "rollout_event",
        "reward_memory_ingest", "error", "error_code",
    ) if key in spent}
    if error is None and not settled_replay and _has_verified_fresh_spend(payload, spent, expected):
        payload["settlement_progress"] = spent["settlement_progress"]
        payload["settlement_result"] = spent["settlement_result"]
        payload.pop("settlement_owed", None)
        return
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
    """Keep the receipt view bounded and retain complete recovery controls.

    This opt-in output adapter follows the original owners and receipt readback.
    A wide result requires reading the complete controls before acting.
    """
    if payload.get("dry_run"):
        return payload
    progress = payload.get("settlement_progress")
    failed_with_spend = payload.get("ok") is False and bool(payload.get("quota_spend"))
    if not failed_with_spend and (payload.get("ok") is not True
            or not isinstance(progress, dict) or progress.get("state") != "settled"
            or payload.get("settlement_owed") is not None):
        return payload
    from .quota_capture import budgeted_settlement_result
    from ..command_invocation import current_loopx_cli_argv
    from ..control_plane.turn_driver.native_context_profile import NATIVE_CONTEXT_PAGE_OUTPUT_BYTES

    reader_argv = current_loopx_cli_argv()
    if failed_with_spend:
        from ..control_plane.quota.error_codes import QuotaDecisionTransportError

        try:
            return budgeted_settlement_result(payload, runtime_root=runtime_root,
                output_bytes=NATIVE_CONTEXT_PAGE_OUTPUT_BYTES, reader_argv=reader_argv)
        except QuotaDecisionTransportError as exc:
            # Retain the original refusal and retry controls if capture fails.
            return {**payload, "result_delivery_error": {"error_code": exc.error_code, "reason": str(exc)}}

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
        reader_argv=reader_argv)
