from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Literal, TypedDict

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
MetricAllowance = dict[Metric, int]
JsonObject = Mapping[str, object]


class CliOutputRowComparison(TypedDict):
    """Typed comparison result for a single matched or unmatched CLI output row."""

    row_id: str
    status: str
    deltas: dict[str, int | None]
    allowances: dict[str, int | None]
    failures: list[str]
    review_signals: list[str]


class CliOutputReceiptComparison(TypedDict):
    """Typed top-level summary returned by compare_cli_output_receipts."""

    schema_version: str
    ok: bool
    base_row_count: int
    candidate_row_count: int
    compared_row_count: int
    failed_row_count: int
    review_required: bool
    review_row_count: int
    candidate_only_row_count: int
    rows: list[CliOutputRowComparison]


@dataclass(frozen=True)
class GrowthAllowance:
    """Relative and fixed per-metric growth limits for a qualification policy."""

    ratio: float
    json: MetricAllowance
    markdown: MetricAllowance


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
_ACTION_PORTFOLIO_V0_MIGRATION_GROWTH_ALLOWANCE: MetricAllowance = {
    "chars": 1_600,
    "utf8_bytes": 1_600,
    "lines": 42,
    "compact_payload_chars": 1_280,
}

# quota_planning_horizon_v0 adds one bounded read-only strategic context to the
# default agent path and TurnEnvelope. This allowance applies only while the
# schema and action-signature coverage move to v0/v3; after merge the candidate
# becomes the new baseline and ordinary growth limits apply again.
# Default Goal + flat work context costs 2,940 compact characters on the
# matched multi-Agent fixture; preserve its work visibility and completeness.
_PLANNING_HORIZON_V0_MIGRATION_GROWTH_ALLOWANCE: MetricAllowance = {
    "chars": 3_200,
    "utf8_bytes": 3_200,
    "lines": 84,
    "compact_payload_chars": 3_000,
}

# Capability-owned context adds one bounded signed planning contribution. Only
# an explicit older-coverage -> v4 transition receives this allowance; v4 -> v4
# keeps the ordinary output budget. Absolute probe ceilings remain unchanged.
_AGENT_CONTEXT_V4_MIGRATION_GROWTH_ALLOWANCE: MetricAllowance = {
    "chars": 2_048,
    "utf8_bytes": 2_048,
    "lines": 48,
    "compact_payload_chars": 1_664,
}

# todo_planning_inventory_detail_v0 adds planning/claim semantics only to the
# explicit agent-Todo detail variants. The allowance is bound to the declared
# none-to-v0 schema migration; after merge the ordinary cold-path budget
# applies again.
_PLANNING_INVENTORY_DETAIL_V0_MIGRATION_GROWTH_ALLOWANCE: MetricAllowance = {
    "chars": 1_280,
    "utf8_bytes": 1_280,
    "lines": 36,
    "compact_payload_chars": 1_024,
}

# Source-complete Todo counts add one compact scope/completeness envelope to
# the existing Todo, status, and quota projections. The allowance is bound to
# the none-to-v0 schema transition; v0-to-v0 changes use the ordinary budgets.
_TODO_WORK_COUNTS_V0_MIGRATION_GROWTH_ALLOWANCE: MetricAllowance = {
    "chars": 320,
    "utf8_bytes": 320,
    "lines": 10,
    "compact_payload_chars": 192,
}

