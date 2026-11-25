from __future__ import annotations

from dataclasses import dataclass

from ..quota.turn_envelope import (
    ACTION_SIGNATURE_COVERAGE_V0,
    ACTION_SIGNATURE_COVERAGE_V1,
    ACTION_SIGNATURE_COVERAGE_V2,
    ACTION_SIGNATURE_COVERAGE_V3,
    ACTION_SIGNATURE_COVERAGE_V4,
    ACTION_SIGNATURE_COVERAGE_V5,
)
from .cli_output_differential_allowances import (
    _ACTION_PORTFOLIO_V0_MIGRATION_GROWTH_ALLOWANCE,
    _AGENT_CONTEXT_V4_MIGRATION_GROWTH_ALLOWANCE,
    _GUIDED_TODO_DELTA_V0_MIGRATION_GROWTH_ALLOWANCE,
    _PLANNING_HORIZON_V0_MIGRATION_GROWTH_ALLOWANCE,
    _PLANNING_INVENTORY_DETAIL_V0_MIGRATION_GROWTH_ALLOWANCE,
    _PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE,
    _QUOTA_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE,
    _TODO_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE,
    _TODO_WORK_COUNTS_V0_MIGRATION_GROWTH_ALLOWANCE,
    ACTION_PORTFOLIO_SCHEMA_VERSION_V0,
    ACTION_PORTFOLIO_SCHEMA_VERSION_V1,
    ACTION_PORTFOLIO_SCHEMA_VERSION_V2,
    GUIDED_TODO_DELTA_SCHEMA_VERSION_V0,
    PLANNING_HORIZON_SCHEMA_VERSION_V0,
    PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION_V0,
    TODO_WORK_COUNTS_SCHEMA_VERSION_V0,
    JsonObject,
    Metric,
    MetricAllowance,
    mapping_field,
    surface_from_row_id,
    tuple_field,
)

_ALLOWED_ACTION_SIGNATURE_MIGRATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (ACTION_SIGNATURE_COVERAGE_V0, ACTION_SIGNATURE_COVERAGE_V1),
        (ACTION_SIGNATURE_COVERAGE_V0, ACTION_SIGNATURE_COVERAGE_V2),
        (ACTION_SIGNATURE_COVERAGE_V1, ACTION_SIGNATURE_COVERAGE_V2),
        (ACTION_SIGNATURE_COVERAGE_V0, ACTION_SIGNATURE_COVERAGE_V3),
        (ACTION_SIGNATURE_COVERAGE_V1, ACTION_SIGNATURE_COVERAGE_V3),
        (ACTION_SIGNATURE_COVERAGE_V2, ACTION_SIGNATURE_COVERAGE_V3),
        (ACTION_SIGNATURE_COVERAGE_V0, ACTION_SIGNATURE_COVERAGE_V4),
        (ACTION_SIGNATURE_COVERAGE_V1, ACTION_SIGNATURE_COVERAGE_V4),
        (ACTION_SIGNATURE_COVERAGE_V2, ACTION_SIGNATURE_COVERAGE_V4),
        (ACTION_SIGNATURE_COVERAGE_V3, ACTION_SIGNATURE_COVERAGE_V4),
        (ACTION_SIGNATURE_COVERAGE_V0, ACTION_SIGNATURE_COVERAGE_V5),
        (ACTION_SIGNATURE_COVERAGE_V1, ACTION_SIGNATURE_COVERAGE_V5),
        (ACTION_SIGNATURE_COVERAGE_V2, ACTION_SIGNATURE_COVERAGE_V5),
        (ACTION_SIGNATURE_COVERAGE_V3, ACTION_SIGNATURE_COVERAGE_V5),
        (ACTION_SIGNATURE_COVERAGE_V4, ACTION_SIGNATURE_COVERAGE_V5),
    }
)

_ALLOWED_PORTFOLIO_MIGRATIONS: frozenset[tuple[tuple[str, ...], tuple[str, ...]]] = (
    frozenset(
        {
            ((), (ACTION_PORTFOLIO_SCHEMA_VERSION_V0,)),
            ((), (ACTION_PORTFOLIO_SCHEMA_VERSION_V1,)),
            ((), (ACTION_PORTFOLIO_SCHEMA_VERSION_V2,)),
            (
                (ACTION_PORTFOLIO_SCHEMA_VERSION_V0,),
                (ACTION_PORTFOLIO_SCHEMA_VERSION_V1,),
            ),
            (
                (ACTION_PORTFOLIO_SCHEMA_VERSION_V0,),
                (ACTION_PORTFOLIO_SCHEMA_VERSION_V2,),
            ),
            (
                (ACTION_PORTFOLIO_SCHEMA_VERSION_V1,),
                (ACTION_PORTFOLIO_SCHEMA_VERSION_V2,),
            ),
        }
    )
)

