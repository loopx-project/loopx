"""An independent submitted review avoids duplicate work, never earns approval."""
from __future__ import annotations

from copy import deepcopy

import pytest

from loopx.pr_review import build_pr_review_packet, render_pr_review_markdown

HEAD, OLD = "a" * 40, "b" * 40


def review(identity=1, *, login="contributor", state="COMMENTED", head=HEAD,
           association="CONTRIBUTOR", is_bot=False):
    return {"id": identity, "url": f"https://github.com/owner/repo/pull/42#pullrequestreview-{identity}",
            "author": {"login": login, "is_bot": is_bot}, "authorAssociation": association,
            "state": state, "commit": {"oid": head}, "body": "See inline feedback.",
            "submittedAt": f"2026-10-01T00:00:{identity:02d}Z"}


def raw_pr(reviews, *, complete=True, head=HEAD, state="OPEN", draft=False):
    return {
        "number": 42, "title": "Preserve the runtime contract", "state": state,
        "body": "Keep the original runtime behavior.", "changedFiles": 1,
        "author": {"login": "author"}, "headRefOid": head, "baseRefOid": "c" * 40,
        "isDraft": draft, "createdAt": "2026-10-01T00:00:00Z",
        "files": [{"path": "src/runtime.py", "additions": 2, "deletions": 1}],
        "reviews": reviews, "reviews_complete": complete,
        "reviewDecision": "CHANGES_REQUESTED", "mergeStateStatus": "CLEAN",
        "mergeable": "MERGEABLE", "commits": [],
    }


def packet(reviews, *, complete=True, head=HEAD, state="OPEN", draft=False, fresh=False):
    return build_pr_review_packet(pull_requests=[raw_pr(reviews, complete=complete,
        head=head, state=state, draft=draft)],
        repository="owner/repo", source="fixture", limit=10, state_filter="all",
        reviewer_login="agent", wait_for_ci=False,
        fresh_audit_exact_heads=[f"42@{head}"] if fresh else [])


@pytest.mark.parametrize("state", ["COMMENTED", "APPROVED", "CHANGES_REQUESTED"])
@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR"])
def test_submitted_independent_review_covers_work_without_our_body_format(state, association):
    data = packet([review(state=state, association=association)])
    row = data["pull_requests"][0]
    assert row["review_action_kind"] is None
    assert data["review_sequence"] == [] and data["summary"]["review_attention_count"] == 0
    assert row["review_plan"] is row["review_template"] is None
    assert row["evidence_commands"] == []
    evidence = row["independent_review"]
    assert evidence["reviewer"] == "contributor" and evidence["head_oid"] == HEAD
    assert evidence["association"] == association and evidence["state"] == state
    assert evidence["review_id"] == 1 and "pullrequestreview-1" in evidence["url"]
    assert evidence["grants_approval"] is evidence["grants_merge_authority"] is False
    assert row["review_conclusion"]["valid"] is False
    assert row["review_decision"] == "CHANGES_REQUESTED"
    assert "independent" in row["review_goal"].lower()
    assert "contributor" in render_pr_review_markdown(data)


@pytest.mark.parametrize("change", [
    {"author": {"login": "AUTHOR", "is_bot": False}},
    {"author": {"login": "contributor", "is_bot": True}},
    {"author": {"login": "contributor"}},
    {"authorAssociation": "NONE"}, {"authorAssociation": "FIRST_TIME_CONTRIBUTOR"},
    {"authorAssociation": None}, {"state": "PENDING"}, {"state": "DISMISSED"},
    {"authorAssociation": {"role": "OWNER"}},
    {"author": {"login": " author ", "is_bot": False}},
    {"commit": {"oid": OLD}}, {"submittedAt": None}, {"submittedAt": "not-a-date"},
])
def test_unqualified_review_does_not_suppress_full_review(change):
    row = packet([review() | change])["pull_requests"][0]
    assert row["review_action_kind"] == "rereview_pull_request_exact_head"
    assert row["review_plan"] is not None and row["evidence_commands"]
    assert "independent_review" not in row


def test_incomplete_review_source_does_not_suppress_or_invent_a_conclusion():
    data = packet([review()], complete=False)
    row = data["pull_requests"][0]
    assert row["review_action_kind"] == "rereview_pull_request_exact_head"
    assert row["review_conclusion"]["valid"] is False
    assert "independent_review" not in row


