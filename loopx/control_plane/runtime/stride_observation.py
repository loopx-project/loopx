"""Read-only hierarchical stride observation (RFC #3204, M1 shadow slice).

This module derives a provider-neutral ``hierarchical_stride_observation_v0``
projection from existing public-safe run receipts. It is observation only:
it never changes Todo selection, scheduler cadence, quota spend,
notification, gates, or execution behavior (``shadow_only`` is always true).
Fields that cannot be derived from existing receipts stay ``unknown`` rather
than being guessed from prose or command names.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

STRIDE_OBSERVATION_SCHEMA_VERSION = "hierarchical_stride_observation_v0"
STRIDE_EVALUATION_SCHEMA_VERSION = "hierarchical_stride_evaluation_v0"
EVIDENCE_FRESH_WINDOW_HOURS = 6
AUTHORITY_CHANGE_CLASSIFICATIONS = frozenset(
    {
        # These are the complete values emitted by the current controlled
        # authority/replan writers. Unknown classifications stay no-change.
        "bounded_replan_progress",
        "operator_gate_approved",
        "operator_gate_rejected",
        "operator_gate_deferred",
    }
)


def _compact_text(value: Any, *, limit: int = 180) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _has_authority_change_marker(value: Any) -> bool:
    """Return true only for an exact value from the controlled writer set.

    ``classification`` remains a descriptive, caller-supplied field in M1.
    It therefore cannot be parsed as semantic evidence: token, substring,
    negation, and co-occurrence heuristics all admit false authority changes.
    Until run receipts carry a typed authority-change field, this closed set is
    the fail-closed compatibility boundary.
    """

    classification = _compact_text(value, limit=240).casefold()
    return classification in AUTHORITY_CHANGE_CLASSIFICATIONS


def read_run_index(runtime_root: Path, goal_id: str) -> list[dict[str, Any]]:
    """Read one goal's durable public run index (best effort, tolerant)."""

    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    if not index_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _parse_timestamp(value: Any) -> datetime | None:
    text = _compact_text(value, limit=80)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_stride_observation(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any]:
    """Project a shadow-only stride observation from existing run receipts."""

    runs = read_run_index(runtime_root, goal_id)
    agent_runs = [
        run
        for run in runs
        if _compact_text(run.get("agent_id")) == agent_id
    ]
    generated_at = datetime.now(UTC)
    fresh_cutoff = generated_at - timedelta(hours=EVIDENCE_FRESH_WINDOW_HOURS)
    latest_run = agent_runs[-1] if agent_runs else None
    latest_generated_at = _parse_timestamp(
        (latest_run or {}).get("generated_at")
    )
    evidence_fresh = (
        latest_generated_at is not None
        and latest_generated_at >= fresh_cutoff
    )

    authority_change_index: int | None = None
    for index, run in enumerate(agent_runs):
        if _has_authority_change_marker(run.get("classification")):
            authority_change_index = index
    bounded_slices_since_change = (
        len(agent_runs) - authority_change_index - 1
        if authority_change_index is not None
        else len(agent_runs)
    )

    material_slices = sum(
        1
        for run in agent_runs
        if _compact_text(run.get("delivery_outcome")) == "outcome_progress"
    )
    latest_outcome = (
        _compact_text((latest_run or {}).get("delivery_outcome"), limit=60)
        or "none"
    )

    mismatch_signals: list[str] = []
    if agent_runs and not evidence_fresh:
        mismatch_signals.append("settlement_lag")

    return {
        "schema_version": STRIDE_OBSERVATION_SCHEMA_VERSION,
        "lineage": {
            "goal_id": _compact_text(goal_id),
            "agent_id": _compact_text(agent_id),
            "source_run_count": len(agent_runs),
        },
        "delivery": {
            "material_slices": material_slices,
            "latest_outcome": latest_outcome,
            "evidence_fresh": evidence_fresh,
        },
        "authority": {
            "bounded_slices_since_change": bounded_slices_since_change,
            "segment_disposition": "unknown",
        },
        "effect": {
            "unknown": True,
        },
        "mismatch_signals": mismatch_signals,
        "shadow_only": True,
    }


def evaluate_stride_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """Offline evaluator: report derived mismatch signals, recommend nothing."""

    signals = list(observation.get("mismatch_signals") or [])
    return {
        "schema_version": STRIDE_EVALUATION_SCHEMA_VERSION,
        "signals": signals,
        "recommendations": [],
        "shadow_only": True,
    }
