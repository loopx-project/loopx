from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Literal

from ..heartbeat.agent_input import HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION
from ..quota.turn_envelope import (
    ACTION_SIGNATURE_COVERAGE_V0,
    ACTION_SIGNATURE_COVERAGE_V1,
    ACTION_SIGNATURE_COVERAGE_V2,
    ACTION_SIGNATURE_COVERAGE_V3,
    ACTION_SIGNATURE_COVERAGE_V4,
)


CLI_OUTPUT_PROBE_SCHEMA_VERSION = "loopx_cli_output_probe_v0"
CLI_OUTPUT_FIXTURE_CONTRACT_VERSION = "loopx_cli_output_public_fixture_v0"
CLI_OUTPUT_DIFFERENTIAL_SCHEMA_VERSION = "loopx_cli_output_differential_v0"
ACTION_PORTFOLIO_SCHEMA_VERSION_V0 = "quota_action_portfolio_v0"
ACTION_PORTFOLIO_SCHEMA_VERSION_V1 = "quota_action_portfolio_v1"
ACTION_PORTFOLIO_SCHEMA_VERSION_V2 = "quota_action_portfolio_v2"
PLANNING_HORIZON_SCHEMA_VERSION_V0 = "quota_planning_horizon_v0"
GUIDED_TODO_DELTA_SCHEMA_VERSION_V0 = "loopx_guided_todo_delta_v0"
PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION_V0 = "todo_planning_inventory_detail_v0"
TODO_WORK_COUNTS_SCHEMA_VERSION_V0 = "todo_work_counts_v0"

Metric = Literal["chars", "utf8_bytes", "lines", "compact_payload_chars"]


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


@dataclass(frozen=True)
class GrowthAllowance:
    ratio: float
    json: dict[Metric, int]
    markdown: dict[Metric, int]


_GROWTH_ALLOWANCE_BY_POLICY: dict[str, GrowthAllowance] = {
    "absolute_hot_path": GrowthAllowance(
        ratio=0.005,
        json={"chars": 64, "utf8_bytes": 128, "lines": 2, "compact_payload_chars": 64},
        markdown={
            "chars": 32,
            "utf8_bytes": 64,
            "lines": 1,
            "compact_payload_chars": 0,
        },
    ),
    "baseline_and_growth": GrowthAllowance(
        ratio=0.005,
        json={
            "chars": 128,
            "utf8_bytes": 256,
            "lines": 2,
            "compact_payload_chars": 128,
        },
        markdown={
            "chars": 64,
            "utf8_bytes": 128,
            "lines": 1,
            "compact_payload_chars": 0,
        },
    ),
    "explicit_limit_cold_path": GrowthAllowance(
        ratio=0.01,
        json={
            "chars": 256,
            "utf8_bytes": 512,
            "lines": 5,
            "compact_payload_chars": 256,
        },
        markdown={
            "chars": 128,
            "utf8_bytes": 256,
            "lines": 2,
            "compact_payload_chars": 0,
        },
    ),
    "explicit_opt_in_cold_path": GrowthAllowance(
        ratio=0.01,
        json={
            "chars": 256,
            "utf8_bytes": 512,
            "lines": 5,
            "compact_payload_chars": 256,
        },
        markdown={
            "chars": 128,
            "utf8_bytes": 256,
            "lines": 2,
            "compact_payload_chars": 0,
        },
    ),
}

# quota_action_portfolio_v1/v2 migrations add a bounded executable portfolio
# and then inline the small action context needed to interpret each choice.
# This allowance applies only to a declared schema transition; once v2 is the
# baseline, ordinary policy budgets apply again.
_ACTION_PORTFOLIO_V0_MIGRATION_GROWTH_ALLOWANCE: dict[Metric, int] = {
    "chars": 1_600,
    "utf8_bytes": 1_600,
    "lines": 42,
    "compact_payload_chars": 1_280,
}