_QUOTA_JSON_ALLOWANCE_BY_SCENARIO: dict[str, int] = {
    "small": 2_048,
    "crowded": 11_264,
    "multi_agent": 4_608,
}

_QUOTA_MARKDOWN_ALLOWANCE_BY_SCENARIO: dict[str, int] = {
    "small": 18_432,
    "crowded": 46_336,
    "multi_agent": 24_064,
}


def _action_signature_migration(base: JsonObject, candidate: JsonObject) -> str | None:
    base_coverages = base.get("action_signature_coverages")
    candidate_coverages = candidate.get("action_signature_coverages")
    if not (
        isinstance(base_coverages, list)
        and len(base_coverages) == 1
        and isinstance(base_coverages[0], str)
        and isinstance(candidate_coverages, list)
        and len(candidate_coverages) == 1
        and isinstance(candidate_coverages[0], str)
    ):
        return None
    migration = (base_coverages[0], candidate_coverages[0])
    if migration not in _ALLOWED_ACTION_SIGNATURE_MIGRATIONS:
        return None
    return f"{migration[0]} -> {migration[1]}"


def _action_portfolio_schema_migration(
    base: JsonObject, candidate: JsonObject
) -> str | None:
    before = tuple_field(base, "action_portfolio_schema_versions")
    after = tuple_field(candidate, "action_portfolio_schema_versions")
    if (before, after) in _ALLOWED_PORTFOLIO_MIGRATIONS:
        before_label = before[0] if before else "none"
        after_label = after[0] if after else "none"
        return f"{before_label} -> {after_label}"
    return None


def _none_to_single_version_migration(
    base: JsonObject,
    candidate: JsonObject,
    *,
    field: str,
    expected_version: str,
) -> str | None:
    base_versions = tuple_field(base, field)
    candidate_versions = tuple_field(candidate, field)
    if base_versions == () and candidate_versions == (expected_version,):
        return f"none -> {expected_version}"
    return None


def _planning_horizon_schema_migration(
    base: JsonObject, candidate: JsonObject
) -> str | None:
    return _none_to_single_version_migration(
        base,
        candidate,
        field="planning_horizon_schema_versions",
        expected_version=PLANNING_HORIZON_SCHEMA_VERSION_V0,
    )


def _guided_todo_delta_schema_migration(
    base: JsonObject, candidate: JsonObject
) -> str | None:
    return _none_to_single_version_migration(
        base,
        candidate,
        field="guided_todo_delta_schema_versions",
        expected_version=GUIDED_TODO_DELTA_SCHEMA_VERSION_V0,
    )


def _planning_inventory_detail_schema_migration(
    base: JsonObject, candidate: JsonObject
) -> str | None:
    return _none_to_single_version_migration(
        base,
        candidate,
        field="planning_inventory_detail_schema_versions",
        expected_version=PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION_V0,
    )


def _todo_work_counts_schema_migration(
    base: JsonObject, candidate: JsonObject
) -> str | None:
    return _none_to_single_version_migration(
        base,
        candidate,
        field="todo_work_counts_schema_versions",
        expected_version=TODO_WORK_COUNTS_SCHEMA_VERSION_V0,
    )


@dataclass(frozen=True)
class _SchemaMigrationState:
    """Detected declared schema and action-signature transitions for one row pair."""

    signature_changed: bool
    signature_migration: str | None
    portfolio_schema_changed: bool
    portfolio_schema_migration: str | None
    horizon_schema_changed: bool
    horizon_schema_migration: str | None
    inventory_detail_schema_changed: bool
    inventory_detail_schema_migration: str | None
    guided_todo_delta_schema_changed: bool
    guided_todo_delta_schema_migration: str | None
    todo_work_counts_schema_changed: bool
    todo_work_counts_schema_migration: str | None
    portfolio_growth_migration: bool
    horizon_growth_migration: bool
    agent_context_growth_migration: bool
    inventory_detail_growth_migration: bool
    guided_todo_delta_growth_migration: bool
    todo_work_counts_growth_migration: bool


