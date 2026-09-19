#!/usr/bin/env python3
"""Smoke-test the public showcase catalog and case pages."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "docs" / "showcases" / "showcase-catalog.json"
SHOWCASES = REPO_ROOT / "docs" / "showcases" / "README.md"
POC_FEEDBACK_LOOP = REPO_ROOT / "docs" / "showcases" / "poc-feedback-case-report-loop.md"
PRIVATE_MARKERS = tuple(
    "".join(parts)
    for parts in (
        ("lark", "office.com"),
        ("internal", "-api-drive"),
        ("bytedance.com", "/wiki"),
        ("/", "Users/"),
        (".codex", "/loopx"),
        ("BEGIN", " PRIVATE ", "KEY"),
        ("Author", "ization:"),
        ("to", "ken="),
        ("pass", "word="),
    )
)
PRIMARY_SHOWCASE_IDS = [
    "2026-07-cpp-accuracy-long-run",
    "2026-07-four-day-unattended-agent",
    "2026-07-public-engine-refactor",
    "2026-06-27-overnight-pr-batch",
    "2026-06-24-pr-issue-auto-fix",
    "2026-06-23-agent-to-agent-pr-comments",
    "2026-06-23-overnight-project-refactor",
    "2026-06-19-dynamic-workflow-hardware-agent",
    "2026-06-19-loopx-self-iteration",
    "2026-06-17-blocked-p0-safe-rotation",
]
CASE_TYPES = {
    "independent_user",
    "contributor_case",
    "creator_dogfooding",
    "reproducible_demo",
}
FORBIDDEN_SHOWCASE_COPY = (
    "故事" + "节奏",
    "Story " + "beats",
    "Website Story " + "Beats",
)
FORBIDDEN_TRIVIAL_METRIC_COPY = (
    "issue-fix 公开文件",
    "issue-fix public files",
    "smoke assertions",
    "storyboard panels",
    "source statuses",
    "coverage points",
    "render refs",
    "handoff 回归引用",
    "synthetic todos",
    "todo lifecycle smokes",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_public_safe(path: Path) -> None:
    text = read(path)
    for marker in PRIVATE_MARKERS:
        assert marker not in text, f"{path}: private marker {marker!r}"


def main() -> int:
    catalog = json.loads(read(CATALOG))
    assert catalog["schema_version"] == "loopx_showcase_catalog_v1", catalog
    cases = catalog.get("cases")
    assert isinstance(cases, list) and len(cases) >= 2, catalog

    case_ids = {case.get("id") for case in cases}
    assert "2026-06-17-blocked-p0-safe-rotation" in case_ids, case_ids
    assert "2026-06-19-dynamic-workflow-hardware-agent" in case_ids, case_ids
    assert "2026-06-19-loopx-self-iteration" in case_ids, case_ids
    frontstage_ids = [case.get("id") for case in cases if isinstance(case.get("frontend_card"), dict)]
    assert frontstage_ids[: len(PRIMARY_SHOWCASE_IDS)] == PRIMARY_SHOWCASE_IDS, frontstage_ids

    source_coverage = catalog.get("source_coverage")
    assert isinstance(source_coverage, list) and len(source_coverage) == 6, source_coverage
    source_keys = {row.get("source_key") for row in source_coverage}
    assert len(source_keys) == len(source_coverage), source_keys
    promoted = [row for row in source_coverage if row.get("disposition") == "promoted_case"]
    assert {row.get("case_id") for row in promoted} == set(PRIMARY_SHOWCASE_IDS[:3]), promoted

    assert_public_safe(CATALOG)
    assert_public_safe(SHOWCASES)
    assert_public_safe(POC_FEEDBACK_LOOP)

    for case in cases:
        case_id = str(case.get("id") or "")
        page = REPO_ROOT / str(case.get("case_page") or "")
        assert page.is_file(), case
        assert_public_safe(page)
        assert case.get("title"), case
        assert case.get("headline"), case
        assert case.get("evidence_boundary"), case
        assert case.get("user_value"), case
        assert case.get("case_type") in CASE_TYPES, case
        assert case.get("evidence_strength"), case
        assert isinstance(case.get("pattern_tags"), list) and case["pattern_tags"], case
        evidence_metrics = case.get("evidence_metrics")
        assert isinstance(evidence_metrics, list) and len(evidence_metrics) >= 2, case
        for metric in evidence_metrics:
            assert metric.get("value"), case
            labels = metric.get("labels")
            assert isinstance(labels, dict), case
            assert labels.get("zh") and labels.get("en"), case
        evidence_assets = case.get("evidence_assets", [])
        assert isinstance(evidence_assets, list), case
        for asset in evidence_assets:
            assert isinstance(asset, dict), case
            asset_path = asset.get("path")
            assert isinstance(asset_path, str) and asset_path.startswith("docs/assets/showcases/"), asset
            assert not asset_path.startswith(("http://", "https://")), asset
            assert (REPO_ROOT / asset_path).is_file(), asset
            assert asset.get("alt") and asset.get("caption"), asset
        frontend = case.get("frontend_card")
        appendix = case.get("appendix_surface")
        assert isinstance(frontend, dict) or isinstance(appendix, dict), case
        if isinstance(frontend, dict):
            assert frontend.get("visual_metaphor"), case
            assert isinstance(frontend.get("story_beats"), list) and len(frontend["story_beats"]) >= 3, case
        else:
            assert appendix.get("reason"), case
            assert appendix.get("public_surface") == "appendix_only", case
        localized_pages = case.get("localized_pages")
        assert isinstance(localized_pages, dict), case
        for lang in ("zh", "en"):
            localized_page = localized_pages.get(lang)
            assert isinstance(localized_page, str), case
            assert localized_page.startswith("docs/showcases/"), case
            assert localized_page.endswith(".html"), case
            localized_path = REPO_ROOT / localized_page
            assert localized_path.is_file(), case
            assert_public_safe(localized_path)
            localized_text = read(localized_path)
            for phrase in FORBIDDEN_SHOWCASE_COPY:
                assert phrase not in localized_text, f"{localized_page}: forbidden copy {phrase!r}"
            for phrase in FORBIDDEN_TRIVIAL_METRIC_COPY:
                assert phrase not in localized_text, f"{localized_page}: trivial metric copy {phrase!r}"
            if case_id != "2026-06-19-dynamic-workflow-hardware-agent" or lang == "en":
                assert "Repository evidence" in localized_text or "仓库证据" in localized_text, localized_page
                assert "Repository sources" in localized_text or "仓库来源" in localized_text, localized_page
                localized_case = case.get("localizations", {}).get(lang, {})
                expected_boundary = localized_case.get("evidence_boundary", case.get("evidence_boundary"))
                assert str(expected_boundary) in localized_text, localized_page
            if case.get("page_assets") == "shared":
                table = case.get("showcase_table", {})
                localized_table = case.get("localizations", {}).get(lang, {})
                expected_proof = localized_table.get("proof_point", table.get("proof_point"))
                expected_intervention = localized_table.get(
                    "loopx_intervention", table.get("loopx_intervention")
                )
                assert expected_proof and str(expected_proof) in localized_text, localized_page
                assert expected_intervention and str(expected_intervention) in localized_text, localized_page
                assert 'href="../showcase-page.css"' in localized_text, localized_page

        demo_command = case.get("demo_command")
        if case.get("status") == "reproducible_synthetic_demo":
            assert isinstance(demo_command, str) and demo_command.startswith("python3 examples/"), case
            demo_path = REPO_ROOT / demo_command.split(" ", 2)[1]
            assert demo_path.is_file(), case
        if case.get("status") == "redacted_stub_pending_contributor_details":
            assert demo_command is None, case
            page_text = read(page)
            assert "public-safe stub" in page_text, case_id
            assert "No reproducible public demo is included yet" in page_text, case_id
        if case.get("status") == "public_safe_interactive_case":
            assert demo_command is None, case
            interactive_page = case.get("interactive_page")
            assert isinstance(interactive_page, str), case
            assert interactive_page.startswith("docs/showcases/"), case
            assert interactive_page.endswith(".html"), case
            interactive_path = REPO_ROOT / interactive_page
            assert interactive_path.is_file(), case
            assert_public_safe(interactive_path)
            interactive_text = read(interactive_path)
            for phrase in (
                "loopx 在芯片开发任务上的实践",
                "Claude Code",
                "DUDU",
                "CV32E40P",
                "VeeR EH1",
                "Viterbi",
            ):
                assert phrase in interactive_text, phrase
            page_text = read(page)
            assert interactive_page.split("/")[-1] in page_text, case_id
            assert "Public Artifact" in page_text, case_id
        if case.get("status") == "public_safe_case_spec":
            assert demo_command is None, case
            assert isinstance(appendix, dict), case
            storyboard = case.get("storyboard_path")
            assert isinstance(storyboard, str) and storyboard.startswith("docs/showcases/"), case
            storyboard_path = REPO_ROOT / storyboard
            assert storyboard_path.is_file(), case
            assert_public_safe(storyboard_path)
            feedback_contract = case.get("feedback_contract_path")
            if feedback_contract is not None:
                assert isinstance(feedback_contract, str) and feedback_contract.startswith("docs/showcases/"), case
                feedback_contract_path = REPO_ROOT / feedback_contract
                assert feedback_contract_path.is_file(), case
                assert_public_safe(feedback_contract_path)
        if case_id == "2026-06-19-loopx-self-iteration":
            assert case.get("status") == "public_evidence_case", case
            assert demo_command is None, case
            workload = case.get("workload_signal")
            assert isinstance(workload, dict), case
            assert workload.get("anchor_commit") == "86d6d9d", workload
            assert workload.get("scope") == "whole_public_repository", workload
            whole_repository = workload.get("whole_repository")
            assert isinstance(whole_repository, dict), workload
            assert whole_repository.get("commit_count", 0) >= 800, whole_repository
            assert whole_repository.get("files_touched", 0) >= 570, whole_repository
            assert whole_repository.get("insertions", 0) >= 260000, whole_repository
            assert whole_repository.get("deletions", 0) >= 40000, whole_repository
            recent_window = workload.get("recent_window")
            assert isinstance(recent_window, dict), workload
            assert recent_window.get("since") == "2026-06-18T00:00:00+08:00", recent_window
            assert recent_window.get("commit_count", 0) >= 240, recent_window
            assert recent_window.get("files_touched", 0) >= 210, recent_window
            public_window = workload.get("public_window")
            assert isinstance(public_window, dict), workload
            assert public_window.get("calendar_days", 0) >= 19, public_window
            assert public_window.get("active_commit_days", 0) >= 16, public_window
            efficiency = workload.get("efficiency_model")
            assert isinstance(efficiency, dict), workload
            assert efficiency.get("baseline") == "AI-coding-assisted product process", efficiency
            estimated_days = efficiency.get("estimated_developer_days")
            assert isinstance(estimated_days, dict), efficiency
            assert estimated_days.get("low", 0) >= 50, estimated_days
            assert estimated_days.get("high", 0) >= estimated_days.get("low", 0), estimated_days
            assert "peer_claim_scope" in case.get("pattern_tags", []), case
            assert "efficiency_evidence_model" in case.get("pattern_tags", []), case
            page_text = read(page)
            for phrase in (
                "LoopX was used to improve a fast-moving LoopX repository",
                "The public repository history shows a connected long-horizon feature chain",
                "Efficiency Evidence Model",
                "The baseline below already assumes competent AI coding help",
                "59-92 developer-days",
                "3.0x-4.7x calendar",
                "Benchmark and adapter maturation",
                "Control-plane correctness",
                "Planning and dreaming lanes",
                "--self-merged --evidence",
                "The workload signal is the whole public repository through fixed anchor commit",
                "completion evidence recorded self-merge and validation outcomes",
            ):
                assert phrase in page_text, phrase

    docs_index = read(REPO_ROOT / "docs" / "README.md")
    repo_readme = read(REPO_ROOT / "README.md")
    showcase_index = read(SHOWCASES)
    feedback_loop = read(POC_FEEDBACK_LOOP)
    assert "showcases/README.md" in docs_index, "docs index must link showcases"
    assert "docs/showcases/README.md" in repo_readme, "README must link showcases"
    assert "poc-feedback-case-report-loop.md" in showcase_index, "showcase index must link PoC feedback loop"
    for phrase in (
        "GitHub Issues or Discussions as the primary public entry",
        "Case Report Shape",
        "Evidence Checklist",
        "only catalog-backed, public-safe cases become public cards",
        "private local status or unreviewed anecdotes",
    ):
        assert phrase in feedback_loop, phrase
    for phrase in (
        "Loop engineering for long-horizon AI agents and peer agent teams.",
        "The open, provider-neutral, stateful control plane for long-horizon agents.",
        "https://loopx-project.github.io/loopx/",
        "## Advanced Paths",
        "docs/assets/long-running-loop-openviking-trajectory.png",
        "docs/assets/long-running-loop-ml-experiment-trajectory.png",
        "### Presets and Auto Research",
        "### Review Agent Work",
        "## Evidence",
        "### Used In Real Projects",
        "docs/showcases/cases/independent-cpp-accuracy-long-run.md",
        "docs/showcases/cases/independent-four-day-unattended-agent.md",
        "docs/showcases/cases/independent-public-engine-refactor.md",
        "complete Showcase catalog",
    ):
        assert phrase in repo_readme, phrase
    featured_section = repo_readme.split("### Used In Real Projects", 1)[1].split("More inspectable surfaces:", 1)[0]
    assert featured_section.count("- **Independent user") == 3, featured_section
    assert "user-feedback-coverage.md" in showcase_index, showcase_index
    hosted_frontstage = "https://loopx-project.github.io/loopx/frontstage/"
    assert hosted_frontstage not in repo_readme, (
        "README must promote the public homepage instead of hosted frontstage"
    )
    assert hosted_frontstage not in showcase_index, (
        "showcase index must not promote hosted frontstage"
    )

    print("showcase-catalog-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
