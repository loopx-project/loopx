#!/usr/bin/env python3
"""Smoke-test the public-safe `loopx pr-review` command."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import loopx.pr_review as pr_review_module  # noqa: E402
from loopx.capabilities.project_skill_delivery.core import (  # noqa: E402
    discover_project_scoped_skill_ids,
)
from loopx.doctor import REQUIRED_INSTALLED_SKILL_PHRASES  # noqa: E402
from loopx.pr_review import (  # noqa: E402
    _github_search_date,
    build_pr_review_packet,
    load_pr_fixture,
)
from loopx.skill_install_readback import PACKAGED_HOST_SKILL_IDS  # noqa: E402

FIXTURE = REPO_ROOT / "examples" / "fixtures" / "pr-review.public.json"
PR_REVIEW_SKILL = REPO_ROOT / "skills" / "loopx-pr-review" / "SKILL.md"
PR_MERGE_SKILL_DIR = REPO_ROOT / "skills" / "loopx-pr-merge"
PR_MERGE_SKILL = REPO_ROOT / "skills" / "loopx-pr-merge" / "SKILL.md"
PRIVATE_PATTERNS = [
    re.compile(r"/" + r"Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/" + r"private/"),
    re.compile(r"/tmp/"),
    re.compile(r"/var/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
]


def run_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def assert_public_safe(payload: dict[str, object]) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(text):
            raise AssertionError(
                f"pr-review payload leaked private pattern {pattern.pattern!r}"
            )


# `--check-merge-readiness` now mandates a Goal id. Every authoritative
# invocation an agent may read has to carry it, or the canonical self-merge
# gate fails deterministically before it can record its observation.
MERGE_READINESS_GUIDANCE_PATHS = (
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "loopx" / "capabilities" / "pr_review_queue" / "README.md",
    REPO_ROOT / "loopx" / "capabilities" / "pr_review_queue" / "catalog_entry.py",
    PR_REVIEW_SKILL,
    PR_MERGE_SKILL,
)


def assert_merge_readiness_invocations_require_goal_id() -> None:
    for path in MERGE_READINESS_GUIDANCE_PATHS:
        source = path.read_text(encoding="utf-8")
        for span in re.findall(r"`[^`]*--check-merge-readiness[^`]*`", source):
            assert "--goal-id" in span, (
                f"{path.name} must pass --goal-id to --check-merge-readiness: {span}"
            )
        for line in source.splitlines():
            if "--check-merge-readiness" in line and "`" not in line:
                assert "--goal-id" in line, (
                    f"{path.name} must pass --goal-id to --check-merge-readiness: "
                    f"{line.strip()}"
                )


def main() -> int:
    skill_source = PR_REVIEW_SKILL.read_text(encoding="utf-8")
    skill_text = " ".join(skill_source.split())
    for phrase in (
        "This skill is a thin host adapter",
        "keeps ordinary queue discovery open-only",
        "explicit `--state merged|all`",
        "agent_response_contract.review_execution_contract",
        "pull_requests[review_action_kind!=null].review_plan",
        "pull_requests[review_action_kind!=null].review_template",
        "pull_requests[review_action_kind!=null].evidence_commands",
        "Apply `completion_gate` literally",
        "Re-read the remote head immediately before verdict and publication",
        "formal `REQUEST_CHANGES`",
        "Read the published review back",
        "Route approval, merge, self-merge, and admin bypass to `loopx-pr-merge`",
        "--check-merge-readiness NUMBER@HEAD_OID",
        "admin bypass never overrides this gate",
        "Full PR Review And Bilingual Format",
        "findings-only or blocker-only body is incomplete",
        "Cover every changed surface and key symbols",
        "详细中文评审",
        "英文简短结论",
        "complete Chinese five-block review plus one concise English verdict",
        "review_execution_contract.policy_revision",
        "review_policy_revision",
        "Do not retain expired temporary worktree overrides",
        "Treat `candidate` as a preview, not a durable projection",
        "durable Todo target-key readback -> `--projected-exact-head` -> exact-head review/comment readback -> `--handled-exact-head`",
        "Never send the projection ACK before the Todo exists",
        "Generic `re-review`, `重新review`, and `复审` wording selects the named PR; it is not a force-refresh token.",
        "the row stays in `pull_requests` inventory but must not appear in `review_sequence`",
        "--target-exact-head NUMBER@HEAD_OID",
    ):
        assert phrase in skill_text, phrase
    assert len(skill_source.splitlines()) <= 180, len(skill_source.splitlines())
    for duplicated_contract_heading in (
        "Per-PR Evidence And Depth Gate",
        "Motivation Causal Chain",
        "Implementation Execution Chain",
        "Code Volume And Simplification Review",
        "Scope-Fit And Active Call-Site Gate",
        "Change Proportionality Gate",
    ):
        assert duplicated_contract_heading not in skill_source, (
            duplicated_contract_heading
        )
    # The skill must track the capability's revision, not pin a copy of it:
    # a literal here goes stale on the next capability bump and blocks
    # reviewers with a mismatch that has nothing to do with their change.
    assert not re.search(r"policy_revision\s*==\s*[0-9]+", skill_source), (
        "skill must not pin a literal review policy revision"
    )

    # The merge-decision workflow must require the capability-owned review
    # evidence for the exact head, and must stay out of the default installed
    # skill set so it cannot disturb hosts that never merge LoopX pull requests.
    assert PR_MERGE_SKILL.is_file(), PR_MERGE_SKILL
    assert "loopx-pr-merge" not in PACKAGED_HOST_SKILL_IDS
    # Repo-kept means repo-only: no scope marker, so no delivery path can copy
    # this workflow onto a host that did not deliberately adopt it, and the
    # wheel does not ship it either.
    assert not (PR_MERGE_SKILL_DIR / ".loopx-skill-scope").exists()
    assert "loopx-pr-merge" not in discover_project_scoped_skill_ids(
        REPO_ROOT / "skills"
    )
    assert "loopx-pr-merge" not in REQUIRED_INSTALLED_SKILL_PHRASES
    packaged_files = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["setuptools"]["data-files"]
    assert not [
        path
        for paths in packaged_files.values()
        for path in paths
        if path.startswith("skills/loopx-pr-merge/")
    ], "the repo-kept merge workflow must stay out of the packaged wheel"
    merge_text = " ".join(PR_MERGE_SKILL.read_text(encoding="utf-8").split())
    for phrase in (
        "Optional maintainer workflow",
        "stays outside the default installed skill set",
        "It lives in the repository",
        "loopx --format json pr-review --state all",
        "agent_response_contract.review_execution_contract",
        "--check-merge-readiness NUMBER@HEAD_OID",
        "ready=true",
        "completion_gate",
        "admin bypass never overrides this gate",
        "A merge decision without this evidence is not authorized",
    ):
        assert phrase in merge_text, phrase
    assert_merge_readiness_invocations_require_goal_id()

    assert _github_search_date("2026-06-28T00:00:00+08:00") == "2026-06-27"
    assert _github_search_date("2026-06-28T00:00:00Z") == "2026-06-28"
    calls: list[list[str]] = []

    def fake_run_gh_json(args: list[str], *, cwd: Path | None = None) -> object:
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return {}
        assert args[:2] == ["pr", "list"], calls
        assert "--search" in args and "updated:>=2026-06-27" in args, args
        state = args[args.index("--state") + 1]
        if state == "open":
            return [
                {
                    "number": 900,
                    "title": "Runtime review fixture",
                    "url": "https://github.com/owner/repo/pull/900",
                    "state": "OPEN",
                    "updatedAt": "2026-06-28T00:01:00Z",
                    "files": [
                        {"path": "src/status.py", "additions": 4, "deletions": 1}
                    ],
                    "changedFiles": 1,
                    "additions": 4,
                    "deletions": 1,
                    "statusCheckRollup": [],
                }
            ]
        if state == "closed":
            return [
                {
                    "number": 901,
                    "title": "Merged review fixture",
                    "url": "https://github.com/owner/repo/pull/901",
                    "state": "MERGED",
                    "updatedAt": "2026-06-27T23:59:00Z",
                    "closedAt": "2026-06-28T00:03:00Z",
                    "mergedAt": "2026-06-28T00:03:00Z",
                    "files": [
                        {"path": "docs/review.md", "additions": 2, "deletions": 0}
                    ],
                    "changedFiles": 1,
                    "additions": 2,
                    "deletions": 0,
                    "statusCheckRollup": [],
                }
            ]
        raise AssertionError(args)

    original_run_gh_json = pr_review_module._run_gh_json
    try:
        pr_review_module._run_gh_json = fake_run_gh_json
        fetched = pr_review_module.fetch_github_pull_requests(
            repo="owner/repo",
            limit=10,
            state_filter="all",
            since="2026-06-28T00:00:00+08:00",
        )
    finally:
        pr_review_module._run_gh_json = original_run_gh_json
    list_calls = [args for args in calls if args[:2] == ["pr", "list"]]
    assert len(list_calls) == 2, calls
    assert [args[args.index("--state") + 1] for args in list_calls] == [
        "open",
        "closed",
    ], calls
    assert fetched[0]["number"] == 900, fetched
    assert fetched[0]["files"][0]["path"] == "src/status.py", fetched
    assert fetched[1]["number"] == 901, fetched
    assert fetched[1]["state"] == "MERGED", fetched

    payload = json.loads(
        run_cli(
            "--format", "json", "pr-review", "--fixture", str(FIXTURE),
            "--state", "all", "--limit", "5"
        ).stdout
    )
    assert payload["schema_version"] == "loopx_pr_review_command_response_v0", payload
    request = payload["request"]
    assert request["command"] == "/loopx-pr-review", request
    assert (
        request["cli_command"]
        == "loopx pr-review [--repo owner/repo] [--target-exact-head NUMBER@HEAD_OID] [--state open|merged|all] [--review-priority other-developers-first|owner-first] [--since ISO]"
    ), request
    assert request["privacy_mode"] == "public_safe_github_metadata", request
    assert request["dry_run"] is True, request
    assert request["repository"] == "owner/repo", request
    assert request["state_filter"] == "all", request
    assert request["review_priority"] == "other-developers-first", request
    assert "result_completeness" in request["include"], request
    assert "scheduling_policy" in request["include"], request
    assert payload["result_completeness"]["complete"] is True, payload
    scheduling_policy = payload["scheduling_policy"]
    assert (
        scheduling_policy["schema_version"]
        == "pull_request_review_scheduling_policy_v1"
    ), scheduling_policy
    assert [item["id"] for item in scheduling_policy["ordered_tiers"][:3]] == [
        "other_developer_feedback_and_aged_backlog",
        "other_developer_remaining",
        "authenticated_developer_owned",
    ], scheduling_policy
    assert "one-off author filters" in scheduling_policy["manual_override_rule"]
    assert payload["summary"]["total_pr_count"] == 4, payload["summary"]
    assert payload["summary"]["open_pr_count"] == 3, payload["summary"]
    assert payload["summary"]["merged_pr_count"] == 1, payload["summary"]
    default_open = json.loads(
        run_cli(
            "--format", "json", "pr-review", "--fixture", str(FIXTURE), "--limit", "5"
        ).stdout
    )
    assert default_open["request"]["state_filter"] == "open", default_open["request"]
    assert default_open["summary"]["total_pr_count"] == 3, default_open["summary"]
    assert default_open["summary"]["merged_pr_count"] == 0, default_open["summary"]
    target = next(item for item in payload["pull_requests"] if item["number"] == 770)
    exact_target = f"{target['number']}@{target['head_oid']}"
    targeted = json.loads(
        run_cli(
            "--format", "json", "pr-review", "--fixture", str(FIXTURE),
            "--target-exact-head", exact_target,
        ).stdout
    )
    assert targeted["request"]["target_exact_heads"] == [exact_target], targeted
    assert targeted["request"]["state_filter"] == "all", targeted["request"]
    assert targeted["result_completeness"]["complete"] is True, targeted
    assert targeted["result_completeness"]["limit_scope"] == "exact_targets", targeted
    assert [item["number"] for item in targeted["pull_requests"]] == [target["number"]]
    assert payload["summary"]["post_merge_review_count"] == 1, payload["summary"]
    assert payload["summary"]["review_attention_count"] == 3, payload["summary"]
    assert payload["summary"]["draft_count"] == 1, payload["summary"]
    groups = payload["review_groups"]
    assert groups["unmerged"]["group_id"] == "unmerged", groups
    assert groups["merged"]["group_id"] == "merged", groups
    assert groups["unmerged"]["count"] == 3, groups
    assert groups["merged"]["count"] == 1, groups
    assert groups["merged"]["complete"] is True, groups
    assert 770 not in groups["unmerged"]["pr_numbers"], groups
    assert groups["merged"]["pr_numbers"] == [770], groups
    assert groups["unmerged"]["review_sequence"][0]["number"] == 771, groups
    assert groups["merged"]["review_sequence"][0]["number"] == 770, groups
    assert groups["unmerged"]["actionable_count"] == 2, groups
    assert groups["unmerged"]["no_action_count"] == 1, groups
    assert groups["merged"]["actionable_count"] == 1, groups
    assert groups["merged"]["no_action_count"] == 0, groups
    sequence = payload["review_sequence"]
    assert all(item["review_action_kind"] is not None for item in sequence), sequence
    for item in payload["pull_requests"]:
        if item["review_action_kind"] is None:
            assert item["review_plan"] is None, item
            assert item["review_template"] is None, item
            assert item["evidence_commands"] == [], item
        else:
            assert item["review_plan"], item
            assert item["review_template"], item
            assert item["evidence_commands"], item
    assert sequence[0]["number"] == 771, sequence
    assert any(item["number"] == 775 for item in payload["pull_requests"]), payload[
        "pull_requests"
    ]
    assert all(item["number"] != 775 for item in sequence), sequence
    assert any(
        item["number"] == 770 and item["state"] == "MERGED" for item in sequence
    ), sequence

    merge_head = "e" * 40
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_root = Path(temp_dir) / "runtime"
        registry_path = Path(temp_dir) / "registry.json"
        registry_path.write_text(
            json.dumps({"goals": [{"id": "test-goal", "repo": temp_dir}]}),
            encoding="utf-8",
        )
        merge_fixture_path = Path(temp_dir) / "merge-readiness.json"
        merge_fixture = {
            "repository": "owner/repo",
            "pull_requests": [
                {
                    "number": 4110,
                    "title": "Exact-head merge gate fixture",
                    "url": "https://github.com/owner/repo/pull/4110",
                    "state": "OPEN",
                    "author": {"login": "contributor"},
                    "headRefOid": merge_head,
                    "baseRefName": "main",
                    "isDraft": False,
                    "reviewDecision": "APPROVED",
                    "mergeStateStatus": "CLEAN",
                    "files": [
                        {"path": "src/runtime.py", "additions": 1, "deletions": 1}
                    ],
                    "reviews": [
                        {
                            "state": "APPROVED",
                            "body": (
                                "## 动机\n动机。\n\n## 改动思路\n思路。\n\n"
                                "## 具体改动\n改动。\n\n## 对主干的风险\n风险。\n\n"
                                "## 我的整体评价\n通过。\n\n"
                                f"English verdict: APPROVE at exact head {merge_head}."
                            ),
                            "author": {"login": "maintainer"},
                            "commit": {"oid": merge_head},
                            "submittedAt": "2026-09-09T11:14:01Z",
                        }
                    ],
                    "statusCheckRollup": [
                        {
                            "name": "Sign-off",
                            "status": "COMPLETED",
                            "conclusion": "SUCCESS",
                        },
                        {
                            "name": "merge-gate",
                            "status": "COMPLETED",
                            "conclusion": "SUCCESS",
                        },
                    ],
                    "review_thread_summary": {
                        "schema_version": "github_review_thread_summary_v0",
                        "complete": True,
                        "total_count": 0,
                        "unresolved_count": 0,
                    },
                }
            ],
        }
        merge_fixture_path.write_text(json.dumps(merge_fixture), encoding="utf-8")
        ready = json.loads(
            run_cli(
                "--runtime-root",
                str(runtime_root),
                "--registry",
                str(registry_path),
                "--format",
                "json",
                "pr-review",
                "--goal-id",
                "test-goal",
                "--fixture",
                str(merge_fixture_path),
                "--check-merge-readiness",
                f"4110@{merge_head}",
            ).stdout
        )
        assert ready["ready"] is True, ready
        assert ready["blocking_reasons"] == [], ready
        unchanged_queue = json.loads(
            run_cli(
                "--runtime-root",
                str(runtime_root),
                "--registry",
                str(registry_path),
                "--format",
                "json",
                "pr-review",
                "--goal-id",
                "test-goal",
                "--fixture",
                str(merge_fixture_path),
                "--state",
                "open",
            ).stdout
        )
        unchanged_item = unchanged_queue["pull_requests"][0]
        assert unchanged_item["review_action_kind"] is None, unchanged_item
        assert (
            unchanged_item["merge_readiness_observation"]["observation_state"]
            == "observed_unchanged"
        ), unchanged_item

        merge_fixture["pull_requests"][0]["reviews"][0]["body"] = merge_fixture[
            "pull_requests"
        ][0]["reviews"][0]["body"].replace(merge_head, "d" * 40)
        merge_fixture["pull_requests"][0]["statusCheckRollup"][0]["conclusion"] = (
            "FAILURE"
        )
        merge_fixture_path.write_text(json.dumps(merge_fixture), encoding="utf-8")
        blocked_run = run_cli(
            "--runtime-root",
            str(runtime_root),
            "--registry",
            str(registry_path),
            "--format",
            "json",
            "pr-review",
            "--goal-id",
            "test-goal",
            "--fixture",
            str(merge_fixture_path),
            "--check-merge-readiness",
            f"4110@{merge_head}",
            check=False,
        )
        assert blocked_run.returncode == 1, blocked_run
        blocked = json.loads(blocked_run.stdout)
        assert (
            "current_head_review_missing_or_invalid" in blocked["blocking_reasons"]
        ), blocked
        assert "status_checks_failed" in blocked["blocking_reasons"], blocked

    # Merge readiness follows the typed approval verdict, not GitHub's review
    # state. The platform blocks self-approval, so an author-owned approval is
    # stored as COMMENTED; a state-based rule counted these still-open heads as
    # concluded and hid approved heads that had gone behind, conflicted, lost
    # checks, or become blocked.
    approval_head = "f" * 40

    def approved_open_head(
        number: int,
        *,
        merge_state: str,
        review_state: str = "COMMENTED",
        verdict: str = "APPROVE",
    ) -> dict[str, object]:
        title = (
            "Approval conclusion (author-owned PR; GitHub blocks formal self-approval)"
            if verdict == "APPROVE"
            else "Request changes conclusion (author-owned PR; GitHub blocks formal self-review)"
        )
        return {
            "number": number,
            "title": f"Approved open head {number}",
            "url": f"https://github.com/owner/repo/pull/{number}",
            "state": "OPEN",
            "author": {"login": "maintainer"},
            "headRefOid": approval_head,
            "baseRefName": "main",
            "isDraft": False,
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": merge_state,
            "files": [{"path": "src/runtime.py", "additions": 1, "deletions": 1}],
            "reviews": [
                {
                    "state": review_state,
                    "body": (
                        f"{title}\n\n"
                        "## 动机\n动机。\n\n## 改动思路\n思路。\n\n"
                        "## 具体改动\n改动。\n\n## 对主干的风险\n风险。\n\n"
                        "## 我的整体评价\n通过。\n\n"
                        f"English verdict: {verdict} at exact head {approval_head}."
                    ),
                    "author": {"login": "maintainer"},
                    "commit": {"oid": approval_head},
                    "submittedAt": "2026-09-09T11:14:01Z",
                }
            ],
            "statusCheckRollup": [
                {"name": "Sign-off", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {
                    "name": "merge-gate",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS" if merge_state == "CLEAN" else "FAILURE",
                },
            ],
            "review_thread_summary": {
                "schema_version": "github_review_thread_summary_v0",
                "complete": True,
                "total_count": 0,
                "unresolved_count": 0,
            },
        }

    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_root = Path(temp_dir) / "runtime"
        registry_path = Path(temp_dir) / "registry.json"
        registry_path.write_text(
            json.dumps({"goals": [{"id": "test-goal", "repo": temp_dir}]}),
            encoding="utf-8",
        )
        approval_fixture_path = Path(temp_dir) / "approved-open-heads.json"
        approval_fixture = {
            "repository": "owner/repo",
            "reviewer_login": "maintainer",
            "pull_requests": [
                approved_open_head(4111, merge_state="BEHIND"),
                approved_open_head(4112, merge_state="CLEAN"),
                approved_open_head(
                    4113, merge_state="CLEAN", verdict="REQUEST_CHANGES"
                ),
            ],
        }
        approval_fixture_path.write_text(
            json.dumps(approval_fixture), encoding="utf-8"
        )
        approval_packet = json.loads(
            run_cli(
                "--format",
                "json",
                "pr-review",
                "--fixture",
                str(approval_fixture_path),
                "--state",
                "open",
            ).stdout
        )
        approvals = {
            item["number"]: item for item in approval_packet["pull_requests"]
        }
        for number in (4111, 4112):
            assert approvals[number]["review_conclusion"]["verdict"] == "APPROVE", approvals
            assert (
                approvals[number]["review_action_kind"]
                == "qualify_pull_request_merge_readiness"
            ), approvals[number]
        assert approvals[4111]["merge_state"] == "BEHIND", approvals[4111]
        assert approvals[4112]["merge_state"] == "CLEAN", approvals[4112]
        assert approvals[4113]["review_conclusion"]["verdict"] == "REQUEST_CHANGES", approvals
        assert approvals[4113]["review_action_kind"] is None, approvals[4113]
        assert approval_packet["summary"]["review_attention_count"] == 2, (
            approval_packet["summary"]
        )
        assert sorted(
            item["number"] for item in approval_packet["review_sequence"]
        ) == [4111, 4112], approval_packet["review_sequence"]
        for number, expected_blockers in (
            (4111, ("merge_state_requires_update", "status_checks_failed")),
            (4112, ()),
        ):
            readiness = json.loads(
                run_cli(
                    "--runtime-root",
                    str(runtime_root),
                    "--registry",
                    str(registry_path),
                    "--format",
                    "json",
                    "pr-review",
                    "--goal-id",
                    "test-goal",
                    "--fixture",
                    str(approval_fixture_path),
                    "--check-merge-readiness",
                    f"{number}@{approval_head}",
                    check=not expected_blockers,
                ).stdout
            )
            for blocker in expected_blockers:
                assert blocker in readiness["blocking_reasons"], readiness
            assert readiness["ready"] is (not expected_blockers), readiness
    assert sequence[0]["risk_hint_level"] == "medium", sequence[0]
    assert sequence[0]["main_risk_level"] == "medium", sequence[0]
    merged_sequence = next(item for item in sequence if item["number"] == 770)
    assert merged_sequence["risk_hint_level"] == "medium", merged_sequence
    assert merged_sequence["main_risk_level"] == "high", merged_sequence
    assert (
        merged_sequence["review_action_kind"]
        == "audit_merged_pull_request_exact_head"
    ), merged_sequence
    first = next(item for item in payload["pull_requests"] if item["number"] == 773)
    assert "newcomer command path" in first["motivation"], first
    template = first["review_template"]
    assert template["schema_version"] == "pr_review_five_block_template_v0", template
    assert "Empty scaffold only" in template["purpose"], template
    assert "review_execution_contract" in template["output_hint"], template
    labels = [section["label"] for section in template["sections"]]
    assert labels == ["动机", "改动思路", "具体改动", "对主干的风险", "我的整体评价"], (
        template
    )
    for section in template["sections"]:
        assert section["content"] == "", section
        assert section["word_hint"], section
        assert section["agent_instruction"], section
        assert "quota.py" not in section["agent_instruction"], section
    assert all("无最低字数" in section["word_hint"] for section in template["sections"])
    concrete_change = next(
        section for section in template["sections"] if section["label"] == "具体改动"
    )
    assert "### 关键代码讲解" in concrete_change["agent_instruction"], concrete_change
    assert (
        "2-5 behavior-bearing exact-head symbols"
        in concrete_change["agent_instruction"]
    ), concrete_change
    assert "headRefOid" in first["evidence_commands"][0], first["evidence_commands"]
    assert "headRefOid" in first["evidence_commands"][-1], first["evidence_commands"]
    assert template["review_order"][0] == "docs/guides/newcomer-command-path.md", (
        template
    )
    assert first["checks"]["counts"]["success"] == 2, first["checks"]
    assert "public_docs" in first["areas"], first["areas"]
    risk_hint = first["metadata_risk_hint"]
    assert risk_hint["schema_version"] == "pr_metadata_risk_hint_v0", risk_hint
    assert risk_hint["level"] == "low", risk_hint
    assert "Metadata-only" in risk_hint["disclaimer"], risk_hint
    assert "quota.py" not in json.dumps(risk_hint), risk_hint
    main_risk = first["main_regression_analysis"]
    assert main_risk["schema_version"] == "main_regression_analysis_v0", main_risk
    assert main_risk["risk_level"] == "low", main_risk
    assert main_risk["post_merge_review"] is False, main_risk
    assert main_risk["potential_regressions"], main_risk
    assert main_risk["bug_risks"], main_risk
    contract = payload.get("agent_response_contract", {}).get(
        "review_execution_contract", {}
    )
    scope_fit_reqs = [
        req
        for req in contract.get("evidence_requirements", [])
        if req.get("evidence_id") == "scope_fit"
    ]
    assert scope_fit_reqs, "scope_fit evidence requirement missing from contract"
    assert scope_fit_reqs[0]["required_when"] == "behavior_bearing_change", scope_fit_reqs
    proportionality_reqs = [
        req
        for req in contract.get("evidence_requirements", [])
        if req.get("evidence_id") == "change_proportionality"
    ]
    assert proportionality_reqs, (
        "change_proportionality evidence requirement missing from contract"
    )
    assert proportionality_reqs[0]["required_when"] == "code_change"
    assert proportionality_reqs[0]["verdict_values"] == [
        "proportionate",
        "disproportionate",
        "not_yet_proven",
    ]
    isolation_reqs = [
        req
        for req in contract.get("evidence_requirements", [])
        if req.get("evidence_id") == "default_off_isolation"
    ]
    assert isolation_reqs, "default_off_isolation evidence requirement missing"
    assert isolation_reqs[0]["required_when"] == "behavior_bearing_change"
    assert "paired_counterfactual_validation" in isolation_reqs[0]["fields"]
    authority_reqs = [
        req
        for req in contract.get("evidence_requirements", [])
        if req.get("evidence_id") == "authority_semantics"
    ]
    assert authority_reqs, "authority_semantics evidence requirement missing"
    assert authority_reqs[0]["required_when"] == "code_change"
    code_pr = next(
        (
            p
            for p in payload.get("pull_requests", [])
            if (p.get("review_plan") or {}).get("applicability", {}).get("code_change")
        ),
        None,
    )
    assert code_pr is not None, "fixture must include a code-change PR"
    assert "scope_fit" in code_pr["review_plan"]["required_evidence_ids"], code_pr[
        "review_plan"
    ]
    assert (
        "change_proportionality" in code_pr["review_plan"]["required_evidence_ids"]
    ), code_pr["review_plan"]
    assert (
        "default_off_isolation" in code_pr["review_plan"]["required_evidence_ids"]
    ), code_pr["review_plan"]
    assert (
        "authority_semantics" in code_pr["review_plan"]["required_evidence_ids"]
    ), code_pr["review_plan"]
    assert code_pr["review_plan"]["applicability"]["scope_fit_required"] is True, (
        code_pr["review_plan"]
    )
    reuse = code_pr["review_plan"]["result_template"]["evidence"]["repository_reuse"]
    assert reuse == {"status": "unverified"}, reuse
    assert code_pr["review_plan"]["applicability"]["repository_reuse_required"] is True
    assert (
        code_pr["review_plan"]["applicability"]["change_proportionality_required"]
        is True
    ), code_pr["review_plan"]
    assert (
        code_pr["review_plan"]["applicability"]["default_off_isolation_required"]
        is True
    ), code_pr["review_plan"]
    assert (
        code_pr["review_plan"]["applicability"]["authority_semantics_required"]
        is True
    ), code_pr["review_plan"]
    docs_pr = next(
        (
            p
            for p in payload.get("pull_requests", [])
            if p.get("review_plan")
            and not p["review_plan"].get("applicability", {}).get("code_change")
        ),
        None,
    )
    assert docs_pr is not None, "fixture must include a docs-only PR"
    assert docs_pr["review_plan"]["applicability"]["scope_fit_required"] is False
    assert "repository_reuse" not in docs_pr["review_plan"]["required_evidence_ids"]
    assert (
        docs_pr["review_plan"]["applicability"]["change_proportionality_required"]
        is False
    )
    assert (
        docs_pr["review_plan"]["applicability"]["default_off_isolation_required"]
        is False
    )
    assert (
        docs_pr["review_plan"]["applicability"]["authority_semantics_required"]
        is False
    )
    risk_section = next(
        section
        for section in template["sections"]
        if section["label"] == "对主干的风险"
    )
    assert "scope_fit" in risk_section["agent_instruction"], risk_section
    assert "change_proportionality" in risk_section["agent_instruction"], risk_section
    assert "default_off_isolation" in risk_section["agent_instruction"], risk_section
    assert "authority_semantics" in risk_section["agent_instruction"], risk_section

    default_payload = json.loads(
        run_cli("--format", "json", "pr-review", "--fixture", str(FIXTURE)).stdout
    )
    assert default_payload["request"]["limit"] == 100, default_payload["request"]
    assert "autonomous_review" not in default_payload, default_payload

    observed = json.loads(
        run_cli(
            "--format",
            "json",
            "pr-review",
            "--fixture",
            str(FIXTURE),
            "--state",
            "open",
            "--autonomous-observation",
        ).stdout
    )
    observation = observed["autonomous_review"]
    assert observed["request"]["autonomous_observation"] is True, observed["request"]
    assert "autonomous_review" in observed["request"]["include"], observed["request"]
    assert (
        observation["schema_version"] == "pull_request_review_queue_observation_v1"
    ), observation
    assert observation["observation_state"] == "material_transition", observation
    assert observation["candidate_count"] == 1, observation
    assert observation["candidate"]["number"] == 771, observation
    assert (
        observation["candidate"]["head_oid"]
        == "7710000000000000000000000000000000000000"
    ), observation
    assert observation["write_authority_granted"] is False, observation
    assert_public_safe(observed)

    with tempfile.TemporaryDirectory() as temp_dir:
        previous_path = Path(temp_dir) / "previous.json"
        previous_path.write_text(json.dumps(observed), encoding="utf-8")
        unchanged = json.loads(
            run_cli(
                "--format",
                "json",
                "pr-review",
                "--fixture",
                str(FIXTURE),
                "--state",
                "open",
                "--autonomous-observation",
                "--previous-observation-json",
                str(previous_path),
            ).stdout
        )
        acknowledged = json.loads(
            run_cli(
                "--format",
                "json",
                "pr-review",
                "--fixture",
                str(FIXTURE),
                "--state",
                "open",
                "--autonomous-observation",
                "--previous-observation-json",
                str(previous_path),
                "--projected-exact-head",
                "771@7710000000000000000000000000000000000000",
            ).stdout
        )
    assert unchanged["request"]["previous_observation_supplied"] is True, unchanged[
        "request"
    ]
    assert (
        unchanged["autonomous_review"]["observation_state"] == "observed_unchanged"
    ), unchanged
    assert unchanged["autonomous_review"]["candidate"]["number"] == 771, unchanged
    assert unchanged["autonomous_review"]["projected_candidate_count"] == 0, unchanged
    progressed_observation = acknowledged["autonomous_review"]
    assert progressed_observation["observation_state"] == "observed_unchanged", (
        acknowledged
    )
    assert progressed_observation["candidate"]["number"] == 773, acknowledged
    assert progressed_observation["projected_candidate_count"] == 1, acknowledged
    assert progressed_observation["handled_exact_head_count"] == 0, acknowledged

    with tempfile.TemporaryDirectory() as temp_dir:
        checkpoint_path = Path(temp_dir) / "monitor.json"
        checkpoint_first = json.loads(
            run_cli(
                "--format",
                "json",
                "pr-review",
                "--fixture",
                str(FIXTURE),
                "--state",
                "open",
                "--autonomous-observation",
                "--observation-state-file",
                str(checkpoint_path),
            ).stdout
        )
        assert checkpoint_first["autonomous_review"]["candidate"]["number"] == 771
        assert checkpoint_first["request"]["observation_state_file_supplied"] is True
        assert checkpoint_first["request"]["local_checkpoint_write_performed"] is True
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert (
            checkpoint["schema_version"] == "pull_request_review_monitor_checkpoint_v0"
        )
        assert checkpoint["repository"] == "owner/repo"

        checkpoint_second = json.loads(
            run_cli(
                "--format",
                "json",
                "pr-review",
                "--fixture",
                str(FIXTURE),
                "--state",
                "open",
                "--autonomous-observation",
                "--observation-state-file",
                str(checkpoint_path),
            ).stdout
        )
        assert checkpoint_second["autonomous_review"]["candidate"]["number"] == 771
        assert checkpoint_second["request"]["previous_observation_supplied"] is True

        checkpoint_projected = json.loads(
            run_cli(
                "--format",
                "json",
                "pr-review",
                "--fixture",
                str(FIXTURE),
                "--state",
                "open",
                "--autonomous-observation",
                "--observation-state-file",
                str(checkpoint_path),
                "--projected-exact-head",
                "771@7710000000000000000000000000000000000000",
            ).stdout
        )
        assert checkpoint_projected["autonomous_review"]["candidate"]["number"] == 773

        checkpoint_handled = json.loads(
            run_cli(
                "--format",
                "json",
                "pr-review",
                "--fixture",
                str(FIXTURE),
                "--state",
                "open",
                "--autonomous-observation",
                "--observation-state-file",
                str(checkpoint_path),
                "--handled-exact-head",
                "771@7710000000000000000000000000000000000000",
            ).stdout
        )
        assert checkpoint_handled["autonomous_review"]["handled_exact_head_count"] == 1

        checkpoint_reloaded = json.loads(
            run_cli(
                "--format",
                "json",
                "pr-review",
                "--fixture",
                str(FIXTURE),
                "--state",
                "open",
                "--autonomous-observation",
                "--observation-state-file",
                str(checkpoint_path),
            ).stdout
        )
        assert checkpoint_reloaded["autonomous_review"]["handled_exact_head_count"] == 1
        assert_public_safe(checkpoint_reloaded)

        invalid_checkpoint_path = Path(temp_dir) / "invalid.json"
        invalid_checkpoint_path.write_text("{}", encoding="utf-8")
        invalid_result = run_cli(
            "--format",
            "json",
            "pr-review",
            "--fixture",
            str(FIXTURE),
            "--state",
            "open",
            "--autonomous-observation",
            "--observation-state-file",
            str(invalid_checkpoint_path),
            check=False,
        )
        invalid_payload = json.loads(invalid_result.stdout)
        assert invalid_result.returncode == 1, invalid_result
        assert invalid_payload["ok"] is False, invalid_payload
        assert str(invalid_checkpoint_path) not in json.dumps(invalid_payload)

    incomplete_observation = json.loads(
        run_cli(
            "--format",
            "json",
            "pr-review",
            "--fixture",
            str(FIXTURE),
            "--state",
            "open",
            "--limit",
            "1",
            "--autonomous-observation",
        ).stdout
    )["autonomous_review"]
    assert incomplete_observation["observation_state"] == "not_observed", (
        incomplete_observation
    )
    assert incomplete_observation["candidate"] is None, incomplete_observation

    repository, fixture_prs, _fixture_reviewer = load_pr_fixture(FIXTURE)
    merged_fixture = next(item for item in fixture_prs if item.get("state") == "MERGED")
    busy_window = []
    for offset in range(105):
        item = dict(merged_fixture)
        item["number"] = 1000 + offset
        item["url"] = f"https://github.com/owner/repo/pull/{1000 + offset}"
        busy_window.append(item)
    truncated = build_pr_review_packet(
        pull_requests=busy_window,
        repository=repository,
        limit=100,
        source="fixture",
        state_filter="merged",
    )
    completeness = truncated["result_completeness"]
    assert completeness["complete"] is False, completeness
    assert completeness["groups"]["merged"] == {
        "complete": False,
        "observed_count": 105,
        "included_count": 100,
        "truncated": True,
    }, completeness
    assert completeness["recommended_limit"] >= 106, completeness
    assert truncated["review_groups"]["merged"]["truncated"] is True, truncated

    saturated_source = build_pr_review_packet(
        pull_requests=busy_window[:100],
        repository=repository,
        limit=100,
        source="github_cli",
        state_filter="merged",
        source_scan={
            "schema_version": "pr_review_source_scan_v0",
            "complete": False,
            "pull_requests": busy_window[:100],
            "states": [
                {
                    "state": "merged",
                    "fetch_limit": 100,
                    "fetched_count": 100,
                    "included_after_window": 100,
                    "source_saturated": True,
                    "source_read_valid": True,
                }
            ],
        },
    )
    saturated_completeness = saturated_source["result_completeness"]
    assert saturated_completeness["complete"] is False, saturated_completeness
    assert saturated_completeness["source_scan_complete"] is False, (
        saturated_completeness
    )
    assert saturated_completeness["observed_count_is_lower_bound"] is True, (
        saturated_completeness
    )
    assert "pull_requests" not in saturated_completeness["source_scan"], (
        saturated_completeness
    )
    assert saturated_completeness["recommended_limit"] == 200, saturated_completeness
    assert main_risk["verification_focus"], main_risk
    assert "quota.py" not in json.dumps(main_risk), main_risk
    response_contract = payload["agent_response_contract"]
    assert (
        response_contract["schema_version"] == "pr_review_agent_response_contract_v0"
    ), response_contract
    assert response_contract["table_only_response_allowed"] is False, response_contract
    assert response_contract["slash_prefix_dominates_intent"] is True, response_contract
    assert response_contract["stats_only_requires_explicit_opt_out"] is True, (
        response_contract
    )
    assert response_contract["queue_table_role"] == "preface_only", response_contract
    assert response_contract["selection_execution_contract"] == {
        "schema_version": "pr_review_selection_execution_contract_v0",
        "explicit_selection_scope": "ordering_only",
        "review_action_authority": "pull_requests[].review_action_kind",
        "review_sequence_membership": "review_action_kind_non_null_only",
        "no_action_inventory_location": "pull_requests",
        "generic_rereview_terms_force_fresh_audit": False,
        "no_action_behavior": "compact_exact_head_conclusion_readback_only",
        "no_action_execution_artifacts": "plan_and_template_null_commands_empty",
        "force_fresh_audit_requires": (
            "An explicit request to rerun evidence despite the unchanged/no-action "
            "exact head, or a concrete new concern or evidence invalidation, encoded "
            "as --fresh-audit-exact-head NUMBER@HEAD_OID."
        ),
    }, response_contract
    assert response_contract["required_packet_fields_to_preserve"] == [
        "agent_response_contract",
        "agent_response_contract.review_execution_contract",
        "result_completeness",
        "scheduling_policy",
        "review_groups",
        "pull_requests[review_action_kind!=null].review_plan",
        "pull_requests[review_action_kind!=null].review_template",
        "pull_requests[review_action_kind!=null].evidence_commands",
    ], response_contract
    assert response_contract["required_final_sections"] == [
        "动机",
        "改动思路",
        "具体改动",
        "对主干的风险",
        "我的整体评价",
    ], response_contract
    depth = response_contract["explanation_depth_contract"]
    assert depth["schema_version"] == "pr_review_explanation_depth_v0", depth
    assert depth["authority"] == "agent_response_contract.review_execution_contract", (
        depth
    )
    assert "may not know" in depth["reader_profile"], depth
    execution = response_contract["review_execution_contract"]
    assert execution["schema_version"] == "pull_request_review_execution_contract_v2", (
        execution
    )
    requirements = {
        item["evidence_id"]: item for item in execution["evidence_requirements"]
    }
    assert set(requirements) == {
        "problem_context",
        "architecture_flow",
        "repository_reuse",
        "observable_semantics",
        "changed_line_classification",
        "scope_fit",
        "symbol_map",
        "walkthroughs",
        "validation_matrix",
        "failure_analysis",
        "code_volume",
        "change_proportionality",
        "default_off_isolation",
        "authority_semantics",
        "typed_state_rule",
        "domain_neutrality",
        "behavior_change_disclosure",
        "guidance_vs_obligation",
        "durable_smoke_value",
        "semantic_alignment",
    }, requirements
    assert requirements["symbol_map"]["item_count"] == {"minimum": 2, "maximum": 5}
    assert "caller_evidence" in requirements["symbol_map"]["item_fields"]
    assert requirements["changed_line_classification"]["categories"] == [
        "production",
        "tests_or_fixtures",
        "docs",
        "generated",
        "mechanical_moves",
    ]
    assert "negative_fields" in requirements["walkthroughs"]
    assert "regression_test" in requirements["failure_analysis"]["fields"]
    assert requirements["code_volume"]["verdict_values"] == [
        "necessary",
        "partly_avoidable",
        "not_yet_proven",
    ]
    assert requirements["change_proportionality"]["verdict_values"] == [
        "proportionate",
        "disproportionate",
        "not_yet_proven",
    ]
    assert "green CI" in requirements["change_proportionality"]["rule"]
    assert "original problem" in requirements["change_proportionality"]["rule"]
    assert requirements["default_off_isolation"]["verdict_values"] == [
        "isolated",
        "not_isolated",
        "not_applicable",
        "not_yet_proven",
    ]
    assert "paired_counterfactual_validation" in requirements[
        "default_off_isolation"
    ]["fields"]
    assert requirements["authority_semantics"]["verdict_values"] == [
        "aligned",
        "misleading",
        "not_applicable",
        "not_yet_proven",
    ]
    assert "actual_actor_lifecycle" in requirements["authority_semantics"]["fields"]
    assert "substring denylists" in requirements["typed_state_rule"]["rule"]
    assert "domain-neutral" in requirements["domain_neutrality"]["rule"]
    assert (
        "silent behavior changes" in requirements["behavior_change_disclosure"]["rule"]
    )
    assert "must_attempt_work" in requirements["guidance_vs_obligation"]["rule"]
    assert "real, durable value" in requirements["durable_smoke_value"]["rule"]
    assert "same-shape batch farming" in requirements["durable_smoke_value"]["rule"]
    assert execution["completion_gate"]["metadata_only_verdict_allowed"] is False
    assert execution["completion_gate"]["stale_head_verdict_allowed"] is False
    assert execution["completion_gate"]["blocking_evidence_verdicts"] == {
        "problem_context": ["off_goal", "fragmented", "not_yet_proven"],
        "repository_reuse": ["unjustified_duplication", "not_yet_proven"],
        "observable_semantics": ["unintended_drift", "not_yet_proven"],
        "change_proportionality": ["disproportionate", "not_yet_proven"],
        "default_off_isolation": ["not_isolated", "not_yet_proven"],
        "authority_semantics": ["misleading", "not_yet_proven"],
        "semantic_alignment": ["not_yet_proven", "violated"],
    }
    assert execution["finding_contract"]["findings_first"] is True
    first_plan = first["review_plan"]
    assert first_plan["schema_version"] == "pull_request_review_plan_v1", first_plan
    assert first_plan["applicability"]["docs_only"] is True, first_plan
    assert first_plan["applicability"]["symbol_map_required"] is False, first_plan
    assert first_plan["applicability"]["typed_state_rule_required"] is False, first_plan
    assert (
        first_plan["applicability"]["behavior_change_disclosure_required"] is False
    ), first_plan
    assert first_plan["applicability"]["domain_neutrality_required"] is False, (
        first_plan
    )
    assert first_plan["applicability"]["guidance_vs_obligation_required"] is False, (
        first_plan
    )
    assert "symbol_map" not in first_plan["required_evidence_ids"], first_plan
    assert first_plan["result_template"]["target_exact_head"] == (
        "773@7730000000000000000000000000000000000000"
    ), first_plan
    merged = next(item for item in payload["pull_requests"] if item["number"] == 770)
    merged_plan = merged["review_plan"]
    assert merged_plan["applicability"]["code_change"] is True, merged_plan
    assert merged_plan["applicability"]["symbol_map_required"] is True, merged_plan
    assert merged_plan["applicability"]["negative_walkthrough_required"] is True, (
        merged_plan
    )
    assert merged_plan["applicability"]["typed_state_rule_required"] is True, (
        merged_plan
    )
    assert (
        merged_plan["applicability"]["behavior_change_disclosure_required"] is True
    ), merged_plan
    assert "typed_state_rule" in merged_plan["required_evidence_ids"], merged_plan
    assert "behavior_change_disclosure" in merged_plan["required_evidence_ids"], (
        merged_plan
    )
    assert "symbol_map" in merged_plan["required_evidence_ids"], merged_plan
    merged_risk_hint = merged["metadata_risk_hint"]
    assert merged_risk_hint["level"] == "medium", merged_risk_hint
    merged_main_risk = merged["main_regression_analysis"]
    assert merged_main_risk["risk_level"] == "high", merged_main_risk
    assert merged_main_risk["post_merge_review"] is True, merged_main_risk
    assert any(
        "Runtime or CLI behavior" in item
        for item in merged_main_risk["potential_regressions"]
    ), merged_main_risk
    assert payload["boundary"]["absolute_paths_recorded"] is False, payload["boundary"]
    assert_public_safe(payload)

    group_limited = json.loads(
        run_cli(
            "--format", "json", "pr-review", "--fixture", str(FIXTURE),
            "--state", "all", "--limit", "1"
        ).stdout
    )
    assert group_limited["summary"]["total_pr_count"] == 2, group_limited["summary"]
    assert group_limited["summary"]["open_pr_count"] == 1, group_limited["summary"]
    assert group_limited["summary"]["merged_pr_count"] == 1, group_limited["summary"]
    assert group_limited["review_groups"]["unmerged"]["pr_numbers"] == [771], (
        group_limited["review_groups"]
    )
    assert group_limited["review_groups"]["merged"]["pr_numbers"] == [770], (
        group_limited["review_groups"]
    )
    assert [item["number"] for item in group_limited["pull_requests"]] == [
        771,
        770,
    ], group_limited["pull_requests"]

    open_only = json.loads(
        run_cli(
            "--format",
            "json",
            "pr-review",
            "--fixture",
            str(FIXTURE),
            "--state",
            "open",
            "--limit",
            "5",
        ).stdout
    )
    assert open_only["summary"]["total_pr_count"] == 3, open_only["summary"]
    assert open_only["summary"]["merged_pr_count"] == 0, open_only["summary"]

    windowed = json.loads(
        run_cli(
            "--format",
            "json",
            "pr-review",
            "--fixture",
            str(FIXTURE),
            "--state",
            "all",
            "--since",
            "2026-06-27T12:20:00Z",
            "--limit",
            "5",
        ).stdout
    )
    assert windowed["request"]["since"] == "2026-06-27T12:20:00Z", windowed["request"]
    assert windowed["summary"]["total_pr_count"] == 3, windowed["summary"]
    assert windowed["summary"]["open_pr_count"] == 2, windowed["summary"]
    assert windowed["summary"]["merged_pr_count"] == 1, windowed["summary"]
    assert any(item["number"] == 770 for item in windowed["review_sequence"]), windowed[
        "review_sequence"
    ]

    default_markdown = run_cli(
        "pr-review", "--fixture", str(FIXTURE), "--limit", "1"
    ).stdout
    assert "state_filter: `open`" in default_markdown, default_markdown
    assert "#770" not in default_markdown, default_markdown
    markdown = run_cli(
        "pr-review", "--fixture", str(FIXTURE), "--state", "all", "--limit", "1"
    ).stdout
    assert "# Project PR Review Queue" in markdown, markdown
    assert "current gh repository" not in markdown, markdown
    assert "state_filter: `all`" in markdown, markdown
    assert "merged=`" in markdown, markdown
    assert "tool contract: run `loopx pr-review` first" in markdown, markdown
    assert "final answer contract: queue/table is only a preface" in markdown, markdown
    assert "## Agent Output Contract" in markdown, markdown
    assert "Do not stop at the queue/table summary" in markdown, markdown
    assert "agent_response_contract.review_execution_contract" in markdown, markdown
    assert "review plan: exact_head=" in markdown, markdown
    assert "remote head SHA" in markdown, markdown
    assert (
        "Required card headings: `动机`, `改动思路`, `具体改动`, `对主干的风险`, `我的整体评价`"
        in markdown
    ), markdown
    assert "`关键代码讲解`" in markdown, markdown
    assert "## Unmerged PRs" in markdown, markdown
    assert "## Merged PRs" in markdown, markdown
    assert "#770" in markdown, markdown
    assert "## Combined Review Sequence" in markdown, markdown
    assert markdown.index("## Unmerged PRs") < markdown.index("## Merged PRs"), markdown
    assert "risk_hint=`medium`" in markdown, markdown
    assert "template below is intentionally blank" in markdown, markdown
    assert "- 推荐阅读顺序:" in markdown, markdown
    assert "- 五块模板（留空给 agentloop 填写）:" in markdown, markdown
    assert "动机（按证据需要；无最低字数）" in markdown, markdown
    assert "改动思路（按证据需要；无最低字数）" in markdown, markdown
    assert "具体改动（按证据需要；无最低字数）" in markdown, markdown
    assert "对主干的风险（按证据需要；无最低字数）" in markdown, markdown
    assert "我的整体评价（按证据需要；无最低字数）" in markdown, markdown
    assert "main regression risk:" not in markdown, markdown
    assert "## Combined Review Sequence" in markdown, markdown
    assert "PR #771" in markdown, markdown

    print("pr-review-command-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