def test_dismissal_reopens_work_and_new_head_requires_new_review():
    histories = [[review(), review(2, state="DISMISSED")],
                 [review(), review(2, state="PENDING")]]
    for reviews, expected in zip(histories, ["rereview_pull_request_exact_head", None]):
        forward = packet(reviews)["pull_requests"][0]
        reverse = packet(list(reversed(reviews)))["pull_requests"][0]
        assert forward["review_action_kind"] == reverse["review_action_kind"] == expected
    assert packet([review()], head=OLD)["pull_requests"][0]["review_action_kind"] is not None
    assert packet([review(), review(2, head=OLD)], head=OLD)["review_sequence"] == []


def test_another_reviewers_dismissal_does_not_erase_independent_coverage():
    data = packet([review(), review(2, login="other", state="DISMISSED")])
    assert data["pull_requests"][0]["review_action_kind"] is None
    assert data["pull_requests"][0]["independent_review"]["reviewer"] == "contributor"


def test_explicit_fresh_audit_can_override_peer_coverage_without_granting_approval():
    row = packet([review()], fresh=True)["pull_requests"][0]
    assert row["review_action_kind"] == "audit_pull_request_exact_head"
    assert row["fresh_audit_requested"] and row["review_plan"] is not None
    assert row["review_conclusion"]["valid"] is False


def test_merged_head_is_inventory_only_when_independently_reviewed():
    assert packet([review()], state="MERGED")["review_sequence"] == []


def test_unknown_identity_parity_does_not_change_old_execution_or_strict_body_rules():
    original = review()
    original["author"].pop("is_bot")
    before = packet([deepcopy(original)], complete=False)["pull_requests"][0]
    after = packet([deepcopy(original)], complete=True)["pull_requests"][0]
    assert before == after


@pytest.mark.parametrize("state,draft", [("OPEN", True), ("CLOSED", False)])
def test_fresh_audit_is_not_a_draft_or_closed_lifecycle_bypass(state, draft):
    with pytest.raises(ValueError, match="requires a valid prior conclusion"):
        packet([review()], state=state, draft=draft, fresh=True)


@pytest.mark.parametrize("state", ["COMMENTED", "APPROVED", "CHANGES_REQUESTED"])
def test_work_coverage_cannot_satisfy_approval_closeout_or_erase_dissent(state):
    from loopx.capabilities.pr_review_queue.approval_closeout import plan_approval_closeout

    data = packet([review(state=state)])["pull_requests"][0]
    result = plan_approval_closeout({"expected_exact_head": f"42@{HEAD}",
        "repository": "owner/repo", "pull_request": {"number": 42, "headRefOid": HEAD,
            "state": "OPEN", "author": {"login": "author"}, "reviewDecision": data["review_decision"]},
        "readback_head": HEAD, "reviews_complete": True,
        "reviews": [rest_review(review(state=state))],
        "review_conclusions": [data["review_conclusion"] | {"review_id": 1}]})
    assert result["status"] == "hold"
    assert "exact_head_approval_missing" in result["hold_reasons"]
    assert result["dismissal_authorized"] is result["merge_authorized"] is False
    if state == "CHANGES_REQUESTED":
        assert [row["review_id"] for row in result["blocking_reviews"]] == [1]


def rest_review(row):
    return {"id": row["id"], "html_url": row["url"], "user": {
        "login": row["author"]["login"], "type": "Bot" if row["author"].get("is_bot") else "User"},
        "author_association": row["authorAssociation"], "state": row["state"],
        "submitted_at": row["submittedAt"], "commit_id": row["commit"]["oid"], "body": row["body"]}


def provider(pages, *, unavailable=False, after_head=HEAD):
    """Metadata substitutes GitHub IO, never the typed policy under test."""
    snapshot = review()
    snapshot["author"].pop("is_bot")  # Actual gh view omits actor type.
    pr = raw_pr([snapshot])
    pr.pop("reviews_complete")
    calls = []

    def run(args, *, cwd=None):
        del cwd
        calls.append(args)
        assert "statusCheckRollup" not in ",".join(args)
        if args[:2] == ["pr", "view"]:
            if args[args.index("--json") + 1] == "headRefOid,baseRefOid":
                return {"headRefOid": after_head, "baseRefOid": pr["baseRefOid"]}
            return deepcopy(pr)
        assert args[:4] == ["api", "--paginate", "--slurp", "repos/owner/repo/pulls/42/reviews?per_page=100"]
        if unavailable:
            raise OSError("Review history temporarily unavailable")
        return deepcopy(pages)

    return run, calls


