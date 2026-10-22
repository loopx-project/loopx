"""Visual evidence consistency in the existing user-experience review.

Declarations and capture references cannot prove readability or inspected pixels.
This helper owns no rendering, scoring, execution or approval authority.
"""

from collections.abc import Mapping
from typing import Any

from .review_body import normalized_review_prose, review_section_text


VISUAL_ASSESSMENT: dict[str, Any] = {
    "decision_values": ["not_applicable", "inspected", "not_yet_proven", "regression"],
    "blocking_decisions": ["not_yet_proven", "regression"],
    "fields": ["decision", "reason"],
    "applicable_fields": ["content_scope", "expanded_content", "responsive_layout", "observations", "unverified_scope"],
    "observation_fields": ["viewport", "state", "method", "observation", "evidence_ref", "revision", "build_boundary"],
    "state_values": ["default", "expanded", "recovery"],
    "method_values": ["visual_inspection", "automated_check"],
    "publication": {"section": "我的整体评价", "fields": ["reason", "unverified_scope"]},
    "rule": (
        "Complete visual_review inside outcome_impact.user_experience for every review. "
        "Use not_applicable with the inspected path in reason when no rendered presentation "
        "changes; a CLI or internal-only change does not require screenshots. Inspect the actual "
        "affected whole page using representative long/repeated content and mixed states, including "
        "lower regions. Name that data in content_scope. Set expanded_content and responsive_layout "
        "from the affected journey and accepted design, explaining the selection in reason. "
        "For density/disclosure changes, inspect the default view and a fully expanded item; "
        "for responsive changes also inspect a narrow view, including expanded narrow content "
        "when both apply. Each observation identifies its viewport (narrow boolean), state, "
        "method, actual composition, capture/walkthrough reference, revision and build boundary. "
        "Compare hierarchy, repeated explanations, body typography, primary values versus "
        "method/hash/provenance and access to failures, unknowns and full evidence. Explain what "
        "became easier to find and where detail remains reachable. Default folding, field presence, "
        "data correctness and no horizontal overflow are automated checks, not visual inspection. "
        "Reuse existing captures with their original revision and an explicit unchanged-assumption "
        "check in the observation; do not rerun every screen or paid model for each PR. "
        "inspected records observation, not a quality pass: retain regressions and material "
        "unverified readability as regression or not_yet_proven even if the parent UX decision "
        "says improved. Record unverified_scope explicitly, including none when justified. "
        "Publish reason and unverified_scope in 我的整体评价. Missing evidence can support an "
        "honest REQUEST_CHANGES, never APPROVE. The checker checks declared coverage and "
        "consistency, not image truth, aesthetics, comprehension or model compliance."
    ),
}


def check_visual_assessment(value: object, *, experience_decision: object) -> list[str]:
    key = "problem_context:outcome_impact:user_experience:visual_review"
    blockers: list[str] = []
    contract = VISUAL_ASSESSMENT
    if not isinstance(value, Mapping):
        return [f"{key}:missing_assessment"]

    def require_text(row: Mapping[str, Any], fields: list[str], prefix: str) -> None:
        for field in fields:
            text = row.get(field)
            if not isinstance(text, str) or not text.strip():
                blockers.append(f"{prefix}:missing_or_invalid_field:{field}")

    require_text(value, contract["fields"], key)
    decision = value.get("decision")
    if decision not in contract["decision_values"]:
        return [*blockers, f"{key}:invalid_decision"]
    if decision == "not_applicable":
        return blockers
    if decision in contract["blocking_decisions"]:
        blockers.append(f"{key}:blocking_decision")
    if experience_decision == "not_applicable":
        blockers.append(f"{key}:contradicts_experience_inapplicability")
    require_text(value, ["content_scope", "unverified_scope"], key)
    for field in ("expanded_content", "responsive_layout"):
        if type(value.get(field)) is not bool:
            blockers.append(f"{key}:missing_or_invalid_field:{field}")
    observations = value.get("observations")
    if not isinstance(observations, list):
        return [*blockers, f"{key}:missing_observations"]
    coverage: set[tuple[str, bool]] = set()
    for index, row in enumerate(observations):
        prefix = f"{key}:observations[{index}]"
        if not isinstance(row, Mapping):
            blockers.append(f"{prefix}:not_object")
            continue
        before = len(blockers)
        require_text(row, contract["observation_fields"], prefix)
        if row.get("state") not in contract["state_values"]:
            blockers.append(f"{prefix}:invalid_state")
        if row.get("method") not in contract["method_values"]:
            blockers.append(f"{prefix}:invalid_method")
        if type(row.get("narrow")) is not bool:
            blockers.append(f"{prefix}:missing_or_invalid_field:narrow")
        if len(blockers) == before and row.get("method") == "visual_inspection":
            coverage.add((str(row["state"]), bool(row["narrow"])))
    if not any(state == "default" for state, _ in coverage):
        blockers.append(f"{key}:missing_default_inspection")
    if value.get("expanded_content") is True and not any(state == "expanded" for state, _ in coverage):
        blockers.append(f"{key}:missing_expanded_inspection")
    if value.get("responsive_layout") is True:
        if ("default", True) not in coverage:
            blockers.append(f"{key}:missing_narrow_default_inspection")
        if value.get("expanded_content") is True and ("expanded", True) not in coverage:
            blockers.append(f"{key}:missing_narrow_expanded_inspection")
    return blockers


def visual_publication_errors(value: object, body: str) -> list[str]:
    if not isinstance(value, Mapping) or value.get("decision") == "not_applicable":
        return []
    publication = VISUAL_ASSESSMENT["publication"]
    section = normalized_review_prose(review_section_text(body, publication["section"]))
    errors = []
    for field in publication["fields"]:
        text = value.get(field)
        normalized = normalized_review_prose(text) if isinstance(text, str) else ""
        if not normalized or normalized not in section:
            errors.append(f"review_body:visual_not_published:{field}")
    return errors
