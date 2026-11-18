"""Read model declarations for queue coverage without changing review authority."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ...reasoning_effort import REASONING_EFFORTS
from .approval_closeout import select_review_history
from .review_body import reviewer_declaration_lines


# A product alias is evidence only when paired with its declared provider.
_PROVIDERS = {
    "gpt": {"openai"},
    "gemini": {"google", "google deepmind"},
    "claude": {"anthropic"},
    "deepseek": {"deepseek"},
    "qwen": {"alibaba", "alibaba cloud", "qwen"},
}
_MODEL_NAME = re.compile(r"([a-z][a-z0-9]*)(?:[ .-][a-z0-9]+)*")
_COMPACT_DECLARATION = re.compile(
    r"(?:model_agent\s*[·|]\s*)?([^·|]+?)\s*[·|]\s*([^()]+?)"
    r"\s*\((runtime_reported|self_reported)(?:,\s*[\w -]+)?\)",
    re.IGNORECASE,
)
# Bare effort labels follow the runtime vocabulary and cannot be provenance tags.
_DOTTED_EFFORT_LABELS = "|".join(
    re.escape(label) for label in (*REASONING_EFFORTS, "effort_unavailable")
)
_DOTTED_DECLARATION = re.compile(
    r"model_agent\s*·\s*([^·]+?)\s*·\s*([^·]+?)\s*·\s*(runtime_reported|self_reported)"
    rf"(?:\s*·\s*(?:reasoning_effort=[\w -]+|{_DOTTED_EFFORT_LABELS}))?",
    re.IGNORECASE,
)
_KEYED_DECLARATION = re.compile(
    r"model_agent;\s*model=([^;]+);\s*provider=([^;]+);\s*"
    r"declaration_source=(runtime_reported|self_reported)"
    r"(?:;\s*reasoning_effort=[\w -]+)?\.?",
    re.IGNORECASE,
)
_KEYED_PIPE_DECLARATION = re.compile(
    r"model_agent\s*\|\s*model=([^|=]+)\s*\|\s*provider=([^|=]+)"
    r"(?:\s*\|\s*reasoning_effort=[\w -]+)?"
    r"\s*\|\s*source=(runtime_reported|self_reported)",
    re.IGNORECASE,
)
_LEGACY_DECLARATION = re.compile(
    r"model_agent\s*\|\s*(runtime_reported|self_reported)\s*\|\s*([^|]+)\s*\|\s*([^|]+)",
    re.IGNORECASE,
)


def _model_family(model: object, provider: object) -> str | None:
    if not isinstance(model, str) or not isinstance(provider, str):
        return None
    name = _MODEL_NAME.fullmatch(model.strip().casefold())
    if name and provider.strip().casefold() in _PROVIDERS.get(name[1], ()):
        return name[1]
    return None


def current_review_model_family(execution: Mapping[str, Any] | None) -> str | None:
    if not execution or execution.get("status") != "runtime_reported":
        return None
    return _model_family(execution.get("model"), execution.get("provider"))


def published_review_model(body: object) -> tuple[str, str] | None:
    if not isinstance(body, str):
        return None
    lines = reviewer_declaration_lines(body)
    if len(lines) != 1:
        return None
    match = (
        _COMPACT_DECLARATION.fullmatch(lines[0])
        or _DOTTED_DECLARATION.fullmatch(lines[0])
        or _KEYED_DECLARATION.fullmatch(lines[0])
        or _KEYED_PIPE_DECLARATION.fullmatch(lines[0])
    )
    if match:
        model, provider, source = match.groups()
    else:
        legacy = _LEGACY_DECLARATION.fullmatch(lines[0])
        if not legacy:
            return None
        source, model, provider = legacy.groups()
    family = _model_family(model, provider)
    return (family, source.casefold()) if family else None


def needs_cross_model_review(
    *, family: str, history: dict[str, Any], selection: Mapping[str, Any],
    model_reports: Sequence[tuple[str, str] | None],
) -> bool:
    """Detect missing model coverage from one complete exact-head snapshot.

    Interpret provenance for queue work only. The existing history owner
    still determines whether an account supplies independent review evidence.
    """
    author = history["author"].casefold()
    head = history["head"].casefold()
    by_login: dict[str, list[dict[str, Any]]] = {}
    matching_ids: set[int] = set()
    other_ids: set[int] = set()
    other_author_review = False
    for row, declared in zip(history["reviews"], model_reports, strict=True):
        login = row["login"].casefold()
        by_login.setdefault(login, []).append(row)
        if (row["head"].casefold() != head or row["state"] not in
                {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}):
            continue
        review_id = row["review_id"]
        if type(review_id) is not int or review_id <= 0 or declared is None:
            continue
        reviewed_family, source = declared
        if login == author:
            if row["state"] == "COMMENTED" and row["conclusion"]["valid"]:
                if reviewed_family != family:
                    other_author_review = True
                elif source == "runtime_reported":
                    # A shared author login cannot establish GitHub independence.
                    # A complete review already published by this model family
                    # still prevents repeat assignment to the same model.
                    return False
            continue
        (matching_ids if reviewed_family == family else other_ids).add(review_id)

    selected = selection.get("independent_review", {}).get("review_id")
    if selected in matching_ids:
        return False
    other_independent_review = selected in other_ids
    if not (other_author_review or other_independent_review or other_ids):
        return False

    # Preserve each candidate account's full history, including later comments
    # and dismissals. Unrelated accounts cannot overwrite the matching evidence.
    candidate_ids = matching_ids | other_ids
    for login, reviews in by_login.items():
        if login == author or not login or not any(
            row["review_id"] in candidate_ids for row in reviews
        ):
            continue
        evidence = select_review_history({**history, "reviews": reviews})
        review_id = evidence.get("independent_review", {}).get("review_id")
        if review_id in matching_ids:
            return False
        other_independent_review |= review_id in other_ids
    return other_author_review or other_independent_review