def source_packet(run):
    from loopx.capabilities.pr_review_queue.github_source import scan_github_pull_request_targets

    scan = scan_github_pull_request_targets(repository="owner/repo", exact_heads=[f"42@{HEAD}"],
        wait_for_ci=False, run_gh_json=run)
    return build_pr_review_packet(pull_requests=scan["pull_requests"], repository="owner/repo",
        source="github_cli_exact_targets", source_scan=scan, limit=10, state_filter="all",
        reviewer_login="agent", wait_for_ci=False)


def test_complete_provider_history_sees_late_dismissal_then_another_review():
    pages = [[rest_review(review(state="APPROVED"))],
             [rest_review(review(2, state="DISMISSED"))]]
    run, calls = provider(pages)
    assert source_packet(run)["pull_requests"][0]["review_action_kind"] is not None
    pages[-1].append(rest_review(review(3, login="maintainer", association="MEMBER")))
    data = source_packet(run)
    assert data["pull_requests"][0]["review_action_kind"] is None
    assert data["pull_requests"][0]["independent_review"]["reviewer"] == "maintainer"
    assert data["result_completeness"]["complete"]
    assert len([call for call in calls if call[0] == "api"]) == 2


def test_source_failure_keeps_review_runnable_and_recovery_can_cover_it():
    pages = [[rest_review(review())]]
    unavailable, _ = provider(pages, unavailable=True)
    failed = source_packet(unavailable)
    assert failed["result_completeness"]["complete"]  # Ordinary versioned details are intact.
    assert failed["pull_requests"][0]["review_action_kind"] is not None
    assert "independent_review" not in failed["pull_requests"][0]
    recovered, _ = provider(pages)
    assert source_packet(recovered)["review_sequence"] == []


def test_provider_rechecks_head_after_history_before_suppressing_work():
    run, _ = provider([[rest_review(review())]], after_head=OLD)
    with pytest.raises(RuntimeError, match="detail read was incomplete"):
        source_packet(run)


@pytest.mark.parametrize("pages", [[{}], [[{}]], [[rest_review(review()) | {"user": None}]]])
def test_malformed_provider_history_retains_ordinary_review(pages):
    run, _ = provider(pages)
    row = source_packet(run)["pull_requests"][0]
    assert row["review_action_kind"] is not None and "independent_review" not in row


def test_inline_only_submitted_review_does_not_need_an_agent_body():
    row = packet([review() | {"body": ""}])["pull_requests"][0]
    assert row["review_action_kind"] is None and not row["review_conclusion"]["valid"]


def _provider_with_comments(
    review_pages, comment_pages, *, pr_author="cocolord", current_head=HEAD,
):
    snapshot = deepcopy(review_pages[0][0])
    pr = raw_pr([{
        "id": snapshot["id"], "url": snapshot["html_url"],
        "author": {"login": snapshot["user"]["login"]},
        "authorAssociation": snapshot["author_association"],
        "state": snapshot["state"], "commit": {"oid": snapshot["commit_id"]},
        "body": snapshot["body"], "submittedAt": snapshot["submitted_at"],
    }], head=current_head)
    pr["author"] = {"login": pr_author}
    pr.pop("reviews_complete")
    calls = []

    def run(args, *, cwd=None):
        del cwd
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            if args[args.index("--json") + 1] == "headRefOid,baseRefOid":
                return {"headRefOid": current_head, "baseRefOid": pr["baseRefOid"]}
            return deepcopy(pr)
        if args[:4] == ["api", "--paginate", "--slurp", "repos/owner/repo/pulls/42/reviews?per_page=100"]:
            return deepcopy(review_pages)
        if args[:4] == ["api", "--paginate", "--slurp", "repos/owner/repo/pulls/42/comments?per_page=100"]:
            if isinstance(comment_pages, Exception):
                raise comment_pages
            return deepcopy(comment_pages)
        raise AssertionError(f"unexpected gh args: {args}")

    return run, calls


