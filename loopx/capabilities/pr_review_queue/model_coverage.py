"""Read model declarations for queue coverage without changing review authority."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .review_body import parse_model_reviewer_line, reviewer_declaration_lines


# A product alias is evidence only when paired with its declared provider.
_PROVIDERS = {
    "gpt": {"openai"},
    "gemini": {"google", "google deepmind"},
    "claude": {"anthropic"},
    "deepseek": {"deepseek"},
    "qwen": {"alibaba", "alibaba cloud", "qwen"},
}
_MODEL_NAME = re.compile(r"([a-z][a-z0-9]*)(?:[ .-][a-z0-9]+)*")


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
    declaration = parse_model_reviewer_line(lines[0])
    if declaration is None or declaration.source is None:
        return None
    family = _model_family(declaration.model, declaration.provider)
    return (family, declaration.source) if family else None


def needs_cross_model_review(
    *, family: str, history: dict[str, Any], independent_review_ids: Sequence[int],
    model_reports: Sequence[tuple[str, str] | None],
) -> bool:
    """Detect missing model coverage from one complete exact-head snapshot.

    Interpret provenance for queue work only. The existing history owner
    still determines whether an account supplies independent review evidence.
    """
    author = history["author"].casefold()
    head = history["head"].casefold()
    independent_ids = set(independent_review_ids)
    other_family_review = False
    for row, declared in zip(history["reviews"], model_reports, strict=True):
        if (row["head"].casefold() != head or row["state"] not in
                {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}):
            continue
        review_id = row["review_id"]
        if type(review_id) is not int or review_id <= 0 or declared is None:
            continue
        reviewed_family, source = declared
        if row["login"].casefold() == author:
            if row["state"] == "COMMENTED" and row["conclusion"]["valid"]:
                if reviewed_family != family:
                    other_family_review = True
                elif source == "runtime_reported":
                    # A shared author login cannot establish GitHub independence.
                    # A complete review already published by this model family
                    # still prevents repeat assignment to the same model.
                    return False
            continue
        if review_id in independent_ids:
            if reviewed_family == family:
                return False
            other_family_review = True
    return other_family_review
