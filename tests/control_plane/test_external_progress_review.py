from __future__ import annotations

import hashlib

from loopx.control_plane.work_items.external_progress_review import (
    EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND,
    external_progress_review_trigger,
)
from loopx.control_plane.work_items.progress_observation import (
    normalize_progress_observation,
    semantic_progress_delta,
)
from loopx.control_plane.work_items.autonomous_replan_ack import (
    autonomous_replan_ack_recorded,
)

AGENT = "worker"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run(sequence: int, *, agent: str = AGENT, turn: str | None = None, ack: bool = False) -> dict[str, object]:
    row: dict[str, object] = {
        "classification": "bounded_delivery",
        "generated_at": f"2026-09-21T00:00:{sequence:02d}Z",
        "agent_id": agent,
        "progress_observation": {
            "schema_version": "typed_progress_observation_v0",
            "result_class": "advanced",
            "hypothesis_id": f"hypothesis-{sequence}",
        },
    }
    if turn is not None:
        row["turn_instance_id"] = turn
    if ack:
        row["autonomous_replan_ack"] = {
            "recorded": True,
            "semantic_delta": {"accepted": True},
        }
    return row


def receipt(
    sequence: int,
    *,
    turn: str | None = None,
    agent: str = AGENT,
    status: str = "completed",
    noul: bool | None = True,
    choice: bool | None = True,
    evidence: str | None = None,
    contract: str = "contract-1",
    reason: str | None = None,
    todo: str | None = None,
) -> dict[str, object]:
    return {
        "receipt_id": _digest(f"event-{sequence}"),
        "event_id": _digest(f"event-{sequence}"),
        "evidence_id": _digest(evidence or f"evidence-{sequence}"),
        "contract_revision": _digest(contract),
        "sequence": sequence,
        "status": status,
        "reason": reason,
        "run": {
            "turn_instance_id": turn,
            "generated_at": f"2026-09-21T00:00:{sequence:02d}Z",
            "agent_id": agent,
            "todo_id": todo,
        },
        "judgments": {"choice": None, "noul": None},
        "drift_signal": {"noul": noul, "choice": choice},
    }


def trigger(runs, receipts, **overrides):
    options = {
        "receipts": receipts,
        "agent_id": AGENT,
        "threshold": 2,
        "signal": "noul",
        "contract_revision": _digest("contract-1"),
        "ack_recorded": autonomous_replan_ack_recorded,
    }
    options.update(overrides)
    return external_progress_review_trigger(runs, **options)


def test_two_consecutive_completed_drift_receipts_trigger() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    result = trigger(runs, [receipt(2, turn="t2"), receipt(1, turn="t1")])
    assert result is not None
    assert result["kind"] == EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND
    assert result["run_count"] == 2
    assert result["latest_generated_at"] == "2026-09-21T00:00:02Z"
    assert result["oldest_counted_generated_at"] == "2026-09-21T00:00:01Z"
    assert result["frontier_identity"] == "progress_review:" + _digest("evidence-2")
    assert result["agent_id"] == AGENT
    assert "delta" not in result and "text" not in result
    assert result["model_authority"] == "none"
    # The newest counted run's typed observation is bound as the discharge baseline,
    # and every distinct typed claim in the window travels with the trigger.
    assert result["progress_baseline"] == normalize_progress_observation(runs[0]["progress_observation"])
    assert result["progress_fingerprint"] == result["progress_baseline"]["fingerprint"]
    assert result["progress_window"] == [
        normalize_progress_observation(runs[0]["progress_observation"]),
        normalize_progress_observation(runs[1]["progress_observation"]),
    ]


def test_bound_baseline_rejects_a_repeated_observation_and_accepts_new_evidence() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    for row in runs:
        row["progress_observation"]["surface_id"] = "surface-retry"
        row["progress_observation"]["evidence_ids"] = [f"evidence-{row['generated_at'][-3:-1]}"]
    result = trigger(runs, [receipt(2, turn="t2"), receipt(1, turn="t1")])
    assert result is not None
    baseline = result["progress_baseline"]
    identical = dict(runs[0]["progress_observation"])
    assert semantic_progress_delta(identical, baseline=baseline)["accepted"] is False
    same_hypothesis_new_evidence = {**identical, "evidence_ids": ["evidence-fresh"]}
    assert semantic_progress_delta(same_hypothesis_new_evidence, baseline=baseline)["accepted"] is False
    new_hypothesis = {**identical, "hypothesis_id": "hypothesis-next", "evidence_ids": ["evidence-fresh"]}
    assert semantic_progress_delta(new_hypothesis, baseline=baseline)["accepted"] is True
    # The codec states evidence novelty as a fact; the outcome owner decides
    # which obligation sources require it behind a renamed identifier.
    assert semantic_progress_delta(identical, baseline=baseline)["evidence_novel"] is False
    assert semantic_progress_delta(same_hypothesis_new_evidence, baseline=baseline)["evidence_novel"] is True
    renamed_only = {**identical, "hypothesis_id": "hypothesis-renamed"}
    renamed_delta = semantic_progress_delta(renamed_only, baseline=baseline)
    assert renamed_delta["delta_kinds"] == ["new_hypothesis"] and renamed_delta["evidence_novel"] is False
    new_blocker = {
        "schema_version": "typed_progress_observation_v0",
        "result_class": "blocked",
        "blocker_id": "blocker-new",
        "evidence_ids": ["evidence-blocker"],
    }
    assert semantic_progress_delta(new_blocker, baseline=baseline)["accepted"] is True
    # Without a baseline the identical observation would have passed as new work.
    assert semantic_progress_delta(identical, baseline=None)["accepted"] is True


