from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Iterable

from ..control_plane.runtime.public_safety import REMOTE_LOCATION_SURFACE_PATTERN
from ..domain_state import default_domain_state_file_path, upsert_domain_state_jsonl


DOMAIN_PACK_CONTRACT_SCHEMA_VERSION = "domain_pack_contract_v0"
ML_EXPERIMENT_ADVISORY_PACKET_SCHEMA_VERSION = "ml_experiment_advisory_packet_v0"
ML_EXPERIMENT_RESULT_SCHEMA_VERSION = "ml_experiment_result_v0"
DATASET_WINDOW_CONTRACT_SCHEMA_VERSION = "dataset_window_contract_v0"
HYPOTHESIS_LEDGER_SCHEMA_VERSION = "hypothesis_ledger_v0"
EXPERIMENT_REPLAN_SCHEMA_VERSION = "experiment_replan_v0"
VOLC_MLP_TASK_PACKET_SCHEMA_VERSION = "volc_mlp_task_packet_v0"
VOLC_MLP_RESULT_LEDGER_SCHEMA_VERSION = "volc_mlp_result_ledger_v0"
ML_EXPERIMENT_DOMAIN_STATE_LEDGER_FILENAME = "ledger.jsonl"

GUARDRAIL_STATUSES = ("clean", "warning", "failed", "unknown")
HYPOTHESIS_STATUSES = ("active", "supported", "weakened", "retired", "unknown")
VOLC_MLP_TASK_STATES = (
    "Creating",
    "Waiting",
    "Queueing",
    "Deploying",
    "Running",
    "Stopping",
    "Completed",
    "Failed",
    "Stopped",
    "Unknown",
)

_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_ABSOLUTE_PATH_RE = re.compile(
    r"(^|[\s:=])(?:"
    + "/" + "Users/"
    + "|/private/|/tmp/|~[/\\s]|[A-Za-z]:\\\\)"
)
_PRIVATE_MARKER_TERMS = [
    "author" + "ization:",
    r"bearer\s+[A-Za-z0-9._-]+",
    r"api[_-]?" + "key",
    "pass" + "word",
    "sec" + "ret",
    r"begin (?:rsa |open)?private " + "key",
    "lark" + "office",
    r"fei" + r"shu\.cn",
    "byte" + "dance",
]
_PRIVATE_MARKER_RE = re.compile(r"(?i)(" + "|".join(_PRIVATE_MARKER_TERMS) + ")")


def _compact_public_text(value: str, *, field: str, max_len: int = 160) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(f"{field} must be non-empty")
    if len(text) > max_len:
        raise ValueError(f"{field} is too long for a compact public-safe field")
    if ".." in text:
        raise ValueError(f"{field} must not contain parent-directory markers")
    if _ABSOLUTE_PATH_RE.search(text) or text.startswith(("/", "~")):
        raise ValueError(f"{field} must use a public alias, not a local/private path")
    if REMOTE_LOCATION_SURFACE_PATTERN.search(text):
        raise ValueError(f"{field} must use a public alias, not a raw URL or remote path")
    if _PRIVATE_MARKER_RE.search(text):
        raise ValueError(f"{field} contains a private or credential-like marker")
    return text


def _compact_public_token(value: str, *, field: str) -> str:
    token = _compact_public_text(value, field=field, max_len=80)
    if not _TOKEN_RE.match(token):
        raise ValueError(
            f"{field} must be a compact public token using letters, digits, dot, colon, dash, or underscore"
        )
    return token


def _compact_public_text_list(values: Iterable[str] | None, *, field: str) -> list[str]:
    return [_compact_public_text(value, field=f"{field}[]") for value in values or []]