def _schema_migration_state(
    base: JsonObject, candidate: JsonObject, *, output_format: str
) -> _SchemaMigrationState:
    base_signature = base.get("action_signature_sha256")
    signature_changed = bool(
        base_signature and candidate.get("action_signature_sha256") != base_signature
    )
    signature_migration = (
        _action_signature_migration(base, candidate) if signature_changed else None
    )
    for field in (
        "action_portfolio_schema_versions",
        "planning_horizon_schema_versions",
        "planning_inventory_detail_schema_versions",
    ):
        tuple_field(base, field)
        tuple_field(candidate, field)
    portfolio_schema_changed = base.get("action_portfolio_schema_versions") != candidate.get(
        "action_portfolio_schema_versions"
    )
    portfolio_schema_migration = (
        _action_portfolio_schema_migration(base, candidate)
        if portfolio_schema_changed
        else None
    )
    horizon_schema_changed = base.get("planning_horizon_schema_versions") != candidate.get(
        "planning_horizon_schema_versions"
    )
    horizon_schema_migration = (
        _planning_horizon_schema_migration(base, candidate)
        if horizon_schema_changed
        else None
    )
    inventory_detail_schema_changed = base.get(
        "planning_inventory_detail_schema_versions"
    ) != candidate.get("planning_inventory_detail_schema_versions")
    inventory_detail_schema_migration = (
        _planning_inventory_detail_schema_migration(base, candidate)
        if inventory_detail_schema_changed
        else None
    )
    guided_todo_delta_schema_changed = tuple_field(
        base, "guided_todo_delta_schema_versions"
    ) != tuple_field(candidate, "guided_todo_delta_schema_versions")
    guided_todo_delta_schema_migration = (
        _guided_todo_delta_schema_migration(base, candidate)
        if guided_todo_delta_schema_changed
        else None
    )
    todo_work_counts_schema_changed = tuple_field(
        base, "todo_work_counts_schema_versions"
    ) != tuple_field(candidate, "todo_work_counts_schema_versions")
    todo_work_counts_schema_migration = (
        _todo_work_counts_schema_migration(base, candidate)
        if todo_work_counts_schema_changed
        else None
    )
    is_json = output_format == "json"
    return _SchemaMigrationState(
        signature_changed=signature_changed,
        signature_migration=signature_migration,
        portfolio_schema_changed=portfolio_schema_changed,
        portfolio_schema_migration=portfolio_schema_migration,
        horizon_schema_changed=horizon_schema_changed,
        horizon_schema_migration=horizon_schema_migration,
        inventory_detail_schema_changed=inventory_detail_schema_changed,
        inventory_detail_schema_migration=inventory_detail_schema_migration,
        guided_todo_delta_schema_changed=guided_todo_delta_schema_changed,
        guided_todo_delta_schema_migration=guided_todo_delta_schema_migration,
        todo_work_counts_schema_changed=todo_work_counts_schema_changed,
        todo_work_counts_schema_migration=todo_work_counts_schema_migration,
        portfolio_growth_migration=bool(
            is_json
            and (
                portfolio_schema_migration
                or (
                    signature_migration
                    and signature_migration.endswith(f" -> {ACTION_SIGNATURE_COVERAGE_V2}")
                )
            )
        ),
        horizon_growth_migration=bool(
            is_json
            and (
                horizon_schema_migration
                or (
                    signature_migration
                    and signature_migration.endswith(f" -> {ACTION_SIGNATURE_COVERAGE_V3}")
                )
            )
        ),
        agent_context_growth_migration=bool(
            is_json
            and signature_migration
            and signature_migration.endswith(f" -> {ACTION_SIGNATURE_COVERAGE_V4}")
        ),
        inventory_detail_growth_migration=bool(is_json and inventory_detail_schema_migration),
        guided_todo_delta_growth_migration=bool(
            is_json and guided_todo_delta_schema_migration
        ),
        todo_work_counts_growth_migration=bool(is_json and todo_work_counts_schema_migration),
    )


