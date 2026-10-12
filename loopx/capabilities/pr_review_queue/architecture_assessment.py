"""Declared architecture judgment inside the existing proportionality evidence.

This checks a review's consistency and publication, never architectural truth.
It owns no Goal state, provider admission, configuration or effect authority.
"""

from collections.abc import Mapping
from typing import Any

from .review_body import normalized_review_prose, review_section_text


ARCHITECTURE_ASSESSMENT: dict[str, Any] = {
    "decision_values": ["retain", "simplify_now", "follow_up", "not_yet_proven", "not_applicable"],
    "blocking_decisions": ["simplify_now", "not_yet_proven"],
    "fields": ["decision", "reason"],
    "applicable_fields": ["mechanisms", "current_pr_boundary", "validation_evidence"],
    "responsibility_values": ["invariant", "policy", "provider_io", "projection", "local_helper"],
    "mechanism_fields": [
        "mechanism", "responsibility", "owning_boundary", "activation_and_default",
        "failure_and_recovery", "placement_basis",
    ],
    "publication": {
        "sections": ["改动思路", "我的整体评价"],
        "fields": ["reason", "current_pr_boundary"],
    },
    "rule": (
        "Decompose the behavior-bearing mechanisms, not every file, before judging the bundle. "
        "For each, distinguish a correctness invariant, optional policy, provider IO, projection "
        "or local helper. Name the current and recommended owning boundary, activation/default "
        "scope and failure/recovery consequence. Ground placement in the target repository's "
        "accepted contract, current maintainer direction or demonstrated material risk. "
        "Do not impose LoopX architecture on another repository. Feature-off parity answers "
        "compatibility, not whether a core guarantee should be opt-in or an expensive strategy "
        "should be default. Judge those choices separately, without inventing a default change. "
        "Separate required decision/effect phases from optional observers; an optional provider "
        "cannot mint obligations or inherit unrelated hook authority. Reuse repository_reuse, "
        "authority_semantics, guidance_vs_obligation and validation_matrix as evidence. "
        "Publish the decisive reason and current_pr_boundary in 改动思路 or 我的整体评价, "
        "including the smallest repair and safe deferred owner/acceptance when staging. "
        "A stage must deliver a usable invariant through a real caller, not only an empty "
        "schema, registry or serializer; combine tightly coupled stages. Do not require all "
        "parent-roadmap work for a justified prerequisite. Unaccepted preferences or future "
        "extensions remain follow_up, not current blockers. retain requires evidenced fit; "
        "simplify_now or not_yet_proven blocks APPROVE even if the parent verdict says "
        "proportionate, CI is green and the last bug was fixed. A local change can use "
        "not_applicable with its inspected boundary. The checker cannot generate insight, "
        "prove these claims, or reward a higher rejection rate."
    ),
}


def check_architecture_assessment(value: object) -> list[str]:
    """Reject inconsistent/missing declarations, leaving evidence truth to review."""
    key = "change_proportionality:architecture_assessment"
    blockers: list[str] = []
    contract = ARCHITECTURE_ASSESSMENT
    if not isinstance(value, Mapping):
        return [f"{key}:missing_assessment"]

    def require(row: Mapping[str, Any], fields: list[str], prefix: str) -> None:
        for field in fields:
            text = row.get(field)
            if not isinstance(text, str) or not text.strip():
                blockers.append(f"{prefix}:missing_or_invalid_field:{field}")

    require(value, contract["fields"], key)
    decision = value.get("decision")
    if not isinstance(decision, str) or decision not in contract["decision_values"]:
        blockers.append(f"{key}:invalid_decision")
        return blockers
    if decision in contract["blocking_decisions"]:
        blockers.append(f"{key}:blocking_decision")
    if decision == "not_applicable":
        return blockers
    require(value, [field for field in contract["applicable_fields"] if field != "mechanisms"], key)
    mechanisms = value.get("mechanisms")
    if not isinstance(mechanisms, list) or not mechanisms:
        blockers.append(f"{key}:missing_mechanisms")
        return blockers
    for index, mechanism in enumerate(mechanisms):
        prefix = f"{key}:mechanisms[{index}]"
        if not isinstance(mechanism, Mapping):
            blockers.append(f"{prefix}:not_object")
            continue
        require(mechanism, contract["mechanism_fields"], prefix)
        responsibility = mechanism.get("responsibility")
        if not isinstance(responsibility, str) or responsibility not in contract["responsibility_values"]:
            blockers.append(f"{prefix}:invalid_responsibility")
    return blockers


def architecture_publication_errors(value: object, body: str) -> list[str]:
    """The private result cannot be the only place readers see the judgment."""
    if not isinstance(value, Mapping) or value.get("decision") == "not_applicable":
        return []
    publication = ARCHITECTURE_ASSESSMENT["publication"]
    sections = [normalized_review_prose(review_section_text(body, section))
                for section in publication["sections"]]
    errors = []
    for field in publication["fields"]:
        text = value.get(field)
        normalized = normalized_review_prose(text) if isinstance(text, str) else ""
        if not normalized:
            errors.append(f"review_body:architecture_not_text:{field}")
        elif not any(normalized in section for section in sections):
            errors.append(f"review_body:architecture_not_published:{field}")
    return errors
