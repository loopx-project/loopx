from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...public_safe_text import PUBLIC_SAFE_REFERENCE_PATTERN as TOKEN_RE
from .application import REWARD_MEMORY_APPLICATION_RECEIPT_SCHEMA_VERSION
from .candidate_review import (
    REWARD_MEMORY_REVIEW_SCHEMA_VERSION,
    review_reward_memory_candidate,
)
from .evaluation import REWARD_MEMORY_EVALUATION_SCHEMA_VERSION
from .memory_utility import (
    build_reward_memory_utility_observation,
    normalize_reward_memory_ref_digests,
    reward_memory_application_receipt_id,
    validate_reward_memory_utility_observation,
)
from .registry import normalize_reward_memory_corpus


REWARD_MEMORY_DOGFOOD_RECEIPT_SCHEMA_VERSION = "reward_memory_dogfood_receipt_v1"
REWARD_MEMORY_DOGFOOD_BATCH_SCHEMA_VERSION = "reward_memory_dogfood_batch_v1"
REWARD_MEMORY_OPERATOR_CONTROL_SCHEMA_VERSION = "reward_memory_operator_control_v0"

APPLICATION_DISPOSITIONS = {"applied", "not_applied", "refuted"}
UTILITY_EVALUATION_STATUSES = {"accepted", "rejected", "not_requested"}
DOMAIN_FAMILIES = {"issue_fix", "loopx"}
OPERATOR_ACTIONS = {"edit", "retire"}
MAX_RECEIPTS = 24
MAX_OPERATOR_CONTROLS = 8

_DOGFOOD_RECEIPT_FIELDS = frozenset(
    {
        "ok",
        "schema_version",
        "domain_family",
        "domain_id",
        "application_id",
        "application_receipt_id",
        "artifact_ref",
        "corpus_id",
        "surface_id",
        "application_outcome",
        "application_disposition",
        "module_outcome",
        "utility_evaluation",
        "utility_observation",
        "memory_ref_digests",
        "verification",
        "cost",
        "intervention",
        "bot_feedback",
        "receipt_id",
        "grants_new_action_authority",
        "raw_content_captured",
        "external_writes_performed",
    }
)
_VERIFICATION_FIELDS = frozenset(
    {"result_readback_verified", "current_artifact_verified"}
)
_MODULE_OUTCOME_FIELDS = frozenset(
    {"verified", "outcome_ref", "outcome_status", "summary"}
)
_COST_FIELDS = frozenset({"latency_ms", "model_tokens", "provider_call_count"})
_INTERVENTION_FIELDS = frozenset({"count", "summary"})
_BOT_FEEDBACK_FIELDS = frozenset({"captured", "summary"})
_UTILITY_ATTRIBUTION_FIELDS = frozenset({"context", "proposal", "created_at"})


