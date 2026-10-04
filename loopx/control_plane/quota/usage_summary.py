from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Callable

from .ledger_readback import net_quota_slot_spend, quota_slot_contribution
from .spend_sources import VISIBLE_GOAL_SLOT_SPEND_SOURCE
from .usage_collector import UsageRowError, UsageSample, collect_usage_for_run
from ..runtime.time import now_utc

USAGE_PROXY_NOTE = (
    "run-history proxy; carries aggregate token/cost/duration, "
    "excludes raw thread logs"
)

ParseTimestamp = Callable[[Any], Any]
SlotContribution = tuple[tuple[str, str], str, int]
USAGE_METRIC_NAMES = (
    "input_tokens",
    "output_tokens",
    "cache_tokens",
    "cost_usd",
    "duration_ms",
)


def _goal_quota_spend_slots(
    contributions: list[SlotContribution],
) -> tuple[dict[str, int], int]:
    """Fold each run's slot contribution into per-goal window spend.

    Buckets are keyed by ``(goal_id, run key)`` and clamped by the shared
    ledger rule, so a goal only ever loses the spend a void actually targets.
    """

    per_goal: dict[str, int] = {}
    total = 0
    for (goal_id, _run_key), slots in net_quota_slot_spend(contributions).items():
        per_goal[goal_id] = per_goal.get(goal_id, 0) + slots
        total += slots
    return per_goal, total


def is_automation_run(run: dict[str, Any]) -> bool:
    quota_event = run.get("quota_event") if isinstance(run.get("quota_event"), dict) else {}
    source = str(quota_event.get("source") or run.get("source") or "").lower()
    if source == VISIBLE_GOAL_SLOT_SPEND_SOURCE:
        return False
    if source in {"heartbeat", "automation", "cron"}:
        return True
    if "heartbeat" in source or "automation" in source:
        return True
    return str(run.get("classification") or "") in {"quota_slot_spent", "quota_slot_voided"}


def is_progress_signal_run(run: dict[str, Any]) -> bool:
    classification = str(run.get("classification") or "")
    return bool(classification and classification not in {"quota_slot_spent", "quota_slot_voided", "state_refreshed"})


def blank_usage_goal(goal_id: str) -> dict[str, Any]:
    return {
        "goal_id": goal_id,
        "runs_24h": 0,
        "runs_7d": 0,
        "quota_spend_slots_24h": 0,
        "quota_spend_slots_7d": 0,
        "automation_run_count_24h": 0,
        "automation_run_count_7d": 0,
        "progress_signal_run_count_24h": 0,
        "progress_signal_run_count_7d": 0,
        "project_share_24h": 0.0,
        "input_tokens_24h": 0,
        "input_tokens_7d": 0,
        "output_tokens_24h": 0,
        "output_tokens_7d": 0,
        "cache_tokens_24h": 0,
        "cache_tokens_7d": 0,
        "cost_usd_24h": 0.0,
        "cost_usd_7d": 0.0,
        "duration_ms_24h": 0,
        "duration_ms_7d": 0,
    }


def _accumulate_usage(
    bucket: dict[str, Any],
    sample: UsageSample,
    suffix: str,
    observed_metrics: set[str],
) -> None:
    bucket[f"input_tokens_{suffix}"] = (
        int(bucket.get(f"input_tokens_{suffix}") or 0) + sample.input_tokens
    )
    bucket[f"output_tokens_{suffix}"] = (
        int(bucket.get(f"output_tokens_{suffix}") or 0) + sample.output_tokens
    )
    observed_metrics.add(f"input_tokens_{suffix}")
    observed_metrics.add(f"output_tokens_{suffix}")
    if sample.cache_tokens is not None:
        bucket[f"cache_tokens_{suffix}"] = (
            int(bucket.get(f"cache_tokens_{suffix}") or 0) + sample.cache_tokens
        )
        observed_metrics.add(f"cache_tokens_{suffix}")
    if sample.cost_usd is not None:
        key = f"cost_usd_{suffix}"
        accumulated_cost = float(bucket.get(key) or 0.0) + sample.cost_usd
        if not math.isfinite(accumulated_cost):
            raise UsageRowError(f"usage summary {key} must be finite")
        bucket[key] = accumulated_cost
        observed_metrics.add(key)
    if sample.duration_ms is not None:
        bucket[f"duration_ms_{suffix}"] = (
            int(bucket.get(f"duration_ms_{suffix}") or 0) + sample.duration_ms
        )
        observed_metrics.add(f"duration_ms_{suffix}")


def _round_cost(bucket: dict[str, Any]) -> None:
    for suffix in ("24h", "7d"):
        key = f"cost_usd_{suffix}"
        if key in bucket:
            rounded_cost = round(float(bucket[key]), 6)
            if not math.isfinite(rounded_cost):
                raise UsageRowError(f"usage summary {key} must be finite")
            bucket[key] = rounded_cost


def _strip_unobserved_usage_metrics(
    bucket: dict[str, Any],
    observed_metrics: set[str],
) -> None:
    for suffix in ("24h", "7d"):
        for metric_name in USAGE_METRIC_NAMES:
            key = f"{metric_name}_{suffix}"
            if key not in observed_metrics:
                bucket.pop(key, None)