def test_thread_reply_only_empty_commented_review_does_not_conclude_current_head():
    # Shape of GitHub GraphQL addPullRequestReviewThreadReply on cocolord PR:
    # prior CHANGES_REQUESTED on OLD head, plus empty-body COMMENTED wrapper on HEAD.
    old_req = rest_review(review(1, login="loopx-agent", state="CHANGES_REQUESTED", head=OLD, association="MEMBER"))
    reply_wrapper = rest_review(review(2, login="loopx-agent", state="COMMENTED", head=HEAD, association="MEMBER")) | {"body": ""}
    comments = [[{
        "id": 4713426271,
        "pull_request_review_id": 2,
        "in_reply_to_id": 4713301777,
    }]]
    run, calls = _provider_with_comments([[old_req, reply_wrapper]], comments, pr_author="cocolord")
    from loopx.capabilities.pr_review_queue.github_source import scan_github_pull_request_targets

    scan = scan_github_pull_request_targets(repository="owner/repo", exact_heads=[f"42@{HEAD}"], wait_for_ci=False, run_gh_json=run)
    annotated = {r["id"]: r for r in scan["pull_requests"][0]["reviews"]}
    assert annotated[2]["comment_provenance"] == "reply_only"
    assert annotated[2]["thread_reply_only"] is True
    row = source_packet(run)["pull_requests"][0]
    assert row["review_action_kind"] == "rereview_pull_request_exact_head"
    assert row["scheduling_lane"] != "current_head_concluded"
    assert "independent_review" not in row
    assert any("pulls/42/comments" in " ".join(call) for call in calls)


@pytest.mark.parametrize("comments", [
    [[{"id": 99, "pull_request_review_id": 1}]],
    [[{"id": 99, "pull_request_review_id": 1, "in_reply_to_id": None}]],
    [[{"id": 99, "pull_request_review_id": 1}, {"id": 100, "pull_request_review_id": 1, "in_reply_to_id": 88}]],
    [[{"id": 100, "pull_request_review_id": 1, "in_reply_to_id": 88}], [{"id": 99, "pull_request_review_id": 1, "in_reply_to_id": None}]],
    [[{"id": 99, "pull_request_review_id": 1, "in_reply_to_id": None}], [{"id": 101}]],
])
def test_inline_root_empty_commented_review_still_establishes_independent_review(comments):
    from loopx.capabilities.pr_review_queue.github_source import scan_github_pull_request_targets

    root_inline = rest_review(review(1, login="contributor", state="COMMENTED", head=HEAD)) | {"body": "   "}
    run, _ = _provider_with_comments([[root_inline]], comments, pr_author="author")
    scan = scan_github_pull_request_targets(repository="owner/repo", exact_heads=[f"42@{HEAD}"], wait_for_ci=False, run_gh_json=run)
    assert scan["pull_requests"][0]["reviews"][0]["comment_provenance"] == "inline_root"
    assert "thread_reply_only" not in scan["pull_requests"][0]["reviews"][0]
    row = source_packet(run)["pull_requests"][0]
    assert row["review_action_kind"] is None
    assert row["independent_review"]["review_id"] == 1
    assert row["independent_review"]["source_complete"] is True


def test_thread_reply_wrapper_preserves_earlier_current_head_formal_opinion():
    formal = rest_review(review(10, login="loopx-agent", state="CHANGES_REQUESTED", head=HEAD, association="MEMBER"))
    reply_wrapper = rest_review(review(11, login="loopx-agent", state="COMMENTED", head=HEAD, association="MEMBER")) | {"body": ""}
    comments = [[{
        "id": 4713426271,
        "pull_request_review_id": 11,
        "in_reply_to_id": 4713301777,
    }]]
    for ordered in ([formal, reply_wrapper], [reply_wrapper, formal]):
        for bad_or_good in (comments, OSError("comments unavailable"), [[comments[0][0]], [{"id": 999}]]):
            run, _ = _provider_with_comments([ordered], bad_or_good, pr_author="cocolord")
            row = source_packet(run)["pull_requests"][0]
            assert row["review_action_kind"] is None
            assert row["independent_review"]["review_id"] == 10
            assert row["independent_review"]["state"] == "CHANGES_REQUESTED"
            assert row["independent_review"]["source_complete"] is True


