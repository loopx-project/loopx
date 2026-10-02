"""Approval is not proof that another reviewer's findings were resolved."""
from __future__ import annotations

import pytest
import json
from pathlib import Path

from loopx.capabilities.pr_review_queue.approval_closeout import plan_approval_closeout
from loopx.capabilities.pr_review_queue import build_agent_response_contract

HEAD, OLD = "a" * 40, "b" * 40


def review(identity, state, *, login="other", head=OLD):
    return {"id": identity, "state": state, "user": {"login": login},
            "commit_id": head, "submitted_at": f"2026-10-01T00:00:{identity:02d}Z",
            "html_url": f"https://github.com/owner/repo/pull/42#pullrequestreview-{identity}"}


def request(reviews):
    return {"expected_exact_head": f"42@{HEAD}", "repository": "owner/repo",
            "pull_request": {"number": 42, "headRefOid": HEAD, "state": "OPEN",
                             "reviewDecision": "CHANGES_REQUESTED"},
            "readback_head": HEAD, "reviews_complete": True, "reviews": reviews,
            "approval_conclusion": {"valid": True, "verdict": "APPROVE"}}


def test_other_reviewer_blocker_survives_own_approval_and_comments():
    result = plan_approval_closeout(request([
        review(1, "CHANGES_REQUESTED"), review(2, "COMMENTED"),
        review(3, "APPROVED", login="maintainer", head=HEAD),
    ]))
    assert result["status"] == "verification_required"
    assert [row["review_id"] for row in result["blocking_reviews"]] == [1]
    assert result["blocking_reviews"][0]["on_approved_head"] is False
    assert result["dismissal_authorized"] is False
    assert result["github_write_performed"] is False


@pytest.mark.parametrize("superseding", ["APPROVED", "DISMISSED"])
def test_superseded_history_is_not_resurrected(superseding):
    data = request([
        review(1, "CHANGES_REQUESTED"), review(2, superseding),
    ])
    data["pull_request"]["reviewDecision"] = None
    result = plan_approval_closeout(data)
    assert result["status"] == "clear"
    assert result["blocking_reviews"] == []
    data["reviews"].reverse()
    assert result == plan_approval_closeout(data)


def test_aggregate_history_conflict_is_not_a_clear_closeout():
    result = plan_approval_closeout(request([review(1, "DISMISSED")]))
    assert result["status"] == "hold"
    assert "aggregate_review_decision_conflict" in result["hold_reasons"]


def test_only_latest_blocker_per_reviewer_needs_reconciliation():
    result = plan_approval_closeout(request([
        review(1, "CHANGES_REQUESTED"), review(2, "CHANGES_REQUESTED", head=HEAD),
        review(3, "PENDING"), review(4, "CHANGES_REQUESTED", login="third"),
    ]))
    assert [row["review_id"] for row in result["blocking_reviews"]] == [2, 4]
    # A newer/same-head review cannot silently be classified as resolved either.
    assert result["blocking_reviews"][0]["on_approved_head"] is True
    assert result["dismissal_authorized"] is False


@pytest.mark.parametrize("field,value,reason", [
    ("readback_head", OLD, "head_changed"),
    ("reviews_complete", False, "review_source_incomplete"),
    ("approval_conclusion", {"valid": False, "verdict": "APPROVE"}, "exact_head_approval_missing"),
    ("approval_conclusion", {"valid": True, "verdict": "REQUEST_CHANGES"}, "exact_head_approval_missing"),
])
def test_unverified_closeout_is_a_hold_not_clear(field, value, reason):
    data = request([review(1, "CHANGES_REQUESTED")])
    data[field] = value
    result = plan_approval_closeout(data)
    assert result["status"] == "hold"
    assert reason in result["hold_reasons"]
    assert result["dismissal_authorized"] is False


@pytest.mark.parametrize("change", [
    {"state": "UNKNOWN"}, {"user": {}}, {"id": None},
    {"submitted_at": "not-a-date"}, {"commit_id": ""},
])
def test_malformed_history_fails_closed(change):
    row = review(1, "CHANGES_REQUESTED") | change
    with pytest.raises(ValueError):
        plan_approval_closeout(request([row]))


def test_capability_owns_closeout_and_preserves_authority_boundary():
    closeout = build_agent_response_contract()["review_execution_contract"]["approval_closeout"]
    assert "--check-approval-closeout NUMBER@HEAD_OID" in closeout["readback_command"]
    assert closeout["required_after"] == "published_exact_head_approve_readback"
    assert closeout["old_commit_proves_resolution"] is False
    assert closeout["grants_dismissal_or_merge_authority"] is False
    assert "all_prior_findings_verified_resolved" in closeout["dismissal_requires"]
    assert "github_permission_and_owner_authority" in closeout["dismissal_requires"]
    assert "target_dismissed_approval_preserved_head_unchanged" in closeout["readback_requires"]


def test_live_adapter_paginates_history_without_ci_or_mutations(monkeypatch, capsys):
    from loopx.cli import main
    import loopx.pr_review as pr_module
    from loopx.capabilities.pr_review_queue import github_source

    body = (Path(__file__).parents[2] / "examples/fixtures/pr-review.body.md").read_text()
    body = body.replace("HEAD_OID", HEAD).replace("VERDICT", "APPROVE")
    own = review(3, "APPROVED", login="maintainer", head=HEAD) | {"body": body}
    calls = []

    def read(args, **_):
        calls.append(args)
        if args[0] == "api":
            assert "--paginate" in args and "--slurp" in args
            return [[review(1, "CHANGES_REQUESTED")], [own]]
        assert "statusCheckRollup" not in args[-1]
        return {"number": 42, "headRefOid": HEAD, "state": "OPEN",
                "author": {"login": "contributor"}, "reviewDecision": "CHANGES_REQUESTED",
                "files": [{"path": "loopx/runtime.py"}], "changedFiles": 1}

    monkeypatch.setattr(github_source, "run_gh_json", read)
    monkeypatch.setattr(pr_module, "resolve_current_github_login", lambda: "maintainer")
    assert main(["--format", "json", "pr-review", "--repo", "owner/repo",
                 "--check-approval-closeout", f"42@{HEAD}"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "verification_required"
    assert result["blocking_reviews"][0]["review_id"] == 1
    assert len(calls) == 3
    assert all("--method" not in args for args in calls)
    assert "body" not in json.dumps(result)


def test_cli_rejects_mixed_modes_before_github_read(monkeypatch, capsys):
    from loopx.cli import main
    from loopx.capabilities.pr_review_queue import github_source

    monkeypatch.setattr(github_source, "run_gh_json", lambda *_: pytest.fail("unexpected GitHub read"))
    assert main(["--format", "json", "pr-review", "--repo", "owner/repo",
                 "--check-approval-closeout", f"42@{HEAD}", "--autonomous-observation"]) == 1
    assert "cannot be combined" in capsys.readouterr().out