def _finite_float(value: float | int | str, *, field: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _non_negative_int(value: int | str | None, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    number = int(value)
    if number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number


def _redacted_ref(value: str, *, field: str) -> dict[str, Any]:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError(f"{field} must be non-empty")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return {
        "kind": "redacted_ref",
        "value": f"redacted:{digest}",
        "raw_recorded": False,
    }


def _public_or_redacted_ref(value: str | None, *, field: str) -> dict[str, Any] | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return {
            "kind": "alias",
            "value": _compact_public_text(str(value), field=field),
            "raw_recorded": False,
        }
    except ValueError:
        return _redacted_ref(str(value), field=field)


def _public_or_redacted_ref_list(values: Iterable[str] | None, *, field: str) -> list[dict[str, Any]]:
    return [
        ref
        for ref in (_public_or_redacted_ref(value, field=f"{field}[]") for value in values or [])
        if ref is not None
    ]


def build_ml_experiment_domain_pack_contract(*, enabled: bool = False) -> dict[str, Any]:
    """Return the default-off ML experiment domain-pack boundary."""

    return {
        "schema_version": DOMAIN_PACK_CONTRACT_SCHEMA_VERSION,
        "pack": "ml_experiment",
        "enabled": bool(enabled),
        "autonomy": "suggest_only" if not enabled else "advisory",
        "allowed_actions": [
            "ingest_metrics",
            "classify_results",
            "write_hypothesis_ledger",
            "propose_replan",
        ],
        "disabled_actions": [
            "launch_training_job",
            "stop_training_job",
            "restart_training_job",
            "sync_to_production",
            "select_primary_metric_without_authority",
        ],
        "capability_requirements": [
            "compact_public_metric_artifact",
            "dataset_window_contract",
            "hypothesis_ledger",
        ],
        "authority_boundary": {
            "primary_metric_authority": "explicit_board_or_owner_decision_only",
            "launch_authority": "disabled_until_explicit_registry_delivery_mode",
            "production_authority": "disabled_until_explicit_registry_delivery_mode",
        },
    }


def build_volc_mlp_task_packet(
    *,
    task_id: str,
    task_name: str,
    state: str = "Unknown",
    priority: int | str | None = None,
    retried_times: int | str | None = None,
    train_window: str,
    eval_window: str,
    code_ref: str,
    model_name: str,
    mechanism_family: str = "unknown",
    source_task_id: str | None = None,
    workspace_ref: str | None = None,
    metric_refs: Iterable[str] | None = None,
    primary_metric: str = "offline_auc",
    guardrail_metrics: Iterable[str] | None = None,
    next_action: str = "monitor_task_until_terminal_metrics",
) -> dict[str, Any]:
    """Build a public-safe Volc/MLP task fact packet.

    The packet intentionally stores task identity, windows, lineage aliases, and
    redacted artifact handles only. It must not become a raw launcher spec,
    credential store, command log, environment dump, or private path ledger.
    """

    compact_state = _compact_public_text(state, field="state", max_len=40)
    if compact_state not in VOLC_MLP_TASK_STATES:
        raise ValueError(f"state must be one of {', '.join(VOLC_MLP_TASK_STATES)}")
    dataset_window = build_dataset_window_contract(
        train_window=train_window,
        eval_window=eval_window,
    )
    source_handle = (
        _compact_public_token(source_task_id, field="source_task_id")
        if source_task_id
        else None
    )
    workspace_handle = _public_or_redacted_ref(workspace_ref, field="workspace_ref")
    metric_handles = _public_or_redacted_ref_list(metric_refs, field="metric_refs")
    return {
        "ok": True,
        "schema_version": VOLC_MLP_TASK_PACKET_SCHEMA_VERSION,
        "provider": "volc_mlp",
        "pack": build_ml_experiment_domain_pack_contract(enabled=False),
        "mode": "default_off_external_task_fact_packet",
        "observable_handle": {
            "task_id": _compact_public_token(task_id, field="task_id"),
            "task_name": _compact_public_text(task_name, field="task_name"),
            "state": compact_state,
            "priority": _non_negative_int(priority, field="priority"),
            "retried_times": _non_negative_int(retried_times, field="retried_times"),
            "source_task_id": source_handle,
        },
        "dataset_window": dataset_window,
        "lineage": {
            "code_ref": _compact_public_text(code_ref, field="code_ref"),
            "model_name": _compact_public_token(model_name, field="model_name"),
            "mechanism_family": _compact_public_text(mechanism_family, field="mechanism_family"),
            "workspace_ref": workspace_handle,
        },
        "metric_artifacts": metric_handles,
        "decision_boundary": {
            "primary_metric": _compact_public_text(primary_metric, field="primary_metric"),
            "guardrail_metrics": _compact_public_text_list(guardrail_metrics, field="guardrail_metrics"),
            "conclusion_eligibility": "aligned_eval_window_and_guardrails_required",
            "train_metrics_are_guardrails_only": True,
        },
        "poll_contract": {
            "allowed_observations": [
                "task_state",
                "workspace_created",
                "fail_marker",
                "done_marker",
                "compact_train_metrics",
                "compact_eval_metrics",
            ],
            "raw_logs_recorded": False,
            "raw_command_recorded": False,
            "raw_env_recorded": False,
            "private_artifacts_recorded": False,
        },
        "launch_actions_enabled": False,
        "production_actions_enabled": False,
        "recommended_next_action": _compact_public_text(next_action, field="next_action"),
    }


def _optional_primary_metric_delta(
    *,
    baseline_value: float | int | str | None,
    candidate_value: float | int | str | None,
    higher_is_better: bool,
) -> dict[str, Any]:
    if baseline_value is None and candidate_value is None:
        return {
            "baseline_value": None,
            "candidate_value": None,
            "delta": None,
            "relative_delta": None,
            "higher_is_better": bool(higher_is_better),
            "primary_metric_status": "pending",
        }
    if baseline_value is None or candidate_value is None:
        raise ValueError("baseline_value and candidate_value must be provided together")
    return classify_primary_metric_delta(
        baseline_value=baseline_value,
        candidate_value=candidate_value,
        higher_is_better=higher_is_better,
    )


def _volc_result_outcome(
    *,
    state: str,
    metric_status: str,
    guardrail_status: str,
    failure_labels: list[str],
) -> str:
    if failure_labels or state in {"Failed", "Stopped"}:
        return "needs_repair_before_conclusion"
    if metric_status == "pending":
        return "monitor_until_aligned_eval"
    if guardrail_status == "failed":
        return "no_promote_guardrail_failed"
    if metric_status == "regressed":
        return "no_promote_metric_regressed"
    if metric_status == "flat":
        return "retire_or_replan"
    if metric_status == "improved" and guardrail_status == "clean":
        return "promote_to_larger_window_or_handoff"
    return "inconclusive_guardrail_review"


def _default_volc_result_next_action(outcome: str) -> str:
    if outcome == "monitor_until_aligned_eval":
        return "poll_until_terminal_aligned_eval"
    if outcome == "promote_to_larger_window_or_handoff":
        return "promote_after_owner_or_registry_gate"
    if outcome == "needs_repair_before_conclusion":
        return "repair_startup_or_eval_path_before_retry"
    if outcome.startswith("no_promote"):
        return "retire_candidate_and_avoid_near_neighbor_retry"
    if outcome == "retire_or_replan":
        return "retire_candidate_or_design_distinct_mechanism"
    return "collect_guardrail_evidence_before_decision"


def build_volc_mlp_result_ledger(
    *,
    experiment_id: str,
    task_id: str,
    task_name: str,
    state: str = "Unknown",
    priority: int | str | None = None,
    retried_times: int | str | None = None,
    train_window: str,
    eval_window: str,
    code_ref: str,
    model_name: str,
    mechanism_family: str = "unknown",
    primary_metric: str = "offline_auc",
    baseline_value: float | int | str | None = None,
    candidate_value: float | int | str | None = None,
    higher_is_better: bool = True,
    guardrail_status: str = "unknown",
    baseline_task_id: str | None = None,
    source_task_id: str | None = None,
    workspace_ref: str | None = None,
    metric_refs: Iterable[str] | None = None,
    guardrail_metrics: Iterable[str] | None = None,
    positive_evidence: Iterable[str] | None = None,
    negative_evidence: Iterable[str] | None = None,
    failure_labels: Iterable[str] | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    """Build a public-safe result ledger row for a Volc/MLP task.

    This is the bridge from an external task packet to LoopX's benchmark-ledger
    reasoning: same-window comparison, train-metric guardrails, failure
    attribution, and promotion/no-promotion routing. It remains observation-only
    and deliberately avoids raw commands, env dumps, logs, private paths, and
    credentials.
    """

    compact_state = _compact_public_text(state, field="state", max_len=40)
    if compact_state not in VOLC_MLP_TASK_STATES:
        raise ValueError(f"state must be one of {', '.join(VOLC_MLP_TASK_STATES)}")
    compact_guardrail_status = _compact_public_token(guardrail_status, field="guardrail_status")
    if compact_guardrail_status not in GUARDRAIL_STATUSES:
        raise ValueError(f"guardrail_status must be one of {', '.join(GUARDRAIL_STATUSES)}")
    compact_failures = _compact_public_text_list(failure_labels, field="failure_labels")
    metric = _optional_primary_metric_delta(
        baseline_value=baseline_value,
        candidate_value=candidate_value,
        higher_is_better=higher_is_better,
    )
    outcome = _volc_result_outcome(
        state=compact_state,
        metric_status=str(metric.get("primary_metric_status")),
        guardrail_status=compact_guardrail_status,
        failure_labels=compact_failures,
    )
    resolved_next_action = next_action or _default_volc_result_next_action(outcome)
    task_packet = build_volc_mlp_task_packet(
        task_id=task_id,
        task_name=task_name,
        state=compact_state,
        priority=priority,
        retried_times=retried_times,
        train_window=train_window,
        eval_window=eval_window,
        code_ref=code_ref,
        model_name=model_name,
        mechanism_family=mechanism_family,
        source_task_id=source_task_id or baseline_task_id,
        workspace_ref=workspace_ref,
        metric_refs=metric_refs,
        primary_metric=primary_metric,
        guardrail_metrics=guardrail_metrics,
        next_action=resolved_next_action,
    )
    baseline_handle = (
        _compact_public_token(baseline_task_id, field="baseline_task_id")
        if baseline_task_id
        else None
    )
    positive_labels = _compact_public_text_list(positive_evidence, field="positive_evidence")
    negative_labels = _compact_public_text_list(negative_evidence, field="negative_evidence")
    promotion_eligible = outcome == "promote_to_larger_window_or_handoff"
    return {
        "ok": True,
        "schema_version": VOLC_MLP_RESULT_LEDGER_SCHEMA_VERSION,
        "provider": "volc_mlp",
        "pack": build_ml_experiment_domain_pack_contract(enabled=False),
        "mode": "default_off_external_result_ledger",
        "experiment_id": _compact_public_token(experiment_id, field="experiment_id"),
        "task_packet": task_packet,
        "comparison": {
            "baseline_task_id": baseline_handle,
            "primary_metric": _compact_public_text(primary_metric, field="primary_metric"),
            "primary_metric_delta": metric,
            "guardrail_status": compact_guardrail_status,
            "guardrail_metrics": _compact_public_text_list(guardrail_metrics, field="guardrail_metrics"),
            "same_window_required": True,
            "aligned_eval_required_for_conclusion": True,
            "train_metrics_are_guardrails_only": True,
        },
        "evidence": {
            "positive": positive_labels,
            "negative": negative_labels,
            "raw_metrics_recorded": False,
            "private_artifacts_recorded": False,
        },
        "failure_attribution": {
            "labels": compact_failures,
            "raw_logs_recorded": False,
            "raw_command_recorded": False,
            "raw_env_recorded": False,
        },
        "decision": {
            "outcome": outcome,
            "promotion_eligible": promotion_eligible,
            "requires_owner_or_registry_gate": promotion_eligible,
            "avoid_near_neighbor_retry": outcome.startswith("no_promote"),
            "recommended_next_action": _compact_public_text(
                resolved_next_action,
                field="next_action",
            ),
        },
        "launch_actions_enabled": False,
        "production_actions_enabled": False,
    }


def _volc_payload_task_id(payload: dict[str, Any]) -> str | None:
    if payload.get("schema_version") == VOLC_MLP_TASK_PACKET_SCHEMA_VERSION:
        handle = payload.get("observable_handle")
    else:
        task_packet = payload.get("task_packet") if isinstance(payload.get("task_packet"), dict) else {}
        handle = task_packet.get("observable_handle") if isinstance(task_packet.get("observable_handle"), dict) else {}
    if not isinstance(handle, dict):
        return None
    task_id = handle.get("task_id")
    return str(task_id) if task_id else None


def ml_experiment_ledger_key(payload: dict[str, Any]) -> dict[str, str | None]:
    """Return the stable public key used for JSONL upserts."""

    schema_version = str(payload.get("schema_version") or "")
    if schema_version not in {
        VOLC_MLP_TASK_PACKET_SCHEMA_VERSION,
        VOLC_MLP_RESULT_LEDGER_SCHEMA_VERSION,
    }:
        raise ValueError("unsupported ML experiment ledger payload schema")
    provider = str(payload.get("provider") or "")
    task_id = _volc_payload_task_id(payload)
    if not provider or not task_id:
        raise ValueError("ML experiment ledger payload must include provider and task_id")
    return {
        "schema_version": schema_version,
        "provider": provider,
        "experiment_id": str(payload.get("experiment_id") or "") or None,
        "task_id": task_id,
    }


def upsert_ml_experiment_ledger_jsonl(
    ledger_path: str | Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Upsert a public-safe ML experiment payload into a JSONL ledger.

    The payload builders already redact private paths and reject compact fields
    that look like raw URLs, credentials, or private markers. This writer keeps
    the ledger append-friendly while avoiding duplicate rows for repeated polls
    of the same external task.
    """

    if payload.get("ok") is not True:
        raise ValueError("only successful ML experiment payloads can be written to the ledger")
    return upsert_domain_state_jsonl(
        ledger_path,
        payload,
        key=ml_experiment_ledger_key(payload),
        existing_key_fn=ml_experiment_ledger_key,
    )


def default_ml_experiment_domain_state_ledger_path(
    *,
    project: str | Path = ".",
    goal_id: str,
) -> Path:
    """Return the project-local domain-state ledger path for ML experiments."""

    return default_domain_state_file_path(
        project=project,
        goal_id=goal_id,
        domain_pack="ml_experiment",
        filename=ML_EXPERIMENT_DOMAIN_STATE_LEDGER_FILENAME,
    )


def build_dataset_window_contract(
    *,
    train_window: str,
    eval_window: str,
    granularity: str = "daily",
    intersection_policy: str = "matched_window_only",
    missing_window_policy: str = "mark_inconclusive",
) -> dict[str, Any]:
    return {
        "schema_version": DATASET_WINDOW_CONTRACT_SCHEMA_VERSION,
        "train_window": _compact_public_text(train_window, field="train_window"),
        "eval_window": _compact_public_text(eval_window, field="eval_window"),
        "granularity": _compact_public_token(granularity, field="granularity"),
        "intersection_policy": _compact_public_token(intersection_policy, field="intersection_policy"),
        "missing_window_policy": _compact_public_token(missing_window_policy, field="missing_window_policy"),
        "conclusion_eligibility": "eligible_if_windows_match_and_guardrails_clear",
    }


def build_hypothesis_ledger_entry(
    *,
    hypothesis_id: str,
    mechanism_family: str,
    route: str,
    status: str = "active",
    positive_evidence: Iterable[str] | None = None,
    negative_evidence: Iterable[str] | None = None,
) -> dict[str, Any]:
    compact_status = _compact_public_token(status, field="hypothesis_status")
    if compact_status not in HYPOTHESIS_STATUSES:
        raise ValueError(f"hypothesis_status must be one of {', '.join(HYPOTHESIS_STATUSES)}")
    return {
        "schema_version": HYPOTHESIS_LEDGER_SCHEMA_VERSION,
        "hypothesis_id": _compact_public_token(hypothesis_id, field="hypothesis_id"),
        "mechanism_family": _compact_public_text(mechanism_family, field="mechanism_family"),
        "route": _compact_public_token(route, field="route"),
        "status": compact_status,
        "positive_evidence": _compact_public_text_list(positive_evidence, field="positive_evidence"),
        "negative_evidence": _compact_public_text_list(negative_evidence, field="negative_evidence"),
        "raw_metrics_recorded": False,
        "private_artifacts_recorded": False,
    }


def classify_primary_metric_delta(
    *,
    baseline_value: float,
    candidate_value: float,
    higher_is_better: bool,
) -> dict[str, Any]:
    baseline = _finite_float(baseline_value, field="baseline_value")
    candidate = _finite_float(candidate_value, field="candidate_value")
    delta = candidate - baseline
    signed_improvement = delta if higher_is_better else -delta
    if abs(delta) < 1e-12:
        status = "flat"
    elif signed_improvement > 0:
        status = "improved"
    else:
        status = "regressed"
    relative_delta = None if abs(baseline) < 1e-12 else delta / abs(baseline)
    return {
        "baseline_value": baseline,
        "candidate_value": candidate,
        "delta": delta,
        "relative_delta": relative_delta,
        "higher_is_better": bool(higher_is_better),
        "primary_metric_status": status,
    }


def build_ml_experiment_result(
    *,
    experiment_id: str,
    primary_metric: str,
    baseline_value: float,
    candidate_value: float,
    higher_is_better: bool = True,
    guardrail_status: str = "unknown",
    dataset_window: dict[str, Any],
    hypothesis: dict[str, Any],
) -> dict[str, Any]:
    compact_guardrail_status = _compact_public_token(guardrail_status, field="guardrail_status")
    if compact_guardrail_status not in GUARDRAIL_STATUSES:
        raise ValueError(f"guardrail_status must be one of {', '.join(GUARDRAIL_STATUSES)}")
    metric = classify_primary_metric_delta(
        baseline_value=baseline_value,
        candidate_value=candidate_value,
        higher_is_better=higher_is_better,
    )
    if compact_guardrail_status == "failed":
        decision_status = "blocked_by_guardrail"
    elif metric["primary_metric_status"] == "improved" and compact_guardrail_status == "clean":
        decision_status = "candidate_not_winner_yet"
    elif metric["primary_metric_status"] == "regressed":
        decision_status = "needs_replan"
    else:
        decision_status = "inconclusive"
    return {
        "schema_version": ML_EXPERIMENT_RESULT_SCHEMA_VERSION,
        "experiment_id": _compact_public_token(experiment_id, field="experiment_id"),
        "primary_metric": _compact_public_text(primary_metric, field="primary_metric"),
        **metric,
        "guardrail_status": compact_guardrail_status,
        "decision_status": decision_status,
        "dataset_window": dataset_window,
        "hypothesis": hypothesis,
        "raw_metrics_recorded": False,
        "private_artifacts_recorded": False,
    }


def build_experiment_replan_preview(
    *,
    result: dict[str, Any],
    next_candidates: Iterable[str] | None = None,
    allocation: str = "one_followup",
) -> dict[str, Any]:
    candidate_labels = _compact_public_text_list(next_candidates, field="next_candidate")
    if not candidate_labels:
        candidate_labels = ["near_neighbor_ablation", "guardrail_holdout_check"]
    decision_status = str(result.get("decision_status") or "inconclusive")
    if decision_status == "candidate_not_winner_yet":
        recommendation = "validate_on_holdout_before_promotion"
    elif decision_status == "blocked_by_guardrail":
        recommendation = "repair_guardrail_before_more_exploration"
    else:
        recommendation = "run_one_followup_or_retire_hypothesis"
    return {
        "schema_version": EXPERIMENT_REPLAN_SCHEMA_VERSION,
        "recommendation": recommendation,
        "allocation": _compact_public_token(allocation, field="allocation"),
        "next_candidates": candidate_labels,
        "launch_actions_enabled": False,
        "production_actions_enabled": False,
        "requires_explicit_authorization": True,
    }


def build_ml_experiment_advisory_packet(
    *,
    experiment_id: str,
    primary_metric: str,
    baseline_value: float,
    candidate_value: float,
    higher_is_better: bool = True,
    guardrail_status: str = "unknown",
    train_window: str,
    eval_window: str,
    granularity: str = "daily",
    hypothesis_id: str,
    mechanism_family: str,
    route: str,
    hypothesis_status: str = "active",
    positive_evidence: Iterable[str] | None = None,
    negative_evidence: Iterable[str] | None = None,
    next_candidates: Iterable[str] | None = None,
) -> dict[str, Any]:
    dataset_window = build_dataset_window_contract(
        train_window=train_window,
        eval_window=eval_window,
        granularity=granularity,
    )
    hypothesis = build_hypothesis_ledger_entry(
        hypothesis_id=hypothesis_id,
        mechanism_family=mechanism_family,
        route=route,
        status=hypothesis_status,
        positive_evidence=positive_evidence,
        negative_evidence=negative_evidence,
    )
    result = build_ml_experiment_result(
        experiment_id=experiment_id,
        primary_metric=primary_metric,
        baseline_value=baseline_value,
        candidate_value=candidate_value,
        higher_is_better=higher_is_better,
        guardrail_status=guardrail_status,
        dataset_window=dataset_window,
        hypothesis=hypothesis,
    )
    replan = build_experiment_replan_preview(
        result=result,
        next_candidates=next_candidates,
    )
    return {
        "ok": True,
        "schema_version": ML_EXPERIMENT_ADVISORY_PACKET_SCHEMA_VERSION,
        "pack": build_ml_experiment_domain_pack_contract(enabled=False),
        "mode": "default_off_advisory_preview",
        "result": result,
        "replan_preview": replan,
        "raw_metrics_recorded": False,
        "private_artifacts_recorded": False,
        "launch_actions_enabled": False,
        "production_actions_enabled": False,
        "recommended_next_action": "owner_or_registry_can_enable_advisory_mode_for_this_goal",
    }


def render_ml_experiment_advisory_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return f"ML experiment advisory preview failed: {payload.get('error')}\n"
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    replan = payload.get("replan_preview") if isinstance(payload.get("replan_preview"), dict) else {}
    pack = payload.get("pack") if isinstance(payload.get("pack"), dict) else {}
    lines = [
        "# ML Experiment Advisory Preview",
        "",
        f"- experiment: `{result.get('experiment_id')}`",
        f"- primary metric: `{result.get('primary_metric')}`",
        f"- status: `{result.get('primary_metric_status')}`",
        f"- delta: `{result.get('delta')}`",
        f"- guardrail: `{result.get('guardrail_status')}`",
        f"- decision: `{result.get('decision_status')}`",
        f"- pack enabled: `{pack.get('enabled')}`",
        f"- launch actions enabled: `{payload.get('launch_actions_enabled')}`",
        f"- production actions enabled: `{payload.get('production_actions_enabled')}`",
        "",
        "## Replan Preview",
        "",
        f"- recommendation: `{replan.get('recommendation')}`",
        f"- allocation: `{replan.get('allocation')}`",
        "- next candidates: "
        + ", ".join(f"`{candidate}`" for candidate in replan.get("next_candidates", []) or []),
    ]
    return "\n".join(lines) + "\n"


def render_volc_mlp_task_packet_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return f"Volc MLP task packet failed: {payload.get('error')}\n"
    handle = payload.get("observable_handle") if isinstance(payload.get("observable_handle"), dict) else {}
    dataset_window = payload.get("dataset_window") if isinstance(payload.get("dataset_window"), dict) else {}
    lineage = payload.get("lineage") if isinstance(payload.get("lineage"), dict) else {}
    decision = payload.get("decision_boundary") if isinstance(payload.get("decision_boundary"), dict) else {}
    metric_artifacts = [
        ref.get("value")
        for ref in payload.get("metric_artifacts", []) or []
        if isinstance(ref, dict) and ref.get("value")
    ]
    workspace_ref = lineage.get("workspace_ref") if isinstance(lineage.get("workspace_ref"), dict) else {}
    lines = [
        "# Volc MLP Task Packet",
        "",
        f"- task: `{handle.get('task_id')}`",
        f"- name: `{handle.get('task_name')}`",
        f"- state: `{handle.get('state')}`",
        f"- priority/retries: `{handle.get('priority')}` / `{handle.get('retried_times')}`",
        f"- source task: `{handle.get('source_task_id')}`",
        f"- train window: `{dataset_window.get('train_window')}`",
        f"- eval window: `{dataset_window.get('eval_window')}`",
        f"- code ref: `{lineage.get('code_ref')}`",
        f"- model: `{lineage.get('model_name')}`",
        f"- mechanism: `{lineage.get('mechanism_family')}`",
        f"- workspace ref: `{workspace_ref.get('value')}`",
        "- metric refs: " + ", ".join(f"`{ref}`" for ref in metric_artifacts),
        f"- primary metric: `{decision.get('primary_metric')}`",
        "- guardrails: "
        + ", ".join(f"`{metric}`" for metric in decision.get("guardrail_metrics", []) or []),
        f"- launch actions enabled: `{payload.get('launch_actions_enabled')}`",
        f"- production actions enabled: `{payload.get('production_actions_enabled')}`",
        f"- next action: `{payload.get('recommended_next_action')}`",
    ]
    ledger_write = payload.get("ledger_write") if isinstance(payload.get("ledger_write"), dict) else {}
    if ledger_write:
        lines.extend(
            [
                "",
                "## Ledger Write",
                "",
                f"- status: `{ledger_write.get('status')}`",
                f"- row count: `{ledger_write.get('row_count')}`",
                f"- path recorded: `{ledger_write.get('path_recorded')}`",
            ]
        )
    return "\n".join(lines) + "\n"


def render_volc_mlp_result_ledger_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return f"Volc MLP result ledger failed: {payload.get('error')}\n"
    task_packet = payload.get("task_packet") if isinstance(payload.get("task_packet"), dict) else {}
    handle = (
        task_packet.get("observable_handle")
        if isinstance(task_packet.get("observable_handle"), dict)
        else {}
    )
    comparison = payload.get("comparison") if isinstance(payload.get("comparison"), dict) else {}
    metric = (
        comparison.get("primary_metric_delta")
        if isinstance(comparison.get("primary_metric_delta"), dict)
        else {}
    )
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    failure = (
        payload.get("failure_attribution")
        if isinstance(payload.get("failure_attribution"), dict)
        else {}
    )
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    lines = [
        "# Volc MLP Result Ledger",
        "",
        f"- experiment: `{payload.get('experiment_id')}`",
        f"- task: `{handle.get('task_id')}`",
        f"- name: `{handle.get('task_name')}`",
        f"- state: `{handle.get('state')}`",
        f"- baseline task: `{comparison.get('baseline_task_id')}`",
        f"- primary metric: `{comparison.get('primary_metric')}`",
        f"- metric status: `{metric.get('primary_metric_status')}`",
        f"- delta: `{metric.get('delta')}`",
        f"- guardrail: `{comparison.get('guardrail_status')}`",
        "- positive evidence: "
        + ", ".join(f"`{label}`" for label in evidence.get("positive", []) or []),
        "- negative evidence: "
        + ", ".join(f"`{label}`" for label in evidence.get("negative", []) or []),
        "- failure labels: "
        + ", ".join(f"`{label}`" for label in failure.get("labels", []) or []),
        f"- outcome: `{decision.get('outcome')}`",
        f"- promotion eligible: `{decision.get('promotion_eligible')}`",
        f"- avoid near-neighbor retry: `{decision.get('avoid_near_neighbor_retry')}`",
        f"- launch actions enabled: `{payload.get('launch_actions_enabled')}`",
        f"- production actions enabled: `{payload.get('production_actions_enabled')}`",
        f"- next action: `{decision.get('recommended_next_action')}`",
    ]
    ledger_write = payload.get("ledger_write") if isinstance(payload.get("ledger_write"), dict) else {}
    if ledger_write:
        lines.extend(
            [
                "",
                "## Ledger Write",
                "",
                f"- status: `{ledger_write.get('status')}`",
                f"- row count: `{ledger_write.get('row_count')}`",
                f"- path recorded: `{ledger_write.get('path_recorded')}`",
            ]
        )
    return "\n".join(lines) + "\n"
