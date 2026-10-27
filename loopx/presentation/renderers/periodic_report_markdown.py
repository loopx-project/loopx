from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlsplit

from ...capabilities.periodic_report.adapters import (
    ARTIFACT_SCHEMA,
    DOCUMENT_SCHEMA,
    PeriodicReportRendererAdapter,
    validate_periodic_report_editorial,
)
from ..public_safety import redact_public_text


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _safe(value: object, *, maximum: int) -> str:
    return str(redact_public_text(value, limit=maximum))


def _visibility(item: Mapping[str, Any]) -> str:
    return str(item.get("visibility") or "primary")


def periodic_report_source_url(value: object, *, _legacy_source_links: bool = False) -> str | None:
    """A display link is an unchanged HTTP URL, not evidence of access or trust."""
    if _legacy_source_links:
        # Read-only reconstruction of an already frozen pre-fix artifact.
        raw = _safe(value, maximum=500)
        parsed = urlsplit(raw)
        return raw if parsed.scheme in {"http", "https"} and parsed.netloc else None
    if not isinstance(value, str) or not value or value != _safe(value, maximum=500):
        return None
    if any(char.isspace() or unicodedata.category(char).startswith("C")
           or char in "<>\\`" for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return None
        parsed.port  # Reject malformed ports rather than linking a different address.
    except ValueError:
        return None
    return value


def _markdown_source(value: object, labels: Mapping[str, str], *, _legacy_source_links: bool = False) -> str:
    source = _safe(value, maximum=500)
    if _legacy_source_links:
        return f"[{labels['source']}]({source})"
    url = periodic_report_source_url(value)
    if url:
        # Parentheses and quotes must not terminate a Markdown destination.
        destination = quote(url, safe=":/?#[]@!$&'*+,;=%-._~")
        return f"[{labels['source']}]({destination})"
    # Retain opaque/private/malformed references as data, never fake links.
    fence = "`" * (1 + max((len(run) for run in re.findall(r"`+", source)), default=0))
    padding = " " if source.startswith("`") or source.endswith("`") else ""
    return f"{labels['source']}{labels['label_separator']}{fence}{padding}{source}{padding}{fence}"


def _source_references(document: Mapping[str, Any]) -> dict[str, int]:
    """Number opaque references once, in document order, for both formats."""
    references: dict[str, int] = {}
    for section in _items(document.get("sections")):
        for item in _items(section.get("items")):
            reference = item.get("source_ref")
            if isinstance(reference, str) and reference and not periodic_report_source_url(reference):
                references.setdefault(reference, len(references) + 1)
    return references


def periodic_report_labels(language: object) -> dict[str, str]:
    """Return the small built-in report UI vocabulary for one language."""

    if str(language or "").lower().startswith("zh"):
        return {
            "period": "报告周期",
            "no_items": "暂无条目。",
            "next": "下一步",
            "source": "来源",
            "source_references": "来源标识",
            "supporting_appendix": "附录：投递与运行信息",
            "label_separator": "：",
        }
    return {
        "period": "Period",
        "no_items": "No items.",
        "next": "Next",
        "source": "source",
        "source_references": "Source references",
        "supporting_appendix": "Appendix: delivery and runtime context",
        "label_separator": ": ",
    }


def _render_markdown_item(
    lines: list[str],
    item: Mapping[str, Any],
    *,
    labels: Mapping[str, str],
    references: Mapping[str, int],
    _legacy_source_links: bool = False,
) -> None:
    item_title = _safe(item.get("title"), maximum=240)
    source_ref = item.get("source_ref")
    status = _safe(item.get("status"), maximum=80)
    headline = f"- **{item_title}**"
    if status:
        headline += f" · `{status}`"
    if source_ref:
        if isinstance(source_ref, str) and source_ref in references:
            headline += f" · {labels['source']} {references[source_ref]}"
        else:
            headline += f" · {_markdown_source(source_ref, labels, _legacy_source_links=_legacy_source_links)}"
    lines.append(headline)
    lines.append(f"  - {_safe(item.get('summary'), maximum=1000)}")
    for detail in _items(item.get("details")):
        detail_label = _safe(detail.get("label"), maximum=40)
        detail_text = _safe(detail.get("text"), maximum=500)
        lines.append(
            f"  - **{detail_label}{labels['label_separator']}** {detail_text}"
        )
    next_action = _safe(item.get("next_action"), maximum=500)
    if next_action:
        lines.append(
            f"  - **{labels['next']}{labels['label_separator']}** {next_action}"
        )


def render_periodic_report_markdown(
    document: Mapping[str, Any],
    *,
    _legacy_source_links: bool = False,
    _legacy_layout: bool = False,
) -> dict[str, Any]:
    """Render normalized sections without changing ordering or business meaning."""

    if document.get("schema_version") != DOCUMENT_SCHEMA:
        raise ValueError(f"document must use {DOCUMENT_SCHEMA}")
    validate_periodic_report_editorial(document)
    # Read-only reconstruction of artifacts frozen before these presentation fixes.
    _legacy_layout = _legacy_layout or _legacy_source_links
    references = {} if _legacy_layout else _source_references(document)
    title = _safe(document.get("title"), maximum=200)
    window = _mapping(document.get("period_window"))
    editorial = _mapping(document.get("editorial"))
    editorial_orchestration = _mapping(editorial.get("orchestration"))
    labels = periodic_report_labels(editorial.get("language"))
    period_label = _safe(editorial.get("period_label"), maximum=160)
    lines = [
        f"# {title}",
        "",
        (
            period_label
            or f"{labels['period']}：`{window.get('start_at')}` – `{window.get('end_at')}`"
        ),
    ]
    summary = _safe(editorial.get("summary"), maximum=600)
    highlights = [] if _legacy_layout else _items(editorial.get("highlights"))
    if highlights:
        lines.append("")
        for highlight in highlights:
            label = _safe(highlight.get("label"), maximum=80)
            value = _safe(highlight.get("value"), maximum=48)
            detail = _safe(highlight.get("detail"), maximum=160)
            lines.append(f"- **{label}{labels['label_separator']}{value}**" + (f" · {detail}" if detail else ""))
    elif summary:
        lines.extend(["", summary])
    primary_item_count = 0
    supporting_item_count = 0
    supporting_sections: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for section in _items(document.get("sections")):
        all_items = _items(section.get("items"))
        section_items = [item for item in all_items if _visibility(item) == "primary"]
        section_supporting = [
            item for item in all_items if _visibility(item) == "supporting"
        ]
        supporting_item_count += len(section_supporting)
        if section_supporting:
            supporting_sections.append((section, section_supporting))
        primary_item_count += len(section_items)
        if all_items and not section_items:
            continue
        lines.extend(["", f"## {_safe(section.get('title'), maximum=160)}", ""])
        if not section_items:
            lines.append(f"- {labels['no_items']}")
            continue
        for item in section_items:
            _render_markdown_item(lines, item, labels=labels, references=references, _legacy_source_links=_legacy_source_links)
    if supporting_sections:
        lines.extend(["", f"## {labels['supporting_appendix']}"])
        for section, section_items in supporting_sections:
            lines.extend(["", f"### {_safe(section.get('title'), maximum=160)}", ""])
            for item in section_items:
                _render_markdown_item(lines, item, labels=labels, references=references, _legacy_source_links=_legacy_source_links)
    if references:
        lines.extend(["", f"## {labels['source_references']}", ""])
        for reference, number in references.items():
            lines.append(f"{number}. {_markdown_source(reference, labels)}")
    content = "\n".join(lines).rstrip() + "\n"
    content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document_digest = hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": ARTIFACT_SCHEMA,
        "artifact_id": "periodic_report_markdown",
        "renderer_id": "markdown_v0",
        "renderer_kind": "markdown",
        "content": content,
        "content_digest": f"sha256:{content_digest}",
        "artifact_ref": f"artifact:periodic-report/{content_digest[:24]}",
        "document_digest": f"sha256:{document_digest}",
        "content_policy": {
            "primary_visibility": "primary",
            "supporting_visibility": "supporting",
            "supporting_context": "appendix",
            "process_narration_default_visible": False,
            "editorial_summary_source": editorial_orchestration.get(
                "summary_source", "none"
            ),
            "readability_policy": editorial_orchestration.get(
                "readability_policy", "none"
            ),
            "primary_item_count": primary_item_count,
            "supporting_item_count": supporting_item_count,
        },
        "boundary": {
            "schedule_policy_applied": False,
            "business_evidence_judged": False,
            "external_writes_performed": False,
        },
    }


def periodic_report_markdown_renderer_adapter() -> PeriodicReportRendererAdapter:
    return PeriodicReportRendererAdapter(
        renderer_id="markdown_v0",
        renderer_kind="markdown",
        render=render_periodic_report_markdown,
    )
