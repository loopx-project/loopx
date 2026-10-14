#!/usr/bin/env python3
"""Smoke-test the catalog-driven showcase frontstage prototype."""

from __future__ import annotations

import json
import html as html_module
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "docs" / "showcases" / "showcase-catalog.json"
PROTOTYPE = REPO_ROOT / "examples" / "showcase-frontstage-prototype.py"
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
    "2026-10-vllm-long-context-optimization",
    "2026-06-27-overnight-pr-batch",
    "2026-06-24-pr-issue-auto-fix",
    "2026-06-23-agent-to-agent-pr-comments",
    "2026-06-23-overnight-project-refactor",
    "2026-06-19-dynamic-workflow-hardware-agent",
    "2026-06-19-loopx-self-iteration",
    "2026-06-17-blocked-p0-safe-rotation",
    "2026-09-dsh-loopx-replan",
]


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    cases = catalog["cases"]
    frontstage_cases = [case for case in cases if isinstance(case.get("frontend_card"), dict)]
    appendix_cases = [case for case in cases if isinstance(case.get("appendix_surface"), dict)]
    with tempfile.TemporaryDirectory(prefix="loopx-showcase-frontstage-") as tmp:
        output = Path(tmp) / "frontstage.html"
        result = subprocess.run(
            [sys.executable, str(PROTOTYPE), "--output", str(output)],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        assert str(output) in result.stdout, result.stdout
        html = output.read_text(encoding="utf-8")
        unescaped_html = html_module.unescape(html)

    for marker in PRIVATE_MARKERS:
        assert marker not in html, marker
    forbidden_display_terms = (
        "Story " + "beats",
        "Website " + "Story " + "Beats",
        "故事" + "节奏",
    )
    for marker in forbidden_display_terms:
        assert marker not in html, marker

    for required in (
        '<section class="hero">',
        '<p class="punchline">',
        'aria-label="Executor loop versus control plane"',
        'aria-label="Showcase filters"',
        'data-status-filter="all"',
        'data-case-search',
        'data-visible-count',
        "Efficiency Evidence Model",
        "Baseline vs actual public window",
        "AI-assisted baseline",
        "two-person lens",
        "docs/showcases/showcase-catalog.json",
        "control-plane-board.svg",
        str(catalog["schema_version"]),
    ):
        assert required in html, required

    assert [case["id"] for case in frontstage_cases] == PRIMARY_SHOWCASE_IDS, frontstage_cases
    assert any(case["id"] == "2026-06-20-creator-operator-case-spec" for case in appendix_cases), appendix_cases

    statuses = {str(case["status"]) for case in frontstage_cases}
    for status in statuses:
        assert f'data-status-filter="{status}"' in html, status

    for case in appendix_cases:
        assert f'data-case-id="{case["id"]}"' not in html, case["id"]

    for case in frontstage_cases:
        case_id = str(case["id"])
        assert f'data-case-id="{case_id}"' in html, case_id
        assert f'data-status="{case["status"]}"' in html, case_id
        assert f'data-domain="{case["domain"]}"' in html, case_id
        for field in ("title", "headline", "user_value", "evidence_boundary"):
            assert str(case[field]) in unescaped_html, (case_id, field)
            assert str(case[field]).lower() in unescaped_html.lower(), (case_id, field, "search")
        assert str(case["case_page"]).replace("docs/showcases/", "") in html or str(case["case_page"]) in html
        storyboard_path = case.get("storyboard_path")
        if isinstance(storyboard_path, str) and storyboard_path:
            assert storyboard_path.replace("docs/showcases/", "") in html or storyboard_path in html
        feedback_contract_path = case.get("feedback_contract_path")
        if isinstance(feedback_contract_path, str) and feedback_contract_path:
            assert feedback_contract_path.replace("docs/showcases/", "") in html or feedback_contract_path in html
        for tag in case.get("pattern_tags", []):
            assert str(tag) in html, (case_id, tag)
        frontend = case["frontend_card"]
        for beat in frontend.get("story_beats", []):
            assert str(beat) in html, (case_id, beat)
        workload = case.get("workload_signal")
        if isinstance(workload, dict) and isinstance(workload.get("efficiency_model"), dict):
            model = workload["efficiency_model"]
            whole = workload.get("whole_repository")
            public_window = workload.get("public_window")
            assert isinstance(whole, dict), case_id
            assert isinstance(public_window, dict), case_id
            assert str(whole["commit_count"]) in html, case_id
            assert f'{public_window["calendar_days"]}d' in html, case_id
            baseline = model["estimated_developer_days"]
            single = model["single_engineer_calendar_compression"]
            team = model["two_person_team_calendar_compression"]
            assert f'{baseline["low"]}-{baseline["high"]}d' in html, case_id
            assert f'{single["low"]}-{single["high"]}x' in html, case_id
            assert f'{team["low"]}-{team["high"]}x' in html, case_id
            assert str(model["claim_boundary"]) in html, case_id

    print("showcase-frontstage-prototype-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
