from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from ...capabilities.periodic_report.adapters import (
    ARTIFACT_SCHEMA,
    DOCUMENT_SCHEMA,
    PeriodicReportRendererAdapter,
    validate_periodic_report_editorial,
)
from ..public_safety import redact_public_text
from .periodic_report_markdown import (
    periodic_report_labels,
    periodic_report_source_url,
    render_periodic_report_markdown,
)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _safe_text(value: object, *, maximum: int) -> str:
    return escape(str(redact_public_text(value, limit=maximum)))


def _safe_attr(value: object, *, maximum: int) -> str:
    return escape(str(redact_public_text(value, limit=maximum)), quote=True)


def _safe_http_url(value: object) -> str | None:
    url = periodic_report_source_url(value)
    return escape(url, quote=True) if url else None


def _document_digest(document: Mapping[str, Any]) -> str:
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _embedded_json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _visibility(item: Mapping[str, Any]) -> str:
    return str(item.get("visibility") or "primary")


def _html_labels(language: object) -> dict[str, str]:
    labels = periodic_report_labels(language)
    if str(language or "").lower().startswith("zh"):
        labels.update(
            {
                "default_kicker": "周期报告",
                "all_sections": "全部章节",
                "report_index": "报告目录",
                "filter_placeholder": "搜索报告内容",
                "filter_aria": "搜索报告条目",
                "copy_markdown": "复制完整文字版",
                "print_pdf": "打印 / 存为 PDF",
                "report": "报告",
                "item_count": "{count} 项",
                "no_items_section": "本章节暂无条目。",
                "source_link": "查看来源 ↗",
                "supporting_context": "附录：投递与运行信息",
                "operational_notes": "运行说明",
                "profile": "报告配置",
                "generated": "生成时间",
                "document": "报告摘要",
                "markdown": "文字版摘要",
                "source": "来源",
                "status": "状态",
                "items": "条目数",
                "snapshot": "快照",
                "no_source_receipts": "暂无来源回执。",
                "facts_aria": "报告概览",
                "sections": "章节",
                "sources": "来源",
                "copy_success": "完整文字版已复制",
                "copy_failed": "复制失败",
                "content_kind_delivery_receipt": "投递回执",
                "content_kind_runtime": "运行状态",
                "content_kind_supporting": "补充说明",
                "source_status_complete": "完整",
                "source_status_partial": "部分",
                "source_status_failed": "失败",
                "source_status_unknown": "未知",
            }
        )
        return labels
    labels.update(
        {
            "default_kicker": "Periodic report",
            "all_sections": "All sections",
            "report_index": "Report index",
            "filter_placeholder": "Filter report",
            "filter_aria": "Filter report items",
            "copy_markdown": "Copy full report",
            "print_pdf": "Print / PDF",
            "report": "Report",
            "item_count": "{count} items",
            "no_items_section": "No items in this section.",
            "source_link": "Source ↗",
            "supporting_context": "Appendix: delivery and runtime context",
            "operational_notes": "Operational notes",
            "profile": "Profile",
            "generated": "Generated",
            "document": "Document",
            "markdown": "Markdown",
            "source": "Source",
            "status": "Status",
            "items": "Items",
            "snapshot": "Snapshot",
            "no_source_receipts": "No source receipts.",
            "facts_aria": "Report facts",
            "sections": "Sections",
            "sources": "Sources",
            "copy_success": "Full report copied",
            "copy_failed": "Copy failed",
            "content_kind_delivery_receipt": "Delivery receipt",
            "content_kind_runtime": "Runtime",
            "content_kind_supporting": "Supporting note",
            "source_status_complete": "Complete",
            "source_status_partial": "Partial",
            "source_status_failed": "Failed",
            "source_status_unknown": "Unknown",
        }
    )
    return labels


def _primary_items(section: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in _items(section.get("items")) if _visibility(item) == "primary"
    ]


