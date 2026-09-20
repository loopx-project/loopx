#!/usr/bin/env python3
"""Smoke-test the public docs information architecture."""

from __future__ import annotations

from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

ROOT_DOCS = {
    "README.md",
    "architecture.md",
    "community.md",
    "community.zh-CN.md",
    "heartbeat-automation-prompt.md",
    "index.md",
    "integration.md",
    "project-agent-todo-contract.md",
    "public-private-boundary.md",
    "quota-allocation.md",
    "state-interaction-model.md",
    "status-data-contract.md",
}

PRODUCT_ROOT_DOCS = {
    "README.md",
    "domain-capability-packs.md",
    "public-adoption-loop.md",
    "release-note-template.md",
    "release-readiness.md",
    "scenario-capability-gap-map.md",
    "vision.md",
}

LOCAL_LINK_PATTERNS = (
    re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)\s]+)"),
    re.compile(r"^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)", re.MULTILINE),
    re.compile(r"\b(?:href|src)=[\"']([^\"']+)[\"']"),
)


MOVED_PATHS = {
    "docs/commit-readiness-manifest-20260603.md": (
        "docs/archive/release-readiness/commit-readiness-manifest-20260603.md"
    ),
    "docs/commit-readiness-manifest-20260606.md": (
        "docs/archive/release-readiness/commit-readiness-manifest-20260606.md"
    ),
    "docs/outcome-floor-safe-bypass-incident-20260606.md": (
        "docs/archive/incidents/outcome-floor-safe-bypass-incident-20260606.md"
    ),
    "docs/protocol-action-packet-codex-cli-wrapper-v0.md": (
        "docs/reference/protocols/protocol-action-packet-codex-cli-wrapper-v0.md"
    ),
    "docs/protocol-action-packet-decision-v0.md": (
        "docs/reference/protocols/protocol-action-packet-decision-v0.md"
    ),
    "docs/protocol-action-packet-router-comparison-v0.md": (
        "docs/reference/protocols/protocol-action-packet-router-comparison-v0.md"
    ),
    "docs/codex-cli-long-run-benchmark-design.md": (
        "deprecate/benchmark-legacy/docs/research/long-horizon-agent-benchmarks/"
        "codex-cli-long-run-benchmark-design.md"
    ),
    "docs/codex-cli-long-run-regression.md": (
        "deprecate/benchmark-legacy/docs/research/long-horizon-agent-benchmarks/"
        "codex-cli-long-run-regression.md"
    ),
    "docs/project-skill-delivery.md": (
        "loopx/capabilities/project_skill_delivery/README.md"
    ),
    "CONTRIBUTOR_TASKS.md": "docs/development/contributor-tasks.md",
    "DESIGN.md": "docs/development/design.md",
    "AUTHORS.md": "docs/project/authors.md",
    "TRADEMARKS.md": "docs/project/trademarks.md",
}

# docs/index.md .md targets that stay outside mkdocs nav on purpose.
# Prefer fixing mkdocs.yaml nav for public hosted entry points instead.
DOCS_INDEX_NAV_ALLOWLIST: dict[str, str] = {
    "community.md": "hosted community entry linked below the first screen; top-nav promotion remains owner-reviewed",
    "community.zh-CN.md": "zh locale sibling for the hosted community entry",
}

# docs/README.md catalog .md targets that stay outside mkdocs top nav on purpose.
DOCS_CATALOG_NAV_ALLOWLIST = {
    "architecture/README.md": "architecture tree index; RFCs linked from Reference nav",
    "archive/README.md": "excluded from hosted site via exclude_docs",
    "community.md": "community entry linked from the hosted index below the first screen",
    "community.zh-CN.md": "zh locale sibling for the community entry",
    "community/open-strategy-reviews.md": "community process; catalog-only entry",
    "community/open-strategy-reviews.zh-CN.md": "zh locale sibling for community reviews",
    "development/contributor-tasks.md": "contributor board; not a hosted docs primary page",
    "project/authors.md": "project meta linked from README community section",
    "project/brand-guide.md": "project meta linked from README community section",
    "project/brand-guide.zh-CN.md": "zh locale sibling for brand guide",
    "project/history.md": "project meta linked from README community section",
    "project/licensing.md": "project meta linked from README community section",
    "project/trademarks.md": "project meta linked from README community section",
    "reference/effect-interpreter-packet.md": "deep packet doc reachable from Reference",
    "research/README.md": "research evidence index; not a top-nav primary",
    "update-notes/README.md": "dated progress notes; catalog-only entry",
}