def _token(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not TOKEN_RE.fullmatch(result):
        raise ValueError(f"{label} must be a compact public-safe token")
    return result


def _compact(value: object, label: str, *, limit: int = 500) -> str:
    result = public_safe_compact_text(value, limit=limit)
    if not result:
        raise ValueError(f"{label} must be compact and public-safe")
    return result


def _boolean(mapping: Mapping[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _counter(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _exact_fields(
    mapping: Mapping[str, Any], expected: frozenset[str], label: str
) -> None:
    keys = set(mapping)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError(f"{label} fields must be strings")
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        raise ValueError(
            f"{label} has invalid fields: missing={missing}, unknown={unknown}"
        )


def _digest(payload: Mapping[str, Any], *, prefix: str) -> str:
    value = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    return f"{prefix}:{value}"


def _application_disposition(application_outcome: object) -> str:
    outcome = str(application_outcome or "").strip()
    if outcome == "applied":
        return "applied"
    if outcome == "refuted":
        return "refuted"
    if outcome in {
        "ignored",
        "failed",
        "not_available",
        "available_not_applied",
    }:
        return "not_applied"
    raise ValueError("application receipt outcome is invalid")


def _dogfood_receipt_identity(receipt: Mapping[str, Any]) -> dict[str, Any]:
    # Utility delivery has its own observation_id. Keep this identity tied to the
    # application settlement so evaluator absence, rejection, or revision cannot
    # duplicate application disposition and cost metrics.
    module_outcome = receipt["module_outcome"]
    return {
        "schema_version": receipt["schema_version"],
        "application_receipt_id": receipt["application_receipt_id"],
        "application_outcome": receipt["application_outcome"],
        "application_disposition": receipt["application_disposition"],
        "module_outcome_ref": module_outcome["outcome_ref"],
        "module_outcome_status": module_outcome["outcome_status"],
    }


def build_reward_memory_dogfood_receipt(
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind one real module outcome to an existing compact application receipt."""

    if _boolean(observation, "raw_content_captured"):
        raise ValueError("dogfood observations must not contain raw content")
    family = str(observation.get("domain_family") or "").strip()
    if family not in DOMAIN_FAMILIES:
        raise ValueError("domain_family must be issue_fix or loopx")
    domain_id = _token(observation.get("domain_id"), "domain_id")
    if family == "issue_fix" and not domain_id.startswith("issue_fix."):
        raise ValueError("issue_fix domain_id must use the issue_fix namespace")
    if family == "loopx" and not domain_id.startswith("loopx."):
        raise ValueError("loopx domain_id must use the loopx namespace")

    application = observation.get("application_receipt")
    if not isinstance(application, Mapping):
        raise ValueError("application_receipt must be an object")
    if (
        application.get("schema_version")
        != REWARD_MEMORY_APPLICATION_RECEIPT_SCHEMA_VERSION
    ):
        raise ValueError("application_receipt must use the Stage-3 receipt schema")
    application_id = _token(application.get("application_id"), "application_id")
    application_receipt_id = reward_memory_application_receipt_id(application)
    artifact_ref = _token(application.get("artifact_ref"), "artifact_ref")
    corpus_id = _token(application.get("corpus_id"), "corpus_id")
    surface_id = _token(application.get("surface_id"), "surface_id")
    application_outcome = str(application.get("outcome") or "").strip()
    application_disposition = _application_disposition(application_outcome)
    readback_verified = _boolean(application, "result_readback_verified")
    current_verified = _boolean(application, "current_artifact_verified")
    if _boolean(application, "grants_new_action_authority"):
        raise ValueError("application receipt cannot grant action authority")
    if _boolean(application, "raw_content_captured"):
        raise ValueError("application receipt cannot contain raw content")
    if _boolean(application, "external_writes_performed"):
        raise ValueError("application receipt cannot perform external writes")
    if application_disposition in {"applied", "refuted"} and not readback_verified:
        raise ValueError("applied or refuted requires exact provider result readback")
    if application_disposition in {"applied", "refuted"} and not current_verified:
        raise ValueError("applied or refuted requires current-artifact verification")
    digests = normalize_reward_memory_ref_digests(
        application.get("memory_ref_digests"),
        "memory_ref_digests",
        minimum=0,
    )
    if application_disposition in {"applied", "refuted"} and not digests:
        raise ValueError("applied or refuted requires attributed memory references")

    module_outcome = observation.get("module_outcome")
    if not isinstance(module_outcome, Mapping):
        raise ValueError("module_outcome must be an object")
    if (
        _token(module_outcome.get("artifact_ref"), "module_outcome.artifact_ref")
        != artifact_ref
    ):
        raise ValueError("module outcome and application artifact_ref must match")
    outcome_verified = _boolean(module_outcome, "outcome_verified")
    if not outcome_verified:
        raise ValueError("dogfood requires a verified real module outcome")
    outcome_ref = _token(
        module_outcome.get("outcome_ref"), "module_outcome.outcome_ref"
    )
    outcome_status = _token(
        module_outcome.get("outcome_status"), "module_outcome.outcome_status"
    )
    outcome_summary = _compact(
        module_outcome.get("summary"), "module_outcome.summary", limit=360
    )

    verified_outcome = {
        "verified": True,
        "outcome_ref": outcome_ref,
        "artifact_ref": artifact_ref,
        "outcome_status": outcome_status,
    }
    utility_observation: dict[str, Any] | None = None
    if "utility_attribution" not in observation:
        utility_evaluation = {
            "status": "not_requested",
            "reason_code": "utility_attribution_not_requested",
        }
    else:
        try:
            attribution = observation.get("utility_attribution")
            if not isinstance(attribution, Mapping):
                raise ValueError("utility_attribution must be an object")
            _exact_fields(
                attribution,
                _UTILITY_ATTRIBUTION_FIELDS,
                "utility_attribution",
            )
            attribution_context = attribution.get("context")
            evaluator_proposal = attribution.get("proposal")
            if not isinstance(attribution_context, Mapping):
                raise ValueError("utility attribution context must be an object")
            if not isinstance(evaluator_proposal, Mapping):
                raise ValueError("utility attribution proposal must be an object")
            built_observation = build_reward_memory_utility_observation(
                application,
                verified_outcome,
                attribution_context,
                evaluator_proposal,
                created_at=attribution.get("created_at"),
            )
            utility_observation = validate_reward_memory_utility_observation(
                built_observation,
                application_receipt=application,
                verified_outcome=verified_outcome,
                attribution_context=attribution_context,
            )
        except ValueError:
            utility_evaluation = {
                "status": "rejected",
                "reason_code": "utility_attribution_rejected",
            }
        else:
            utility_evaluation = {"status": "accepted"}

    cost = observation.get("cost")
    if not isinstance(cost, Mapping):
        raise ValueError("cost must be an object")
    compact_cost = {
        key: _counter(cost, key)
        for key in ("latency_ms", "model_tokens", "provider_call_count")
    }
    intervention = observation.get("intervention")
    if not isinstance(intervention, Mapping):
        raise ValueError("intervention must be an object")
    intervention_count = _counter(intervention, "count")
    intervention_summary = None
    if intervention_count:
        intervention_summary = _compact(
            intervention.get("summary"), "intervention.summary", limit=240
        )

    bot_feedback = observation.get("bot_feedback") or {
        "captured": False,
        "summary": None,
    }
    if not isinstance(bot_feedback, Mapping):
        raise ValueError("bot_feedback must be an object")
    feedback_captured = _boolean(bot_feedback, "captured")
    feedback_summary = None
    if feedback_captured:
        feedback_summary = _compact(
            bot_feedback.get("summary"), "bot_feedback.summary", limit=240
        )

    receipt = {
        "ok": True,
        "schema_version": REWARD_MEMORY_DOGFOOD_RECEIPT_SCHEMA_VERSION,
        "domain_family": family,
        "domain_id": domain_id,
        "application_id": application_id,
        "application_receipt_id": application_receipt_id,
        "artifact_ref": artifact_ref,
        "corpus_id": corpus_id,
        "surface_id": surface_id,
        "application_outcome": application_outcome,
        "application_disposition": application_disposition,
        "module_outcome": {
            "verified": True,
            "outcome_ref": outcome_ref,
            "outcome_status": outcome_status,
            "summary": outcome_summary,
        },
        "utility_evaluation": utility_evaluation,
        "utility_observation": utility_observation,
        "memory_ref_digests": digests,
        "verification": {
            "result_readback_verified": readback_verified,
            "current_artifact_verified": current_verified,
        },
        "cost": compact_cost,
        "intervention": {
            "count": intervention_count,
            "summary": intervention_summary,
        },
        "bot_feedback": {
            "captured": feedback_captured,
            "summary": feedback_summary,
        },
        "grants_new_action_authority": False,
        "raw_content_captured": False,
        "external_writes_performed": False,
    }
    receipt["receipt_id"] = _digest(
        _dogfood_receipt_identity(receipt), prefix="dogfood"
    )
    return receipt


def _scope_matches(record: Mapping[str, Any], corpus: Mapping[str, Any]) -> bool:
    scope = record.get("scope")
    expected = corpus["scope"]
    if not isinstance(scope, Mapping):
        return False
    base_matches = (
        scope.get("workspace_ref") == expected["workspace_ref"]
        and scope.get("project_ref") == expected["project_ref"]
        and set(scope.get("surface_ids") or []).issubset(set(expected["surface_ids"]))
    )
    if not base_matches:
        return False
    if corpus["freshness"]["mode"] == "revision_bound":
        return scope.get("revision_ref") == corpus["freshness"]["source_revision"]
    return True


def build_reward_memory_operator_control(
    reviewed_record: Mapping[str, Any],
    corpus: Mapping[str, Any],
    *,
    action: str,
    operator_checkpoint: Mapping[str, Any],
    control_ref: str,
    reasoning_summary: str,
    edited_content_summary: str | None = None,
) -> dict[str, Any]:
    """Prepare an authorized edit or retirement without performing a provider write."""

    normalized = normalize_reward_memory_corpus(corpus)
    if action not in OPERATOR_ACTIONS:
        raise ValueError("operator action must be edit or retire")
    if normalized["lifecycle"]["state"] != "active":
        raise ValueError("operator control corpus must be active")
    if normalized["write_authority"] == "read_only":
        raise ValueError("operator control corpus is read-only")
    if reviewed_record.get("schema_version") != REWARD_MEMORY_REVIEW_SCHEMA_VERSION:
        raise ValueError("operator control requires an active reviewed record")
    if (
        reviewed_record.get("status") != "active"
        or reviewed_record.get("guard_passed") is not True
    ):
        raise ValueError("operator control requires a guard-passed active record")
    record = reviewed_record.get("record")
    if not isinstance(record, Mapping):
        raise ValueError("reviewed record is incomplete")
    lifecycle = record.get("lifecycle")
    if not isinstance(lifecycle, Mapping) or lifecycle.get("state") != "active":
        raise ValueError("operator control target must be active")
    if record.get("target_class") != normalized["class_id"]:
        raise ValueError("operator control corpus class does not match")
    if not _scope_matches(record, normalized):
        raise ValueError("operator control corpus scope does not match")
    if not isinstance(operator_checkpoint, Mapping):
        raise ValueError("operator_checkpoint must be an object")
    if not _boolean(operator_checkpoint, "verified"):
        raise ValueError("operator authority checkpoint must be verified")
    operator_ref = _token(
        operator_checkpoint.get("operator_ref"), "operator_checkpoint.operator_ref"
    )
    authority_ref = _token(
        operator_checkpoint.get("authority_ref"), "operator_checkpoint.authority_ref"
    )
    source_ref = _token(
        operator_checkpoint.get("source_ref"), "operator_checkpoint.source_ref"
    )
    checkpoint_corpus_id = _token(
        operator_checkpoint.get("corpus_id"), "operator_checkpoint.corpus_id"
    )
    checkpoint_project_ref = _token(
        operator_checkpoint.get("project_ref"), "operator_checkpoint.project_ref"
    )
    checkpoint_action = str(operator_checkpoint.get("action") or "").strip()
    if checkpoint_corpus_id != normalized["corpus_id"]:
        raise ValueError("operator authority corpus does not match")
    if checkpoint_project_ref != normalized["scope"]["project_ref"]:
        raise ValueError("operator authority project does not match")
    if checkpoint_action != action:
        raise ValueError("operator authority action does not match")
    expected_authority = (
        normalized["owner_ref"]
        if action == "edit"
        else normalized["maintenance"]["retirement_authority"]
    )
    if authority_ref != expected_authority:
        raise ValueError("operator authority does not match the corpus declaration")

    target = deepcopy(dict(reviewed_record))
    if action == "edit":
        target_record = target["record"]
        old_ref = _token(target_record.get("candidate_ref"), "candidate_ref")
        target_record["lifecycle"] = {
            "state": "candidate",
            "supersedes_refs": [old_ref],
        }
    review = {
        "decision": action,
        "reviewer_ref": operator_ref,
        "review_ref": _token(control_ref, "control_ref"),
        "reasoning_summary": _compact(
            reasoning_summary, "reasoning_summary", limit=360
        ),
    }
    if action == "edit":
        review["edited_content_summary"] = _compact(
            edited_content_summary, "edited_content_summary", limit=500
        )
    decision = review_reward_memory_candidate(target, review)
    old_candidate_ref = _token(record.get("candidate_ref"), "candidate_ref")
    new_candidate_ref = _token(
        decision["record"].get("candidate_ref"), "decision.candidate_ref"
    )
    receipt = {
        "schema_version": REWARD_MEMORY_OPERATOR_CONTROL_SCHEMA_VERSION,
        "control_ref": review["review_ref"],
        "action": action,
        "effective_action": decision["effective_decision"],
        "operator_ref": operator_ref,
        "authority_ref": authority_ref,
        "authority_source_ref": source_ref,
        "authority_verified": True,
        "corpus_id": normalized["corpus_id"],
        "prior_candidate_ref_digest": hashlib.sha256(
            old_candidate_ref.encode("utf-8")
        ).hexdigest()[:16],
        "result_candidate_ref_digest": hashlib.sha256(
            new_candidate_ref.encode("utf-8")
        ).hexdigest()[:16],
        "result_state": decision["record"]["lifecycle"]["state"],
        "reasoning_summary": review["reasoning_summary"],
        "provider_write_performed": False,
        "readback_verified": False,
        "next_step": (
            "review_replacement_then_owner_write_and_exact_readback"
            if action == "edit"
            else "owner_write_retirement_then_exact_readback"
        ),
        "grants_new_action_authority": False,
        "raw_content_captured": False,
        "external_writes_performed": False,
    }
    return {
        "ok": True,
        "schema_version": REWARD_MEMORY_OPERATOR_CONTROL_SCHEMA_VERSION,
        "status": "control_ready",
        "decision": decision,
        "receipt": receipt,
    }


def _validate_control_receipt(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("operator control receipt must be an object")
    if raw.get("schema_version") != REWARD_MEMORY_OPERATOR_CONTROL_SCHEMA_VERSION:
        raise ValueError("operator control receipt schema is invalid")
    action = str(raw.get("action") or "").strip()
    if action not in OPERATOR_ACTIONS:
        raise ValueError("operator control receipt action is invalid")
    if _boolean(raw, "authority_verified") is not True:
        raise ValueError("operator control authority must be verified")
    if _boolean(raw, "provider_write_performed"):
        raise ValueError("Stage-5 control receipt must precede provider write")
    if str(raw.get("effective_action") or "").strip() != action:
        raise ValueError("operator control effective action is invalid")
    expected_state = "candidate" if action == "edit" else "retired"
    if str(raw.get("result_state") or "").strip() != expected_state:
        raise ValueError("operator control result state is invalid")
    for key in (
        "control_ref",
        "operator_ref",
        "authority_ref",
        "authority_source_ref",
        "corpus_id",
        "prior_candidate_ref_digest",
        "result_candidate_ref_digest",
    ):
        _token(raw.get(key), key)
    if _boolean(raw, "grants_new_action_authority"):
        raise ValueError("operator control cannot grant action authority")
    if _boolean(raw, "raw_content_captured"):
        raise ValueError("operator control cannot contain raw content")
    if _boolean(raw, "external_writes_performed"):
        raise ValueError("operator control cannot perform external writes")
    return dict(raw)


def _validate_dogfood_receipt(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("dogfood receipt must be an object")
    _exact_fields(raw, _DOGFOOD_RECEIPT_FIELDS, "dogfood receipt")
    if raw.get("schema_version") != REWARD_MEMORY_DOGFOOD_RECEIPT_SCHEMA_VERSION:
        raise ValueError("dogfood receipt schema is invalid")
    if _boolean(raw, "ok") is not True:
        raise ValueError("dogfood receipt must be successful")
    family = str(raw.get("domain_family") or "").strip()
    domain_id = _token(raw.get("domain_id"), "domain_id")
    if family not in DOMAIN_FAMILIES or not domain_id.startswith(f"{family}."):
        raise ValueError("dogfood receipt domain is invalid")
    application_outcome = str(raw.get("application_outcome") or "").strip()
    application_disposition = str(raw.get("application_disposition") or "").strip()
    if application_disposition not in APPLICATION_DISPOSITIONS:
        raise ValueError("dogfood receipt application disposition is invalid")
    if _application_disposition(application_outcome) != application_disposition:
        raise ValueError("dogfood receipt application outcome is inconsistent")
    for key in (
        "receipt_id",
        "application_id",
        "application_receipt_id",
        "artifact_ref",
        "corpus_id",
        "surface_id",
    ):
        _token(raw.get(key), key)
    verification = raw.get("verification")
    if not isinstance(verification, Mapping):
        raise ValueError("dogfood receipt verification is invalid")
    _exact_fields(verification, _VERIFICATION_FIELDS, "dogfood receipt verification")
    readback = _boolean(verification, "result_readback_verified")
    current = _boolean(verification, "current_artifact_verified")
    normalized_verification = {
        "result_readback_verified": readback,
        "current_artifact_verified": current,
    }
    if application_disposition in {"applied", "refuted"} and not (readback and current):
        raise ValueError("applied or refuted receipt is not exactly verified")
    digests = normalize_reward_memory_ref_digests(
        raw.get("memory_ref_digests"),
        "dogfood receipt memory_ref_digests",
        minimum=0,
    )
    if raw["memory_ref_digests"] != digests:
        raise ValueError("dogfood receipt memory_ref_digests must be sorted")
    if application_disposition in {"applied", "refuted"} and not digests:
        raise ValueError("applied or refuted receipt requires memory references")
    module_outcome = raw.get("module_outcome")
    if not isinstance(module_outcome, Mapping):
        raise ValueError("dogfood module outcome is invalid")
    _exact_fields(module_outcome, _MODULE_OUTCOME_FIELDS, "dogfood module outcome")
    if not _boolean(module_outcome, "verified"):
        raise ValueError("dogfood module outcome is not verified")
    outcome_ref = _token(
        module_outcome.get("outcome_ref"), "module_outcome.outcome_ref"
    )
    outcome_status = _token(
        module_outcome.get("outcome_status"), "module_outcome.outcome_status"
    )
    normalized_module_outcome = {
        "verified": True,
        "outcome_ref": outcome_ref,
        "outcome_status": outcome_status,
        "summary": _compact(
            module_outcome.get("summary"), "module_outcome.summary", limit=360
        ),
    }

    utility_evaluation = raw.get("utility_evaluation")
    if not isinstance(utility_evaluation, Mapping):
        raise ValueError("dogfood utility evaluation is invalid")
    evaluation_status = str(utility_evaluation.get("status") or "").strip()
    if evaluation_status not in UTILITY_EVALUATION_STATUSES:
        raise ValueError("dogfood utility evaluation status is invalid")
    utility_observation = raw.get("utility_observation")
    if evaluation_status == "accepted":
        if set(utility_evaluation) != {"status"}:
            raise ValueError("accepted utility evaluation must not carry a reason")
        normalized_utility = validate_reward_memory_utility_observation(
            utility_observation
        )
        utility_scope = normalized_utility["scope"]
        if (
            utility_scope["corpus_id"] != raw["corpus_id"]
            or utility_scope["surface_id"] != raw["surface_id"]
        ):
            raise ValueError("embedded utility observation scope does not match")
        if normalized_utility["outcome_ref"] != outcome_ref:
            raise ValueError("embedded utility observation outcome does not match")
        if (
            normalized_utility["application_receipt_id"]
            != raw["application_receipt_id"]
        ):
            raise ValueError(
                "embedded utility observation application receipt does not match"
            )
        if normalized_utility["attribution_level"] == "item":
            if (
                len(normalized_utility["memory_ref_digests"]) != 1
                or normalized_utility["memory_ref_digests"][0] not in digests
            ):
                raise ValueError("embedded item utility memory digest is not applied")
        elif normalized_utility["memory_ref_digests"] != digests:
            raise ValueError("embedded utility observation memory digests do not match")
        normalized_utility_evaluation = {"status": "accepted"}
    else:
        expected_reason = f"utility_attribution_{evaluation_status}"
        if set(utility_evaluation) != {"status", "reason_code"} or (
            utility_evaluation.get("reason_code") != expected_reason
        ):
            raise ValueError("dogfood utility evaluation reason is invalid")
        if utility_observation is not None:
            raise ValueError(
                "unaccepted utility evaluation cannot carry an observation"
            )
        normalized_utility = None
        normalized_utility_evaluation = {
            "status": evaluation_status,
            "reason_code": expected_reason,
        }

    if raw["receipt_id"] != _digest(_dogfood_receipt_identity(raw), prefix="dogfood"):
        raise ValueError("dogfood receipt identity is invalid")
    cost = raw.get("cost")
    if not isinstance(cost, Mapping):
        raise ValueError("dogfood receipt cost is invalid")
    _exact_fields(cost, _COST_FIELDS, "dogfood receipt cost")
    normalized_cost = {
        key: _counter(cost, key)
        for key in ("latency_ms", "model_tokens", "provider_call_count")
    }
    intervention = raw.get("intervention")
    if not isinstance(intervention, Mapping):
        raise ValueError("dogfood receipt intervention is invalid")
    _exact_fields(intervention, _INTERVENTION_FIELDS, "dogfood receipt intervention")
    intervention_count = _counter(intervention, "count")
    intervention_summary = intervention.get("summary")
    if intervention_count:
        normalized_intervention_summary = _compact(
            intervention_summary, "intervention.summary", limit=240
        )
    elif intervention_summary is not None:
        raise ValueError("intervention.summary must be null when count is zero")
    else:
        normalized_intervention_summary = None
    normalized_intervention = {
        "count": intervention_count,
        "summary": normalized_intervention_summary,
    }
    feedback = raw.get("bot_feedback")
    if not isinstance(feedback, Mapping):
        raise ValueError("dogfood receipt bot_feedback is invalid")
    _exact_fields(feedback, _BOT_FEEDBACK_FIELDS, "dogfood receipt bot_feedback")
    feedback_captured = _boolean(feedback, "captured")
    feedback_summary = feedback.get("summary")
    if feedback_captured:
        normalized_feedback_summary = _compact(
            feedback_summary, "bot_feedback.summary", limit=240
        )
    elif feedback_summary is not None:
        raise ValueError("bot_feedback.summary must be null when captured is false")
    else:
        normalized_feedback_summary = None
    normalized_feedback = {
        "captured": feedback_captured,
        "summary": normalized_feedback_summary,
    }
    if _boolean(raw, "grants_new_action_authority"):
        raise ValueError("dogfood receipt cannot grant action authority")
    if _boolean(raw, "raw_content_captured"):
        raise ValueError("dogfood receipt cannot contain raw content")
    if _boolean(raw, "external_writes_performed"):
        raise ValueError("dogfood receipt cannot perform external writes")
    result = dict(raw)
    result["verification"] = normalized_verification
    result["module_outcome"] = normalized_module_outcome
    result["utility_evaluation"] = normalized_utility_evaluation
    result["cost"] = normalized_cost
    result["intervention"] = normalized_intervention
    result["bot_feedback"] = normalized_feedback
    result["utility_observation"] = normalized_utility
    return result


def build_reward_memory_dogfood_batch(
    receipts: Sequence[Mapping[str, Any]],
    operator_controls: Sequence[Mapping[str, Any]],
    *,
    evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate bounded Stage-5 evidence without claiming production uplift."""

    if not isinstance(evaluation, Mapping):
        raise ValueError("evaluation must be a Stage-4 evaluation packet")
    if len(receipts) > MAX_RECEIPTS:
        raise ValueError(f"dogfood batch accepts at most {MAX_RECEIPTS} receipts")
    if len(operator_controls) > MAX_OPERATOR_CONTROLS:
        raise ValueError(
            f"dogfood batch accepts at most {MAX_OPERATOR_CONTROLS} controls"
        )
    normalized_receipts = [_validate_dogfood_receipt(item) for item in receipts]
    controls = [_validate_control_receipt(item) for item in operator_controls]
    receipt_ids = [item["receipt_id"] for item in normalized_receipts]
    if len(receipt_ids) != len(set(receipt_ids)):
        raise ValueError("dogfood batch must not double-count a receipt")
    application_receipt_ids = [
        item["application_receipt_id"] for item in normalized_receipts
    ]
    if len(application_receipt_ids) != len(set(application_receipt_ids)):
        raise ValueError(
            "dogfood batch must not double-count an application settlement"
        )
    control_refs = [item["control_ref"] for item in controls]
    if len(control_refs) != len(set(control_refs)):
        raise ValueError("dogfood batch must not double-count an operator control")

    gate_ready = (
        evaluation.get("schema_version") == REWARD_MEMORY_EVALUATION_SCHEMA_VERSION
        and evaluation.get("status") == "passed"
        and isinstance(evaluation.get("release_gate"), Mapping)
        and evaluation["release_gate"].get("status") == "ready_for_bounded_dogfood"
    )
    issue_fix_count = sum(
        item["domain_family"] == "issue_fix" for item in normalized_receipts
    )
    loopx_domains = sorted(
        {
            item["domain_id"]
            for item in normalized_receipts
            if item["domain_family"] == "loopx"
        }
    )
    application_dispositions = {
        item["application_disposition"] for item in normalized_receipts
    }
    actions = {item["action"] for item in controls}
    reason_codes: list[str] = []
    if not gate_ready:
        reason_codes.append("stage4_release_gate_not_ready")
    if issue_fix_count < 1:
        reason_codes.append("issue_fix_outcome_missing")
    if len(loopx_domains) < 2:
        reason_codes.append("two_loopx_domains_required")
    for disposition in sorted(APPLICATION_DISPOSITIONS - application_dispositions):
        reason_codes.append(f"application_disposition_missing:{disposition}")
    for action in sorted(OPERATOR_ACTIONS - actions):
        reason_codes.append(f"operator_control_missing:{action}")

    utility_observations = [
        item["utility_observation"]
        for item in normalized_receipts
        if item["utility_observation"] is not None
    ]
    disposition_metrics = {
        f"{disposition}_count": sum(
            item["application_disposition"] == disposition
            for item in normalized_receipts
        )
        for disposition in sorted(APPLICATION_DISPOSITIONS)
    }
    utility_label_metrics = {
        f"utility_{label}_count": sum(
            item["utility_label"] == label for item in utility_observations
        )
        for label in ("helpful", "harmful", "neutral", "unknown")
    }
    totals = {
        "receipt_count": len(normalized_receipts),
        "issue_fix_receipt_count": issue_fix_count,
        "loopx_domain_count": len(loopx_domains),
        **disposition_metrics,
        "utility_observation_count": len(utility_observations),
        **utility_label_metrics,
        "utility_rejected_count": sum(
            item["utility_evaluation"]["status"] == "rejected"
            for item in normalized_receipts
        ),
        "utility_not_requested_count": sum(
            item["utility_evaluation"]["status"] == "not_requested"
            for item in normalized_receipts
        ),
        "latency_ms": sum(item["cost"]["latency_ms"] for item in normalized_receipts),
        "model_tokens": sum(
            item["cost"]["model_tokens"] for item in normalized_receipts
        ),
        "provider_call_count": sum(
            item["cost"]["provider_call_count"] for item in normalized_receipts
        ),
        "intervention_count": sum(
            item["intervention"]["count"] for item in normalized_receipts
        ),
        "bot_feedback_count": sum(
            item["bot_feedback"]["captured"] for item in normalized_receipts
        ),
    }
    ready = not reason_codes
    compact_receipts = []
    for item in normalized_receipts:
        compact_receipts.append(
            {
                "receipt_id": item["receipt_id"],
                "domain_family": item["domain_family"],
                "domain_id": item["domain_id"],
                "application": {
                    "application_id": item["application_id"],
                    "application_receipt_id": item["application_receipt_id"],
                    "artifact_ref": item["artifact_ref"],
                    "corpus_id": item["corpus_id"],
                    "surface_id": item["surface_id"],
                    "outcome": item["application_outcome"],
                    "disposition": item["application_disposition"],
                    "memory_ref_digests": item["memory_ref_digests"],
                    "verification": item["verification"],
                },
                "module_outcome": item["module_outcome"],
                "utility": {
                    "evaluation": item["utility_evaluation"],
                    "observation": item["utility_observation"],
                },
                "cost": item["cost"],
                "intervention": item["intervention"],
                "bot_feedback": item["bot_feedback"],
            }
        )
    return {
        "ok": ready,
        "schema_version": REWARD_MEMORY_DOGFOOD_BATCH_SCHEMA_VERSION,
        "status": "ready_for_bounded_issue_fix_pilot" if ready else "hold",
        "reason_codes": reason_codes,
        "stage4_gate_verified": gate_ready,
        "loopx_domains": loopx_domains,
        "application_dispositions": sorted(application_dispositions),
        "operator_controls": sorted(actions),
        "metrics": totals,
        "receipts": compact_receipts,
        "boundaries": {
            "semantic_uplift_claim_allowed": False,
            "production_rollout_allowed": False,
            "automatic_recall_enabled": False,
            "new_store_provider_or_scheduler_added": False,
            "operator_write_performed": False,
            "raw_content_captured": False,
        },
    }