# quota_planning_horizon_v0 adds one bounded read-only strategic context to the
# default agent path and TurnEnvelope. This allowance applies only while the
# schema and action-signature coverage move to v0/v3; after merge the candidate
# becomes the new baseline and ordinary growth limits apply again.
_PLANNING_HORIZON_V0_MIGRATION_GROWTH_ALLOWANCE: dict[Metric, int] = {
    "chars": 3_200,
    "utf8_bytes": 3_200,
    "lines": 84,
    "compact_payload_chars": 2_800,
}

# Capability-owned context adds one bounded signed planning contribution. Only
# an explicit older-coverage -> v4 transition receives this allowance; v4 -> v4
# keeps the ordinary output budget. Absolute probe ceilings remain unchanged.
_AGENT_CONTEXT_V4_MIGRATION_GROWTH_ALLOWANCE: dict[Metric, int] = {
    "chars": 2_048,
    "utf8_bytes": 2_048,
    "lines": 48,
    "compact_payload_chars": 1_664,
}

# todo_planning_inventory_detail_v0 adds planning/claim semantics only to the
# explicit agent-Todo detail variants. The allowance is bound to the declared
# none-to-v0 schema migration; after merge the ordinary cold-path budget
# applies again.
_PLANNING_INVENTORY_DETAIL_V0_MIGRATION_GROWTH_ALLOWANCE: dict[Metric, int] = {
    "chars": 1_280,
    "utf8_bytes": 1_280,
    "lines": 36,
    "compact_payload_chars": 1_024,
}

# Source-complete Todo counts add one compact scope/completeness envelope to
# the existing Todo, status, and quota projections. The allowance is bound to
# the none-to-v0 schema transition; v0-to-v0 changes use the ordinary budgets.
_TODO_WORK_COUNTS_V0_MIGRATION_GROWTH_ALLOWANCE: dict[Metric, int] = {
    "chars": 320,
    "utf8_bytes": 320,
    "lines": 10,
    "compact_payload_chars": 192,
}

# Explicit runtime-root command routing repeats one bounded command prefix per
# executable action. The allowance covers the prefix and its JSON projection;
# it is per newly observed route, not per row, so unrelated output growth still
# fails under the normal hot-path policy.
_RUNTIME_ROOT_COMMAND_ROUTE_GROWTH_PER_ROUTE: dict[Metric, int] = {
    "chars": 160,
    "utf8_bytes": 160,
    "lines": 0,
    "compact_payload_chars": 160,
}

# Automatic Reward Memory adds one fail-closed reflection/validation contract
# to the installed heartbeat body.  The allowance is bound to an exact
# none-to-v1 prompt revision, applies only to heartbeat rows, and keeps the
# absolute surface ceilings intact.  Once v1 is the baseline, normal budgets
# apply again.
_REWARD_MEMORY_OUTCOME_PROMPT_V1_MIGRATION_ALLOWANCE: dict[Metric, int] = {
    "chars": 640,
    "utf8_bytes": 640,
    "lines": 5,
    "compact_payload_chars": 640,
}

# Replacing fixed Chinese heartbeat instructions with user-language policy
# grows the installed prompt once. These per-mode limits cover the measured
# same-fixture base/head delta plus 24-28 characters of variation; the brief
# renderer also adds five lines. Absolute output ceilings still apply, and
# once v1 is in the baseline ordinary growth limits apply again.
_HEARTBEAT_USER_LANGUAGE_V1_MIGRATION_ALLOWANCE: dict[str, dict[Metric, int]] = {
    "heartbeat_prompt_thin": {"chars": 400, "compact_payload_chars": 400},
    "heartbeat_prompt_brief": {
        "chars": 720,
        "lines": 6,
        "compact_payload_chars": 720,
    },
    "heartbeat_prompt_compact": {"chars": 288, "compact_payload_chars": 288},
    "heartbeat_prompt_full": {"chars": 288, "compact_payload_chars": 288},
}

