"""Shared publication/readback shape checks; length does not prove review quality."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ...reasoning_effort import REASONING_EFFORTS

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
REVIEWER_DECLARATION_SOURCES = ("runtime_reported", "self_reported")


@dataclass(frozen=True)
class PublishedModelDeclaration:
    model: str
    provider: str
    source: str | None
    reasoning_effort: str | None
    has_model_actor: bool


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


def _reviewer_field(text: str) -> str:
    text = text.strip()
    if text.startswith("`") and text.endswith("`"):
        left = text.lstrip("`")
        right = text.rstrip("`")
        if len(left) == len(right):
            return left.rstrip("`").strip()
    elif len(text) > 2 and text[0] in "*_" and text[0] == text[-1]:
        return text[1:-1].strip()
    return text


def _keyed_reviewer_fields(fields: list[str]) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for field in fields:
        name, sep, text = field.partition("=")
        key = _reviewer_field(name).casefold()
        if key == "declaration_source":
            key = "source"
        if not sep or key not in ("model", "provider", "source", "reasoning_effort") or key in values:
            return None
        values[key] = _reviewer_field(text)
    return values if all(values.get(key) for key in ("model", "provider", "source")) else None


def parse_model_reviewer_line(line: str) -> PublishedModelDeclaration | None:
    """Parse the fields after the visible Reviewer label, including historical forms."""
    # The historical compact form puts the source and optional effort in parentheses.
    pair, _, attribution = line.rpartition("(")
    basis = attribution[:-1].split(",") if pair and attribution.endswith(")") else []
    source_label = _reviewer_field(basis[0]).casefold() if basis else ""
    compact = source_label in REVIEWER_DECLARATION_SOURCES
    # Old self-reports could include this note without the typed source marker.
    legacy_self_report = len(basis) == 1 and source_label == "self-reported family"
    identity = pair.rstrip() if compact or legacy_self_report else line
    if ";" in identity:
        identity = identity.removesuffix(".")
    fields = [_reviewer_field(field) for field in re.split(r"[·|;]", identity)]
    has_model_actor = fields[0].casefold() == "model_agent"
    if has_model_actor:
        fields = fields[1:]
    elif not compact:
        return None

    source: str | None = None
    effort: str | None = None
    explicit_effort = False
    if compact:
        if len(fields) != 2 or len(basis) > 2:
            return None
        model, provider = fields
        source = basis[0]
        if len(basis) == 2:
            effort = basis[1]
            explicit_effort = True
    elif any("=" in field for field in fields[:2]):
        keyed = _keyed_reviewer_fields(fields)
        if keyed is None:
            return None
        model, provider, source = keyed["model"], keyed["provider"], keyed["source"]
        effort = keyed.get("reasoning_effort")
        explicit_effort = effort is not None
    else:
        if not 2 <= len(fields) <= 4:
            return None
        if len(fields) == 3 and fields[0].casefold() in REVIEWER_DECLARATION_SOURCES:
            fields = fields[1:] + fields[:1]
        model, provider, *basis = fields
        source = basis[0] if basis else None
        if len(basis) == 2:
            name, sep, text = basis[1].partition("=")
            explicit_effort = bool(sep) and _reviewer_field(name).casefold() == "reasoning_effort"
            effort = text if explicit_effort else basis[1]

    if any(not re.fullmatch(r"[^·|;=`]+", field) or field.casefold() in REVIEWER_DECLARATION_SOURCES
           for field in (model, provider)):
        return None
    if source is not None:
        source = _reviewer_field(source).casefold()
        if source not in REVIEWER_DECLARATION_SOURCES:
            return None
    if effort is not None:
        effort = _reviewer_field(effort).casefold()
        if explicit_effort:
            if not re.fullmatch(r"[\w -]+", effort) or effort in REVIEWER_DECLARATION_SOURCES:
                return None
        elif effort not in (*REASONING_EFFORTS, "effort_unavailable"):
            return None
    return PublishedModelDeclaration(model, provider, source, effort, has_model_actor)


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
