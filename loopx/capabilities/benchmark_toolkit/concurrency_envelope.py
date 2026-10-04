from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .experiment_identity import (
    ARM_ROLES,
    experiment_token_text as _token,
)
from ...domain_state import default_domain_state_file_path
from ...file_lock import exclusive_file_lock
from ...registry import atomic_write_json

BENCHMARK_CONCURRENCY_ENVELOPE_SCHEMA_VERSION = "benchmark_concurrency_envelope_v0"
BENCHMARK_RESOURCE_HEADROOM_RECEIPT_SCHEMA_VERSION = (
    "benchmark_resource_headroom_receipt_v0"
)
BENCHMARK_CONCURRENCY_ENVELOPE_FILENAME = "concurrency-envelope.json"

_RESOURCE_HEADROOM_KINDS = {
    "file_descriptors",
    "memory",
    "persistent_storage",
    "process_capacity",
    "provider_capacity",
    "temporary_storage",
}
_RESOURCE_HEADROOM_STATES = {"sufficient", "insufficient", "unresolved"}
_MAX_RESOURCE_HEADROOM_VALIDITY = timedelta(minutes=15)
_STATE_FIELDS = {
    "schema_version",
    "configured_at",
    "updated_at",
    "config",
    "active_runs",
}
_CONFIG_FIELDS = {
    "max_active_cases",
    "target_active_cases",
    "max_baseline_cases",
    "max_test_cases",
    "reserved_test_cases",
    "require_resource_headroom_receipt",
}
_ACTIVE_RUN_FIELDS = {"run_id", "case_id", "arm_role", "admitted_at"}
_RESOURCE_HEADROOM_RECEIPT_FIELDS = {
    "schema_version",
    "observed_at",
    "expires_at",
    "checks",
}
_RESOURCE_HEADROOM_CHECK_FIELDS = {"kind", "state"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.isoformat().replace("+00:00", "Z")


def _bounded_int(value: Any, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{field} must be a {qualifier} integer")
    if value > 1024:
        raise ValueError(f"{field} must not exceed 1024")
    return value


def _reject_unknown_fields(
    value: Mapping[str, Any], *, allowed: set[str], field: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field} contains unsupported fields: {', '.join(unknown)}")


def _arm_group(arm_role: str) -> str:
    return "baseline" if arm_role == "baseline" else "test"


def normalize_benchmark_concurrency_config(
    value: Mapping[str, Any],
) -> dict[str, int | bool]:
    if not isinstance(value, Mapping):
        raise TypeError("benchmark concurrency config must be an object")
    _reject_unknown_fields(value, allowed=_CONFIG_FIELDS, field="config")
    total = _bounded_int(
        value.get("max_active_cases"), field="max_active_cases", minimum=1
    )
    target_value = value.get("target_active_cases", total)
    config = {
        "max_active_cases": total,
        "target_active_cases": _bounded_int(
            target_value, field="target_active_cases", minimum=1
        ),
        "max_baseline_cases": _bounded_int(
            value.get("max_baseline_cases"), field="max_baseline_cases"
        ),
        "max_test_cases": _bounded_int(
            value.get("max_test_cases"), field="max_test_cases"
        ),
        "reserved_test_cases": _bounded_int(
            value.get("reserved_test_cases"), field="reserved_test_cases"
        ),
        "require_resource_headroom_receipt": value.get(
            "require_resource_headroom_receipt", False
        ),
    }
    if not isinstance(config["require_resource_headroom_receipt"], bool):
        raise TypeError("require_resource_headroom_receipt must be boolean")
    if config["target_active_cases"] > total:
        raise ValueError("target_active_cases cannot exceed max_active_cases")
    if config["max_baseline_cases"] > total:
        raise ValueError("max_baseline_cases cannot exceed max_active_cases")
    if config["max_test_cases"] > total:
        raise ValueError("max_test_cases cannot exceed max_active_cases")
    if config["reserved_test_cases"] > config["max_test_cases"]:
        raise ValueError("reserved_test_cases cannot exceed max_test_cases")
    if config["reserved_test_cases"] > total:
        raise ValueError("reserved_test_cases cannot exceed max_active_cases")
    if (
        config["target_active_cases"]
        > config["max_baseline_cases"] + config["max_test_cases"]
    ):
        raise ValueError(
            "target_active_cases cannot exceed combined baseline and test capacity"
        )
    return config


def build_benchmark_concurrency_config(
    *,
    max_active_cases: int,
    target_active_cases: int | None = None,
    max_baseline_cases: int | None = None,
    max_test_cases: int | None = None,
    reserved_test_cases: int = 0,
    require_resource_headroom_receipt: bool = False,
) -> dict[str, int | bool]:
    total = _bounded_int(max_active_cases, field="max_active_cases", minimum=1)
    reserved = _bounded_int(reserved_test_cases, field="reserved_test_cases")
    return normalize_benchmark_concurrency_config(
        {
            "max_active_cases": total,
            "target_active_cases": (
                total if target_active_cases is None else target_active_cases
            ),
            "max_baseline_cases": (
                total - reserved if max_baseline_cases is None else max_baseline_cases
            ),
            "max_test_cases": total if max_test_cases is None else max_test_cases,
            "reserved_test_cases": reserved,
            "require_resource_headroom_receipt": require_resource_headroom_receipt,
        }
    )


def normalize_benchmark_resource_headroom_receipt(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a public-safe provider observation without persisting raw metrics."""

    if not isinstance(value, Mapping):
        raise TypeError("benchmark resource headroom receipt must be an object")
    _reject_unknown_fields(
        value,
        allowed=_RESOURCE_HEADROOM_RECEIPT_FIELDS,
        field="resource_headroom_receipt",
    )
    if (
        value.get("schema_version")
        != BENCHMARK_RESOURCE_HEADROOM_RECEIPT_SCHEMA_VERSION
    ):
        raise ValueError("benchmark resource headroom receipt schema mismatch")
    observed_at = _timestamp(value.get("observed_at"), field="observed_at")
    expires_at = _timestamp(value.get("expires_at"), field="expires_at")
    observed_time = datetime.fromisoformat(observed_at)
    expires_time = datetime.fromisoformat(expires_at)
    if expires_time <= observed_time:
        raise ValueError("expires_at must be later than observed_at")
    if expires_time - observed_time > _MAX_RESOURCE_HEADROOM_VALIDITY:
        raise ValueError(
            "resource headroom receipt validity must not exceed 15 minutes"
        )
    raw_checks = value.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise ValueError("resource headroom receipt requires at least one check")
    checks: list[dict[str, str]] = []
    for raw_check in raw_checks:
        if not isinstance(raw_check, Mapping):
            raise TypeError("resource headroom check must be an object")
        _reject_unknown_fields(
            raw_check,
            allowed=_RESOURCE_HEADROOM_CHECK_FIELDS,
            field="resource_headroom_check",
        )
        kind = _token(raw_check.get("kind"), field="resource_headroom_kind")
        state = _token(raw_check.get("state"), field="resource_headroom_state")
        if kind not in _RESOURCE_HEADROOM_KINDS:
            raise ValueError("resource headroom kind is unsupported")
        if state not in _RESOURCE_HEADROOM_STATES:
            raise ValueError("resource headroom state is unsupported")
        checks.append({"kind": kind, "state": state})
    kinds = [item["kind"] for item in checks]
    if len(kinds) != len(set(kinds)):
        raise ValueError("resource headroom checks cannot repeat a kind")
    return {
        "schema_version": BENCHMARK_RESOURCE_HEADROOM_RECEIPT_SCHEMA_VERSION,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "checks": sorted(checks, key=lambda item: item["kind"]),
    }


def _resource_headroom_admission(
    receipt: Mapping[str, Any] | None,
    *,
    required: bool,
    admitted_at: str,
) -> dict[str, Any]:
    if receipt is None:
        return {
            "qualified": not required,
            "required": required,
            "reason_codes": ["resource_headroom_receipt_required"] if required else [],
            "check_count": 0,
            "check_kinds": [],
            "receipt_persisted": False,
        }
    normalized = normalize_benchmark_resource_headroom_receipt(receipt)
    reasons: list[str] = []
    admission_time = datetime.fromisoformat(admitted_at)
    observed_time = datetime.fromisoformat(normalized["observed_at"])
    expires_time = datetime.fromisoformat(normalized["expires_at"])
    if observed_time > admission_time:
        reasons.append("resource_headroom_receipt_from_future")
    if admission_time >= expires_time:
        reasons.append("resource_headroom_receipt_expired")
    for check in normalized["checks"]:
        if check["state"] == "insufficient":
            reasons.append(f"{check['kind']}_insufficient")
        elif check["state"] == "unresolved":
            reasons.append(f"{check['kind']}_unresolved")
    return {
        "qualified": not reasons,
        "required": required,
        "reason_codes": reasons,
        "check_count": len(normalized["checks"]),
        "check_kinds": [item["kind"] for item in normalized["checks"]],
        "receipt_persisted": False,
    }


def _normalize_active_run(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("active run must be an object")
    _reject_unknown_fields(value, allowed=_ACTIVE_RUN_FIELDS, field="active_run")
    arm_role = _token(value.get("arm_role"), field="arm_role")
    if arm_role not in ARM_ROLES:
        raise ValueError("arm_role is unsupported")
    return {
        "run_id": _token(value.get("run_id"), field="run_id"),
        "case_id": _token(value.get("case_id"), field="case_id"),
        "arm_role": arm_role,
        "admitted_at": _timestamp(value.get("admitted_at"), field="admitted_at"),
    }


def normalize_benchmark_concurrency_envelope(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("benchmark concurrency envelope must be an object")
    _reject_unknown_fields(value, allowed=_STATE_FIELDS, field="envelope")
    if value.get("schema_version") != BENCHMARK_CONCURRENCY_ENVELOPE_SCHEMA_VERSION:
        raise ValueError("benchmark concurrency envelope schema mismatch")
    active_value = value.get("active_runs")
    if not isinstance(active_value, list):
        raise TypeError("active_runs must be a list")
    active_runs = [_normalize_active_run(item) for item in active_value]
    run_ids = [item["run_id"] for item in active_runs]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("active_runs cannot contain duplicate run_id values")
    return {
        "schema_version": BENCHMARK_CONCURRENCY_ENVELOPE_SCHEMA_VERSION,
        "configured_at": _timestamp(value.get("configured_at"), field="configured_at"),
        "updated_at": _timestamp(value.get("updated_at"), field="updated_at"),
        "config": normalize_benchmark_concurrency_config(value.get("config")),
        "active_runs": sorted(active_runs, key=lambda item: item["run_id"]),
    }


def default_benchmark_concurrency_envelope_path(
    *, project: str | Path = ".", goal_id: str
) -> Path:
    return default_domain_state_file_path(
        project=project,
        goal_id=goal_id,
        domain_pack="benchmark_toolkit",
        filename=BENCHMARK_CONCURRENCY_ENVELOPE_FILENAME,
    )


def read_benchmark_concurrency_envelope(path: str | Path) -> dict[str, Any] | None:
    target = Path(path).expanduser()
    if not target.exists():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    return normalize_benchmark_concurrency_envelope(payload)


def _counts(active_runs: list[dict[str, str]]) -> dict[str, int]:
    baseline = sum(
        1 for item in active_runs if _arm_group(item["arm_role"]) == "baseline"
    )
    test = len(active_runs) - baseline
    return {"total": len(active_runs), "baseline": baseline, "test": test}


def _capacity(config: Mapping[str, Any], counts: Mapping[str, int]) -> dict[str, int]:
    return {
        "total": max(0, config["target_active_cases"] - counts["total"]),
        "baseline": max(0, config["max_baseline_cases"] - counts["baseline"]),
        "test": max(0, config["max_test_cases"] - counts["test"]),
    }


def build_benchmark_concurrency_status(
    envelope: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if envelope is None:
        return {
            "ok": True,
            "schema_version": BENCHMARK_CONCURRENCY_ENVELOPE_SCHEMA_VERSION,
            "configured": False,
            "active_counts": {"total": 0, "baseline": 0, "test": 0},
            "active_runs": [],
            "next_action": "configure_concurrency_envelope",
            "path_recorded": False,
        }
    normalized = normalize_benchmark_concurrency_envelope(envelope)
    counts = _counts(normalized["active_runs"])
    config = normalized["config"]
    overcommitted = (
        counts["total"] > config["max_active_cases"]
        or counts["baseline"] > config["max_baseline_cases"]
        or counts["test"] > config["max_test_cases"]
    )
    target = config["target_active_cases"]
    target_gap = max(0, target - counts["total"])
    target_excess = max(0, counts["total"] - target)
    underfilled = not overcommitted and target_gap > 0
    if (
        counts["test"] < config["reserved_test_cases"]
        or counts["baseline"] >= config["max_baseline_cases"]
    ):
        preferred_arm_group = "test"
    elif counts["test"] >= config["max_test_cases"]:
        preferred_arm_group = "baseline"
    else:
        preferred_arm_group = "baseline_or_test"
    backfill_hint = {
        "required": underfilled,
        "reason_code": "active_below_target" if underfilled else None,
        "missing_cases": target_gap,
        "preferred_arm_group": preferred_arm_group if underfilled else None,
        "instruction": (
            "After reconciling real runner liveness and releasing exact terminal "
            f"or runner-invalid runs, admit and launch up to {target_gap} additional "
            "benchmark case(s), then read concurrency-status again."
            if underfilled
            else None
        ),
    }
    runtime_reconciliation_hint = {
        "required_on_transition_or_cadence": True,
        "reason_code": "admission_ledger_is_not_runtime_liveness",
        "active_count_source": "admission_ledger",
        "authoritative_observation": "benchmark_runtime_observation_v0",
        "observation_command": "loopx benchmark runtime-observation --help",
        "triggers": [
            "launch_receipt",
            "terminal_transition",
            "runner_invalid_transition",
            "periodic_liveness_check",
        ],
        "instruction": (
            "Classify exact-job receipts and runner-owner liveness through "
            "runtime-observation. Apply its typed terminal or runner-invalid transition "
            "before releasing that run, then read concurrency-status and backfill any "
            "reported gap."
        ),
    }
    if overcommitted:
        next_action = "release_terminal_runs"
    elif target_excess > 0:
        next_action = "drain_to_target"
    elif underfilled:
        next_action = "backfill_to_target"
    else:
        next_action = "hold_at_target"
    return {
        "ok": not overcommitted,
        "schema_version": BENCHMARK_CONCURRENCY_ENVELOPE_SCHEMA_VERSION,
        "configured": True,
        "configured_at": normalized["configured_at"],
        "updated_at": normalized["updated_at"],
        "config": config,
        "active_counts": counts,
        "remaining_capacity": _capacity(config, counts),
        "target_occupancy": {
            "active_cases": target,
            "current_cases": counts["total"],
            "missing_cases": target_gap,
            "excess_cases": target_excess,
            "underfilled": underfilled,
            "above_target": target_excess > 0,
            "utilization": counts["total"] / target,
        },
        "backfill_hint": backfill_hint,
        "runtime_reconciliation_hint": runtime_reconciliation_hint,
        "overcommitted": overcommitted,
        "active_runs": normalized["active_runs"],
        "next_action": next_action,
        "path_recorded": False,
    }


def _admission_preview(
    envelope: Mapping[str, Any] | None,
    *,
    run_id: str,
    case_id: str,
    arm_role: str,
    admitted_at: str,
    resource_headroom_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    compact_run = _normalize_active_run(
        {
            "run_id": run_id,
            "case_id": case_id,
            "arm_role": arm_role,
            "admitted_at": admitted_at,
        }
    )
    if envelope is None:
        return {
            "ok": False,
            "admitted": False,
            "write_performed": False,
            "reason_codes": ["concurrency_envelope_not_configured"],
            "run": compact_run,
        }
    normalized = normalize_benchmark_concurrency_envelope(envelope)
    existing = next(
        (
            item
            for item in normalized["active_runs"]
            if item["run_id"] == compact_run["run_id"]
        ),
        None,
    )
    if existing is not None:
        if (
            existing["case_id"] != compact_run["case_id"]
            or existing["arm_role"] != compact_run["arm_role"]
        ):
            raise ValueError(
                "run_id is already admitted with a different case or arm_role"
            )
        return {
            "ok": True,
            "admitted": True,
            "idempotent": True,
            "write_performed": False,
            "reason_codes": [],
            "run": existing,
            "status": build_benchmark_concurrency_status(normalized),
        }

    config = normalized["config"]
    counts = _counts(normalized["active_runs"])
    group = _arm_group(compact_run["arm_role"])
    reasons: list[str] = []
    resource_headroom = _resource_headroom_admission(
        resource_headroom_receipt,
        required=bool(config["require_resource_headroom_receipt"]),
        admitted_at=admitted_at,
    )
    reasons.extend(resource_headroom["reason_codes"])
    if counts["total"] >= config["target_active_cases"]:
        reasons.append("target_capacity_exhausted")
    if counts["total"] >= config["max_active_cases"]:
        reasons.append("total_capacity_exhausted")
    if group == "baseline" and counts["baseline"] >= config["max_baseline_cases"]:
        reasons.append("baseline_capacity_exhausted")
    if group == "test" and counts["test"] >= config["max_test_cases"]:
        reasons.append("test_capacity_exhausted")
    if group == "baseline":
        unfilled_test_reserve = max(0, config["reserved_test_cases"] - counts["test"])
        total_after = counts["total"] + 1
        remaining_after = config["target_active_cases"] - total_after
        if remaining_after < unfilled_test_reserve:
            reasons.append("reserved_test_capacity")

    if reasons:
        return {
            "ok": False,
            "admitted": False,
            "write_performed": False,
            "reason_codes": reasons,
            "run": compact_run,
            "resource_headroom": resource_headroom,
            "status": build_benchmark_concurrency_status(normalized),
        }
    next_envelope = {
        **normalized,
        "updated_at": admitted_at,
        "active_runs": [*normalized["active_runs"], compact_run],
    }
    return {
        "ok": True,
        "admitted": True,
        "idempotent": False,
        "write_performed": False,
        "reason_codes": [],
        "run": compact_run,
        "resource_headroom": resource_headroom,
        "status": build_benchmark_concurrency_status(next_envelope),
        "envelope": normalize_benchmark_concurrency_envelope(next_envelope),
    }


def configure_benchmark_concurrency_envelope(
    path: str | Path,
    config: Mapping[str, Any],
    *,
    execute: bool = False,
    agent_id: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser()
    compact_config = normalize_benchmark_concurrency_config(config)
    timestamp = _timestamp(observed_at or _now_iso(), field="observed_at")

    def build(current: Mapping[str, Any] | None) -> dict[str, Any]:
        normalized = (
            normalize_benchmark_concurrency_envelope(current)
            if current is not None
            else None
        )
        envelope = normalize_benchmark_concurrency_envelope(
            {
                "schema_version": BENCHMARK_CONCURRENCY_ENVELOPE_SCHEMA_VERSION,
                "configured_at": (
                    normalized["configured_at"] if normalized is not None else timestamp
                ),
                "updated_at": timestamp,
                "config": compact_config,
                "active_runs": normalized["active_runs"]
                if normalized is not None
                else [],
            }
        )
        status = build_benchmark_concurrency_status(envelope)
        return {
            "ok": status["ok"],
            "configured": status["ok"],
            "dry_run": not execute,
            "write_performed": False,
            "status": status,
            "envelope": envelope,
        }

    if not execute:
        return build(read_benchmark_concurrency_envelope(target))
    with exclusive_file_lock(
        target, agent_id=agent_id, operation="benchmark_concurrency_configure"
    ):
        result = build(read_benchmark_concurrency_envelope(target))
        if result["ok"]:
            atomic_write_json(target, result["envelope"])
            result["write_performed"] = True
        return result


def admit_benchmark_case(
    path: str | Path,
    *,
    run_id: str,
    case_id: str,
    arm_role: str,
    execute: bool = False,
    agent_id: str | None = None,
    admitted_at: str | None = None,
    resource_headroom_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser()
    timestamp = _timestamp(admitted_at or _now_iso(), field="admitted_at")
    if not execute:
        result = _admission_preview(
            read_benchmark_concurrency_envelope(target),
            run_id=run_id,
            case_id=case_id,
            arm_role=arm_role,
            admitted_at=timestamp,
            resource_headroom_receipt=resource_headroom_receipt,
        )
        result["dry_run"] = True
        return result
    with exclusive_file_lock(
        target, agent_id=agent_id, operation="benchmark_concurrency_admit"
    ):
        result = _admission_preview(
            read_benchmark_concurrency_envelope(target),
            run_id=run_id,
            case_id=case_id,
            arm_role=arm_role,
            admitted_at=timestamp,
            resource_headroom_receipt=resource_headroom_receipt,
        )
        result["dry_run"] = False
        envelope = result.pop("envelope", None)
        if result["ok"] and envelope is not None:
            atomic_write_json(target, envelope)
            result["write_performed"] = True
        return result


def release_benchmark_case(
    path: str | Path,
    *,
    run_id: str,
    execute: bool = False,
    agent_id: str | None = None,
    released_at: str | None = None,
) -> dict[str, Any]:
    target = Path(path).expanduser()
    compact_run_id = _token(run_id, field="run_id")
    timestamp = _timestamp(released_at or _now_iso(), field="released_at")

    def build(
        current: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if current is None:
            return (
                {
                    "ok": True,
                    "released": False,
                    "idempotent": True,
                    "write_performed": False,
                    "reason_codes": ["concurrency_envelope_not_configured"],
                    "run_id": compact_run_id,
                },
                None,
            )
        normalized = normalize_benchmark_concurrency_envelope(current)
        remaining = [
            item
            for item in normalized["active_runs"]
            if item["run_id"] != compact_run_id
        ]
        released = len(remaining) != len(normalized["active_runs"])
        envelope = normalize_benchmark_concurrency_envelope(
            {**normalized, "updated_at": timestamp, "active_runs": remaining}
        )
        return (
            {
                "ok": True,
                "released": released,
                "idempotent": not released,
                "write_performed": False,
                "reason_codes": [] if released else ["run_not_active"],
                "run_id": compact_run_id,
                "status": build_benchmark_concurrency_status(envelope),
            },
            envelope if released else None,
        )

    if not execute:
        result, _ = build(read_benchmark_concurrency_envelope(target))
        result["dry_run"] = True
        return result
    with exclusive_file_lock(
        target, agent_id=agent_id, operation="benchmark_concurrency_release"
    ):
        result, envelope = build(read_benchmark_concurrency_envelope(target))
        result["dry_run"] = False
        if envelope is not None:
            atomic_write_json(target, envelope)
            result["write_performed"] = True
        return result


def render_benchmark_concurrency_markdown(payload: Mapping[str, Any]) -> str:
    status = (
        payload.get("status") if isinstance(payload.get("status"), Mapping) else payload
    )
    counts = (
        status.get("active_counts")
        if isinstance(status.get("active_counts"), Mapping)
        else {}
    )
    config = status.get("config") if isinstance(status.get("config"), Mapping) else {}
    lines = [
        "# Benchmark Concurrency Envelope",
        "",
        f"- Configured: `{status.get('configured', False)}`",
        f"- Active: `{counts.get('total', 0)}` (baseline `{counts.get('baseline', 0)}`, test `{counts.get('test', 0)}`)",
    ]
    if config:
        lines.extend(
            [
                f"- Maximum active cases: `{config.get('max_active_cases')}`",
                f"- Target active cases: `{config.get('target_active_cases')}`",
                f"- Baseline cap: `{config.get('max_baseline_cases')}`",
                f"- Test cap: `{config.get('max_test_cases')}`",
                f"- Reserved test slots: `{config.get('reserved_test_cases')}`",
                (
                    "- Resource headroom receipt required: "
                    f"`{config.get('require_resource_headroom_receipt', False)}`"
                ),
            ]
        )
    if payload.get("action"):
        lines.extend(
            [
                f"- Adaptive action: `{payload.get('action')}`",
                (
                    "- Adaptive target: "
                    f"`{payload.get('current_target_active_cases')}` -> "
                    f"`{payload.get('next_target_active_cases')}`"
                ),
            ]
        )
    target = status.get("target_occupancy")
    if isinstance(target, Mapping) and target.get("underfilled"):
        lines.append(
            f"- Underfilled: `true` (missing `{target.get('missing_cases', 0)}` case(s)); "
            "admit and launch replacements, then read status again."
        )
    reason_codes = payload.get("reason_codes")
    if isinstance(reason_codes, list) and reason_codes:
        lines.append(f"- Reasons: `{', '.join(str(item) for item in reason_codes)}`")
    return "\n".join(lines).rstrip() + "\n"
