"""The ordered answer shape one steward answer must follow.

The manager objective and the managed skill are instructions, so nothing here
decides what a model writes. This module owns the *order* an answer must carry,
and projects that order into the instruction text the manager receives, so the
contract and the prose that carries it cannot drift apart.

Scope note: the classifier is structural. It reports which contract sections a
text contains and in which order. It cannot judge whether the content under
``结论`` is a conclusion, because the manager answer is free text; closing that
gap needs a structured response field and is tracked as its own step instead of
being approximated with phrase matching.
"""

from __future__ import annotations

from dataclasses import dataclass

MANAGER_ANSWER_CONTRACT_SCHEMA_VERSION = "manager_answer_contract_v0"


@dataclass(frozen=True)
class ManagerAnswerSection:
    """One ordered part of a steward answer, with the rule that keeps it honest."""

    section_id: str
    label: str
    requirement: str


# The order is the contract. `gaps` is last on purpose: coverage disclaimers are
# a bounded footer, never the answer body.
MANAGER_ANSWER_SECTIONS: tuple[ManagerAnswerSection, ...] = (
    ManagerAnswerSection(
        section_id="conclusion",
        label="结论",
        requirement="Answer the question first, in one or two sentences.",
    ),
    ManagerAnswerSection(
        section_id="milestones",
        label="里程碑/基线",
        requirement=(
            "Name the milestone, baseline or comparison the conclusion is measured against."
        ),
    ),
    ManagerAnswerSection(
        section_id="evidence",
        label="依据",
        requirement=(
            "Name the evidence the conclusion rests on, with its coverage and freshness."
        ),
    ),
    ManagerAnswerSection(
        section_id="gaps",
        label="缺口",
        requirement="Name the remaining gaps and the coverage limits, last and bounded.",
    ),
)

MANAGER_ANSWER_FOOTER_SECTION_ID = "gaps"
MANAGER_ANSWER_FOOTER_RULE = (
    "Coverage disclaimers, missing-evidence notes and unreadable-source caveats belong to the "
    "缺口 section as a bounded footer; they are never the answer body and never the opening."
)


def manager_answer_labels() -> tuple[str, ...]:
    """Return the ordered labels one steward answer must carry."""

    return tuple(section.label for section in MANAGER_ANSWER_SECTIONS)


def manager_answer_section_ids() -> tuple[str, ...]:
    """Return the ordered section ids, so callers never re-declare the order."""

    return tuple(section.section_id for section in MANAGER_ANSWER_SECTIONS)


def manager_answer_contract_instruction() -> str:
    """Render the ordering rule the manager objective and skill must both carry."""

    order = " -> ".join(manager_answer_labels())
    return (
        f"Lead every answer with this contract order, labelling the parts: {order}. "
        f"{MANAGER_ANSWER_FOOTER_RULE}"
    )


def classify_manager_answer_shape(text: str) -> dict[str, object]:
    """Report which contract sections a rendered answer carries, and in what order.

    A structural check only: it finds the contract's own labels, so a compliant
    answer is recognisable without guessing at wording. A section that a reader
    would consider present but unlabelled is reported as missing, which is the
    honest reading of an answer that does not follow the contract.
    """

    labels = manager_answer_labels()
    positions = [(label, text.find(label)) for label in labels]
    present = [label for label, index in positions if index >= 0]
    missing = [label for label, index in positions if index < 0]
    ordered = [label for label, _index in sorted(
        ((label, index) for label, index in positions if index >= 0),
        key=lambda entry: entry[1],
    )]
    if missing:
        state = "incomplete"
    elif ordered != list(labels):
        state = "out_of_order"
    else:
        state = "ordered"
    return {
        "schema_version": MANAGER_ANSWER_CONTRACT_SCHEMA_VERSION,
        "state": state,
        "required_order": list(labels),
        "observed_order": ordered,
        "present_sections": present,
        "missing_sections": missing,
        "footer_section": next(
            section.label
            for section in MANAGER_ANSWER_SECTIONS
            if section.section_id == MANAGER_ANSWER_FOOTER_SECTION_ID
        ),
    }


__all__ = [
    "MANAGER_ANSWER_CONTRACT_SCHEMA_VERSION",
    "MANAGER_ANSWER_FOOTER_RULE",
    "MANAGER_ANSWER_FOOTER_SECTION_ID",
    "MANAGER_ANSWER_SECTIONS",
    "ManagerAnswerSection",
    "classify_manager_answer_shape",
    "manager_answer_contract_instruction",
    "manager_answer_labels",
    "manager_answer_section_ids",
]
