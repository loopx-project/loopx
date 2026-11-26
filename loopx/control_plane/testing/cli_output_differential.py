from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from ..heartbeat.agent_input import HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION
from .cli_output_differential_allowances import (
    _ACTION_PORTFOLIO_V0_MIGRATION_GROWTH_ALLOWANCE as _ACTION_PORTFOLIO_V0_MIGRATION_GROWTH_ALLOWANCE,
    _AGENT_CONTEXT_V4_MIGRATION_GROWTH_ALLOWANCE as _AGENT_CONTEXT_V4_MIGRATION_GROWTH_ALLOWANCE,
    _COMMAND_ROUTE_GROWTH_PER_ROUTE as _COMMAND_ROUTE_GROWTH_PER_ROUTE,
    _GROWTH_ALLOWANCE_BY_POLICY as _GROWTH_ALLOWANCE_BY_POLICY,
    _GUIDED_TODO_DELTA_V0_MIGRATION_GROWTH_ALLOWANCE as _GUIDED_TODO_DELTA_V0_MIGRATION_GROWTH_ALLOWANCE,
    _HEARTBEAT_USER_LANGUAGE_V1_MIGRATION_ALLOWANCE as _HEARTBEAT_USER_LANGUAGE_V1_MIGRATION_ALLOWANCE,
    _MANAGED_EXECUTOR_BINDING_SURFACES as _MANAGED_EXECUTOR_BINDING_SURFACES,
    _PLANNING_HORIZON_V0_MIGRATION_GROWTH_ALLOWANCE as _PLANNING_HORIZON_V0_MIGRATION_GROWTH_ALLOWANCE,
    _PLANNING_INVENTORY_DETAIL_V0_MIGRATION_GROWTH_ALLOWANCE as _PLANNING_INVENTORY_DETAIL_V0_MIGRATION_GROWTH_ALLOWANCE,
    _PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE as _PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE,
    _QUOTA_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE as _QUOTA_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE,
    _REWARD_MEMORY_OUTCOME_PROMPT_V1_MIGRATION_ALLOWANCE as _REWARD_MEMORY_OUTCOME_PROMPT_V1_MIGRATION_ALLOWANCE,
    _TODO_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE as _TODO_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE,
    _TODO_WORK_COUNTS_V0_MIGRATION_GROWTH_ALLOWANCE as _TODO_WORK_COUNTS_V0_MIGRATION_GROWTH_ALLOWANCE,
    _TURN_HOST_AND_MANAGED_EXECUTOR_BINDING_V0_GROWTH_ALLOWANCE as _TURN_HOST_AND_MANAGED_EXECUTOR_BINDING_V0_GROWTH_ALLOWANCE,
    ACTION_PORTFOLIO_SCHEMA_VERSION_V0 as ACTION_PORTFOLIO_SCHEMA_VERSION_V0,
    ACTION_PORTFOLIO_SCHEMA_VERSION_V1 as ACTION_PORTFOLIO_SCHEMA_VERSION_V1,
    ACTION_PORTFOLIO_SCHEMA_VERSION_V2 as ACTION_PORTFOLIO_SCHEMA_VERSION_V2,
    CLI_OUTPUT_DIFFERENTIAL_SCHEMA_VERSION as CLI_OUTPUT_DIFFERENTIAL_SCHEMA_VERSION,
    CLI_OUTPUT_FIXTURE_CONTRACT_VERSION as CLI_OUTPUT_FIXTURE_CONTRACT_VERSION,
    CLI_OUTPUT_PROBE_SCHEMA_VERSION as CLI_OUTPUT_PROBE_SCHEMA_VERSION,
    GUIDED_TODO_DELTA_SCHEMA_VERSION_V0 as GUIDED_TODO_DELTA_SCHEMA_VERSION_V0,
    PLANNING_HORIZON_SCHEMA_VERSION_V0 as PLANNING_HORIZON_SCHEMA_VERSION_V0,
    PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION_V0 as PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION_V0,
    TODO_WORK_COUNTS_SCHEMA_VERSION_V0 as TODO_WORK_COUNTS_SCHEMA_VERSION_V0,
    CliOutputReceiptComparison as CliOutputReceiptComparison,
    CliOutputRowComparison as CliOutputRowComparison,
    GrowthAllowance as GrowthAllowance,
    JsonObject as JsonObject,
    Metric as Metric,
    MetricAllowance as MetricAllowance,
    _command_route_growth_allowances,
    _decision_evidence_migration_allowance,
    _growth_limit,
    _has_decision_evidence,
    _heartbeat_peer_admission_migration_allowance,
    _heartbeat_user_language_migration_allowance,
    _removed,
    _reward_memory_outcome_prompt_allowance,
    _rows_by_id,
    _turn_host_and_managed_executor_binding_allowance,
    surface_from_row_id,
)
from .cli_output_differential_carriers import (
    _authoring_input_migration as _authoring_input_migration,
    _continuation_instruction_migration as _continuation_instruction_migration,
    _task_step_read_fence_migration as _task_step_read_fence_migration,
    _turn_projection_migration as _turn_projection_migration,
)
from .cli_output_differential_migrations import (
    _action_portfolio_schema_migration as _action_portfolio_schema_migration,
    _action_signature_migration as _action_signature_migration,
    _complete_quota_source_allowance,
    _complete_quota_source_migration,
    _complete_turn_plan_migration,
    _guided_todo_delta_schema_migration as _guided_todo_delta_schema_migration,
    _matching_quota_controls as _matching_quota_controls,
    _planning_horizon_schema_migration as _planning_horizon_schema_migration,
    _planning_inventory_detail_schema_migration as _planning_inventory_detail_schema_migration,
    _projection_envelope_migration,
    _schema_migration_growth_allowance,
    _schema_migration_state,
    _SchemaMigrationState as _SchemaMigrationState,
    _todo_work_counts_schema_migration as _todo_work_counts_schema_migration,
)

