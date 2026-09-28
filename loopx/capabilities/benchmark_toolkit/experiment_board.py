from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ...domain_state import default_domain_state_file_path, upsert_domain_state_jsonl
from .factorial_contrast import (
    build_benchmark_factorial_contrasts,
    build_benchmark_metric_delta,
)

BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION = "benchmark_experiment_board_row_v0"
BENCHMARK_EXPERIMENT_BOARD_SCHEMA_VERSION = "benchmark_experiment_board_v0"
BENCHMARK_EXPERIMENT_BOARD_LEDGER_FILENAME = "experiment-board.jsonl"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_ARM_ROLES = {"baseline", "control", "treatment", "explore"}
_RUN_STATUSES = {"planned", "running", "completed", "runner_invalid", "cancelled"}
_RUN_STATUS_TRANSITIONS = {
    "planned": _RUN_STATUSES,
    "running": {"running", "completed", "runner_invalid", "cancelled"},
    "completed": {"completed"},
    "runner_invalid": {"runner_invalid"},
    "cancelled": {"cancelled"},
}
_TERMINAL_RUN_STATUSES = {"completed", "runner_invalid", "cancelled"}
_CLAIM_SCOPES = {"matched_study", "diagnostic_only", "inventory_only"}
_TREATMENT_FIDELITY = {"qualified", "failed", "pending", "not_applicable"}
_INSIGHT_STATUSES = {"pending", "complete", "not_required"}

_ROW_FIELDS = {
    "schema_version",
    "benchmark_id",
    "study_id",
    "case_id",
    "run_id",
    "arm_id",
    "arm_role",
    "attempt",
    "status",
    "observed_at",
    "model_id",
    "protocol_id",
    "comparison_protocol_id",
    "runner_revision",
    "orchestrator_runtime",
    "comparison_anchor_run_id",
    "claim_scope",
    "primary_metric",
    "guardrail_metrics",
    "metrics",
    "countability",
    "treatment_fidelity",
    "effort",
    "insight",
}


def benchmark_run_status_is_terminal(value: object) -> bool:
    """Return whether a normalized run status closes its lifecycle."""

    return isinstance(value, str) and value in _TERMINAL_RUN_STATUSES


def _reject_unknown_fields(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    field: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field} contains unsupported fields: {', '.join(unknown)}")