# These RFCs predate the bilingual RFC rule. New and modified RFCs must carry
# a same-basename Chinese mirror; keep this legacy list explicit until each is
# migrated rather than silently weakening the invariant.
RFC_BILINGUAL_LEGACY_ALLOWLIST = {
    "agent-im-openviking-collaboration-v0.md",
    "benchmark-study-upload-dashboard-v0.md",
    "goal-usage-token-cost-v0.md",
    "provider-neutral-turn-start-inbox-hook-v0.md",
    "single-owner-local-daemon-v0.md",
    # Existing mirrors that predate reciprocal language links.
    "cross-session-memory-substrate-v0.md",
    "desktop-execution-frontends-v0.md",
    "goal-channel-collaboration-v0.md",
    "obelisk-session-evidence-provider-v0.md",
    "post-outcome-memory-utility-attribution-v0.md",
    "goal-direction-baseline-v0.md",
    "harness-selection-dsh-pi-v0.md",
}

# Stable README advanced-docs entry links under docs/ that must stay reachable.
STABLE_README_DOCS_ENTRY_LINKS = (
    "operations/README.md",
    "quota-allocation.md",
    "heartbeat-automation-prompt.md",
    "status-data-contract.md",
    "concepts/README.md",
    "product/foundations/README.md",
    "product/vision.md",
    "integration.md",
    "integrations/README.md",
    "development/README.md",
    "reference/README.md",
    "development/control-plane-course/README.md",
    "development/testing-and-quality.md",
    "public-private-boundary.md",
    "showcases/README.md",
    "research/README.md",
    "update-notes/README.md",
    "project/technical-directions.md",
    "development/contributor-tasks.md",
    "project/authors.md",
    "project/history.md",
    "project/trademarks.md",
    "project/brand-guide.md",
)

MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\((<[^>]+>|[^)\s]+)")



def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def compact(text: str) -> str:
    return " ".join(text.split())


def subsection(text: str, heading: str) -> str:
    marker = f"### {heading}"
    assert marker in text, marker
    body = text.split(marker, 1)[1]
    return body.split("\n### ", 1)[0].split("\n## ", 1)[0]


def _normalize_md_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    return target.split("#", 1)[0].split("?", 1)[0]


def iter_relative_md_targets(text: str) -> list[str]:
    targets: list[str] = []
    for match in MD_LINK_RE.finditer(text):
        target = _normalize_md_target(match.group(1))
        if not target or not target.endswith(".md"):
            continue
        if target.startswith(("http://", "https://", "mailto:", "/")):
            continue
        targets.append(target)
    return targets


def check_rfc_language_mirrors() -> None:
    """Require bilingual mirrors for new RFCs and validate reciprocal links."""
    rfc_dir = DOCS / "architecture" / "rfcs"
    for english in sorted(rfc_dir.glob("*.md")):
        if english.name in {"README.md", "TEMPLATE.md"} or english.name.endswith(".zh-CN.md"):
            continue
        if english.name in RFC_BILINGUAL_LEGACY_ALLOWLIST:
            continue
        chinese = english.with_name(f"{english.stem}.zh-CN.md")
        if not chinese.exists():
            raise AssertionError(f"RFC missing required Chinese mirror: {english.name}")
        english_text = english.read_text(encoding="utf-8")
        chinese_text = chinese.read_text(encoding="utf-8")
        assert chinese.name in english_text, f"RFC missing English -> Chinese link: {english.name}"
        assert english.name in chinese_text, f"RFC missing Chinese -> English link: {chinese.name}"
        assert "semantic mirror" in english_text.lower(), english.name
        assert "语义镜像" in chinese_text, chinese.name


LEDGER_ENTRY_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
# The appendix that points at the ledger directory is whichever one an RFC has
# free: an RFC whose Appendix A carries other content adopts a later letter
# rather than renumbering history and forcing every open branch to re-resolve it.
LEDGER_APPENDIX_HEADING = re.compile(r"^## Appendix [A-Z]: Execution ledger", re.MULTILINE)