__all__ = [
    "ACTION_PORTFOLIO_SCHEMA_VERSION_V0",
    "ACTION_PORTFOLIO_SCHEMA_VERSION_V1",
    "ACTION_PORTFOLIO_SCHEMA_VERSION_V2",
    "CLI_OUTPUT_DIFFERENTIAL_SCHEMA_VERSION",
    "CLI_OUTPUT_FIXTURE_CONTRACT_VERSION",
    "CLI_OUTPUT_PROBE_SCHEMA_VERSION",
    "CliOutputReceiptComparison",
    "CliOutputRowComparison",
    "GUIDED_TODO_DELTA_SCHEMA_VERSION_V0",
    "GrowthAllowance",
    "JsonObject",
    "Metric",
    "MetricAllowance",
    "PLANNING_HORIZON_SCHEMA_VERSION_V0",
    "PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION_V0",
    "TODO_WORK_COUNTS_SCHEMA_VERSION_V0",
    "compare_cli_output_receipts",
    "select_cli_output_base_ref",
]

_HOST_PROMPT_STATIC_SAFETY_ALLOWANCE: MetricAllowance = {
    "chars": 512,
    "utf8_bytes": 640,
    "lines": 5,
    "compact_payload_chars": 512,
}

_HOST_PROMPT_SURFACES = frozenset(
    {
        "heartbeat_prompt_thin",
        "heartbeat_prompt_brief",
        "heartbeat_prompt_compact",
    }
)


def select_cli_output_base_ref(
    requested_ref: str,
    *,
    main_ref: str,
    is_ancestor: Callable[[str, str], bool],
) -> str:
    """Choose main for a sync commit that brings main into a stale PR base."""
    if requested_ref == main_ref:
        return requested_ref
    head_contains_main = is_ancestor(main_ref, "HEAD")
    requested_contains_main = is_ancestor(main_ref, requested_ref)
    if head_contains_main and not requested_contains_main:
        return main_ref
    return requested_ref


def _missing_items_message(label: str, missing: list[str]) -> str:
    preview = ", ".join(missing[:5])
    suffix = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
    return f"{label} removed: {preview}{suffix}"


def _record_schema_migration(
    *,
    changed: bool,
    migrated: str | None,
    failure_message: str,
    signal_prefix: str,
    failures: list[str],
    review_signals: list[str],
) -> None:
    if not changed:
        return
    if migrated is None:
        failures.append(failure_message)
    else:
        review_signals.append(f"{signal_prefix}: {migrated}")