def _schema_migration_growth_allowance(
    migration: _SchemaMigrationState,
    metric: Metric,
) -> int:
    allowances: list[int] = []
    if migration.portfolio_growth_migration:
        allowances.append(_ACTION_PORTFOLIO_V0_MIGRATION_GROWTH_ALLOWANCE[metric])
    if migration.horizon_growth_migration:
        allowances.append(_PLANNING_HORIZON_V0_MIGRATION_GROWTH_ALLOWANCE[metric])
    if migration.agent_context_growth_migration:
        allowances.append(_AGENT_CONTEXT_V4_MIGRATION_GROWTH_ALLOWANCE[metric])
    if migration.inventory_detail_growth_migration:
        allowances.append(_PLANNING_INVENTORY_DETAIL_V0_MIGRATION_GROWTH_ALLOWANCE[metric])
    if migration.guided_todo_delta_growth_migration:
        allowances.append(_GUIDED_TODO_DELTA_V0_MIGRATION_GROWTH_ALLOWANCE[metric])
    if migration.todo_work_counts_growth_migration:
        allowances.append(_TODO_WORK_COUNTS_V0_MIGRATION_GROWTH_ALLOWANCE[metric])
    return max(allowances, default=0)


def _complete_turn_plan_migration(base: JsonObject, candidate: JsonObject) -> bool:
    """One declared v0 -> v1 plan transition with unchanged measured controls."""
    row = base.get("row_id")
    before, after = base.get("turn_plan_controls"), candidate.get("turn_plan_controls")
    return bool(
        isinstance(row, str)
        and row == candidate.get("row_id")
        and base.get("format") == candidate.get("format") == "json"
        and row
        in {
            "surface/loopx_turn_plan/small/json",
            "surface/loopx_turn_plan/crowded/json",
            "surface/loopx_turn_plan/multi_agent/json",
            "variant/loopx_turn_plan_transaction_detail/small/json",
        }
        and isinstance(before, dict)
        and isinstance(after, dict)
        and before.get("plan_version") == "loopx_turn_plan_v0"
        and after.get("plan_version") == "loopx_turn_plan_v1"
        and isinstance(before.get("controls_sha256"), str)
        and len(before["controls_sha256"]) == 64
        and before["controls_sha256"] == after.get("controls_sha256")
        and base.get("output_contract_version") is None
        and candidate.get("output_contract_version")
        == ("loopx_turn_plan_v1" if row.startswith("variant/") else None)
    )


def _matching_quota_controls(base: JsonObject, candidate: JsonObject) -> bool:
    before, after = base.get("quota_controls"), candidate.get("quota_controls")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    fields = before.get("field_sha256")
    # The frozen probe owns the source-field inventory; its independent tests
    # pin every field and mutation. Compare its bounded witness here without
    # creating another inventory/consumer of retired source fields.
    return bool(
        isinstance(fields, dict)
        and len(fields) == 19
        and all(isinstance(key, str) and key for key in fields)
        and all(isinstance(value, str) and len(value) == 64 for value in fields.values())
        and fields == after.get("field_sha256")
        and isinstance(before.get("controls_sha256"), str)
        and len(before["controls_sha256"]) == 64
        and before["controls_sha256"] == after.get("controls_sha256")
    )


def _complete_quota_source_migration(base: JsonObject, candidate: JsonObject) -> bool:
    """Qualify this declared carrier transition, not arbitrary or later growth.

    The fixture compares 19 original control values, not a schema name alone.
    This does not certify full instruction equivalence, recovery or model use.
    """
    formats = {"json", "markdown"}
    variants = {
        "quota_should_run_scheduler_detail",
        "quota_should_run_todo_summary_detail",
        "quota_should_run_user_todo_summary_detail",
        "quota_should_run_goal_boundary_detail",
        "quota_should_run_vision_detail",
        "quota_should_run_all_detail",
    }
    aliases = {
        "quota_should_run_turn_envelope": {"json", "markdown"},
        "quota_should_run_turn_envelope_blocking_user_gate": {"json"},
        "quota_should_run_turn_envelope_multi_subagent": {"json"},
    }
    scenario, output_format = base.get("scenario"), base.get("format")
    variant = base.get("variant_id")
    alias_variant = (
        isinstance(variant, str)
        and variant in aliases
        and output_format in aliases[variant]
    )
    base_controls = mapping_field(base, "quota_controls")
    candidate_controls = candidate.get("quota_controls")
    source_schema = base_controls.get("source_schema")
    legacy = alias_variant and source_schema == "loopx_turn_envelope_v0"
    allowed_variant = variant in variants or alias_variant
    policy = "explicit_opt_in_cold_path" if variant else "absolute_hot_path"
    row = (
        f"variant/{variant}/{scenario}/{output_format}"
        if variant
        else f"surface/quota_should_run/{scenario}/{output_format}"
    )
    if (
        "source_schema" not in base_controls
        or not (
            base.get("row_id") == candidate.get("row_id") == row
            and base.get("surface_id") == candidate.get("surface_id") == "quota_should_run"
            and base.get("variant_id") == candidate.get("variant_id")
            and scenario == candidate.get("scenario")
            and scenario in {"small", "crowded", "multi_agent"}
            and (scenario == "small" if variant else True)
            and output_format == candidate.get("format")
            and output_format in formats
            and (not variant or allowed_variant)
            and base.get("qualification_policy") == candidate.get("qualification_policy") == policy
            and base.get("output_contract_version") is None
            and candidate.get("output_contract_version") == "loopx_turn_envelope_v1"
            and tuple_field(base, "projection_envelope_schema_versions")
            == (() if legacy else ("loopx_projection_envelope_v0",))
            and tuple_field(candidate, "projection_envelope_schema_versions")
            == ("loopx_projection_envelope_v0",)
            and base.get("markdown_anchor")
            == ("# LoopX Turn Envelope" if legacy else "# LoopX Quota Should Run")
            and candidate.get("markdown_anchor") == "# LoopX Turn Envelope"
            and isinstance(candidate_controls, dict)
            and _matching_quota_controls(base, candidate)
        )
    ):
        return False
    return (
        source_schema == ("loopx_turn_envelope_v0" if legacy else None)
        and candidate_controls.get("source_schema") == "loopx_turn_envelope_v1"
    )


