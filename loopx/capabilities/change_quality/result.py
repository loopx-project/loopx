from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from ...feedback import validate_public_safe_text
from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result


CHANGE_QUALITY_RESULT_SCHEMA_VERSION = "change_quality_agent_result_v2"
CHANGE_QUALITY_GUARDRAIL_SCHEMA_VERSION = "change_quality_guardrail_summary_v0"

RISK_SEVERITIES = frozenset({"blocker", "warning", "advisory"})
REUSE_OUTCOMES = frozenset({"reused", "retained", "deferred", "not_applicable"})
SIMPLIFICATION_OUTCOMES = frozenset(
    {"fixed", "retained", "deferred", "not_applicable"}
)
VALIDATION_STATUSES = frozenset({"passed", "failed", "skipped"})
EVIDENCE_REF_KINDS = frozenset({"path", "instruction", "validator"})

REVIEW_LENSES = (
    {
        "lens_id": "reuse",
        "question": (
            "Does the change reuse established helpers and durable knowledge "
            "instead of duplicating them?"
        ),
    },
    {
        "lens_id": "type_api_boundary",
        "question": (
            "Are types, schemas, compatibility windows, and caller-facing "
            "contracts explicit and coherent?"
        ),
    },
    {
        "lens_id": "configuration",
        "question": (
            "Does configuration stay single-sourced, validated, and free of "
            "hidden mode coupling?"
        ),
    },
    {
        "lens_id": "runtime_ownership",
        "question": (
            "Are lifecycle, concurrency, state, and side-effect ownership "
            "placed in the correct runtime boundary?"
        ),
    },
    {
        "lens_id": "quality_simplification",
        "question": (
            "Can the final behavior be expressed with less indirection, "
            "branching, duplication, or speculative abstraction?"
        ),
    },
    {
        "lens_id": "efficiency",
        "question": (
            "Does the change avoid unnecessary work, unbounded growth, and "
            "avoidable hot-path cost?"
        ),
    },
    {
        "lens_id": "error_supervision",
        "question": (
            "Are failures observable, actionable, and supervised without "
            "silent fallback or broad exception plumbing?"
        ),
    },
    {
        "lens_id": "test_validation",
        "question": (
            "Do tests and repository-native validators prove the intended "
            "semantics, including important negative paths?"
        ),
    },
    {
        "lens_id": "documentation_comments",
        "question": (
            "Do names, comments, and docs explain the current contract without "
            "stale narration or duplicated truth?"
        ),
    },
    {
        "lens_id": "security_release",
        "question": (
            "Are security, privacy, permissions, migrations, and release "
            "compatibility handled at the changed boundaries?"
        ),
    },
)
REVIEW_LENS_IDS = tuple(item["lens_id"] for item in REVIEW_LENSES)
SIMPLIFY_PRIMARY_LENS_IDS = ("reuse", "quality_simplification")
SIMPLIFY_GUARDRAIL_LENS_IDS = tuple(
    lens_id for lens_id in REVIEW_LENS_IDS if lens_id not in SIMPLIFY_PRIMARY_LENS_IDS
)