@pytest.mark.parametrize("bad_comments", [
    OSError("comments unavailable"),
    [],
    [[]],
    {},
    [[123]],
    [[{"id": 100, "pull_request_review_id": 2, "in_reply_to_id": 88}], [{"id": 101}]],
    [[{"id": 100, "pull_request_review_id": 2, "in_reply_to_id": 88}], [{"id": 101, "pull_request_review_id": None}]],
    [[{"id": 100, "pull_request_review_id": 2, "in_reply_to_id": 88}], [{"id": 101, "pull_request_review_id": "2"}]],
    [[{"id": 100, "pull_request_review_id": 999, "in_reply_to_id": 88}], [{"id": 101, "pull_request_review_id": 888, "in_reply_to_id": None}]],
    [[{"id": 100, "pull_request_review_id": 2, "in_reply_to_id": 0}]],
])
def test_unknown_comment_provenance_retains_rereview_and_never_claims_source_complete(bad_comments):
    from loopx.capabilities.pr_review_queue.github_source import scan_github_pull_request_targets

    old_req = rest_review(review(1, login="loopx-agent", state="CHANGES_REQUESTED", head=OLD, association="MEMBER"))
    reply_wrapper = rest_review(review(2, login="loopx-agent", state="COMMENTED", head=HEAD, association="MEMBER")) | {"body": ""}
    run, _ = _provider_with_comments([[old_req, reply_wrapper]], bad_comments, pr_author="cocolord")
    scan = scan_github_pull_request_targets(repository="owner/repo", exact_heads=[f"42@{HEAD}"], wait_for_ci=False, run_gh_json=run)
    annotated = {r["id"]: r for r in scan["pull_requests"][0]["reviews"]}
    assert annotated[2]["comment_provenance"] == "unknown"
    assert "thread_reply_only" not in annotated[2]
    row = source_packet(run)["pull_requests"][0]
    assert row["review_action_kind"] == "rereview_pull_request_exact_head"
    assert row["scheduling_lane"] != "current_head_concluded"
    assert "independent_review" not in row


def test_cross_model_coverage_ignores_thread_reply_only_commented_review():
    reply_wrapper = review(2, login="loopx-agent", state="COMMENTED", head=HEAD, association="MEMBER") | {
        "body": "", "thread_reply_only": True,
    }
    pr = raw_pr([reply_wrapper], complete=True, head=HEAD) | {"author": {"login": "cocolord"}}
    data = build_pr_review_packet(
        pull_requests=[pr], repository="owner/repo", source="fixture", limit=10,
        state_filter="all", reviewer_login="loopx-agent", wait_for_ci=False,
        reviewer_model_family="gpt",
    )
    row = data["pull_requests"][0]
    assert row["review_action_kind"] == "rereview_pull_request_exact_head"
    assert "independent_review" not in row


def test_old_head_reply_wrapper_preserves_current_head_formal_opinion_across_queues():
    from loopx.capabilities.pr_review_queue.github_source import scan_github_pull_request_targets

    formal_body = (
        "Reviewer: model_agent · GPT-6 · OpenAI · runtime_reported · effort_unavailable\n\n"
        "Looks solid overall.\n\n"
        "Verdict: APPROVE"
    )
    head_formal = rest_review(
        review(10, login="peer", state="APPROVED", head=HEAD, association="MEMBER")
        | {"body": formal_body, "submittedAt": "2026-10-01T10:00:00Z"}
    )
    old_reply = rest_review(
        review(11, login="peer", state="COMMENTED", head=OLD, association="MEMBER")
        | {"body": "", "submittedAt": "2026-10-01T11:00:00Z"}
    )
    reply_comments = [[{
        "id": 4713500001,
        "pull_request_review_id": 11,
        "in_reply_to_id": 4713400001,
    }]]

    for ordered in ([head_formal, old_reply], [old_reply, head_formal]):
        for family in (None, "gpt"):
            run, calls = _provider_with_comments([ordered], reply_comments, pr_author="cocolord")
            scan = scan_github_pull_request_targets(
                repository="owner/repo",
                exact_heads=[f"42@{HEAD}"],
                wait_for_ci=False,
                run_gh_json=run,
            )
            annotated = {r["id"]: r for r in scan["pull_requests"][0]["reviews"]}
            assert annotated[11]["comment_provenance"] == "reply_only"
            assert any("pulls/42/comments" in str(part) for args in calls for part in args)

            kwargs = {"reviewer_model_family": family} if family else {}
            packet_data = build_pr_review_packet(
                pull_requests=scan["pull_requests"],
                source="github_cli_exact_targets",
                source_scan=scan,
                repository="owner/repo",
                limit=10,
                state_filter="all",
                reviewer_login="loopx-agent",
                wait_for_ci=False,
                **kwargs,
            )
            row = packet_data["pull_requests"][0]
            assert row["review_action_kind"] is None
            assert row["independent_review"]["review_id"] == 10
            assert row["independent_review"]["state"] == "APPROVED"
            assert row["independent_review"]["source_complete"] is True
            assert row.get("cross_model_review_needed") is not True


