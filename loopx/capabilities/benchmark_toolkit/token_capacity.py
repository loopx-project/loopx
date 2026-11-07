from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...domain_state import default_domain_state_file_path
from ...file_lock import exclusive_file_lock
from ...registry import atomic_write_json
from .concurrency_envelope import (
    BENCHMARK_RESOURCE_HEADROOM_RECEIPT_SCHEMA_VERSION,
    _now_iso,
    _reject_unknown_fields,
    _timestamp,
)
from .experiment_identity import experiment_token_text as _token

BENCHMARK_TOKEN_CAPACITY_POLICY_SCHEMA_VERSION = "benchmark_token_capacity_policy_v0"
BENCHMARK_TOKEN_USAGE_SAMPLE_SCHEMA_VERSION = "benchmark_token_usage_sample_v0"
BENCHMARK_TOKEN_CAPACITY_STATE_SCHEMA_VERSION = "benchmark_token_capacity_state_v0"
BENCHMARK_TOKEN_CAPACITY_DECISION_SCHEMA_VERSION = (
    "benchmark_token_capacity_decision_v0"
)
BENCHMARK_TOKEN_CAPACITY_FILENAME = "token-capacity.json"

_WINDOW = timedelta(minutes=1)
_MAX_COOLDOWN_SECONDS = 3600
_MAX_TOKEN_VALUE = 10**15
_METERING_STATES = {"measured", "estimated", "unmetered"}
_WORKLOAD_CLASSES = {"solver", "evaluator"}
_POLICY_FIELDS = {
    "schema_version",
    "quota_tokens_per_minute",
    "target_utilization_basis_points",
    "cold_start_reserve_tokens_per_minute",
    "evaluator_reserve_tokens_per_minute",
    "cooldown_seconds",
}
_SAMPLE_FIELDS = {
    "schema_version",
    "sample_id",
    "observed_at",
    "metering_state",
    "request_count",
    "uncached_input_tokens",
    "cached_input_tokens",
    "output_tokens",
}
_RESERVATION_FIELDS = {
    "run_id",
    "workload_class",
    "tokens_per_minute",
    "reserved_at",
}
_COOLDOWN_FIELDS = {"last_rejected_at", "until"}
_STATE_FIELDS = {
    "schema_version",
    "configured_at",
    "updated_at",
    "policy",
    "samples",
    "reservations",
    "cooldown",
}


