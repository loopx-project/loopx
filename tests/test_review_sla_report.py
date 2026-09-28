"""Review SLA report: business-time arithmetic, attribution and the review clock."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "review_sla_report.py"
GOVERNANCE = REPO_ROOT / ".github" / "GOVERNANCE.md"
spec = importlib.util.spec_from_file_location("loopx_review_sla_report", SCRIPT_PATH)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def _actor(login, *, typename="User"):
    return {"login": login, "__typename": typename}


def _pr(
    number,
    author,
    created,
    *,
    reviews=(),
    comments=(),
    merged=None,
    merged_by=None,
    files=("loopx/x.py",),
    draft=False,
    timeline=(),
    review_total=None,
    comment_total=None,
):
    return {
        "number": number,
        "state": "MERGED" if merged else "OPEN",
        "createdAt": created,
        "mergedAt": merged,
        "isDraft": draft,
        "author": author if isinstance(author, dict) else _actor(author),
        "mergedBy": merged_by if isinstance(merged_by, dict) else (_actor(merged_by) if merged_by else None),
        "files": {"totalCount": len(files), "nodes": [{"path": path} for path in files]},
        "reviews": {
            "totalCount": len(reviews) if review_total is None else review_total,
            "nodes": [
                {"author": who if isinstance(who, dict) else _actor(who), "submittedAt": at}
                for who, at in reviews
            ],
        },
        "comments": {
            "totalCount": len(comments) if comment_total is None else comment_total,
            "nodes": [
                {"author": who if isinstance(who, dict) else _actor(who), "createdAt": at}
                for who, at in comments
            ],
        },
        "timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": list(timeline)},
    }


def _ready(at):
    return {"__typename": "ReadyForReviewEvent", "createdAt": at}


def _to_draft(at):
    return {"__typename": "ConvertToDraftEvent", "createdAt": at}


def test_business_hours_skip_the_utc8_weekend():
    # Friday 2026-09-25 22:00 UTC+8 to Monday 2026-09-28 02:00 UTC+8.
    start = report._ts("2026-09-25T14:00:00Z")
    end = report._ts("2026-09-27T18:00:00Z")
    assert report.business_hours(start, end) == 4.0


def test_default_responders_match_the_published_roster():
    """The metric counts appointed reviewers, so the list has to be the roster."""
    roster = {
        match.group(1).casefold()
        for match in re.finditer(
            r"^\| \[`@([^`]+)`\]\([^)]*\) \| ", GOVERNANCE.read_text(encoding="utf-8"), re.M
        )
    }
    assert set(report.DEFAULT_RESPONDERS) <= roster, sorted(roster)
    # The roster table lists exactly the accounts the report treats as responders.
    assert set(report.DEFAULT_RESPONDERS) == {"huangruiteng", "steven-kid", "maxliux5"}


def test_actor_identity_comes_from_the_typed_actor_not_the_login():
    pr = _pr(
        1,
        "alice",
        "2026-09-21T01:00:00Z",
        comments=[(_actor("copilot-peter"), "2026-09-21T01:30:00Z")],
        reviews=[(_actor("release-bot", typename="Bot"), "2026-09-21T01:45:00Z")],
        merged="2026-09-21T05:00:00Z",
        merged_by="huangruiteng",
    )
    assert report.is_human(_actor("copilot-peter")) is True
    assert report.is_human(_actor("release-bot", typename="Bot")) is False
    assert report.is_human(_actor("dependabot", typename="Mannequin")) is False
    # A Bot review is never a response, and a non-roster login is not a maintainer.
    assert report.first_response(pr, report.DEFAULT_RESPONDERS) == report._ts("2026-09-21T05:00:00Z")


def test_first_response_requires_an_appointed_responder():
    pr = _pr(
        1,
        "alice",
        "2026-09-21T00:00:00Z",
        comments=[("ordinary-reader", "2026-09-21T01:00:00Z")],
        reviews=[("steven-kid", "2026-09-24T01:00:00Z")],
    )
    # The published target is the first maintainer response, not the first reply.
    assert report.first_response(pr, ("huangruiteng", "steven-kid", "maxliux5")) == report._ts(
        "2026-09-24T01:00:00Z"
    )
    assert report.first_response(pr, ("maxliux5",)) is None


def test_review_clock_uses_the_first_exit_from_draft():
    created_draft = _pr(1, "alice", "2026-09-21T01:00:00Z", timeline=[_ready("2026-09-24T01:00:00Z")])
    created_ready = _pr(2, "alice", "2026-09-21T01:00:00Z", timeline=[_to_draft("2026-09-22T01:00:00Z")])
    assert report.review_clock_start(created_draft) == (report._ts("2026-09-24T01:00:00Z"), "left_draft")
    assert report.review_clock_start(created_ready) == (report._ts("2026-09-21T01:00:00Z"), "opened")
    assert report.review_clock_start(_pr(3, "alice", "2026-09-21T01:00:00Z"))[1] == "opened"

    summary = report.latency_summary(
        [
            _pr(
                1,
                "alice",
                "2026-09-21T01:00:00Z",
                timeline=[_ready("2026-09-24T01:00:00Z")],
                reviews=[("huangruiteng", "2026-09-24T02:00:00Z")],
            )
        ],
        exclude_authors={"huangruiteng"},
    )
    # The transition, not the opening, is the clock, so the response is inside
    # the target instead of 73 weekday hours.
    assert summary["clock_start"] == {"opened": 0, "left_draft": 1}
    assert summary["first_maintainer_response_hours"]["p50"] == 1.0
    assert summary["first_maintainer_response_within_2_business_days"] == 1.0


def test_latency_summary_excludes_maintainer_and_draft_prs():
    prs = [
        _pr(1, "alice", "2026-09-21T01:00:00Z", reviews=[("huangruiteng", "2026-09-21T03:00:00Z")]),
        _pr(2, "bob", "2026-09-21T01:00:00Z", reviews=[("huangruiteng", "2026-09-24T01:00:00Z")]),
        _pr(3, "huangruiteng", "2026-09-21T01:00:00Z"),
        _pr(4, "carol", "2026-09-21T01:00:00Z", draft=True),
    ]
    summary = report.latency_summary(prs, exclude_authors={"huangruiteng"})
    assert summary["pull_requests"] == 2
    assert summary["without_maintainer_response"] == 0
    assert summary["first_maintainer_response_within_2_business_days"] == 0.5
    assert summary["first_maintainer_response_hours"]["p50"] == 72.0


def test_truncated_review_or_comment_lists_are_reported():
    prs = [
        _pr(1, "alice", "2026-09-21T01:00:00Z", review_total=31),
        _pr(2, "bob", "2026-09-21T01:00:00Z", comment_total=45),
        _pr(3, "carol", "2026-09-21T01:00:00Z"),
    ]
    assert report.truncated_records(prs) == 2
    assert report.latency_summary(prs, exclude_authors={"huangruiteng"})["truncated_records"] == 2


def test_cross_author_reviews_count_distinct_prs_and_honour_path_scope():
    prs = [
        _pr(
            1,
            "alice",
            "2026-09-21T01:00:00Z",
            reviews=[("bob", "2026-09-21T02:00:00Z")],
            comments=[("bob", "2026-09-21T03:00:00Z")],
            files=("loopx/capabilities/periodic_report/a.py",),
        ),
        _pr(2, "bob", "2026-09-21T01:00:00Z", comments=[("bob", "2026-09-21T02:00:00Z")]),
        _pr(3, "alice", "2026-09-21T01:00:00Z", comments=[("bob", "2026-09-21T02:00:00Z")], files=("docs/a.md",)),
        _pr(
            4,
            "alice",
            "2026-09-21T01:00:00Z",
            comments=[(_actor("release-bot", typename="Bot"), "2026-09-21T02:00:00Z")],
        ),
    ]
    assert report.cross_author_reviews(prs) == {"bob": 2}
    scoped = report.cross_author_reviews(prs, path_prefixes=("loopx/capabilities/periodic_report/",))
    assert scoped == {"bob": 1}


def test_cli_reads_json_lines_input(tmp_path, capsys):
    source = tmp_path / "prs.jsonl"
    rows = [_pr(1, "alice", "2026-09-21T01:00:00Z", reviews=[("huangruiteng", "2026-09-21T02:00:00Z")])]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    assert (
        report.main([
            "--since", "2026-09-01", "--input", str(source),
            "--exclude-author", "huangruiteng", "--format", "json",
        ])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["contributor_latency"]["pull_requests"] == 1
    assert payload["contributor_latency"]["responders"] == sorted(report.DEFAULT_RESPONDERS)
    assert payload["cross_author_reviews"] == {"huangruiteng": 1}


def test_cli_accepts_an_explicit_responder_list(tmp_path, capsys):
    source = tmp_path / "prs.jsonl"
    rows = [_pr(1, "alice", "2026-09-21T01:00:00Z", comments=[("ordinary-reader", "2026-09-21T02:00:00Z")])]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    assert report.main(["--since", "2026-09-01", "--input", str(source), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["contributor_latency"]["without_maintainer_response"] == 1
    assert (
        report.main([
            "--since", "2026-09-01", "--input", str(source),
            "--responder", "ordinary-reader", "--format", "json",
        ])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["contributor_latency"]["without_maintainer_response"] == 0
