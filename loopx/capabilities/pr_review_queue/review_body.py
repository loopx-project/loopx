"""Shared publication/readback shape checks; length does not prove review quality."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

# Presentation aliases share the existing canonical section ids. They do not
# broaden evidence matching to arbitrary headings that contain a Chinese word.
REVIEW_SECTION_TITLES = {
    "动机": "动机 / Motivation",
    "改动思路": "改动思路 / Approach",
    "具体改动": "具体改动 / Changes",
    "对主干的风险": "对主干的风险 / Risks",
    "我的整体评价": "我的整体评价 / Verdict",
}
REQUIRED_FINAL_SECTIONS = list(REVIEW_SECTION_TITLES)
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
AUTHOR_OWNED_APPROVAL_FALLBACK_TITLE = (
    "Approval conclusion (author-owned PR; GitHub blocks formal self-approval)"
)
AUTHOR_OWNED_REQUEST_CHANGES_FALLBACK_TITLE = (
    "Request changes conclusion (author-owned PR; GitHub blocks formal self-review)"
)


def review_body_requirements(*, behavior_bearing: bool) -> dict[str, int]:
    # Count letters/numbers in explanatory prose, not Markdown, URLs or code.
    # Documentation-only fixes need less space than an executable contract.
    return dict(zip(REQUIRED_FINAL_SECTIONS,
                    (40, 80, 180, 120, 60) if behavior_bearing else (20, 30, 50, 30, 20)))


def english_review_verdict(body: str) -> str | None:
    verdicts = _english_verdicts(body)
    return verdicts[0] if len(verdicts) == 1 else None


def _english_verdicts(body: str) -> list[str]:
    verdicts: list[str] = []
    for line in _visible_lines(body):
        match = re.match(r"(?i)^english verdict\s*:\s*(APPROVE|REQUEST_CHANGES)\b",
                         line.strip().replace("**", ""))
        if match:
            verdicts.append(match.group(1).upper())
    return verdicts


def _visible_lines(body: str) -> Iterator[str]:
    fence: str | None = None
    comment_open = False
    for line in body.splitlines():
        if fence is not None:
            closing = FENCE_CLOSE.match(line)
            if closing and closing[1][0] == fence[0] and len(closing[1]) >= len(fence):
                fence = None
            continue
        visible_parts: list[str] = []
        offset = 0
        while offset < len(line):
            if comment_open:
                end = line.find("-->", offset)
                if end < 0:
                    break
                comment_open = False
                offset = end + 3
            else:
                start = line.find("<!--", offset)
                if start < 0:
                    visible_parts.append(line[offset:])
                    break
                visible_parts.append(line[offset:start])
                comment_open = True
                offset = start + 4
        visible = "".join(visible_parts)
        opening = FENCE_OPEN.match(visible)
        if opening and (opening[1][0] == "~" or "`" not in opening[2]):
            fence = opening[1]
            continue
        yield visible


def visible_review_text(body: str) -> str:
    return "\n".join(_visible_lines(body))


def author_owned_review_title_matches(body: str, verdict: str | None) -> bool:
    """Use the same visible, literal conclusion at publication and readback."""
    expected = {
        "APPROVE": AUTHOR_OWNED_APPROVAL_FALLBACK_TITLE,
        "REQUEST_CHANGES": AUTHOR_OWNED_REQUEST_CHANGES_FALLBACK_TITLE,
    }.get(verdict or "")
    return bool(expected and expected in visible_review_text(body))


def reviewer_declaration_lines(body: str) -> list[str]:
    # Same visibility rule as the English verdict: a comment or fenced block
    # cannot carry the declaration a reader is meant to see.
    lines: list[str] = []
    for line in _visible_lines(body):
        match = re.match(r"(?i)^reviewer\s*:\s*(.+)$", line.strip().replace("**", ""))
        if match:
            lines.append(match.group(1).strip())
    return lines


def normalized_review_prose(prose: str) -> str:
    """Compare visible wording while tolerating ordinary Markdown formatting."""
    prose = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", prose)
    prose = re.sub(r"[`*_]", "", prose)
    return "".join(prose.split())


def _prose_size(lines: list[str]) -> int:
    prose = "\n".join(dict.fromkeys(lines))
    prose = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", prose)
    prose = re.sub(r"https?://\S+|\b[0-9a-fA-F]{40,64}\b", "", prose)
    return sum(char.isalnum() for char in prose)


def _review_sections(body: str) -> tuple[dict[str, list[str]], list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    section_level = 0
    reasons: list[str] = []
    visible_lines = list(_visible_lines(body))
    for line in visible_lines:
        stripped = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if heading:
            level, label = len(heading[1]), heading[2]
            canonical = next((section for section, title in REVIEW_SECTION_TITLES.items()
                              if label in (section, title)), None)
            if canonical is not None:
                if canonical in sections:
                    reasons.append(f"duplicate_section:{canonical}")
                sections.setdefault(canonical, [])
                current, section_level = canonical, level
            elif level <= section_level:
                current = None
            continue
        if english_review_verdict(line):
            continue
        if current and stripped:
            sections[current].append(stripped)
    return sections, reasons


def review_section_text(body: str, label: str) -> str:
    sections, _ = _review_sections(body)
    return "\n".join(sections.get(label, []))


def check_review_body(body: str, *, head_oid: str, behavior_bearing: bool) -> dict[str, Any]:
    floors = review_body_requirements(behavior_bearing=behavior_bearing)
    sections, reasons = _review_sections(body)
    visible_lines = list(_visible_lines(body))
    sizes = {label: _prose_size(lines) for label, lines in sections.items()}
    for label, floor in floors.items():
        if label not in sections:
            reasons.append(f"missing_section:{label}")
        elif sizes[label] < floor:
            reasons.append(f"section_too_short:{label}:{sizes[label]}<{floor}")
    if not head_oid or head_oid.casefold() not in "\n".join(visible_lines).casefold():
        reasons.append("missing_exact_head")
    verdicts = _english_verdicts(body)
    verdict = verdicts[0] if len(verdicts) == 1 else None
    if len(verdicts) > 1:
        reasons.append("ambiguous_english_verdict")
    elif verdict is None:
        reasons.append("missing_english_verdict")
    return {"valid": not reasons, "invalid_reasons": reasons,
            "section_lengths": sizes, "minimum_prose_characters": floors,
            "verdict": verdict, "evidence_truth_verified": False}
