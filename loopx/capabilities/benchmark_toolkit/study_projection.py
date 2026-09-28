"""Provider-neutral benchmark study, upload, and dashboard contracts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from .experiment_board import (
    BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
    benchmark_experiment_board_row_key,
    benchmark_run_status_is_terminal,
    build_benchmark_experiment_board,
    normalize_benchmark_experiment_board_row,
    preview_benchmark_experiment_board_upsert,
)
from .four_arm_contract import BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION
from .runtime_observation import (
    BENCHMARK_RUNTIME_OBSERVATION_SCHEMA_VERSION,
    build_benchmark_runtime_observation,
)

BENCHMARK_STUDY_MANIFEST_SCHEMA_VERSION = "benchmark_study_manifest_v0"
BENCHMARK_UPLOAD_ENVELOPE_SCHEMA_VERSION = "benchmark_upload_envelope_v0"
BENCHMARK_UPLOAD_READBACK_RECEIPT_SCHEMA_VERSION = (
    "benchmark_upload_readback_receipt_v0"
)
BENCHMARK_LOCAL_UPLOAD_RECORD_SCHEMA_VERSION = "benchmark_local_upload_record_v0"
BENCHMARK_CASE_INSIGHT_PROJECTION_SCHEMA_VERSION = (
    "benchmark_case_insight_projection_v0"
)
BENCHMARK_STUDY_DASHBOARD_SCHEMA_VERSION = "benchmark_study_dashboard_v0"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ARM_ROLES = {"baseline", "control", "treatment", "explore"}
_METRIC_ROLES = {"primary", "guardrail", "supporting"}
_RECORD_KINDS = {
    "study_manifest",
    "experiment_board_row",
    "case_insight_projection",
    "runtime_observation",
    "behavior_finding",
}
_CONFIDENCE_LEVELS = {"low", "medium", "high"}
_EXPECTEDNESS = {"expected", "unexpected", "mixed", "unknown"}


def _reject_unknown_fields(
    payload: Mapping[str, Any], *, allowed: set[str], field: str
) -> None:
    unknown = sorted(set(payload) - allowed)
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


def _timestamp(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.isoformat()


def _bounded_text(value: Any, *, field: str, limit: int = 600) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or "\x00" in text:
        raise ValueError(f"{field} must be bounded non-empty text")
    return text


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_number(value: Any, *, field: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    if not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite")
    return value


def _public_scalar(value: Any, *, field: str) -> str | int | float | bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _finite_number(value, field=field)
    return _token(value, field=field)


def _normalize_extension_metadata(value: Any) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping) or len(value) > 16:
        raise TypeError("extension_metadata must be a small object")
    output: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _token(raw_key, field="extension_metadata key")
        if isinstance(raw_value, list):
            if len(raw_value) > 16:
                raise ValueError("extension_metadata lists contain at most 16 items")
            output[key] = [
                _public_scalar(item, field=f"extension_metadata.{key}")
                for item in raw_value
            ]
        else:
            output[key] = _public_scalar(raw_value, field=f"extension_metadata.{key}")
    return dict(sorted(output.items()))


def normalize_benchmark_study_manifest(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a reusable study declaration without adding scoring authority."""

    if not isinstance(payload, Mapping):
        raise TypeError("benchmark study manifest must be an object")
    _reject_unknown_fields(
        payload,
        allowed={
            "schema_version",
            "benchmark_id",
            "study_id",
            "protocol_id",
            "comparison_protocol_id",
            "case_set",
            "factors",
            "arms",
            "baseline_arm_id",
            "metrics",
            "source_revisions",
            "labels",
            "extension_metadata",
            "privacy_classification",
        },
        field="study manifest",
    )
    if payload.get("schema_version") != BENCHMARK_STUDY_MANIFEST_SCHEMA_VERSION:
        raise ValueError("benchmark study manifest schema mismatch")
    if payload.get("privacy_classification") != "public_safe":
        raise ValueError("study manifest must use public_safe privacy classification")

    case_set = payload.get("case_set")
    if not isinstance(case_set, Mapping):
        raise TypeError("case_set must be an object")
    _reject_unknown_fields(
        case_set,
        allowed={"case_set_id", "case_ids"},
        field="case_set",
    )
    raw_case_ids = case_set.get("case_ids")
    if (
        not isinstance(raw_case_ids, list)
        or not raw_case_ids
        or len(raw_case_ids) > 5000
    ):
        raise ValueError("case_set.case_ids must contain 1 to 5000 cases")
    case_ids = [_token(item, field="case_set.case_ids") for item in raw_case_ids]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("case_set.case_ids must be unique")

    raw_factors = payload.get("factors")
    if not isinstance(raw_factors, list) or not (1 <= len(raw_factors) <= 16):
        raise ValueError("factors must contain 1 to 16 entries")
    factors: list[dict[str, Any]] = []
    levels_by_factor: dict[str, set[str]] = {}
    for raw_factor in raw_factors:
        if not isinstance(raw_factor, Mapping):
            raise TypeError("factors entries must be objects")
        _reject_unknown_fields(
            raw_factor,
            allowed={"factor_id", "levels"},
            field="factor",
        )
        factor_id = _token(raw_factor.get("factor_id"), field="factor.factor_id")
        raw_levels = raw_factor.get("levels")
        if not isinstance(raw_levels, list) or not (2 <= len(raw_levels) <= 16):
            raise ValueError("factor.levels must contain 2 to 16 levels")
        levels = [_token(item, field="factor.levels") for item in raw_levels]
        if len(set(levels)) != len(levels) or factor_id in levels_by_factor:
            raise ValueError("factor ids and levels must be unique")
        levels_by_factor[factor_id] = set(levels)
        factors.append({"factor_id": factor_id, "levels": levels})

    raw_arms = payload.get("arms")
    if not isinstance(raw_arms, list) or not (2 <= len(raw_arms) <= 32):
        raise ValueError("arms must contain 2 to 32 entries")
    arms: list[dict[str, Any]] = []
    arm_ids: set[str] = set()
    assignments_seen: set[tuple[tuple[str, str], ...]] = set()
    for raw_arm in raw_arms:
        if not isinstance(raw_arm, Mapping):
            raise TypeError("arms entries must be objects")
        _reject_unknown_fields(
            raw_arm,
            allowed={"arm_id", "arm_role", "factor_assignments"},
            field="arm",
        )
        arm_id = _token(raw_arm.get("arm_id"), field="arm.arm_id")
        arm_role = _token(raw_arm.get("arm_role"), field="arm.arm_role")
        if arm_role not in _ARM_ROLES:
            raise ValueError("arm.arm_role is unsupported")
        raw_assignments = raw_arm.get("factor_assignments")
        if not isinstance(raw_assignments, Mapping):
            raise TypeError("arm.factor_assignments must be an object")
        if set(raw_assignments) != set(levels_by_factor):
            raise ValueError("every arm must assign every declared factor exactly once")
        assignments = {
            _token(key, field="arm.factor_assignments key"): _token(
                value, field=f"arm.factor_assignments.{key}"
            )
            for key, value in raw_assignments.items()
        }
        if any(
            assignments[factor_id] not in levels
            for factor_id, levels in levels_by_factor.items()
        ):
            raise ValueError("arm factor assignment names an undeclared level")
        assignment_key = tuple(sorted(assignments.items()))
        if arm_id in arm_ids or assignment_key in assignments_seen:
            raise ValueError("arm ids and factor assignments must be unique")
        arm_ids.add(arm_id)
        assignments_seen.add(assignment_key)
        arms.append(
            {
                "arm_id": arm_id,
                "arm_role": arm_role,
                "factor_assignments": dict(sorted(assignments.items())),
            }
        )

    baseline_arm_id = _token(payload.get("baseline_arm_id"), field="baseline_arm_id")
    baseline_arms = [arm for arm in arms if arm["arm_role"] == "baseline"]
    if len(baseline_arms) != 1 or baseline_arms[0]["arm_id"] != baseline_arm_id:
        raise ValueError("manifest must declare exactly one matching baseline arm")

    raw_metrics = payload.get("metrics")
    if not isinstance(raw_metrics, list) or not (1 <= len(raw_metrics) <= 16):
        raise ValueError("metrics must contain 1 to 16 entries")
    metrics: list[dict[str, Any]] = []
    metric_names: set[str] = set()
    for raw_metric in raw_metrics:
        if not isinstance(raw_metric, Mapping):
            raise TypeError("metrics entries must be objects")
        _reject_unknown_fields(
            raw_metric,
            allowed={"metric_name", "role", "unit", "higher_is_better", "binary"},
            field="metric",
        )
        metric_name = _token(raw_metric.get("metric_name"), field="metric.metric_name")
        role = _token(raw_metric.get("role"), field="metric.role")
        if role not in _METRIC_ROLES:
            raise ValueError("metric.role is unsupported")
        higher_is_better = raw_metric.get("higher_is_better")
        binary = raw_metric.get("binary", False)
        if not isinstance(higher_is_better, bool) or not isinstance(binary, bool):
            raise TypeError("metric direction and binary flags must be boolean")
        if metric_name in metric_names:
            raise ValueError("metric names must be unique")
        metric_names.add(metric_name)
        metric = {
            "metric_name": metric_name,
            "role": role,
            "higher_is_better": higher_is_better,
            "binary": binary,
        }
        unit = _optional_token(raw_metric.get("unit"), field="metric.unit")
        if unit is not None:
            metric["unit"] = unit
        metrics.append(metric)
    if sum(metric["role"] == "primary" for metric in metrics) != 1:
        raise ValueError("manifest must declare exactly one primary metric")

    raw_revisions = payload.get("source_revisions")
    if (
        not isinstance(raw_revisions, list)
        or not raw_revisions
        or len(raw_revisions) > 16
    ):
        raise ValueError("source_revisions must contain 1 to 16 entries")
    source_revisions: list[dict[str, str]] = []
    revision_components: set[str] = set()
    for raw_revision in raw_revisions:
        if not isinstance(raw_revision, Mapping):
            raise TypeError("source_revisions entries must be objects")
        _reject_unknown_fields(
            raw_revision,
            allowed={"component", "revision"},
            field="source_revision",
        )
        component = _token(
            raw_revision.get("component"), field="source_revision.component"
        )
        if component in revision_components:
            raise ValueError("source revision components must be unique")
        revision_components.add(component)
        source_revisions.append(
            {
                "component": component,
                "revision": _token(
                    raw_revision.get("revision"), field="source_revision.revision"
                ),
            }
        )

    labels = payload.get("labels", {})
    if not isinstance(labels, Mapping) or len(labels) > 8:
        raise TypeError("labels must be a small object")
    normalized_labels = {
        _token(key, field="labels key"): _bounded_text(
            value, field=f"labels.{key}", limit=160
        )
        for key, value in labels.items()
    }

    return {
        "schema_version": BENCHMARK_STUDY_MANIFEST_SCHEMA_VERSION,
        "benchmark_id": _token(payload.get("benchmark_id"), field="benchmark_id"),
        "study_id": _token(payload.get("study_id"), field="study_id"),
        "protocol_id": _token(payload.get("protocol_id"), field="protocol_id"),
        "comparison_protocol_id": _token(
            payload.get("comparison_protocol_id"), field="comparison_protocol_id"
        ),
        "case_set": {
            "case_set_id": _token(
                case_set.get("case_set_id"), field="case_set.case_set_id"
            ),
            "case_ids": case_ids,
        },
        "factors": factors,
        "arms": arms,
        "baseline_arm_id": baseline_arm_id,
        "metrics": metrics,
        "source_revisions": source_revisions,
        "labels": dict(sorted(normalized_labels.items())),
        "extension_metadata": _normalize_extension_metadata(
            payload.get("extension_metadata")
        ),
        "privacy_classification": "public_safe",
    }


