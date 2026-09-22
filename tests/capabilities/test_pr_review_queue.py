from __future__ import annotations

from copy import deepcopy

from loopx.capabilities.pr_review_queue import (
    DEFAULT_REVIEW_PRIORITY,
    PullRequestReviewPriority,
    build_pull_request_review_queue_observation,
    build_scheduling_policy,
    materialize_review_execution,
    scheduling_tier,
)
from loopx.capabilities.pr_review_queue.readiness_observation import (
    MERGE_READINESS_OBSERVATION_SCHEMA_VERSION,
    observation_key,
    readiness_material_fingerprint,
    readiness_material_state,
)


def _concluded_item(
    *,
    number: int = 1,
    state: str = "OPEN",
    conclusion: dict[str, object] | None = None,
    draft: bool = False,
    decision: str = "REVIEW_REQUIRED",
) -> dict[str, object]:
    return {
        "number": number,
        "state": state,
        "head_oid": f"{number:040d}",
        "is_draft": draft,
        "review_decision": decision,
        "review_conclusion": conclusion
        if conclusion is not None
        else {"valid": True, "state": "COMMENTED", "verdict": "APPROVE"},
    }


def _action_kind(item: dict[str, object]) -> str | None:
    return materialize_review_execution(
        item, fresh_audit_exact_heads=set()
    )["review_action_kind"]


def _pr(
    number: int,
    *,
    head: str | None = None,
    decision: str = "REVIEW_REQUIRED",
    draft: bool = False,
    merge_state: str = "CLEAN",
    failures: list[str] | None = None,
    author_owned: bool = False,
) -> dict[str, object]:
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/owner/repo/pull/{number}",
        "state": "OPEN",
        "head_oid": head or f"{number:040d}",
        "review_decision": decision,
        "is_draft": draft,
        "merge_state": merge_state,
        "author_owned": author_owned,
        "checks": {
            "counts": {
                "success": 1,
                "failure": len(failures or []),
                "pending": 0,
                "unknown": 0,
            },
            "failures": failures or [],
            "pending": [],
        },
    }


def _observe(
    items: list[dict[str, object]],
    *,
    complete: bool = True,
    previous: dict[str, object] | None = None,
    handled: list[str] | None = None,
    projected: list[str] | None = None,
    review_priority: object = DEFAULT_REVIEW_PRIORITY,
) -> dict[str, object]:
    return build_pull_request_review_queue_observation(
        repository="owner/repo",
        pull_requests=items,
        result_completeness={"complete": complete},
        previous_observation=previous,
        handled_exact_heads=handled or [],
        projected_exact_heads=projected or [],
        review_priority=review_priority,
    )


def test_incomplete_queue_is_not_observed_and_preserves_baseline() -> None:
    baseline = _observe([_pr(1)])
    result = _observe([_pr(1), _pr(2)], complete=False, previous=baseline)

    assert result["observation_state"] == "not_observed"
    assert result["queue_fingerprint"] is None
    assert result["previous_queue_fingerprint"] == baseline["queue_fingerprint"]
    assert result["baseline_preserved"] is True
    assert [item["number"] for item in result["items"]] == [1]
    assert result["queue_size"] is None
    assert result["candidate_count"] == 0
    assert result["candidate"] is None
    assert result["pending_candidate_exact_head"] == f"1@{1:040d}"

    recovered = _observe([_pr(1)], previous=result)
    assert recovered["observation_state"] == "observed_unchanged"
    assert recovered["candidate"]["number"] == 1

    advanced = _observe(
        [_pr(1), _pr(2)],
        previous=result,
        handled=[f"1@{1:040d}"],
    )
    assert advanced["candidate"]["number"] == 2