def _bounded_text(value: Any, *, field: str, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    validate_public_safe_text(field, text)
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return text


def _relative_path(value: Any, *, field: str) -> str | None:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be a repo-relative path")
    validate_public_safe_text(field, text)
    return path.as_posix()


def _bounded_text_list(
    value: Any,
    *,
    field: str,
    item_limit: int,
    count_limit: int,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    if len(value) > count_limit:
        raise ValueError(f"{field} supports at most {count_limit} items")
    result = [
        _bounded_text(item, field=f"{field}[]", limit=item_limit) for item in value
    ]
    if any(not item for item in result):
        raise ValueError(f"{field} cannot contain empty values")
    return result


def _normalize_evidence_ref(value: Any, *, field: str) -> str:
    text = _bounded_text(value, field=field, limit=400)
    kind, separator, target = text.partition(":")
    if not separator or kind not in EVIDENCE_REF_KINDS:
        raise ValueError(
            f"{field} must use one of "
            f"{[f'{item}:<ref>' for item in sorted(EVIDENCE_REF_KINDS)]}"
        )
    if kind in {"path", "instruction"}:
        normalized_target = _relative_path(target, field=field)
    else:
        normalized_target = _bounded_text(target, field=field, limit=160)
    if not normalized_target:
        raise ValueError(f"{field} requires a non-empty reference target")
    return f"{kind}:{normalized_target}"


def _normalize_evidence_refs(value: Any, *, field: str) -> list[str]:
    refs = [
        _normalize_evidence_ref(item, field=f"{field}[]")
        for item in _bounded_text_list(
            value,
            field=field,
            item_limit=400,
            count_limit=20,
        )
    ]
    if len(set(refs)) != len(refs):
        raise ValueError(f"{field} must not contain duplicates")
    return refs


def _normalize_conclusion(
    value: Any,
    *,
    field: str,
    allowed_outcomes: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    outcome = str(value.get("outcome") or "").strip().lower()
    if outcome not in allowed_outcomes:
        raise ValueError(f"{field}.outcome must be one of {sorted(allowed_outcomes)}")
    summary = _bounded_text(value.get("summary"), field=f"{field}.summary", limit=400)
    if not summary:
        raise ValueError(f"{field}.summary is required")
    evidence_refs = _normalize_evidence_refs(
        value.get("evidence_refs"),
        field=f"{field}.evidence_refs",
    )
    if not evidence_refs:
        raise ValueError(f"{field}.evidence_refs must contain grounded evidence")
    return {
        "outcome": outcome,
        "summary": summary,
        "evidence_refs": evidence_refs,
    }


def _normalize_risk(value: Any, *, index: int) -> dict[str, Any]:
    field = f"risks[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    category = str(value.get("category") or "").strip()
    if category not in SIMPLIFY_GUARDRAIL_LENS_IDS:
        raise ValueError(
            f"{field}.category must be one of {sorted(SIMPLIFY_GUARDRAIL_LENS_IDS)}"
        )
    severity = str(value.get("severity") or "").strip().lower()
    if severity not in RISK_SEVERITIES:
        raise ValueError(
            f"{field}.severity must be one of {sorted(RISK_SEVERITIES)}"
        )
    code = _bounded_text(value.get("code"), field=f"{field}.code", limit=80)
    message = _bounded_text(value.get("message"), field=f"{field}.message", limit=320)
    if not code or not message:
        raise ValueError(f"{field} requires code and message")
    evidence_refs = _normalize_evidence_refs(
        value.get("evidence_refs"),
        field=f"{field}.evidence_refs",
    )
    if not evidence_refs:
        raise ValueError(f"{field}.evidence_refs must contain grounded evidence")
    risk = {
        "category": category,
        "severity": severity,
        "code": code,
        "message": message,
        "resolved": value.get("resolved") is True,
        "evidence_refs": evidence_refs,
    }
    path = _relative_path(value.get("path"), field=f"{field}.path")
    if path:
        risk["path"] = path
    line = value.get("line")
    if line is not None:
        if not isinstance(line, int) or line < 1:
            raise ValueError(f"{field}.line must be a positive integer")
        risk["line"] = line
    return risk


def _normalize_validation(value: Any, *, index: int) -> dict[str, Any]:
    field = f"validation[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    status = str(value.get("status") or "").strip().lower()
    if status not in VALIDATION_STATUSES:
        raise ValueError(f"{field}.status must be one of {sorted(VALIDATION_STATUSES)}")
    validator = _bounded_text(
        value.get("validator"),
        field=f"{field}.validator",
        limit=120,
    )
    scope = _bounded_text(value.get("scope"), field=f"{field}.scope", limit=240)
    if not validator or not scope:
        raise ValueError(f"{field} requires validator and scope")
    required = value.get("required") is not False
    evidence: dict[str, Any] = {
        "validator": validator,
        "status": status,
        "scope": scope,
        "required": required,
    }
    command = _bounded_text(value.get("command"), field=f"{field}.command", limit=320)
    reason = _bounded_text(value.get("reason"), field=f"{field}.reason", limit=320)
    if command:
        evidence["command"] = command
    if reason:
        evidence["reason"] = reason
    if status in {"failed", "skipped"} and not reason:
        raise ValueError(f"{field} with status={status} requires reason")
    if "covers_paths" in value:
        evidence["covers_paths"] = [
            _relative_path(path, field=f"{field}.covers_paths[]")
            for path in _bounded_text_list(
                value["covers_paths"], field=f"{field}.covers_paths",
                item_limit=240, count_limit=200,
            )
        ]
    if "failure_attribution" in value:
        _validate_attribution_public_safety(value["failure_attribution"], field=field)
        evidence["failure_attribution"] = value["failure_attribution"]
    return evidence


def _validate_attribution_public_safety(value: Any, *, field: str) -> None:
    # IO/privacy codec only. Shape, freshness and qualification belong to TS.
    if isinstance(value, str):
        validate_public_safe_text(field, value)
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_attribution_public_safety(item, field=f"{field}.{key}")
    elif isinstance(value, list):
        for item in value:
            _validate_attribution_public_safety(item, field=field)


def _qualify_validation(
    validation: list[dict[str, Any]], scope: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        result = effect_runtime_result(
            "capabilities.change_quality.validation_gate",
            {"validation": validation, "scope": scope},
        )
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    if not isinstance(result, dict):
        raise TypeError("typed change-quality validation result mismatch")
    return result


def _validate_evidence_targets(
    refs: list[str],
    *,
    field: str,
    changed_files: set[str],
    instruction_refs: set[str],
    validator_ids: set[str],
) -> None:
    targets = {
        "path": changed_files,
        "instruction": instruction_refs,
        "validator": validator_ids,
    }
    for evidence_ref in refs:
        kind, target = evidence_ref.split(":", 1)
        if target not in targets[kind]:
            raise ValueError(f"{field} references unknown evidence: {evidence_ref}")


def _normalize_simplification_result(
    value: Any,
    *,
    safe_fix_allowed: bool,
) -> dict[str, Any]:
    simplification = _normalize_conclusion(
        value,
        field="simplification",
        allowed_outcomes=SIMPLIFICATION_OUTCOMES,
    )
    safe_fix_applied = bool(
        isinstance(value, dict) and value.get("safe_fix_applied") is True
    )
    if safe_fix_applied and not safe_fix_allowed:
        raise ValueError(
            "result reports simplification.safe_fix_applied but goal policy "
            "forbids safe fixes"
        )
    if safe_fix_applied != (simplification["outcome"] == "fixed"):
        raise ValueError(
            "simplification.outcome=fixed and safe_fix_applied=true must agree"
        )
    simplification["safe_fix_applied"] = safe_fix_applied
    return simplification


def _normalize_risks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("risks must be an array")
    if len(value) > 20:
        raise ValueError("risks supports at most 20 items")
    risks = [_normalize_risk(item, index=index) for index, item in enumerate(value)]
    risk_codes = [item["code"] for item in risks]
    if len(set(risk_codes)) != len(risk_codes):
        raise ValueError("risk codes must be unique")
    return risks


def _normalize_validations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("validation must contain at least one item")
    if len(value) > 20:
        raise ValueError("validation supports at most 20 items")
    validation = [
        _normalize_validation(item, index=index) for index, item in enumerate(value)
    ]
    validator_ids = [item["validator"] for item in validation]
    if len(set(validator_ids)) != len(validator_ids):
        raise ValueError("validation validator ids must be unique")
    return validation


def normalize_change_quality_result(
    value: Any,
    *,
    expected_fingerprint: str,
    safe_fix_allowed: bool,
    expected_changed_files: list[str] | None = None,
    expected_instruction_refs: list[str] | None = None,
    expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("result JSON root must be an object")
    if value.get("schema_version") != CHANGE_QUALITY_RESULT_SCHEMA_VERSION:
        raise ValueError(
            f"result schema_version must be {CHANGE_QUALITY_RESULT_SCHEMA_VERSION}"
        )
    allowed_fields = {
        "schema_version",
        "scope_fingerprint",
        "reviewed_final_scope",
        "reuse",
        "simplification",
        "risks",
        "validation",
    }
    unsupported = sorted(set(value) - allowed_fields)
    if unsupported:
        raise ValueError(f"result contains unsupported fields: {unsupported}")
    fingerprint = str(value.get("scope_fingerprint") or "").strip()
    if fingerprint != expected_fingerprint:
        raise ValueError(
            "result scope_fingerprint does not match the current exact diff; "
            "rerun prepare after every safe fix"
        )
    if value.get("reviewed_final_scope") is not True:
        raise ValueError("result must set reviewed_final_scope=true")

    reuse = _normalize_conclusion(
        value.get("reuse"),
        field="reuse",
        allowed_outcomes=REUSE_OUTCOMES,
    )
    simplification = _normalize_simplification_result(
        value.get("simplification"),
        safe_fix_allowed=safe_fix_allowed,
    )
    risks = _normalize_risks(value.get("risks"))
    validation = _normalize_validations(value.get("validation"))
    if any("failure_attribution" in item for item in validation):
        validation = _qualify_validation(validation, expected_scope)["validation"]
    changed_files = set(expected_changed_files or [])
    instruction_refs = set(expected_instruction_refs or [])
    validator_ids = {item["validator"] for item in validation}
    for field, conclusion in (
        ("reuse", reuse),
        ("simplification", simplification),
    ):
        _validate_evidence_targets(
            conclusion["evidence_refs"],
            field=field,
            changed_files=changed_files,
            instruction_refs=instruction_refs,
            validator_ids=validator_ids,
        )
    for index, risk in enumerate(risks):
        _validate_evidence_targets(
            risk["evidence_refs"],
            field=f"risks[{index}]",
            changed_files=changed_files,
            instruction_refs=instruction_refs,
            validator_ids=validator_ids,
        )
        if (
            "path" in risk
            and expected_changed_files is not None
            and risk["path"] not in changed_files
        ):
            raise ValueError(f"risks[{index}].path must name a changed file")

    return {
        "schema_version": CHANGE_QUALITY_RESULT_SCHEMA_VERSION,
        "scope_fingerprint": fingerprint,
        "reviewed_final_scope": True,
        "reuse": reuse,
        "simplification": simplification,
        "risks": risks,
        "validation": validation,
    }


def derive_change_quality_guardrails(
    result: dict[str, Any], *, expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    risks = result["risks"]
    validation = result["validation"]
    qualification = _qualify_validation(validation, expected_scope)
    states: list[dict[str, Any]] = []
    blocking_codes: list[str] = []
    for guardrail_id in SIMPLIFY_GUARDRAIL_LENS_IDS:
        category_risks = [item for item in risks if item["category"] == guardrail_id]
        unresolved_blockers = [
            item["code"]
            for item in category_risks
            if item["severity"] == "blocker" and item["resolved"] is not True
        ]
        validation_blockers: list[str] = []
        validation_risks: list[str] = []
        if guardrail_id == "test_validation":
            validation_blockers = qualification["blocking_codes"]
            validation_risks = qualification["risk_codes"]
        current_blockers = [*unresolved_blockers, *validation_blockers]
        blocking_codes.extend(current_blockers)
        unresolved_risks = [
            item["code"] for item in category_risks if item["resolved"] is not True
        ]
        if current_blockers:
            status = "blocked"
        elif unresolved_risks or validation_risks:
            status = "risk"
        elif category_risks:
            status = "resolved"
        elif guardrail_id == "test_validation" and validation:
            status = "satisfied"
        else:
            status = "not_triggered"
        states.append(
            {
                "guardrail_id": guardrail_id,
                "status": status,
                "risk_codes": [item["code"] for item in category_risks],
                "blocking_codes": current_blockers,
            }
        )
    return {
        "schema_version": CHANGE_QUALITY_GUARDRAIL_SCHEMA_VERSION,
        "derived": True,
        "states": states,
        "blocking_codes": blocking_codes,
    }


def change_quality_result_decision(
    result: dict[str, Any], *, expected_scope: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    guardrails = derive_change_quality_guardrails(result, expected_scope=expected_scope)
    unresolved = list(guardrails["blocking_codes"])
    return ("fail", unresolved) if unresolved else ("pass", [])