def build_usage_summary(
    history: dict[str, Any],
    *,
    parse_timestamp: ParseTimestamp,
) -> dict[str, Any]:
    now = now_utc()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)
    totals = {
        "runs_24h": 0,
        "runs_7d": 0,
        "quota_spend_slots_24h": 0,
        "quota_spend_slots_7d": 0,
        "automation_run_count_24h": 0,
        "automation_run_count_7d": 0,
        "progress_signal_run_count_24h": 0,
        "progress_signal_run_count_7d": 0,
        "input_tokens_24h": 0,
        "input_tokens_7d": 0,
        "output_tokens_24h": 0,
        "output_tokens_7d": 0,
        "cache_tokens_24h": 0,
        "cache_tokens_7d": 0,
        "cost_usd_24h": 0.0,
        "cost_usd_7d": 0.0,
        "duration_ms_24h": 0,
        "duration_ms_7d": 0,
    }
    goals: dict[str, dict[str, Any]] = {}
    observed_usage_metrics: set[str] = set()
    goal_usage_metrics: dict[str, set[str]] = {}
    slot_contributions_24h: list[SlotContribution] = []
    slot_contributions_7d: list[SlotContribution] = []
    sample_count = 0

    for run in history.get("runs") or []:
        if not isinstance(run, dict):
            continue
        sample_count += 1
        generated_at = parse_timestamp(run.get("generated_at"))
        if generated_at is None:
            continue
        goal_id = str(run.get("goal_id") or "unknown-goal")
        goal = goals.setdefault(goal_id, blank_usage_goal(goal_id))
        slot_contribution = quota_slot_contribution(run)
        automation_event = is_automation_run(run)
        progress_signal = is_progress_signal_run(run)
        # Present-but-illegal usage fails closed inside collect_usage_for_run.
        usage_sample = collect_usage_for_run(run)
        goal_metrics = goal_usage_metrics.setdefault(goal_id, set())

        if generated_at >= cutoff_7d:
            totals["runs_7d"] += 1
            goal["runs_7d"] += 1
            if slot_contribution is not None:
                kind, run_key, slots = slot_contribution
                slot_contributions_7d.append(((goal_id, run_key), kind, slots))
            if automation_event:
                totals["automation_run_count_7d"] += 1
                goal["automation_run_count_7d"] += 1
            if progress_signal:
                totals["progress_signal_run_count_7d"] += 1
                goal["progress_signal_run_count_7d"] += 1
            if usage_sample is not None:
                _accumulate_usage(totals, usage_sample, "7d", observed_usage_metrics)
                _accumulate_usage(goal, usage_sample, "7d", goal_metrics)
        if generated_at >= cutoff_24h:
            totals["runs_24h"] += 1
            goal["runs_24h"] += 1
            if slot_contribution is not None:
                kind, run_key, slots = slot_contribution
                slot_contributions_24h.append(((goal_id, run_key), kind, slots))
            if automation_event:
                totals["automation_run_count_24h"] += 1
                goal["automation_run_count_24h"] += 1
            if progress_signal:
                totals["progress_signal_run_count_24h"] += 1
                goal["progress_signal_run_count_24h"] += 1
            if usage_sample is not None:
                _accumulate_usage(totals, usage_sample, "24h", observed_usage_metrics)
                _accumulate_usage(goal, usage_sample, "24h", goal_metrics)

    goal_slots_24h, total_slots_24h = _goal_quota_spend_slots(slot_contributions_24h)
    goal_slots_7d, total_slots_7d = _goal_quota_spend_slots(slot_contributions_7d)
    totals["quota_spend_slots_24h"] = total_slots_24h
    totals["quota_spend_slots_7d"] = total_slots_7d
    for goal_id, goal in goals.items():
        goal["quota_spend_slots_24h"] = goal_slots_24h.get(goal_id, 0)
        goal["quota_spend_slots_7d"] = goal_slots_7d.get(goal_id, 0)

    if totals["runs_24h"]:
        for goal in goals.values():
            goal["project_share_24h"] = round(goal["runs_24h"] / totals["runs_24h"], 3)

    _round_cost(totals)
    for goal in goals.values():
        _round_cost(goal)

    goal_rows = sorted(
        goals.values(),
        key=lambda item: (
            item["runs_24h"],
            item["quota_spend_slots_24h"],
            float(item.get("cost_usd_24h") or 0.0),
            int(item.get("input_tokens_24h") or 0),
            item["runs_7d"],
            item["goal_id"],
        ),
        reverse=True,
    )
    _strip_unobserved_usage_metrics(totals, observed_usage_metrics)
    for goal in goal_rows:
        _strip_unobserved_usage_metrics(
            goal,
            goal_usage_metrics.get(str(goal.get("goal_id") or ""), set()),
        )
    return {
        "available": True,
        "source": "run_history",
        "generated_at": now.isoformat(),
        "sample_run_count": sample_count,
        "proxy_note": USAGE_PROXY_NOTE,
        "totals": totals,
        "goals": goal_rows,
    }
