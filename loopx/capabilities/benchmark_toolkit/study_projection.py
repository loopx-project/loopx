"""Provider-neutral benchmark study, upload, and dashboard contracts."""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from ...control_plane.content_digest import BARE_SHA256_PATTERN
from ...file_lock import exclusive_file_lock
from .experiment_board import (
    BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
    _finite_number,
    _optional_token,
    _reject_unknown_fields,
    _timestamp,
    benchmark_experiment_board_row_key,
    benchmark_run_status_is_terminal,
    build_benchmark_experiment_board,
    normalize_benchmark_experiment_board_row,
    preview_benchmark_experiment_board_upsert,
)
from .experiment_identity import (
    ARM_ROLES,
    experiment_run_key,
    experiment_token_text as _token,
)
from .factorial_contrast import benchmark_metric_comparison_value
from .four_arm_contract import BENCHMARK_FOUR_ARM_CONTRACT_SCHEMA_VERSION
from .runtime_observation import (
    BENCHMARK_RUNTIME_OBSERVATION_SCHEMA_VERSION,
    build_benchmark_runtime_observation,
)
from .token_capacity import aggregate_context_policy_effort

Payload = dict[str, object]
PayloadView = Mapping[str, object]

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


def _as_map(value: object) -> Payload:
    return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}


def _as_map_list(value: object) -> list[Payload]:
    return (
        [_as_map(item) for item in value if isinstance(item, Mapping)]
        if isinstance(value, list)
        else []
    )


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _sorted_value_counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _bounded_text(value: object, *, field: str, limit: int = 600) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or "\x00" in text:
        raise ValueError(f"{field} must be bounded non-empty text")
    return text


def _canonical_digest(payload: PayloadView) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public_scalar(value: object, *, field: str) -> str | int | float | bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _finite_number(value, field=field)
    return _token(value, field=field)


