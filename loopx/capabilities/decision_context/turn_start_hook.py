"""Host-local turn-start hook adapter for the TS-owned Decision Context binding."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
from typing import Any, overload

DECISION_CONTEXT_TURN_HOOK_ID = "decision_context.turn_context"
DECISION_CONTEXT_HOOK_CAPABILITY_ID = "decision-context"
DECISION_CONTEXT_HOOK_BINDINGS_DIRNAME = Path("decision-context") / "agent-bindings"


def _goal_scope_fields(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any]:
    from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
    from ...paths import registry_project_root

    goal = load_goal_from_registry(registry_path, goal_id)
    if not goal or not goal.get("repo") or not goal.get("state_file"):
        raise ValueError(
            "Decision Context hook requires a registered Goal with a project and state file"
        )
    project = Path(str(goal["repo"])).expanduser()
    if not project.is_absolute():
        project = registry_project_root(registry_path) / project
    owner = (project / str(goal["state_file"])).resolve()
    return {
        "schema_version": "decision_context_hook_request_v1",
        "goal_state_ref": str(owner),
        "goal_instance_id": goal.get("goal_instance_id"),
        "runtime_root": str(runtime_root.expanduser().resolve()),
        "goal_id": goal_id,
        "agent_id": agent_id,
        "registered_agents": registered_agent_ids_for_goal(goal),
    }


def decision_context_hook_binding(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    action: str = "read",
    **fields: Any,
) -> dict[str, Any]:
    from ...control_plane.effect_runtime import effect_runtime_result

    request = {
        **_goal_scope_fields(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
        ),
        "action": action,
        **fields,
    }
    result = effect_runtime_result("decision_context.hook", request)
    if not isinstance(result, dict):
        raise TypeError("invalid decision-context hook response")
    return result


def resolve_bound_decision_context_locators(
    *,
    registry_path: Path | None,
    runtime_root: Path | None,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any] | None:
    """Resolve an active local hook binding without exposing private paths publicly."""
    if registry_path is None or runtime_root is None:
        return None
    try:
        (runtime_root / DECISION_CONTEXT_HOOK_BINDINGS_DIRNAME).stat()
    except FileNotFoundError:
        return None
    except OSError:
        pass
    try:
        resolved = decision_context_hook_binding(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            action="resolve_local",
        )
    except Exception:  # noqa: BLE001 - callers surface explicit missing/invalid profiles
        return None
    local_binding = resolved.get("local_binding")
    return dict(local_binding) if isinstance(local_binding, Mapping) else None


def _freshness_counts_and_alerts(
    freshness: Mapping[str, Any] | None,
) -> tuple[dict[str, int], list[str]]:
    counts = {"fresh": 0, "stale": 0, "never_read": 0, "not_scanned": 0}
    alerted: list[str] = []
    if not isinstance(freshness, Mapping):
        return counts, alerted
    sources = freshness.get("sources")
    if not isinstance(sources, list):
        return counts, alerted
    for row in sources:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1
        reasons = row.get("alert_reasons")
        source_id = str(row.get("source_id") or "").strip()
        if source_id and (
            status in {"stale", "never_read"}
            or (isinstance(reasons, list) and len(reasons) > 0)
        ):
            alerted.append(source_id)
    return counts, sorted(set(alerted))


def _bound_cli_commands(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    spool_configured: bool,
) -> dict[str, str]:
    from ...command_invocation import loopx_cli_argv

    prefix = [
        *loopx_cli_argv(),
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "decision-context",
    ]
    scope_flags = ["--goal-id", goal_id, "--agent-id", agent_id, "--format", "json"]
    commands = {
        "turn_context": shlex.join([*prefix, "hook", "turn-context", *scope_flags]),
        "inspect_profile": shlex.join([*prefix, "inspect-profile", *scope_flags]),
        "source_manifest": shlex.join([*prefix, "source-manifest", *scope_flags]),
        "prepare_evidence": shlex.join(
            [*prefix, "prepare-evidence", "--decision-id", "turn-rebase", *scope_flags]
        ),
        "recall_context": shlex.join(
            [
                *prefix,
                "recall-context",
                "--context-scope-ref",
                "goal",
                "--query",
                "current decision context",
                "--query-summary",
                "Current turn decision context",
                *scope_flags,
            ]
        ),
    }
    if spool_configured:
        commands["capture_status"] = shlex.join([*prefix, "capture-status", *scope_flags])
    return commands


def _collect_public_safe_observation(
    *,
    goal_id: str,
    agent_id: str,
    runtime_root: Path,
    local_binding: Mapping[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    from .assembler import DecisionEvidenceRecords
    from .profile import resolve_decision_context_activation
    from .runtime import assemble_profile_decision_evidence
    from .sources import build_decision_source_manifest

    mode = str(local_binding.get("mode") or "prepare_evidence")
    profile_path = Path(str(local_binding["profile_path"])).expanduser()
    cursor_raw = local_binding.get("cursor_state_path")
    cursor_path = Path(str(cursor_raw)).expanduser() if cursor_raw else None
    spool_raw = local_binding.get("spool_path")
    spool_path = Path(str(spool_raw)).expanduser() if spool_raw else None

    activation, profile = resolve_decision_context_activation(
        goal_id=goal_id,
        agent_id=agent_id,
        profile_path=profile_path,
    )
    if activation.get("available") is not True or profile is None:
        reason = str(
            activation.get("reason_code")
            or activation.get("status")
            or "profile_unavailable"
        )
        return {
            "status": "unavailable",
            "error_code": f"decision_context_{reason}",
        }

    enabled_count = sum(1 for source in profile.sources if source.enabled)
    advisory_default = (
        "not_invoked" if profile.context_provider is not None else "not_configured"
    )

    if mode == "source_manifest":
        manifest = build_decision_source_manifest(
            goal_id=goal_id,
            observed_at=observed_at,
            sources=profile.sources,
        )
        p0_enabled = sorted(
            source.source_id
            for source in profile.sources
            if source.enabled and source.priority == "p0"
        )
        source_count_val = manifest.get("source_count")
        return {
            "status": "available",
            "observed_at": str(manifest["observed_at"]),
            "source_count": (
                source_count_val
                if isinstance(source_count_val, int)
                else len(profile.sources)
            ),
            "enabled_source_count": enabled_count,
            "changed_source_ids": [],
            "failed_source_ids": [],
            "alerted_source_ids": [],
            "uncovered_p0_source_ids": p0_enabled,
            "p0_complete": len(p0_enabled) == 0,
            "freshness_counts": {
                "fresh": 0,
                "stale": 0,
                "never_read": 0,
                "not_scanned": enabled_count,
            },
            "advisory_recall_status": advisory_default,
            "capture_pending_batch_count": None,
            "raw_content_captured": False,
            "private_locator_captured": False,
        }

    if mode == "capture_status":
        if spool_path is None:
            return {
                "status": "unavailable",
                "error_code": "decision_context_spool_not_configured",
            }
        from .capture import capture_profile_sources

        status_packet = capture_profile_sources(
            goal_id=goal_id,
            agent_id=agent_id,
            profile_path=profile_path,
            spool_path=spool_path,
            cursor_path=cursor_path,
            execute=False,
            health_runtime_root=runtime_root,
        )
        if status_packet.get("ok") is not True:
            reason = str(
                status_packet.get("reason_code")
                or status_packet.get("status")
                or "capture_unavailable"
            )
            return {
                "status": "unavailable",
                "error_code": f"decision_context_{reason}",
            }
        counts, alerted = _freshness_counts_and_alerts(
            status_packet.get("source_freshness")
            if isinstance(status_packet.get("source_freshness"), Mapping)
            else None
        )
        changed: list[str] = []
        failed: list[str] = []
        raw_sources = status_packet.get("sources")
        source_rows = raw_sources if isinstance(raw_sources, list) else []
        for row in source_rows:
            if not isinstance(row, Mapping):
                continue
            sid = str(row.get("source_id") or "").strip()
            if not sid:
                continue
            pending_count = row.get("pending_batch_count")
            if isinstance(pending_count, int) and pending_count > 0:
                changed.append(sid)
            streak = row.get("failure_streak")
            if (isinstance(streak, int) and streak > 0) or row.get("acquisition_held"):
                failed.append(sid)
        p0_uncovered = sorted(
            source.source_id
            for source in profile.sources
            if source.enabled
            and source.priority == "p0"
            and source.source_id in (set(alerted) | set(failed))
        )
        raw_pending_total = status_packet.get("pending_batch_count")
        pending_total = raw_pending_total if isinstance(raw_pending_total, int) else 0
        return {
            "status": "available",
            "observed_at": observed_at,
            "source_count": len(profile.sources),
            "enabled_source_count": enabled_count,
            "changed_source_ids": sorted(set(changed)),
            "failed_source_ids": sorted(set(failed)),
            "alerted_source_ids": alerted,
            "uncovered_p0_source_ids": p0_uncovered,
            "p0_complete": len(p0_uncovered) == 0,
            "freshness_counts": counts,
            "advisory_recall_status": advisory_default,
            "capture_pending_batch_count": pending_total,
            "raw_content_captured": False,
            "private_locator_captured": False,
        }

    # Default mode == "prepare_evidence": bounded read-only authority scan +
    # optional configured advisory recall receipt; cursors are never mutated.
    if profile.context_provider is not None and not profile.context_provider.get(
        "scope_ref"
    ):
        return {
            "status": "unavailable",
            "error_code": "decision_context_scope_ref_missing",
        }
    _act, assembly = assemble_profile_decision_evidence(
        goal_id=goal_id,
        agent_id=agent_id,
        profile_path=profile_path,
        decision_id="turn-start-observation",
        observed_at=observed_at,
        before=observed_at,
        cursor_path=cursor_path,
        rebase=lambda _collection: DecisionEvidenceRecords(),
        runtime_root=runtime_root,
    )
    if assembly is None:
        return {
            "status": "unavailable",
            "error_code": "decision_context_assembly_unavailable",
        }
    packet = assembly.public_packet()
    raw_scans = packet.get("source_scan_receipts")
    scans: list[Any] = raw_scans if isinstance(raw_scans, list) else []
    changed_ids = sorted(
        {
            str(scan["source_id"])
            for scan in scans
            if isinstance(scan, Mapping)
            and scan.get("status") == "completed"
            and isinstance(scan.get("changed_count"), int)
            and int(scan["changed_count"]) > 0
        }
    )
    failed_ids = sorted(
        {
            str(scan["source_id"])
            for scan in scans
            if isinstance(scan, Mapping)
            and scan.get("status") in {"failed", "unavailable"}
        }
    )
    raw_freshness = packet.get("source_freshness")
    counts, alerted = _freshness_counts_and_alerts(
        raw_freshness if isinstance(raw_freshness, Mapping) else None
    )
    raw_coverage = packet.get("source_coverage")
    coverage: Mapping[str, Any] = (
        raw_coverage if isinstance(raw_coverage, Mapping) else {}
    )
    raw_uncovered = coverage.get("incomplete_required_source_ids")
    uncovered_list = raw_uncovered if isinstance(raw_uncovered, list) else []
    uncovered_p0 = [str(item) for item in uncovered_list if str(item).strip()]
    raw_retrieval = packet.get("context_retrieval_receipt")
    retrieval: Mapping[str, Any] | None = (
        raw_retrieval if isinstance(raw_retrieval, Mapping) else None
    )
    recall_status = (
        str(retrieval.get("status") or "unavailable")
        if retrieval is not None
        else advisory_default
    )
    return {
        "status": "available",
        "observed_at": observed_at,
        "source_count": len(profile.sources),
        "enabled_source_count": enabled_count,
        "changed_source_ids": changed_ids,
        "failed_source_ids": failed_ids,
        "alerted_source_ids": alerted,
        "uncovered_p0_source_ids": sorted(set(uncovered_p0)),
        "p0_complete": bool(coverage.get("complete", len(uncovered_p0) == 0)),
        "freshness_counts": counts,
        "advisory_recall_status": recall_status,
        "capture_pending_batch_count": None,
        "raw_content_captured": False,
        "private_locator_captured": False,
    }


def read_decision_context_turn_context(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build the validated public-safe turn context view for one Goal/Agent."""
    explicit_now = (
        str(observed_at).strip()
        if observed_at and str(observed_at).strip()
        else None
    )
    projected: dict[str, Any] | None = None
    for _attempt in range(3):
        now = explicit_now or datetime.now(timezone.utc).isoformat()
        resolved = decision_context_hook_binding(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            action="resolve_local",
        )
        if not resolved.get("ok"):
            return resolved
        if resolved.get("status") == "absent":
            return {
                "ok": True,
                "status": "absent",
                "goal_id": goal_id,
                "agent_id": agent_id,
                "observation_count": 0,
                "current": resolved["current"],
            }

        current_view = resolved.get("current")
        expected_revision = (
            current_view.get("revision")
            if isinstance(current_view, Mapping)
            else None
        )
        local_binding = resolved.get("local_binding")
        spool_configured = bool(
            isinstance(local_binding, Mapping) and local_binding.get("spool_path")
        )
        commands = _bound_cli_commands(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            spool_configured=spool_configured,
        )
        if not isinstance(local_binding, Mapping):
            # Retired binding: project retirement notice through the TS owner,
            # guarded by expected_revision so a concurrent re-bind cannot be masked.
            projected = decision_context_hook_binding(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                action="project_turn_view",
                expected_revision=expected_revision,
                observation={"status": "retired"},
                commands=commands,
            )
        else:
            try:
                observation = _collect_public_safe_observation(
                    goal_id=goal_id,
                    agent_id=agent_id,
                    runtime_root=runtime_root,
                    local_binding=local_binding,
                    observed_at=now,
                )
            except Exception:  # noqa: BLE001 - isolate provider/file errors to an unavailable receipt
                observation = {
                    "status": "unavailable",
                    "error_code": "decision_context_observation_failed",
                }
            projected = decision_context_hook_binding(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                action="project_turn_view",
                expected_revision=expected_revision,
                observation=observation,
                commands=commands,
            )
        if projected.get("status") != "revision_conflict":
            return projected
    assert projected is not None
    return projected