# Explicit registry/runtime-root routing repeats one bounded command argument per
# executable action. The allowance covers the prefix and its JSON projection;
# it is per newly observed route, not per row, so unrelated output growth still
# fails under the normal hot-path policy.
_COMMAND_ROUTE_GROWTH_PER_ROUTE: MetricAllowance = {
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
_REWARD_MEMORY_OUTCOME_PROMPT_V1_MIGRATION_ALLOWANCE: MetricAllowance = {
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
_HEARTBEAT_USER_LANGUAGE_V1_MIGRATION_ALLOWANCE: dict[str, MetricAllowance] = {
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
_TURN_HOST_AND_MANAGED_EXECUTOR_BINDING_V0_GROWTH_ALLOWANCE: MetricAllowance = {
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

# loopx_guided_todo_delta_v0 adds the continuation-aware Todo authoring
# decision contract (reuse/update/link_successor/add_new plus a bounded
# runnable-frontier summary) to the guided start-goal packet when an
# existing-Agent takeover is projected. The allowance is bound to the
# declared none-to-v0 schema migration; after merge the ordinary
# start_goal_guided growth budgets apply again.
_GUIDED_TODO_DELTA_V0_MIGRATION_GROWTH_ALLOWANCE: MetricAllowance = {
    "chars": 512,
    "utf8_bytes": 512,
    "lines": 16,
    "compact_payload_chars": 448,
}

# The unchanged status matrix adds 2,801 pretty JSON chars / 102 lines /
# 1,910 compact chars for five source rows. Quota should-run adds 2,617 JSON
# chars or 167 Markdown chars for its inherited and direct source rows. This
# reviewed one-time transition leaves headroom without changing absolute
# ceilings or ordinary v0-to-v0 growth.
_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE = GrowthAllowance(
    ratio=0,
    json={"chars": 3_000, "utf8_bytes": 3_000, "lines": 110, "compact_payload_chars": 2_048},
    markdown={"chars": 192, "utf8_bytes": 224, "lines": 3, "compact_payload_chars": 0},
)
_QUOTA_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE = GrowthAllowance(
    ratio=0,
    json={"chars": 3_000, "utf8_bytes": 3_000, "lines": 110, "compact_payload_chars": 2_800},
    markdown={"chars": 192, "utf8_bytes": 224, "lines": 3, "compact_payload_chars": 0},
)
# The unchanged 18-row Todo matrix adds 783–854 JSON chars and no lines.
# Source freshness and omitted-work coverage are useful; Markdown is unchanged.
_TODO_PROJECTION_ENVELOPE_V0_MIGRATION_ALLOWANCE: MetricAllowance = {
    "chars": 896,
    "utf8_bytes": 896,
    "lines": 0,
    "compact_payload_chars": 896,
}

_DECISION_EVIDENCE_ALLOWANCE_BY_ROW_ID: dict[str, MetricAllowance] = {
    "surface/quota_should_run/crowded/json": {
        "chars": 7_000,
        "utf8_bytes": 7_000,
        "lines": 96,
        "compact_payload_chars": 6_000,
    },
    "surface/diagnose/crowded/json": {
        "chars": 15_000,
        "utf8_bytes": 15_000,
        "lines": 190,
        "compact_payload_chars": 12_000,
    },
    "variant/review_packet_full/small/json": {
        "chars": 800,
        "utf8_bytes": 800,
        "lines": 24,
        "compact_payload_chars": 650,
    },
}

_HEARTBEAT_SURFACES = frozenset(
    {
        "heartbeat_prompt_thin",
        "heartbeat_prompt_brief",
        "heartbeat_prompt_compact",
        "heartbeat_prompt_full",
    }
)


def surface_from_row_id(row_id: str) -> str:
    """Extract the surface component from a canonical row_id (<kind>/<surface>/...)."""
    return row_id.partition("/")[2].partition("/")[0]


def tuple_field(row: JsonObject, field: str) -> tuple[str, ...]:
    """Read an optional string-sequence version field, rejecting mappings and strings."""
    raw = row.get(field)
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise TypeError(f"{field} must be a sequence of strings")
    versions: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise TypeError(f"{field} entries must be strings")
        versions.append(item)
    return tuple(versions)


def mapping_field(row: JsonObject, field: str) -> Mapping[object, object]:
    """Read an optional mapping field without masking non-mapping inputs."""
    raw = row.get(field)
    if not raw:
        return {}
    if isinstance(raw, Mapping):
        return raw
    raise TypeError(f"{field} must be a mapping")


def shape_paths(row: JsonObject) -> set[str]:
    """Return json_shape_paths as a verified set of strings."""
    raw = row.get("json_shape_paths")
    if raw is None:
        return set()
    if isinstance(raw, (str, bytes, bytearray, Mapping)) or not isinstance(
        raw, Iterable
    ):
        raise TypeError("json_shape_paths must be iterable")
    paths: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise TypeError("json_shape_paths entries must be strings")
        paths.add(item)
    return paths


def _turn_host_and_managed_executor_binding_allowance(
    row_id: str,
    base: JsonObject,
    candidate: JsonObject,
    metric: Metric,
) -> int:
    surface = surface_from_row_id(row_id)
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
    base: JsonObject,
    candidate: JsonObject,
    metric: Metric,
) -> int:
    surface = surface_from_row_id(row_id)
    if (
        row_id.startswith(("surface/", "variant/"))
        and surface in _HEARTBEAT_SURFACES
        and base.get("reward_memory_outcome_prompt_revision") is None
        and candidate.get("reward_memory_outcome_prompt_revision")
        == "reward_memory_outcome_prompt_v1"
    ):
        return _REWARD_MEMORY_OUTCOME_PROMPT_V1_MIGRATION_ALLOWANCE[metric]
    return 0


def _heartbeat_peer_admission_migration_allowance(
    row_id: str,
    base: JsonObject,
    candidate: JsonObject,
    metric: Metric,
) -> int:
    # Readable peer admission adds 117 chars on the same CLI fixture. Admit
    # this reviewed transition once; ordinary growth limits resume at v1.
    if (
        row_id.startswith("surface/heartbeat_prompt_thin/")
        and base.get("heartbeat_peer_admission_prompt_revision") is None
        and candidate.get("heartbeat_peer_admission_prompt_revision")
        == "heartbeat_peer_admission_v1"
        and metric in {"chars", "utf8_bytes", "compact_payload_chars"}
    ):
        return 160
    return 0


def _heartbeat_user_language_migration_allowance(
    row_id: str,
    base: JsonObject,
    candidate: JsonObject,
    metric: Metric,
) -> int:
    surface = surface_from_row_id(row_id)
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


def _command_route_growth_allowances(
    base: JsonObject, candidate: JsonObject
) -> tuple[dict[str, int], MetricAllowance]:
    additions: dict[str, int] = {}
    for option in ("runtime_root", "registry"):
        field = f"{option}_command_route_count"
        before, after = base.get(field), candidate.get(field)
        # Missing, malformed or negative observations never grant an allowance.
        if type(before) is int and type(after) is int and 0 <= before < after:
            additions[option] = after - before
    if not additions:
        return {}, {}
    return additions, {
        metric: sum(additions.values()) * allowance
        for metric, allowance in _COMMAND_ROUTE_GROWTH_PER_ROUTE.items()
    }


def _rows_by_id(receipt: JsonObject) -> dict[str, JsonObject]:
    if receipt.get("schema_version") != CLI_OUTPUT_PROBE_SCHEMA_VERSION:
        raise ValueError("CLI output probe receipt has an unsupported schema_version")
    if receipt.get("fixture_contract_version") != CLI_OUTPUT_FIXTURE_CONTRACT_VERSION:
        raise ValueError(
            "CLI output probe receipt has an unsupported fixture_contract_version"
        )
    rows = receipt.get("rows")
    if not isinstance(rows, list):
        raise ValueError("CLI output probe receipt rows must be a list")
    indexed: dict[str, JsonObject] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("CLI output probe row must be an object with row_id")
        raw_row: dict[object, object] = row
        row_id = raw_row.get("row_id")
        if not isinstance(row_id, str):
            raise ValueError("CLI output probe row must be an object with row_id")
        if row_id in indexed:
            raise ValueError(f"duplicate CLI output probe row_id: {row_id}")
        indexed[row_id] = {str(key): value for key, value in raw_row.items()}
    return indexed


def _growth_limit(*, policy: str, output_format: str, metric: Metric, base: int) -> int:
    allowance = _GROWTH_ALLOWANCE_BY_POLICY.get(policy)
    if allowance is None:
        raise ValueError(f"unsupported CLI output qualification policy: {policy}")
    floors = allowance.json if output_format == "json" else allowance.markdown
    return max(floors[metric], math.ceil(base * allowance.ratio))


def _removed(base: JsonObject, candidate: JsonObject, field: str) -> list[str]:
    base_raw = base.get(field, [])
    candidate_raw = candidate.get(field, [])
    if isinstance(base_raw, (str, bytes, bytearray, Mapping)) or not isinstance(
        base_raw, Iterable
    ):
        raise TypeError(f"{field} must be iterable")
    if isinstance(candidate_raw, (str, bytes, bytearray, Mapping)) or not isinstance(
        candidate_raw, Iterable
    ):
        raise TypeError(f"{field} must be iterable")
    base_values = {str(value) for value in base_raw}
    candidate_values = {str(value) for value in candidate_raw}
    return sorted(base_values - candidate_values)


def _has_decision_evidence(row: JsonObject) -> bool:
    paths = shape_paths(row)
    return any(
        path.endswith(".replan_context.core_goal")
        and path.removesuffix("core_goal") + "evidence" in paths
        and path.removesuffix("core_goal") + "coverage_ledger" in paths
        for path in paths
    )


def _decision_evidence_migration_allowance(
    base: JsonObject, candidate: JsonObject, metric: Metric
) -> int:
    # One-time, measured transition from coverage-only to dense decision context.
    # Future dense->dense edits and ordinary guard rows keep their original budget.
    if (
        candidate.get("format") != "json"
        or _has_decision_evidence(base)
        or not _has_decision_evidence(candidate)
    ):
        return 0
    row_id = str(candidate["row_id"])
    limits = _DECISION_EVIDENCE_ALLOWANCE_BY_ROW_ID.get(row_id)
    if limits is None:
        return 0
    return limits[metric]