def test_initial_complete_observation_selects_one_exact_head_candidate() -> None:
    result = _observe([_pr(1), _pr(2)])

    assert result["observation_state"] == "material_transition"
    assert result["changed_pr_numbers"] == [1, 2]
    assert result["candidate_count"] == 1
    assert result["repository"] == "owner/repo"
    assert result["queue_size"] == 2
    assert all(
        set(item)
        == {"number", "fingerprint", "head_oid", "review_decision"}
        | {"review_action_kind"}
        for item in result["items"]
    )
    candidate = result["candidate"]
    assert candidate["number"] == 1
    assert candidate["head_oid"] == f"{1:040d}"
    todo = candidate["todo_preview"]
    assert todo["action_kind"] == "review_pull_request_exact_head"
    assert todo["task_repository"] == "git:github.com/owner/repo"
    assert todo["required_capabilities"] == ["network", "external_evidence_poll"]
    assert result["write_authority_granted"] is False
    assert result["external_write_performed"] is False


def test_other_developer_owned_candidate_precedes_owner_by_default() -> None:
    result = _observe([_pr(1), _pr(2, author_owned=True)])

    assert result["candidate"]["number"] == 1
    assert result["candidate_selection_reason"] == "other_developer_owned_first"
    assert result["scheduling_policy"]["review_priority"] == (
        PullRequestReviewPriority.OTHER_DEVELOPERS_FIRST.value
    )
    assert result["scheduling_policy"]["owner_first_active"] is False


def test_authenticated_developer_owned_candidate_precedes_other_developer_in_owner_mode() -> None:
    result = _observe(
        [_pr(1), _pr(2, author_owned=True)],
        review_priority=PullRequestReviewPriority.OWNER_FIRST,
    )

    assert result["candidate"]["number"] == 2
    assert result["candidate_selection_reason"] == "authenticated_developer_owned_first"
    assert result["scheduling_policy"]["owner_first_active"] is True


def test_scheduling_policy_tiers_match_machine_lane_values() -> None:
    policy = build_scheduling_policy(
        authenticated_developer_login="maintainer",
        review_priority=PullRequestReviewPriority.OWNER_FIRST,
    )

    for declared_tier in policy["ordered_tiers"]:
        for lane in declared_tier["lanes"]:
            assert scheduling_tier(
                {"scheduling_lane": lane}, review_priority="owner-first"
            ) == declared_tier["tier"]


def test_community_fast_feedback_does_not_preempt_unprojected_owner() -> None:
    first = _observe(
        [_pr(1, decision="CHANGES_REQUESTED"), _pr(2, author_owned=True)],
        review_priority="owner-first",
    )

    changed = _observe(
        [
            _pr(1, head="f" * 40, decision="CHANGES_REQUESTED"),
            _pr(2, author_owned=True),
        ],
        previous=first,
        review_priority="owner-first",
    )

    assert changed["changed_pr_numbers"] == [1]
    assert changed["candidate"]["number"] == 2
    assert changed["candidate_selection_reason"] == "authenticated_developer_owned_first"


def test_unacknowledged_candidate_replays_on_unchanged_observation() -> None:
    first = _observe([_pr(1), _pr(2)])
    repeated = _observe([_pr(1), _pr(2)], previous=first)

    assert repeated["observation_state"] == "observed_unchanged"
    assert repeated["changed_pr_numbers"] == []
    assert repeated["candidate"]["number"] == 1
    assert repeated["pending_candidate_exact_head"] == f"1@{1:040d}"
    assert repeated["projected_candidate_exact_heads"] == []


def test_round_robin_rotates_only_after_explicit_projection_ack() -> None:
    first = _observe([_pr(1), _pr(2), _pr(3)])
    assert first["candidate"]["number"] == 1
    assert first["projected_candidate_exact_heads"] == []

    second = _observe(
        [_pr(1), _pr(2), _pr(3)],
        previous=first,
        projected=[f"1@{1:040d}"],
    )
    assert second["candidate"]["number"] == 2
    assert second["projected_candidate_exact_heads"] == [f"1@{1:040d}"]

    third = _observe(
        [_pr(1), _pr(2), _pr(3)],
        previous=second,
        projected=[f"2@{2:040d}"],
    )
    assert third["candidate"]["number"] == 3
    assert third["projected_candidate_exact_heads"] == [
        f"1@{1:040d}",
        f"2@{2:040d}",
    ]

    exhausted = _observe(
        [_pr(1), _pr(2), _pr(3)],
        previous=third,
        projected=[f"3@{3:040d}"],
    )
    assert exhausted["candidate"] is None
    assert exhausted["pending_candidate_exact_head"] == f"3@{3:040d}"
    assert exhausted["projected_candidate_count"] == 3