@overload
def extend_turn_start_dispatch(
    dispatch: dict[str, Any],
    *,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any]: ...


@overload
def extend_turn_start_dispatch(
    dispatch: None,
    *,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None: ...


def extend_turn_start_dispatch(
    dispatch: dict[str, Any] | None,
    *,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Attach Decision Context turn-start observation with strict default-off parity."""
    if not agent_id:
        return dispatch
    try:
        (runtime_root / DECISION_CONTEXT_HOOK_BINDINGS_DIRNAME).stat()
    except FileNotFoundError:
        return dispatch
    except OSError:
        # Let the typed provider disclose permission denial rather than
        # treating an inaccessible existing namespace as disabled.
        pass

    from ...command_invocation import loopx_cli_argv
    from ...control_plane.capability_hooks import (
        TURN_START_HOOK_RESULT_SCHEMA_VERSION,
        TurnStartHookRegistration,
        dispatch_turn_start_hooks,
    )

    snapshot: dict[str, Any] | None = None

    def produce() -> dict[str, Any]:
        nonlocal snapshot
        state = read_decision_context_turn_context(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
        )
        snapshot = state
        if not state.get("ok"):
            error_code = (
                "decision_context_hook_permission_denied"
                if state.get("status") == "permission_denied"
                else str(state.get("error") or "decision_context_hook_unavailable")
            )
            return {
                "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
                "hook_id": DECISION_CONTEXT_TURN_HOOK_ID,
                "capability_id": DECISION_CONTEXT_HOOK_CAPABILITY_ID,
                "phase": "turn_start",
                "status": "unavailable",
                "observation_count": 0,
                "agent_read_required": False,
                "external_reads_performed": False,
                "external_writes_performed": False,
                "local_private_state_mutated": False,
                "private_content_returned": False,
                "provider_payload_returned": False,
                "error_code": error_code,
            }
        count = int(state.get("observation_count") or 0)
        return {
            "schema_version": TURN_START_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": DECISION_CONTEXT_TURN_HOOK_ID,
            "capability_id": DECISION_CONTEXT_HOOK_CAPABILITY_ID,
            "phase": "turn_start",
            "status": "observed" if count > 0 else "empty",
            "observation_count": count,
            "agent_read_required": count > 0,
            "external_reads_performed": False,
            "external_writes_performed": False,
            "local_private_state_mutated": False,
            "private_content_returned": False,
            "provider_payload_returned": False,
            "error_code": None,
        }

    command = shlex.join(
        [
            *loopx_cli_argv(),
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_root),
            "decision-context",
            "hook",
            "turn-context",
            "--goal-id",
            goal_id,
            "--agent-id",
            agent_id,
            "--format",
            "json",
        ]
    )
    hook = TurnStartHookRegistration(
        hook_id=DECISION_CONTEXT_TURN_HOOK_ID,
        capability_id=DECISION_CONTEXT_HOOK_CAPABILITY_ID,
        requested_read_scope=("owner_private_decision_context",),
        requested_write_scope=(),
        producer=produce,
        context_reader=lambda: {
            "ok": True,
            "status": "read",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "current": snapshot["current"],
        }
        if snapshot is not None
        else {
            "ok": False,
            "error": "decision_context_snapshot_missing",
        },
        required_read={
            "kind": "decision_context",
            "command": command,
            "reason": (
                "Read current Decision Context source freshness, changed-source refs "
                "and uncovered P0 alerts before acting. Context grants no authority."
            ),
            "ordering": "before_work",
        },
    )

    extra = dispatch_turn_start_hooks((hook,))
    # Another Goal/Agent's binding directory does not opt this scope into
    # Decision Context. Retired bindings remain present to invalidate cache.
    if (
        snapshot is not None
        and snapshot.get("ok") is True
        and snapshot.get("status") == "absent"
    ):
        return dispatch

    result: dict[str, Any] = dict(dispatch or {})
    for key in ("results", "required_reads", "failures", "contexts"):
        extra_items = extra.get(key)
        if key == "contexts" and not (isinstance(extra_items, list) and extra_items):
            continue
        existing_items = result.get(key)
        result[key] = (
            (list(existing_items) if isinstance(existing_items, list) else [])
            + (list(extra_items) if isinstance(extra_items, list) else [])
        )
    for key in ("registered_count", "invoked_count"):
        existing_count = result.get(key)
        extra_count = extra.get(key)
        result[key] = (
            (existing_count if isinstance(existing_count, int) else 0)
            + (extra_count if isinstance(extra_count, int) else 0)
        )
    return result


def register_decision_context_hook_commands(
    commands: Any,
    add_format: Any,
) -> None:
    parser = commands.add_parser(
        "hook",
        help="Read, bind, unbind or inspect host-local Decision Context turn-start hooks.",
    )
    parser.add_argument(
        "decision_context_hook_action",
        choices=["read", "bind", "unbind", "history", "turn-context"],
    )
    add_format(parser)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument(
        "--profile",
        help="Private local profile path to bind (required for bind).",
    )
    parser.add_argument(
        "--cursor-state",
        help="Optional private reviewed-cursor JSON path to bind.",
    )
    parser.add_argument(
        "--spool",
        help="Optional private SQLite capture spool path to bind.",
    )
    parser.add_argument(
        "--mode",
        choices=["prepare_evidence", "source_manifest", "capture_status"],
        default="prepare_evidence",
        help="Read-only turn-start observation mode for an active binding.",
    )
    parser.add_argument(
        "--expected-revision",
        help="Current read revision; use 'none' only for an empty store.",
    )
    parser.add_argument(
        "--operation-id",
        help="Stable operation id for idempotent bind/unbind commits.",
    )
    parser.add_argument("--after-cursor", help="History page continuation.")
    parser.add_argument("--observed-at", help="Optional UTC ISO-8601 observation time.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit a bind/unbind change; otherwise preview only.",
    )


def handle_decision_context_hook(
    args: Any,
    *,
    registry_path: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    action = str(args.decision_context_hook_action)
    if action == "turn-context":
        return read_decision_context_turn_context(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            observed_at=getattr(args, "observed_at", None),
        )
    fields: dict[str, Any] = {"execute": bool(args.execute)}
    if action in {"bind", "unbind"}:
        if args.expected_revision is None:
            raise ValueError(
                "read hook binding first, then pass --expected-revision "
                "(or 'none' for an empty store)"
            )
        if not str(args.operation_id or "").strip():
            raise ValueError("--operation-id is required for bind and unbind")
        fields["expected_revision"] = (
            None if args.expected_revision == "none" else args.expected_revision
        )
        fields["operation_id"] = str(args.operation_id).strip()
        fields["source_kind"] = "operator_configuration"
    if action == "bind":
        from .profile import resolve_decision_context_activation

        if not str(args.profile or "").strip():
            raise ValueError("--profile is required for bind")
        profile_path = Path(str(args.profile)).expanduser().resolve()
        activation, _ = resolve_decision_context_activation(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            profile_path=profile_path,
        )
        if activation.get("available") is not True:
            reason = str(
                activation.get("reason_code")
                or activation.get("status")
                or "profile_unavailable"
            )
            raise ValueError(
                f"profile must be available for {args.goal_id}/{args.agent_id} "
                f"before binding ({reason})"
            )
        fields.update(
            profile_path=str(profile_path),
            cursor_state_path=(
                str(Path(str(args.cursor_state)).expanduser().resolve())
                if str(args.cursor_state or "").strip()
                else None
            ),
            spool_path=(
                str(Path(str(args.spool)).expanduser().resolve())
                if str(args.spool or "").strip()
                else None
            ),
            mode=str(args.mode or "prepare_evidence"),
        )
    if action == "history":
        fields["after_cursor"] = args.after_cursor
    return decision_context_hook_binding(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        action=action,
        **fields,
    )


def render_decision_context_hook(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
