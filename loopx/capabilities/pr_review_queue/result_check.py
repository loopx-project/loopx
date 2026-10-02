"""Check a review's declared evidence for consistency, never its truth."""

import re
from collections.abc import Mapping
from typing import Any

from .review_contract import (
    COMPATIBILITY_ASSESSMENT,
    OUTCOME_IMPACT_ASSESSMENT,
    REVIEWER_DECLARATION,
    SCOPE_COVERAGE_ASSESSMENT,
    SEMANTIC_CANDIDATE_DECISIONS,
    SPEC_BASIS_ASSESSMENT,
    VALIDATION_FAILURE_ATTRIBUTION,
    build_review_execution_contract,
    build_review_plan,
)
from .review_body import (
    check_review_body,
    reviewer_declaration_lines,
    visible_review_text,
)


def _missing(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _require_fields(
    blockers: list[str],
    *,
    evidence_id: str,
    value: object,
    fields: object,
    location: str = "",
) -> None:
    if not isinstance(fields, list):
        return
    if not isinstance(value, Mapping):
        blockers.append(f"{evidence_id}:{location or 'value'}_not_object")
        return
    for field in fields:
        if not isinstance(field, str) or _missing(value.get(field)):
            prefix = f"{location}:" if location else ""
            blockers.append(f"{evidence_id}:{prefix}missing_field:{field}")


def _require_items(
    blockers: list[str],
    *,
    evidence_id: str,
    row: Mapping[str, Any],
    requirement: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    items_field = requirement.get("items_field")
    if not isinstance(items_field, str):
        return []
    raw_items = row.get(items_field)
    if not isinstance(raw_items, list):
        blockers.append(f"{evidence_id}:missing_or_invalid_items")
        return []
    count = requirement.get("item_count")
    if isinstance(count, Mapping):
        minimum = count.get("minimum")
        maximum = count.get("maximum")
        if type(minimum) is int and len(raw_items) < minimum:
            blockers.append(f"{evidence_id}:too_few_items")
        if type(maximum) is int and len(raw_items) > maximum:
            blockers.append(f"{evidence_id}:too_many_items")
    items: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            blockers.append(f"{evidence_id}:items[{index}]_not_object")
            continue
        items.append(item)
        _require_fields(
            blockers,
            evidence_id=evidence_id,
            value=item,
            fields=requirement.get("item_fields"),
            location=f"items[{index}]",
        )
    return items


def _required_validation_case_ids(
    requirement: Mapping[str, Any],
    applicability: Mapping[str, Any],
) -> set[str]:
    required: set[str] = set()
    cases = requirement.get("required_cases")
    if not isinstance(cases, list):
        return required
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        case_id = case.get("case_id")
        condition = case.get("required_when")
        if not isinstance(case_id, str):
            continue
        if condition == "always" or (
            isinstance(condition, str) and applicability.get(condition) is True
        ):
            required.add(case_id)
    return required


def _check_validation_failures(
    blockers: list[str], items: list[Mapping[str, Any]],
) -> None:
    """Separate review causality from the merge gate's red-check observation."""

    for item in items:
        case_id = str(item.get("case_id") or "unknown")
        key = f"validation_matrix:{case_id}"
        status = item.get("status")
        if not isinstance(status, str) or status not in {
            "passed", "failed", "skipped", "pending", "unverified", "not_applicable",
        }:
            blockers.append(f"{key}:invalid_status")
        if type(item.get("required")) is not bool:
            blockers.append(f"{key}:required_not_boolean")
        if not isinstance(status, str):
            continue
        if item.get("required") is not True or status == "passed":
            continue
        if status in {"pending", "unverified", "not_applicable"}:
            blockers.append(f"{key}:required_validation_not_proven")
            continue
        attribution = item.get("failure_attribution")
        if not isinstance(attribution, Mapping):
            blockers.append(f"{key}:failure_attribution_missing")
            continue
        disposition = attribution.get("disposition")
        if disposition not in VALIDATION_FAILURE_ATTRIBUTION["dispositions"]:
            blockers.append(f"{key}:invalid_failure_disposition")
            continue
        if disposition not in VALIDATION_FAILURE_ATTRIBUTION["non_blocking_dispositions"]:
            blockers.append(f"{key}:attributable_or_unresolved_failure")
            continue
        _require_fields(
            blockers, evidence_id=key, value=attribution,
            fields=VALIDATION_FAILURE_ATTRIBUTION["common_fields"],
        )
        if disposition == "pre_existing_unrelated":
            _require_fields(
                blockers, evidence_id=key, value=attribution,
                fields=VALIDATION_FAILURE_ATTRIBUTION["pre_existing_fields"],
            )
            baseline = attribution.get("baseline_failure_signature")
            head = attribution.get("head_failure_signature")
            if not isinstance(baseline, str) or not baseline.strip() or baseline != head:
                blockers.append(f"{key}:failure_signature_changed")
            if attribution.get("base_revision") == attribution.get("head_revision"):
                blockers.append(f"{key}:base_and_head_not_distinct")
        else:
            _require_fields(
                blockers, evidence_id=key, value=attribution,
                fields=VALIDATION_FAILURE_ATTRIBUTION["external_fields"],
            )


def _check_compatibility_assessment(blockers: list[str], value: object) -> None:
    key = "code_volume:compatibility_assessment"
    contract = COMPATIBILITY_ASSESSMENT
    _require_fields(blockers, evidence_id=key, value=value, fields=contract["fields"])
    if not isinstance(value, Mapping):
        return
    decision = value.get("decision")
    if decision not in contract["decision_values"]:
        blockers.append(f"{key}:invalid_decision")
        return
    if decision in contract["blocking_decisions"]:
        blockers.append(f"{key}:blocking_decision")
    if decision == "not_applicable":
        return
    _require_fields(blockers, evidence_id=key, value=value, fields=contract["applicable_fields"])
    boundary = value.get("deployment_boundary")
    if boundary not in contract["deployment_boundary_values"]:
        blockers.append(f"{key}:invalid_deployment_boundary")
    if boundary == "unknown" and decision != "not_yet_proven":
        blockers.append(f"{key}:unknown_boundary_cannot_justify_decision")


def _check_outcome_impact(blockers: list[str], value: object) -> None:
    key = "problem_context:outcome_impact"
    contract = OUTCOME_IMPACT_ASSESSMENT
    if not isinstance(value, Mapping):
        blockers.append(f"{key}:missing_assessment")
        return
    for dimension in contract["dimensions"]:
        row = value.get(dimension)
        row_key = f"{key}:{dimension}"
        _require_fields(blockers, evidence_id=row_key, value=row, fields=contract["fields"])
        if not isinstance(row, Mapping):
            continue
        decision = row.get("decision")
        if decision not in contract["decision_values"]:
            blockers.append(f"{row_key}:invalid_decision")
        if decision == "not_applicable":
            continue
        _require_fields(blockers, evidence_id=row_key, value=row, fields=contract["applicable_fields"])
        refs = row.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(
            not isinstance(ref, str) or not ref.strip() for ref in refs
        ):
            blockers.append(f"{row_key}:missing_evidence_refs")
        if decision in contract["blocking_decisions"]:
            blockers.append(f"{row_key}:blocking_decision")
            _require_fields(blockers, evidence_id=row_key, value=row, fields=["minimum_repair"])
        if decision == "accepted_tradeoff":
            _require_fields(blockers, evidence_id=row_key, value=row,
                            fields=["acceptance_basis", "bounded_cost_and_recovery"])


# The declaration names a product family, so the characters that mark a URL,
# path, host:port, account or ARN have no place in it. This is a shape rule,
# not a product-name denylist. Known false positive: router- or
# version-qualified ids such as `vendor/model` or `model@version` are rejected;
# declare the product-family name instead. A typed runtime-reported identity
# would replace this text check once a host supplies one.
_DECLARED_NAME_FORBIDDEN = re.compile(r"[/:@]")
_PUBLISHED_LINE_FORBIDDEN = re.compile(r"://|@")


def _reviewer_errors(value: object, body: str) -> list[str]:
    key = "reviewer"
    contract = REVIEWER_DECLARATION
    if not isinstance(value, Mapping):
        return [f"{key}:missing_declaration"]
    actor_kind = value.get("actor_kind")
    if actor_kind not in contract["actor_kinds"]:
        return [f"{key}:invalid_actor_kind"]
    errors: list[str] = []
    if value.get("declaration_source") not in contract["declaration_sources"]:
        errors.append(f"{key}:invalid_declaration_source")
    declared: list[str] = [str(actor_kind)]
    if actor_kind == "model_agent":
        for field in contract["model_agent_fields"]:
            text = value.get(field)
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{key}:missing_field:{field}")
            elif _DECLARED_NAME_FORBIDDEN.search(text):
                errors.append(f"{key}:not_a_product_family_name:{field}")
            else:
                declared.append(text.strip())
    lines = reviewer_declaration_lines(body)
    if len(lines) != 1:
        errors.append(f"{key}:body_line_missing_or_ambiguous")
    elif _PUBLISHED_LINE_FORBIDDEN.search(lines[0]):
        errors.append(f"{key}:body_line_carries_runtime_detail")
    elif any(token.casefold() not in lines[0].casefold() for token in declared):
        errors.append(f"{key}:body_line_disagrees_with_declaration")
    return errors


def _check_spec_basis(blockers: list[str], value: object) -> None:
    key = "problem_context:spec_basis"
    contract = SPEC_BASIS_ASSESSMENT
    _require_fields(blockers, evidence_id=key, value=value, fields=contract["fields"])
    if not isinstance(value, Mapping):
        return
    decision = value.get("decision")
    if decision not in contract["decision_values"]:
        blockers.append(f"{key}:invalid_decision")
        return
    source = value.get("spec_source")
    if source not in contract["spec_source_values"]:
        blockers.append(f"{key}:invalid_spec_source")
    if decision in contract["blocking_decisions"]:
        blockers.append(f"{key}:blocking_decision")
    if decision == "no_spec" and source != "none":
        blockers.append(f"{key}:no_spec_cannot_cite_a_source")
    if decision != "mapped":
        return
    if source == "none":
        blockers.append(f"{key}:mapped_without_spec_source")
    _require_fields(blockers, evidence_id=key, value=value, fields=contract["mapped_fields"])
    # The reference and revision are published and matched against the body, so
    # a non-string value would pass the non-empty check and then never be
    # compared; require text here, as for criterion_id below.
    for field in contract["published_text_fields"]:
        text = value.get(field)
        if not _missing(text) and (not isinstance(text, str) or not text.strip()):
            blockers.append(f"{key}:invalid_{field}")
    criteria = _require_items(
        blockers, evidence_id=key, row=value,
        requirement={"items_field": "criteria", "item_fields": contract["criterion_fields"],
                     "item_count": {"minimum": 1}},
    )
    seen: set[str] = set()
    for criterion in criteria:
        # Membership, hashing and publication all assume a public identity.
        # A bool hashes and is non-empty, and a non-string identity silently
        # falls out of the published-body check, so validate the type here
        # rather than letting a later set or dict operation decide.
        criterion_id = criterion.get("criterion_id")
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            blockers.append(f"{key}:invalid_criterion_id")
            continue
        if criterion_id in seen:
            blockers.append(f"{key}:duplicate_criterion:{criterion_id}")
        seen.add(criterion_id)
        disposition = criterion.get("disposition")
        if not isinstance(disposition, str) or disposition not in contract["disposition_fields"]:
            blockers.append(f"{key}:invalid_disposition:{criterion_id}")
            continue
        _require_fields(blockers, evidence_id=f"{key}:{criterion_id}", value=criterion,
                        fields=contract["disposition_fields"][disposition])
        if disposition in contract["blocking_dispositions"]:
            blockers.append(f"{key}:unmet_criterion:{criterion_id}")


def _unpublished_spec_references(value: object, body: str) -> list[str]:
    # The structured result stays local; another operator reads only the body.
    # A path alone moves with the branch, so the immutable revision the review
    # judged against has to travel with it for that reader to open the same text.
    if not isinstance(value, Mapping) or value.get("decision") != "mapped":
        return []
    text = visible_review_text(body).casefold()
    references = [value.get("spec_ref"), value.get("spec_revision")]
    criteria = value.get("criteria")
    if isinstance(criteria, list):
        references += [item.get("criterion_id") for item in criteria if isinstance(item, Mapping)]
    # Non-text identities are rejected by _check_spec_basis; they are skipped
    # here only because they have no text to look for in the body.
    return [
        f"review_body:spec_reference_not_published:{reference}"
        for reference in references
        if isinstance(reference, str) and reference.strip()
        and reference.strip().casefold() not in text
    ]


def _check_scope_coverage(blockers: list[str], value: object) -> None:
    key = "observable_semantics:scope_coverage"
    contract = SCOPE_COVERAGE_ASSESSMENT
    _require_fields(blockers, evidence_id=key, value=value, fields=contract["fields"])
    if not isinstance(value, Mapping):
        return
    decision = value.get("decision")
    if decision not in contract["decision_values"]:
        blockers.append(f"{key}:invalid_decision")
    if decision in {"overbroad", "not_yet_proven"}:
        blockers.append(f"{key}:blocking_decision")
    if decision == "not_applicable":
        return
    _require_fields(blockers, evidence_id=key, value=value, fields=contract["applicable_fields"])
    cases = _require_items(blockers, evidence_id=key, row=value,
                           requirement={"items_field": "cases", "item_fields": contract["case_fields"]})
    ids = [case.get("case_id") for case in cases]
    for case_id in contract["case_ids"]:
        if ids.count(case_id) != 1:
            blockers.append(f"{key}:missing_or_duplicate_case:{case_id}")
    for case in cases:
        case_id = case.get("case_id")
        status = case.get("status")
        if case_id not in contract["case_ids"] or status not in contract["case_statuses"]:
            blockers.append(f"{key}:invalid_case")
        elif status in {"failed", "unverified"}:
            blockers.append(f"{key}:case_not_proven:{case_id}")
        elif status == "not_applicable" and _missing(case.get("reason")):
            blockers.append(f"{key}:missing_case_reason:{case_id}")
        if case_id == "covered_subject" and status != "passed":
            blockers.append(f"{key}:covered_subject_not_proven")


def check_review_result(
    packet: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    target = result.get("target_exact_head")
    items = packet.get("pull_requests")
    if not isinstance(items, list):
        raise TypeError("review packet must contain pull_requests")
    matches = [
        item
        for item in items
        if isinstance(item, Mapping)
        and build_review_plan(item)["target"]["exact_head_key"] == target
        and target is not None
    ]
    if len(matches) != 1:
        raise ValueError("review result must match exactly one packet PR head")
    if not str(matches[0].get("review_action_kind") or "").strip():
        raise ValueError(
            "review result cannot execute an inventory-only exact head; regenerate "
            "the packet with an explicit fresh-audit request"
        )
    # Rebuild policy from this installed capability, not caller-supplied plans.
    plan = build_review_plan(matches[0])
    applicability = plan.get("applicability")
    if not isinstance(applicability, Mapping):
        applicability = {}
    contract = build_review_execution_contract()
    requirements = {
        row["evidence_id"]: row for row in contract["evidence_requirements"]
    }
    blockers: list[str] = []
    errors: list[str] = []
    revision = result.get("review_policy_revision")
    if type(revision) is not int or revision != contract["policy_revision"]:
        blockers.append("review_policy_revision:stale_or_missing")
    if result.get("schema_version") != "pull_request_review_result_v1":
        errors.append("unsupported_result_schema")
    evidence = result.get("evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
        errors.append("evidence_not_object")
    evidence_ids = list(plan["required_evidence_ids"])
    # Contract findings supplied for docs-only reviews still constrain approval.
    if "semantic_alignment" in evidence and "semantic_alignment" not in evidence_ids:
        evidence_ids.append("semantic_alignment")
    for key in evidence_ids:
        row = evidence.get(key)
        if not isinstance(row, Mapping):
            blockers.append(f"{key}:missing")
            continue
        status = row.get("status")
        if status != "verified":
            blockers.append(f"{key}:not_verified")
        if status not in contract["evidence_status_values"]:
            errors.append(f"{key}:invalid_status")
        detail = {k: v for k, v in row.items() if k not in {"status", "verdict"}}
        if not any(v not in (None, "", [], {}) for v in detail.values()):
            blockers.append(f"{key}:missing_evidence_detail")
        if status == "verified":
            requirement = requirements[key]
            if key == "problem_context":
                _check_outcome_impact(blockers, row.get("outcome_impact"))
                _check_spec_basis(blockers, row.get("spec_basis"))
            if key == "code_volume":
                _check_compatibility_assessment(blockers, row.get("compatibility_assessment"))
            if key == "observable_semantics":
                _check_scope_coverage(blockers, row.get("scope_coverage"))
            if key == "semantic_alignment":
                decision = row.get("candidate_decision")
                verdict = row.get("verdict")
                if (verdict != "not_applicable" or decision is not None) and (
                    decision not in SEMANTIC_CANDIDATE_DECISIONS
                ):
                    blockers.append("semantic_alignment:invalid_candidate_decision")
                if decision == "unknown" and verdict not in (
                    "advisory", "not_yet_proven", "violated"
                ):
                    blockers.append("semantic_alignment:unknown_cannot_claim_alignment")
                if verdict == "not_applicable" and decision in (
                    "extend_vocabulary", "create_vocabulary", "compatibility_only"
                ):
                    blockers.append("semantic_alignment:contract_change_requires_evidence")
            _require_fields(
                blockers,
                evidence_id=key,
                value=row,
                fields=requirement.get("fields"),
            )
            fields_by_verdict = requirement.get("fields_by_verdict", {})
            verdict = row.get("verdict")
            if isinstance(verdict, str):
                _require_fields(
                    blockers,
                    evidence_id=key,
                    value=row,
                    fields=fields_by_verdict.get(verdict),
                )
            items = _require_items(
                blockers,
                evidence_id=key,
                row=row,
                requirement=requirement,
            )
            if key == "validation_matrix":
                _check_validation_failures(blockers, items)
            positive_field = requirement.get("positive_field")
            if isinstance(positive_field, str):
                _require_fields(
                    blockers,
                    evidence_id=key,
                    value=row.get(positive_field),
                    fields=requirement.get("positive_fields"),
                    location=positive_field,
                )
            negative_field = requirement.get("negative_field")
            if (
                isinstance(negative_field, str)
                and applicability.get("negative_walkthrough_required") is True
            ):
                _require_fields(
                    blockers,
                    evidence_id=key,
                    value=row.get(negative_field),
                    fields=requirement.get("negative_fields"),
                    location=negative_field,
                )
            required_cases = _required_validation_case_ids(requirement, applicability)
            if required_cases:
                observed_cases = {
                    str(item.get("case_id"))
                    for item in items
                    if isinstance(item.get("case_id"), str)
                }
                for case_id in sorted(required_cases - observed_cases):
                    blockers.append(f"{key}:missing_required_case:{case_id}")
        allowed = requirements[key].get("verdict_values")
        if allowed and row.get("verdict") not in allowed:
            blockers.append(f"{key}:missing_or_invalid_verdict")
        rejected = contract["completion_gate"]["blocking_evidence_verdicts"].get(
            key, []
        )
        if row.get("verdict") in rejected:
            blockers.append(f"{key}:blocking_verdict")
    findings = result.get("findings")
    if not isinstance(findings, list):
        errors.append("findings_not_array")
    else:
        for finding in findings:
            if not isinstance(finding, Mapping):
                errors.append("finding_not_object")
                continue
            severity = finding.get("severity")
            if "blocking" in finding and not isinstance(finding["blocking"], bool):
                errors.append("invalid_finding_blocking_flag")
            if severity not in {"P0", "P1", "P2", "P3"}:
                errors.append("invalid_finding_severity")
            if finding.get("blocking") is True or severity in {"P0", "P1"}:
                blockers.append("unresolved_blocking_finding")
    verdict = result.get("verdict")
    body = check_review_body(str(result.get("review_body") or ""),
                             head_oid=str(matches[0].get("head_oid") or ""),
                             behavior_bearing=applicability.get("behavior_bearing_change") is True)
    errors.extend(f"review_body:{reason}" for reason in body["invalid_reasons"])
    body_text = str(result.get("review_body") or "")
    errors.extend(_reviewer_errors(result.get("reviewer"), body_text))
    problem_context = evidence.get("problem_context")
    errors.extend(_unpublished_spec_references(
        problem_context.get("spec_basis") if isinstance(problem_context, Mapping) else None,
        body_text,
    ))
    if body["verdict"] is not None and body["verdict"] != verdict:
        errors.append("review_body:verdict_mismatch")
    if verdict not in {"APPROVE", "REQUEST_CHANGES"}:
        errors.append("unsupported_verdict")
    if verdict == "APPROVE" and blockers:
        errors.append("approval_contradicts_evidence")
    if verdict == "REQUEST_CHANGES" and not blockers:
        errors.append("request_changes_without_blocker")
    return {
        "ok": not errors,
        "schema_version": "pull_request_review_result_check_v0",
        "target_exact_head": target,
        "verdict": verdict,
        "approval_consistent": not errors and not blockers,
        "errors": sorted(set(errors)),
        "approval_blockers": sorted(set(blockers)),
        "review_body_check": body,
        "evidence_truth_verified": False,
        "remote_head_verified": False,
        "external_writes_performed": False,
    }
