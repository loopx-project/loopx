"""Software-agnostic exploration harness runtime.

This module is the generic half of a two-layer design:

- **This layer (LoopX)** owns scheduling and bookkeeping: independent
  wall-clock budget arms, a shared work queue with concurrency keys, the
  first-seen novelty ledger with adapter-injected flag weights, router-state
  feedback, frontier *scheduling* (when to spend speculative slots and on
  which seed families), anytime checkpoints, and generic metric aggregation.

- **The experiment layer** (driven by a Claude/Codex session) owns every
  domain fact: how to execute a work item, what the observation keys mean,
  how failures are classified, and -- crucially -- WHAT the speculative
  variants are. Variant strategy is authored by the agent as a data file
  (the variant catalog), never generated here.

Hard rules, in the spirit of the wider LoopX design:
- this module never calls an LLM and never imports domain vocabulary;
- variant intelligence travels through files: the harness EMITS
  ``frontier_requests_<arm>.json`` (seed text + seen-observation digest) and
  CONSUMES ``variant_catalog.json`` entries the agent wrote after reading
  those requests -- the same packet-out / decision-in contract the LoopX
  planner uses;
- novelty is only ever credited from observation keys and weighted flags the
  adapter reports; specs carry intent, not novelty.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import time
from typing import cast

from .episode_runtime import (
    RECORD_KIND_STANDALONE,
    build_episode_execution_units,
    combine_execution_metrics,
    execute_episode_group,
    recoverable_episode_mode,
    summarize_execution_records,
)
from .harness_checkpoint import (
    HARNESS_CHECKPOINT_SCHEMA_VERSION,
    build_arm_checkpoint,
    load_arm_checkpoint,
    write_arm_checkpoint,
)
from .harness_frontier import (
    FRONTIER_REQUESTS_SCHEMA_VERSION as FRONTIER_REQUESTS_SCHEMA_VERSION,
    VARIANT_CATALOG_SCHEMA_VERSION as VARIANT_CATALOG_SCHEMA_VERSION,
    NoveltyLedger as NoveltyLedger,
    VariantCatalog as VariantCatalog,
    _frontier_gain as _frontier_gain,
    emit_frontier_requests as emit_frontier_requests,
    initial_arm_router_state,
    plan_round_robin as plan_round_robin,
    plan_router_no_prune as plan_router_no_prune,
    select_frontier_work as select_frontier_work,
    update_router_epoch_feedback,
)
from .harness_queue import (
    DEFAULT_OBSERVATION_WEIGHT as DEFAULT_OBSERVATION_WEIGHT,
    HARNESS_ARM_SCHEMA_VERSION as HARNESS_ARM_SCHEMA_VERSION,
    HARNESS_RUNTIME_POLICY_SCHEMA_VERSION as HARNESS_RUNTIME_POLICY_SCHEMA_VERSION,
    ITEM_FAILURE_POLICIES as ITEM_FAILURE_POLICIES,
    ITEM_FAILURE_POLICY_FATAL as ITEM_FAILURE_POLICY_FATAL,
    ITEM_FAILURE_POLICY_RECORD as ITEM_FAILURE_POLICY_RECORD,
    _MIN_DURATION_MINUTES as _MIN_DURATION_MINUTES,
    HarnessArmResult as HarnessArmResult,
    HarnessArmSummary as HarnessArmSummary,
    HarnessCheckpointPoint as HarnessCheckpointPoint,
    HarnessEpochRecord as HarnessEpochRecord,
    HarnessEpisodePolicy as HarnessEpisodePolicy,
    HarnessLaneRecord as HarnessLaneRecord,
    HarnessResumeSummary as HarnessResumeSummary,
    HarnessRuntimePolicy as HarnessRuntimePolicy,
    HarnessScoreSummary as HarnessScoreSummary,
    ItemFailurePolicy as ItemFailurePolicy,
    SeedAdapter as SeedAdapter,
    _adapter_error_record as _adapter_error_record,
    _as_float,
    _as_int,
    _now_perf_minutes as _now_perf_minutes,
    _normalize_item_failure_policy as _normalize_item_failure_policy,
    _read_json as _read_json,
    _write_json as _write_json,
    aggregate_arms as aggregate_arms,
    arm_runtime_signature as _arm_runtime_signature,
    build_arm_runtime_policy,
    run_queue_epoch as run_queue_epoch,
    step_auc as step_auc,
)

__all__ = [
    "DEFAULT_OBSERVATION_WEIGHT",
    "FRONTIER_REQUESTS_SCHEMA_VERSION",
    "HARNESS_ARM_SCHEMA_VERSION",
    "HARNESS_RUNTIME_POLICY_SCHEMA_VERSION",
    "ITEM_FAILURE_POLICIES",
    "ITEM_FAILURE_POLICY_FATAL",
    "ITEM_FAILURE_POLICY_RECORD",
    "VARIANT_CATALOG_SCHEMA_VERSION",
    "HarnessArmResult",
    "HarnessArmSummary",
    "HarnessCheckpointPoint",
    "HarnessEpochRecord",
    "HarnessEpisodePolicy",
    "HarnessLaneRecord",
    "HarnessResumeSummary",
    "HarnessRuntimePolicy",
    "HarnessScoreSummary",
    "ItemFailurePolicy",
    "NoveltyLedger",
    "SeedAdapter",
    "VariantCatalog",
    "aggregate_arms",
    "emit_frontier_requests",
    "plan_round_robin",
    "plan_router_no_prune",
    "run_budget_arm",
    "run_queue_epoch",
    "select_frontier_work",
    "step_auc",
]


@dataclass
class _ArmExecutionState:
    epochs: list[HarnessEpochRecord]
    checkpoints: list[HarnessCheckpointPoint]
    coverage_first_seen: dict[str, float]
    router_state: dict[str, object] | None
    load_profile: dict[str, object] | None
    raw_cum: float
    novel_cum: float
    variant_records_cum: int
    next_epoch: int
    elapsed_offset: float
    execution_metrics_cum: dict[str, object]


def _seed_historical_execution_metrics(
    epochs: Sequence[HarnessEpochRecord],
    *,
    episode_mode: str | None,
) -> dict[str, object]:
    execution_metric_fields = set(combine_execution_metrics([]))
    historical_execution_metrics: list[dict[str, object]] = []
    for completed_epoch in epochs:
        stored_metrics = completed_epoch.get("execution_metrics")
        if (
            isinstance(stored_metrics, Mapping)
            and execution_metric_fields.issubset(stored_metrics)
        ):
            historical_execution_metrics.append(dict(stored_metrics))
            continue
        historical_records: list[Mapping[str, object]] = [
            record
            for lane in completed_epoch.get("lanes") or ()
            for record in lane.get("results") or ()
        ]
        if episode_mode is None:
            historical_records = [
                {
                    **record,
                    "execution_group_id": None,
                    "record_kind": RECORD_KIND_STANDALONE,
                }
                for record in historical_records
            ]
        historical_execution_metrics.append(
            dict(summarize_execution_records(historical_records))
        )
    return dict(combine_execution_metrics(historical_execution_metrics))


def _restore_arm_state(
    *,
    checkpoint_file: Path,
    checkpoint_enabled: bool,
    resume: bool,
    use_router: bool,
    runtime_signature: Mapping[str, object],
    ledger: NoveltyLedger,
    catalog: VariantCatalog | None,
    episode_mode: str | None,
) -> _ArmExecutionState:
    epochs: list[HarnessEpochRecord] = []
    checkpoints: list[HarnessCheckpointPoint] = []
    coverage_first_seen: dict[str, float] = {}
    router_state = initial_arm_router_state() if use_router else None
    load_profile: dict[str, object] | None = None
    raw_cum = 0.0
    novel_cum = 0.0
    variant_records_cum = 0
    next_epoch = 1
    elapsed_offset = 0.0
    if resume:
        state = load_arm_checkpoint(
            checkpoint_file,
            expected_signature=runtime_signature,
        )
        epochs = [cast(HarnessEpochRecord, dict(epoch)) for epoch in state["epochs"]]
        checkpoints = [
            cast(HarnessCheckpointPoint, dict(point)) for point in state["checkpoints"]
        ]
        ledger.seen = {str(key) for key in state["novelty_seen"]}
        router_state = (
            dict(state["router_state"])
            if isinstance(state.get("router_state"), Mapping)
            else None
        )
        load_profile = (
            dict(state["load_profile"])
            if isinstance(state.get("load_profile"), Mapping)
            else None
        )
        coverage_first_seen = {
            str(family): _as_float(minutes)
            for family, minutes in state["coverage_first_seen"].items()
        }
        raw_cum = _as_float(state.get("raw_value_total") or 0.0)
        novel_cum = _as_float(state.get("novel_value_total") or 0.0)
        variant_records_cum = _as_int(state.get("variant_records_total") or 0)
        next_epoch = _as_int(state["next_epoch"])
        elapsed_offset = _as_float(state.get("elapsed_minutes") or 0.0)
        if catalog is not None:
            catalog.restore_consumed(state["catalog_consumed"])
    elif checkpoint_enabled and catalog is not None:
        # Reusing a run_root is not an implicit restart contract. A fresh arm
        # starts with fresh consumption; only resume restores prior attempts.
        catalog.restore_consumed([])
    if not resume:
        checkpoint_file.unlink(missing_ok=True)
    return _ArmExecutionState(
        epochs=epochs,
        checkpoints=checkpoints,
        coverage_first_seen=coverage_first_seen,
        router_state=router_state,
        load_profile=load_profile,
        raw_cum=raw_cum,
        novel_cum=novel_cum,
        variant_records_cum=variant_records_cum,
        next_epoch=next_epoch,
        elapsed_offset=elapsed_offset,
        execution_metrics_cum=_seed_historical_execution_metrics(
            epochs, episode_mode=episode_mode
        ),
    )


def _epoch_executor(
    adapter: object,
    *,
    episode_mode: str | None,
    failure_policy: str,
) -> Callable[..., object]:
    if episode_mode is not None:

        def _run_episode(item: Mapping[str, object], **context: object) -> list[dict[str, object]]:
            return list(
                execute_episode_group(
                    item,
                    adapter=adapter,
                    failure_policy=failure_policy,
                    fatal_failure_policy=ITEM_FAILURE_POLICY_FATAL,
                    error_record_factory=_adapter_error_record,
                    run_root=cast(Path, context["run_root"]),
                    arm=str(context["arm"]),
                    epoch=cast(int, context["epoch"]),
                    branch_id=str(context["branch_id"]),
                )
            )

        return _run_episode
    # Accessed lazily so an episode-only adapter (no legacy execute) is valid
    # as long as prepare_episode_group never returns None.
    execute_fn = getattr(adapter, "execute", None)
    if not callable(execute_fn):
        raise AttributeError(f"{type(adapter).__name__!r} object has no attribute 'execute'")
    return cast(Callable[..., object], execute_fn)


def _record_epoch_outcomes(
    *,
    state: _ArmExecutionState,
    lanes: list[HarnessLaneRecord],
    seed_items: Sequence[Mapping[str, object]],
    queue_size: int,
    execution_unit_count: int,
    frontier_audit: dict[str, object] | None,
    ledger: NoveltyLedger,
    episode_mode: str | None,
    use_router: bool,
    worker_count: int,
    epoch: int,
    epoch_wall: float,
    started: float,
    run_root: Path,
    arm_key: str,
) -> float:
    epoch_raw = 0.0
    epoch_novel = 0.0
    records: list[dict[str, object]] = []
    for lane in lanes:
        for record in lane.get("results") or ():
            credit = ledger.credit(record)
            record["novel_value"] = credit["novel_value"]
            record["raw_value"] = ledger.raw_value(record)
            epoch_raw += _as_float(record["raw_value"])
            epoch_novel += credit["novel_value"]
            if record.get("is_variant"):
                state.variant_records_cum += 1
            records.append(record)
    state.raw_cum += epoch_raw
    state.novel_cum += epoch_novel
    metric_records: list[Mapping[str, object]] = (
        list(records)
        if episode_mode is not None
        else [
            {
                **record,
                "execution_group_id": None,
                "record_kind": RECORD_KIND_STANDALONE,
            }
            for record in records
        ]
    )
    epoch_execution_metrics = dict(summarize_execution_records(metric_records))
    state.execution_metrics_cum = dict(
        combine_execution_metrics([state.execution_metrics_cum, epoch_execution_metrics])
    )
    elapsed_after = state.elapsed_offset + _now_perf_minutes(started)
    for record in records:
        family = str(record.get("family") or "")
        if family:
            state.coverage_first_seen.setdefault(family, round(elapsed_after, 3))
    if use_router:
        state.router_state, state.load_profile = update_router_epoch_feedback(
            current_router_state=state.router_state,
            records=records,
            seed_items=seed_items,
            lanes=lanes,
            episode_mode=episode_mode,
            epoch=epoch,
            epoch_wall=epoch_wall,
            run_root=run_root,
            arm_key=arm_key,
        )
    state.checkpoints.append(
        {
            "epoch": epoch,
            "elapsed_minutes": round(elapsed_after, 3),
            "raw_value_cum": round(state.raw_cum, 3),
            "novel_value_cum": round(state.novel_cum, 3),
            "coverage_count_cum": len(state.coverage_first_seen),
            "variant_records_cum": state.variant_records_cum,
            "requested_worker_minutes_cum": round(elapsed_after * worker_count, 3),
            "execution_metrics_cum": dict(state.execution_metrics_cum),
        }
    )
    state.epochs.append(
        {
            "schema_version": HARNESS_ARM_SCHEMA_VERSION,
            "epoch": epoch,
            "epoch_wall_minutes": round(epoch_wall, 3),
            "queue_size": queue_size,
            "execution_unit_count": execution_unit_count,
            "execution_metrics": epoch_execution_metrics,
            "frontier": frontier_audit,
            "lanes": lanes,
        }
    )
    return elapsed_after


def run_budget_arm(
    adapter: object,
    *,
    arm_key: str,
    run_root: Path,
    budget_minutes: float,
    worker_count: int,
    max_epochs: int = 1000,
    use_router: bool = False,
    frontier_max_lanes: int = 0,
    variant_catalog_path: Path | None = None,
    goal_id: str = "exploration",
    agent_id: str = "harness",
    stagger_seconds: float = 0.0,
    duration_guard_factor: float = 1.15,
    duration_guard_recent: int = 3,
    resumable: bool = False,
    resume: bool = False,
    checkpoint_path: Path | None = None,
    item_failure_policy: str | None = None,
) -> HarnessArmResult:
    """Run ONE arm alone against its wall-clock budget; return arm payload.

    ``use_router=False`` is the blind baseline (round-robin sweep, no
    variants). ``use_router=True`` adds router-ordered no-prune planning,
    router-state feedback, and -- when a variant catalog path is given --
    agent-authored frontier variants appended to each epoch's queue. Restart
    is opt-in and only trusts the validated epoch-boundary checkpoint manifest;
    rolling progress remains an observability surface.
    """

    run_root = Path(run_root)
    failure_policy = _normalize_item_failure_policy(
        item_failure_policy
        if item_failure_policy is not None
        else getattr(adapter, "item_failure_policy", None)
    )
    episode_mode = recoverable_episode_mode(adapter)
    checkpoint_enabled = bool(resumable or resume or checkpoint_path is not None)
    checkpoint_file = (
        Path(checkpoint_path)
        if checkpoint_path is not None
        else run_root / f"arm_checkpoint_{arm_key}.json"
    )
    runtime_signature = _arm_runtime_signature(
        adapter,
        arm_key=arm_key,
        worker_count=worker_count,
        use_router=use_router,
        frontier_max_lanes=frontier_max_lanes,
        variant_catalog_enabled=variant_catalog_path is not None,
        goal_id=goal_id,
        agent_id=agent_id,
        item_failure_policy=failure_policy,
        episode_mode=episode_mode,
    )
    ledger = NoveltyLedger()
    catalog = (
        VariantCatalog(
            Path(variant_catalog_path),
            run_root / f"variant_consumption_{arm_key}.json",
        )
        if variant_catalog_path
        else None
    )
    state = _restore_arm_state(
        checkpoint_file=checkpoint_file,
        checkpoint_enabled=checkpoint_enabled,
        resume=resume,
        use_router=use_router,
        runtime_signature=runtime_signature,
        ledger=ledger,
        catalog=catalog,
        episode_mode=episode_mode,
    )
    stop_reason = "max_epochs"
    started = time.perf_counter()
    for epoch in range(state.next_epoch, int(max_epochs) + 1):
        elapsed = state.elapsed_offset + _now_perf_minutes(started)
        remaining = float(budget_minutes) - elapsed
        if state.epochs and remaining <= 0:
            stop_reason = "budget_exhausted"
            break
        if state.epochs and duration_guard_factor > 0:
            recent = state.epochs[-max(1, int(duration_guard_recent)) :]
            estimated = max(
                float(entry.get("epoch_wall_minutes") or 0.0) for entry in recent
            ) * float(duration_guard_factor)
            if remaining < estimated:
                stop_reason = "duration_guard"
                break
        seed_items = [dict(item) for item in cast(SeedAdapter, adapter).list_seed_items()]
        if not seed_items:
            stop_reason = "seed_queue_empty"
            break
        if episode_mode is not None and failure_policy == ITEM_FAILURE_POLICY_FATAL:
            # Router planning keys by item_id and variant compilation calls
            # adapter code, so fatal structural validation must see the raw
            # seeds before either operation can collapse ids or cause effects.
            build_episode_execution_units(
                seed_items, arm=arm_key, epoch=epoch, fatal_preflight=True
            )
        queue_items = (
            plan_router_no_prune(
                seed_items,
                goal_id=goal_id,
                agent_id=agent_id,
                worker_count=worker_count,
                router_state=state.router_state,
                load_profile=state.load_profile,
            )
            if use_router
            else plan_round_robin(seed_items)
        )
        frontier_audit: dict[str, object] | None = None
        if use_router and catalog is not None and int(frontier_max_lanes) > 0:
            variant_items, frontier_audit = select_frontier_work(
                seed_items,
                catalog=catalog,
                adapter=adapter,
                router_state=state.router_state,
                epoch=epoch,
                max_lanes=frontier_max_lanes,
            )
            queue_items = queue_items + variant_items
            emit_frontier_requests(
                run_root / f"frontier_requests_{arm_key}.json",
                arm_key=arm_key,
                epoch=epoch,
                seed_items=seed_items,
                ledger=ledger,
                catalog=catalog,
                router_state=state.router_state,
            )
        epoch_start = time.perf_counter()
        execution_items: list[dict[str, object]] = (
            [
                dict(unit)
                for unit in build_episode_execution_units(
                    queue_items,
                    arm=arm_key,
                    epoch=epoch,
                    fatal_preflight=failure_policy == ITEM_FAILURE_POLICY_FATAL,
                )
            ]
            if episode_mode is not None
            else queue_items
        )
        lanes = run_queue_epoch(
            execution_items,
            execute=_epoch_executor(
                adapter, episode_mode=episode_mode, failure_policy=failure_policy
            ),
            worker_count=worker_count,
            run_root=run_root,
            arm=arm_key,
            epoch=epoch,
            stagger_seconds=stagger_seconds,
            item_failure_policy=failure_policy,
        )
        elapsed_after = _record_epoch_outcomes(
            state=state,
            lanes=lanes,
            seed_items=seed_items,
            queue_size=len(queue_items),
            execution_unit_count=len(execution_items),
            frontier_audit=frontier_audit,
            ledger=ledger,
            episode_mode=episode_mode,
            use_router=use_router,
            worker_count=worker_count,
            epoch=epoch,
            epoch_wall=_now_perf_minutes(epoch_start),
            started=started,
            run_root=run_root,
            arm_key=arm_key,
        )
        if checkpoint_enabled:
            write_arm_checkpoint(
                checkpoint_file,
                build_arm_checkpoint(
                    arm_key=arm_key,
                    runtime_signature=runtime_signature,
                    next_epoch=epoch + 1,
                    elapsed_minutes=elapsed_after,
                    epochs=state.epochs,
                    checkpoints=state.checkpoints,
                    novelty_seen=sorted(ledger.seen),
                    router_state=state.router_state,
                    load_profile=state.load_profile,
                    catalog_consumed=sorted(catalog.consumed) if catalog else [],
                    coverage_first_seen=state.coverage_first_seen,
                    raw_value_total=state.raw_cum,
                    novel_value_total=state.novel_cum,
                    variant_records_total=state.variant_records_cum,
                ),
            )
        _write_json(
            run_root / f"rolling_progress_{arm_key}.json",
            {
                "arm": arm_key,
                "completed_epochs": len(state.epochs),
                "last_checkpoint": state.checkpoints[-1],
            },
        )
    return {
        "schema_version": HARNESS_ARM_SCHEMA_VERSION,
        "arm_key": arm_key,
        "budget_minutes": float(budget_minutes),
        "elapsed_minutes": round(state.elapsed_offset + _now_perf_minutes(started), 3),
        "stop_reason": stop_reason,
        "epoch_count": len(state.epochs),
        "raw_value_total": round(state.raw_cum, 3),
        "novel_value_total": round(state.novel_cum, 3),
        "variant_records_total": state.variant_records_cum,
        "execution_metrics": state.execution_metrics_cum,
        "coverage_count": len(state.coverage_first_seen),
        "coverage_first_seen_minutes": state.coverage_first_seen,
        "checkpoints": state.checkpoints,
        "epochs": state.epochs,
        "resume": {
            "schema_version": "loopx_explore_harness_resume_v0",
            "enabled": checkpoint_enabled,
            "resumed": bool(resume),
            "checkpoint_schema_version": HARNESS_CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_path": str(checkpoint_file) if checkpoint_enabled else None,
            "restored_epoch_count": state.next_epoch - 1 if resume else 0,
        },
        "runtime_policy": build_arm_runtime_policy(
            failure_policy=failure_policy, episode_mode=episode_mode
        ),
    }