def test_round_robin_accepts_handled_rotated_candidate() -> None:
    first = _observe([_pr(1), _pr(2)])
    second = _observe([_pr(1), _pr(2)], previous=first, projected=[f"1@{1:040d}"])

    assert second["candidate"]["number"] == 2
    handled_second = _observe(
        [_pr(1), _pr(2)],
        previous=second,
        handled=[f"2@{2:040d}"],
    )
    assert handled_second["handled_exact_heads"] == [f"2@{2:040d}"]
    assert handled_second["candidate"] is None
    assert handled_second["projected_candidate_exact_heads"] == [f"1@{1:040d}"]


def test_handled_exact_head_advances_unchanged_backlog() -> None:
    first = _observe([_pr(1), _pr(2)], review_priority="owner-first")
    handled = [f"1@{1:040d}"]
    repeated = _observe(
        [_pr(1), _pr(2)],
        previous=first,
        handled=handled,
        review_priority="owner-first",
    )

    assert repeated["observation_state"] == "observed_unchanged"
    assert repeated["changed_pr_numbers"] == []
    assert repeated["handled_exact_heads"] == handled
    assert repeated["candidate"]["number"] == 2
    assert repeated["candidate_selection_reason"] == "age_fair_backlog_progression"

    still_pending = _observe(
        [_pr(1), _pr(2)], previous=repeated, review_priority="owner-first"
    )
    assert still_pending["observation_state"] == "observed_unchanged"
    assert still_pending["candidate"]["number"] == 2
    assert still_pending["pending_candidate_exact_head"] == f"2@{2:040d}"


def test_handled_changed_pr_does_not_strand_unchanged_backlog() -> None:
    first = _observe([_pr(1), _pr(2)], review_priority="owner-first")
    result = _observe(
        [_pr(1, decision="CHANGES_REQUESTED"), _pr(2)],
        previous=first,
        handled=[f"1@{1:040d}"],
        review_priority="owner-first",
    )

    assert result["observation_state"] == "material_transition"
    assert result["changed_pr_numbers"] == [1]
    assert result["candidate"]["number"] == 2
    assert result["candidate_selection_reason"] == "age_fair_backlog_progression"


def test_new_head_reopens_a_previously_handled_pr() -> None:
    first = _observe([_pr(1), _pr(2)], review_priority="owner-first")
    result = _observe(
        [_pr(1, head="f" * 40), _pr(2)],
        previous=first,
        handled=[f"1@{1:040d}"],
        review_priority="owner-first",
    )

    assert result["candidate"]["number"] == 1
    assert result["candidate"]["head_oid"] == "f" * 40
    assert result["candidate_selection_reason"] == "age_fair_backlog_progression"
    assert result["handled_exact_heads"] == []


def test_closed_handled_pr_advances_and_prunes_cursor() -> None:
    first = _observe([_pr(1), _pr(2)], review_priority="owner-first")
    result = _observe(
        [_pr(2)],
        previous=first,
        handled=[f"1@{1:040d}"],
        review_priority="owner-first",
    )

    assert result["removed_pr_numbers"] == ["1"]
    assert result["candidate"]["number"] == 2
    assert result["candidate_selection_reason"] == "age_fair_backlog_progression"
    assert result["handled_exact_heads"] == []


def test_handled_exact_head_must_be_number_and_full_oid() -> None:
    try:
        _observe([_pr(1)], handled=["1@short"])
    except ValueError as exc:
        assert "NUMBER@HEAD_OID" in str(exc)
    else:
        raise AssertionError("invalid handled exact head was accepted")


def test_handled_exact_head_must_match_prior_candidate() -> None:
    first = _observe([_pr(1), _pr(2)])
    try:
        _observe([_pr(1), _pr(2)], previous=first, handled=[f"2@{2:040d}"])
    except ValueError as exc:
        assert "prior candidate" in str(exc)
    else:
        raise AssertionError("an unselected exact head was marked handled")