def check_rfc_ledger_entries() -> None:
    """Validate the per-file RFC execution ledger.

    The ledger exists so that two branches adding an entry on the same day touch
    two different files instead of the same append cluster. That only holds while
    every entry is its own file with a sortable name, so the naming is checked
    rather than described. Nothing enumerates the entries: an index line would
    reintroduce exactly the shared line this directory removes.
    """
    ledger = DOCS / "architecture" / "rfcs" / "ledger"
    if not ledger.is_dir():
        return
    assert (ledger / "README.md").exists(), "ledger README missing"
    assert (ledger / "README.zh-CN.md").exists(), "ledger README missing its Chinese mirror"
    # Several RFCs carry an execution-ledger appendix, so entries are shared and
    # must say which one they belong to. The RFC slug is a directory, and the
    # directory has to name a real RFC: an entry cannot claim an RFC that does
    # not exist, and the per-RFC listing stays the index.
    scopes = sorted(path for path in ledger.iterdir() if path.is_dir())
    assert scopes, "ledger has no per-RFC directories"
    for scope in scopes:
        rfc_document = DOCS / "architecture" / "rfcs" / f"{scope.name}.md"
        assert rfc_document.is_file(), (
            f"ledger directory {scope.name}/ does not name an RFC: "
            f"{rfc_document.relative_to(DOCS.parent)} does not exist"
        )
        assert LEDGER_APPENDIX_HEADING.search(
            rfc_document.read_text(encoding="utf-8")
        ), (
            f"{scope.name} has a ledger directory but no execution-ledger appendix"
        )
        for entry in sorted(scope.glob("*.md")):
            if entry.name.endswith(".zh-CN.md"):
                english = entry.with_name(entry.name[: -len(".zh-CN.md")] + ".md")
                assert english.exists(), (
                    f"ledger entry has a Chinese mirror with no English original: {entry.name}"
                )
                continue
            assert LEDGER_ENTRY_NAME.match(entry.stem), (
                f"ledger entry must be named YYYY-MM-DD-slug.md, got: {entry.name}"
            )
            chinese = entry.with_name(f"{entry.stem}.zh-CN.md")
            assert chinese.exists(), f"ledger entry missing required Chinese mirror: {entry.name}"
            assert entry.read_text(encoding="utf-8").strip(), f"empty ledger entry: {entry.name}"
            assert chinese.read_text(encoding="utf-8").strip(), f"empty ledger entry: {chinese.name}"


def mkdocs_nav_paths(mkdocs_text: str) -> set[str]:
    assert "\nnav:\n" in mkdocs_text or mkdocs_text.startswith("nav:\n"), mkdocs_text
    nav_body = mkdocs_text.split("nav:", 1)[1]
    return set(re.findall(r"([A-Za-z0-9_./-]+\.md)", nav_body))


def generated_docs_site_sources() -> dict[str, Path]:
    repo_root = str(REPO_ROOT)
    docs_dir = str(DOCS)
    for path in (repo_root, docs_dir):
        if path not in sys.path:
            sys.path.insert(0, path)
    from capability_docs import documentation_maps

    site_to_source, _source_to_site = documentation_maps()
    return {site.as_posix(): source for site, source in site_to_source.items()}


def resolve_docs_relative_target(source_docs_rel: str, target: str) -> Path:
    source = DOCS / source_docs_rel
    return (source.parent / target).resolve()


def assert_path_in_nav_or_allowlisted(
    *,
    source_label: str,
    docs_relative: str,
    nav_paths: set[str],
    allowlist: dict[str, str],
) -> None:
    if docs_relative in nav_paths:
        return
    reason = allowlist.get(docs_relative)
    assert reason, (
        f"{source_label} links to {docs_relative}, which is missing from "
        "mkdocs.yaml nav and has no allowlist reason"
    )
    assert reason.strip(), f"{docs_relative}: empty allowlist reason"