# Two reviewed causes grow the Turn plan readback once, and both are consequences
# of the same declared behavior change:
#
# 1. the plan adds one bounded managed-executor binding so a caller sees which
#    executor a planned Turn would use and whether it can launch here, instead
#    of inferring it from the host id;
# 2. the default host becomes the managed `dsh` host, so the plan's host
#    projection, scheduler execution context, and controller-owned
#    `next_cli_actions` rendering change with it (a bounded-headless plan hands
#    the next steps back to the outer controller instead of to the agent CLI
#    loop).
#
# The allowance is bound to the rejected-then-accepted
# none-to-v0 transition and to the Turn surfaces that quote it; quota, status,
# and every other agent-facing surface keep the ordinary budget. It is one
# time: once v0 is the baseline a v0-to-v0 change receives no allowance.
_TURN_HOST_AND_MANAGED_EXECUTOR_BINDING_V0_GROWTH_ALLOWANCE: dict[Metric, int] = {
    "chars": 832,
    "utf8_bytes": 832,
    "lines": 14,
    "compact_payload_chars": 768,
}

_MANAGED_EXECUTOR_BINDING_SURFACES = frozenset(
    {
        "loopx_turn_plan",
        "loopx_turn_plan_transaction_detail",
        "loopx_turn_run_once_preview",
    }
)


def _turn_host_and_managed_executor_binding_allowance(
    row_id: str,
    base: Mapping[str, Any],
    candidate: Mapping[str, Any],
    metric: Metric,
) -> int:
    surface = row_id.partition("/")[2].partition("/")[0]
    if (
        row_id.startswith(("surface/", "variant/"))
        and surface in _MANAGED_EXECUTOR_BINDING_SURFACES
        and base.get("managed_executor_binding_revision") is None
        and candidate.get("managed_executor_binding_revision")
        == "managed_executor_binding_v0"
    ):
        return _TURN_HOST_AND_MANAGED_EXECUTOR_BINDING_V0_GROWTH_ALLOWANCE[metric]
    return 0


def _reward_memory_outcome_prompt_allowance(
    row_id: str,
    base: Mapping[str, Any],
    candidate: Mapping[str, Any],
    metric: Metric,
) -> int:
    surface = row_id.partition("/")[2].partition("/")[0]
    if (
        row_id.startswith(("surface/", "variant/"))
        and surface
        in {
            "heartbeat_prompt_thin",
            "heartbeat_prompt_brief",
            "heartbeat_prompt_compact",
            "heartbeat_prompt_full",
        }
        and base.get("reward_memory_outcome_prompt_revision") is None
        and candidate.get("reward_memory_outcome_prompt_revision")
        == "reward_memory_outcome_prompt_v1"
    ):
        return _REWARD_MEMORY_OUTCOME_PROMPT_V1_MIGRATION_ALLOWANCE[metric]
    return 0


def _heartbeat_user_language_migration_allowance(
    row_id: str,
    base: Mapping[str, Any],
    candidate: Mapping[str, Any],
    metric: Metric,
) -> int:
    surface = row_id.partition("/")[2].partition("/")[0]
    if (
        row_id.startswith(("surface/", "variant/"))
        and base.get("heartbeat_user_language_prompt_revision") is None
        and candidate.get("heartbeat_user_language_prompt_revision")
        == "heartbeat_user_language_v1"
    ):
        return _HEARTBEAT_USER_LANGUAGE_V1_MIGRATION_ALLOWANCE.get(surface, {}).get(
            metric, 0
        )
    return 0


# loopx_guided_todo_delta_v0 adds the continuation-aware Todo authoring
# decision contract (reuse/update/link_successor/add_new plus a bounded
# runnable-frontier summary) to the guided start-goal packet when an
# existing-Agent takeover is projected. The allowance is bound to the
# declared none-to-v0 schema migration; after merge the ordinary
# start_goal_guided growth budgets apply again.
_GUIDED_TODO_DELTA_V0_MIGRATION_GROWTH_ALLOWANCE: dict[Metric, int] = {
    "chars": 512,
    "utf8_bytes": 512,
    "lines": 16,
    "compact_payload_chars": 448,
}