def _normalize_extension_metadata(value: object) -> Payload:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping) or len(value) > 16:
        raise TypeError("extension_metadata must be a small object")
    output: Payload = {}
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
    payload: PayloadView,
) -> Payload:
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
    case_set_view = _as_map(case_set)
    _reject_unknown_fields(
        case_set_view,
        allowed={"case_set_id", "case_ids"},
        field="case_set",
    )
    raw_case_ids = case_set_view.get("case_ids")
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
    factors: list[Payload] = []
    levels_by_factor: dict[str, set[str]] = {}
    for raw_factor in raw_factors:
        if not isinstance(raw_factor, Mapping):
            raise TypeError("factors entries must be objects")
        factor_view = _as_map(raw_factor)
        _reject_unknown_fields(
            factor_view,
            allowed={"factor_id", "levels"},
            field="factor",
        )
        factor_id = _token(factor_view.get("factor_id"), field="factor.factor_id")
        raw_levels = factor_view.get("levels")
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
    arms: list[Payload] = []
    arm_ids: set[str] = set()
    assignments_seen: set[tuple[tuple[str, str], ...]] = set()
    for raw_arm in raw_arms:
        if not isinstance(raw_arm, Mapping):
            raise TypeError("arms entries must be objects")
        arm_view = _as_map(raw_arm)
        _reject_unknown_fields(
            arm_view,
            allowed={"arm_id", "arm_role", "factor_assignments"},
            field="arm",
        )
        arm_id = _token(arm_view.get("arm_id"), field="arm.arm_id")
        arm_role = _token(arm_view.get("arm_role"), field="arm.arm_role")
        if arm_role not in ARM_ROLES:
            raise ValueError("arm.arm_role is unsupported")
        raw_assignments = arm_view.get("factor_assignments")
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
    metrics: list[Payload] = []
    metric_names: set[str] = set()
    for raw_metric in raw_metrics:
        if not isinstance(raw_metric, Mapping):
            raise TypeError("metrics entries must be objects")
        metric_view = _as_map(raw_metric)
        _reject_unknown_fields(
            metric_view,
            allowed={"metric_name", "role", "unit", "higher_is_better", "binary"},
            field="metric",
        )
        metric_name = _token(metric_view.get("metric_name"), field="metric.metric_name")
        role = _token(metric_view.get("role"), field="metric.role")
        if role not in _METRIC_ROLES:
            raise ValueError("metric.role is unsupported")
        higher_is_better = metric_view.get("higher_is_better")
        binary = metric_view.get("binary", False)
        if not isinstance(higher_is_better, bool) or not isinstance(binary, bool):
            raise TypeError("metric direction and binary flags must be boolean")
        if metric_name in metric_names:
            raise ValueError("metric names must be unique")
        metric_names.add(metric_name)
        metric: Payload = {
            "metric_name": metric_name,
            "role": role,
            "higher_is_better": higher_is_better,
            "binary": binary,
        }
        unit = _optional_token(metric_view.get("unit"), field="metric.unit")
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
        revision_view = _as_map(raw_revision)
        _reject_unknown_fields(
            revision_view,
            allowed={"component", "revision"},
            field="source_revision",
        )
        component = _token(
            revision_view.get("component"), field="source_revision.component"
        )
        if component in revision_components:
            raise ValueError("source revision components must be unique")
        revision_components.add(component)
        source_revisions.append(
            {
                "component": component,
                "revision": _token(
                    revision_view.get("revision"), field="source_revision.revision"
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
                case_set_view.get("case_set_id"), field="case_set.case_set_id"
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
    payload: PayloadView,
) -> Payload:
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
    if payload.get("schema_version") != BENCHMARK_CASE_INSIGHT_PROJECTION_SCHEMA_VERSION:
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


def _require_bool_arg(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field}_must_be_boolean")
    return value


def _require_str_arg(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid_{field}")
    return value


def _normalize_runtime_observation(payload: PayloadView) -> Payload:
    if payload.get("schema_version") != BENCHMARK_RUNTIME_OBSERVATION_SCHEMA_VERSION:
        raise ValueError("benchmark runtime observation schema mismatch")
    normalized = build_benchmark_runtime_observation(
        admission_active=_require_bool_arg(
            payload.get("admission_active"), field="admission_active"
        ),
        job_receipt_state=_require_str_arg(
            payload.get("job_receipt_state"), field="job_receipt_state"
        ),
        runner_owner_state=_require_str_arg(
            payload.get("runner_owner_state"), field="runner_owner_state"
        ),
        terminal_result_present=_require_bool_arg(
            payload.get("terminal_result_present"), field="terminal_result_present"
        ),
        typed_fatal_runner_error=_require_bool_arg(
            payload.get("typed_fatal_runner_error"), field="typed_fatal_runner_error"
        ),
    )
    if dict(payload) != normalized:
        raise ValueError(
            "runtime observation must match the canonical public projection"
        )
    return dict(normalized)


def _normalize_record_payload(record_kind: str, payload: object) -> Payload:
    if not isinstance(payload, Mapping):
        raise TypeError("upload payload must be an object")
    payload_view = _as_map(payload)
    if record_kind == "study_manifest":
        return normalize_benchmark_study_manifest(payload_view)
    if record_kind == "experiment_board_row":
        return normalize_benchmark_experiment_board_row(payload_view)
    if record_kind == "case_insight_projection":
        return normalize_benchmark_case_insight_projection(payload_view)
    if record_kind == "runtime_observation":
        return _normalize_runtime_observation(payload_view)
    if record_kind == "behavior_finding":
        from .behavior_finding import normalize_benchmark_behavior_finding

        return dict(normalize_benchmark_behavior_finding(payload_view))
    raise ValueError("upload record kind is unsupported")


def build_benchmark_upload_envelope(
    payload: object,
    *,
    record_kind: str,
    producer_id: str,
    producer_version: str,
    benchmark_id: str,
    study_id: str,
    idempotency_key: str,
    observed_at: str,
    source_revision: str,
    supersedes_record_id: object = None,
) -> Payload:
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
    envelope: Payload = {
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
    payload: object,
) -> Payload:
    if not isinstance(payload, Mapping):
        raise TypeError("benchmark upload envelope must be an object")
    view = _as_map(payload)
    _reject_unknown_fields(
        view,
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
    if view.get("schema_version") != BENCHMARK_UPLOAD_ENVELOPE_SCHEMA_VERSION:
        raise ValueError("benchmark upload envelope schema mismatch")
    if view.get("privacy_classification") != "public_safe":
        raise ValueError("benchmark upload envelope must be public_safe")
    rebuilt = build_benchmark_upload_envelope(
        view.get("payload"),
        record_kind=str(view.get("record_kind") or ""),
        producer_id=str(view.get("producer_id") or ""),
        producer_version=str(view.get("producer_version") or ""),
        benchmark_id=str(view.get("benchmark_id") or ""),
        study_id=str(view.get("study_id") or ""),
        idempotency_key=str(view.get("idempotency_key") or ""),
        observed_at=str(view.get("observed_at") or ""),
        source_revision=str(view.get("source_revision") or ""),
        supersedes_record_id=view.get("supersedes_record_id"),
    )
    if view.get("record_id") != rebuilt["record_id"]:
        raise ValueError("benchmark upload record_id does not match envelope identity")
    digest = str(view.get("payload_digest") or "")
    if not BARE_SHA256_PATTERN.fullmatch(digest) or digest != rebuilt["payload_digest"]:
        raise ValueError("benchmark upload payload digest mismatch")
    return rebuilt


def _read_jsonl_objects(path: Path) -> list[Payload]:
    if not path.exists():
        return []
    rows: list[Payload] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid local upload JSONL row {index}") from exc
        if not isinstance(row, dict):
            raise TypeError(f"invalid local upload JSONL row {index}")
        rows.append(_as_map(row))
    return rows


def read_benchmark_local_upload_records(
    store_path: str | Path,
) -> list[Payload]:
    records: list[Payload] = []
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
    if [_as_int(row["provider_revision"]) for row in records] != list(
        range(1, len(records) + 1)
    ):
        raise ValueError("local upload provider revisions must be contiguous")
    return records


def _same_board_row(left: PayloadView, right: PayloadView) -> bool:
    return benchmark_experiment_board_row_key(
        left
    ) == benchmark_experiment_board_row_key(right)


def _validate_case_insight_attachment(
    envelope: PayloadView, active_envelopes: Iterable[PayloadView]
) -> None:
    if envelope["record_kind"] != "case_insight_projection":
        return
    insight = _as_map(envelope["payload"])
    board_rows = [
        _as_map(candidate["payload"])
        for candidate in active_envelopes
        if candidate["record_kind"] == "experiment_board_row"
        and experiment_run_key(_as_map(candidate["payload"]))
        == experiment_run_key(insight)
    ]
    if len(board_rows) != 1:
        raise ValueError(
            "case-insight upload requires one active exact-run board record"
        )
    run = board_rows[0]
    if not benchmark_run_status_is_terminal(run["status"]):
        raise ValueError("case-insight upload requires a terminal run")
    if run["status"] != insight["outcome_status"]:
        raise ValueError("case insight outcome does not match its terminal run")
    if _as_map(run.get("insight")).get("status") != "complete":
        raise ValueError(
            "case-insight upload requires complete post-run analysis status"
        )


def _validate_case_insight_attachments(
    records: Iterable[PayloadView],
) -> None:
    active_envelopes = _active_envelopes(records)
    for envelope in active_envelopes:
        _validate_case_insight_attachment(envelope, active_envelopes)


def _validate_supersession(
    envelope: PayloadView, records: Sequence[PayloadView]
) -> None:
    envelopes = [_as_map(row.get("envelope")) for row in records]
    by_id = {str(item["record_id"]): item for item in envelopes}
    supersedes = envelope.get("supersedes_record_id")
    prior = by_id.get(str(supersedes)) if isinstance(supersedes, str) and supersedes else None
    if envelope["record_kind"] == "study_manifest" and any(
        item["record_kind"] == "study_manifest"
        and item["benchmark_id"] == envelope["benchmark_id"]
        and item["study_id"] == envelope["study_id"]
        and item["record_id"] != envelope["record_id"]
        for item in envelopes
    ):
        raise ValueError(
            "study manifest comparison intent is immutable; use a new study_id"
        )
    if supersedes and prior is None:
        raise ValueError("superseded upload record does not exist")
    if supersedes == envelope["record_id"]:
        raise ValueError("an upload record cannot supersede itself")
    superseded_ids = {
        item["supersedes_record_id"]
        for item in envelopes
        if item.get("supersedes_record_id")
    }
    if supersedes in superseded_ids:
        raise ValueError("upload must supersede the current active record")
    envelope_payload = _as_map(envelope["payload"])
    if prior is not None:
        for field in ("producer_id", "benchmark_id", "study_id", "record_kind"):
            if prior[field] != envelope[field]:
                raise ValueError("supersession identity does not match prior record")
        old_payload = _as_map(prior["payload"])
        if envelope["record_kind"] == "experiment_board_row":
            if not _same_board_row(old_payload, envelope_payload):
                raise ValueError("board-row supersession must preserve run identity")
            preview_benchmark_experiment_board_upsert([old_payload], envelope_payload)
        elif envelope["record_kind"] == "case_insight_projection":
            for field in ("case_id", "run_id"):
                if old_payload[field] != envelope_payload[field]:
                    raise ValueError(
                        "case-insight supersession must preserve case and run identity"
                    )
        elif envelope["record_kind"] == "behavior_finding":
            if old_payload["finding_id"] != envelope_payload["finding_id"]:
                raise ValueError("finding supersession must preserve finding identity")

    if envelope["record_kind"] == "behavior_finding":
        related = [
            item
            for item in _active_envelopes(records)
            if item["record_kind"] == "behavior_finding"
            and item["benchmark_id"] == envelope["benchmark_id"]
            and item["study_id"] == envelope["study_id"]
            and _as_map(item["payload"]).get("finding_id")
            == envelope_payload.get("finding_id")
        ]
        if related and supersedes not in {item["record_id"] for item in related}:
            raise ValueError("changed finding upload requires explicit supersession")

    if envelope["record_kind"] == "experiment_board_row":
        related = [
            item
            for item in envelopes
            if item["record_kind"] == "experiment_board_row"
            and _same_board_row(_as_map(item["payload"]), envelope_payload)
            and item["record_id"] != envelope["record_id"]
        ]
        if related and not supersedes:
            raise ValueError("changed board-row upload requires explicit supersession")
        if related and supersedes not in {item["record_id"] for item in related}:
            raise ValueError("board-row upload must supersede its current run record")
    _validate_case_insight_attachments([*records, {"envelope": envelope}])


def _receipt(
    envelope: PayloadView, *, revision: int, disposition: str, wrote: bool
) -> Payload:
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


def _resolve_upload_candidate(
    path: Path, normalized: PayloadView
) -> tuple[list[Payload], Payload | None]:
    records = read_benchmark_local_upload_records(path)
    existing = next(
        (
            row
            for row in records
            if _as_map(row.get("envelope")).get("record_id") == normalized["record_id"]
        ),
        None,
    )
    if existing is not None:
        stored = _as_map(existing["envelope"])
        if (
            stored["record_kind"] != normalized["record_kind"]
            or stored["payload_digest"] != normalized["payload_digest"]
        ):
            raise ValueError("idempotency key was reused with different content")
        return records, _receipt(
            stored,
            revision=_as_int(existing["provider_revision"]),
            disposition="replayed",
            wrote=False,
        )
    _validate_supersession(normalized, records)
    return records, None


def simulate_benchmark_upload(
    store_path: str | Path,
    envelope: PayloadView,
    *,
    execute: bool = False,
) -> Payload:
    """Preview or append to an explicit local JSONL provider simulation."""

    normalized = normalize_benchmark_upload_envelope(envelope)
    path = Path(store_path).expanduser()
    if not execute:
        records, replay = _resolve_upload_candidate(path, normalized)
        return replay or _receipt(
            normalized,
            revision=len(records) + 1,
            disposition="preview_accepted",
            wrote=False,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        records, replay = _resolve_upload_candidate(path, normalized)
        if replay is not None:
            return replay
        revision = len(records) + 1
        record: Payload = {
            "schema_version": BENCHMARK_LOCAL_UPLOAD_RECORD_SCHEMA_VERSION,
            "provider_revision": revision,
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
        revision=revision,
        disposition="accepted",
        wrote=True,
    )


def read_benchmark_upload_receipt(
    store_path: str | Path, *, record_id: str
) -> Payload:
    requested = _token(record_id, field="record_id")
    records = read_benchmark_local_upload_records(store_path)
    selected = next(
        (
            row
            for row in records
            if _as_map(row.get("envelope")).get("record_id") == requested
        ),
        None,
    )
    if selected is None:
        raise ValueError("benchmark upload record was not found")
    selected_envelope = _as_map(selected["envelope"])
    superseded_by = next(
        (
            str(_as_map(row.get("envelope")).get("record_id") or "")
            for row in records
            if _as_map(row.get("envelope")).get("supersedes_record_id") == requested
        ),
        None,
    )
    receipt = _receipt(
        selected_envelope,
        revision=_as_int(selected["provider_revision"]),
        disposition="readback_verified",
        wrote=False,
    )
    receipt["superseded"] = superseded_by is not None
    if superseded_by is not None:
        receipt["superseded_by"] = superseded_by
    return receipt


def _active_envelopes(records: Iterable[PayloadView]) -> list[Payload]:
    normalized = [
        normalize_benchmark_upload_envelope(
            row.get("envelope") if "envelope" in row else row
        )
        for row in records
    ]
    superseded = {
        envelope["supersedes_record_id"]
        for envelope in normalized
        if envelope.get("supersedes_record_id")
    }
    return [
        envelope for envelope in normalized if envelope["record_id"] not in superseded
    ]


def _validate_four_arm_manifest_alignment(
    study: PayloadView, four_arm_contract: PayloadView
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
    study_arms = {
        str(arm["arm_id"]): str(arm["arm_role"])
        for arm in _as_map_list(study.get("arms"))
    }
    contract_arms = {
        str(arm.get("arm_id") or ""): str(arm.get("arm_role") or "")
        for arm in raw_arms
        if isinstance(arm, Mapping)
    }
    if contract_arms != study_arms:
        raise ValueError("four-arm contract arms do not match the study manifest")


def _metric_aggregate(
    rows: Sequence[PayloadView], metric_name: str
) -> Payload:
    metrics = [
        _as_map(row_metrics[metric_name])
        for row in rows
        if metric_name in (row_metrics := _as_map(row.get("metrics")))
    ]
    values = [
        float(metric_value)
        for metric in metrics
        if isinstance(metric_value := metric.get("value"), (int, float))
        and not isinstance(metric_value, bool)
    ]
    totals = [metric.get("total") for metric in metrics]
    result: Payload = {
        "case_denominator": len(metrics),
        "value_sum": sum(values),
        "value_mean": statistics.fmean(values) if values else None,
        "value_median": statistics.median(values) if values else None,
        "value_min": min(values) if values else None,
        "value_max": max(values) if values else None,
    }
    typed_totals = [
        float(total)
        for total in totals
        if isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0
    ]
    if metrics and len(typed_totals) == len(totals):
        rates = [value / total for value, total in zip(values, typed_totals)]
        total_sum = sum(typed_totals)
        result.update(
            {
                "case_macro_rate": statistics.fmean(rates),
                "suite_micro_rate": sum(values) / total_sum,
                "suite_micro_numerator": sum(values),
                "suite_micro_denominator": total_sum,
            }
        )
    return result


def _effort_aggregate(rows: Sequence[PayloadView]) -> Payload:
    output: Payload = {}
    for field in (
        "duration_ms",
        "agent_steps",
        "goal_turns",
        "token_count",
        "estimated_cost_usd",
    ):
        values = [
            float(val)
            for row in rows
            if isinstance(val := _as_map(row.get("effort")).get(field), (int, float))
            and not isinstance(val, bool)
        ]
        output[field] = {
            "denominator": len(values),
            "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
        }
    output.update(aggregate_context_policy_effort(rows))
    return output


def build_benchmark_study_dashboard(
    manifest: PayloadView,
    records: Iterable[PayloadView],
    *,
    four_arm_contract: PayloadView | None = None,
) -> Payload:
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
        _as_map(envelope["payload"])
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
    board_rows = [_as_map(envelope["payload"]) for envelope in board_envelopes]
    board_provenance = {
        experiment_run_key(_as_map(envelope["payload"])): {
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
    case_set = _as_map(study["case_set"])
    raw_case_ids = case_set.get("case_ids")
    declared_case_ids = [
        str(cid) for cid in (raw_case_ids if isinstance(raw_case_ids, list) else [])
    ]
    declared_cases = set(declared_case_ids)
    arms_by_id = {str(arm["arm_id"]): arm for arm in _as_map_list(study["arms"])}
    study_metrics = _as_map_list(study["metrics"])
    metric_by_name = {str(metric["metric_name"]): metric for metric in study_metrics}
    primary_metric = next(
        str(metric["metric_name"])
        for metric in study_metrics
        if metric["role"] == "primary"
    )
    for row in board_rows:
        if row["case_id"] not in declared_cases or row["arm_id"] not in arms_by_id:
            raise ValueError("board row is outside manifest coverage")
        if row["primary_metric"] != primary_metric:
            raise ValueError("board row primary metric does not match manifest")
        row_metrics = _as_map(row["metrics"])
        if set(row_metrics) - set(metric_by_name):
            raise ValueError("board row includes a metric absent from manifest catalog")
        for metric_name, raw_val in row_metrics.items():
            value_map = _as_map(raw_val)
            declared = metric_by_name[metric_name]
            if value_map.get("higher_is_better") != declared["higher_is_better"]:
                raise ValueError("board row metric direction does not match manifest")
            if value_map.get("unit") != declared.get("unit"):
                raise ValueError("board row metric unit does not match manifest")
            if declared["binary"] and value_map.get("value") not in {0, 1}:
                raise ValueError("binary benchmark metric must be 0 or 1")
        if row["comparison_protocol_id"] != study["comparison_protocol_id"]:
            raise ValueError("board row comparison protocol does not match manifest")

    insight_envelopes = [
        envelope
        for envelope in envelopes
        if envelope["record_kind"] == "case_insight_projection"
    ]
    insights = {
        experiment_run_key(_as_map(envelope["payload"])): _as_map(envelope["payload"])
        for envelope in insight_envelopes
    }
    if len(insights) != len(insight_envelopes):
        raise ValueError("study upload store has multiple active insights for one run")
    for insight_envelope in insight_envelopes:
        insight = _as_map(insight_envelope["payload"])
        if insight["case_id"] not in declared_cases:
            raise ValueError("case insight is outside manifest coverage")
        _validate_case_insight_attachment(insight_envelope, envelopes)
    runtime_observations = [
        _as_map(envelope["payload"])
        for envelope in envelopes
        if envelope["record_kind"] == "runtime_observation"
    ]
    board = build_benchmark_experiment_board(
        board_rows,
        benchmark_id=str(study["benchmark_id"]),
        study_id=str(study["study_id"]),
        four_arm_contract=four_arm_contract,
    )
    board_run_rows = _as_map_list(board.get("runs"))
    board_comparison_rows = _as_map_list(board.get("comparisons"))
    board_contrast_rows = _as_map_list(board.get("factorial_contrasts"))

    cells: dict[tuple[str, str], list[Payload]] = {}
    for row in board_run_rows:
        cells.setdefault((str(row["case_id"]), str(row["arm_id"])), []).append(row)
    selected: dict[tuple[str, str], Payload] = {}
    ambiguous_cells = 0
    for key, candidates in cells.items():
        countable = [
            row
            for row in candidates
            if _as_map(row.get("countability")).get("score_countable")
        ]
        if len(countable) == 1:
            selected[key] = countable[0]
        elif len(countable) > 1:
            ambiguous_cells += 1

    arm_details: list[Payload] = []
    for arm_id, arm in arms_by_id.items():
        arm_rows = [
            row
            for (_case_id, candidate_arm), row in selected.items()
            if candidate_arm == arm_id
        ]
        all_arm_rows = [row for row in board_run_rows if row["arm_id"] == arm_id]
        binary: Payload = {}
        for metric_name, metric in metric_by_name.items():
            if not metric["binary"]:
                continue
            values = [
                float(val)
                for row in arm_rows
                if metric_name in (rm := _as_map(row.get("metrics")))
                and isinstance(val := _as_map(rm[metric_name]).get("value"), (int, float))
                and not isinstance(val, bool)
            ]
            successes = sum(value == 1 for value in values)
            binary[metric_name] = {
                "success_count": successes,
                "case_denominator": len(values),
                "success_rate": successes / len(values) if values else None,
            }
        arm_details.append(
            {
                **arm,
                "protocol_counts": _sorted_value_counts(
                    str(row["protocol_id"]) for row in all_arm_rows
                ),
                "runner_revision_counts": _sorted_value_counts(
                    str(rev)
                    for row in all_arm_rows
                    if (rev := row.get("runner_revision"))
                ),
                "orchestrator_runtime_counts": _sorted_value_counts(
                    json.dumps(rt, sort_keys=True)
                    for row in all_arm_rows
                    if (rt := row.get("orchestrator_runtime"))
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
                "failure_class_counts": _sorted_value_counts(
                    str(fc)
                    for row in arm_rows
                    if (fc := _as_map(insights.get(experiment_run_key(row))).get("failure_class"))
                ),
            }
        )

    case_matrix: list[Payload] = []
    complete_design_case_count = 0
    for case_id in declared_case_ids:
        arm_cells: list[Payload] = []
        for arm_id in arms_by_id:
            selected_row = selected.get((case_id, arm_id))
            arm_cells.append(
                {
                    "arm_id": arm_id,
                    "selected_run_id": selected_row["run_id"] if selected_row else None,
                    "score_countable": selected_row is not None,
                    "metrics": selected_row["metrics"] if selected_row else {},
                    "effort": selected_row["effort"] if selected_row else {},
                    "insight": insights.get(experiment_run_key(selected_row)) if selected_row else None,
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
        for comparison in board_comparison_rows
        if comparison["matched_pair_countable"]
    ]
    comparisons_by_case: dict[str, list[Payload]] = {}
    for comparison in countable_comparisons:
        comparisons_by_case.setdefault(str(comparison["case_id"]), []).append(comparison)
    for case in case_matrix:
        eligible = comparisons_by_case.get(str(case["case_id"]), [])
        ranked = [
            comparison
            for comparison in eligible
            if benchmark_metric_comparison_value(
                _as_map(_as_map(comparison.get("metric_deltas")).get(primary_metric))
            )
            is not None
        ]
        # A raw count and a rate have no meaningful shared magnitude ordering.
        scales = {
            "delta_rate" in _as_map(_as_map(item.get("metric_deltas")).get(primary_metric))
            for item in ranked
        }
        if len(scales) > 1:
            ranked = []
        largest = max(
            ranked,
            key=lambda item: abs(
                benchmark_metric_comparison_value(
                    _as_map(_as_map(item.get("metric_deltas")).get(primary_metric))
                )
                or 0
            ),
            default=None,
        )
        case["eligible_comparisons"] = eligible
        case["largest_eligible_primary_contrast"] = largest

    contrast_summary: Payload = {}
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
            deltas = _as_map(item.get("metric_deltas"))
            direction = _as_map(deltas.get(primary_metric)).get("direction")
            if isinstance(direction, str) and direction in directions:
                directions[direction] += 1
            for metric_name, transitions in binary_transitions.items():
                binary_delta = deltas.get(metric_name)
                if not isinstance(binary_delta, Mapping) or binary_delta.get(
                    "comparison_unavailable_reason"
                ):
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
    runtime_counts = _sorted_value_counts(
        str(item["classification"]) for item in runtime_observations
    )
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
                row["status"] == "running" for row in board_run_rows
            ),
            "matched_pair_countable_count": len(countable_comparisons),
            "factorial_contrast_count": len(board_contrast_rows),
            "factorial_contrast_countable_count": sum(
                bool(item["factorial_contrast_countable"])
                for item in board_contrast_rows
            ),
            "runtime_observation_count": len(runtime_observations),
            "runtime_classification_counts": runtime_counts,
        },
        "arms": arm_details,
        "contrasts": contrast_summary,
        "factorial_contrasts": board_contrast_rows,
        "cases": case_matrix,
        "runs": [
            {
                **row,
                "redacted_insight": insights.get(experiment_run_key(row)),
                "upload_provenance": board_provenance[experiment_run_key(row)],
            }
            for row in board_run_rows
        ],
        "authority": {
            "score_source": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
            "matched_comparison_source": board["schema_version"],
            "factorial_comparison_source": (
                board_contrast_rows[0]["schema_version"]
                if board_contrast_rows
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