def assert_hosted_docs_nav_parity() -> None:
    """Catch broken or orphaned hosted-docs entry points without touching README UI."""
    mkdocs_text = read("mkdocs.yaml")
    nav_paths = mkdocs_nav_paths(mkdocs_text)
    assert nav_paths, "mkdocs.yaml nav must list hosted pages"
    assert "book/**" in mkdocs_text, "Developer Book stays on its own MkDocs configs"

    generated_sources = generated_docs_site_sources()
    for nav_path in sorted(nav_paths):
        target = DOCS / nav_path
        if target.is_file():
            continue
        generated_source = generated_sources.get(nav_path)
        assert generated_source is not None and generated_source.is_file(), (
            f"orphaned mkdocs nav entry (missing file): {nav_path}"
        )

    docs_index = read("docs/index.md")
    for raw_target in iter_relative_md_targets(docs_index):
        resolved = resolve_docs_relative_target("index.md", raw_target)
        assert resolved.exists(), f"broken docs/index.md link: {raw_target}"
        docs_relative = str(resolved.relative_to(DOCS.resolve()))
        assert_path_in_nav_or_allowlisted(
            source_label="docs/index.md",
            docs_relative=docs_relative,
            nav_paths=nav_paths,
            allowlist=DOCS_INDEX_NAV_ALLOWLIST,
        )

    docs_catalog = read("docs/README.md")
    for raw_target in iter_relative_md_targets(docs_catalog):
        if raw_target.startswith("../"):
            resolved = (DOCS / raw_target).resolve()
            assert resolved.exists(), f"broken docs catalog link: {raw_target}"
            continue
        resolved = resolve_docs_relative_target("README.md", raw_target)
        assert resolved.exists(), f"broken docs catalog link: {raw_target}"
        try:
            docs_relative = str(resolved.relative_to(DOCS.resolve()))
        except ValueError:
            continue
        assert_path_in_nav_or_allowlisted(
            source_label="docs/README.md",
            docs_relative=docs_relative,
            nav_paths=nav_paths,
            allowlist=DOCS_CATALOG_NAV_ALLOWLIST,
        )

    for allowlist_path, reason in {
        **DOCS_INDEX_NAV_ALLOWLIST,
        **DOCS_CATALOG_NAV_ALLOWLIST,
    }.items():
        assert reason.strip(), f"{allowlist_path}: empty allowlist reason"
        assert allowlist_path not in nav_paths, (
            f"{allowlist_path} is in mkdocs nav; remove the stale allowlist entry"
        )
        assert (DOCS / allowlist_path).is_file(), (
            f"allowlisted docs path missing: {allowlist_path}"
        )

    for docs_relative in STABLE_README_DOCS_ENTRY_LINKS:
        assert (DOCS / docs_relative).is_file(), (
            f"stable README docs entry missing: {docs_relative}"
        )
        assert_path_in_nav_or_allowlisted(
            source_label="README stable docs entry",
            docs_relative=docs_relative,
            nav_paths=nav_paths,
            allowlist=DOCS_CATALOG_NAV_ALLOWLIST,
        )

    book_zh = read("docs/book/index.md")
    book_en = read("docs/book/en/index.md")
    assert "[English edition](/loopx/docs/book/en/)" in book_zh, (
        "docs/book/index.md must cross-link the English edition"
    )
    assert "[简体中文版](/loopx/docs/book/)" in book_en, (
        "docs/book/en/index.md must cross-link the Chinese edition"
    )


def assert_local_doc_links_resolve() -> None:
    for source in DOCS.rglob("*"):
        if source.suffix.lower() not in {".md", ".html"}:
            continue
        text = source.read_text(encoding="utf-8")
        for pattern in LOCAL_LINK_PATTERNS:
            for match in pattern.finditer(text):
                raw_target = match.group(1)
                if raw_target.startswith("<") and raw_target.endswith(">"):
                    raw_target = raw_target[1:-1]
                if not raw_target or raw_target.startswith(
                    (
                        "#",
                        "/",
                        "http://",
                        "https://",
                        "mailto:",
                        "data:",
                        "javascript:",
                    )
                ):
                    continue
                relative_target = raw_target.split("#", 1)[0].split("?", 1)[0]
                if not relative_target:
                    continue
                resolved = (source.parent / relative_target).resolve()
                assert resolved.exists(), (
                    f"broken local docs link: {source.relative_to(REPO_ROOT)} "
                    f"-> {raw_target}"
                )


