from __future__ import annotations

from html.parser import HTMLParser

import pytest

from loopx.capabilities.periodic_report import (
    PeriodicReportAdapterRegistry,
    build_periodic_report_document,
    build_periodic_report_source_result,
)
from loopx.presentation.renderers.periodic_report_html import (
    periodic_report_html_renderer_adapter,
)
from loopx.presentation.renderers.periodic_report_markdown import (
    periodic_report_markdown_renderer_adapter,
)


def _document(*, language: str = "en") -> dict[str, object]:
    source = build_periodic_report_source_result(
        source_id="release_notes",
        source_kind="release_activity",
        status="complete",
        observed_at="2026-07-20T00:40:00Z",
        sections=[
            {
                "section_id": "completed",
                "title": "Completed <week>",
                "order": 10,
                "items": [
                    {
                        "item_id": "release_2.4",
                        "title": "Release <script>alert(1)</script>",
                        "summary": "Published & verified.",
                        "value_rank": 50,
                        "status": "published",
                        "source_ref": "javascript:alert(1)",
                        "next_action": "Observe adoption.",
                        "content_kind": "outcome",
                        "tags": ["delivery"],
                        "tag_labels": {"delivery": "Shipped safely"},
                        "details": [
                            {
                                "label": "Evidence",
                                "text": "One staged rollout completed.",
                            }
                        ],
                    },
                    {
                        "item_id": "release_2.5",
                        "title": "Release candidate",
                        "summary": "Ready for review.",
                        "value_rank": 60,
                        "content_kind": "progress",
                        "source_ref": "https://example.test/releases/2.5",
                    },
                ],
            },
            {
                "section_id": "report_operations",
                "title": "Report operations",
                "order": 90,
                "items": [
                    {
                        "item_id": "delivery_parity",
                        "title": "Artifacts share one normalized document",
                        "summary": "Delivery digests were verified.",
                        "value_rank": 10,
                        "content_kind": "delivery_receipt",
                        "visibility": "supporting",
                        "status": "verified",
                    }
                ],
            },
        ],
    )
    return build_periodic_report_document(
        title="Engineering report",
        generated_at="2026-07-20T01:00:00Z",
        period_window={
            "start_at": "2026-07-13T00:00:00Z",
            "end_at": "2026-07-20T00:00:00Z",
        },
        profile={"profile_id": "maintenance", "profile_version": "v1"},
        sources=[source],
        editorial={
            "language": language,
            "kicker": "Engineering · Weekly report",
            "period_label": "July 13–20, 2026",
            "highlights": [
                {
                    "highlight_id": "shipped",
                    "value": "1",
                    "label": "Release shipped",
                    "tone": "positive",
                },
                {
                    "highlight_id": "review",
                    "value": "1",
                    "label": "Needs review",
                    "tone": "attention",
                },
            ],
        },
    )


def test_html_artifact_is_self_contained_interactive_and_registry_valid() -> None:
    registry = PeriodicReportAdapterRegistry()
    registry.register_renderer(periodic_report_html_renderer_adapter())

    artifact = registry.render("html_artifact_v0", _document())
    content = artifact["content"]

    assert content.startswith("<!doctype html>")
    assert 'data-renderer="html_artifact_v0"' in content
    assert "data-report-search" in content
    assert 'data-section-filter="completed"' in content
    assert 'data-presentation="editorial_dense_v2"' in content
    assert '<html lang="en">' in content
    assert (
        "Outcome: Release &lt;script&gt;alert(1)&lt;/script&gt;. "
        "Next: Observe adoption."
    ) in content
    assert "July 13–20, 2026" in content
    assert 'href="#section-completed"' in content
    assert "data-copy-markdown" in content
    assert "data-print-report" in content
    assert "Release &lt;script&gt;alert(1)&lt;/script&gt;" in content
    assert "<script>alert(1)</script>" not in content
    assert 'href="javascript:' not in content
    assert 'href="https://example.test/releases/2.5"' in content
    assert "https://cdn" not in content
    assert '<details class="supporting" data-supporting-context>' in content
    assert content.index("Release candidate") < content.index(
        "Appendix: delivery and runtime context"
    )
    primary_body, supporting = content.split(
        '<details class="supporting" data-supporting-context>', maxsplit=1
    )
    assert "Artifacts share one normalized document" not in primary_body
    assert "Artifacts share one normalized document" in supporting
    assert artifact["renderer_kind"] == "html"
    assert artifact["media_type"] == "text/html; charset=utf-8"
    assert artifact["single_file"] is True
    assert artifact["zero_build"] is True
    assert artifact["external_dependencies"] == []
    assert artifact["presentation_profile"] == "editorial_dense_v2"
    assert artifact["content_policy"] == {
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
        "editorial_summary_source": "typed_primary_items",
        "readability_policy": "audience_v1",
        "primary_item_count": 2,
        "supporting_item_count": 1,
    }
    assert artifact["boundary"] == {
        "schedule_policy_applied": False,
        "business_evidence_judged": False,
        "external_writes_performed": False,
    }