def normalize_benchmark_case_insight_projection(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a redacted post-run insight; raw evidence has no schema slot."""

    if not isinstance(payload, Mapping):
        raise TypeError("benchmark case insight projection must be an object")
    _reject_unknown_fields(
        payload,
        allowed={
            "schema_version",
            "benchmark_id",
            "study_id",
            "case_id",
            "run_id",
            "outcome_status",
            "failure_class",
            "causal_summary",
            "expectedness",
            "implication",
            "next_probe",
            "confidence",
            "evidence_refs",
            "privacy_classification",
            "producer_redaction_attested",
        },
        field="case insight projection",
    )
    if (
        payload.get("schema_version")
        != BENCHMARK_CASE_INSIGHT_PROJECTION_SCHEMA_VERSION
    ):
        raise ValueError("benchmark case insight projection schema mismatch")
    if payload.get("privacy_classification") != "public_safe":
        raise ValueError("case insight projection must be public_safe")
    if payload.get("producer_redaction_attested") is not True:
        raise ValueError(
            "case insight projection requires producer redaction attestation"
        )
    expectedness = _token(payload.get("expectedness"), field="expectedness")
    confidence = _token(payload.get("confidence"), field="confidence")
    if expectedness not in _EXPECTEDNESS or confidence not in _CONFIDENCE_LEVELS:
        raise ValueError("case insight expectedness or confidence is unsupported")
    outcome_status = _token(payload.get("outcome_status"), field="outcome_status")
    if not benchmark_run_status_is_terminal(outcome_status):
        raise ValueError("case insight outcome_status must be terminal")
    raw_refs = payload.get("evidence_refs", [])
    if not isinstance(raw_refs, list) or len(raw_refs) > 16:
        raise ValueError("evidence_refs must contain at most 16 handles")
    evidence_refs = [_token(item, field="evidence_refs") for item in raw_refs]
    return {
        "schema_version": BENCHMARK_CASE_INSIGHT_PROJECTION_SCHEMA_VERSION,
        "benchmark_id": _token(payload.get("benchmark_id"), field="benchmark_id"),
        "study_id": _token(payload.get("study_id"), field="study_id"),
        "case_id": _token(payload.get("case_id"), field="case_id"),
        "run_id": _token(payload.get("run_id"), field="run_id"),
        "outcome_status": outcome_status,
        "failure_class": _token(payload.get("failure_class"), field="failure_class"),
        "causal_summary": _bounded_text(
            payload.get("causal_summary"), field="causal_summary"
        ),
        "expectedness": expectedness,
        "implication": _bounded_text(payload.get("implication"), field="implication"),
        "next_probe": _bounded_text(payload.get("next_probe"), field="next_probe"),
        "confidence": confidence,
        "evidence_refs": evidence_refs,
        "privacy_classification": "public_safe",
        "producer_redaction_attested": True,
    }


def _normalize_runtime_observation(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != BENCHMARK_RUNTIME_OBSERVATION_SCHEMA_VERSION:
        raise ValueError("benchmark runtime observation schema mismatch")
    normalized = build_benchmark_runtime_observation(
        admission_active=payload.get("admission_active"),
        job_receipt_state=payload.get("job_receipt_state"),
        runner_owner_state=payload.get("runner_owner_state"),
        terminal_result_present=payload.get("terminal_result_present"),
        typed_fatal_runner_error=payload.get("typed_fatal_runner_error"),
    )
    if dict(payload) != normalized:
        raise ValueError(
            "runtime observation must match the canonical public projection"
        )
    return normalized


def _normalize_record_payload(record_kind: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("upload payload must be an object")
    if record_kind == "study_manifest":
        return normalize_benchmark_study_manifest(payload)
    if record_kind == "experiment_board_row":
        return normalize_benchmark_experiment_board_row(payload)
    if record_kind == "case_insight_projection":
        return normalize_benchmark_case_insight_projection(payload)
    if record_kind == "runtime_observation":
        return _normalize_runtime_observation(payload)
    if record_kind == "behavior_finding":
        from .behavior_finding import normalize_benchmark_behavior_finding

        return normalize_benchmark_behavior_finding(payload)
    raise ValueError("upload record kind is unsupported")


def build_benchmark_upload_envelope(
    payload: Mapping[str, Any],
    *,
    record_kind: str,
    producer_id: str,
    producer_version: str,
    benchmark_id: str,
    study_id: str,
    idempotency_key: str,
    observed_at: str,
    source_revision: str,
    supersedes_record_id: str | None = None,
) -> dict[str, Any]:
    """Build one digest-bound envelope. No provider or network call occurs."""

    kind = _token(record_kind, field="record_kind")
    if kind not in _RECORD_KINDS:
        raise ValueError("upload record kind is unsupported")
    normalized_payload = _normalize_record_payload(kind, payload)
    benchmark = _token(benchmark_id, field="benchmark_id")
    study = _token(study_id, field="study_id")
    for field in ("benchmark_id", "study_id"):
        if field in normalized_payload and normalized_payload[field] != (
            benchmark if field == "benchmark_id" else study
        ):
            raise ValueError(f"upload envelope {field} does not match payload")
    key = _token(idempotency_key, field="idempotency_key")
    identity = {
        "producer_id": _token(producer_id, field="producer_id"),
        "benchmark_id": benchmark,
        "study_id": study,
        "idempotency_key": key,
    }
    record_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    envelope: dict[str, Any] = {
        "schema_version": BENCHMARK_UPLOAD_ENVELOPE_SCHEMA_VERSION,
        "record_id": record_id,
        "producer_id": identity["producer_id"],
        "producer_version": _token(producer_version, field="producer_version"),
        "benchmark_id": benchmark,
        "study_id": study,
        "record_kind": kind,
        "idempotency_key": key,
        "observed_at": _timestamp(observed_at, field="observed_at"),
        "source_revision": _token(source_revision, field="source_revision"),
        "privacy_classification": "public_safe",
        "payload_digest": _canonical_digest(normalized_payload),
        "payload": normalized_payload,
    }
    supersedes = _optional_token(supersedes_record_id, field="supersedes_record_id")
    if supersedes is not None:
        envelope["supersedes_record_id"] = supersedes
    return envelope


def normalize_benchmark_upload_envelope(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("benchmark upload envelope must be an object")
    _reject_unknown_fields(
        payload,
        allowed={
            "schema_version",
            "record_id",
            "producer_id",
            "producer_version",
            "benchmark_id",
            "study_id",
            "record_kind",
            "idempotency_key",
            "observed_at",
            "source_revision",
            "privacy_classification",
            "payload_digest",
            "payload",
            "supersedes_record_id",
        },
        field="upload envelope",
    )
    if payload.get("schema_version") != BENCHMARK_UPLOAD_ENVELOPE_SCHEMA_VERSION:
        raise ValueError("benchmark upload envelope schema mismatch")
    if payload.get("privacy_classification") != "public_safe":
        raise ValueError("benchmark upload envelope must be public_safe")
    rebuilt = build_benchmark_upload_envelope(
        payload.get("payload"),
        record_kind=str(payload.get("record_kind") or ""),
        producer_id=str(payload.get("producer_id") or ""),
        producer_version=str(payload.get("producer_version") or ""),
        benchmark_id=str(payload.get("benchmark_id") or ""),
        study_id=str(payload.get("study_id") or ""),
        idempotency_key=str(payload.get("idempotency_key") or ""),
        observed_at=str(payload.get("observed_at") or ""),
        source_revision=str(payload.get("source_revision") or ""),
        supersedes_record_id=payload.get("supersedes_record_id"),
    )
    if payload.get("record_id") != rebuilt["record_id"]:
        raise ValueError("benchmark upload record_id does not match envelope identity")
    digest = str(payload.get("payload_digest") or "")
    if not _DIGEST_RE.fullmatch(digest) or digest != rebuilt["payload_digest"]:
        raise ValueError("benchmark upload payload digest mismatch")
    return rebuilt


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid local upload JSONL row {index}") from exc
        if not isinstance(row, dict):
            raise TypeError(f"invalid local upload JSONL row {index}")
        rows.append(row)
    return rows


def read_benchmark_local_upload_records(
    store_path: str | Path,
) -> list[dict[str, Any]]:
    records = []
    for row in _read_jsonl_objects(Path(store_path).expanduser()):
        _reject_unknown_fields(
            row,
            allowed={"schema_version", "provider_revision", "envelope"},
            field="local upload record",
        )
        if row.get("schema_version") != BENCHMARK_LOCAL_UPLOAD_RECORD_SCHEMA_VERSION:
            raise ValueError("local upload record schema mismatch")
        revision = row.get("provider_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("local upload provider_revision must be positive")
        records.append(
            {
                "schema_version": BENCHMARK_LOCAL_UPLOAD_RECORD_SCHEMA_VERSION,
                "provider_revision": revision,
                "envelope": normalize_benchmark_upload_envelope(row.get("envelope")),
            }
        )
    if [row["provider_revision"] for row in records] != list(
        range(1, len(records) + 1)
    ):
        raise ValueError("local upload provider revisions must be contiguous")
    return records


def _same_board_row(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return benchmark_experiment_board_row_key(
        left
    ) == benchmark_experiment_board_row_key(right)


def _validate_case_insight_attachment(
    envelope: Mapping[str, Any], active_envelopes: Iterable[Mapping[str, Any]]
) -> None:
    if envelope["record_kind"] != "case_insight_projection":
        return
    insight = envelope["payload"]
    board_rows = [
        candidate["payload"]
        for candidate in active_envelopes
        if candidate["record_kind"] == "experiment_board_row"
        and candidate["benchmark_id"] == envelope["benchmark_id"]
        and candidate["study_id"] == envelope["study_id"]
        and candidate["payload"]["run_id"] == insight["run_id"]
    ]
    if len(board_rows) != 1:
        raise ValueError(
            "case-insight upload requires one active exact-run board record"
        )
    run = board_rows[0]
    if run["case_id"] != insight["case_id"]:
        raise ValueError("case insight identity does not match its board row")
    if not benchmark_run_status_is_terminal(run["status"]):
        raise ValueError("case-insight upload requires a terminal run")
    if run["status"] != insight["outcome_status"]:
        raise ValueError("case insight outcome does not match its terminal run")
    if run["insight"]["status"] != "complete":
        raise ValueError(
            "case-insight upload requires complete post-run analysis status"
        )


def _validate_case_insight_attachments(
    records: Iterable[Mapping[str, Any]],
) -> None:
    active_envelopes = _active_envelopes(records)
    for envelope in active_envelopes:
        _validate_case_insight_attachment(envelope, active_envelopes)


def _validate_supersession(
    envelope: Mapping[str, Any], records: list[dict[str, Any]]
) -> None:
    by_id = {row["envelope"]["record_id"]: row for row in records}
    supersedes = envelope.get("supersedes_record_id")
    prior = by_id.get(supersedes) if supersedes else None
    if envelope["record_kind"] == "study_manifest" and any(
        row["envelope"]["record_kind"] == "study_manifest"
        and row["envelope"]["benchmark_id"] == envelope["benchmark_id"]
        and row["envelope"]["study_id"] == envelope["study_id"]
        and row["envelope"]["record_id"] != envelope["record_id"]
        for row in records
    ):
        raise ValueError(
            "study manifest comparison intent is immutable; use a new study_id"
        )
    if supersedes and prior is None:
        raise ValueError("superseded upload record does not exist")
    if supersedes == envelope["record_id"]:
        raise ValueError("an upload record cannot supersede itself")
    superseded_ids = {
        row["envelope"]["supersedes_record_id"]
        for row in records
        if row["envelope"].get("supersedes_record_id")
    }
    if supersedes in superseded_ids:
        raise ValueError("upload must supersede the current active record")
    if prior is not None:
        old = prior["envelope"]
        for field in ("producer_id", "benchmark_id", "study_id", "record_kind"):
            if old[field] != envelope[field]:
                raise ValueError("supersession identity does not match prior record")
        if envelope["record_kind"] == "experiment_board_row":
            if not _same_board_row(old["payload"], envelope["payload"]):
                raise ValueError("board-row supersession must preserve run identity")
            preview_benchmark_experiment_board_upsert(
                [old["payload"]], envelope["payload"]
            )
        elif envelope["record_kind"] == "case_insight_projection":
            for field in ("case_id", "run_id"):
                if old["payload"][field] != envelope["payload"][field]:
                    raise ValueError(
                        "case-insight supersession must preserve case and run identity"
                    )
        elif envelope["record_kind"] == "behavior_finding":
            if old["payload"]["finding_id"] != envelope["payload"]["finding_id"]:
                raise ValueError("finding supersession must preserve finding identity")

    if envelope["record_kind"] == "behavior_finding":
        related = [
            e for e in _active_envelopes(records)
            if e["record_kind"] == "behavior_finding"
            and e["benchmark_id"] == envelope["benchmark_id"]
            and e["study_id"] == envelope["study_id"]
            and e["payload"]["finding_id"] == envelope["payload"]["finding_id"]
        ]
        if related and supersedes not in {e["record_id"] for e in related}:
            raise ValueError("changed finding upload requires explicit supersession")

    if envelope["record_kind"] == "experiment_board_row":
        related = [
            row["envelope"]
            for row in records
            if row["envelope"]["record_kind"] == "experiment_board_row"
            and _same_board_row(row["envelope"]["payload"], envelope["payload"])
            and row["envelope"]["record_id"] != envelope["record_id"]
        ]
        if related and not supersedes:
            raise ValueError("changed board-row upload requires explicit supersession")
        if related and supersedes not in {item["record_id"] for item in related}:
            raise ValueError("board-row upload must supersede its current run record")
    _validate_case_insight_attachments([*records, {"envelope": envelope}])


def _receipt(
    envelope: Mapping[str, Any], *, revision: int, disposition: str, wrote: bool
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": BENCHMARK_UPLOAD_READBACK_RECEIPT_SCHEMA_VERSION,
        "provider_id": "local_simulation",
        "record_id": envelope["record_id"],
        "payload_digest": envelope["payload_digest"],
        "provider_revision": revision,
        "disposition": disposition,
        "write_performed": wrote,
        "external_write_performed": False,
        "network_access_performed": False,
        "path_recorded": False,
    }


def simulate_benchmark_upload(
    store_path: str | Path,
    envelope: Mapping[str, Any],
    *,
    execute: bool = False,
) -> dict[str, Any]:
    """Preview or append to an explicit local JSONL provider simulation."""

    normalized = normalize_benchmark_upload_envelope(envelope)
    path = Path(store_path).expanduser()
    if not execute:
        records = read_benchmark_local_upload_records(path)
        existing = next(
            (
                row
                for row in records
                if row["envelope"]["record_id"] == normalized["record_id"]
            ),
            None,
        )
        if existing is not None:
            stored = existing["envelope"]
            if (
                stored["record_kind"] != normalized["record_kind"]
                or stored["payload_digest"] != normalized["payload_digest"]
            ):
                raise ValueError("idempotency key was reused with different content")
            return _receipt(
                stored,
                revision=existing["provider_revision"],
                disposition="replayed",
                wrote=False,
            )
        _validate_supersession(normalized, records)
        return _receipt(
            normalized,
            revision=len(records) + 1,
            disposition="preview_accepted",
            wrote=False,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        records = read_benchmark_local_upload_records(path)
        existing = next(
            (
                row
                for row in records
                if row["envelope"]["record_id"] == normalized["record_id"]
            ),
            None,
        )
        if existing is not None:
            stored = existing["envelope"]
            if (
                stored["record_kind"] != normalized["record_kind"]
                or stored["payload_digest"] != normalized["payload_digest"]
            ):
                raise ValueError("idempotency key was reused with different content")
            return _receipt(
                stored,
                revision=existing["provider_revision"],
                disposition="replayed",
                wrote=False,
            )
        _validate_supersession(normalized, records)
        record = {
            "schema_version": BENCHMARK_LOCAL_UPLOAD_RECORD_SCHEMA_VERSION,
            "provider_revision": len(records) + 1,
            "envelope": normalized,
        }
        rows = [*records, record]
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            for row in rows:
                temporary.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                )
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    return _receipt(
        normalized,
        revision=record["provider_revision"],
        disposition="accepted",
        wrote=True,
    )


def read_benchmark_upload_receipt(
    store_path: str | Path, *, record_id: str
) -> dict[str, Any]:
    requested = _token(record_id, field="record_id")
    records = read_benchmark_local_upload_records(store_path)
    selected = next(
        (row for row in records if row["envelope"]["record_id"] == requested), None
    )
    if selected is None:
        raise ValueError("benchmark upload record was not found")
    superseded_by = next(
        (
            row["envelope"]["record_id"]
            for row in records
            if row["envelope"].get("supersedes_record_id") == requested
        ),
        None,
    )
    receipt = _receipt(
        selected["envelope"],
        revision=selected["provider_revision"],
        disposition="readback_verified",
        wrote=False,
    )
    receipt["superseded"] = superseded_by is not None
    if superseded_by is not None:
        receipt["superseded_by"] = superseded_by
    return receipt


def _active_envelopes(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in records:
        candidate = row.get("envelope") if "envelope" in row else row
        normalized.append(normalize_benchmark_upload_envelope(candidate))
    superseded = {
        envelope["supersedes_record_id"]
        for envelope in normalized
        if envelope.get("supersedes_record_id")
    }
    return [
        envelope for envelope in normalized if envelope["record_id"] not in superseded
    ]


def _validate_four_arm_manifest_alignment(
    study: Mapping[str, Any], four_arm_contract: Mapping[str, Any]
) -> None:
    """Prevent a valid four-arm contract from being attached to another design."""

    if (
        four_arm_contract.get("schema_version")
        != BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION
    ):
        raise ValueError("four-arm contract schema mismatch")
    raw_arms = four_arm_contract.get("arms")
    if not isinstance(raw_arms, list):
        raise TypeError("four-arm contract arms must be a list")
    study_arms = {arm["arm_id"]: arm["arm_role"] for arm in study["arms"]}
    contract_arms = {
        str(arm.get("arm_id") or ""): str(arm.get("arm_role") or "")
        for arm in raw_arms
        if isinstance(arm, Mapping)
    }
    if contract_arms != study_arms:
        raise ValueError("four-arm contract arms do not match the study manifest")


def _metric_aggregate(
    rows: list[Mapping[str, Any]], metric_name: str
) -> dict[str, Any]:
    metrics = [
        row["metrics"][metric_name]
        for row in rows
        if metric_name in row.get("metrics", {})
    ]
    values = [float(metric["value"]) for metric in metrics]
    totals = [metric.get("total") for metric in metrics]
    result: dict[str, Any] = {
        "case_denominator": len(metrics),
        "value_sum": sum(values),
        "value_mean": statistics.fmean(values) if values else None,
        "value_median": statistics.median(values) if values else None,
        "value_min": min(values) if values else None,
        "value_max": max(values) if values else None,
    }
    if metrics and all(
        isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0
        for total in totals
    ):
        rates = [value / float(total) for value, total in zip(values, totals)]
        total_sum = sum(float(total) for total in totals)
        result.update(
            {
                "case_macro_rate": statistics.fmean(rates),
                "suite_micro_rate": sum(values) / total_sum,
                "suite_micro_numerator": sum(values),
                "suite_micro_denominator": total_sum,
            }
        )
    return result


def _effort_aggregate(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for field in (
        "duration_ms",
        "agent_steps",
        "goal_turns",
        "token_count",
        "estimated_cost_usd",
    ):
        values = [
            float(row["effort"][field])
            for row in rows
            if field in row.get("effort", {})
        ]
        output[field] = {
            "denominator": len(values),
            "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
        }
    return output


def build_benchmark_study_dashboard(
    manifest: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
    *,
    four_arm_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive a read-only data packet from one manifest and uploaded records."""

    study = normalize_benchmark_study_manifest(manifest)
    if four_arm_contract is not None:
        _validate_four_arm_manifest_alignment(study, four_arm_contract)
    envelopes = [
        envelope
        for envelope in _active_envelopes(records)
        if envelope["benchmark_id"] == study["benchmark_id"]
        and envelope["study_id"] == study["study_id"]
    ]
    uploaded_manifests = [
        envelope["payload"]
        for envelope in envelopes
        if envelope["record_kind"] == "study_manifest"
    ]
    if len(uploaded_manifests) > 1:
        raise ValueError("study upload store has multiple active manifests")
    if uploaded_manifests and uploaded_manifests[0] != study:
        raise ValueError(
            "study upload store manifest does not match dashboard manifest"
        )
    board_envelopes = [
        envelope
        for envelope in envelopes
        if envelope["record_kind"] == "experiment_board_row"
    ]
    board_rows = [envelope["payload"] for envelope in board_envelopes]
    board_provenance = {
        envelope["payload"]["run_id"]: {
            key: envelope[key]
            for key in (
                "record_id",
                "producer_id",
                "producer_version",
                "observed_at",
                "source_revision",
                "payload_digest",
            )
        }
        for envelope in board_envelopes
    }
    declared_cases = set(study["case_set"]["case_ids"])
    arms_by_id = {arm["arm_id"]: arm for arm in study["arms"]}
    metric_by_name = {metric["metric_name"]: metric for metric in study["metrics"]}
    primary_metric = next(
        metric["metric_name"]
        for metric in study["metrics"]
        if metric["role"] == "primary"
    )
    for row in board_rows:
        if row["case_id"] not in declared_cases or row["arm_id"] not in arms_by_id:
            raise ValueError("board row is outside manifest coverage")
        if row["primary_metric"] != primary_metric:
            raise ValueError("board row primary metric does not match manifest")
        if set(row["metrics"]) - set(metric_by_name):
            raise ValueError("board row includes a metric absent from manifest catalog")
        for metric_name, value in row["metrics"].items():
            declared = metric_by_name[metric_name]
            if value.get("higher_is_better") != declared["higher_is_better"]:
                raise ValueError("board row metric direction does not match manifest")
            if value.get("unit") != declared.get("unit"):
                raise ValueError("board row metric unit does not match manifest")
            if declared["binary"] and value["value"] not in {0, 1}:
                raise ValueError("binary benchmark metric must be 0 or 1")
        if row["comparison_protocol_id"] != study["comparison_protocol_id"]:
            raise ValueError("board row comparison protocol does not match manifest")

    insight_envelopes = [
        envelope
        for envelope in envelopes
        if envelope["record_kind"] == "case_insight_projection"
    ]
    insights = {
        envelope["payload"]["run_id"]: envelope["payload"]
        for envelope in insight_envelopes
    }
    insight_records = [envelope["payload"] for envelope in insight_envelopes]
    if len(insights) != len(insight_records):
        raise ValueError("study upload store has multiple active insights for one run")
    for insight_envelope in insight_envelopes:
        insight = insight_envelope["payload"]
        if insight["case_id"] not in declared_cases:
            raise ValueError("case insight is outside manifest coverage")
        _validate_case_insight_attachment(insight_envelope, envelopes)
    runtime_observations = [
        envelope["payload"]
        for envelope in envelopes
        if envelope["record_kind"] == "runtime_observation"
    ]
    board = build_benchmark_experiment_board(
        board_rows,
        benchmark_id=study["benchmark_id"],
        study_id=study["study_id"],
        four_arm_contract=four_arm_contract,
    )

    cells: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in board["runs"]:
        cells.setdefault((row["case_id"], row["arm_id"]), []).append(row)
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    ambiguous_cells = 0
    for key, candidates in cells.items():
        countable = [
            row for row in candidates if row["countability"]["score_countable"]
        ]
        if len(countable) == 1:
            selected[key] = countable[0]
        elif len(countable) > 1:
            ambiguous_cells += 1

    arm_details = []
    for arm_id, arm in arms_by_id.items():
        arm_rows = [
            row
            for (case_id, candidate_arm), row in selected.items()
            if candidate_arm == arm_id
        ]
        all_arm_rows = [row for row in board["runs"] if row["arm_id"] == arm_id]
        binary = {}
        for metric_name, metric in metric_by_name.items():
            if not metric["binary"]:
                continue
            values = [
                float(row["metrics"][metric_name]["value"])
                for row in arm_rows
                if metric_name in row["metrics"]
            ]
            binary[metric_name] = {
                "success_count": sum(value == 1 for value in values),
                "case_denominator": len(values),
                "success_rate": (
                    sum(value == 1 for value in values) / len(values)
                    if values
                    else None
                ),
            }
        arm_details.append(
            {
                **arm,
                "protocol_counts": dict(
                    sorted(
                        {
                            protocol: sum(
                                row["protocol_id"] == protocol for row in all_arm_rows
                            )
                            for protocol in {row["protocol_id"] for row in all_arm_rows}
                        }.items()
                    )
                ),
                "runner_revision_counts": dict(
                    sorted(
                        {
                            revision: sum(
                                row.get("runner_revision") == revision
                                for row in all_arm_rows
                            )
                            for revision in {
                                row.get("runner_revision") for row in all_arm_rows
                            }
                            if revision
                        }.items()
                    )
                ),
                "orchestrator_runtime_counts": dict(
                    sorted(
                        {
                            json.dumps(runtime, sort_keys=True): sum(
                                row.get("orchestrator_runtime") == runtime
                                for row in all_arm_rows
                            )
                            for runtime in [
                                row.get("orchestrator_runtime")
                                for row in all_arm_rows
                                if row.get("orchestrator_runtime")
                            ]
                        }.items()
                    )
                ),
                "intended_case_count": len(declared_cases),
                "run_count": len(all_arm_rows),
                "terminal_run_count": sum(
                    row["status"] in {"completed", "runner_invalid", "cancelled"}
                    for row in all_arm_rows
                ),
                "selected_score_countable_case_count": len(arm_rows),
                "coverage_rate": len(arm_rows) / len(declared_cases),
                "metrics": {
                    metric_name: _metric_aggregate(arm_rows, metric_name)
                    for metric_name in metric_by_name
                },
                "binary_outcomes": binary,
                "effort": _effort_aggregate(arm_rows),
                "failure_class_counts": dict(
                    sorted(
                        {
                            failure: sum(
                                insights.get(row["run_id"], {}).get("failure_class")
                                == failure
                                for row in arm_rows
                            )
                            for failure in {
                                insights.get(row["run_id"], {}).get("failure_class")
                                for row in arm_rows
                            }
                            if failure
                        }.items()
                    )
                ),
            }
        )

    case_matrix = []
    complete_design_case_count = 0
    for case_id in study["case_set"]["case_ids"]:
        arm_cells = []
        for arm_id in arms_by_id:
            row = selected.get((case_id, arm_id))
            arm_cells.append(
                {
                    "arm_id": arm_id,
                    "selected_run_id": row["run_id"] if row else None,
                    "score_countable": row is not None,
                    "metrics": row["metrics"] if row else {},
                    "effort": row["effort"] if row else {},
                    "insight": insights.get(row["run_id"]) if row else None,
                }
            )
        complete = all(cell["score_countable"] for cell in arm_cells)
        complete_design_case_count += int(complete)
        case_matrix.append(
            {
                "case_id": case_id,
                "complete_declared_design": complete,
                "arms": arm_cells,
            }
        )

    countable_comparisons = [
        comparison
        for comparison in board["comparisons"]
        if comparison["matched_pair_countable"]
    ]
    comparisons_by_case: dict[str, list[dict[str, Any]]] = {}
    for comparison in countable_comparisons:
        comparisons_by_case.setdefault(comparison["case_id"], []).append(comparison)
    for case in case_matrix:
        eligible = comparisons_by_case.get(case["case_id"], [])
        ranked = [
            comparison
            for comparison in eligible
            if isinstance(
                comparison.get("metric_deltas", {})
                .get(primary_metric, {})
                .get("delta"),
                (int, float),
            )
        ]
        largest = max(
            ranked,
            key=lambda item: abs(item["metric_deltas"][primary_metric]["delta"]),
            default=None,
        )
        case["eligible_comparisons"] = eligible
        case["largest_eligible_primary_contrast"] = largest
    contrast_summary: dict[str, Any] = {}
    for candidate_arm in arms_by_id:
        items = [
            comparison
            for comparison in countable_comparisons
            if comparison["candidate_arm_id"] == candidate_arm
        ]
        if not items:
            continue
        directions = {"improved": 0, "flat": 0, "regressed": 0}
        binary_transitions = {
            metric_name: {"0_to_1": 0, "1_to_0": 0, "same": 0}
            for metric_name, metric in metric_by_name.items()
            if metric["binary"]
        }
        for item in items:
            primary = item.get("metric_deltas", {}).get(primary_metric, {})
            direction = primary.get("direction")
            if direction in directions:
                directions[direction] += 1
            for metric_name, transitions in binary_transitions.items():
                binary_delta = item.get("metric_deltas", {}).get(metric_name)
                if not isinstance(binary_delta, Mapping):
                    continue
                before, after = (
                    binary_delta.get("baseline_value"),
                    binary_delta.get("candidate_value"),
                )
                if before == 0 and after == 1:
                    transitions["0_to_1"] += 1
                elif before == 1 and after == 0:
                    transitions["1_to_0"] += 1
                elif before == after:
                    transitions["same"] += 1
        contrast_summary[candidate_arm] = {
            "matched_pair_denominator": len(items),
            "primary_metric_directions": directions,
            "binary_metric_transitions": binary_transitions,
        }

    intended_cells = len(declared_cases) * len(arms_by_id)
    selected_count = len(selected)
    runtime_counts = {
        classification: sum(
            item["classification"] == classification for item in runtime_observations
        )
        for classification in sorted(
            {item["classification"] for item in runtime_observations}
        )
    }
    return {
        "ok": True,
        "schema_version": BENCHMARK_STUDY_DASHBOARD_SCHEMA_VERSION,
        "benchmark_id": study["benchmark_id"],
        "study_id": study["study_id"],
        "design": {
            "protocol_id": study["protocol_id"],
            "comparison_protocol_id": study["comparison_protocol_id"],
            "case_set": study["case_set"],
            "factors": study["factors"],
            "baseline_arm_id": study["baseline_arm_id"],
            "metric_catalog": study["metrics"],
            "source_revisions": study["source_revisions"],
            "labels": study["labels"],
            "extension_metadata": study["extension_metadata"],
        },
        "status": "complete" if selected_count == intended_cells else "provisional",
        "campaign": {
            "intended_case_count": len(declared_cases),
            "intended_arm_count": len(arms_by_id),
            "intended_cell_denominator": intended_cells,
            "selected_score_countable_cell_count": selected_count,
            "selected_score_countable_coverage_rate": selected_count / intended_cells,
            "complete_declared_design_case_count": complete_design_case_count,
            "ambiguous_score_countable_cell_count": ambiguous_cells,
            "in_flight_run_count": sum(
                row["status"] == "running" for row in board["runs"]
            ),
            "matched_pair_countable_count": len(countable_comparisons),
            "factorial_contrast_count": len(board["factorial_contrasts"]),
            "factorial_contrast_countable_count": sum(
                item["factorial_contrast_countable"]
                for item in board["factorial_contrasts"]
            ),
            "runtime_observation_count": len(runtime_observations),
            "runtime_classification_counts": runtime_counts,
        },
        "arms": arm_details,
        "contrasts": contrast_summary,
        "factorial_contrasts": board["factorial_contrasts"],
        "cases": case_matrix,
        "runs": [
            {
                **row,
                "redacted_insight": insights.get(row["run_id"]),
                "upload_provenance": board_provenance[row["run_id"]],
            }
            for row in board["runs"]
        ],
        "authority": {
            "score_source": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
            "matched_comparison_source": board["schema_version"],
            "factorial_comparison_source": (
                board["factorial_contrasts"][0]["schema_version"]
                if board["factorial_contrasts"]
                else None
            ),
            "manifest_changes_scores": False,
            "dashboard_is_execution_authority": False,
        },
        "public_boundary": {
            "raw_task_recorded": False,
            "raw_trajectory_recorded": False,
            "hidden_evaluation_recorded": False,
            "raw_verifier_output_recorded": False,
            "credentials_recorded": False,
            "local_paths_recorded": False,
        },
        "write_performed": False,
        "network_access_performed": False,
    }