def assert_effect_interpreter_docs_are_canonical() -> None:
    public_lecture_url_fragments = (
        "6a01d501000000003700c5de",
        "6a02f388000000003502b2d6",
        "6a057524000000003701f6aa",
    )
    packet_doc = compact(read("docs/reference/effect-interpreter-packet.md"))
    for required in (
        "EffectRequest",
        "EffectInterpretation",
        "EffectObservation",
        "EffectNext",
        "EffectTurn",
        "Around Semantics",
        "execution_mode",
        "interpret_turn_result_packet",
    ):
        assert required in packet_doc, required

    rfc = compact(read("docs/architecture/rfcs/agent-loop-effect-interpreter-v0.md"))
    for required in (
        "Composition And Around Semantics",
        "Handler Is Data, Not a Callable",
        "General Effect-Program Abstraction",
        "M6: General Effect-Program Abstraction",
        "Milestone Status",
    ):
        assert required in rfc, required

    architecture = compact(read("docs/architecture.md"))
    assert "Control Plane As Effect Interpreter" in architecture

    lecture = compact(
        read("docs/development/control-plane-course/01-agent-loop-effectful-program.md")
    )
    assert "Around 是数据，不是回调" in lecture
    assert "CLI 是更高密度的 effect" in lecture
    for fragment in public_lecture_url_fragments:
        assert fragment in rfc, fragment
        assert fragment in lecture, fragment


def assert_contributor_task_board_is_current() -> None:
    tasks = compact(read("docs/development/contributor-tasks.md"))
    for required in (
        "The four canonical global manager CLI commands are shipped",
        "`/loop-goal-summary` remains host-only and outside this contributor slice",
        "A shared typed Effect Program drives quota, Turn, task-lease, and todo-completion settlement",
        "The scheduler remains outside settlement",
        "M7 parity fixtures plus a read-only journal inspection/`interpret_turn_journal` lens shipped",
        "do not extract a shared executor until two adapters share execution ownership",
    ):
        assert required in tasks, required
    for stale in (
        "Implement `/loopx-global-todos` or `/loopx-global-risks` next",
        "Implement `/loopx-global-risks` next",
        "Implement the remaining canonical `/loopx-global-risks` command",
        "global risks and goal summary stay host-only",
        "Add one negative fixture proving fail-closed legacy upgrade",
        "| GH-C82 |",
        "| GH-C59 |",
        "| GH-C61 |",
        "| GH-C83 |",
        "| GH-C84 |",
        "| GH-C92 |",
        "| GH-C93 |",
        "| GH-C49 |",
        "| GH-C60 |",
        "| GH-C62 |",
        "| GH-C64 |",
        "| GH-C71 |",
        "| GH-C74 |",
        "| GH-C75 |",
        "| GH-C76 |",
        "| GH-C80 |",
        "| GH-C85 |",
        "| GH-C95 |",
        "| GH-C97 |",
    ):
        assert stale not in tasks, stale


def assert_contributor_task_links_are_current() -> None:
    for path in (
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/SUPPORT.md",
        "docs/book/chapters/source-protocol-map.md",
        "docs/book/en/chapters/source-protocol-map.md",
        "docs/book/chapters/source-validation-to-pr.md",
        "docs/book/en/chapters/source-validation-to-pr.md",
    ):
        assert "docs/development/contributor-tasks.md" in read(path), path

    assert "/docs/development/contributor-tasks.md @huangruiteng" in read(
        ".github/CODEOWNERS"
    )
    for path in (
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/SUPPORT.md",
        ".github/CODEOWNERS",
    ):
        assert "main/CONTRIBUTOR_TASKS.md" not in read(path), path
        assert "../CONTRIBUTOR_TASKS.md" not in read(path), path
        assert "/CONTRIBUTOR_TASKS.md @" not in read(path), path