def test_no_typed_observation_on_the_counted_window_raises_nothing() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    for row in runs:
        row.pop("progress_observation")
    assert trigger(runs, [receipt(2, turn="t2"), receipt(1, turn="t1")]) is None


def test_neutral_bookkeeping_rows_are_neither_counted_nor_gaps() -> None:
    void = {"classification": "quota_slot_voided", "generated_at": "2026-09-21T00:00:03Z"}
    spend = {"classification": "quota_slot_spent", "generated_at": "2026-09-21T00:00:01Z", "agent_id": AGENT}
    runs = [void, run(2, turn="t2"), spend, run(1, turn="t1")]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    assert trigger(runs, receipts) is None
    result = trigger(runs, receipts, neutral_classifications={"quota_slot_voided", "quota_slot_spent"})
    assert result is not None and result["run_count"] == 2


def test_self_declared_advanced_alone_is_not_enough_without_receipts() -> None:
    assert trigger([run(2, turn="t2"), run(1, turn="t1")], []) is None


def test_unknown_abstained_or_failed_receipt_breaks_the_streak() -> None:
    runs = [run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    for middle in (
        receipt(2, turn="t2", noul=None, choice=None, status="abstained"),
        receipt(2, turn="t2", noul=None, choice=None, status="failed"),
        receipt(2, turn="t2", noul=False),
    ):
        receipts = [receipt(3, turn="t3"), middle, receipt(1, turn="t1")]
        assert trigger(runs, receipts) is None


def test_missing_receipt_for_a_transition_breaks_the_streak() -> None:
    runs = [run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    assert trigger(runs, [receipt(3, turn="t3"), receipt(1, turn="t1")]) is None


def test_acknowledged_replan_rearms_the_trigger() -> None:
    runs = [run(3, turn="t3"), run(2, turn="t2", ack=True), run(1, turn="t1")]
    receipts = [receipt(3, turn="t3"), receipt(2, turn="t2"), receipt(1, turn="t1")]
    assert trigger(runs, receipts) is None
    runs = [run(4, turn="t4"), run(3, turn="t3"), run(2, turn="t2", ack=True)]
    receipts = [receipt(4, turn="t4"), receipt(3, turn="t3"), receipt(2, turn="t2")]
    assert trigger(runs, receipts) is not None


def test_same_transition_receipts_prefer_the_later_evaluation_over_the_local_sequence() -> None:
    """A re-initialised observer restarts `sequence` at zero; recency is the clock."""

    runs = [run(2, turn="t2"), run(1, turn="t1")]
    stale_high_sequence = {**receipt(9, turn="t2", noul=False, choice=False), "recorded_at": 10.0}
    fresh_low_sequence = {**receipt(0, turn="t2"), "recorded_at": 20.0}
    receipts = [stale_high_sequence, fresh_low_sequence, {**receipt(1, turn="t1"), "recorded_at": 5.0}]
    result = trigger(runs, receipts)
    assert result is not None and result["run_count"] == 2
    # Reversed recency: the on-goal verdict is the later evaluation and ends the streak.
    stale_high_sequence["recorded_at"], fresh_low_sequence["recorded_at"] = 20.0, 10.0
    assert trigger(runs, receipts) is None


def test_same_turn_retry_and_same_evidence_count_once() -> None:
    runs = [run(3, turn="t2"), run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    result = trigger(runs, receipts)
    assert result is not None and result["run_count"] == 2
    same_evidence = [receipt(2, turn="t2", evidence="shared"), receipt(1, turn="t1", evidence="shared")]
    assert trigger([run(2, turn="t2"), run(1, turn="t1")], same_evidence) is None


def test_retry_claim_remains_in_external_obligation_and_cannot_be_replayed() -> None:
    """One logical Turn counts once, but all of its typed claims bind the ACK."""

    newest_retry = run(3, turn="t2")
    newest_retry.pop("progress_observation")
    earlier_retry = run(2, turn="t2")
    first_turn = run(1, turn="t1")
    for row in (earlier_retry, first_turn):
        row["progress_observation"]["surface_id"] = "retry"
        row["progress_observation"]["evidence_ids"] = [f"evidence-{row['generated_at'][-3:-1]}"]
    runs = [newest_retry, earlier_retry, first_turn]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    evidence = trigger(runs, receipts)
    assert evidence is not None and evidence["run_count"] == 2
    assert [claim["hypothesis_id"] for claim in evidence["progress_window"]] == [
        "hypothesis-2", "hypothesis-1",
    ]
    obligation = autonomous_replan_obligation_from_runs(
        runs, agent_todos=None, external_progress_review=_context("assist", receipts),
    )
    assert obligation is not None and obligation["triggers"][0]["kind"] == EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND
    replay = semantic_delta_from_writeback(
        obligation=obligation, progress_observation=earlier_retry["progress_observation"],
    )
    assert replay["accepted"] is False
    assert replay["satisfying_outcomes"] == []
    pivot = semantic_delta_from_writeback(
        obligation=obligation,
        progress_observation={
            **earlier_retry["progress_observation"], "hypothesis_id": "hypothesis-new",
            "evidence_ids": ["evidence-new"],
        },
    )
    assert pivot["accepted"] is True and pivot["satisfying_outcomes"] == ["new_hypothesis"]
    for mode in ("off", "shadow"):
        assert autonomous_replan_obligation_from_runs(
            runs, agent_todos=None, external_progress_review=_context(mode, receipts),
        ) is None
    assert autonomous_replan_obligation_from_runs(runs, agent_todos=None) is None
    # An accepted ACK within the same Turn still cuts off older retry claims.
    earlier_retry["autonomous_replan_ack"] = {"recorded": True, "semantic_delta": {"accepted": True}}
    assert trigger(runs, receipts) is None


def test_contract_revision_change_invalidates_earlier_receipts() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2", contract="contract-2"), receipt(1, turn="t1")]
    assert trigger(runs, receipts) is None
    # Receipts that agree with each other but not with the pinned goal contract
    # are history, never current evidence.
    old = [receipt(2, turn="t2", contract="contract-0"), receipt(1, turn="t1", contract="contract-0")]
    assert trigger(runs, old) is None
    assert trigger(runs, old, contract_revision=_digest("contract-0")) is not None


def test_assist_without_a_pinned_contract_never_triggers() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    assert trigger(runs, receipts, contract_revision=None) is None
    assert trigger(runs, receipts, contract_revision="") is None


def test_turn_match_requires_exact_agent_and_todo_including_absence() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    assert trigger(runs, [receipt(2, turn="t2", agent="someone-else"), receipt(1, turn="t1")]) is None
    runs_with_todo = [dict(run(2, turn="t2"), todo_id="todo-a"), run(1, turn="t1")]
    assert trigger(runs_with_todo, [receipt(2, turn="t2", todo="todo-b"), receipt(1, turn="t1")]) is None
    assert trigger(runs_with_todo, [receipt(2, turn="t2", todo="todo-a"), receipt(1, turn="t1")]) is not None
    # Missing attribution cannot stand in for a specific bound Todo.
    assert trigger(runs_with_todo, [receipt(2, turn="t2"), receipt(1, turn="t1")]) is None


def test_ambiguous_fallback_identity_is_never_attributed() -> None:
    runs = [run(2), run(1)]
    two_for_one = [receipt(3, evidence="other"), receipt(2), receipt(1)]
    # receipt 3 and 2 have no turn id and share (generated_at, agent) of run 2.
    two_for_one[0]["run"]["generated_at"] = two_for_one[1]["run"]["generated_at"]
    assert trigger(runs, two_for_one) is None


def pending(n: int, turn: str) -> dict[str, object]:
    return receipt(n, turn=turn, status="not_evaluated", noul=None, choice=None, reason="pending_evaluation")


def failed(n: int, turn: str, *, status: str = "failed", reason: str | None = "invalid_response_or_local_io") -> dict[str, object]:
    return receipt(n, turn=turn, status=status, noul=None, choice=None, reason=reason)


def test_formed_streak_survives_newer_unevaluated_transitions() -> None:
    """Absence of a verdict is not evidence that the drift was handled."""

    runs = [run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    streak = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    newest_without_verdict = {
        "pending": pending(3, "t3"),
        "failed": failed(3, "t3"),
        "abstained": failed(3, "t3", status="abstained", reason="insufficient_evidence_or_uncertain"),
        "stale": failed(3, "t3", status="stale", reason="revoked_or_stale_after_response"),
        "not_evaluated": failed(3, "t3", status="not_evaluated", reason="egress_denied"),
        "undecided": receipt(3, turn="t3", noul=None, choice=None),
        "identity_conflict": receipt(3, turn="t3", agent="someone-else"),
        "other_revision": receipt(3, turn="t3", contract="contract-2"),
    }
    for reason, newest in newest_without_verdict.items():
        result = trigger(runs, [newest, *streak])
        assert result is not None, reason
        assert result["run_count"] == 2 and result["consecutive_drift"] == 2
        assert result["unevaluated_transitions"] == {
            "total": 1, "newer_than_latest_drift": 1, "by_reason": {reason: 1},
        }
        # The baseline is the newest typed claim in the window, evaluated or
        # not: acknowledging with a claim already on record is not a pivot.
        assert result["progress_baseline"]["hypothesis_id"] == "hypothesis-3"
        assert result["baseline_generated_at"] == "2026-09-21T00:00:03Z"
        assert [item["hypothesis_id"] for item in result["progress_window"]] == ["hypothesis-3", "hypothesis-2", "hypothesis-1"]
    # No receipt at all for the newest transition.
    result = trigger(runs, streak)
    assert result is not None and result["unevaluated_transitions"]["by_reason"] == {"missing": 1}
    # An ambiguous fallback identity is unattributed, not a break.
    fallback_runs = [run(3), run(2, turn="t2"), run(1, turn="t1")]
    two_for_one = [receipt(4, evidence="other"), receipt(3), *streak]
    two_for_one[0]["run"]["generated_at"] = two_for_one[1]["run"]["generated_at"]
    result = trigger(fallback_runs, two_for_one)
    assert result is not None and result["unevaluated_transitions"]["by_reason"] == {"unattributed": 1}
    # Any depth of outstanding evaluation keeps the obligation open.
    deep_runs = [run(n, turn=f"t{n}") for n in range(6, 0, -1)]
    deep_receipts = [pending(6, "t6"), failed(5, "t5"), pending(4, "t4"), failed(3, "t3"), *streak]
    result = trigger(deep_runs, deep_receipts)
    assert result is not None
    assert result["unevaluated_transitions"] == {
        "total": 4, "newer_than_latest_drift": 4, "by_reason": {"pending": 2, "failed": 2},
    }


def test_formation_requires_a_gap_free_drift_streak() -> None:
    """Before an obligation exists, every counted transition must be evaluated drift."""

    runs = [run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    for gap in (pending(2, "t2"), failed(2, "t2"), receipt(2, turn="t2", noul=None, choice=None)):
        assert trigger(runs, [receipt(3, turn="t3"), gap, receipt(1, turn="t1")]) is None
    assert trigger(runs, [receipt(3, turn="t3"), receipt(1, turn="t1")]) is None
    # Drift verdicts separated by unevaluated transitions never add up.
    runs = [run(n, turn=f"t{n}") for n in range(5, 0, -1)]
    scattered = [receipt(5, turn="t5"), pending(4, "t4"), receipt(3, turn="t3"), failed(2, "t2"), receipt(1, turn="t1")]
    assert trigger(runs, scattered) is None


def test_newer_drift_after_formation_extends_and_rebinds_the_baseline() -> None:
    runs = [run(4, turn="t4"), run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(4, turn="t4"), pending(3, "t3"), receipt(2, turn="t2"), receipt(1, turn="t1")]
    result = trigger(runs, receipts)
    assert result is not None
    assert result["run_count"] == 3 and result["consecutive_drift"] == 2
    assert result["unevaluated_transitions"] == {"total": 1, "newer_than_latest_drift": 0, "by_reason": {"pending": 1}}
    assert result["progress_baseline"]["hypothesis_id"] == "hypothesis-4"
    assert result["frontier_identity"] == "progress_review:" + _digest("evidence-4")
    assert result["oldest_counted_generated_at"] == "2026-09-21T00:00:01Z"


def test_a_newer_completed_on_goal_verdict_ends_the_open_condition() -> None:
    """A positive verdict on a newer transition is evidence; a missing one is not."""

    runs = [run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    on_goal = receipt(3, turn="t3", noul=False, choice=False)
    assert trigger(runs, [on_goal, receipt(2, turn="t2"), receipt(1, turn="t1")]) is None
    runs = [run(4, turn="t4"), run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    assert trigger(runs, [receipt(4, turn="t4"), on_goal, receipt(2, turn="t2"), receipt(1, turn="t1")]) is None
    assert trigger(runs, [pending(4, "t4"), on_goal, receipt(2, turn="t2"), receipt(1, turn="t1")]) is None
    # An on-goal verdict bound to another revision is history, not a verdict.
    stale_on_goal = receipt(3, turn="t3", noul=False, choice=False, contract="contract-0")
    runs = [run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    result = trigger(runs, [stale_on_goal, receipt(2, turn="t2"), receipt(1, turn="t1")])
    assert result is not None and result["unevaluated_transitions"]["by_reason"] == {"other_revision": 1}


def test_baseline_is_the_newest_typed_observation_in_the_window() -> None:
    runs = [run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    runs[0].pop("progress_observation")
    receipts = [receipt(3, turn="t3"), receipt(2, turn="t2"), receipt(1, turn="t1")]
    result = trigger(runs, receipts)
    assert result is not None and result["progress_baseline"]["hypothesis_id"] == "hypothesis-2"
    assert result["baseline_generated_at"] == "2026-09-21T00:00:02Z"
    # An unevaluated newest claim still binds; the evaluated one below does not.
    runs = [run(3, turn="t3"), run(2, turn="t2"), run(1, turn="t1")]
    runs[1].pop("progress_observation")
    result = trigger(runs, [pending(3, "t3"), receipt(2, turn="t2"), receipt(1, turn="t1")])
    assert result is not None and result["progress_baseline"]["hypothesis_id"] == "hypothesis-3"
    # A window with no typed observation at all raises nothing.
    for row in runs:
        row.pop("progress_observation", None)
    assert trigger(runs, [pending(3, "t3"), receipt(2, turn="t2"), receipt(1, turn="t1")]) is None


def test_signal_selection_and_agent_scoping() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2", noul=False, choice=True), receipt(1, turn="t1", noul=False, choice=True)]
    assert trigger(runs, receipts) is None
    assert trigger(runs, receipts, signal="choice") is not None
    assert trigger(runs, receipts, signal="prose") is None
    other = [run(2, agent="other", turn="t2"), run(1, turn="t1")]
    assert trigger(other, [receipt(2, turn="t2", agent="other"), receipt(1, turn="t1")]) is None


def test_fallback_identity_uses_generated_at_and_agent() -> None:
    runs = [run(2), run(1)]
    receipts = [receipt(2), receipt(1)]
    result = trigger(runs, receipts)
    assert result is not None and result["run_count"] == 2
    assert trigger([run(2), run(1)], [receipt(2), receipt(1, agent="someone-else")]) is None


def test_threshold_floor_is_two_and_higher_thresholds_wait() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    assert trigger(runs, receipts, threshold=1) is not None
    assert trigger(runs, receipts, threshold=3) is None


# --- obligation and status wiring -------------------------------------------

from loopx.control_plane.work_items.project_asset import (  # noqa: E402
    attach_active_state_project_asset_fields,
)
from loopx.status import (  # noqa: E402
    autonomous_replan_obligation_from_runs,
    external_progress_review_context,
)


def _context(mode: str, receipts: list[dict[str, object]], *, signal: str = "noul", threshold: int = 2, pin: str | None = _digest("contract-1")) -> dict[str, object]:
    return {
        "policy": {"mode": mode, "signal": signal, "drift_threshold": threshold, "contract_revision": pin},
        "receipts": receipts,
        "summary": {"schema_version": "progress_review_status_v0", "mode": mode, "receipt_count": len(receipts)},
    }


def test_assist_policy_turns_receipts_into_the_existing_obligation() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    obligation = autonomous_replan_obligation_from_runs(
        runs, agent_todos=None, external_progress_review=_context("assist", receipts)
    )
    assert obligation is not None
    assert obligation["required"] is True
    assert obligation["triggers"][0]["kind"] == EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND
    assert obligation["frontier_identity"].startswith("progress_review:")
    assert obligation["external_progress_review"]["run_count"] == 2
    assert obligation["external_progress_review"]["model_authority"] == "none"
    assert obligation["external_progress_review"]["effect"] == "required_obligation_under_goal_policy"
    assert obligation["progress_baseline"] == normalize_progress_observation(runs[0]["progress_observation"])
    assert any("acceptance criterion" in action["text"] for action in obligation["todo_actions"])
    assert "acceptance criterion" in obligation["recommended_action"]
    assert obligation["stop_condition"]


def test_shadow_and_off_policies_never_raise_an_obligation() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    assert (
        autonomous_replan_obligation_from_runs(
            runs, agent_todos=None, external_progress_review=_context("assist", receipts, pin=None)
        )
        is None
    )
    for mode in ("shadow", "off"):
        assert (
            autonomous_replan_obligation_from_runs(
                runs, agent_todos=None, external_progress_review=_context(mode, receipts)
            )
            is None
        )
    assert autonomous_replan_obligation_from_runs(runs, agent_todos=None) is None


def test_typed_fuse_keeps_precedence_over_external_review() -> None:
    fused = []
    for sequence in (2, 1):
        row = run(sequence, turn=f"t{sequence}")
        row["progress_observation"] = {
            "schema_version": "typed_progress_observation_v0",
            "result_class": "unchanged",
            "hypothesis_id": "same",
        }
        fused.append(row)
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    obligation = autonomous_replan_obligation_from_runs(
        fused, agent_todos=None, external_progress_review=_context("assist", receipts)
    )
    assert obligation is not None
    assert obligation["triggers"][0]["kind"] == "typed_progress_repeat"


def test_external_review_precedes_periodic_review_only_in_assist() -> None:
    runs = [run(sequence, turn=f"t{sequence}") for sequence in range(20, 0, -1)]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    plain = autonomous_replan_obligation_from_runs(runs, agent_todos=None)
    shadow = autonomous_replan_obligation_from_runs(
        runs, agent_todos=None, external_progress_review=_context("shadow", receipts)
    )
    assisted = autonomous_replan_obligation_from_runs(
        runs, agent_todos=None, external_progress_review=_context("assist", receipts)
    )
    assert plain is not None and plain["triggers"][0]["kind"] == "periodic_review_due"
    assert shadow == plain
    assert assisted is not None
    assert assisted["triggers"][0]["kind"] == EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND


def test_attach_surfaces_summary_and_binds_review_into_obligation() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    item: dict[str, object] = {"project_asset": {}}
    attached = attach_active_state_project_asset_fields(
        item,
        latest_runs=runs,
        autonomous_replan_obligation_from_runs=autonomous_replan_obligation_from_runs,
        external_progress_review=_context("assist", receipts),
    )
    assert item["external_progress_review"]["receipt_count"] == 2
    assert attached["external_progress_review"]["mode"] == "assist"
    assert item["autonomous_replan_obligation"]["triggers"][0]["kind"] == EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND
    plain: dict[str, object] = {"project_asset": {}}
    attach_active_state_project_asset_fields(
        plain,
        latest_runs=runs,
        autonomous_replan_obligation_from_runs=autonomous_replan_obligation_from_runs,
    )
    assert "external_progress_review" not in plain
    assert "autonomous_replan_obligation" not in plain


def test_context_loader_is_silent_for_off_and_reads_receipts_when_on(tmp_path) -> None:
    from loopx.capabilities.progress_review.receipt import write_progress_review_receipt

    goal = {"id": "ctx-goal", "control_plane": {"progress_review": {"mode": "shadow"}}}
    assert external_progress_review_context({"id": "ctx-goal"}, tmp_path) is None
    assert external_progress_review_context(goal, None) is None
    loaded = external_progress_review_context(goal, tmp_path)
    assert loaded is not None and loaded["receipts"] == [] and loaded["summary"]["receipt_count"] == 0
    stored = {
        **receipt(1, turn="t1"),
        "schema_version": "progress_review_receipt_v0",
        "signal_rule_version": "progress_review_signal_rule_v1",
        "goal_id": "ctx-goal",
        "question_version": "scoped-progress-sentinel-v2",
        "model": "fixture-v1",
        "judgments": {
            "choice": {"relation": "off_goal", "increment": "no_new_evidence"},
            "noul": {"behavior_change": 0.05, "serves_acceptance": 0.04, "evidence_increment": 0.1},
        },
        "label_probability_threshold": 0.6,
        "recorded_at": 1.0,
    }
    write_progress_review_receipt(tmp_path, "ctx-goal", stored)
    loaded = external_progress_review_context(goal, tmp_path)
    assert loaded is not None and loaded["summary"]["receipt_count"] == 1
    assert loaded["summary"]["latest"]["drift_signal"] == {"noul": True, "choice": True}
    # A pinned revision partitions receipts into current and stale.
    pinned = {"id": "ctx-goal", "control_plane": {"progress_review": {"mode": "assist", "contract_revision": _digest("contract-2")}}}
    loaded = external_progress_review_context(pinned, tmp_path)
    assert loaded is not None and loaded["receipts"] == [] and loaded["summary"]["stale_receipts"] == 1
    unpinned = {"id": "ctx-goal", "control_plane": {"progress_review": {"mode": "assist"}}}
    loaded = external_progress_review_context(unpinned, tmp_path)
    assert loaded is not None and loaded["summary"]["assist_blocked_reason"] == "contract_revision_unpinned"


# --- discharge policy through the shared outcome owner --------------------

from loopx.control_plane.work_items.progress_observation import (  # noqa: E402
    replan_writeback_requirements,
    semantic_delta_from_writeback,
)


def _vision(outcome: str = "continue", *, evidence: list[str] | None = None) -> dict[str, object]:
    return {
        "state": "active",
        "vision_patch": {"acceptance_summary": "Retry acceptance still needs a behaviour-changing slice"},
        "path_delta": {"outcome": outcome, "evidence_refs": ["evidence:review"] if evidence is None else evidence},
    }


def test_external_review_discharge_refuses_renamed_identifiers_over_the_same_evidence() -> None:
    runs = [run(2, turn="t2"), run(1, turn="t1")]
    for row in runs:
        row["progress_observation"]["surface_id"] = "retry"
        row["progress_observation"]["evidence_ids"] = [f"evidence-{row['generated_at'][-3:-1]}"]
    obligation = autonomous_replan_obligation_from_runs(
        runs, agent_todos=None,
        external_progress_review=_context("assist", [receipt(2, turn="t2"), receipt(1, turn="t1")]),
    )
    assert obligation is not None
    requirements = replan_writeback_requirements(obligation)
    assert "fresh_vision_path_outcome" in requirements["required_any_of"]
    assert "new_hypothesis" in requirements["required_any_of"]
    assert "--progress-hypothesis-id" in requirements["cli_semantic_args"]
    contract = requirements["writeback_contract"]
    assert contract["identity_outcomes_require_new_evidence"] is True
    assert "--agent-vision-json" in contract["alternative_cli_semantic_args"]

    def qualify(observation=None, vision=None):
        return semantic_delta_from_writeback(
            obligation=obligation, progress_observation=observation, agent_vision=vision,
        )

    baseline = obligation["progress_baseline"]
    renamed = {**baseline, "hypothesis_id": "hypothesis-renamed"}
    refused = qualify(renamed)
    assert refused["accepted"] is False
    assert refused["reason_code"] == "progress_identity_without_new_evidence"
    assert qualify({**renamed, "surface_id": "retry-renamed", "probe_kind": "probe-renamed"})["accepted"] is False
    pivot = qualify({**renamed, "evidence_ids": [*baseline["evidence_ids"], "evidence-new"]})
    assert pivot["accepted"] is True and pivot["satisfying_outcomes"] == ["new_hypothesis"]
    blocker = qualify({
        "schema_version": "typed_progress_observation_v0", "result_class": "blocked",
        "blocker_id": "blocker-review", "evidence_ids": baseline["evidence_ids"],
    })
    assert blocker["accepted"] is True and "new_concrete_blocker" in blocker["satisfying_outcomes"]
    kept = qualify(None, _vision("continue"))
    assert kept["accepted"] is True and kept["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
    # Replaying the older claim of the window (a new hypothesis against the
    # single baseline, with evidence absent from that baseline) is refused:
    # novelty is judged against every claim that formed the obligation.
    older = normalize_progress_observation(runs[1]["progress_observation"])
    replayed = qualify(dict(older))
    assert replayed["accepted"] is False
    assert replayed["reason_code"] == "progress_observation_replayed"
    # The older hypothesis id over the window's evidence ids is a rename, not a pivot.
    assert qualify({**older, "evidence_ids": baseline["evidence_ids"]})["reason_code"] == "progress_identity_without_new_evidence"
    # Returning to an earlier hypothesis on genuinely new evidence is a typed pivot.
    returned = qualify({**older, "evidence_ids": [*older["evidence_ids"], "evidence-fresh"]})
    assert returned["accepted"] is True and returned["satisfying_outcomes"] == ["new_hypothesis"]
    # A replayed claim beside an evidence-linked vision path is accepted for the vision only.
    both_replayed = qualify(dict(older), _vision("continue"))
    assert both_replayed["accepted"] is True and both_replayed["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
    assert qualify(None, _vision("replan"))["accepted"] is True
    assert qualify(None, _vision("wait"))["accepted"] is False
    assert qualify(None, _vision("continue", evidence=[]))["accepted"] is False
    # A renamed identifier together with a vision path is accepted for the vision, not the rename.
    both = qualify(renamed, _vision("continue"))
    assert both["accepted"] is True and both["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
    # The typed fuse's own obligation keeps its broader policy: the rule is scoped to the source.
    fuse = {"triggers": [{"kind": "typed_progress_repeat", "progress_baseline": baseline}], "progress_baseline": baseline}
    assert semantic_delta_from_writeback(obligation=fuse, progress_observation=renamed)["accepted"] is True


def test_external_review_replay_policy_covers_blocker_and_terminal_claims() -> None:
    baseline = normalize_progress_observation({
        "schema_version": "typed_progress_observation_v0", "result_class": "advanced",
        "hypothesis_id": "latest", "evidence_ids": ["evidence-latest"],
    })
    earlier_claims = (
        {"result_class": "blocked", "blocker_id": "blocker-old", "evidence_ids": ["evidence-old"]},
        {"result_class": "exploration_exhausted", "coverage_scope_id": "coverage-old",
         "coverage_complete": True, "evidence_ids": ["evidence-old"]},
        {"result_class": "no_followup", "coverage_scope_id": "coverage-old",
         "evidence_ids": ["evidence-old"]},
    )
    for earlier in earlier_claims:
        earlier = {"schema_version": "typed_progress_observation_v0", **earlier}
        obligation = {
            "triggers": [{"kind": EXTERNAL_PROGRESS_REVIEW_TRIGGER_KIND}],
            "progress_baseline": baseline,
            "progress_window": [baseline, normalize_progress_observation(earlier)],
        }
        terminal_vision = (
            {"state": "no_followup", "path_delta": {"outcome": "stop"}}
            if earlier["result_class"] == "no_followup" else None
        )
        replayed = semantic_delta_from_writeback(
            obligation=obligation, progress_observation=earlier, agent_vision=terminal_vision,
        )
        assert replayed["accepted"] is False, earlier["result_class"]
        assert replayed["reason_code"] == "progress_observation_replayed"
        assert semantic_delta_from_writeback(
            obligation=obligation, progress_observation=earlier, agent_vision=_vision("continue"),
        )["satisfying_outcomes"] == ["fresh_vision_path_outcome"]
        successor = {**earlier, "evidence_ids": ["evidence-new"]}
        if earlier["result_class"] == "blocked":
            successor["blocker_id"] = "blocker-new"
        else:
            successor["coverage_scope_id"] = "coverage-new"
        assert semantic_delta_from_writeback(
            obligation=obligation, progress_observation=successor, agent_vision=terminal_vision,
        )["accepted"] is True


def test_external_review_discharge_covers_every_claim_in_a_long_visible_window() -> None:
    """Pending transitions must not push an old claim outside replay protection."""

    runs = [run(number, turn=f"t{number}") for number in range(35, 0, -1)]
    for number, row in enumerate(reversed(runs), start=1):
        row["progress_observation"]["surface_id"] = "retry"
        row["progress_observation"]["evidence_ids"] = [f"evidence-{number}"]
    receipts = [receipt(2, turn="t2"), receipt(1, turn="t1")]
    obligation = autonomous_replan_obligation_from_runs(
        runs, agent_todos=None, external_progress_review=_context("assist", receipts)
    )
    assert obligation is not None and obligation["required"] is True
    assert len(obligation["progress_window"]) == len(runs)
    oldest = normalize_progress_observation(runs[-1]["progress_observation"])
    refused = semantic_delta_from_writeback(
        obligation=obligation, progress_observation=oldest
    )
    assert refused["accepted"] is False
    assert refused["reason_code"] == "progress_observation_replayed"
    pivot = semantic_delta_from_writeback(
        obligation=obligation,
        progress_observation={
            **oldest,
            "evidence_ids": ["evidence-not-in-window"],
        },
    )
    assert pivot["accepted"] is True
    assert "new_hypothesis" in pivot["satisfying_outcomes"]
    kept = semantic_delta_from_writeback(
        obligation=obligation, progress_observation=None, agent_vision=_vision()
    )
    assert kept["accepted"] is True
    assert kept["satisfying_outcomes"] == ["fresh_vision_path_outcome"]


def test_context_loader_reports_a_rebind_hint_when_the_newest_receipt_is_bound_elsewhere(tmp_path) -> None:
    from loopx.capabilities.progress_review.receipt import write_progress_review_receipt

    def stored(sequence: int, contract: str) -> dict[str, object]:
        return {
            **receipt(sequence, turn=f"t{sequence}", contract=contract),
            "schema_version": "progress_review_receipt_v0",
            "signal_rule_version": "progress_review_signal_rule_v1",
            "goal_id": "rebind-goal",
            "question_version": "scoped-progress-sentinel-v2",
            "model": "fixture-v1",
            "judgments": {
                "choice": {"relation": "off_goal", "increment": "no_new_evidence"},
                "noul": {"behavior_change": 0.05, "serves_acceptance": 0.04, "evidence_increment": 0.1},
            },
            "label_probability_threshold": 0.6,
            "recorded_at": float(sequence),
        }

    write_progress_review_receipt(tmp_path, "rebind-goal", stored(1, "contract-1"))
    pinned = {"id": "rebind-goal", "control_plane": {"progress_review": {"mode": "assist", "contract_revision": _digest("contract-1")}}}
    loaded = external_progress_review_context(pinned, tmp_path)
    assert loaded is not None
    assert loaded["summary"]["newest_receipt_contract_revision"] == _digest("contract-1")
    assert "rebind_hint" not in loaded["summary"]
    # The observer basis moved on; the pin did not.
    write_progress_review_receipt(tmp_path, "rebind-goal", stored(2, "contract-2"))
    loaded = external_progress_review_context(pinned, tmp_path)
    assert loaded is not None
    assert loaded["summary"]["stale_receipts"] == 1
    assert loaded["summary"]["newest_receipt_contract_revision"] == _digest("contract-2")
    assert loaded["summary"]["rebind_hint"] == "newer_receipts_under_unpinned_revision"
    # Re-pinning to the current basis clears the hint and drops the old receipt to history.
    repinned = {"id": "rebind-goal", "control_plane": {"progress_review": {"mode": "assist", "contract_revision": _digest("contract-2")}}}
    loaded = external_progress_review_context(repinned, tmp_path)
    assert loaded is not None and "rebind_hint" not in loaded["summary"] and loaded["summary"]["stale_receipts"] == 1
    # Shadow with a pin gets the same hint; without a pin there is nothing to rebind.
    shadow = {"id": "rebind-goal", "control_plane": {"progress_review": {"mode": "shadow", "contract_revision": _digest("contract-1")}}}
    loaded = external_progress_review_context(shadow, tmp_path)
    assert loaded is not None and loaded["summary"]["rebind_hint"] == "newer_receipts_under_unpinned_revision"
    unpinned = {"id": "rebind-goal", "control_plane": {"progress_review": {"mode": "shadow"}}}
    loaded = external_progress_review_context(unpinned, tmp_path)
    assert loaded is not None and "rebind_hint" not in loaded["summary"]


def test_newest_receipt_follows_run_order_not_the_observer_local_sequence(tmp_path) -> None:
    """A re-initialised observer restarts `sequence` at zero under a new revision."""

    from loopx.capabilities.progress_review.receipt import (
        load_progress_review_receipts,
        write_progress_review_receipt,
    )

    def stored(sequence: int, contract: str, *, generated_at: str, recorded_at: float, turn: str) -> dict[str, object]:
        record = {
            **receipt(sequence, turn=turn, contract=contract),
            "schema_version": "progress_review_receipt_v0",
            "signal_rule_version": "progress_review_signal_rule_v1",
            "goal_id": "order-goal",
            "question_version": "scoped-progress-sentinel-v2",
            "model": "fixture-v1",
            "judgments": {
                "choice": {"relation": "off_goal", "increment": "no_new_evidence"},
                "noul": {"behavior_change": 0.05, "serves_acceptance": 0.04, "evidence_increment": 0.1},
            },
            "label_probability_threshold": 0.6,
            "recorded_at": recorded_at,
        }
        record["run"] = {**record["run"], "generated_at": generated_at}
        return record

    # Old observer state under R1 wrote sequences 1 and 2.
    write_progress_review_receipt(tmp_path, "order-goal", stored(1, "contract-1", generated_at="2026-09-21T00:00:01Z", recorded_at=1.0, turn="t1"))
    write_progress_review_receipt(tmp_path, "order-goal", stored(2, "contract-1", generated_at="2026-09-21T00:00:02Z", recorded_at=2.0, turn="t2"))
    # New observer state under R2 starts again at sequence 0, for a later transition.
    write_progress_review_receipt(tmp_path, "order-goal", stored(0, "contract-2", generated_at="2026-09-21T00:00:05Z", recorded_at=5.0, turn="t5"))

    loaded, rejected = load_progress_review_receipts(tmp_path, "order-goal")
    assert rejected == 0
    assert [item["run"]["turn_instance_id"] for item in loaded] == ["t5", "t2", "t1"]
    # The load limit keeps the newest transition, not the highest local sequence.
    limited, _ = load_progress_review_receipts(tmp_path, "order-goal", limit=1)
    assert [item["run"]["turn_instance_id"] for item in limited] == ["t5"]

    pinned_old = {"id": "order-goal", "control_plane": {"progress_review": {"mode": "assist", "contract_revision": _digest("contract-1")}}}
    context = external_progress_review_context(pinned_old, tmp_path)
    assert context is not None
    assert context["summary"]["newest_receipt_contract_revision"] == _digest("contract-2")
    assert context["summary"]["rebind_hint"] == "newer_receipts_under_unpinned_revision"
    assert context["summary"]["stale_receipts"] == 1
    pinned_new = {"id": "order-goal", "control_plane": {"progress_review": {"mode": "assist", "contract_revision": _digest("contract-2")}}}
    context = external_progress_review_context(pinned_new, tmp_path)
    assert context is not None
    assert context["summary"]["newest_receipt_contract_revision"] == _digest("contract-2")
    assert "rebind_hint" not in context["summary"]
    assert context["summary"]["stale_receipts"] == 2
    assert context["summary"]["latest"]["run"]["turn_instance_id"] == "t5"