def _compare_row_growth(
    base: JsonObject,
    candidate: JsonObject,
    *,
    migration: _SchemaMigrationState,
    command_route_allowances: MetricAllowance,
) -> tuple[dict[str, int | None], dict[str, int | None], list[str], list[str]]:
    """Compare output cost independently of contract and semantic coverage."""
    row_id = str(base["row_id"])
    policy = str(base.get("qualification_policy") or "")
    output_format = str(base.get("format") or "")
    failures: list[str] = []
    review_signals: list[str] = []
    projection_allowance, projection_failures, projection_signals = (
        _projection_envelope_migration(base, candidate, output_format=output_format)
    )
    failures.extend(projection_failures)
    review_signals.extend(projection_signals)
    fence_allowance, fence_signals = _task_step_read_fence_migration(base, candidate)
    review_signals.extend(fence_signals)
    authoring_allowance, authoring_signals = _authoring_input_migration(base, candidate)
    review_signals.extend(authoring_signals)
    continuation_allowance, continuation_signals = _continuation_instruction_migration(
        base, candidate
    )
    review_signals.extend(continuation_signals)
    context_allowance, context_signals = _turn_projection_migration(base, candidate)
    review_signals.extend(context_signals)
    # The source-complete detail replaces the old summary; default inspection
    # retains a small pinned-source route. Absolute ceilings stay unchanged.
    if _complete_turn_plan_migration(base, candidate):
        bound = 16_000 if row_id.startswith("variant/") else 2_000
        context_metrics: tuple[Metric, ...] = (
            "chars",
            "utf8_bytes",
            "compact_payload_chars",
        )
        context_allowance = {
            metric: max(context_allowance.get(metric, 0), bound)
            for metric in context_metrics
        }
        review_signals.append(
            "Turn plan v0 -> v1: unchanged current controls; full-source costs require review"
        )
    quota_allowance = _complete_quota_source_allowance(base, candidate)
    deltas: dict[str, int | None] = {}
    allowances: dict[str, int | None] = {}
    metrics: tuple[Metric, ...] = (
        "chars",
        "utf8_bytes",
        "lines",
        "compact_payload_chars",
    )
    for metric in metrics:
        base_value = base.get(metric)
        candidate_value = candidate.get(metric)
        if not isinstance(base_value, int) or not isinstance(candidate_value, int):
            deltas[metric] = None
            allowances[metric] = None
            continue
        delta = candidate_value - base_value
        allowance = max(
            _growth_limit(
                policy=policy,
                output_format=output_format,
                metric=metric,
                base=base_value,
            ),
            _reward_memory_outcome_prompt_allowance(row_id, base, candidate, metric),
            _heartbeat_peer_admission_migration_allowance(
                row_id, base, candidate, metric
            ),
            _heartbeat_user_language_migration_allowance(
                row_id, base, candidate, metric
            ),
            _turn_host_and_managed_executor_binding_allowance(
                row_id, base, candidate, metric
            ),
            _schema_migration_growth_allowance(migration, metric),
            _decision_evidence_migration_allowance(base, candidate, metric),
            projection_allowance.get(metric, 0),
            fence_allowance.get(metric, 0),
            authoring_allowance.get(metric, 0),
            continuation_allowance.get(metric, 0),
            context_allowance.get(metric, 0),
            quota_allowance.get(metric, 0),
        )
        # Thin installed prompts contain bilingual lifecycle instructions. A
        # small character-level clarification can cost three bytes per CJK
        # character. Keep character, line and absolute output ceilings intact;
        # do not relax quota or other agent-facing surfaces with this allowance.
        if (
            row_id.startswith("surface/heartbeat_prompt_thin/")
            and metric == "utf8_bytes"
        ):
            allowance = max(allowance, 192)
        if (
            row_id.startswith(("surface/", "variant/"))
            and surface_from_row_id(row_id) in _HOST_PROMPT_SURFACES
            and base.get("host_prompt_static_safety_revision") is None
            and candidate.get("host_prompt_static_safety_revision")
            == "host_prompt_static_safety_v1"
        ):
            # Authorized static safety + executable shell bootstrap restoration.
            # Absolute ceilings stay enforced by the probe; once merged, v1->v1
            # receives no allowance. Quota/status and other surfaces are excluded.
            allowance = max(allowance, _HOST_PROMPT_STATIC_SAFETY_ALLOWANCE[metric])
        if command_route_allowances:
            allowance = max(allowance, command_route_allowances[metric])
        deltas[metric] = delta
        allowances[metric] = allowance
        if delta > allowance:
            failures.append(f"{metric} grew by {delta}; allowance is {allowance}")

    return deltas, allowances, failures, review_signals