def _complete_quota_source_allowance(
    base: JsonObject, candidate: JsonObject
) -> MetricAllowance:
    if not _complete_quota_source_migration(base, candidate):
        return {}
    # Same frozen ef099ae3 caller: full Goal material moves into this source;
    # Markdown carries every field. Absolute ceilings still apply. A v1 -> v1
    # change, altered control/material witness or unrelated row gets no waiver.
    base_controls = mapping_field(base, "quota_controls")
    legacy = base_controls["source_schema"] == "loopx_turn_envelope_v0"
    output_format = str(base.get("format") or "")
    scenario = str(base.get("scenario") or "")
    if legacy:
        chars = 16_640 if output_format == "json" else 23_296
    elif output_format == "json":
        chars = _QUOTA_JSON_ALLOWANCE_BY_SCENARIO[scenario]
    else:
        chars = (
            19_456
            if base.get("variant_id")
            else _QUOTA_MARKDOWN_ALLOWANCE_BY_SCENARIO[scenario]
        )
    allowance: MetricAllowance = {"chars": chars, "utf8_bytes": chars}
    if output_format == "json":
        allowance["compact_payload_chars"] = chars
    else:
        allowance["lines"] = 6
    return allowance


def _projection_envelope_migration(
    base: JsonObject,
    candidate: JsonObject,
    *,
    output_format: str,
) -> tuple[MetricAllowance, list[str], list[str]]:
    """Qualify the one-time projection schema transition, never unrelated growth."""
    before = tuple_field(base, "projection_envelope_schema_versions")
    after = tuple_field(candidate, "projection_envelope_schema_versions")
    if before == after:
        return {}, [], []
    if _complete_turn_plan_migration(base, candidate):
        return {}, [], ["complete Turn plan exposes the full source projection metadata"]
    if _complete_quota_source_migration(base, candidate):
        return {}, [], ["complete quota source retains its original projection metadata"]
    surface_id = str(base.get("surface_id") or "")
    row_surface = surface_from_row_id(str(base.get("row_id") or ""))
    if (
        surface_id == "todo_list"
        and row_surface in {"todo_list", "todo_list_limited", "todo_list_thin"}
        and output_format == "json"
        and before == ()
        and after == ("loopx_projection_envelope_v0",)
    ):
        return _TODO_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE, [], [
            "Todo projection envelope schema migrated: none -> loopx_projection_envelope_v0"
        ]
    if (
        surface_id in {"status", "quota_should_run"}
        and before == ()
        and after == ("loopx_projection_envelope_v0",)
    ):
        migration_allowance = (
            _QUOTA_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE
            if surface_id == "quota_should_run"
            else _PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE
        )
        allowance = (
            migration_allowance.json
            if output_format == "json"
            else migration_allowance.markdown
        )
        return allowance, [], [
            "projection envelope schema migrated: none -> loopx_projection_envelope_v0"
        ]
    return {}, ["projection envelope schema coverage changed"], []