def test_old_head_inline_root_dismissed_and_unknown_still_retire_current_head_coverage():
    from loopx.capabilities.pr_review_queue.github_source import scan_github_pull_request_targets

    formal_body = (
        "Reviewer: model_agent · GPT-6 · OpenAI · runtime_reported · effort_unavailable\n\n"
        "Looks solid overall.\n\n"
        "Verdict: APPROVE"
    )
    head_formal = rest_review(
        review(10, login="peer", state="APPROVED", head=HEAD, association="MEMBER")
        | {"body": formal_body, "submittedAt": "2026-10-01T10:00:00Z"}
    )
    old_empty = rest_review(
        review(11, login="peer", state="COMMENTED", head=OLD, association="MEMBER")
        | {"body": "", "submittedAt": "2026-10-01T11:00:00Z"}
    )
    old_dismissed = rest_review(
        review(12, login="peer", state="DISMISSED", head=OLD, association="MEMBER")
        | {"body": "", "submittedAt": "2026-10-01T11:00:00Z"}
    )

    root_comments = [[{"id": 4713500002, "pull_request_review_id": 11, "in_reply_to_id": None}]]
    for page, bad_or_root, expected_prov in (
        ([head_formal, old_empty], root_comments, "inline_root"),
        ([head_formal, old_empty], OSError("comments unavailable"), "unknown"),
        ([head_formal, old_dismissed], OSError("should not fetch comments"), None),
    ):
        run, _ = _provider_with_comments([page], bad_or_root, pr_author="cocolord")
        scan = scan_github_pull_request_targets(
            repository="owner/repo",
            exact_heads=[f"42@{HEAD}"],
            wait_for_ci=False,
            run_gh_json=run,
        )
        if expected_prov is not None:
            annotated = {r["id"]: r for r in scan["pull_requests"][0]["reviews"]}
            assert annotated[11]["comment_provenance"] == expected_prov

        for family in (None, "gpt"):
            kwargs = {"reviewer_model_family": family} if family else {}
            packet_data = build_pr_review_packet(
                pull_requests=scan["pull_requests"],
                source="github_cli_exact_targets",
                source_scan=scan,
                repository="owner/repo",
                limit=10,
                state_filter="all",
                reviewer_login="loopx-agent",
                wait_for_ci=False,
                **kwargs,
            )
            row = packet_data["pull_requests"][0]
            assert row["review_action_kind"] is not None
            assert "independent_review" not in row


def test_earlier_or_unrelated_old_head_empty_commented_skips_comments_fetch():
    from loopx.capabilities.pr_review_queue.github_source import scan_github_pull_request_targets

    earlier_old = rest_review(
        review(9, login="peer", state="COMMENTED", head=OLD, association="MEMBER")
        | {"body": "", "submittedAt": "2026-10-01T09:00:00Z"}
    )
    head_formal = rest_review(
        review(10, login="peer", state="APPROVED", head=HEAD, association="MEMBER")
        | {"body": "Looks good.", "submittedAt": "2026-10-01T10:00:00Z"}
    )
    unrelated_old = rest_review(
        review(8, login="other", state="COMMENTED", head=OLD, association="CONTRIBUTOR")
        | {"body": "", "submittedAt": "2026-10-01T11:00:00Z"}
    )

    for page in ([head_formal], [earlier_old, head_formal], [earlier_old, head_formal, unrelated_old]):
        run, calls = _provider_with_comments([page], OSError("unexpected comments call"), pr_author="cocolord")
        scan = scan_github_pull_request_targets(
            repository="owner/repo",
            exact_heads=[f"42@{HEAD}"],
            wait_for_ci=False,
            run_gh_json=run,
        )
        assert not any("pulls/42/comments" in str(part) for args in calls for part in args)
        row = build_pr_review_packet(
            pull_requests=scan["pull_requests"],
            source="github_cli_exact_targets",
            source_scan=scan,
            repository="owner/repo",
            limit=10,
            state_filter="all",
            reviewer_login="loopx-agent",
            wait_for_ci=False,
        )["pull_requests"][0]
        assert row["review_action_kind"] is None
        assert row["independent_review"]["review_id"] == 10
        assert row["independent_review"]["source_complete"] is True