def _supporting_items(section: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in _items(section.get("items"))
        if _visibility(item) == "supporting"
    ]


def _render_tags(item: Mapping[str, Any]) -> str:
    value = item.get("tags")
    tag_labels = _mapping(item.get("tag_labels"))
    tags = (
        [
            _safe_text(tag_labels.get(tag) or tag, maximum=128)
            for tag in value
            if isinstance(tag, str) and tag.strip()
        ]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
        else []
    )
    if not tags:
        return ""
    return (
        '<span class="tags">'
        + "".join(f'<span class="tag">{tag}</span>' for tag in tags)
        + "</span>"
    )


def _render_details(value: object) -> str:
    details = _items(value)
    if not details:
        return ""
    rows = []
    for detail in details:
        rows.append(
            '<div class="item-detail">'
            f'<dt>{_safe_text(detail.get("label"), maximum=40)}</dt>'
            f'<dd>{_safe_text(detail.get("text"), maximum=500)}</dd>'
            "</div>"
        )
    return f'<dl class="item-details">{"".join(rows)}</dl>'


def _render_item(
    item: Mapping[str, Any],
    *,
    index: int,
    labels: Mapping[str, str],
) -> str:
    title = _safe_text(item.get("title"), maximum=240)
    summary = _safe_text(item.get("summary"), maximum=1000)
    status = _safe_text(item.get("status"), maximum=80)
    next_action = _safe_text(item.get("next_action"), maximum=500)
    source_ref = item.get("source_ref")
    source_url = _safe_http_url(source_ref)
    content_kind = _safe_attr(item.get("content_kind") or "unspecified", maximum=128)
    search_text = " ".join(
        str(item.get(field) or "")
        for field in (
            "title",
            "summary",
            "status",
            "source_id",
            "next_action",
            "tags",
            "tag_labels",
            "details",
        )
    )
    kicker = ""
    if status:
        kicker += f'<span class="status">{status}</span>'
    kicker += _render_tags(item)
    next_block = ""
    if next_action:
        next_block = (
            f'<div class="next-action"><span>{labels["next"]}</span>'
            f"<p>{next_action}</p></div>"
        )
    if source_url:
        source = (
            f'<a class="source-link" href="{source_url}" '
            'target="_blank" rel="noopener noreferrer">'
            f'{labels["source_link"]}</a>'
        )
    elif source_ref:
        source = (
            f'<span class="source-ref">{_safe_text(source_ref, maximum=500)}</span>'
        )
    else:
        source = ""
    footer = f'<div class="item-footer">{source}</div>' if source else ""
    return (
        f'<article class="report-row" data-content-kind="{content_kind}" '
        f'data-search="{_safe_attr(search_text, maximum=2200)}">'
        f'<div class="row-index">{index:02d}</div>'
        '<div class="row-main">'
        f'<div class="row-kicker">{kicker}</div>'
        f"<h3>{title}</h3>"
        f'<p class="summary">{summary}</p>'
        f"{_render_details(item.get('details'))}"
        f"{next_block}{footer}"
        "</div></article>"
    )


def _render_section(
    section: Mapping[str, Any],
    *,
    section_index: int,
    item_start: int,
    labels: Mapping[str, str],
) -> tuple[str, int]:
    section_id = _safe_attr(section.get("section_id"), maximum=128)
    title = _safe_text(section.get("title"), maximum=160)
    items = _primary_items(section)
    rows = "".join(
        _render_item(item, index=item_start + offset, labels=labels)
        for offset, item in enumerate(items)
    )
    if not rows:
        rows = f'<p class="empty">{labels["no_items_section"]}</p>'
    content = (
        f'<section class="report-section" id="section-{section_id}" '
        f'data-section="{section_id}">'
        '<div class="section-heading"><div>'
        f'<p class="eyebrow">{section_index:02d} / {labels["report"]}</p>'
        f"<h2>{title}</h2></div>"
        f'<span class="section-count">{labels["item_count"].format(count=len(items))}</span>'
        f'</div><div class="rows">{rows}</div></section>'
    )
    return content, item_start + len(items)


