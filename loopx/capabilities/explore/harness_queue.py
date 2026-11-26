"""Execution queue, shared JSON/coercion helpers, and scoring for Explore harness."""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
import concurrent.futures
from enum import Enum
import json
from pathlib import Path
import threading
import time
from typing import Protocol, TypedDict, cast

from .episode_runtime import item_concurrency_keys

HARNESS_ARM_SCHEMA_VERSION = "loopx_explore_harness_arm_v0"
HARNESS_RUNTIME_POLICY_SCHEMA_VERSION = "loopx_explore_harness_runtime_policy_v0"

DEFAULT_OBSERVATION_WEIGHT = 1.0
_MIN_DURATION_MINUTES = 0.05


class ItemFailurePolicy(str, Enum):
    """Policy controlling how per-item execution exceptions are handled."""

    RECORD = "record"
    FATAL = "fatal"


ITEM_FAILURE_POLICY_RECORD = ItemFailurePolicy.RECORD.value
ITEM_FAILURE_POLICY_FATAL = ItemFailurePolicy.FATAL.value
ITEM_FAILURE_POLICIES = {policy.value for policy in ItemFailurePolicy}


class SeedAdapter(Protocol):
    """Minimum adapter interface required by ``run_budget_arm``."""

    def list_seed_items(self) -> Sequence[Mapping[str, object]]: ...


class HarnessLaneRecord(TypedDict):
    """Execution records and timing for one concurrent queue worker lane."""

    branch_id: str
    launch_index: int
    wall_minutes: float
    item_ids: list[object]
    families: list[object]
    results: list[dict[str, object]]


class HarnessCheckpointPoint(TypedDict):
    """Cumulative anytime curve sample emitted after each epoch."""

    epoch: int
    elapsed_minutes: float
    raw_value_cum: float
    novel_value_cum: float
    coverage_count_cum: int
    variant_records_cum: int
    requested_worker_minutes_cum: float
    execution_metrics_cum: dict[str, object]


class HarnessEpochRecord(TypedDict):
    """One completed epoch's execution summary and lane payloads."""

    schema_version: str
    epoch: int
    epoch_wall_minutes: float
    queue_size: int
    execution_unit_count: int
    execution_metrics: dict[str, object]
    frontier: dict[str, object] | None
    lanes: list[HarnessLaneRecord]


class HarnessResumeSummary(TypedDict):
    """Restart and checkpoint metadata attached to an arm result."""

    schema_version: str
    enabled: bool
    resumed: bool
    checkpoint_schema_version: str
    checkpoint_path: str | None
    restored_epoch_count: int


class HarnessEpisodePolicy(TypedDict):
    """Episode grouping capability mode reported in ``runtime_policy``."""

    mode: str
    adapter_owned_restore: bool


class HarnessRuntimePolicy(TypedDict):
    """Runtime policy and execution mode metadata for one budget arm."""

    schema_version: str
    item_failure_policy: str
    item_exception_isolation: bool
    episode_execution: HarnessEpisodePolicy
    planner_guidance: dict[str, dict[str, object]]


class HarnessArmResult(TypedDict):
    """Full structured payload returned by ``run_budget_arm``."""

    schema_version: str
    arm_key: str
    budget_minutes: float
    elapsed_minutes: float
    stop_reason: str
    epoch_count: int
    raw_value_total: float
    novel_value_total: float
    variant_records_total: int
    execution_metrics: dict[str, object]
    coverage_count: int
    coverage_first_seen_minutes: dict[str, float]
    checkpoints: list[HarnessCheckpointPoint]
    epochs: list[HarnessEpochRecord]
    resume: HarnessResumeSummary
    runtime_policy: HarnessRuntimePolicy


class HarnessArmSummary(TypedDict):
    """Aggregated summary row for one arm in ``aggregate_arms``."""

    novel_value_endpoint: object
    novel_value_auc: float
    raw_value_total: object
    coverage_count: object
    variant_records_total: object
    epoch_count: object
    elapsed_minutes: object
    stop_reason: object
    execution_metrics: dict[str, object]
    novel_value_per_requested_worker_minute: float
    novel_value_per_effective_compute_minute: float | None
    anytime_curve: list[dict[str, object]]