def assert_technical_direction_governance_is_current() -> None:
    direction = read("docs/project/technical-directions.md")
    direction_zh = read("docs/project/technical-directions.zh-CN.md")
    rfc_index = read("docs/architecture/rfcs/README.md")
    tasks = read("docs/development/contributor-tasks.md")
    issue_template = read(".github/ISSUE_TEMPLATE/contributor-task.yml")
    pr_template = read(".github/PULL_REQUEST_TEMPLATE.md")
    governance = read(".github/GOVERNANCE.md")

    for required in (
        "Long-Horizon Benchmarks and Evidence",
        "Operator Surface and IM Integration",
        "Shared Goal Authority and Cross-host Coordination",
        "Architecture and Research Incubator",
        "Stable Foundation: Control-Plane Reliability",
        "frontend-control-plane-im-prototype-rfc",
        "@maxliux5",
        "NoKV is an unpromoted optional provider candidate",
        "#3243",
        "#3244",
        "#3245",
        "#3246",
    ):
        assert required in direction, required

    for required in (
        "长程 Benchmark 与证据",
        "Operator Surface 与 IM Integration",
        "Shared Goal Authority 与跨 Host 协作",
        "架构与研究孵化器",
        "稳定基础：控制面可靠性",
        "frontend-control-plane-im-prototype-rfc",
        "@maxliux5",
        "NoKV 是位于 LoopX authority 之后",
        "#3243",
        "#3244",
        "#3245",
        "#3246",
    ):
        assert required in direction_zh, required

    for required in (
        "## Control-Plane Kernel, State, And Migration",
        "## Planning, Research, And Adaptive Intelligence",
        "## Runtime, Capability, And Collaboration Integration",
        "## Operator Experience And Observability",
        "## Benchmark And Reliability Engineering",
        "Current Technical Directions",
    ):
        assert required in rfc_index, required
    assert "## Status matrix" not in rfc_index

    # The task board routes to canonical direction/roadmap owners instead of
    # duplicating their mutable maturity table.
    for required in (
        "../project/technical-directions.md",
        "../architecture/rfcs/loopx-overall-roadmap-v0.md",
    ):
        assert required in tasks, required

    for content in (issue_template, pr_template):
        for required in (
            "Long-horizon benchmark evidence",
            "Operator surface and IM integration",
            "Shared Goal Authority and cross-host coordination",
            "Architecture and research incubator",
        ):
            assert required in content, required

    for required in (
        "## Technical Direction Governance",
        "direction/*",
        "does not override merged runtime and stable reference contracts",
    ):
        assert required in governance, required


