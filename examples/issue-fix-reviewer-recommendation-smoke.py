#!/usr/bin/env python3
"""Smoke-test explainable reviewer recommendations from local ownership evidence."""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loopx.capabilities.issue_fix.reviewer_recommendation import (  # noqa: E402
    ISSUE_FIX_REVIEWER_RECOMMENDATION_SCHEMA_VERSION,
    ISSUE_FIX_REVIEWER_SOURCES_INPUT_SCHEMA_VERSION,
    build_issue_fix_reviewer_recommendation_packet,
)
from loopx.extensions.process_runtime import remove_owned_tree  # noqa: E402


PRIVATE_PATTERNS = [
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/private/"),
    re.compile(r"/tmp/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\"),
]


@contextmanager
def temporary_git_repo() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="loopx-reviewer-recommendation-"))
    try:
        yield path
    finally:
        for attempt in range(10):
            try:
                remove_owned_tree(path)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                if exc.errno != errno.ENOTEMPTY or attempt == 9:
                    raise
                time.sleep(0.05)


def run_git(
    repo: Path,
    *args: str,
    author: str | None = None,
    author_email: str | None = None,
) -> None:
    env = dict(os.environ)
    if author:
        login = author.lower().replace(" ", "-")
        env.update(
            {
                "GIT_AUTHOR_NAME": author,
                "GIT_AUTHOR_EMAIL": author_email or f"{login}@users.noreply.github.com",
                "GIT_COMMITTER_NAME": author,
                "GIT_COMMITTER_EMAIL": f"{login}@users.noreply.github.com",
            }
        )
    subprocess.run(
        ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(
    repo: Path,
    message: str,
    author: str,
    *,
    author_email: str | None = None,
) -> None:
    run_git(repo, "add", "-A")
    run_git(
        repo,
        "commit",
        "-m",
        message,
        author=author,
        author_email=author_email,
    )


def assert_public_safe(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    for pattern in PRIVATE_PATTERNS:
        assert not pattern.search(text), pattern.pattern
    assert "@users.noreply.github.com" not in text, text
    assert "raw codeowners" not in text.lower(), text
    assert payload["external_reads_performed"] is False
    assert payload["external_writes_performed"] is False
    assert payload["review_request_performed"] is False
    assert payload["local_paths_captured"] is False
    assert payload["raw_git_output_captured"] is False
    assert payload["commit_emails_captured"] is False


def run_cli(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(result.stdout)


def main() -> int:
    reviewer_sources = {
        "schema_version": ISSUE_FIX_REVIEWER_SOURCES_INPUT_SCHEMA_VERSION,
        "sources": [
            {
                "source_id": "repository-maintainer-map",
                "source_kind": "maintainer_map",
                "reference": "https://github.com/owner/repo/issues/10",
                "trust": "verified",
                "freshness": "current",
                "observed_at": "2026-07-10T00:00:00Z",
                "routes": [
                    {
                        "route_id": "src-general",
                        "match_kind": "path_prefix",
                        "pattern": "src",
                        "primary_reviewers": ["@broad-owner"],
                        "fallback_reviewers": ["@broad-backup"],
                    },
                    {
                        "route_id": "service-specific",
                        "match_kind": "path_prefix",
                        "pattern": "src/service.py",
                        "primary_reviewers": ["@declared-service-owner"],
                        "fallback_reviewers": ["@declared-service-backup"],
                    },
                    {
                        "route_id": "cross-module",
                        "match_kind": "repository_fallback",
                        "primary_reviewers": [],
                        "fallback_reviewers": ["@cross-module-owner"],
                    },
                ],
            }
        ],
    }
    dry = build_issue_fix_reviewer_recommendation_packet(
        repo_path="path-not-read-in-preview",
        repo="owner/repo",
        changed_files=["src/service.py"],
        reviewer_sources_input=reviewer_sources,
    )
    assert dry["ok"] is True, dry
    assert dry["schema_version"] == ISSUE_FIX_REVIEWER_RECOMMENDATION_SCHEMA_VERSION
    assert dry["execute"] is False
    assert dry["private_repo_state_read"] is False
    assert dry["recommendation_status"] == "preview_only"
    assert dry["reviewer_source_count"] == 1
    assert dry["reviewer_source_refs"] == ["https://github.com/owner/repo/issues/10"]
    assert_public_safe(dry)
    try:
        build_issue_fix_reviewer_recommendation_packet(
            repo_path="path-not-read-in-preview",
            repo="owner/repo",
            changed_files=[f"src/file-{index}.py" for index in range(101)],
        )
    except ValueError as exc:
        assert "at most 100" in str(exc), exc
    else:
        raise AssertionError("changed-file truncation must fail closed")
    try:
        build_issue_fix_reviewer_recommendation_packet(
            repo_path="path-not-read-in-preview",
            repo="owner/repo",
            changed_files=["src/service.py"],
            base_ref="--output=unexpected",
        )
    except ValueError as exc:
        assert "base_ref" in str(exc), exc
    else:
        raise AssertionError("option-like base refs must be rejected")
    unsafe_sources = json.loads(json.dumps(reviewer_sources))
    unsafe_sources["sources"][0]["reference"] = "https://localhost/maintainers"
    try:
        build_issue_fix_reviewer_recommendation_packet(
            repo_path="path-not-read-in-preview",
            repo="owner/repo",
            changed_files=["src/service.py"],
            reviewer_sources_input=unsafe_sources,
        )
    except ValueError as exc:
        assert "local host" in str(exc), exc
    else:
        raise AssertionError("local reviewer source URLs must be rejected")
    naive_sources = json.loads(json.dumps(reviewer_sources))
    naive_sources["sources"][0]["observed_at"] = "2026-07-10T00:00:00"
    try:
        build_issue_fix_reviewer_recommendation_packet(
            repo_path="path-not-read-in-preview",
            repo="owner/repo",
            changed_files=["src/service.py"],
            reviewer_sources_input=naive_sources,
        )
    except ValueError as exc:
        assert "timezone-aware" in str(exc), exc
    else:
        raise AssertionError("naive reviewer source timestamps must be rejected")

    with temporary_git_repo() as repo:
        run_git(repo, "init", "-b", "main")
        write(
            repo / ".github/CODEOWNERS",
            "* @fallback-owner\n/src/ @core-team\n/src/service.py @service-owner\n/docs/ @docs-team\n",
        )
        write(repo / "src/service.py", "VALUE = 1\n")
        write(repo / "src/existing.py", "EXISTING = True\n")
        write(repo / "docs/guide.md", "# Guide\n")
        commit(repo, "Add service and ownership policy", "Alice Maintainer")

        write(repo / "src/service.py", "VALUE = 2\n")
        commit(repo, "Refine service", "Bob Contributor")

        write(repo / "src/service.py", "VALUE = 2\nAUTOMATED = True\n")
        commit(repo, "Automated service refresh", "Release Bot")

        write(repo / "docs/guide.md", "# Guide\n\nAuthored before the feature.\n")
        commit(repo, "Refresh guide before feature", "Current Author")
        write(repo / "src/manual.py", "MANUAL = 1\n")
        commit(
            repo,
            "Add manually resolved module",
            "Human Confirmed",
            author_email="human-confirmed@example.test",
        )

        run_git(repo, "checkout", "-b", "feature/reviewer-plan")
        write(repo / "src/service.py", "VALUE = 3\n")
        write(repo / "src/new_module.py", "NEW = True\n")
        write(repo / "docs/guide.md", "# Guide\n\nUpdated.\n")
        write(repo / "src/manual.py", "MANUAL = 2\n")
        commit(repo, "Update service and docs", "Current Author")

        packet = build_issue_fix_reviewer_recommendation_packet(
            repo_path=repo,
            repo="owner/repo",
            base_ref="main",
            history_limit=20,
            max_candidates=20,
            exclude_reviewers=["@current-author"],
            exclude_author_names=["Current Author"],
            resolved_identities={"Human Confirmed": "@human-confirmed"},
            reviewer_sources_input=reviewer_sources,
            execute=True,
        )
        assert packet["ok"] is True, packet
        assert packet["private_repo_state_read"] is True
        assert packet["recommendation_status"] == "candidates_ready", packet
        assert packet["changed_files"] == [
            "docs/guide.md",
            "src/manual.py",
            "src/new_module.py",
            "src/service.py",
        ], packet
        assert packet["evidence_summary"]["codeowners_source"] == ".github/CODEOWNERS"
        assert packet["evidence_summary"]["history_revision"] == "main"
        assert packet["excluded_author_name_count"] == 2
        assert all(
            item.get("display_name") != "Current Author"
            for item in packet["candidates"]
        ), packet
        by_handle = {
            item["reviewer_handle"]: item
            for item in packet["candidates"]
            if item.get("reviewer_handle")
        }
        assert "@current-author" not in by_handle, by_handle
        assert "@release-bot" not in by_handle, by_handle
        assert "@service-owner" in by_handle, by_handle
        assert "@docs-team" in by_handle, by_handle
        assert "@core-team" in by_handle, by_handle
        assert "@alice-maintainer" in by_handle, by_handle
        assert "@bob-contributor" in by_handle, by_handle
        assert "@human-confirmed" in by_handle, by_handle
        assert "@declared-service-owner" in by_handle, by_handle
        assert "@declared-service-backup" in by_handle, by_handle
        assert "@broad-owner" in by_handle, by_handle
        assert "@broad-backup" in by_handle, by_handle
        assert "@cross-module-owner" in by_handle, by_handle
        assert (
            "verified_identity_mapping" in by_handle["@human-confirmed"]["source_kinds"]
        )
        assert (
            "caller_verified_github_identity"
            in by_handle["@human-confirmed"]["reason_codes"]
        )
        assert packet["resolved_identity_count"] == 1
        assert by_handle["@service-owner"]["confidence"] == "medium"
        assert (
            "repository_codeowners_match" in by_handle["@service-owner"]["reason_codes"]
        )
        assert (
            "changed_module_commit_history"
            in by_handle["@alice-maintainer"]["reason_codes"]
        )
        declared = by_handle["@declared-service-owner"]
        assert declared["confidence"] == "medium"
        assert by_handle["@service-owner"]["score"] > declared["score"]
        assert declared["score"] > by_handle["@bob-contributor"]["score"]
        assert declared["matched_files"] == ["src/service.py"]
        assert declared["source_refs"] == ["https://github.com/owner/repo/issues/10"]
        assert "repository_declared_primary_contact" in declared["reason_codes"]
        assert declared["reviewer_source_evidence"][0]["route_id"] == (
            "service-specific"
        )
        assert declared["reviewer_source_evidence"][0]["pattern"] == ("src/service.py")
        assert declared["reviewer_source_evidence"][0]["observed_at"] == (
            "2026-07-10T00:00:00Z"
        )
        assert "src/service.py" not in by_handle["@broad-owner"]["matched_files"]
        assert by_handle["@cross-module-owner"]["matched_files"] == ["docs/guide.md"]
        assert (
            "repository_declared_cross_module_fallback"
            in by_handle["@cross-module-owner"]["reason_codes"]
        )
        assert packet["evidence_summary"]["raw_reviewer_source_input_captured"] is False
        assert packet["raw_reviewer_source_input_captured"] is False
        assert packet["policy"]["automatic_review_request_allowed"] is True
        assert packet["policy"]["automatic_request_policy"] == (
            "request_top_requestable_when_authorized"
        )
        assert packet["policy"]["external_review_request_authority_required"] is True
        assert_public_safe(packet)

        identity_map = repo / "identity-map.json"
        write(identity_map, json.dumps({"Human Confirmed": "@human-confirmed"}))
        reviewer_sources_path = repo / "reviewer-sources.json"
        write(reviewer_sources_path, json.dumps(reviewer_sources))
        cli_packet = run_cli(
            [
                "issue-fix",
                "reviewer-plan",
                "--repo-path",
                str(repo),
                "--repo",
                "owner/repo",
                "--base-ref",
                "main",
                "--exclude-reviewer",
                "@current-author",
                "--exclude-author-name",
                "Current Author",
                "--identity-map-json",
                str(identity_map),
                "--reviewer-sources-json",
                str(reviewer_sources_path),
                "--execute",
            ]
        )
        assert cli_packet["ok"] is True, cli_packet
        assert cli_packet["recommendation_status"] == "candidates_ready"
        assert cli_packet["reviewer_source_count"] == 1
        assert any(
            item.get("reviewer_handle") == "@declared-service-owner"
            for item in cli_packet["candidates"]
        )
        assert cli_packet["review_request_performed"] is False
        assert_public_safe(cli_packet)

    print("issue-fix-reviewer-recommendation-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