def _token(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{field} must be a compact public-safe token")
    return text


def _optional_token(value: Any, *, field: str) -> str | None:
    if value in (None, ""):
        return None
    return _token(value, field=field)


def _artifact_ref(value: Any, *, field: str) -> str:
    text = _token(value, field=field)
    if "/" in text or "\\" in text or "://" in text:
        raise ValueError(f"{field} must be a basename or public handle, not a path")
    return text


def _finite_number(
    value: Any, *, field: str, non_negative: bool = False
) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    if non_negative and number < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _timestamp(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.isoformat()


def _metric_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError("metrics must be an object")
    if len(value) > 16:
        raise ValueError("metrics must contain at most 16 entries")
    metrics: dict[str, dict[str, Any]] = {}
    for raw_name, raw_metric in value.items():
        name = _token(raw_name, field="metrics name")
        if not isinstance(raw_metric, Mapping):
            raise TypeError(f"metrics.{name} must be an object")
        _reject_unknown_fields(
            raw_metric,
            allowed={"value", "total", "unit", "higher_is_better"},
            field=f"metrics.{name}",
        )
        if "value" not in raw_metric:
            raise ValueError(f"metrics.{name}.value is required")
        metric: dict[str, Any] = {
            "value": _finite_number(raw_metric["value"], field=f"metrics.{name}.value")
        }
        if "total" in raw_metric:
            metric["total"] = _finite_number(
                raw_metric["total"],
                field=f"metrics.{name}.total",
                non_negative=True,
            )
        if raw_metric.get("unit") not in (None, ""):
            metric["unit"] = _token(raw_metric["unit"], field=f"metrics.{name}.unit")
        if "higher_is_better" in raw_metric:
            if not isinstance(raw_metric["higher_is_better"], bool):
                raise ValueError(f"metrics.{name}.higher_is_better must be boolean")
            metric["higher_is_better"] = raw_metric["higher_is_better"]
        metrics[name] = metric
    return dict(sorted(metrics.items()))


def _countability(value: Any) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        raise TypeError("countability must be an object")
    allowed = {"integrity_qualified", "official_result_present", "score_countable"}
    _reject_unknown_fields(value, allowed=allowed, field="countability")
    result: dict[str, bool] = {}
    for field in sorted(allowed):
        field_value = value.get(field)
        if not isinstance(field_value, bool):
            raise TypeError(f"countability.{field} must be boolean")
        result[field] = field_value
    return result


def _effort(value: Any) -> dict[str, float | int]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("effort must be an object")
    allowed = {
        "duration_ms",
        "agent_steps",
        "goal_turns",
        "token_count",
        "estimated_cost_usd",
    }
    _reject_unknown_fields(value, allowed=allowed, field="effort")
    result: dict[str, float | int] = {}
    for field in ("duration_ms", "agent_steps", "goal_turns", "token_count"):
        if field in value:
            result[field] = _non_negative_int(value[field], field=f"effort.{field}")
    if "estimated_cost_usd" in value:
        result["estimated_cost_usd"] = _finite_number(
            value["estimated_cost_usd"],
            field="effort.estimated_cost_usd",
            non_negative=True,
        )
    return result


def _insight(value: Any) -> dict[str, Any]:
    if value in (None, {}):
        return {"status": "pending"}
    if not isinstance(value, Mapping):
        raise TypeError("insight must be an object")
    _reject_unknown_fields(
        value,
        allowed={"status", "classification", "artifact_ref"},
        field="insight",
    )
    status = _token(value.get("status"), field="insight.status")
    if status not in _INSIGHT_STATUSES:
        raise ValueError("insight.status is unsupported")
    result: dict[str, Any] = {"status": status}
    if value.get("classification") not in (None, ""):
        result["classification"] = _token(
            value["classification"], field="insight.classification"
        )
    if value.get("artifact_ref") not in (None, ""):
        result["artifact_ref"] = _artifact_ref(
            value["artifact_ref"], field="insight.artifact_ref"
        )
    return result


def _orchestrator_runtime(value: Any) -> dict[str, str] | None:
    if value in (None, {}):
        return None
    if not isinstance(value, Mapping):
        raise TypeError("orchestrator_runtime must be an object")
    _reject_unknown_fields(
        value,
        allowed={"provider_id", "version", "revision"},
        field="orchestrator_runtime",
    )
    result = {
        "provider_id": _token(
            value.get("provider_id"), field="orchestrator_runtime.provider_id"
        ),
        "revision": _token(
            value.get("revision"), field="orchestrator_runtime.revision"
        ),
    }
    version = _optional_token(
        value.get("version"), field="orchestrator_runtime.version"
    )
    if version is not None:
        result["version"] = version
    return result


def normalize_benchmark_experiment_board_row(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and compact one public-safe benchmark experiment-board row."""

    if not isinstance(payload, Mapping):
        raise TypeError("benchmark experiment board row must be an object")
    _reject_unknown_fields(payload, allowed=_ROW_FIELDS, field="row")
    if payload.get("schema_version") != BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION:
        raise ValueError("benchmark experiment board row schema mismatch")

    arm_role = _token(payload.get("arm_role"), field="arm_role")
    if arm_role not in _ARM_ROLES:
        raise ValueError("arm_role is unsupported")
    status = _token(payload.get("status"), field="status")
    if status not in _RUN_STATUSES:
        raise ValueError("status is unsupported")
    claim_scope = _token(payload.get("claim_scope"), field="claim_scope")
    if claim_scope not in _CLAIM_SCOPES:
        raise ValueError("claim_scope is unsupported")
    if arm_role == "explore" and claim_scope != "diagnostic_only":
        raise ValueError("explore rows must use diagnostic_only claim_scope")

    treatment_fidelity = _token(
        payload.get("treatment_fidelity"), field="treatment_fidelity"
    )
    if treatment_fidelity not in _TREATMENT_FIDELITY:
        raise ValueError("treatment_fidelity is unsupported")
    comparison_anchor_run_id = _optional_token(
        payload.get("comparison_anchor_run_id"), field="comparison_anchor_run_id"
    )
    if arm_role == "baseline":
        if comparison_anchor_run_id is not None:
            raise ValueError("baseline rows cannot name comparison_anchor_run_id")
        if treatment_fidelity != "not_applicable":
            raise ValueError("baseline rows must use not_applicable treatment_fidelity")
    elif comparison_anchor_run_id is None:
        raise ValueError("non-baseline rows must name comparison_anchor_run_id")
    elif treatment_fidelity == "not_applicable":
        raise ValueError(
            "non-baseline rows cannot use not_applicable treatment_fidelity"
        )

    metrics = _metric_map(payload.get("metrics", {}))
    primary_metric = _token(payload.get("primary_metric"), field="primary_metric")
    raw_guardrails = payload.get("guardrail_metrics", [])
    if not isinstance(raw_guardrails, list) or len(raw_guardrails) > 12:
        raise ValueError("guardrail_metrics must be a list with at most 12 entries")
    guardrails: list[str] = []
    for raw_guardrail in raw_guardrails:
        guardrail = _token(raw_guardrail, field="guardrail_metrics")
        if guardrail not in guardrails:
            guardrails.append(guardrail)
    if primary_metric in guardrails:
        raise ValueError("primary_metric cannot also be a guardrail metric")

    countability = _countability(payload.get("countability"))
    if countability["official_result_present"]:
        if primary_metric not in metrics:
            raise ValueError("official result must include primary_metric")
        missing_guardrails = [name for name in guardrails if name not in metrics]
        if missing_guardrails:
            raise ValueError("official result is missing declared guardrail metrics")
    if countability["score_countable"]:
        if status != "completed":
            raise ValueError("score_countable requires completed status")
        if not countability["integrity_qualified"]:
            raise ValueError("score_countable requires integrity_qualified")
        if not countability["official_result_present"]:
            raise ValueError("score_countable requires official_result_present")

    row: dict[str, Any] = {
        "schema_version": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
        "benchmark_id": _token(payload.get("benchmark_id"), field="benchmark_id"),
        "study_id": _token(payload.get("study_id"), field="study_id"),
        "case_id": _token(payload.get("case_id"), field="case_id"),
        "run_id": _token(payload.get("run_id"), field="run_id"),
        "arm_id": _token(payload.get("arm_id"), field="arm_id"),
        "arm_role": arm_role,
        "attempt": _non_negative_int(payload.get("attempt"), field="attempt"),
        "status": status,
        "observed_at": _timestamp(payload.get("observed_at"), field="observed_at"),
        "model_id": _token(payload.get("model_id"), field="model_id"),
        "protocol_id": _token(payload.get("protocol_id"), field="protocol_id"),
        "comparison_protocol_id": _token(
            payload.get("comparison_protocol_id"), field="comparison_protocol_id"
        ),
        "claim_scope": claim_scope,
        "primary_metric": primary_metric,
        "guardrail_metrics": guardrails,
        "metrics": metrics,
        "countability": countability,
        "treatment_fidelity": treatment_fidelity,
        "effort": _effort(payload.get("effort")),
        "insight": _insight(payload.get("insight")),
    }
    runner_revision = _optional_token(
        payload.get("runner_revision"), field="runner_revision"
    )
    if runner_revision is not None:
        row["runner_revision"] = runner_revision
    orchestrator_runtime = _orchestrator_runtime(payload.get("orchestrator_runtime"))
    if orchestrator_runtime is not None:
        row["orchestrator_runtime"] = orchestrator_runtime
    if comparison_anchor_run_id is not None:
        row["comparison_anchor_run_id"] = comparison_anchor_run_id
    return row


def benchmark_experiment_board_row_key(payload: Mapping[str, Any]) -> dict[str, str]:
    row = dict(payload)
    row.pop("domain_state_key", None)
    normalized = normalize_benchmark_experiment_board_row(row)
    return {
        "schema_version": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
        "benchmark_id": normalized["benchmark_id"],
        "study_id": normalized["study_id"],
        "case_id": normalized["case_id"],
        "run_id": normalized["run_id"],
    }


def default_benchmark_experiment_board_path(
    *, project: str | Path = ".", goal_id: str
) -> Path:
    return default_domain_state_file_path(
        project=project,
        goal_id=goal_id,
        domain_pack="benchmark_toolkit",
        filename=BENCHMARK_EXPERIMENT_BOARD_LEDGER_FILENAME,
    )


def read_benchmark_experiment_board_rows(
    ledger_path: str | Path,
) -> list[dict[str, Any]]:
    path = Path(ledger_path).expanduser()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(
        path.read_text(encoding="utf-8").split("\n"), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid experiment-board JSONL row {index}") from exc
        if not isinstance(payload, Mapping):
            raise TypeError(f"invalid experiment-board JSONL row {index}")
        compact = dict(payload)
        compact.pop("domain_state_key", None)
        rows.append(normalize_benchmark_experiment_board_row(compact))
    return rows


def preview_benchmark_experiment_board_upsert(
    rows: Iterable[Mapping[str, Any]], payload: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    normalized_rows = [
        normalize_benchmark_experiment_board_row(dict(row)) for row in rows
    ]
    candidate = normalize_benchmark_experiment_board_row(payload)
    candidate_key = benchmark_experiment_board_row_key(candidate)
    output: list[dict[str, Any]] = []
    status = "inserted"
    for row in normalized_rows:
        if benchmark_experiment_board_row_key(row) == candidate_key:
            if status == "inserted":
                _validate_benchmark_run_status_transition(row, candidate)
                output.append(candidate)
                status = "updated"
            continue
        output.append(row)
    if status == "inserted":
        output.append(candidate)
    return output, status


def _validate_benchmark_run_status_transition(
    existing: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    existing_status = str(existing.get("status") or "")
    candidate_status = str(candidate.get("status") or "")
    if candidate_status not in _RUN_STATUS_TRANSITIONS.get(existing_status, set()):
        raise ValueError(
            f"benchmark run status cannot move from {existing_status} to {candidate_status}"
        )


def _merge_benchmark_experiment_board_domain_row(
    existing: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    existing_row = dict(existing)
    existing_row.pop("domain_state_key", None)
    candidate_row = dict(candidate)
    candidate_row.pop("domain_state_key", None)
    _validate_benchmark_run_status_transition(
        normalize_benchmark_experiment_board_row(existing_row),
        normalize_benchmark_experiment_board_row(candidate_row),
    )
    return candidate


def upsert_benchmark_experiment_board_row(
    ledger_path: str | Path, payload: Mapping[str, Any]
) -> dict[str, Any]:
    row = normalize_benchmark_experiment_board_row(payload)
    write = upsert_domain_state_jsonl(
        ledger_path,
        row,
        key=benchmark_experiment_board_row_key(row),
        existing_key_fn=benchmark_experiment_board_row_key,
        merge_existing_fn=_merge_benchmark_experiment_board_domain_row,
    )
    return {
        "ok": True,
        "schema_version": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
        "row": row,
        "write": write,
    }


def _benchmark_experiment_board_row_key_tuple(
    payload: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    key = benchmark_experiment_board_row_key(payload)
    return (
        key["benchmark_id"],
        key["study_id"],
        key["case_id"],
        key["run_id"],
    )


def _benchmark_experiment_board_observed_at(payload: Mapping[str, Any]) -> datetime:
    return datetime.fromisoformat(str(payload["observed_at"]))


def _advance_reconciled_benchmark_experiment_board_row(
    selected: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    if selected == candidate:
        return selected, False

    selected_status = str(selected["status"])
    candidate_status = str(candidate["status"])
    if (
        selected_status in _TERMINAL_RUN_STATUSES
        and candidate_status in _TERMINAL_RUN_STATUSES
        and candidate_status != selected_status
    ):
        raise ValueError(
            "benchmark experiment board sources contain conflicting terminal states"
        )

    selected_at = _benchmark_experiment_board_observed_at(selected)
    candidate_at = _benchmark_experiment_board_observed_at(candidate)
    if candidate_at == selected_at:
        raise ValueError(
            "benchmark experiment board sources contain ambiguous rows at "
            "the same observed_at timestamp"
        )
    if candidate_at < selected_at:
        return selected, True
    if selected_status in _TERMINAL_RUN_STATUSES:
        if candidate_status in _TERMINAL_RUN_STATUSES:
            return candidate, False
        return selected, True
    if candidate_status not in _RUN_STATUS_TRANSITIONS[selected_status]:
        return selected, True
    return candidate, False


def _select_reconciled_benchmark_experiment_board_row(
    current: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    ordered = sorted(candidates, key=_benchmark_experiment_board_observed_at)
    selected = current
    stale_skipped = 0
    for candidate in ordered:
        if selected is None:
            selected = candidate
            continue
        selected, skipped = _advance_reconciled_benchmark_experiment_board_row(
            selected,
            candidate,
        )
        stale_skipped += int(skipped)

    if selected is None:
        raise ValueError("benchmark experiment board reconcile requires source rows")
    return selected, stale_skipped


def preview_benchmark_experiment_board_reconcile(
    rows: Iterable[Mapping[str, Any]],
    source_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fold provider-owned board shards into one monotonic canonical projection."""

    normalized_rows = [
        normalize_benchmark_experiment_board_row(dict(row)) for row in rows
    ]
    normalized_sources = [
        normalize_benchmark_experiment_board_row(dict(row)) for row in source_rows
    ]
    if not normalized_sources:
        raise ValueError("benchmark experiment board reconcile requires source rows")

    existing_by_key = {
        _benchmark_experiment_board_row_key_tuple(row): row for row in normalized_rows
    }
    candidates_by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in normalized_sources:
        candidates_by_key.setdefault(
            _benchmark_experiment_board_row_key_tuple(row), []
        ).append(row)

    result_by_key = dict(existing_by_key)
    counts = {
        "source_row_count": len(normalized_sources),
        "candidate_run_count": len(candidates_by_key),
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "stale_skipped": 0,
    }
    for key, candidates in candidates_by_key.items():
        current = existing_by_key.get(key)
        selected, stale_skipped = _select_reconciled_benchmark_experiment_board_row(
            current, candidates
        )
        counts["stale_skipped"] += stale_skipped
        result_by_key[key] = selected
        if current is None:
            counts["inserted"] += 1
        elif current == selected:
            counts["unchanged"] += 1
        else:
            counts["updated"] += 1

    ordered_keys = [
        _benchmark_experiment_board_row_key_tuple(row) for row in normalized_rows
    ]
    ordered_keys.extend(key for key in candidates_by_key if key not in existing_by_key)
    return [result_by_key[key] for key in ordered_keys], counts


def _build_comparison(
    candidate: Mapping[str, Any], anchor: Mapping[str, Any] | None
) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []
    if anchor is None:
        reasons.append("comparison_anchor_run_missing")
    else:
        anchor_role = anchor.get("arm_role")
        if candidate.get("arm_role") in {"control", "treatment"}:
            if anchor_role != "baseline":
                reasons.append("standard_arm_anchor_is_not_baseline")
        elif anchor_role not in {"baseline", "control", "treatment"}:
            reasons.append("explore_anchor_role_unsupported")
        for field in ("benchmark_id", "study_id", "case_id", "model_id"):
            if anchor.get(field) != candidate.get(field):
                reasons.append(f"{field}_mismatch")
        if anchor.get("comparison_protocol_id") != candidate.get(
            "comparison_protocol_id"
        ):
            reasons.append("comparison_protocol_mismatch")
        elif anchor.get("protocol_id") != candidate.get("protocol_id"):
            warnings.append("exact_protocol_revision_differs")
        if anchor.get("primary_metric") != candidate.get("primary_metric"):
            reasons.append("primary_metric_mismatch")
        if not anchor.get("countability", {}).get("score_countable"):
            reasons.append("comparison_anchor_score_uncountable")
        if not candidate.get("countability", {}).get("score_countable"):
            reasons.append("candidate_score_uncountable")
        if candidate.get("treatment_fidelity") != "qualified":
            reasons.append("candidate_treatment_fidelity_not_qualified")

    metric_deltas: dict[str, Any] = {}
    if anchor is not None:
        anchor_metrics = anchor.get("metrics", {})
        candidate_metrics = candidate.get("metrics", {})
        for name in sorted(set(anchor_metrics) & set(candidate_metrics)):
            metric_deltas[name] = build_benchmark_metric_delta(
                anchor_metrics[name], candidate_metrics[name]
            )

    return {
        "comparison_id": (
            f"{candidate.get('comparison_anchor_run_id')}->{candidate.get('run_id')}"
        ),
        "benchmark_id": candidate.get("benchmark_id"),
        "study_id": candidate.get("study_id"),
        "case_id": candidate.get("case_id"),
        "comparison_anchor_run_id": candidate.get("comparison_anchor_run_id"),
        "comparison_anchor_arm_role": anchor.get("arm_role") if anchor else None,
        "candidate_run_id": candidate.get("run_id"),
        "candidate_arm_id": candidate.get("arm_id"),
        "candidate_arm_role": candidate.get("arm_role"),
        "claim_scope": candidate.get("claim_scope"),
        "matched_pair_countable": not reasons,
        "reason_codes": reasons,
        "warning_codes": warnings,
        "primary_metric": candidate.get("primary_metric"),
        "metric_deltas": metric_deltas,
    }


def _row_sort_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("benchmark_id") or ""),
        str(row.get("study_id") or ""),
        str(row.get("case_id") or ""),
        str(row.get("observed_at") or ""),
        str(row.get("arm_role") or ""),
        str(row.get("arm_id") or ""),
        str(row.get("run_id") or ""),
    )


def _comparison_lane_counts(
    comparisons: Iterable[Mapping[str, Any]],
    *,
    field: str,
    values: Iterable[str],
) -> dict[str, dict[str, int]]:
    items = list(comparisons)
    return {
        value: {
            "all": sum(1 for item in items if item.get(field) == value),
            "matched_pair_countable": sum(
                1
                for item in items
                if item.get(field) == value
                and item.get("matched_pair_countable") is True
            ),
        }
        for value in sorted(values)
    }


def _orchestrator_runtime_key(row: Mapping[str, Any]) -> str:
    runtime = row.get("orchestrator_runtime")
    if not isinstance(runtime, Mapping):
        return "none"
    provider_id = str(runtime.get("provider_id") or "unknown")
    version = str(runtime.get("version") or "unversioned")
    revision = str(runtime.get("revision") or "unknown")
    return f"{provider_id}@{version}@{revision}"


def build_benchmark_experiment_board(
    rows: Iterable[Mapping[str, Any]],
    *,
    benchmark_id: str | None = None,
    study_id: str | None = None,
    four_arm_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = [normalize_benchmark_experiment_board_row(dict(row)) for row in rows]
    if benchmark_id is not None:
        benchmark_filter = _token(benchmark_id, field="benchmark_id")
        normalized = [
            row for row in normalized if row["benchmark_id"] == benchmark_filter
        ]
    if study_id is not None:
        study_filter = _token(study_id, field="study_id")
        normalized = [row for row in normalized if row["study_id"] == study_filter]
    normalized.sort(key=_row_sort_key)

    factorial_contrasts = (
        build_benchmark_factorial_contrasts(normalized, four_arm_contract)
        if four_arm_contract is not None
        else []
    )

    by_run_id = {row["run_id"]: row for row in normalized}
    comparisons = [
        _build_comparison(row, by_run_id.get(row.get("comparison_anchor_run_id")))
        for row in normalized
        if row["arm_role"] != "baseline"
    ]
    role_counts = {
        role: sum(1 for row in normalized if row["arm_role"] == role)
        for role in sorted(_ARM_ROLES)
    }
    comparison_arm_role_counts = _comparison_lane_counts(
        comparisons,
        field="candidate_arm_role",
        values=_ARM_ROLES - {"baseline"},
    )
    comparison_claim_scope_counts = _comparison_lane_counts(
        comparisons,
        field="claim_scope",
        values=_CLAIM_SCOPES,
    )
    case_keys = {
        (row["benchmark_id"], row["study_id"], row["case_id"]) for row in normalized
    }
    orchestrator_runtime_counts: dict[str, int] = {}
    for row in normalized:
        runtime_key = _orchestrator_runtime_key(row)
        orchestrator_runtime_counts[runtime_key] = (
            orchestrator_runtime_counts.get(runtime_key, 0) + 1
        )
    return {
        "ok": True,
        "schema_version": BENCHMARK_EXPERIMENT_BOARD_SCHEMA_VERSION,
        "summary": {
            "run_count": len(normalized),
            "case_count": len(case_keys),
            "terminal_run_count": sum(
                1
                for row in normalized
                if row["status"] in {"completed", "runner_invalid", "cancelled"}
            ),
            "score_countable_run_count": sum(
                1 for row in normalized if row["countability"]["score_countable"]
            ),
            "comparison_count": len(comparisons),
            "matched_pair_countable_count": sum(
                1 for item in comparisons if item["matched_pair_countable"]
            ),
            "factorial_contrast_count": len(factorial_contrasts),
            "factorial_contrast_countable_count": sum(
                1
                for item in factorial_contrasts
                if item["factorial_contrast_countable"]
            ),
            "arm_role_counts": role_counts,
            "comparison_arm_role_counts": comparison_arm_role_counts,
            "comparison_claim_scope_counts": comparison_claim_scope_counts,
            "orchestrator_runtime_counts": dict(
                sorted(orchestrator_runtime_counts.items())
            ),
        },
        "runs": normalized,
        "comparisons": comparisons,
        "factorial_contrasts": factorial_contrasts,
        "agent_guidance": {
            "required_sequence": [
                "read_board_before_launch_or_case_selection",
                "upsert_preregistered_or_running_row_when_a_run_starts",
                "upsert_terminal_score_countability_effort_and_insight_status",
                "read_matched_comparisons_before_selecting_the_next_arm",
            ],
            "selection_boundary": (
                "Use only preregistered/public-safe candidate sources during solving; "
                "post-run private evidence belongs in benchmark_case_insight_v0, not this board."
            ),
            "claim_boundary": (
                "Only matched_pair_countable comparisons support paired claims; "
                "only factorial_contrast_countable projections support conditional "
                "effects or difference-in-differences; diagnostic_only explore rows "
                "remain a separate evidence lane."
            ),
            "next_action": (
                "upsert_first_run_row"
                if not normalized
                else "close_running_rows_or_review_matched_comparisons"
            ),
        },
        "public_boundary": {
            "raw_task_recorded": False,
            "raw_trajectory_recorded": False,
            "hidden_evaluation_recorded": False,
            "raw_verifier_output_recorded": False,
            "local_paths_recorded": False,
            "credentials_recorded": False,
        },
    }


def _format_metric(metric: Mapping[str, Any] | None) -> str:
    if not metric:
        return "n/a"
    value = metric.get("value")
    total = metric.get("total")
    return f"{value}/{total}" if total is not None else str(value)


def _format_orchestrator_runtime(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "n/a"
    provider_id = str(value.get("provider_id") or "unknown")
    version = str(value.get("version") or "")
    revision = str(value.get("revision") or "unknown")
    identity = f"{provider_id}@{version}" if version else provider_id
    return f"{identity}:{revision[:12]}"


def render_benchmark_experiment_board_markdown(payload: Mapping[str, Any]) -> str:
    summary = (
        payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    )
    claim_scope_counts = (
        summary.get("comparison_claim_scope_counts")
        if isinstance(summary.get("comparison_claim_scope_counts"), Mapping)
        else {}
    )
    matched_study_counts = (
        claim_scope_counts.get("matched_study")
        if isinstance(claim_scope_counts.get("matched_study"), Mapping)
        else {}
    )
    diagnostic_counts = (
        claim_scope_counts.get("diagnostic_only")
        if isinstance(claim_scope_counts.get("diagnostic_only"), Mapping)
        else {}
    )
    lines = [
        "# Benchmark Experiment Board",
        "",
        f"- Runs: `{summary.get('run_count', 0)}`",
        f"- Cases: `{summary.get('case_count', 0)}`",
        f"- Score-countable runs: `{summary.get('score_countable_run_count', 0)}`",
        f"- Countable matched comparisons: `{summary.get('matched_pair_countable_count', 0)}`",
        f"- Countable factorial contrasts: `{summary.get('factorial_contrast_countable_count', 0)}`",
        f"- Countable matched-study comparisons: `{matched_study_counts.get('matched_pair_countable', 0)}`",
        f"- Countable diagnostic-only comparisons: `{diagnostic_counts.get('matched_pair_countable', 0)}`",
        "",
        "## Runs",
        "",
        "| Benchmark | Study | Case | Role | Arm | Orchestrator | Status | Primary | Reward | Integrity | Countable | Fidelity | Steps | Cost USD | Insight |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in payload.get("runs", []) or []:
        if not isinstance(row, Mapping):
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
        primary = metrics.get(row.get("primary_metric"))
        reward = metrics.get("reward")
        countability = (
            row.get("countability")
            if isinstance(row.get("countability"), Mapping)
            else {}
        )
        effort = row.get("effort") if isinstance(row.get("effort"), Mapping) else {}
        insight = row.get("insight") if isinstance(row.get("insight"), Mapping) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("benchmark_id")),
                    str(row.get("study_id")),
                    str(row.get("case_id")),
                    str(row.get("arm_role")),
                    str(row.get("arm_id")),
                    _format_orchestrator_runtime(row.get("orchestrator_runtime")),
                    str(row.get("status")),
                    _format_metric(primary if isinstance(primary, Mapping) else None),
                    _format_metric(reward if isinstance(reward, Mapping) else None),
                    str(countability.get("integrity_qualified")),
                    str(countability.get("score_countable")),
                    str(row.get("treatment_fidelity")),
                    str(effort.get("agent_steps", "n/a")),
                    str(effort.get("estimated_cost_usd", "n/a")),
                    str(insight.get("status", "pending")),
                ]
            )
            + " |"
        )
    if not payload.get("runs"):
        lines.append(
            "| n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |"
        )

    lines.extend(
        [
            "",
            "## Comparisons",
            "",
            "| Case | Candidate role | Candidate arm | Primary delta | Countable | Reasons | Warnings |",
            "| --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for item in payload.get("comparisons", []) or []:
        if not isinstance(item, Mapping):
            continue
        deltas = (
            item.get("metric_deltas")
            if isinstance(item.get("metric_deltas"), Mapping)
            else {}
        )
        primary_delta = deltas.get(item.get("primary_metric"))
        delta_value = (
            primary_delta.get("delta") if isinstance(primary_delta, Mapping) else "n/a"
        )
        reasons = (
            ", ".join(str(value) for value in item.get("reason_codes", []) or [])
            or "none"
        )
        warnings = (
            ", ".join(str(value) for value in item.get("warning_codes", []) or [])
            or "none"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("case_id")),
                    str(item.get("candidate_arm_role")),
                    str(item.get("candidate_arm_id")),
                    str(delta_value),
                    str(item.get("matched_pair_countable")),
                    reasons,
                    warnings,
                ]
            )
            + " |"
        )
    if not payload.get("comparisons"):
        lines.append("| n/a | n/a | n/a | n/a | n/a | n/a | n/a |")

    lines.extend(
        [
            "",
            "## Factorial Contrasts",
            "",
            "| Case | Primary interaction | Countable | Reasons |",
            "| --- | ---: | --- | --- |",
        ]
    )
    for item in payload.get("factorial_contrasts", []) or []:
        if not isinstance(item, Mapping):
            continue
        interaction = (
            item.get("interaction_contrast")
            if isinstance(item.get("interaction_contrast"), Mapping)
            else {}
        )
        metrics = (
            interaction.get("metric_contrasts")
            if isinstance(interaction.get("metric_contrasts"), Mapping)
            else {}
        )
        primary = metrics.get(item.get("primary_metric"))
        interaction_value = (
            primary.get("difference_in_differences")
            if isinstance(primary, Mapping)
            else "n/a"
        )
        reasons = (
            ", ".join(str(value) for value in item.get("reason_codes", []) or [])
            or "none"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("case_id")),
                    str(interaction_value),
                    str(item.get("factorial_contrast_countable")),
                    reasons,
                ]
            )
            + " |"
        )
    if not payload.get("factorial_contrasts"):
        lines.append("| n/a | n/a | n/a | n/a |")

    guidance = (
        payload.get("agent_guidance")
        if isinstance(payload.get("agent_guidance"), Mapping)
        else {}
    )
    lines.extend(
        [
            "",
            "## Agent Lifecycle",
            "",
        ]
    )
    for step in guidance.get("required_sequence", []) or []:
        lines.append(f"- `{step}`")
    lines.extend(
        [
            f"- next: `{guidance.get('next_action')}`",
            f"- selection boundary: {guidance.get('selection_boundary')}",
            f"- claim boundary: {guidance.get('claim_boundary')}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