def _compare_row(base: JsonObject, candidate: JsonObject) -> CliOutputRowComparison:
    row_id = str(base["row_id"])
    failures: list[str] = []
    review_signals: list[str] = []
    policy = str(base.get("qualification_policy") or "")
    output_format = str(base.get("format") or "")
    if candidate.get("qualification_policy") != policy:
        failures.append("qualification_policy changed")
    if candidate.get("format") != output_format:
        failures.append("format changed")

    before_controls, after_controls = (
        base.get("turn_plan_controls"),
        candidate.get("turn_plan_controls"),
    )
    if (
        isinstance(before_controls, dict)
        and isinstance(after_controls, dict)
        and before_controls.get("plan_version") == "loopx_turn_plan_v0"
        and after_controls.get("plan_version") == "loopx_turn_plan_v1"
        and before_controls.get("controls_sha256") != after_controls.get("controls_sha256")
    ):
        old_raw = before_controls["field_sha256"]
        new_raw = after_controls["field_sha256"]
        if not isinstance(old_raw, Mapping) or not isinstance(new_raw, Mapping):
            raise TypeError("turn_plan_controls.field_sha256 must be a mapping")
        old_map = cast(Mapping[str, object], old_raw)
        new_map = cast(Mapping[str, object], new_raw)
        changed_fields = sorted(
            key
            for key in old_map.keys() | new_map.keys()
            if old_map.get(key) != new_map.get(key)
        )
        failures.append("Turn plan current controls changed: " + ", ".join(changed_fields))

    quota_migration = _complete_quota_source_migration(base, candidate)
    base_output_contract = base.get("output_contract_version")
    candidate_output_contract = candidate.get("output_contract_version")
    heartbeat_agent_input_migration = bool(
        row_id.startswith("surface/heartbeat_prompt_thin/")
        and output_format == "json"
        and base_output_contract is None
        and candidate_output_contract == HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION
    )
    if (
        candidate_output_contract != base_output_contract
        and not heartbeat_agent_input_migration
        and not _complete_turn_plan_migration(base, candidate)
        and not quota_migration
    ):
        failures.append("output_contract_version changed")

    migration = _schema_migration_state(
        base,
        candidate,
        output_format=output_format,
    )
    added_command_routes, command_route_allowances = _command_route_growth_allowances(
        base, candidate
    )

    deltas, allowances, growth_failures, growth_signals = _compare_row_growth(
        base,
        candidate,
        migration=migration,
        command_route_allowances=command_route_allowances,
    )
    failures.extend(growth_failures)
    review_signals.extend(growth_signals)

    if output_format == "json":
        missing = _removed(base, candidate, "semantic_json_keys")
        if missing:
            message = _missing_items_message("semantic_json_keys", missing)
            if (
                heartbeat_agent_input_migration
                or _complete_turn_plan_migration(base, candidate)
                or quota_migration
            ):
                review_signals.append(message)
            else:
                failures.append(message)

    if heartbeat_agent_input_migration:
        review_signals.append(
            "output contract migrated: generator payload -> heartbeat_agent_input_v1"
        )

    for field in ("json_shape_paths", "markdown_headings"):
        missing = _removed(base, candidate, field)
        if missing:
            review_signals.append(_missing_items_message(field, missing))

    base_anchor = base.get("markdown_anchor")
    if (
        base_anchor
        and candidate.get("markdown_anchor") != base_anchor
        and not quota_migration
    ):
        failures.append("markdown_anchor changed")
    if migration.signature_changed:
        if migration.signature_migration is None:
            if _complete_turn_plan_migration(base, candidate) or quota_migration:
                review_signals.append(
                    "historical action signature replaced by the checked complete source"
                )
            else:
                failures.append("action_signature semantic digest changed")
        else:
            review_signals.append(
                f"action_signature coverage migrated: {migration.signature_migration}"
            )
    for schema_changed, migrated, failure_message, signal_prefix in (
        (
            migration.portfolio_schema_changed,
            migration.portfolio_schema_migration,
            "action_portfolio schema coverage changed",
            "action_portfolio schema migrated",
        ),
        (
            migration.horizon_schema_changed,
            migration.horizon_schema_migration,
            "planning_horizon schema coverage changed",
            "planning_horizon schema migrated",
        ),
        (
            migration.inventory_detail_schema_changed,
            migration.inventory_detail_schema_migration,
            "planning inventory detail schema coverage changed",
            "planning inventory detail schema migrated",
        ),
        (
            migration.guided_todo_delta_schema_changed,
            migration.guided_todo_delta_schema_migration,
            "guided todo delta schema coverage changed",
            "guided todo delta schema migrated",
        ),
        (
            migration.todo_work_counts_schema_changed,
            migration.todo_work_counts_schema_migration,
            "Todo work-count schema coverage changed",
            "Todo work-count schema migrated",
        ),
    ):
        _record_schema_migration(
            changed=schema_changed,
            migrated=migrated,
            failure_message=failure_message,
            signal_prefix=signal_prefix,
            failures=failures,
            review_signals=review_signals,
        )
    for option, count in added_command_routes.items():
        review_signals.append(
            f"{option.replace('_', '-')} command route coverage added: "
            f"{count} executable route(s)"
        )

    return {
        "row_id": row_id,
        "status": "failed" if failures else "passed",
        "deltas": deltas,
        "allowances": allowances,
        "failures": failures,
        "review_signals": review_signals,
    }


