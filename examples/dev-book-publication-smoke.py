#!/usr/bin/env python3
"""Validate the monorepo-owned LoopX Developer Book publication contract."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOK = REPO_ROOT / "docs" / "book"
CONTROL_PLANE_COURSE = REPO_ROOT / "docs" / "development" / "control-plane-course"
MKDOCS = REPO_ROOT / "mkdocs.yaml"
MKDOCS_ZH = BOOK / "mkdocs.zh.yaml"
MKDOCS_EN = BOOK / "mkdocs.en.yaml"
BRAND_STYLES = REPO_ROOT / "docs" / "stylesheets" / "loopx.css"
HOMEPAGE = REPO_ROOT / "apps" / "presentation" / "site" / "src" / "App.tsx"

CHAPTERS = (
    "workspace-v1",
    "00-reading-guide",
    "01-from-session-to-loop",
    "02-session-goal-loopx",
    "state-substrate",
    "work-graph-and-authority",
    "core-state-machines",
    "03-one-turn",
    "04-runtime-boundaries",
    "05-connect-existing-project",
    "06-codex-app",
    "07-codex-cli",
    "source-protocol-map",
    "source-trace-protocol-chain",
    "source-change-control-plane-rule",
    "source-validation-to-pr",
    "08-extension-placement",
    "09-extension-scaffold",
    "10-extension-lifecycle",
    "11-engineering-boundaries",
    "12-control-plane-course",
    "appendix-reference",
)

COURSE_PAGES = (
    "00-concept-primer",
    "01-agent-loop-effectful-program",
    "02-goal-control-plane-architecture",
    "03-first-real-loop",
    "04-state-substrate",
    "05-work-graph-and-peers",
    "06-quota-decision-kernel",
    "07-host-scheduler-and-heartbeat",
    "08-evidence-refresh-and-self-repair",
    "09-engineering-a-control-plane-rule",
    "10-autonomous-agent-quality-gates",
    "11-extension-layer",
    "topic-long-horizon-convergence",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact(text: str) -> str:
    return " ".join(text.split())


def markdown_link_targets(markdown: str) -> list[str]:
    return re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", markdown)


def semantic_checkpoints(markdown: str) -> list[str]:
    return re.findall(
        r"<!-- community-casebook:([a-z0-9-]+(?::(?:start|end))?) -->",
        markdown,
    )


def marked_section(markdown: str, section_id: str) -> str:
    start = f"<!-- community-casebook:{section_id}:start -->"
    end = f"<!-- community-casebook:{section_id}:end -->"
    assert start in markdown, start
    assert end in markdown, end
    return markdown.split(start, 1)[1].split(end, 1)[0]


def assert_bilingual_concepts(
    zh_path: str,
    en_path: str,
    concepts: tuple[tuple[str, str], ...],
) -> None:
    zh_text = compact(read(BOOK / zh_path))
    en_text = compact(read(BOOK / en_path))
    for zh_marker, en_marker in concepts:
        assert zh_marker in zh_text, f"{zh_path}: missing bilingual marker {zh_marker}"
        assert en_marker in en_text, f"{en_path}: missing bilingual marker {en_marker}"


def assert_community_casebook_is_bilingual() -> None:
    protocol_map_zh = read(BOOK / "chapters" / "source-protocol-map.md")
    protocol_map_en = read(BOOK / "en" / "chapters" / "source-protocol-map.md")
    validation_zh = read(BOOK / "chapters" / "source-validation-to-pr.md")
    validation_en = read(BOOK / "en" / "chapters" / "source-validation-to-pr.md")

    expected_protocol_checkpoints = [
        "signal-to-bounded-work:start",
        "question-before-fix",
        "user-idea-to-contract",
        "claim-before-code",
        "signal-to-bounded-work:end",
        "rfc-review-lab:start",
        "rfc-status",
        "rfc-community-proposal",
        "rfc-output",
        "rfc-review-lab:end",
    ]
    expected_validation_checkpoints = [
        "small-pr:start",
        "small-pr-problem",
        "small-pr-scope",
        "small-pr-lesson",
        "small-pr:end",
        "review-repair:start",
        "review-repair-problem",
        "review-repair-response",
        "review-repair-lesson",
        "review-repair:end",
    ]
    assert semantic_checkpoints(protocol_map_zh) == expected_protocol_checkpoints
    assert semantic_checkpoints(protocol_map_en) == expected_protocol_checkpoints
    assert semantic_checkpoints(validation_zh) == expected_validation_checkpoints
    assert semantic_checkpoints(validation_en) == expected_validation_checkpoints

    for zh_text, en_text, section_id, required_targets in (
        (
            protocol_map_zh,
            protocol_map_en,
            "signal-to-bounded-work",
            (
                "https://github.com/huangruiteng/loopx/discussions/3069",
                "https://github.com/huangruiteng/loopx/issues/2353",
                "https://github.com/huangruiteng/loopx/issues/3549",
                "https://github.com/huangruiteng/loopx/blob/main/docs/development/contributor-tasks.md",
            ),
        ),
        (
            protocol_map_zh,
            protocol_map_en,
            "rfc-review-lab",
            (
                "https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/README.md",
                "https://github.com/huangruiteng/loopx/discussions/3157",
                "https://github.com/huangruiteng/loopx/blob/main/docs/community/open-strategy-reviews.md",
            ),
        ),
        (
            validation_zh,
            validation_en,
            "small-pr",
            ("https://github.com/huangruiteng/loopx/pull/3540",),
        ),
        (
            validation_zh,
            validation_en,
            "review-repair",
            ("https://github.com/huangruiteng/loopx/pull/3529",),
        ),
    ):
        zh_targets = markdown_link_targets(marked_section(zh_text, section_id))
        en_targets = markdown_link_targets(marked_section(en_text, section_id))
        assert zh_targets == en_targets, section_id
        for target in required_targets:
            assert zh_targets.count(target) == 1, target
            assert en_targets.count(target) == 1, target


def validate_rendered_site(site_dir: Path) -> None:
    def assert_state_machine_diagrams(html: str, route: str) -> None:
        diagram_count = len(re.findall(r'<pre class="mermaid">', html))
        assert diagram_count == 10, (
            f"{route}: expected 10 Mermaid source containers, found {diagram_count}"
        )
        assert "language-mermaid" not in html, (
            f"{route}: Mermaid source was rendered as a highlighted code block"
        )
        assert "javascripts/mermaid.js" not in html, (
            f"{route}: stale custom Mermaid initializer is still referenced"
        )
        assert len(re.findall(r'<script src="[^"]*assets/javascripts/bundle\.[^"]+\.min\.js"></script>', html)) == 1, (
            f"{route}: expected the single MkDocs Material renderer owner"
        )

    routes = {
        "index.html": ("LoopX Developer Book", "English edition", "MkDocs Material"),
        "chapters/00-reading-guide/index.html": (
            "Dev Book 与 Control-Plane Course 如何配合",
            "/loopx/docs/development/control-plane-course/06-quota-decision-kernel/",
        ),
        "chapters/workspace-v1/index.html": (
            "操作 LoopX 1.0 Workspace",
            "typed preview",
            "live_steering",
        ),
        "chapters/01-from-session-to-loop/index.html": (
            "从一次会话到长程任务",
        ),
        "chapters/core-state-machines/index.html": (
            "主要状态机与状态流转",
            "一条 Loop、三层抽象、九组状态机",
            "先认识名词：把“事实、判断、动作、证明”分开",
            "为什么这样设计，而不是一个大状态机",
            "状态机通过事实与协议接力",
            "L0：为什么需要 LoopX",
            "GoalControlSnapshot",
            "expected_provider_revision",
            "deferred_resume_candidate",
            "Agent 提交 bounded proposal",
            "Agent 判断错了怎么办",
            "Source state",
            "durable_writeback",
            "LoopX 怎样体现“闭环”",
            "terminal_no_followup",
        ),
        "chapters/05-connect-existing-project/index.html": (
            "快速阅读路线",
            "让 Agent 帮你接入",
        ),
    }
    english_routes = {
        "index.html": ("LoopX Developer Book", "简体中文版", "MkDocs Material"),
        "chapters/00-reading-guide/index.html": (
            "How the Dev Book and Control-Plane Course work together",
            "/loopx/docs/development/control-plane-course/06-quota-decision-kernel/",
        ),
        "chapters/workspace-v1/index.html": (
            "Operate the LoopX 1.0 Workspace",
            "typed preview",
            "live_steering",
        ),
        "chapters/01-from-session-to-loop/index.html": (
            "From one session to long-running work",
        ),
        "chapters/core-state-machines/index.html": (
            "Core state machines and transitions",
            "one Loop, three abstraction levels, nine state-machine families",
            "Vocabulary first: separate facts, decisions, actions, and proof",
            "Why this design instead of one large state machine",
            "facts and protocols connect the machines",
            "L0: Why does LoopX need to exist",
            "GoalControlSnapshot",
            "expected_provider_revision",
            "deferred_resume_candidate",
            "Agent submits bounded proposal",
            "What if the Agent is wrong",
            "Source state",
            "durable_writeback",
            "What makes LoopX a closed loop",
            "terminal_no_followup",
        ),
        "chapters/05-connect-existing-project/index.html": (
            "Fast reading path",
            "Delegate onboarding to an Agent",
        ),
    }
    for relative_path, markers in routes.items():
        target = site_dir / relative_path
        assert target.is_file(), f"missing rendered Developer Book route: {relative_path}"
        html = read(target)
        for marker in markers:
            assert marker in html, f"{relative_path}: missing rendered marker {marker}"
        if relative_path == "chapters/core-state-machines/index.html":
            assert_state_machine_diagrams(html, relative_path)
        assert ":::: tip" not in html, f"{relative_path}: unrendered VitePress container"
        assert 'data-md-color-scheme="slate"' in html, f"{relative_path}: not dark by default"
        if relative_path == "index.html":
            chapter_links = set(re.findall(r'href="[^"]*chapters/[^"#?]+/?(?:index\.html)?"', html))
            assert len(chapter_links) == len(CHAPTERS), (
                f"{relative_path}: expected {len(CHAPTERS)} Chinese chapter links, "
                f"found {len(chapter_links)}"
            )
        else:
            assert html.count("md-nav__item--active md-nav__item--section") == 1, (
                f"{relative_path}: expected exactly one expanded Chinese Part"
            )
        assert "English Chapters" not in html
        assert "Part I — Control-plane foundations" not in html

    english_site_dir = site_dir / "en"
    for relative_path, markers in english_routes.items():
        target = english_site_dir / relative_path
        assert target.is_file(), f"missing rendered English Developer Book route: {relative_path}"
        html = read(target)
        for marker in markers:
            assert marker in html, f"en/{relative_path}: missing rendered marker {marker}"
        if relative_path == "chapters/core-state-machines/index.html":
            assert_state_machine_diagrams(html, f"en/{relative_path}")
        assert 'data-md-color-scheme="slate"' in html, f"en/{relative_path}: not dark by default"
        if relative_path == "index.html":
            chapter_links = set(re.findall(r'href="[^"]*chapters/[^"#?]+/?(?:index\.html)?"', html))
            assert len(chapter_links) == len(CHAPTERS), (
                f"en/{relative_path}: expected {len(CHAPTERS)} English chapter links, "
                f"found {len(chapter_links)}"
            )
        else:
            assert html.count("md-nav__item--active md-nav__item--section") == 1, (
                f"en/{relative_path}: expected exactly one expanded English Part"
            )
        for chinese_section in (
            "第一部分：控制面基础",
            "第二部分：接入现有项目",
            "第三部分：开发者贡献",
            "第四部分：工程边界",
        ):
            assert chinese_section not in html

    main_docs_dir = site_dir.parent
    for page in COURSE_PAGES:
        target = main_docs_dir / "development" / "control-plane-course" / page / "index.html"
        assert target.is_file(), f"missing rendered Control-Plane Course route: {page}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir", type=Path)
    args = parser.parse_args()

    assert not (BOOK / "labs").exists(), "Dev Book publication must not include Labs"
    assert not (REPO_ROOT / "mkdocs.book.zh.yaml").exists()
    assert not (REPO_ROOT / "mkdocs.book.en.yaml").exists()

    mkdocs = read(MKDOCS)
    assert "book/chapters/" not in mkdocs
    assert "book/en/chapters/" not in mkdocs
    assert "book/**" in mkdocs
    assert "javascripts/mermaid.js" not in mkdocs
    assert not (REPO_ROOT / "docs" / "javascripts" / "mermaid.js").exists()
    chinese_state_machines = read(BOOK / "chapters" / "core-state-machines.md")
    english_state_machines = read(BOOK / "en" / "chapters" / "core-state-machines.md")
    for chapter in (chinese_state_machines, english_state_machines):
        assert "must_attempt_work" in chapter
        assert "selection_command" in chapter
        assert "next_cli_actions[0]" in chapter
        assert "turn_instance_id" in chapter
    docs_home = read(REPO_ROOT / "docs" / "index.md")
    docs_readme = read(REPO_ROOT / "docs" / "README.md")
    assert "Developer Book](/loopx/docs/book/)" in docs_home
    assert "Developer Book](/loopx/docs/book/)" in docs_readme
    assert "English edition](/loopx/docs/book/en/)" in docs_readme

    zh_config = read(MKDOCS_ZH)
    en_config = read(MKDOCS_EN)
    for config in (zh_config, en_config):
        assert "INHERIT: ../../mkdocs.yaml" in config
        assert config.index("scheme: slate") < config.index("scheme: default")
        assert "navigation.sections" in config
        assert "navigation.expand" not in config
    assert "docs_dir: ." in zh_config
    assert "site_dir: ../../output/docs-book-zh" in zh_config
    assert "language: zh" in zh_config
    assert "en/**" in zh_config
    assert "docs_dir: en" in en_config
    assert "site_dir: ../../output/docs-book-en" in en_config
    assert "language: en" in en_config
    assert "中文:" not in en_config

    for chapter in CHAPTERS:
        assert f"chapters/{chapter}.md" in zh_config, chapter
        assert f"chapters/{chapter}.md" in en_config, f"en/{chapter}"

    styles = read(BRAND_STYLES)
    for marker in (
        "#050914",
        "#091425",
        "#6eabff",
        "#96c8ff",
        "Iowan Old Style",
        "SFMono-Regular",
        ".md-sidebar",
        ".md-nav__item--section",
        ".md-typeset .grid.cards",
    ):
        assert marker in styles, marker

    homepage = read(HOMEPAGE)
    for path in (
        "docs/book/",
        "chapters/01-from-session-to-loop/",
        "chapters/05-connect-existing-project/",
        "chapters/source-protocol-map/",
    ):
        assert path in homepage
    assert 'language === "en" ? "en/" : ""' in homepage

    for locale_root in (BOOK / "index.md", BOOK / "en" / "index.md"):
        text = read(locale_root)
        assert "layout: home" not in text
        assert "theme: brand" not in text
        assert "VitePress" not in text
        assert "MkDocs Material" in text
    assert "/loopx/docs/book/en/" in read(BOOK / "index.md")
    assert "/loopx/docs/book/" in read(BOOK / "en" / "index.md")

    project_version = tomllib.loads(read(REPO_ROOT / "pyproject.toml"))["project"]["version"]
    release_tag = f"v{project_version}"
    # Historical migration milestones must not move with the package version.
    migration_baseline_tag = "v0.5.4"
    release_markers = {
        "index.md": (
            f"LoopX 发布锚点：`{release_tag}`",
            "TypeScript Control-Plane Migration RFC",
        ),
        "chapters/00-reading-guide.md": (
            f"release `{release_tag}`",
            "transaction-payoff phase",
        ),
        "en/index.md": (
            f"LoopX release anchor: `{release_tag}`",
            "TypeScript Control-Plane Migration RFC",
        ),
        "en/chapters/00-reading-guide.md": (
            f"release `{release_tag}`",
            "transaction-payoff phase",
        ),
    }
    for relative_path, markers in release_markers.items():
        text = read(BOOK / relative_path)
        for marker in markers:
            assert marker in text, f"{relative_path}: missing release-baseline marker {marker}"

    for page in COURSE_PAGES:
        assert (CONTROL_PLANE_COURSE / f"{page}.md").is_file(), page

    assert_community_casebook_is_bilingual()

    reading_guides = (
        read(BOOK / "chapters" / "00-reading-guide.md"),
        read(BOOK / "en" / "chapters" / "00-reading-guide.md"),
    )
    for guide in reading_guides:
        assert "/loopx/docs/development/control-plane-course/" in guide
        for page in COURSE_PAGES:
            assert f"/loopx/docs/development/control-plane-course/{page}/" in guide, page

    assert_bilingual_concepts(
        "chapters/workspace-v1.md",
        "en/chapters/workspace-v1.md",
        (
            ("Personal Workspace milestone", "Personal Workspace milestone"),
            ("不是一套新的事实源", "not a new source of truth"),
            ("loopx dashboard --no-open", "loopx dashboard --no-open"),
            ("typed preview -> human or policy review -> governed apply -> verified receipt -> refreshed projection", "typed preview -> human or policy review -> governed apply -> verified receipt -> refreshed projection"),
            ("loopx machine-config inspect --format json", "loopx machine-config inspect --format json"),
            ("`live_steering`", "`live_steering`"),
            ("`session_queue`", "`session_queue`"),
            ("`async_inbox`", "`async_inbox`"),
            ("loopx periodic-report inspect-profile --preset weekly --format json", "loopx periodic-report inspect-profile --preset weekly --format json"),
            ("Stage 2C authority", "Stage 2C authority"),
        ),
    )
    assert_bilingual_concepts(
        "index.md",
        "en/index.md",
        (
            (f"LoopX 发布锚点：`{release_tag}`", f"LoopX release anchor: `{release_tag}`"),
            ("运行时前提：Python 3.11+ 与 Node.js 22.22.3+", "Runtime prerequisites: Python 3.11+ and Node.js 22.22.3+"),
            ("TypeScript owner", "TypeScript owners"),
            ("这不是两套可独立演进的 控制面", "These are not two independently evolving control planes"),
        ),
    )
    assert_bilingual_concepts(
        "chapters/00-reading-guide.md",
        "en/chapters/00-reading-guide.md",
        (
            ("语义镜像", "semantic mirrors"),
            ("Python 3.11+", "Python 3.11+"),
            ("Node.js 22.22.3+", "Node.js 22.22.3+"),
            ("用户不需要手工维护 daemon", "users do not operate that runtime as a manual daemon"),
            ("TypeScript 已拥有", "TypeScript owns"),
            ("Python CLI 仍负责", "Python CLI still owns"),
            ("不能再实现第二套独立 decision", "must not become a second independent decision implementation"),
            ("transaction-payoff phase", "transaction-payoff phase"),
            ("Stage 3 的第一个 receipt-bound scheduler follow-up 切片", "first receipt-bound scheduler follow-up slice of Stage 3"),
            ("Stage 4 distribution cleanup 仍是后续方向", "Stage 4 distribution cleanup remain future work"),
            ("安装 Provider 不会改变默认本地 authority", "installing a Provider does not change the default local authority"),
        ),
    )
    assert_bilingual_concepts(
        "chapters/02-session-goal-loopx.md",
        "en/chapters/02-session-goal-loopx.md",
        tuple((marker, marker) for marker in (
            "OpenCode 1/2",
            "Pi",
            "KunlunCode Goal Pro",
            "DeepSeek Harness",
            "Runtime Connector Catalog",
        )),
    )
    assert_bilingual_concepts(
        "chapters/core-state-machines.md",
        "en/chapters/core-state-machines.md",
        (
            ("不靠一个巨型状态机", "does not advance a Goal through one giant state machine"),
            ("一条 effectful Agent Loop", "one effectful Agent Loop"),
            ("一条 Loop、三层抽象、九组状态机", "one Loop, three abstraction levels, nine state-machine families"),
            ("状态机通过事实与协议接力", "facts and protocols connect the machines"),
            ("没有一个可以整体覆盖的 `GoalState` 大对象", "does **not** expose one `GoalState` object"),
            ("Agent 提交的是 proposal 或 typed effect", "An Agent submits a proposal or typed effect"),
            ("Agent 判断错了怎么办", "What if the Agent is wrong"),
            ("LoopX 怎样体现“闭环”", "What makes LoopX a closed loop"),
            ("不是 `Todo=done`", "not the same as `Todo=done`"),
            ("严格合取", "strict conjunction"),
        ),
    )
    assert_bilingual_concepts(
        "chapters/03-one-turn.md",
        "en/chapters/03-one-turn.md",
        (
            (f"`{migration_baseline_tag}` 仍提供", f"`{migration_baseline_tag}` still ships"),
            ("显式 opt-in 集成", "explicit opt-in integrations"),
            ("Turn settlement", "Turn settlement"),
            ("Todo completion", "Todo completion"),
            ("Host Todo settlement", "Host Todo settlement"),
            ("spend/void/monitor-poll commit", "spend/void/monitor-poll commit"),
            ("本地 task-lease 完整生命周期", "full local task-lease lifecycle"),
            ("Vision refresh", "Vision refresh"),
            ("receipt-bound scheduler follow-up", "receipt-bound scheduler follow-up"),
            ("Python 已被移除", "Python has been removed"),
            ("TypeScript Control-Plane Migration RFC", "TypeScript Control-Plane Migration RFC"),
        ),
    )
    assert_bilingual_concepts(
        "chapters/state-substrate.md",
        "en/chapters/state-substrate.md",
        (
            (f"`{migration_baseline_tag}` 的 shared-authority 工作", f"Shared-authority work in `{migration_baseline_tag}`"),
            ("provider-neutral TypeScript `AuthorityStore` contract", "provider-neutral TypeScript `AuthorityStore` contract"),
            ("不自动获得 runtime authority", "do not acquire runtime authority automatically"),
            ("不应倒推成", "are not evidence that"),
        ),
    )
    assert_bilingual_concepts(
        "chapters/05-connect-existing-project.md",
        "en/chapters/05-connect-existing-project.md",
        (
            ("Node.js 22.22.3", "Node.js 22.22.3"),
            ("Windows PowerShell 7", "Windows PowerShell 7"),
            ("loopx doctor --deep", "loopx doctor --deep"),
            ("用户不需要手工维护 daemon", "users do not supervise a daemon manually"),
            ("`missing`、`unsupported` 或 `probe_failed`", "`missing`, `unsupported`, or `probe_failed`"),
        ),
    )
    assert_bilingual_concepts(
        "chapters/source-protocol-map.md",
        "en/chapters/source-protocol-map.md",
        (
            (f"在 `{migration_baseline_tag}` 的迁移基线上", f"On the `{migration_baseline_tag}` migration baseline"),
            ("bounded context 和实现语言是两个维度", "bounded context and implementation language are separate dimensions"),
            ("本地 task-lease lifecycle", "local task-lease lifecycle"),
            ("receipt-bound scheduler follow-up", "receipt-bound scheduler follow-up"),
            ("Python facade", "Python facade"),
            ("loopx capability list --format json", "loopx capability list --format json"),
            ("仅有 目录或 README 不证明能力已经发布", "A directory or README alone does not prove that a capability is shipped"),
        ),
    )
    assert_bilingual_concepts(
        "chapters/11-engineering-boundaries.md",
        "en/chapters/11-engineering-boundaries.md",
        (
            ("loopx doctor --deep", "loopx doctor --deep"),
            ("loopx capability list --format json", "loopx capability list --format json"),
            ("TypeScript migration RFC", "TypeScript migration RFC"),
            ("facade exit condition", "facade exit conditions"),
        ),
    )
    assert_bilingual_concepts(
        "chapters/appendix-reference.md",
        "en/chapters/appendix-reference.md",
        (
            ("loopx todo list --goal-id <goal-id> --thin --format json", "loopx todo list --goal-id <goal-id> --thin --format json"),
            (f"`{migration_baseline_tag}` 新增的 `todo list --thin`", f"The `todo list --thin` option added in `{migration_baseline_tag}`"),
            ("不改变默认 list 的选择、排序、quota 或 lifecycle 语义", "without changing default selection, ordering, quota, or lifecycle semantics"),
        ),
    )

    integrated_mechanisms = {
        "chapters/01-from-session-to-loop.md": (
            "PR Issue Fix",
            "Single-Agent Auto ML",
            "Multi-Agent Auto Research",
            "/loopx/docs/development/control-plane-course/02-goal-control-plane-architecture/",
        ),
        "chapters/03-one-turn.md": (
            "Decision Pipeline",
            "identity",
            "capability and workspace eligibility",
            "/loopx/docs/development/control-plane-course/06-quota-decision-kernel/",
        ),
        "chapters/04-runtime-boundaries.md": (
            "Material Evidence Delta",
            "Goal / Acceptance",
            "六条收敛不变量",
            "/loopx/docs/development/control-plane-course/topic-long-horizon-convergence/",
        ),
        "en/chapters/01-from-session-to-loop.md": (
            "PR Issue Fix",
            "Single-Agent Auto ML",
            "Multi-Agent Auto Research",
            "/loopx/docs/development/control-plane-course/02-goal-control-plane-architecture/",
        ),
        "en/chapters/03-one-turn.md": (
            "Decision pipeline",
            "Identity",
            "capability and workspace eligibility",
            "/loopx/docs/development/control-plane-course/06-quota-decision-kernel/",
        ),
        "en/chapters/04-runtime-boundaries.md": (
            "Material evidence delta",
            "Goal or Acceptance",
            "Six convergence invariants",
            "/loopx/docs/development/control-plane-course/topic-long-horizon-convergence/",
        ),
    }
    for relative_path, markers in integrated_mechanisms.items():
        chapter = read(BOOK / relative_path)
        for marker in markers:
            assert marker in chapter, f"{relative_path}: missing integrated mechanism {marker}"

    all_markdown = "\n".join(
        read(path) for path in BOOK.rglob("*.md")
    )
    for forbidden in (
        "cocolord.github.io/loopx-book",
        "cocolord/loopx-book-labs",
        "站点生成器：VitePress",
        "Site generator: VitePress",
        "当前站点使用 VitePress",
        "The site uses VitePress",
        ":::: tip",
        ":::: warning",
        ":::: info",
    ):
        assert forbidden not in all_markdown, forbidden

    if args.site_dir is not None:
        validate_rendered_site(args.site_dir)

    print("dev-book-publication-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