def test_exact_review_fingerprint_change_selects_changed_pr_only() -> None:
    first = _observe([_pr(1), _pr(2)])
    first_acknowledged = _observe(
        [_pr(1), _pr(2)], previous=first, projected=[f"1@{1:040d}"]
    )
    changed = [_pr(1), _pr(2, decision="CHANGES_REQUESTED", head="f" * 40)]
    result = _observe(changed, previous={"autonomous_review": first_acknowledged})

    assert result["observation_state"] == "material_transition"
    assert result["changed_pr_numbers"] == [2]
    assert result["candidate"]["number"] == 2
    assert (
        result["candidate"]["todo_preview"]["action_kind"]
        == "rereview_pull_request_exact_head"
    )


def test_check_only_change_does_not_preempt_older_backlog() -> None:
    first = _observe([_pr(1), _pr(2), _pr(3)], review_priority="owner-first")
    first_acknowledged = _observe(
        [_pr(1), _pr(2), _pr(3)],
        previous=first,
        projected=[f"1@{1:040d}"],
        review_priority="owner-first",
    )
    changed = [_pr(1), _pr(2), _pr(3, failures=["pytest"])]

    result = _observe(
        changed, previous=first_acknowledged, review_priority="owner-first"
    )

    assert result["observation_state"] == "material_transition"
    assert result["changed_pr_numbers"] == [3]
    assert result["candidate"]["number"] == 2
    assert result["candidate_selection_reason"] == "age_fair_backlog_progression"


def test_new_head_after_changes_requested_gets_one_fast_feedback_slot() -> None:
    first = _observe(
        [_pr(1, decision="CHANGES_REQUESTED"), _pr(2)],
        review_priority="owner-first",
    )

    result = _observe(
        [_pr(1, head="f" * 40, decision="CHANGES_REQUESTED"), _pr(2)],
        previous=first,
        review_priority="owner-first",
    )

    assert result["candidate"]["number"] == 1
    assert result["candidate"]["head_oid"] == "f" * 40
    assert result["candidate_selection_reason"] == "author_response_fast_feedback"


def test_check_draft_and_mergeability_changes_are_material() -> None:
    first = _observe([_pr(1), _pr(2)])
    first_acknowledged = _observe(
        [_pr(1), _pr(2)], previous=first, projected=[f"1@{1:040d}"]
    )
    changed = [_pr(1), _pr(2, merge_state="BLOCKED", failures=["pytest"])]
    result = _observe(changed, previous=first_acknowledged)
    assert result["changed_pr_numbers"] == [2]
    assert result["candidate"]["number"] == 2

    draft = deepcopy(changed)
    draft[1]["is_draft"] = True
    draft_result = _observe(draft, previous=result)
    assert draft_result["observation_state"] == "material_transition"
    assert draft_result["changed_pr_numbers"] == [2]
    assert draft_result["candidate"] is None


def test_queue_fingerprint_is_repository_scoped() -> None:
    first = build_pull_request_review_queue_observation(
        repository="owner/one",
        pull_requests=[_pr(1)],
        result_completeness={"complete": True},
    )
    second = build_pull_request_review_queue_observation(
        repository="owner/two",
        pull_requests=[_pr(1)],
        result_completeness={"complete": True},
        previous_observation=first,
    )

    assert second["observation_state"] == "material_transition"
    assert second["queue_fingerprint"] != first["queue_fingerprint"]
    assert second["candidate"]["repository"] == "owner/two"


def test_approved_transition_routes_to_merge_policy_without_granting_it() -> None:
    first = _observe([_pr(1)])
    approved = _observe([_pr(1, decision="APPROVED")], previous=first)

    todo = approved["candidate"]["todo_preview"]
    assert todo["action_kind"] == "qualify_pull_request_merge_readiness"
    assert "route any merge through repository policy" in todo["text"]
    assert approved["write_authority_granted"] is False