def _render_supporting_sections(
    sections: Sequence[Mapping[str, Any]],
    *,
    labels: Mapping[str, str],
) -> str:
    groups = []
    for section in sections:
        items = _supporting_items(section)
        if not items:
            continue
        notes = []
        for item in items:
            status = _safe_text(item.get("status"), maximum=80)
            raw_kind = str(item.get("content_kind") or "supporting")
            kind = _safe_text(
                labels.get(f"content_kind_{raw_kind}") or raw_kind,
                maximum=128,
            )
            label = " · ".join(value for value in (kind, status) if value)
            next_action = _safe_text(item.get("next_action"), maximum=500)
            source_url = _safe_http_url(item.get("source_ref"))
            source = (
                f'<a href="{source_url}" target="_blank" '
                'rel="noopener noreferrer">Source ↗</a>'
                if source_url
                else ""
            )
            notes.append(
                '<article class="supporting-note">'
                f'<p class="supporting-label">{label}</p>'
                f"<h4>{_safe_text(item.get('title'), maximum=240)}</h4>"
                f"<p>{_safe_text(item.get('summary'), maximum=1000)}</p>"
                + (
                    f'<p><strong>{labels["next"]}{labels["label_separator"]}</strong> {next_action}</p>'
                    if next_action
                    else ""
                )
                + source
                + "</article>"
            )
        groups.append(
            '<section class="supporting-section">'
            f"<h3>{_safe_text(section.get('title'), maximum=160)}</h3>"
            f"{''.join(notes)}</section>"
        )
    if not groups:
        return ""
    return (
        f'<div class="supporting-sections"><h3>{labels["operational_notes"]}</h3>'
        + "".join(groups)
        + "</div>"
    )