def _bounded_token_int(
    value: Any,
    *,
    field: str,
    minimum: int = 0,
    maximum: int = _MAX_TOKEN_VALUE,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{field} must be a {qualifier} integer")
    if value > maximum:
        raise ValueError(f"{field} must not exceed {maximum}")
    return value


def build_benchmark_token_capacity_policy(
    *,
    quota_tokens_per_minute: int,
    target_utilization_basis_points: int = 9000,
    cold_start_reserve_tokens_per_minute: int = 0,
    evaluator_reserve_tokens_per_minute: int = 0,
    cooldown_seconds: int = 60,
) -> dict[str, Any]:
    return normalize_benchmark_token_capacity_policy(
        {
            "schema_version": BENCHMARK_TOKEN_CAPACITY_POLICY_SCHEMA_VERSION,
            "quota_tokens_per_minute": quota_tokens_per_minute,
            "target_utilization_basis_points": target_utilization_basis_points,
            "cold_start_reserve_tokens_per_minute": (
                cold_start_reserve_tokens_per_minute
            ),
            "evaluator_reserve_tokens_per_minute": (
                evaluator_reserve_tokens_per_minute
            ),
            "cooldown_seconds": cooldown_seconds,
        }
    )


def normalize_benchmark_token_capacity_policy(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("benchmark token capacity policy must be an object")
    _reject_unknown_fields(value, allowed=_POLICY_FIELDS, field="policy")
    if value.get("schema_version") != BENCHMARK_TOKEN_CAPACITY_POLICY_SCHEMA_VERSION:
        raise ValueError("benchmark token capacity policy schema mismatch")
    quota = _bounded_token_int(
        value.get("quota_tokens_per_minute"),
        field="quota_tokens_per_minute",
        minimum=1,
    )
    utilization = _bounded_token_int(
        value.get("target_utilization_basis_points"),
        field="target_utilization_basis_points",
        minimum=1,
        maximum=10_000,
    )
    cold_start_reserve = _bounded_token_int(
        value.get("cold_start_reserve_tokens_per_minute"),
        field="cold_start_reserve_tokens_per_minute",
    )
    evaluator_reserve = _bounded_token_int(
        value.get("evaluator_reserve_tokens_per_minute"),
        field="evaluator_reserve_tokens_per_minute",
    )
    target = quota * utilization // 10_000
    if cold_start_reserve + evaluator_reserve > target:
        raise ValueError(
            "cold-start and evaluator reserves cannot exceed target token capacity"
        )
    return {
        "schema_version": BENCHMARK_TOKEN_CAPACITY_POLICY_SCHEMA_VERSION,
        "quota_tokens_per_minute": quota,
        "target_utilization_basis_points": utilization,
        "cold_start_reserve_tokens_per_minute": cold_start_reserve,
        "evaluator_reserve_tokens_per_minute": evaluator_reserve,
        "cooldown_seconds": _bounded_token_int(
            value.get("cooldown_seconds"),
            field="cooldown_seconds",
            minimum=1,
            maximum=_MAX_COOLDOWN_SECONDS,
        ),
    }


def normalize_benchmark_token_usage_sample(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("benchmark token usage sample must be an object")
    _reject_unknown_fields(value, allowed=_SAMPLE_FIELDS, field="sample")
    if value.get("schema_version") != BENCHMARK_TOKEN_USAGE_SAMPLE_SCHEMA_VERSION:
        raise ValueError("benchmark token usage sample schema mismatch")
    metering_state = _token(value.get("metering_state"), field="metering_state")
    if metering_state not in _METERING_STATES:
        raise ValueError("metering_state is unsupported")
    sample = {
        "schema_version": BENCHMARK_TOKEN_USAGE_SAMPLE_SCHEMA_VERSION,
        "sample_id": _token(value.get("sample_id"), field="sample_id"),
        "observed_at": _timestamp(value.get("observed_at"), field="observed_at"),
        "metering_state": metering_state,
        "request_count": _bounded_token_int(
            value.get("request_count"), field="request_count"
        ),
        "uncached_input_tokens": _bounded_token_int(
            value.get("uncached_input_tokens"), field="uncached_input_tokens"
        ),
        "cached_input_tokens": _bounded_token_int(
            value.get("cached_input_tokens"), field="cached_input_tokens"
        ),
        "output_tokens": _bounded_token_int(
            value.get("output_tokens"), field="output_tokens"
        ),
    }
    token_total = sum(
        sample[field]
        for field in (
            "uncached_input_tokens",
            "cached_input_tokens",
            "output_tokens",
        )
    )
    if metering_state == "unmetered":
        if sample["request_count"] == 0:
            raise ValueError("unmetered samples must identify at least one request")
        if token_total != 0:
            raise ValueError("unmetered samples cannot claim token counts")
    elif sample["request_count"] == 0 and token_total != 0:
        raise ValueError("token counts require at least one observed request")
    return sample


def _normalize_reservation(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("benchmark token reservation must be an object")
    _reject_unknown_fields(value, allowed=_RESERVATION_FIELDS, field="reservation")
    workload_class = _token(value.get("workload_class"), field="workload_class")
    if workload_class not in _WORKLOAD_CLASSES:
        raise ValueError("workload_class is unsupported")
    return {
        "run_id": _token(value.get("run_id"), field="run_id"),
        "workload_class": workload_class,
        "tokens_per_minute": _bounded_token_int(
            value.get("tokens_per_minute"),
            field="tokens_per_minute",
            minimum=1,
        ),
        "reserved_at": _timestamp(value.get("reserved_at"), field="reserved_at"),
    }


def _normalize_cooldown(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("cooldown must be an object or null")
    _reject_unknown_fields(value, allowed=_COOLDOWN_FIELDS, field="cooldown")
    last_rejected_at = _timestamp(
        value.get("last_rejected_at"), field="last_rejected_at"
    )
    until = _timestamp(value.get("until"), field="until")
    if datetime.fromisoformat(until) <= datetime.fromisoformat(last_rejected_at):
        raise ValueError("cooldown.until must be later than last_rejected_at")
    return {"last_rejected_at": last_rejected_at, "until": until}


def normalize_benchmark_token_capacity_state(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("benchmark token capacity state must be an object")
    _reject_unknown_fields(value, allowed=_STATE_FIELDS, field="state")
    if value.get("schema_version") != BENCHMARK_TOKEN_CAPACITY_STATE_SCHEMA_VERSION:
        raise ValueError("benchmark token capacity state schema mismatch")
    samples_value = value.get("samples")
    reservations_value = value.get("reservations")
    if not isinstance(samples_value, list):
        raise TypeError("samples must be a list")
    if not isinstance(reservations_value, list):
        raise TypeError("reservations must be a list")
    samples = [normalize_benchmark_token_usage_sample(item) for item in samples_value]
    sample_ids = [item["sample_id"] for item in samples]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("samples cannot contain duplicate sample_id values")
    reservations = [_normalize_reservation(item) for item in reservations_value]
    run_ids = [item["run_id"] for item in reservations]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("reservations cannot contain duplicate run_id values")
    configured_at = _timestamp(value.get("configured_at"), field="configured_at")
    updated_at = _timestamp(value.get("updated_at"), field="updated_at")
    if datetime.fromisoformat(updated_at) < datetime.fromisoformat(configured_at):
        raise ValueError("updated_at cannot be earlier than configured_at")
    return {
        "schema_version": BENCHMARK_TOKEN_CAPACITY_STATE_SCHEMA_VERSION,
        "configured_at": configured_at,
        "updated_at": updated_at,
        "policy": normalize_benchmark_token_capacity_policy(value.get("policy")),
        "samples": sorted(
            samples, key=lambda item: (item["observed_at"], item["sample_id"])
        ),
        "reservations": sorted(reservations, key=lambda item: item["run_id"]),
        "cooldown": _normalize_cooldown(value.get("cooldown")),
    }


def default_benchmark_token_capacity_path(
    *, project: str | Path = ".", goal_id: str
) -> Path:
    return default_domain_state_file_path(
        project=project,
        goal_id=goal_id,
        domain_pack="benchmark_toolkit",
        filename=BENCHMARK_TOKEN_CAPACITY_FILENAME,
    )


def read_benchmark_token_capacity_state(path: str | Path) -> dict[str, Any] | None:
    target = Path(path).expanduser()
    if not target.exists():
        return None
    return normalize_benchmark_token_capacity_state(
        json.loads(target.read_text(encoding="utf-8"))
    )


def _target_tokens(policy: Mapping[str, Any]) -> int:
    return (
        policy["quota_tokens_per_minute"]
        * policy["target_utilization_basis_points"]
        // 10_000
    )


def _window_samples(
    samples: list[dict[str, Any]], *, observed_at: str
) -> tuple[list[dict[str, Any]], bool]:
    now = datetime.fromisoformat(observed_at)
    lower = now - _WINDOW
    future = any(datetime.fromisoformat(item["observed_at"]) > now for item in samples)
    return (
        [
            item
            for item in samples
            if lower < datetime.fromisoformat(item["observed_at"]) <= now
        ],
        future,
    )


def _usage_projection(
    samples: list[dict[str, Any]], *, observed_at: str
) -> dict[str, Any]:
    window, future = _window_samples(samples, observed_at=observed_at)
    states = {item["metering_state"] for item in window}
    if future:
        coverage = "unresolved_future_sample"
        reason_codes = ["token_usage_sample_from_future"]
    elif not window:
        coverage = "unobserved"
        reason_codes = ["token_usage_unobserved"]
    elif "unmetered" in states:
        coverage = "partial_unmetered"
        reason_codes = ["token_usage_partial_unmetered"]
    elif "estimated" in states:
        coverage = "complete_estimated"
        reason_codes = []
    else:
        coverage = "complete_measured"
        reason_codes = []
    totals = {
        field: sum(item[field] for item in window)
        for field in (
            "request_count",
            "uncached_input_tokens",
            "cached_input_tokens",
            "output_tokens",
        )
    }
    totals["tokens"] = (
        totals["uncached_input_tokens"]
        + totals["cached_input_tokens"]
        + totals["output_tokens"]
    )
    return {
        "window_seconds": int(_WINDOW.total_seconds()),
        "window_started_at": (datetime.fromisoformat(observed_at) - _WINDOW)
        .isoformat()
        .replace("+00:00", "Z"),
        "observed_at": observed_at,
        "coverage": coverage,
        "reason_codes": reason_codes,
        "sample_count": len(window),
        **totals,
    }


def _reservation_totals(reservations: list[dict[str, Any]]) -> dict[str, int]:
    solver = sum(
        item["tokens_per_minute"]
        for item in reservations
        if item["workload_class"] == "solver"
    )
    evaluator = sum(
        item["tokens_per_minute"]
        for item in reservations
        if item["workload_class"] == "evaluator"
    )
    return {"total": solver + evaluator, "solver": solver, "evaluator": evaluator}


def build_benchmark_token_capacity_status(
    state: Mapping[str, Any] | None,
    *,
    observed_at: str | None = None,
) -> dict[str, Any]:
    timestamp = _timestamp(observed_at or _now_iso(), field="observed_at")
    if state is None:
        return {
            "ok": True,
            "schema_version": BENCHMARK_TOKEN_CAPACITY_STATE_SCHEMA_VERSION,
            "configured": False,
            "observed_at": timestamp,
            "next_action": "configure_token_capacity",
            "path_recorded": False,
        }
    normalized = normalize_benchmark_token_capacity_state(state)
    policy = normalized["policy"]
    usage = _usage_projection(normalized["samples"], observed_at=timestamp)
    reservations = _reservation_totals(normalized["reservations"])
    target = _target_tokens(policy)
    solver_limit = (
        target
        - policy["cold_start_reserve_tokens_per_minute"]
        - policy["evaluator_reserve_tokens_per_minute"]
    )
    evaluator_limit = target - policy["cold_start_reserve_tokens_per_minute"]
    cooldown = normalized["cooldown"]
    cooldown_active = bool(
        cooldown
        and datetime.fromisoformat(timestamp)
        < datetime.fromisoformat(cooldown["until"])
    )
    global_basis = max(usage["tokens"], reservations["total"])
    overcommitted = (
        global_basis > evaluator_limit or reservations["solver"] > solver_limit
    )
    return {
        "ok": True,
        "schema_version": BENCHMARK_TOKEN_CAPACITY_STATE_SCHEMA_VERSION,
        "configured": True,
        "configured_at": normalized["configured_at"],
        "updated_at": normalized["updated_at"],
        "observed_at": timestamp,
        "policy": policy,
        "limits": {
            "target_tokens_per_minute": target,
            "solver_tokens_per_minute": solver_limit,
            "evaluator_tokens_per_minute": evaluator_limit,
        },
        "usage": usage,
        "reservations": {
            **reservations,
            "count": len(normalized["reservations"]),
            "active": normalized["reservations"],
        },
        "capacity_basis_tokens_per_minute": global_basis,
        "overcommitted": overcommitted,
        "admission_ready": (
            not usage["reason_codes"] and not cooldown_active and not overcommitted
        ),
        "cooldown": {
            "active": cooldown_active,
            "until": cooldown["until"] if cooldown_active and cooldown else None,
        },
        "next_action": (
            "wait_for_metering"
            if usage["reason_codes"]
            else "wait_for_provider_cooldown"
            if cooldown_active
            else "release_or_reduce_token_load"
            if overcommitted
            else "evaluate_token_admission"
        ),
        "path_recorded": False,
    }


def configure_benchmark_token_capacity(
    path: str | Path,
    policy: Mapping[str, Any],
    *,
    execute: bool = False,
    agent_id: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser()
    compact_policy = normalize_benchmark_token_capacity_policy(policy)

    def build(
        current: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # Executed builds sample implicit time only after acquiring the state lock.
        timestamp = _timestamp(observed_at or _now_iso(), field="observed_at")
        if current is None:
            configured_at = timestamp
            samples: list[dict[str, Any]] = []
            reservations: list[dict[str, Any]] = []
            cooldown = None
        else:
            normalized = normalize_benchmark_token_capacity_state(current)
            if datetime.fromisoformat(timestamp) < datetime.fromisoformat(
                normalized["updated_at"]
            ):
                raise ValueError("configuration time cannot move state backward")
            configured_at = normalized["configured_at"]
            samples = normalized["samples"]
            reservations = normalized["reservations"]
            cooldown = normalized["cooldown"]
        next_state = normalize_benchmark_token_capacity_state(
            {
                "schema_version": BENCHMARK_TOKEN_CAPACITY_STATE_SCHEMA_VERSION,
                "configured_at": configured_at,
                "updated_at": timestamp,
                "policy": compact_policy,
                "samples": samples,
                "reservations": reservations,
                "cooldown": cooldown,
            }
        )
        return (
            {
                **build_benchmark_token_capacity_status(
                    next_state, observed_at=timestamp
                ),
                "dry_run": not execute,
                "write_performed": False,
            },
            next_state,
        )

    if not execute:
        result, _ = build(read_benchmark_token_capacity_state(target))
        return result
    with exclusive_file_lock(
        target, agent_id=agent_id, operation="benchmark_token_capacity_configure"
    ):
        result, next_state = build(read_benchmark_token_capacity_state(target))
        atomic_write_json(target, next_state)
        result["write_performed"] = True
        return result


def observe_benchmark_token_usage(
    path: str | Path,
    sample: Mapping[str, Any],
    *,
    execute: bool = False,
    agent_id: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser()
    compact_sample = normalize_benchmark_token_usage_sample(sample)

    def build(
        current: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        timestamp = _timestamp(recorded_at or _now_iso(), field="recorded_at")
        if current is None:
            return (
                {
                    "ok": False,
                    "reason_codes": ["token_capacity_not_configured"],
                    "dry_run": not execute,
                    "write_performed": False,
                },
                None,
            )
        normalized = normalize_benchmark_token_capacity_state(current)
        existing = next(
            (
                item
                for item in normalized["samples"]
                if item["sample_id"] == compact_sample["sample_id"]
            ),
            None,
        )
        if existing is not None and existing != compact_sample:
            raise ValueError("sample_id is already recorded with different usage")
        if existing is not None:
            readback_at = (
                max(
                    datetime.fromisoformat(timestamp),
                    datetime.fromisoformat(normalized["updated_at"]),
                )
                .isoformat()
                .replace("+00:00", "Z")
            )
            status = build_benchmark_token_capacity_status(
                normalized, observed_at=readback_at
            )
            return (
                {
                    **status,
                    "recorded": True,
                    "idempotent": True,
                    "dry_run": not execute,
                    "write_performed": False,
                },
                None,
            )
        latest = max(
            datetime.fromisoformat(normalized["updated_at"]),
            datetime.fromisoformat(timestamp),
        )
        latest_at = latest.isoformat().replace("+00:00", "Z")
        lower = latest - _WINDOW
        samples = [
            item
            for item in [*normalized["samples"], compact_sample]
            if datetime.fromisoformat(item["observed_at"]) > lower
        ]
        next_state = normalize_benchmark_token_capacity_state(
            {
                **normalized,
                "updated_at": latest_at,
                "samples": samples,
            }
        )
        return (
            {
                **build_benchmark_token_capacity_status(
                    next_state, observed_at=latest_at
                ),
                "recorded": True,
                "sample": compact_sample,
                "dry_run": not execute,
                "write_performed": False,
            },
            next_state,
        )

    if not execute:
        result, _ = build(read_benchmark_token_capacity_state(target))
        return result
    with exclusive_file_lock(
        target, agent_id=agent_id, operation="benchmark_token_usage_observe"
    ):
        result, next_state = build(read_benchmark_token_capacity_state(target))
        if next_state is not None:
            atomic_write_json(target, next_state)
            result["write_performed"] = True
        return result


def record_benchmark_token_capacity_rejection(
    path: str | Path,
    *,
    retry_after_seconds: int | None = None,
    execute: bool = False,
    agent_id: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser()
    if retry_after_seconds is not None:
        retry_after_seconds = _bounded_token_int(
            retry_after_seconds,
            field="retry_after_seconds",
            minimum=1,
            maximum=_MAX_COOLDOWN_SECONDS,
        )

    def build(
        current: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        timestamp = _timestamp(observed_at or _now_iso(), field="observed_at")
        if current is None:
            return (
                {
                    "ok": False,
                    "reason_codes": ["token_capacity_not_configured"],
                    "dry_run": not execute,
                    "write_performed": False,
                },
                None,
            )
        normalized = normalize_benchmark_token_capacity_state(current)
        if datetime.fromisoformat(timestamp) < datetime.fromisoformat(
            normalized["updated_at"]
        ):
            raise ValueError("rejection time cannot move state backward")
        seconds = max(
            normalized["policy"]["cooldown_seconds"], retry_after_seconds or 0
        )
        until = (
            (datetime.fromisoformat(timestamp) + timedelta(seconds=seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        existing = normalized["cooldown"]
        if existing and datetime.fromisoformat(
            existing["until"]
        ) > datetime.fromisoformat(until):
            until = existing["until"]
        next_state = normalize_benchmark_token_capacity_state(
            {
                **normalized,
                "updated_at": timestamp,
                "cooldown": {"last_rejected_at": timestamp, "until": until},
            }
        )
        return (
            {
                **build_benchmark_token_capacity_status(
                    next_state, observed_at=timestamp
                ),
                "recorded": True,
                "dry_run": not execute,
                "write_performed": False,
            },
            next_state,
        )

    if not execute:
        result, _ = build(read_benchmark_token_capacity_state(target))
        return result
    with exclusive_file_lock(
        target, agent_id=agent_id, operation="benchmark_token_capacity_reject"
    ):
        result, next_state = build(read_benchmark_token_capacity_state(target))
        if next_state is not None:
            atomic_write_json(target, next_state)
            result["write_performed"] = True
        return result


def _headroom_receipt(*, observed_at: str, state: str) -> dict[str, Any]:
    expires_at = (
        (datetime.fromisoformat(observed_at) + timedelta(minutes=1))
        .isoformat()
        .replace("+00:00", "Z")
    )
    return {
        "schema_version": BENCHMARK_RESOURCE_HEADROOM_RECEIPT_SCHEMA_VERSION,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "checks": [{"kind": "provider_capacity", "state": state}],
    }


def _token_admission(
    state: Mapping[str, Any] | None,
    *,
    run_id: str,
    workload_class: str,
    tokens_per_minute: int,
    admitted_at: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    compact_run_id = _token(run_id, field="run_id")
    compact_workload_class = _token(workload_class, field="workload_class")
    if compact_workload_class not in _WORKLOAD_CLASSES:
        raise ValueError("workload_class is unsupported")
    compact_tpm = _bounded_token_int(
        tokens_per_minute, field="tokens_per_minute", minimum=1
    )
    if state is None:
        return (
            {
                "ok": False,
                "admitted": False,
                "reason_codes": ["token_capacity_not_configured"],
                "write_performed": False,
            },
            None,
        )
    normalized = normalize_benchmark_token_capacity_state(state)
    if datetime.fromisoformat(admitted_at) < datetime.fromisoformat(
        normalized["updated_at"]
    ):
        raise ValueError("admission time cannot move state backward")
    existing = next(
        (
            item
            for item in normalized["reservations"]
            if item["run_id"] == compact_run_id
        ),
        None,
    )
    if existing is not None and (
        existing["workload_class"] != compact_workload_class
        or existing["tokens_per_minute"] != compact_tpm
    ):
        raise ValueError("run_id is already reserved with different token capacity")
    status = build_benchmark_token_capacity_status(normalized, observed_at=admitted_at)
    reasons = list(status["usage"]["reason_codes"])
    if status["cooldown"]["active"]:
        reasons.append("provider_capacity_cooldown")
    reservations = normalized["reservations"]
    current = _reservation_totals(reservations)
    projected_reservations = reservations
    if existing is None:
        projected_reservations = [
            *reservations,
            {
                "run_id": compact_run_id,
                "workload_class": compact_workload_class,
                "tokens_per_minute": compact_tpm,
                "reserved_at": admitted_at,
            },
        ]
    projected = _reservation_totals(projected_reservations)
    global_limit = status["limits"]["evaluator_tokens_per_minute"]
    added_tpm = 0 if existing is not None else compact_tpm
    global_basis = max(status["usage"]["tokens"], current["total"]) + added_tpm
    if compact_workload_class == "evaluator":
        capacity_basis = global_basis
        limit = global_limit
    else:
        solver_added_tpm = 0 if existing is not None else compact_tpm
        capacity_basis = (
            max(status["usage"]["tokens"], current["solver"]) + solver_added_tpm
        )
        limit = status["limits"]["solver_tokens_per_minute"]
    if global_basis > global_limit or capacity_basis > limit:
        reasons.append("token_capacity_exhausted")
    reasons = sorted(set(reasons))
    headroom_state = (
        "unresolved"
        if any(
            reason
            in {
                "token_usage_unobserved",
                "token_usage_partial_unmetered",
                "token_usage_sample_from_future",
            }
            for reason in reasons
        )
        else "insufficient"
        if reasons
        else "sufficient"
    )
    decision = {
        "ok": not reasons,
        "schema_version": BENCHMARK_TOKEN_CAPACITY_DECISION_SCHEMA_VERSION,
        "admitted": not reasons,
        "idempotent": existing is not None,
        "reason_codes": reasons,
        "run": {
            "run_id": compact_run_id,
            "workload_class": compact_workload_class,
            "tokens_per_minute": compact_tpm,
        },
        "observed_at": admitted_at,
        "coverage": status["usage"]["coverage"],
        "rolling_usage_tokens_per_minute": status["usage"]["tokens"],
        "projected_reserved_tokens_per_minute": projected["total"],
        "projected_solver_tokens_per_minute": projected["solver"],
        "projected_evaluator_tokens_per_minute": projected["evaluator"],
        "capacity_basis_tokens_per_minute": capacity_basis,
        "admission_limit_tokens_per_minute": limit,
        "resource_headroom_receipt": _headroom_receipt(
            observed_at=admitted_at, state=headroom_state
        ),
        "write_performed": False,
    }
    if reasons or existing is not None:
        return decision, None
    next_state = normalize_benchmark_token_capacity_state(
        {
            **normalized,
            "updated_at": admitted_at,
            "reservations": projected_reservations,
        }
    )
    return decision, next_state


def admit_benchmark_token_capacity(
    path: str | Path,
    *,
    run_id: str,
    workload_class: str,
    tokens_per_minute: int,
    execute: bool = False,
    agent_id: str | None = None,
    admitted_at: str | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser()

    def build(
        current: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        timestamp = _timestamp(admitted_at or _now_iso(), field="admitted_at")
        decision, next_state = _token_admission(
            current,
            run_id=run_id,
            workload_class=workload_class,
            tokens_per_minute=tokens_per_minute,
            admitted_at=timestamp,
        )
        decision["dry_run"] = not execute
        return decision, next_state

    if not execute:
        result, _ = build(read_benchmark_token_capacity_state(target))
        return result
    with exclusive_file_lock(
        target, agent_id=agent_id, operation="benchmark_token_capacity_admit"
    ):
        result, next_state = build(read_benchmark_token_capacity_state(target))
        if next_state is not None:
            atomic_write_json(target, next_state)
            result["write_performed"] = True
        return result


def release_benchmark_token_capacity(
    path: str | Path,
    *,
    run_id: str,
    execute: bool = False,
    agent_id: str | None = None,
    released_at: str | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser()
    compact_run_id = _token(run_id, field="run_id")

    def build(
        current: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        timestamp = _timestamp(released_at or _now_iso(), field="released_at")
        if current is None:
            return (
                {
                    "ok": False,
                    "released": False,
                    "reason_codes": ["token_capacity_not_configured"],
                    "dry_run": not execute,
                    "write_performed": False,
                },
                None,
            )
        normalized = normalize_benchmark_token_capacity_state(current)
        if datetime.fromisoformat(timestamp) < datetime.fromisoformat(
            normalized["updated_at"]
        ):
            raise ValueError("release time cannot move state backward")
        retained = [
            item
            for item in normalized["reservations"]
            if item["run_id"] != compact_run_id
        ]
        if len(retained) == len(normalized["reservations"]):
            return (
                {
                    "ok": True,
                    "released": True,
                    "idempotent": True,
                    "reason_codes": [],
                    "dry_run": not execute,
                    "write_performed": False,
                },
                None,
            )
        next_state = normalize_benchmark_token_capacity_state(
            {
                **normalized,
                "updated_at": timestamp,
                "reservations": retained,
            }
        )
        return (
            {
                "ok": True,
                "released": True,
                "idempotent": False,
                "reason_codes": [],
                "status": build_benchmark_token_capacity_status(
                    next_state, observed_at=timestamp
                ),
                "dry_run": not execute,
                "write_performed": False,
            },
            next_state,
        )

    if not execute:
        result, _ = build(read_benchmark_token_capacity_state(target))
        return result
    with exclusive_file_lock(
        target, agent_id=agent_id, operation="benchmark_token_capacity_release"
    ):
        result, next_state = build(read_benchmark_token_capacity_state(target))
        if next_state is not None:
            atomic_write_json(target, next_state)
            result["write_performed"] = True
        return result


def render_benchmark_token_capacity_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Benchmark token capacity", ""]
    for field in (
        "configured",
        "admitted",
        "released",
        "recorded",
        "coverage",
        "capacity_basis_tokens_per_minute",
        "admission_limit_tokens_per_minute",
        "next_action",
        "write_performed",
    ):
        if field in payload:
            lines.append(f"- {field}: {payload[field]}")
    if payload.get("reason_codes"):
        lines.append(f"- reason_codes: {', '.join(payload['reason_codes'])}")
    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        lines.append(f"- coverage: {usage.get('coverage')}")
        lines.append(f"- rolling_tokens_per_minute: {usage.get('tokens')}")
    return "\n".join(lines) + "\n"


__all__ = [
    "BENCHMARK_TOKEN_CAPACITY_DECISION_SCHEMA_VERSION",
    "BENCHMARK_TOKEN_CAPACITY_FILENAME",
    "BENCHMARK_TOKEN_CAPACITY_POLICY_SCHEMA_VERSION",
    "BENCHMARK_TOKEN_CAPACITY_STATE_SCHEMA_VERSION",
    "BENCHMARK_TOKEN_USAGE_SAMPLE_SCHEMA_VERSION",
    "admit_benchmark_token_capacity",
    "build_benchmark_token_capacity_policy",
    "build_benchmark_token_capacity_status",
    "configure_benchmark_token_capacity",
    "default_benchmark_token_capacity_path",
    "normalize_benchmark_token_capacity_policy",
    "normalize_benchmark_token_capacity_state",
    "normalize_benchmark_token_usage_sample",
    "observe_benchmark_token_usage",
    "read_benchmark_token_capacity_state",
    "record_benchmark_token_capacity_rejection",
    "release_benchmark_token_capacity",
    "render_benchmark_token_capacity_markdown",
]