def test_html_artifact_is_deterministic_for_the_same_document() -> None:
    renderer = periodic_report_html_renderer_adapter()
    first = renderer.render(_document())
    second = renderer.render(_document())

    assert first["content"] == second["content"]
    assert first["content_digest"] == second["content_digest"]
    assert first["artifact_ref"] == second["artifact_ref"]


def test_html_and_markdown_share_one_document_and_primary_content() -> None:
    document = _document()
    html_artifact = periodic_report_html_renderer_adapter().render(document)
    markdown_artifact = periodic_report_markdown_renderer_adapter().render(document)

    assert html_artifact["document_digest"] == markdown_artifact["document_digest"]
    assert (
        html_artifact["companion_markdown_digest"]
        == markdown_artifact["content_digest"]
    )
    for expected in (
        "Release &lt;script&gt;alert(1)&lt;/script&gt;",
        "Published &amp; verified.",
        "One staged rollout completed.",
        "Shipped safely",
        "Observe adoption.",
        "Release candidate",
        "Ready for review.",
        "Outcome: Release &lt;script&gt;alert(1)&lt;/script&gt;. Next: Observe adoption.",
    ):
        assert expected in html_artifact["content"]
    for expected in (
        "Release <script>alert(1)</script>",
        "Published & verified.",
        "One staged rollout completed.",
        "Observe adoption.",
        "Release candidate",
        "Ready for review.",
        "Outcome: Release <script>alert(1)</script>. Next: Observe adoption.",
    ):
        assert expected in markdown_artifact["content"]
    assert "Appendix: delivery and runtime context" in markdown_artifact["content"]
    assert "Artifacts share one normalized document" in markdown_artifact["content"]
    assert markdown_artifact["content_policy"]["supporting_context"] == "appendix"
    assert markdown_artifact["content_policy"]["supporting_item_count"] == 1


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("reference, linked", [
    ("https://example.test/releases/2.5", True),
    ("http://example.test/notes?view=full&part=2", True),
    ("https://example.test/notes/(recovery)?title=\"before\"", True),
    ("sha256:" + "a" * 64, False),
    ("todo:completed-source", False),
    ("artifact:report/window", False),
    ("reports/private-window.md", False),
    ("javascript:alert(1)", False),
    ("data:text/html,<script>alert(1)</script>", False),
    ("//example.test/relative", False),
    ("https:///missing-host", False),
    ("https://example.test:broken/path", False),
    ("https://[broken/path", False),
    ("https://user@example.test/private", False),
    ("https://example.test/line\nbreak", False),
    ("https://example.test/hidden\u202eaddress", False),
    ("https://example.test/\\different-address", False),
    ("https://example.test/" + "x" * 500, False),
])
def test_report_sources_keep_link_eligibility_in_both_formats(reference, linked, language):
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.targets = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "a" and values.get("class") == "source-link":
                self.targets.append(values["href"])

    document = _document(language=language)
    document["sections"][0]["items"][0]["source_ref"] = reference
    html = periodic_report_html_renderer_adapter().render(document)
    markdown = periodic_report_markdown_renderer_adapter().render(document)
    links = Links()
    links.feed(html["content"])
    label = "来源" if language == "zh-CN" else "source"
    assert (reference in links.targets) is linked
    assert markdown["content"].count(f"[{label}](") == (2 if linked else 1)
    assert html["companion_markdown_digest"] == markdown["content_digest"]
    assert html["document_digest"] == markdown["document_digest"]
    # Presentation does not replace substantive outcomes with citation metadata.
    for text in ("Published & verified.", "One staged rollout completed.", "Observe adoption."):
        assert text in markdown["content"]
    if not linked and reference.startswith(("sha256:", "todo:", "artifact:")):
        assert f"`{reference}`" in markdown["content"]