def _render_source_receipts(
    sources: Sequence[Mapping[str, Any]],
    *,
    labels: Mapping[str, str],
) -> str:
    rows = []
    for source in sources:
        rows.append(
            "<tr>"
            f'<th scope="row">{_safe_text(source.get("source_id"), maximum=128)}</th>'
            f"<td>{_safe_text(labels.get('source_status_' + str(source.get('status'))) or source.get('status'), maximum=80)}</td>"
            f"<td>{_safe_text(source.get('item_count'), maximum=40)}</td>"
            f"<td><code>{_safe_text(source.get('snapshot_digest'), maximum=128)}</code></td>"
            "</tr>"
        )
    if not rows:
        return f'<p class="empty">{labels["no_source_receipts"]}</p>'
    return (
        '<div class="table-wrap"><table><thead><tr>'
        f'<th>{labels["source"]}</th><th>{labels["status"]}</th>'
        f'<th>{labels["items"]}</th><th>{labels["snapshot"]}</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


_CSS = """
:root {
  color-scheme: light;
  --ivory: #faf9f5;
  --ink: #171714;
  --paper: #ffffff;
  --clay: #d97757;
  --oat: #e3dacc;
  --line: #d1cfc5;
  --muted: #77766f;
  --soft: #f0eee6;
  --serif: ui-serif, Georgia, Cambria, "Times New Roman", serif;
  --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif;
  --mono: ui-monospace, "SFMono-Regular", Consolas, monospace;
  background: var(--ivory);
  color: var(--ink);
  font-family: var(--sans);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; min-width: 300px; font-size: 15px; line-height: 1.65; }
button, input { font: inherit; }
.shell { width: min(1220px, calc(100% - 40px)); margin: 0 auto; padding: 40px 0 80px; }
.masthead { border-top: 8px solid var(--ink); border-bottom: 1.5px solid var(--ink);
  padding: 27px 0 26px; }
.mast-grid { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 28px;
  align-items: end; }
.eyebrow { margin: 0 0 8px; color: var(--muted); font: 650 10px/1.4 var(--mono);
  letter-spacing: .1em; text-transform: uppercase; }
h1, h2, h3 { font-family: var(--serif); font-weight: 500; letter-spacing: -.018em; }
h1 { max-width: 900px; margin: 0; font-size: clamp(39px, 6vw, 72px); line-height: .98; }
.period { margin: 18px 0 0; color: #454541; font-size: 15px; }
.mast-summary { max-width: 820px; margin: 18px 0 0; color: #3f3f3a; font-size: 16px;
  line-height: 1.72; }
.report-facts { display: grid; grid-template-columns: repeat(2, minmax(112px, 1fr)); gap: 1px;
  border: 1px solid var(--line); background: var(--line); }
.report-facts.fallback { grid-template-columns: repeat(3, minmax(78px, 1fr)); }
.fact { min-width: 112px; padding: 13px 14px; background: var(--paper); }
.fact[data-tone="attention"] { box-shadow: inset 3px 0 0 var(--clay); }
.fact strong { display: block; font: 500 28px/1 var(--serif); }
.fact span { display: block; margin-top: 5px; color: var(--muted); font-size: 10px;
  letter-spacing: .04em; text-transform: uppercase; }
.fact small { display: block; margin-top: 5px; color: var(--muted); font-size: 10px;
  line-height: 1.35; }
.layout { display: grid; grid-template-columns: 205px minmax(0, 1fr); gap: 48px;
  margin-top: 44px; }
.rail { position: sticky; top: 18px; align-self: start; }
.rail-label { margin: 0 0 10px; color: var(--muted); font: 650 10px/1.4 var(--mono);
  letter-spacing: .09em; text-transform: uppercase; }
.rail nav { display: grid; border-top: 1px solid var(--line); }
.section-filter { display: grid; grid-template-columns: 26px 1fr; gap: 8px; width: 100%;
  border: 0; border-bottom: 1px solid var(--line); padding: 10px 2px; background: transparent;
  color: #3e3e3a; cursor: pointer; text-align: left; font-size: 13px; text-decoration: none; }
.section-filter span { color: var(--muted); font: 10px/1.7 var(--mono); }
.section-filter[aria-current="true"] { color: var(--clay); }
.search { width: 100%; margin-top: 15px; border: 1px solid var(--line); border-radius: 8px;
  padding: 9px 10px; background: var(--paper); color: var(--ink); font-size: 12px; }
.tools { display: grid; grid-template-columns: 1fr; gap: 7px; margin-top: 9px; }
.tool { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px;
  background: var(--paper); color: var(--ink); cursor: pointer; text-align: left; font-size: 12px; }
.tool:hover, .search:focus { border-color: var(--ink); outline: none; }
.report-section { margin-bottom: 58px; scroll-margin-top: 20px; }
.section-heading { display: flex; justify-content: space-between; gap: 20px; align-items: end;
  padding-bottom: 13px; border-bottom: 2px solid var(--ink); }
h2 { margin: 0; font-size: 33px; line-height: 1.05; }
.section-count { color: var(--muted); font: 11px/1.4 var(--mono); white-space: nowrap; }
.rows { border-bottom: 1px solid var(--line); }
.report-row { display: grid; grid-template-columns: 50px minmax(0, 1fr); gap: 18px;
  padding: 24px 0; border-top: 1px solid var(--line); }
.report-row:first-child { border-top: 0; }
.row-index { color: var(--line); font: 500 28px/1 var(--serif); }
.row-kicker { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; min-height: 20px;
  margin-bottom: 6px; }
.status { border-radius: 999px; padding: 2px 8px; background: var(--oat); color: #454541;
  font: 10px/1.5 var(--mono); }
.tags { display: inline-flex; flex-wrap: wrap; gap: 5px; }
.tag { color: var(--muted); font: 9px/1.4 var(--mono); letter-spacing: .04em;
  text-transform: uppercase; }
.tag + .tag::before { content: "·"; margin-right: 5px; color: var(--line); }
h3 { margin: 0; font-size: 24px; line-height: 1.2; }
.summary { max-width: 820px; margin: 9px 0 0; color: #42423e; line-height: 1.72; }
.item-details { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px;
  max-width: 900px; margin: 15px 0 0; border: 1px solid var(--line); padding: 0;
  background: var(--line); }
.item-detail { min-width: 0; padding: 11px 13px; background: var(--paper); }
.item-detail dt { margin: 0 0 4px; color: var(--clay); font: 650 10px/1.4 var(--sans); }
.item-detail dd { margin: 0; color: #4a4a45; font-size: 12px; line-height: 1.65; }
.next-action { display: grid; grid-template-columns: 52px minmax(0, 1fr); gap: 11px;
  margin-top: 14px; border-left: 3px solid var(--clay); padding: 7px 0 7px 11px; }
.next-action span { color: var(--clay); font: 650 9px/1.6 var(--mono); letter-spacing: .06em;
  text-transform: uppercase; }
.next-action p { margin: 0; color: #4a4a45; font-size: 13px; }
.item-footer { margin-top: 11px; }
.source-link, .source-ref { color: var(--clay); font: 650 10px/1.4 var(--mono);
  letter-spacing: .04em; text-decoration: none; overflow-wrap: anywhere; }
.source-link:hover { text-decoration: underline; }
.source-ref { color: var(--muted); }
.supporting { margin: 10px 0 0; border-top: 1px solid var(--line); padding-top: 14px; }
.supporting > summary { cursor: pointer; color: var(--muted); font-size: 12px; }
.supporting[open] > summary { color: var(--ink); }
.supporting-body { margin-top: 13px; border: 1px solid var(--line); border-radius: 10px;
  padding: 14px; background: var(--paper); }
.metadata { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 5px 14px;
  margin: 0 0 13px; font-size: 11px; }
.metadata dt { color: var(--muted); }
.metadata dd { margin: 0; overflow-wrap: anywhere; }
.metadata code, td code { font: 10px/1.45 var(--mono); }
.supporting-sections { margin-bottom: 18px; }
.supporting-sections > h3, .supporting-section > h3 { margin: 0 0 10px; font: 600 13px/1.4 var(--sans); }
.supporting-section + .supporting-section { margin-top: 16px; }
.supporting-note { border-top: 1px solid var(--line); padding: 11px 0; }
.supporting-note h4 { margin: 3px 0 5px; font: 600 13px/1.35 var(--sans); }
.supporting-note p { margin: 0; color: #4a4a45; font-size: 11px; }
.supporting-note p + p { margin-top: 5px; }
.supporting-note a { color: var(--clay); font: 650 10px/1.4 var(--mono); text-decoration: none; }
.supporting-label { color: var(--muted) !important; font: 10px/1.4 var(--mono) !important;
  text-transform: uppercase; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 11px; text-align: left; }
th, td { border-top: 1px solid var(--line); padding: 7px 9px; vertical-align: top; }
thead th { border-top: 0; color: var(--muted); font-weight: 600; }
.empty { padding: 18px 0; color: var(--muted); }
.toast { position: fixed; right: 20px; bottom: 20px; transform: translateY(16px);
  opacity: 0; border-radius: 8px; padding: 9px 12px; background: var(--ink); color: #fff;
  font-size: 12px; transition: .18s ease; pointer-events: none; }
.toast.show { transform: none; opacity: 1; }
[hidden] { display: none !important; }
@media (max-width: 820px) {
  .shell { width: min(100% - 26px, 760px); padding-top: 20px; }
  .mast-grid { grid-template-columns: 1fr; }
  .report-facts { width: 100%; }
  .layout { grid-template-columns: 1fr; gap: 32px; }
  .rail { position: static; }
  .rail nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .tools { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 520px) {
  h1 { font-size: 41px; }
  .report-facts, .report-facts.fallback { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .fact { min-width: 0; }
  .rail nav, .tools { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .report-row { grid-template-columns: 34px minmax(0, 1fr); gap: 9px; }
  .item-details { grid-template-columns: 1fr; }
  h3 { font-size: 21px; }
}
@media print {
  .rail, .toast, .supporting { display: none !important; }
  .layout { grid-template-columns: 1fr; }
  .shell { width: 100%; padding: 0; }
  body { background: #fff; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  .toast { transition: none; }
}
"""


_SCRIPT = """
(() => {
  const search = document.querySelector('[data-report-search]');
  const filters = [...document.querySelectorAll('[data-section-filter]')];
  const sections = [...document.querySelectorAll('[data-section]')];
  let active = 'all';
  const apply = () => {
    const query = (search && search.value || '').trim().toLocaleLowerCase();
    sections.forEach((section) => {
      const sectionMatch = active === 'all' || section.dataset.section === active;
      let visible = 0;
      section.querySelectorAll('[data-search]').forEach((item) => {
        const itemMatch = !query || (item.dataset.search || '').toLocaleLowerCase().includes(query);
        item.hidden = !(sectionMatch && itemMatch);
        if (!item.hidden) visible += 1;
      });
      section.hidden = !sectionMatch || visible === 0;
    });
  };
  if (search) search.addEventListener('input', apply);
  const activate = (candidate) => {
    active = candidate ? candidate.dataset.sectionFilter || 'all' : 'all';
    filters.forEach((candidate) => candidate.setAttribute(
      'aria-current', String(candidate.dataset.sectionFilter === active)
    ));
    apply();
  };
  filters.forEach((link) => link.addEventListener('click', () => activate(link)));
  const restoreHash = () => {
    const sectionId = location.hash.startsWith('#section-')
      ? location.hash.slice('#section-'.length)
      : '';
    const link = filters.find((candidate) => candidate.dataset.sectionFilter === sectionId);
    if (!link) {
      if (!location.hash || location.hash === '#report-top') activate(filters[0]);
      return;
    }
    activate(link);
    const section = sections.find((candidate) => candidate.dataset.section === sectionId);
    if (section) requestAnimationFrame(() => section.scrollIntoView({block: 'start'}));
  };
  window.addEventListener('hashchange', restoreHash);
  restoreHash();
  const toast = document.querySelector('[data-copy-toast]');
  const copy = document.querySelector('[data-copy-markdown]');
  if (copy) copy.addEventListener('click', async () => {
    const payload = document.querySelector('[data-report-markdown]');
    const text = payload ? JSON.parse(payload.textContent || '\"\"') : '';
    if (toast) {
      toast.textContent = '';
    }
    try {
      await navigator.clipboard.writeText(text);
      if (toast) toast.textContent = toast.dataset.copySuccess || 'Copied';
    } catch (_error) {
      if (toast) toast.textContent = toast.dataset.copyFailed || 'Copy failed';
    }
    if (toast && toast.textContent) {
      toast.classList.add('show');
      window.setTimeout(() => {
        toast.classList.remove('show');
        toast.textContent = '';
      }, 1400);
    }
  });
  const print = document.querySelector('[data-print-report]');
  if (print) print.addEventListener('click', () => window.print());
})();
"""


def render_periodic_report_html(document: Mapping[str, Any]) -> dict[str, Any]:
    """Render one dense, self-contained report without changing its semantics."""

    if document.get("schema_version") != DOCUMENT_SCHEMA:
        raise ValueError(f"document must use {DOCUMENT_SCHEMA}")
    validate_periodic_report_editorial(document)
    sections = _items(document.get("sections"))
    primary_sections = [
        section
        for section in sections
        if _primary_items(section) or not _items(section.get("items"))
    ]
    sources = _items(document.get("source_snapshots"))
    window = _mapping(document.get("period_window"))
    profile = _mapping(document.get("profile"))
    editorial = _mapping(document.get("editorial"))
    editorial_orchestration = _mapping(editorial.get("orchestration"))
    labels = _html_labels(editorial.get("language"))
    title = _safe_text(document.get("title"), maximum=200)
    item_count = sum(len(_primary_items(section)) for section in sections)
    supporting_item_count = sum(len(_supporting_items(section)) for section in sections)
    document_digest = _document_digest(document)
    markdown_artifact = render_periodic_report_markdown(document)

    filters = [
        '<a class="section-filter" href="#report-top" data-section-filter="all" '
        f'aria-current="true"><span>00</span>{labels["all_sections"]}</a>'
    ]
    rendered_sections = []
    next_item_index = 1
    for section_index, section in enumerate(primary_sections, start=1):
        section_id = _safe_attr(section.get("section_id"), maximum=128)
        section_title = _safe_text(section.get("title"), maximum=160)
        filters.append(
            f'<a class="section-filter" href="#section-{section_id}" '
            f'data-section-filter="{section_id}" aria-current="false">'
            f"<span>{section_index:02d}</span>{section_title}</a>"
        )
        section_html, next_item_index = _render_section(
            section,
            section_index=section_index,
            item_start=next_item_index,
            labels=labels,
        )
        rendered_sections.append(section_html)

    generated_at = _safe_text(document.get("generated_at"), maximum=80)
    profile_id = _safe_text(profile.get("profile_id"), maximum=128)
    profile_version = _safe_text(profile.get("profile_version"), maximum=128)
    language = _safe_attr(editorial.get("language") or "und", maximum=64)
    kicker = _safe_text(
        editorial.get("kicker") or labels["default_kicker"], maximum=120
    )
    summary = _safe_text(editorial.get("summary"), maximum=600)
    period_label = _safe_text(editorial.get("period_label"), maximum=160)
    highlights = _items(editorial.get("highlights"))
    if highlights:
        facts = []
        for highlight in highlights[:4]:
            detail = _safe_text(highlight.get("detail"), maximum=160)
            tone = _safe_attr(highlight.get("tone") or "neutral", maximum=128)
            facts.append(
                f'<div class="fact" data-tone="{tone}">'
                f"<strong>{_safe_text(highlight.get('value'), maximum=48)}</strong>"
                f"<span>{_safe_text(highlight.get('label'), maximum=80)}</span>"
                + (f"<small>{detail}</small>" if detail else "")
                + "</div>"
            )
        facts_html = f'<div class="report-facts">{"".join(facts)}</div>'
    else:
        facts_html = (
            '<div class="report-facts fallback" '
            f'aria-label="{_safe_attr(labels["facts_aria"], maximum=80)}">'
            f'<div class="fact"><strong>{len(primary_sections)}</strong><span>{labels["sections"]}</span></div>'
            f'<div class="fact"><strong>{item_count}</strong><span>{labels["items"]}</span></div>'
            f'<div class="fact"><strong>{len(sources)}</strong><span>{labels["sources"]}</span></div>'
            "</div>"
        )
    supporting_sections = _render_supporting_sections(sections, labels=labels)
    content = (
        "<!doctype html>\n"
        f'<html lang="{language}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; img-src data:\">"
        f"<title>{title}</title><style>{_CSS}</style></head><body>"
        '<div class="shell" id="report-top" data-renderer="html_artifact_v0" '
        'data-presentation="editorial_dense_v2">'
        '<header class="masthead"><div class="mast-grid"><div>'
        f'<p class="eyebrow">{kicker}</p>'
        f"<h1>{title}</h1>"
        f'<p class="period">{period_label or (_safe_text(window.get("start_at"), maximum=80) + " — " + _safe_text(window.get("end_at"), maximum=80))}</p>'
        + (f'<p class="mast-summary">{summary}</p>' if summary else "")
        + f'</div>{facts_html}</div></header><div class="layout">'
        f'<aside class="rail"><p class="rail-label">{labels["report_index"]}</p><nav>'
        f"{''.join(filters)}</nav>"
        '<input class="search" type="search" data-report-search '
        f'placeholder="{_safe_attr(labels["filter_placeholder"], maximum=80)}" '
        f'aria-label="{_safe_attr(labels["filter_aria"], maximum=80)}">'
        '<div class="tools"><button class="tool" type="button" data-copy-markdown>'
        f'{labels["copy_markdown"]}</button><button class="tool" type="button" '
        f'data-print-report>{labels["print_pdf"]}</button></div></aside><main>'
        f"{''.join(rendered_sections)}"
        '<details class="supporting" data-supporting-context><summary>'
        f'{labels["supporting_context"]}</summary><div class="supporting-body">'
        f"{supporting_sections}"
        '<dl class="metadata">'
        f'<dt>{labels["profile"]}</dt><dd>{profile_id} · {profile_version}</dd>'
        f'<dt>{labels["generated"]}</dt><dd>{generated_at}</dd>'
        f'<dt>{labels["document"]}</dt><dd><code>sha256:{document_digest}</code></dd>'
        f'<dt>{labels["markdown"]}</dt><dd><code>{_safe_text(markdown_artifact["content_digest"], maximum=80)}</code></dd>'
        "</dl>"
        f"{_render_source_receipts(sources, labels=labels)}"
        "</div></details></main></div></div>"
        '<div class="toast" data-copy-toast role="status" aria-live="polite" '
        f'data-copy-success="{_safe_attr(labels["copy_success"], maximum=80)}" '
        f'data-copy-failed="{_safe_attr(labels["copy_failed"], maximum=80)}"></div>'
        '<script type="application/json" data-report-markdown>'
        f"{_embedded_json(markdown_artifact['content'])}</script>"
        f"<script>{_SCRIPT}</script></body></html>\n"
    )
    content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "schema_version": ARTIFACT_SCHEMA,
        "artifact_id": "periodic_report_html",
        "renderer_id": "html_artifact_v0",
        "renderer_kind": "html",
        "media_type": "text/html; charset=utf-8",
        "content": content,
        "content_digest": f"sha256:{content_digest}",
        "artifact_ref": f"artifact:periodic-report/{content_digest[:24]}",
        "document_digest": f"sha256:{document_digest}",
        "companion_markdown_digest": markdown_artifact["content_digest"],
        "single_file": True,
        "zero_build": True,
        "external_dependencies": [],
        "interactive_controls": [
            "section_filter",
            "text_filter",
            "copy_markdown",
            "print_report",
        ],
        "presentation_profile": "editorial_dense_v2",
        "content_policy": {
            "primary_body_fields": [
                "title",
                "status",
                "tags",
                "tag_labels",
                "summary",
                "details",
                "next_action",
                "source_ref",
            ],
            "primary_visibility": "primary",
            "supporting_visibility": "supporting",
            "supporting_context": "collapsed",
            "process_narration_default_visible": False,
            "editorial_summary_source": editorial_orchestration.get(
                "summary_source", "none"
            ),
            "readability_policy": editorial_orchestration.get(
                "readability_policy", "none"
            ),
            "primary_item_count": item_count,
            "supporting_item_count": supporting_item_count,
        },
        "boundary": {
            "schedule_policy_applied": False,
            "business_evidence_judged": False,
            "external_writes_performed": False,
        },
    }


def periodic_report_html_renderer_adapter() -> PeriodicReportRendererAdapter:
    return PeriodicReportRendererAdapter(
        renderer_id="html_artifact_v0",
        renderer_kind="html",
        render=render_periodic_report_html,
    )