def compare_cli_output_receipts(
    base_receipt: JsonObject,
    candidate_receipt: JsonObject,
) -> CliOutputReceiptComparison:
    """Compare a candidate CLI output probe receipt against a base receipt."""
    base_rows = _rows_by_id(base_receipt)
    candidate_rows = _rows_by_id(candidate_receipt)
    results: list[CliOutputRowComparison] = []
    for row_id in sorted(base_rows.keys() | candidate_rows.keys()):
        base = base_rows.get(row_id)
        candidate = candidate_rows.get(row_id)
        if base is None:
            results.append(
                {
                    "row_id": row_id,
                    "status": "candidate_only",
                    "deltas": {},
                    "allowances": {},
                    "failures": [],
                    "review_signals": [],
                }
            )
        elif candidate is None:
            parts = row_id.split("/")
            replacement = next(
                (
                    row
                    for key, row in candidate_rows.items()
                    if key.startswith(
                        ("variant/review_packet_full/", "surface/quota_should_run/")
                    )
                    and _has_decision_evidence(row)
                ),
                None,
            )
            retired_evidence_command = bool(
                len(parts) == 4
                and parts[:2] == ["surface", "evidence_log_thin"]
                and replacement is not None
                and _has_decision_evidence(replacement)
            )
            results.append(
                {
                    "row_id": row_id,
                    "status": "passed" if retired_evidence_command else "failed",
                    "deltas": {},
                    "allowances": {},
                    "failures": []
                    if retired_evidence_command
                    else ["qualified base row is missing from candidate"],
                    "review_signals": [
                        "evidence-log retired; scoped decision evidence is delivered by replan_context"
                    ]
                    if retired_evidence_command
                    else [],
                }
            )
        else:
            results.append(_compare_row(base, candidate))

    failed_rows = [row for row in results if row["status"] == "failed"]
    review_rows = [row for row in results if row["review_signals"]]
    return {
        "schema_version": CLI_OUTPUT_DIFFERENTIAL_SCHEMA_VERSION,
        "ok": not failed_rows,
        "base_row_count": len(base_rows),
        "candidate_row_count": len(candidate_rows),
        "compared_row_count": len(results),
        "failed_row_count": len(failed_rows),
        "review_required": bool(review_rows),
        "review_row_count": len(review_rows),
        "candidate_only_row_count": sum(
            1 for row in results if row["status"] == "candidate_only"
        ),
        "rows": results,
    }