def test_markdown_source_destination_does_not_end_at_literal_parentheses_or_quotes():
    document = _document()
    document["sections"][0]["items"][0]["source_ref"] = 'https://example.test/(before)?title="release"'
    markdown = periodic_report_markdown_renderer_adapter().render(document)["content"]
    assert '[source](https://example.test/%28before%29?title=%22release%22)' in markdown
    assert '[source](https://example.test/(before)' not in markdown


def test_source_receipts_are_supporting_context_not_visible_body_copy() -> None:
    artifact = periodic_report_html_renderer_adapter().render(_document())
    content = artifact["content"]
    primary_body, supporting = content.split(
        '<details class="supporting" data-supporting-context>',
        maxsplit=1,
    )

    assert "Release candidate" in primary_body
    assert "snapshot_" not in primary_body
    assert "supporting context" not in primary_body.lower()
    assert "snapshot_" in supporting
    assert "Artifacts share one normalized document" in supporting


def test_html_hash_navigation_and_copy_status_have_safe_initial_state() -> None:
    content = periodic_report_html_renderer_adapter().render(_document())["content"]

    assert 'href="#section-completed"' in content
    assert "window.addEventListener('hashchange', restoreHash)" in content
    assert "location.hash.startsWith('#section-')" in content
    assert 'data-copy-success="Full report copied"' in content
    assert 'data-copy-failed="Copy failed"' in content


def test_zh_report_localizes_controls_and_keeps_supporting_markdown() -> None:
    document = _document(language="zh-CN")

    html = periodic_report_html_renderer_adapter().render(document)["content"]
    markdown = periodic_report_markdown_renderer_adapter().render(document)["content"]

    for expected in (
        "报告目录",
        "全部章节",
        "搜索报告内容",
        "复制完整文字版",
        "打印 / 存为 PDF",
        "附录：投递与运行信息",
        "查看来源 ↗",
    ):
        assert expected in html
    for unexpected in (
        "Report index",
        "All sections",
        "Filter report",
        "Copy Markdown",
        "Supporting context",
    ):
        assert unexpected not in html
    assert "## 附录：投递与运行信息" in markdown
    assert "Artifacts share one normalized document" in markdown


def test_renderer_rejects_tampered_or_authored_hero_summary() -> None:
    document = _document()
    document["editorial"]["summary"] = (
        "Markdown and HTML use one normalized document; archive separately."
    )

    with pytest.raises(ValueError, match="compiled from typed primary items"):
        periodic_report_html_renderer_adapter().render(document)
    with pytest.raises(ValueError, match="compiled from typed primary items"):
        periodic_report_markdown_renderer_adapter().render(document)


def test_process_narration_is_compiled_out_of_the_hero() -> None:
    process_narration = (
        "Markdown 与 HTML 使用同一 normalized document；"
        "资源归档作为可选 extension 单独验证。"
    )
    source = build_periodic_report_source_result(
        source_id="delivery",
        source_kind="delivery_activity",
        status="complete",
        observed_at="2026-07-20T00:40:00Z",
        sections=[
            {
                "section_id": "outcomes",
                "title": "阶段成果",
                "order": 10,
                "items": [
                    {
                        "item_id": "delivery_result",
                        "title": "交付形成规模",
                        "summary": "本期完成主要交付并进入主干。",
                        "content_kind": "outcome",
                    }
                ],
            },
            {
                "section_id": "report_operations",
                "title": "投递与运行信息",
                "order": 90,
                "items": [
                    {
                        "item_id": "artifact_parity",
                        "title": "双产物与可选归档",
                        "summary": process_narration,
                        "content_kind": "delivery_receipt",
                        "visibility": "supporting",
                    }
                ],
            },
        ],
    )
    document = build_periodic_report_document(
        title="项目周报",
        generated_at="2026-07-20T01:00:00Z",
        period_window={
            "start_at": "2026-07-13T00:00:00Z",
            "end_at": "2026-07-20T00:00:00Z",
        },
        profile={"profile_id": "maintenance", "profile_version": "v1"},
        sources=[source],
        editorial={"language": "zh-CN", "kicker": "项目周报"},
    )

    html = periodic_report_html_renderer_adapter().render(document)["content"]
    primary, supporting = html.split(
        '<details class="supporting" data-supporting-context>', maxsplit=1
    )

    assert "本期进展：交付形成规模。" in primary
    assert process_narration not in primary
    assert process_narration in supporting
