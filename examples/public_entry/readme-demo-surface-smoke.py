#!/usr/bin/env python3
"""Validate the public README and cross-runtime demo surface."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def compact(text: str) -> str:
    return " ".join(text.split())


def main() -> int:
    readme = read("README.md")
    readme_zh = read("README.zh-CN.md")
    demo = read("docs/product/use-cases/cross-runtime/cross-runtime-impl-review-demo.md")
    product_index = read("docs/product/README.md")
    cross_runtime_index = read("docs/product/use-cases/cross-runtime/README.md")
    getting_started = read("docs/guides/getting-started.md")
    compact_readme = compact(readme)
    compact_readme_zh = "".join(readme_zh.split())
    compact_demo = compact(demo)
    for required in [
        '<div align="center">',
        "long-horizon agents",
        "personal agent teams",
        "## Meet the Personal Agent Workspace",
        "docs/assets/personal-workspace/workspace-1.0.webp",
        "docs/architecture/rfcs/capable-manager-semantic-handoff-v0.md",
        "## Why LoopX",
        "docs/assets/harness-neutral.en.svg",
        "docs/assets/control-plane-board.en.svg",
        "## Try LoopX",
        "### Start From Your Agent",
        "Codex App",
        "Codex CLI",
        "Claude Code",
        "Cursor, shell, or custom runner",
        "docs/product/use-cases/cross-runtime/README.md",
        "## Advanced Paths",
        "200+ hours of elapsed loop lifetime",
        "200+ hour public contribution arc",
        "Redacted owner-run showcase",
        "not continuous model execution or unattended production autonomy",
        "docs/assets/long-running-loop-openviking-trajectory.png",
        "docs/assets/long-running-loop-ml-experiment-trajectory.png",
        "### Recurring Work Presets",
        "### Review Agent Work",
        "### App and Projection Paths",
        '<a id="how-it-works"></a>',
        '<a id="quick-start"></a>',
        '<a id="see-it-in-action"></a>',
        '<a id="capability-surface"></a>',
        '<a id="community--feedback"></a>',
        "LoopX 1.0 is a usable local control plane",
        "docs/assets/loopx-lark-developer-group.png",
        "docs/assets/loopx-wechat-contact.png",
        "WeChat: <code>huangrt00</code>",
        "loopx configure-goal --goal-id <goal-id>",
        "loopx preset show daily-triage",
    ]:
        assert required in readme, required

    for required in [
        '<a id="快速开始"></a>',
        '<a id="看几个例子"></a>',
        "docs/assets/harness-neutral.zh.svg",
        "docs/assets/control-plane-board.zh.svg",
        "200+ 小时自然时长",
        "超过 200 小时的公开贡献轨迹",
        "经过脱敏的 owner-run showcase",
        "LoopX 1.0 已经是一套可用的长程 Agent 本地控制面",
        "docs/assets/loopx-lark-developer-group.png",
        "docs/assets/loopx-wechat-contact.png",
        "微信：<code>huangrt00</code>",
        "loopx configure-goal --goal-id <goal-id>",
        "loopx preset show daily-triage",
    ]:
        assert required in readme_zh, required

    # Preserve bilingual, responsive diagrams without requiring an old ASCII
    # rendering of the same architecture. Each picture needs a working fallback.
    svg_ns = "{http://www.w3.org/2000/svg}"
    for language, markdown in (("en", readme), ("zh", readme_zh)):
        for diagram in ("harness-neutral", "control-plane-board"):
            for variant in ("", ".mobile"):
                asset = f"docs/assets/{diagram}.{language}{variant}.svg"
                attribute = "srcset" if variant else "src"
                assert f'{attribute}="{asset}"' in markdown, asset
                root = ET.parse(REPO_ROOT / asset).getroot()
                assert root.tag == f"{svg_ns}svg", asset
                assert len(root.attrib["viewBox"].split()) == 4, asset
                assert root.attrib.get("role") == "img", asset
                for tag in ("title", "desc"):
                    assert root.findtext(f"{svg_ns}{tag}"), (asset, tag)
                assert root.find(f".//{svg_ns}script") is None, asset
            assert '<source media="(max-width: 640px)"' in markdown

    for required in [
        "independent reproduction",
    ]:
        assert required in compact_readme, required
    for required in [
        "不是连续模型执行时长或无人值守的生产自治",
        "不代表连续算力执行、独立复现或生产结果",
    ]:
        assert required.replace(" ", "") in compact_readme_zh, required

    for required in [
        "`$loopx <complex task>`",
        "`loopx todo claim`",
        "`loopx review-packet`",
    ]:
        assert required in compact_readme, required

    for required in [
        "# Cross-Runtime Implement/Review Demo",
        "Claude Code owns an implementation todo",
        "Codex owns a review todo",
        "LoopX owns todo claims, gates, evidence, quota, and the next handoff",
        "loopx todo add --goal-id <goal> --role agent",
        "loopx demo impl-review --preset claude-codex --dry-run",
        "loopx --format json quota should-run --goal-id <goal> --agent-id claude-code-impl",
        "loopx review-packet --goal-id <goal>",
        "cross_runtime_impl_review_demo_packet_v0",
        "Review Verdict Contract",
        "Forbidden evidence",
        "raw Claude or Codex transcripts",
    ]:
        assert required in demo, required

    for required in [
        "verdict",
        "blockers",
        "suggestions",
        "verifier",
        "handoff",
        "docs plus fixture validation",
    ]:
        assert required in compact_demo, required

    assert "use-cases/README.md" in product_index
    # Keep both public entry paths on the guide, while retaining the old design
    # as a secondary reference rather than forbidding historical links entirely.
    assert "docs/product/use-cases/cross-runtime/README.md" in readme.split(
        "## Meet the Personal Agent Workspace", 1
    )[0]
    assert "docs/product/use-cases/cross-runtime/README.md#中文指南" in readme_zh.split(
        "## 认识个人 Agent 工作区", 1
    )[0]
    assert "cross-runtime-impl-review-demo.md" in cross_runtime_index
    assert "../../../../examples/collaboration-delivery/README.md" in cross_runtime_index
    assert "../../../integrations/runtime-connector-catalog.md" in cross_runtime_index
    assert "[Agent collaboration guide](README.md)" in demo
    assert "### Recover History Index Collisions" in getting_started
    assert "history rebuild-index-collisions" in getting_started
    assert "--review-plan-json reviewed-plan.json" in getting_started

    print("readme-demo-surface-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