def test_open_head_without_observation_routes_to_typed_merge_readiness() -> None:
    """An approval owes the pre-merge gate until its material state is observed.

    GitHub blocks self-approval, so an author-owned approval is stored as a
    COMMENTED review. Keying the queue on the formal review state counted such a
    head as concluded and hid approved heads that had gone behind, conflicted,
    lost checks, or become blocked.
    """

    assert (
        _action_kind(_concluded_item())
        == "qualify_pull_request_merge_readiness"
    )
    assert (
        _action_kind(
            _concluded_item(
                conclusion={"valid": True, "state": "APPROVED", "verdict": "APPROVE"}
            )
        )
        == "qualify_pull_request_merge_readiness"
    )

    author_owned_request_changes = _concluded_item(
        conclusion={
            "valid": True,
            "state": "COMMENTED",
            "verdict": "REQUEST_CHANGES",
        }
    )
    assert _action_kind(author_owned_request_changes) is None
    assert _action_kind(_concluded_item(state="MERGED")) is None
    assert _action_kind(_concluded_item(draft=True)) is None

    invalid = _concluded_item(
        conclusion={"valid": False, "state": None, "verdict": None}
    )
    assert _action_kind(invalid) == "review_pull_request_exact_head"
    changes_requested = _concluded_item(
        conclusion={"valid": False, "state": None, "verdict": None},
        decision="CHANGES_REQUESTED",
    )
    assert _action_kind(changes_requested) == "rereview_pull_request_exact_head"


def test_readiness_observation_suppresses_only_an_exact_material_match() -> None:
    repository = "owner/repo"
    threads = {
        "complete": True,
        "total_count": 1,
        "unresolved_count": 0,
    }
    item = _concluded_item(
        conclusion={
            "valid": True,
            "status": "valid",
            "state": "APPROVED",
            "verdict": "APPROVE",
            "review_commit": "1" * 40,
            "invalid_reasons": [],
        },
        decision="APPROVED",
    ) | {
        "base_oid": "a" * 40,
        "merge_state": "CLEAN",
        "checks": {
            "total": 1,
            "counts": {"success": 1, "failure": 0, "pending": 0, "unknown": 0},
            "failures": [],
            "pending": [],
        },
        "wait_for_ci": True,
    }
    material = readiness_material_state(
        repository=repository,
        item=item,
        review_threads=threads,
        wait_for_ci=True,
    )
    exact_head = f"1@{item['head_oid']}"
    observations = {
        observation_key(repository, exact_head): {
            "schema_version": MERGE_READINESS_OBSERVATION_SCHEMA_VERSION,
            "material_fingerprint": readiness_material_fingerprint(material),
        }
    }

    unchanged = materialize_review_execution(
        item,
        fresh_audit_exact_heads=set(),
        readiness_observations=observations,
        repository=repository,
        review_threads=threads,
    )
    assert unchanged["review_action_kind"] is None
    assert (
        unchanged["merge_readiness_observation"]["observation_state"]
        == "observed_unchanged"
    )

    mutations = []
    changed_head = deepcopy(item)
    changed_head["head_oid"] = "2" * 40
    mutations.append((changed_head, threads))
    changed_base = deepcopy(item)
    changed_base["base_oid"] = "b" * 40
    mutations.append((changed_base, threads))
    changed_review = deepcopy(item)
    changed_review["review_conclusion"]["review_commit"] = "2" * 40
    mutations.append((changed_review, threads))
    changed_checks = deepcopy(item)
    changed_checks["checks"]["counts"]["success"] = 0
    changed_checks["checks"]["counts"]["failure"] = 1
    changed_checks["checks"]["failures"] = ["merge-gate"]
    mutations.append((changed_checks, threads))
    changed_threads = dict(threads, unresolved_count=1)
    mutations.append((deepcopy(item), changed_threads))
    changed_merge_state = deepcopy(item)
    changed_merge_state["merge_state"] = "BEHIND"
    mutations.append((changed_merge_state, threads))

    for changed_item, changed_thread_state in mutations:
        execution = materialize_review_execution(
            changed_item,
            fresh_audit_exact_heads=set(),
            readiness_observations=observations,
            repository=repository,
            review_threads=changed_thread_state,
        )
        assert execution["review_action_kind"] == "qualify_pull_request_merge_readiness"
        if changed_item["head_oid"] == item["head_oid"]:
            assert (
                execution["merge_readiness_observation"]["observation_state"]
                == "material_transition"
            )