def _runtime_root_route_growth_allowances(
    base: dict[str, Any], candidate: dict[str, Any]
) -> tuple[int, dict[Metric, int]]:
    base_routes = base.get("runtime_root_command_route_count")
    candidate_routes = candidate.get("runtime_root_command_route_count")
    if type(base_routes) is not int or type(candidate_routes) is not int:
        return 0, {}
    added_routes = max(0, candidate_routes - base_routes)
    if not added_routes:
        return 0, {}
    return added_routes, {
        metric: added_routes * allowance
        for metric, allowance in _RUNTIME_ROOT_COMMAND_ROUTE_GROWTH_PER_ROUTE.items()
    }


def _rows_by_id(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if receipt.get("schema_version") != CLI_OUTPUT_PROBE_SCHEMA_VERSION:
        raise ValueError("CLI output probe receipt has an unsupported schema_version")
    if receipt.get("fixture_contract_version") != CLI_OUTPUT_FIXTURE_CONTRACT_VERSION:
        raise ValueError(
            "CLI output probe receipt has an unsupported fixture_contract_version"
        )
    rows = receipt.get("rows")
    if not isinstance(rows, list):
        raise ValueError("CLI output probe receipt rows must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("row_id"), str):
            raise ValueError("CLI output probe row must be an object with row_id")
        row_id = row["row_id"]
        if row_id in indexed:
            raise ValueError(f"duplicate CLI output probe row_id: {row_id}")
        indexed[row_id] = row
    return indexed


def _growth_limit(*, policy: str, output_format: str, metric: Metric, base: int) -> int:
    allowance = _GROWTH_ALLOWANCE_BY_POLICY.get(policy)
    if allowance is None:
        raise ValueError(f"unsupported CLI output qualification policy: {policy}")
    floors = allowance.json if output_format == "json" else allowance.markdown
    return max(floors[metric], math.ceil(base * allowance.ratio))


def _removed(base: dict[str, Any], candidate: dict[str, Any], field: str) -> list[str]:
    base_values = {str(value) for value in base.get(field, [])}
    candidate_values = {str(value) for value in candidate.get(field, [])}
    return sorted(base_values - candidate_values)


def _action_signature_migration(
    base: dict[str, Any], candidate: dict[str, Any]
) -> str | None:
    base_coverages = base.get("action_signature_coverages")
    candidate_coverages = candidate.get("action_signature_coverages")
    allowed_migrations = {
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
    }
    if not (
        isinstance(base_coverages, list)
        and len(base_coverages) == 1
        and isinstance(candidate_coverages, list)
        and len(candidate_coverages) == 1
    ):
        return None
    migration = (base_coverages[0], candidate_coverages[0])
    if migration not in allowed_migrations:
        return None
    return f"{migration[0]} -> {migration[1]}"


def _action_portfolio_schema_migration(
    base: dict[str, Any], candidate: dict[str, Any]
) -> str | None:
    base_versions = base.get("action_portfolio_schema_versions")
    candidate_versions = candidate.get("action_portfolio_schema_versions")
    allowed_migrations = {
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
    migration = (tuple(base_versions or []), tuple(candidate_versions or []))
    if migration in allowed_migrations:
        before = base_versions[0] if base_versions else "none"
        after = candidate_versions[0] if candidate_versions else "none"
        return f"{before} -> {after}"
    return None


def _planning_horizon_schema_migration(
    base: dict[str, Any], candidate: dict[str, Any]
) -> str | None:
    base_versions = tuple(base.get("planning_horizon_schema_versions") or [])
    candidate_versions = tuple(candidate.get("planning_horizon_schema_versions") or [])
    if base_versions == () and candidate_versions == (
        PLANNING_HORIZON_SCHEMA_VERSION_V0,
    ):
        return f"none -> {PLANNING_HORIZON_SCHEMA_VERSION_V0}"
    return None


def _guided_todo_delta_schema_migration(
    base: dict[str, Any], candidate: dict[str, Any]
) -> str | None:
    base_versions = tuple(base.get("guided_todo_delta_schema_versions") or [])
    candidate_versions = tuple(candidate.get("guided_todo_delta_schema_versions") or [])
    if base_versions == () and candidate_versions == (
        GUIDED_TODO_DELTA_SCHEMA_VERSION_V0,
    ):
        return f"none -> {GUIDED_TODO_DELTA_SCHEMA_VERSION_V0}"
    return None


def _planning_inventory_detail_schema_migration(
    base: dict[str, Any], candidate: dict[str, Any]
) -> str | None:
    base_versions = tuple(base.get("planning_inventory_detail_schema_versions") or [])
    candidate_versions = tuple(
        candidate.get("planning_inventory_detail_schema_versions") or []
    )
    if base_versions == () and candidate_versions == (
        PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION_V0,
    ):
        return f"none -> {PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION_V0}"
    return None


def _todo_work_counts_schema_migration(
    base: dict[str, Any], candidate: dict[str, Any]
) -> str | None:
    base_versions = tuple(base.get("todo_work_counts_schema_versions") or [])
    candidate_versions = tuple(candidate.get("todo_work_counts_schema_versions") or [])
    if base_versions == () and candidate_versions == (TODO_WORK_COUNTS_SCHEMA_VERSION_V0,):
        return f"none -> {TODO_WORK_COUNTS_SCHEMA_VERSION_V0}"
    return None


@dataclass(frozen=True)
class _SchemaMigrationState:
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
    base: dict[str, Any], candidate: dict[str, Any], *, output_format: str
) -> _SchemaMigrationState:
    base_signature = base.get("action_signature_sha256")
    signature_changed = bool(
        base_signature and candidate.get("action_signature_sha256") != base_signature
    )
    signature_migration = (
        _action_signature_migration(base, candidate) if signature_changed else None
    )
    base_portfolio_versions = base.get("action_portfolio_schema_versions")
    candidate_portfolio_versions = candidate.get("action_portfolio_schema_versions")
    portfolio_schema_changed = base_portfolio_versions != candidate_portfolio_versions
    portfolio_schema_migration = (
        _action_portfolio_schema_migration(base, candidate)
        if portfolio_schema_changed
        else None
    )
    base_horizon_versions = base.get("planning_horizon_schema_versions")
    candidate_horizon_versions = candidate.get("planning_horizon_schema_versions")
    horizon_schema_changed = base_horizon_versions != candidate_horizon_versions
    horizon_schema_migration = (
        _planning_horizon_schema_migration(base, candidate)
        if horizon_schema_changed
        else None
    )
    base_inventory_detail_versions = base.get(
        "planning_inventory_detail_schema_versions"
    )
    candidate_inventory_detail_versions = candidate.get(
        "planning_inventory_detail_schema_versions"
    )
    inventory_detail_schema_changed = (
        base_inventory_detail_versions != candidate_inventory_detail_versions
    )
    inventory_detail_schema_migration = (
        _planning_inventory_detail_schema_migration(base, candidate)
        if inventory_detail_schema_changed
        else None
    )
    guided_todo_delta_schema_changed = tuple(
        base.get("guided_todo_delta_schema_versions") or []
    ) != tuple(candidate.get("guided_todo_delta_schema_versions") or [])
    guided_todo_delta_schema_migration = (
        _guided_todo_delta_schema_migration(base, candidate)
        if guided_todo_delta_schema_changed
        else None
    )
    todo_work_counts_schema_changed = tuple(
        base.get("todo_work_counts_schema_versions") or []
    ) != tuple(candidate.get("todo_work_counts_schema_versions") or [])
    todo_work_counts_schema_migration = (
        _todo_work_counts_schema_migration(base, candidate)
        if todo_work_counts_schema_changed
        else None
    )
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
            output_format == "json"
            and (
                portfolio_schema_migration
                or (
                    signature_migration
                    and signature_migration.endswith(
                        f" -> {ACTION_SIGNATURE_COVERAGE_V2}"
                    )
                )
            )
        ),
        horizon_growth_migration=bool(
            output_format == "json"
            and (
                horizon_schema_migration
                or (
                    signature_migration
                    and signature_migration.endswith(
                        f" -> {ACTION_SIGNATURE_COVERAGE_V3}"
                    )
                )
            )
        ),
        agent_context_growth_migration=bool(
            output_format == "json"
            and signature_migration
            and signature_migration.endswith(f" -> {ACTION_SIGNATURE_COVERAGE_V4}")
        ),
        inventory_detail_growth_migration=bool(
            output_format == "json" and inventory_detail_schema_migration
        ),
        guided_todo_delta_growth_migration=bool(
            output_format == "json" and guided_todo_delta_schema_migration
        ),
        todo_work_counts_growth_migration=bool(
            output_format == "json" and todo_work_counts_schema_migration
        ),
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
        allowances.append(
            _PLANNING_INVENTORY_DETAIL_V0_MIGRATION_GROWTH_ALLOWANCE[metric]
        )
    if migration.guided_todo_delta_growth_migration:
        allowances.append(_GUIDED_TODO_DELTA_V0_MIGRATION_GROWTH_ALLOWANCE[metric])
    if migration.todo_work_counts_growth_migration:
        allowances.append(_TODO_WORK_COUNTS_V0_MIGRATION_GROWTH_ALLOWANCE[metric])
    return max(allowances, default=0)


# The unchanged status matrix adds 2,801 pretty JSON chars / 102 lines /
# 1,910 compact chars for five source rows. This reviewed one-time transition
# leaves headroom without changing absolute ceilings or ordinary v0-to-v0 growth.
_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE = GrowthAllowance(
    ratio=0,
    json={"chars": 3_000, "utf8_bytes": 3_000, "lines": 110, "compact_payload_chars": 2_048},
    markdown={"chars": 192, "utf8_bytes": 224, "lines": 3, "compact_payload_chars": 0},
)


def _compare_row(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    row_id = str(base["row_id"])
    failures: list[str] = []
    review_signals: list[str] = []
    policy = str(base.get("qualification_policy") or "")
    output_format = str(base.get("format") or "")
    if candidate.get("qualification_policy") != policy:
        failures.append("qualification_policy changed")
    if candidate.get("format") != output_format:
        failures.append("format changed")

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
    ):
        failures.append("output_contract_version changed")

    migration = _schema_migration_state(
        base,
        candidate,
        output_format=output_format,
    )
    added_runtime_root_routes, runtime_root_route_allowances = (
        _runtime_root_route_growth_allowances(base, candidate)
    )

    base_projection = tuple(base.get("projection_envelope_schema_versions") or [])
    candidate_projection = tuple(candidate.get("projection_envelope_schema_versions") or [])
    projection_migration = (
        (row_id.startswith("surface/status/") or row_id.startswith("variant/status_task_graph_detail/"))
        and base_projection == () and candidate_projection == ("loopx_projection_envelope_v0",)
    )
    if base_projection != candidate_projection:
        if projection_migration:
            review_signals.append("projection envelope schema migrated: none -> loopx_projection_envelope_v0")
        else:
            failures.append("projection envelope schema coverage changed")
    projection_allowance = (_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE.json
                            if output_format == "json" else _PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE.markdown)
    deltas: dict[str, int | None] = {}
    allowances: dict[str, int | None] = {}
    for metric in ("chars", "utf8_bytes", "lines", "compact_payload_chars"):
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
            _reward_memory_outcome_prompt_allowance(
                row_id,
                base,
                candidate,
                metric,
            ),
            _heartbeat_user_language_migration_allowance(
                row_id,
                base,
                candidate,
                metric,
            ),
            _turn_host_and_managed_executor_binding_allowance(
                row_id,
                base,
                candidate,
                metric,
            ),
            _schema_migration_growth_allowance(migration, metric),
            projection_allowance[metric] if projection_migration else 0,
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
            and row_id.partition("/")[2].partition("/")[0]
            in {
                "heartbeat_prompt_thin",
                "heartbeat_prompt_brief",
                "heartbeat_prompt_compact",
            }
            and base.get("host_prompt_static_safety_revision") is None
            and candidate.get("host_prompt_static_safety_revision")
            == "host_prompt_static_safety_v1"
        ):
            # Authorized static safety + executable shell bootstrap restoration.
            # Absolute ceilings stay enforced by the probe; once merged, v1->v1
            # receives no allowance. Quota/status and other surfaces are excluded.
            allowance = max(
                allowance,
                {
                    "chars": 512,
                    "utf8_bytes": 640,
                    "lines": 5,
                    "compact_payload_chars": 512,
                }[metric],
            )
        if runtime_root_route_allowances:
            allowance = max(
                allowance,
                runtime_root_route_allowances[metric],
            )
        deltas[metric] = delta
        allowances[metric] = allowance
        if delta > allowance:
            failures.append(f"{metric} grew by {delta}; allowance is {allowance}")

    if output_format == "json":
        missing = _removed(base, candidate, "semantic_json_keys")
        if missing:
            preview = ", ".join(missing[:5])
            suffix = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
            message = f"semantic_json_keys removed: {preview}{suffix}"
            if heartbeat_agent_input_migration:
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
            preview = ", ".join(missing[:5])
            suffix = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
            review_signals.append(f"{field} removed: {preview}{suffix}")

    base_anchor = base.get("markdown_anchor")
    if base_anchor and candidate.get("markdown_anchor") != base_anchor:
        failures.append("markdown_anchor changed")
    if migration.signature_changed:
        if migration.signature_migration is None:
            failures.append("action_signature semantic digest changed")
        else:
            review_signals.append(
                f"action_signature coverage migrated: {migration.signature_migration}"
            )
    if migration.portfolio_schema_changed:
        if migration.portfolio_schema_migration is None:
            failures.append("action_portfolio schema coverage changed")
        else:
            review_signals.append(
                "action_portfolio schema migrated: "
                f"{migration.portfolio_schema_migration}"
            )
    if migration.horizon_schema_changed:
        if migration.horizon_schema_migration is None:
            failures.append("planning_horizon schema coverage changed")
        else:
            review_signals.append(
                "planning_horizon schema migrated: "
                f"{migration.horizon_schema_migration}"
            )
    if migration.inventory_detail_schema_changed:
        if migration.inventory_detail_schema_migration is None:
            failures.append("planning inventory detail schema coverage changed")
        else:
            review_signals.append(
                "planning inventory detail schema migrated: "
                f"{migration.inventory_detail_schema_migration}"
            )
    if runtime_root_route_allowances:
        review_signals.append(
            "runtime-root command route coverage added: "
            f"{added_runtime_root_routes} executable route(s)"
        )
    if migration.guided_todo_delta_schema_changed:
        if migration.guided_todo_delta_schema_migration is None:
            failures.append("guided todo delta schema coverage changed")
        else:
            review_signals.append(
                "guided todo delta schema migrated: "
                f"{migration.guided_todo_delta_schema_migration}"
            )
    if migration.todo_work_counts_schema_changed:
        if migration.todo_work_counts_schema_migration is None:
            failures.append("Todo work-count schema coverage changed")
        else:
            review_signals.append(
                "Todo work-count schema migrated: "
                f"{migration.todo_work_counts_schema_migration}"
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
    base_receipt: dict[str, Any],
    candidate_receipt: dict[str, Any],
) -> dict[str, Any]:
    base_rows = _rows_by_id(base_receipt)
    candidate_rows = _rows_by_id(candidate_receipt)
    results: list[dict[str, Any]] = []
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
            results.append(
                {
                    "row_id": row_id,
                    "status": "failed",
                    "deltas": {},
                    "allowances": {},
                    "failures": ["qualified base row is missing from candidate"],
                    "review_signals": [],
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
            row["status"] == "candidate_only" for row in results
        ),
        "rows": results,
    }
