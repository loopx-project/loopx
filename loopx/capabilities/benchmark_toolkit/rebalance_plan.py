from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any

from .concurrency_envelope import _reject_unknown_fields, _timestamp
from .experiment_identity import experiment_token_text as _token

BENCHMARK_REBALANCE_INVENTORY_SCHEMA_VERSION = "benchmark_rebalance_inventory_v0"
BENCHMARK_REBALANCE_PLAN_SCHEMA_VERSION = "benchmark_rebalance_plan_v0"

_INVENTORY_FIELDS = {"schema_version", "candidates"}
_CANDIDATE_FIELDS = {
    "candidate_id",
    "run_ids",
    "reclaim_priority",
    "state",
    "admitted_at",
}


class BenchmarkRebalanceAction(str, Enum):
    HOLD = "hold"
    STOP_CANDIDATE = "stop_candidate"
    AWAIT_CANDIDATE_CLOSEOUT = "await_candidate_closeout"


class BenchmarkRebalanceCandidateState(str, Enum):
    ACTIVE = "active"
    STOP_REQUESTED = "stop_requested"
    CAPACITY_RELEASED = "capacity_released"


_CANDIDATE_STATES = {item.value for item in BenchmarkRebalanceCandidateState}


def _priority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1024:
        raise ValueError("reclaim_priority must be an integer from 0 through 1024")
    return value


def _normalize_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("rebalance candidate must be an object")
    _reject_unknown_fields(value, allowed=_CANDIDATE_FIELDS, field="candidate")
    candidate_id = _token(value.get("candidate_id"), field="candidate_id")
    raw_run_ids = value.get("run_ids")
    if not isinstance(raw_run_ids, list) or not raw_run_ids:
        raise ValueError("candidate run_ids must be a non-empty list")
    run_ids = [_token(item, field="run_id") for item in raw_run_ids]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("candidate run_ids cannot repeat")
    state = _token(value.get("state"), field="candidate_state")
    if state not in _CANDIDATE_STATES:
        raise ValueError("candidate state is unsupported")
    return {
        "candidate_id": candidate_id,
        "run_ids": sorted(run_ids),
        "reclaim_priority": _priority(value.get("reclaim_priority")),
        "state": state,
        "admitted_at": _timestamp(value.get("admitted_at"), field="admitted_at"),
    }


def normalize_benchmark_rebalance_inventory(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("benchmark rebalance inventory must be an object")
    _reject_unknown_fields(value, allowed=_INVENTORY_FIELDS, field="inventory")
    if value.get("schema_version") != BENCHMARK_REBALANCE_INVENTORY_SCHEMA_VERSION:
        raise ValueError("benchmark rebalance inventory schema mismatch")
    raw_candidates = value.get("candidates")
    if not isinstance(raw_candidates, list):
        raise TypeError("inventory candidates must be a list")
    candidates = [_normalize_candidate(item) for item in raw_candidates]
    candidate_ids = [item["candidate_id"] for item in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate_id values cannot repeat")
    seen: set[str] = set()
    for candidate in candidates:
        overlap = seen.intersection(candidate["run_ids"])
        if overlap:
            raise ValueError("candidate run membership cannot overlap")
        seen.update(candidate["run_ids"])
    return {
        "schema_version": BENCHMARK_REBALANCE_INVENTORY_SCHEMA_VERSION,
        "candidates": sorted(candidates, key=lambda item: item["candidate_id"]),
    }


def build_benchmark_rebalance_plan(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Select one runner-owned stop group without performing any mutation."""

    inventory = normalize_benchmark_rebalance_inventory(
        {
            "schema_version": BENCHMARK_REBALANCE_INVENTORY_SCHEMA_VERSION,
            "candidates": list(candidates),
        }
    )
    normalized = inventory["candidates"]
    contenders = [
        item
        for item in normalized
        if item["state"] != BenchmarkRebalanceCandidateState.CAPACITY_RELEASED.value
    ]
    base: dict[str, Any] = {
        "ok": True,
        "schema_version": BENCHMARK_REBALANCE_PLAN_SCHEMA_VERSION,
        "write_performed": False,
        "process_signal_sent": False,
        "candidate_count": len(normalized),
        "selected_candidate": None,
        "blocking_candidate_ids": [],
    }
    if not contenders:
        return {
            **base,
            "action": BenchmarkRebalanceAction.HOLD.value,
            "reason_codes": ["no_reclaim_candidates"],
            "lowest_reclaim_priority": None,
            "runner_action_required": False,
        }
    lowest_priority = min(item["reclaim_priority"] for item in contenders)
    tier = [item for item in contenders if item["reclaim_priority"] == lowest_priority]
    active = [
        item
        for item in tier
        if item["state"] == BenchmarkRebalanceCandidateState.ACTIVE.value
    ]
    if active:
        selected = max(
            active,
            key=lambda item: (
                datetime.fromisoformat(item["admitted_at"]),
                item["candidate_id"],
            ),
        )
        return {
            **base,
            "action": BenchmarkRebalanceAction.STOP_CANDIDATE.value,
            "reason_codes": ["lowest_reclaim_priority_newest_first"],
            "lowest_reclaim_priority": lowest_priority,
            "selected_candidate": selected,
            "runner_action_required": True,
        }
    return {
        **base,
        "action": BenchmarkRebalanceAction.AWAIT_CANDIDATE_CLOSEOUT.value,
        "reason_codes": ["lower_reclaim_priority_closeout_pending"],
        "lowest_reclaim_priority": lowest_priority,
        "blocking_candidate_ids": sorted(item["candidate_id"] for item in tier),
        "runner_action_required": False,
    }


def build_benchmark_rebalance_plan_from_inventory(
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = normalize_benchmark_rebalance_inventory(inventory)
    return build_benchmark_rebalance_plan(normalized["candidates"])


def render_benchmark_rebalance_plan_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Benchmark rebalance plan",
        "",
        f"- Action: `{payload.get('action', 'unknown')}`",
        f"- Candidates: `{payload.get('candidate_count', 0)}`",
        f"- Runner action required: `{str(bool(payload.get('runner_action_required'))).lower()}`",
        "- Process signal sent: `false`",
    ]
    selected = payload.get("selected_candidate")
    if isinstance(selected, Mapping):
        lines.append(f"- Selected candidate: `{selected.get('candidate_id')}`")
    return "\n".join(lines) + "\n"