def test_review_backlog_keeps_active_cadence_until_all_handled() -> None:
    first = _observe([_pr(1), _pr(2), _pr(3)])

    assert first["review_backlog"]["actionable_unhandled_count"] == 3
    assert first["review_backlog"]["recommended_poll_interval_minutes"] == 3
    assert first["review_backlog"]["recommended_cadence"] == "active_review"

    after_one = _observe(
        [_pr(1), _pr(2), _pr(3)],
        previous=first,
        handled=[f"1@{1:040d}"],
    )
    assert after_one["candidate"]["number"] == 2
    assert after_one["review_backlog"]["actionable_unhandled_count"] == 2
    assert after_one["review_backlog"]["recommended_poll_interval_minutes"] == 3

    after_two = _observe(
        [_pr(1), _pr(2), _pr(3)],
        previous=after_one,
        handled=[f"2@{2:040d}"],
    )
    assert after_two["candidate"]["number"] == 3
    assert after_two["review_backlog"]["actionable_unhandled_count"] == 1
    assert after_two["review_backlog"]["recommended_poll_interval_minutes"] == 3

    after_three = _observe(
        [_pr(1), _pr(2), _pr(3)],
        previous=after_two,
        handled=[f"3@{3:040d}"],
    )
    assert after_three["candidate"] is None
    assert after_three["review_backlog"]["actionable_unhandled_count"] == 0
    assert after_three["review_backlog"]["recommended_poll_interval_minutes"] == 15
    assert after_three["review_backlog"]["recommended_cadence"] == "quiet_wait"


def test_review_backlog_ignores_draft_prs_for_active_cadence() -> None:
    mixed = _observe([_pr(1, draft=True), _pr(2)])

    assert mixed["review_backlog"]["actionable_unhandled_count"] == 1
    assert mixed["review_backlog"]["recommended_poll_interval_minutes"] == 3

    only_draft = _observe([_pr(1, draft=True)])

    assert only_draft["review_backlog"]["actionable_unhandled_count"] == 0
    assert only_draft["review_backlog"]["recommended_poll_interval_minutes"] == 15
    assert only_draft["review_backlog"]["recommended_cadence"] == "quiet_wait"


def test_incomplete_observation_preserves_active_backlog_when_pending() -> None:
    baseline = _observe([_pr(1), _pr(2)])
    incomplete = _observe([_pr(1), _pr(2)], complete=False, previous=baseline)

    assert incomplete["review_backlog"]["actionable_unhandled_count"] == 2
    assert incomplete["review_backlog"]["recommended_poll_interval_minutes"] == 3


def test_legacy_emission_cursors_are_replayed_instead_of_stranded() -> None:
    legacy = _observe([_pr(1), _pr(2)])
    legacy["schema_version"] = "pull_request_review_queue_observation_v0"
    legacy.pop("candidate_projection_ack_semantics")
    legacy["candidate"] = None
    legacy["pending_candidate_exact_head"] = f"2@{2:040d}"
    legacy["projected_candidate_exact_heads"] = [f"1@{1:040d}", f"2@{2:040d}"]
    legacy["projected_candidate_count"] = 2

    recovered = _observe([_pr(1), _pr(2)], previous=legacy)

    assert recovered["candidate"]["number"] == 1
    assert recovered["projected_candidate_exact_heads"] == []
    assert recovered["candidate_projection_ack_semantics"] == "explicit_v1"


def test_projection_ack_must_match_prior_candidate() -> None:
    first = _observe([_pr(1), _pr(2)])
    try:
        _observe(
            [_pr(1), _pr(2)],
            previous=first,
            projected=[f"2@{2:040d}"],
        )
    except ValueError as exc:
        assert "prior candidate" in str(exc)
    else:
        raise AssertionError("an unselected exact head was acknowledged as projected")