class HarnessScoreSummary(TypedDict):
    """Top-level N-arm comparison payload returned by ``aggregate_arms``."""

    schema_version: str
    budget_minutes_per_arm: float
    worker_count: int
    coverage_denominator: int | None
    arms: dict[str, HarnessArmSummary]


def _now_perf_minutes(start: float) -> float:
    return (time.perf_counter() - start) / 60.0


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _read_json(path: Path) -> object:
    if not path.exists():
        return None
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8-sig")))
    except json.JSONDecodeError:
        return None


def _as_float(value: object, *, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"expected numeric value, got {type(value).__name__}")


def _as_int(value: object, *, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError(f"expected integer value, got {type(value).__name__}")


def _collection_or_empty(
    raw: object,
    *,
    field_name: str,
) -> Collection[object]:
    """Accept collections and baseline empty containers; fail closed otherwise."""

    if raw is None or raw == {} or raw == []:
        return ()
    if isinstance(raw, Collection) and not isinstance(
        raw, (str, bytes, bytearray, Mapping)
    ):
        return raw
    raise TypeError(f"{field_name} must be a sequence")


def _mapping_or_empty(
    raw: object,
    *,
    field_name: str,
) -> Mapping[object, object]:
    """Accept mappings and legacy empty lists; fail closed on invalid types."""

    if raw is None or raw == [] or raw == ():
        return {}
    if isinstance(raw, Mapping):
        return raw
    raise TypeError(f"{field_name} must be a mapping")


def _normalize_item_failure_policy(value: object) -> str:
    if isinstance(value, ItemFailurePolicy):
        return value.value
    policy = str(value or ITEM_FAILURE_POLICY_RECORD).strip().lower().replace("_", "-")
    if policy not in ITEM_FAILURE_POLICIES:
        raise ValueError(
            "item_failure_policy must be one of: "
            + ", ".join(sorted(ITEM_FAILURE_POLICIES))
        )
    return policy


def _adapter_error_record(
    item: Mapping[str, object],
    error: Exception,
    *,
    duration_minutes: float,
) -> dict[str, object]:
    message = " ".join(str(error).split())
    spec = item.get("variant_spec")
    return {
        "item_id": item.get("item_id"),
        "family": item.get("family"),
        "is_variant": bool(item.get("is_variant")),
        # The error factory must never raise itself: a malformed variant_spec
        # would otherwise escalate one recordable failure into a lost lane.
        "variant_spec_id": (
            spec.get("spec_id")
            if item.get("is_variant") and isinstance(spec, Mapping)
            else None
        ),
        "execution_status": "adapter_error",
        "item_failure_policy": ITEM_FAILURE_POLICY_RECORD,
        "accepted": False,
        "observation_keys": [],
        "weighted_flags": {},
        "duration_minutes": max(_MIN_DURATION_MINUTES, float(duration_minutes)),
        "retryable_infra_error": bool(
            getattr(error, "retryable_infra_error", False)
        ),
        "adapter_error": {
            "schema_version": "loopx_explore_adapter_error_v0",
            "type": type(error).__name__,
            "message": message[:240],
        },
    }


def _normalize_outcome_batch(
    outcome: object,
    *,
    item: Mapping[str, object],
) -> list[dict[str, object]]:
    if isinstance(outcome, Mapping):
        batch = [dict(outcome)]
    elif isinstance(outcome, Sequence) and not isinstance(
        outcome, (str, bytes, bytearray)
    ):
        batch = []
        for candidate in outcome:
            if not isinstance(candidate, Mapping):
                raise TypeError(
                    "queue execute sequences must contain dict observation records"
                )
            batch.append(dict(candidate))
    else:
        raise TypeError(
            "queue execute must return a dict or sequence of dict observations"
        )
    if not batch:
        raise TypeError("queue execute must return at least one observation")
    spec = item.get("variant_spec")
    spec_id = spec.get("spec_id") if isinstance(spec, Mapping) else None
    for record in batch:
        record.setdefault("item_id", item.get("item_id"))
        record.setdefault("family", item.get("family"))
        record.setdefault("is_variant", bool(item.get("is_variant")))
        if item.get("is_variant"):
            record.setdefault("variant_spec_id", spec_id)
    return batch


def _pull_next_item(
    pending: list[dict[str, object]],
    in_flight: set[str],
    lock: threading.Lock,
) -> dict[str, object] | None:
    with lock:
        for index, item in enumerate(pending):
            keys = item_concurrency_keys(item)
            if any(key in in_flight for key in keys):
                continue
            pending.pop(index)
            in_flight.update(keys)
            return item
        return None


def _run_queue_worker(
    worker_index: int,
    *,
    pending: list[dict[str, object]],
    in_flight: set[str],
    lock: threading.Lock,
    execute: Callable[..., object],
    run_root: Path,
    arm: str,
    epoch: int,
    stagger_seconds: float,
    failure_policy: str,
) -> HarnessLaneRecord:
    branch_id = f"queue_worker_{worker_index + 1:02d}"
    started_at = time.perf_counter()
    if worker_index and stagger_seconds > 0:
        time.sleep(min(30.0, worker_index * stagger_seconds))
    results: list[dict[str, object]] = []
    while True:
        item = _pull_next_item(pending, in_flight, lock)
        if item is None:
            with lock:
                if not pending:
                    break
            time.sleep(0.5)
            continue
        keys = item_concurrency_keys(item)
        item_started = time.perf_counter()
        try:
            outcome = execute(
                item, run_root=run_root, arm=arm, epoch=epoch, branch_id=branch_id
            )
            batch = _normalize_outcome_batch(outcome, item=item)
        except Exception as error:
            if failure_policy == ITEM_FAILURE_POLICY_FATAL:
                raise
            batch = [
                _adapter_error_record(
                    item,
                    error,
                    duration_minutes=_now_perf_minutes(item_started),
                )
            ]
        finally:
            if keys:
                with lock:
                    in_flight.difference_update(keys)
        results.extend(batch)
    return {
        "branch_id": branch_id,
        "launch_index": worker_index,
        "wall_minutes": round(_now_perf_minutes(started_at), 3),
        "item_ids": [record.get("item_id") for record in results],
        "families": [record.get("family") for record in results],
        "results": results,
    }


def run_queue_epoch(
    items: Sequence[Mapping[str, object]],
    *,
    execute: Callable[..., object],
    worker_count: int,
    run_root: Path,
    arm: str,
    epoch: int,
    stagger_seconds: float = 0.0,
    item_failure_policy: str = ITEM_FAILURE_POLICY_RECORD,
) -> list[HarnessLaneRecord]:
    """Drain one epoch's work items through a shared pull queue.

    Items may declare one ``concurrency_key`` or several ``concurrency_keys``.
    Any overlap with an in-flight item waits until all keys free up (the generic
    form of "same write scope must serialize"); disjoint items run concurrently
    up to ``worker_count`` pullers. Returns per-worker lane records.
    """

    failure_policy = _normalize_item_failure_policy(item_failure_policy)
    lock = threading.Lock()
    pending: list[dict[str, object]] = [dict(item) for item in items]
    in_flight: set[str] = set()
    thread_count = max(1, min(int(worker_count), len(pending) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as pool:
        futures = [
            pool.submit(
                _run_queue_worker,
                index,
                pending=pending,
                in_flight=in_flight,
                lock=lock,
                execute=execute,
                run_root=run_root,
                arm=arm,
                epoch=epoch,
                stagger_seconds=stagger_seconds,
                failure_policy=failure_policy,
            )
            for index in range(thread_count)
        ]
        lanes = [future.result() for future in concurrent.futures.as_completed(futures)]
    lanes.sort(key=lambda lane: str(lane.get("branch_id")))
    return lanes


def step_auc(
    checkpoints: Sequence[Mapping[str, object]],
    *,
    value_key: str,
    horizon_minutes: float,
) -> float:
    """Step-integrate a cumulative anytime curve to the budget horizon."""

    auc = 0.0
    prev_t = 0.0
    prev_v = 0.0
    horizon = float(horizon_minutes)
    for checkpoint in checkpoints:
        elapsed = checkpoint.get("elapsed_minutes")
        t = min(_as_float(elapsed) if elapsed is not None else 0.0, horizon)
        if t > prev_t:
            auc += prev_v * (t - prev_t)
            prev_t = t
        val = checkpoint.get(value_key)
        prev_v = _as_float(val) if val is not None else 0.0
    if horizon > prev_t:
        auc += prev_v * (horizon - prev_t)
    return round(auc, 3)


def aggregate_arms(
    arms: Mapping[str, Mapping[str, object]],
    *,
    budget_minutes: float,
    worker_count: int,
    coverage_denominator: int | None = None,
) -> HarnessScoreSummary:
    """Generic two-arm (or N-arm) comparison on novelty-first metrics."""

    arm_summaries: dict[str, HarnessArmSummary] = {}
    for arm_key, arm in arms.items():
        raw_checkpoints = arm.get("checkpoints")
        checkpoints = [
            dict(point)
            for point in _collection_or_empty(raw_checkpoints, field_name="checkpoints")
            if isinstance(point, Mapping)
        ]
        raw_metrics = arm.get("execution_metrics")
        execution_metrics: dict[str, object] = (
            dict(raw_metrics) if isinstance(raw_metrics, Mapping) else {}
        )
        effective_compute = _as_float(
            execution_metrics.get("effective_compute_minutes") or 0.0
        )
        novel_total = arm.get("novel_value_total")
        novel_float = _as_float(novel_total) if novel_total is not None else 0.0
        arm_summaries[arm_key] = {
            "novel_value_endpoint": novel_total,
            "novel_value_auc": step_auc(
                checkpoints, value_key="novel_value_cum", horizon_minutes=budget_minutes
            ),
            "raw_value_total": arm.get("raw_value_total"),
            "coverage_count": arm.get("coverage_count"),
            "variant_records_total": arm.get("variant_records_total"),
            "epoch_count": arm.get("epoch_count"),
            "elapsed_minutes": arm.get("elapsed_minutes"),
            "stop_reason": arm.get("stop_reason"),
            "execution_metrics": execution_metrics,
            "novel_value_per_requested_worker_minute": round(
                novel_float / max(0.001, float(budget_minutes) * worker_count),
                4,
            ),
            "novel_value_per_effective_compute_minute": (
                round(novel_float / effective_compute, 4)
                if effective_compute > 0
                else None
            ),
            "anytime_curve": checkpoints,
        }
    return {
        "schema_version": "loopx_explore_harness_score_v0",
        "budget_minutes_per_arm": float(budget_minutes),
        "worker_count": int(worker_count),
        "coverage_denominator": coverage_denominator,
        "arms": arm_summaries,
    }


def arm_runtime_signature(
    adapter: object,
    *,
    arm_key: str,
    worker_count: int,
    use_router: bool,
    frontier_max_lanes: int,
    variant_catalog_enabled: bool,
    goal_id: str,
    agent_id: str,
    item_failure_policy: str,
    episode_mode: str | None,
) -> dict[str, object]:
    """Build the runtime compatibility signature persisted in arm checkpoints."""

    adapter_type = f"{type(adapter).__module__}.{type(adapter).__qualname__}"
    # Missing keys in legacy manifests read as None, so old standalone arms
    # remain compatible while a recoverable arm cannot resume as standalone.
    return {
        "arm_key": str(arm_key),
        "adapter_type": adapter_type,
        "worker_count": max(1, int(worker_count)),
        "use_router": bool(use_router),
        "frontier_max_lanes": max(0, int(frontier_max_lanes)),
        "variant_catalog_enabled": bool(variant_catalog_enabled),
        "goal_id": str(goal_id),
        "agent_id": str(agent_id),
        "item_failure_policy": item_failure_policy,
        "episode_mode": episode_mode,
    }


def build_arm_runtime_policy(
    *,
    failure_policy: str,
    episode_mode: str | None,
) -> HarnessRuntimePolicy:
    """Build the typed ``runtime_policy`` projection returned by ``run_budget_arm``."""

    return {
        "schema_version": HARNESS_RUNTIME_POLICY_SCHEMA_VERSION,
        "item_failure_policy": failure_policy,
        "item_exception_isolation": failure_policy == ITEM_FAILURE_POLICY_RECORD,
        "episode_execution": {
            "mode": episode_mode or "standalone",
            "adapter_owned_restore": episode_mode is not None,
        },
        "planner_guidance": {
            "retry_backoff": {"enforced": False, "owner": "external_runner"},
            "infra_cooldown": {"enforced": False, "owner": "external_runner"},
        },
    }