def main() -> int:
    docs_index = read("docs/README.md")
    root_readme = read("README.md")
    root_readme_zh = read("README.zh-CN.md")
    governance = read(".github/GOVERNANCE.md")
    support = read(".github/SUPPORT.md")
    auto_research_command_path = read("demo/auto_research/README.md")
    codex_cli_tui_loop = read("docs/product/runtimes/codex-cli/codex-cli-tui-loop.md")
    project_agent_contract = read("docs/project-agent-todo-contract.md")
    status_contract = read("docs/status-data-contract.md")
    compact_auto_research_command_path = compact(auto_research_command_path)
    compact_codex_cli_tui_loop = compact(codex_cli_tui_loop)
    compact_project_agent_contract = compact(project_agent_contract)
    compact_status_contract = compact(status_contract)

    for retired_root_policy in (
        "GOVERNANCE.md",
        "MAINTAINERS.md",
        "SECURITY.md",
        "SUPPORT.md",
        "COMMUNICATIONS.md",
    ):
        assert not (REPO_ROOT / retired_root_policy).exists(), retired_root_policy
    for governance_contract in (
        "## Subsystem Maintainers",
        "### Lark Integration",
        "### Shared Host Integration Seams",
        "### Changing A Subsystem Appointment",
    ):
        assert governance_contract in governance, governance_contract
    for support_contract in (
        "## Choose A Channel",
        "## Official Publication Sources",
        "## Account Authenticity",
        "## Make A Useful Request",
    ):
        assert support_contract in support, support_contract

    for required in [
        "## Choose Your Path",
        "## Core References",
        "## Browse By Subject",
        "## Documentation Policy",
        "architecture/README.md",
        "concepts/README.md",
        "operations/README.md",
        "integrations/README.md",
        "product/README.md",
        "development/README.md",
        "reference/README.md",
        "showcases/README.md",
        "development/testing-and-quality.md",
        "project/technical-directions.md",
    ]:
        assert required in docs_index, required

    navigation_contracts = {
        "Use and Operate": [
            "docs/operations/README.md",
            "docs/quota-allocation.md",
            "docs/heartbeat-automation-prompt.md",
            "docs/status-data-contract.md",
        ],
        "Understand the Control Plane": [
            "docs/concepts/README.md",
            "docs/product/foundations/README.md",
            "docs/product/vision.md",
        ],
        "Integrate and Extend": [
            "docs/integration.md",
            "docs/integrations/README.md",
        ],
        "Build and Review LoopX": [
            "docs/development/README.md",
            "docs/reference/README.md",
            "docs/development/control-plane-course/README.md",
            "docs/development/testing-and-quality.md",
            "docs/public-private-boundary.md",
        ],
        "Inspect Outcomes": [
            "docs/showcases/README.md",
            "docs/research/README.md",
            "docs/update-notes/README.md",
        ],
        "Project and Community": [
            "docs/project/technical-directions.md",
            ".github/GOVERNANCE.md",
            "CONTRIBUTING.md",
            "docs/development/contributor-tasks.md",
            "docs/project/authors.md",
            "docs/project/history.md",
            "docs/project/trademarks.md",
            "docs/project/brand-guide.md",
            "ADOPTERS.md",
        ],
    }
    navigation_contracts_zh = {
        "使用与运维": navigation_contracts["Use and Operate"],
        "理解控制面": navigation_contracts["Understand the Control Plane"],
        "集成与扩展": navigation_contracts["Integrate and Extend"],
        "构建与评审 LoopX": navigation_contracts["Build and Review LoopX"],
        "查看结果与证据": navigation_contracts["Inspect Outcomes"],
        "项目与社区": [
            "docs/project/technical-directions.zh-CN.md",
            *navigation_contracts["Project and Community"][1:7],
            "docs/project/brand-guide.zh-CN.md",
            "ADOPTERS.md",
        ],
    }
    for readme, contracts in (
        (root_readme, navigation_contracts),
        (root_readme_zh, navigation_contracts_zh),
    ):
        for heading, required_links in contracts.items():
            section = subsection(readme, heading)
            for required in required_links:
                assert required in section, f"{heading}: {required}"

    assert "### Validate and Govern" not in root_readme
    assert "### 验证与治理" not in root_readme_zh
    advanced_docs = root_readme.split("## Advanced Documentation", 1)[1].split(
        "\n## ", 1
    )[0]
    advanced_docs_zh = root_readme_zh.split("## 进阶文档", 1)[1].split(
        "\n## ", 1
    )[0]
    for deep_link in [
        "benchmark/README.md",
        "deprecate/benchmark-legacy/README.md",
        "docs/product/foundations/project-level-reward-model.md",
        "loopx/capabilities/reward_memory/README.md",
        "loopx/capabilities/reward_memory/README.zh-CN.md",
    ]:
        assert deep_link not in advanced_docs
        assert deep_link not in advanced_docs_zh

    for path in [
        "docs/archive/README.md",
        "docs/archive/incidents/README.md",
        "docs/archive/release-readiness/README.md",
        "docs/architecture/README.md",
        "docs/architecture/rfcs/README.md",
        "docs/architecture/rfcs/agent-im-openviking-collaboration-v0.md",
        "docs/concepts/README.md",
        "docs/operations/README.md",
        "docs/product/README.md",
        "docs/product/release-note-template.md",
        "docs/product/foundations/README.md",
        "docs/product/migrations/README.md",
        "docs/product/roadmaps/README.md",
        "docs/product/runtimes/README.md",
        "docs/product/runtimes/codex-app/README.md",
        "docs/product/runtimes/codex-cli/README.md",
        "docs/product/surfaces/README.md",
        "docs/product/use-cases/README.md",
        "docs/development/README.md",
        "docs/development/documentation-layout.md",
        "docs/development/testing-and-quality.md",
        "docs/guides/README.md",
        "demo/auto_research/README.md",
        "docs/guides/multi-agent-product-recipe.md",
        "docs/integrations/README.md",
        "docs/reference/README.md",
        "docs/reference/contracts/README.md",
        "docs/reference/protocols/README.md",
        "docs/research/README.md",
        "docs/showcases/README.md",
        "docs/product/runtimes/codex-cli/codex-cli-tui-loop.md",
        "docs/project/technical-directions.md",
        "docs/project/technical-directions.zh-CN.md",
        "docs/project/brand-guide.md",
        "docs/project/brand-guide.zh-CN.md",
    ]:
        assert (REPO_ROOT / path).is_file(), path

    assert (REPO_ROOT / "ADOPTERS.md").is_file()

    developer_index = read("docs/development/README.md")
    quality_guide = read("docs/development/testing-and-quality.md")
    for required in [
        "testing-and-quality.md",
        "Model behavior qualification v0",
        "Benchmark research",
    ]:
        assert required in developer_index, required
    for required in [
        "Quality Layers",
        "Agent-Facing Output Budgets",
        "Decision Replay And Issue #2191",
        "Doubao Model-Behavior Gate",
        "Benchmark Research Evidence",
    ]:
        assert required in quality_guide, required

    root_markdown = {path.name for path in DOCS.glob("*.md")}
    assert root_markdown == ROOT_DOCS, sorted(root_markdown)

    product_root_markdown = {
        path.name for path in (DOCS / "product").glob("*.md")
    }
    assert product_root_markdown == PRODUCT_ROOT_DOCS, sorted(product_root_markdown)

    assert not (DOCS / "outreach").exists(), (
        "retired marketing and launch drafts must stay out of the active docs tree"
    )

    assert_local_doc_links_resolve()
    assert_hosted_docs_nav_parity()
    assert_effect_interpreter_docs_are_canonical()
    assert_contributor_task_board_is_current()
    assert_contributor_task_links_are_current()
    assert_technical_direction_governance_is_current()

    collaboration_rfc = read(
        "docs/architecture/rfcs/agent-im-openviking-collaboration-v0.md"
    )
    for forbidden in [
        "/Users/",
        ".local/research/",
        "source-synthesis.md",
        "minutes scopes",
        "目标群完整消息",
        "逐字稿",
    ]:
        assert forbidden not in collaboration_rfc, forbidden
    for required in [
        "The direct runtime-to-LoopX path is primary",
        "OpenViking receives scoped resources",
        "Public References",
    ]:
        assert required in collaboration_rfc, required

    for old_path, new_path in MOVED_PATHS.items():
        assert not (REPO_ROOT / old_path).exists(), old_path
        assert (REPO_ROOT / new_path).is_file(), new_path

    combined_public_indexes = "\n".join(
        [
            read("README.md"),
            read("docs/development/contributor-tasks.md"),
            read("docs/README.md"),
            read("docs/archive/README.md"),
            read("docs/product/README.md"),
            read("docs/product/runtimes/codex-cli/README.md"),
            read("docs/reference/README.md"),
            read("docs/reference/protocols/README.md"),
            read("docs/research/README.md"),
            read("benchmark/README.md"),
            read("deprecate/benchmark-legacy/README.md"),
            read("docs/showcases/README.md"),
        ]
    )
    for old_path in MOVED_PATHS:
        assert old_path not in combined_public_indexes, old_path
    for new_path in MOVED_PATHS.values():
        basename = Path(new_path).name
        assert (
            new_path in combined_public_indexes
            or basename in combined_public_indexes
            or new_path.startswith("docs/archive/")
            or new_path.startswith("deprecate/benchmark-legacy/")
        ), new_path

    for required in [
        "Do not append a follow-up goal-level `surface_only` sync",
        "--delivery-outcome outcome_progress",
    ]:
        assert required in compact_project_agent_contract, required

    for required in [
        "The best first-run experience is one TUI setup message",
        "Session-Attached Automation",
        "Headless Disabled Boundary",
    ]:
        assert required in compact_codex_cli_tui_loop, required

    for required in [
        "A later `surface_only` project-level sync will become the latest non-agent-lane run",
        "agent_lane_recommendation",
    ]:
        assert required in compact_status_contract, required

    for required in [
        "Start From A Clean Workspace",
        "loopx-auto-research-demo",
        "auto-research demo-e2e",
        "auto-research demo-supervisor",
        "auto-research worker-loop",
        "research-curator",
        "hypothesis-proposer",
        "research-executor",
        "evaluator-promoter",
        "tmux attach -t loopx-auto-research",
        "tmux kill-session -t loopx-auto-research",
        "not a leader agent",
    ]:
        assert required in compact_auto_research_command_path, required

    multi_agent_product_recipe = read("docs/guides/multi-agent-product-recipe.md")
    compact_multi_agent_product_recipe = compact(multi_agent_product_recipe)
    for required in [
        "Multi-Agent Product Recipe",
        "Product preset",
        "Multi-agent kernel",
        "role list",
        "agent scope",
        "worker-local skill snippet",
        "handoff/todo hints",
        "One-Command Launch",
        "Attach, Stop, Retry",
        "Auto-research should stay a reference preset, not the kernel",
    ]:
        assert required in compact_multi_agent_product_recipe, required

    check_rfc_language_mirrors()
    check_rfc_ledger_entries()
    print("docs-governance-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
