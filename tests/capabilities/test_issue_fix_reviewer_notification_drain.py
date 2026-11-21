from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.issue_fix.pr_lifecycle import (
    build_issue_fix_pr_lifecycle_monitor_packet,
)
from loopx.capabilities.issue_fix.reviewer_notification import (
    ISSUE_FIX_REVIEWER_NOTIFICATION_SINKS_INPUT_SCHEMA_VERSION,
    reviewer_notification_idempotency_key,
)
from loopx.capabilities.issue_fix.reviewer_notification_drain import (
    _row_with_live_lifecycle_facts,
    drain_issue_fix_reviewer_notification_queue,
)
from loopx.domain_packs.issue_fix import (
    REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION,
    persist_issue_fix_reviewer_notification_state,
    read_issue_fix_pr_lifecycle_ledger_row,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from loopx.extensions.lark.reviewer_notification import (
    lark_reviewer_notification_sink,
)


def _sink(
    instance_key: str = "fixture-review-lane",
    *,
    destination_id: str = "oc_fixture_chat",
) -> dict[str, Any]:
    return {
        "sink_kind": "lark_chat",
        "sink_instance_key": instance_key,
        "identity_scope": "project_dedicated",
        "reader_profile": "fixture-reader-profile",
        "reader_identity": "user",
        "sender_profile": "fixture-sender-profile",
        "sender_identity": "bot",
        "bot_display_name": "Fixture Review Bot",
        "destination_id": destination_id,
        "reviewer_identities": {
            "@reviewer-a": {
                "member_id": "ou_fixture_reviewer",
                "display_name": "Reviewer A",
            }
        },
    }


def _sinks_input(*sinks: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ISSUE_FIX_REVIEWER_NOTIFICATION_SINKS_INPUT_SCHEMA_VERSION,
        "receipts": [],
        "sinks": [dict(sink) for sink in sinks],
    }


def _live_metadata(
    *,
    author_handle: str = "@author-a",
    reviewed_by: Sequence[str] = (),
    requested_reviewers: Sequence[str] = ("@reviewer-a",),
    comment_notified_reviewers: Sequence[str] = (),
    state: str = "OPEN",
    review_decision: str = "REVIEW_REQUIRED",
    state_bucket: str = "review_required",
    is_draft: bool = False,
    linked_issue_refs: Sequence[str] = ("#42",),
    lifecycle_packet: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "author_handle": author_handle,
        "reviewed_by": list(reviewed_by),
        "requested_reviewers": list(requested_reviewers),
        "comment_notified_reviewers": list(comment_notified_reviewers),
        "state": state,
        "review_decision": review_decision,
        "state_bucket": state_bucket,
        "is_draft": is_draft,
        "linked_issue_refs": list(linked_issue_refs),
    }
    if lifecycle_packet is not None:
        payload["_lifecycle_packet"] = dict(lifecycle_packet)
    return payload


def _queue_receipt(
    *,
    repo: str,
    pr_number: int,
    sink_instance_key: str,
    reviewer_handles: Sequence[str] = ("@reviewer-a",),
    summary: str = "修复通知队列重启写回与状态刷新边界",
    queued_at: str = "2026-07-20T00:00:00Z",
    not_before: str = "2026-07-20T01:00:00Z",
) -> dict[str, Any]:
    key = reviewer_notification_idempotency_key(
        repo=repo,
        pr_number=pr_number,
        sink_kind="lark_chat",
        sink_instance_key=sink_instance_key,
        reviewer_handles=reviewer_handles,
    )
    return {
        "schema_version": REVIEWER_NOTIFICATION_QUEUE_RECEIPT_SCHEMA_VERSION,
        "idempotency_key": key,
        "sink_kind": "lark_chat",
        "reviewer_handles": list(reviewer_handles),
        "message_summary": summary,
        "summary_policy_status": "reward_memory_verified",
        "queued_at": queued_at,
        "not_before": not_before,
        "timezone": "Asia/Shanghai",
        "allowed_local_time": {"start": "09:00", "end": "21:00"},
        "status": "queued",
    }


def _seed_queued_pr(
    ledger_path: Path,
    *,
    repo: str = "owner/repo",
    pr_number: int = 101,
    sinks: Sequence[Mapping[str, Any]],
    reviewer_handles: Sequence[str] = ("@reviewer-a",),
    maintainer_correction_input: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row = build_issue_fix_pr_lifecycle_monitor_packet(
        url=f"https://github.com/{repo}/pull/{pr_number}",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "BLOCKED",
            "statusCheckRollup": [],
        },
        maintainer_correction_input=maintainer_correction_input,
    )
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, row)
    queued_receipts = [
        _queue_receipt(
            repo=repo,
            pr_number=pr_number,
            sink_instance_key=str(sink["sink_instance_key"]),
            reviewer_handles=reviewer_handles,
        )
        for sink in sinks
    ]
    persist_issue_fix_reviewer_notification_state(
        ledger_path,
        row,
        receipts=[],
        queued_receipts=queued_receipts,
    )
    return row, queued_receipts


def _make_sink_adapter(
    *,
    status: str,
    ok: bool,
    external_write_performed: bool,
    notification_verified: bool,
    blocker: str | None = None,
    recorded_sinks: list[str] | None = None,
) -> Any:
    def adapter(**kwargs: Any) -> dict[str, Any]:
        instance_key = str(kwargs["sink"]["sink_instance_key"]).strip()
        key = reviewer_notification_idempotency_key(
            repo=str(kwargs["repo"]),
            pr_number=int(kwargs["pr_number"]),
            sink_kind="lark_chat",
            sink_instance_key=instance_key,
            reviewer_handles=kwargs["reviewer_handles"],
        )
        if key in kwargs["receipts"]:
            return {
                "ok": True,
                "schema_version": "issue_fix_reviewer_notification_sink_result_v0",
                "sink_kind": "lark_chat",
                "status": "already_notified",
                "reviewer_handles": list(kwargs["reviewer_handles"]),
                "resolved_reviewer_count": len(kwargs["reviewer_handles"]),
                "idempotency_key": key,
                "identity_scope": "project_dedicated",
                "external_write_authority_asserted": True,
                "external_write_performed": False,
                "verification_performed": True,
                "notification_verified": True,
                "bot_identity_verified": True,
                "reader_identity_verified": True,
                "private_destination_captured": False,
                "private_member_ids_captured": False,
                "private_bot_profile_captured": False,
                "raw_provider_payload_captured": False,
            }
        if recorded_sinks is not None:
            recorded_sinks.append(instance_key)
        payload: dict[str, Any] = {
            "ok": ok,
            "schema_version": "issue_fix_reviewer_notification_sink_result_v0",
            "sink_kind": "lark_chat",
            "status": status,
            "reviewer_handles": list(kwargs["reviewer_handles"]),
            "resolved_reviewer_count": len(kwargs["reviewer_handles"]),
            "idempotency_key": key,
            "identity_scope": "project_dedicated",
            "external_write_authority_asserted": True,
            "external_write_performed": external_write_performed,
            "verification_performed": True,
            "notification_verified": notification_verified,
            "bot_identity_verified": True,
            "reader_identity_verified": True,
            "private_destination_captured": False,
            "private_member_ids_captured": False,
            "private_bot_profile_captured": False,
            "raw_provider_payload_captured": False,
        }
        if blocker is not None:
            payload["blocker"] = blocker
        return payload

    return adapter


def test_drain_restart_safe_queued_to_sent_durable_writeback(tmp_path: Path) -> None:
    ledger = tmp_path / "pr-lifecycle.jsonl"
    sink = _sink()
    sinks_input = _sinks_input(sink)
    _, queued = _seed_queued_pr(ledger, pr_number=101, sinks=[sink])
    expected_key = queued[0]["idempotency_key"]

    preview = drain_issue_fix_reviewer_notification_queue(
        ledger_path=ledger,
        sinks_input=sinks_input,
        execute=False,
        delivery_observed_at="2026-07-20T01:05:00Z",
    )
    assert preview["ok"] is True
    assert preview["status"] == "preview_due"
    assert preview["queued_receipt_count"] == 1
    assert preview["due_pr_count"] == 1
    assert preview["external_writes_performed"] is False
    assert preview["state_write_performed"] is False

    # Outside the 09:00-21:00 Asia/Shanghai window (22:00 local == 14:00Z), the
    # item stays queued on disk with zero external reads or writes.
    held = drain_issue_fix_reviewer_notification_queue(
        ledger_path=ledger,
        sinks_input=sinks_input,
        execute=True,
        delivery_observed_at="2026-07-20T14:00:00Z",
        metadata_loader=lambda **_: pytest.fail("outside window must not read PR"),
    )
    assert held["ok"] is True
    assert held["status"] == "held_outside_delivery_window"
    assert held["held_pr_count"] == 1
    assert held["remaining_due_pr_count"] == 1
    assert held["external_reads_performed"] is False
    assert held["external_writes_performed"] is False
    assert held["state_write_performed"] is False

    deliveries: list[str] = []
    adapter = _make_sink_adapter(
        status="sent_verified",
        ok=True,
        external_write_performed=True,
        notification_verified=True,
        recorded_sinks=deliveries,
    )
    drained = drain_issue_fix_reviewer_notification_queue(
        ledger_path=ledger,
        sinks_input=sinks_input,
        execute=True,
        delivery_observed_at="2026-07-20T01:05:00Z",
        metadata_loader=lambda *, repo, number: (_live_metadata(), None),
        sink_adapters={"lark_chat": adapter},
    )
    assert drained["ok"] is True
    assert drained["status"] == "drained_verified"
    assert drained["verified_pr_count"] == 1
    assert drained["external_reads_performed"] is True
    assert drained["external_writes_performed"] is True
    assert drained["state_write_performed"] is True
    assert drained["remaining_due_pr_count"] == 0
    assert drained["has_more_due"] is False
    assert drained["items"][0]["status"] == "sent_verified"
    assert drained["items"][0]["notification_verified"] is True
    assert drained["items"][0]["queue_write_performed"] is True
    assert deliveries == ["fixture-review-lane"]

    stored = read_issue_fix_pr_lifecycle_ledger_row(
        ledger, repo="owner/repo", pr_ref="pull_101"
    )
    assert stored is not None
    assert stored["reviewer_notification_queue"] == []
    assert stored["reviewer_notification_receipts"] == [expected_key]
    assert stored["local_paths_captured"] is False

    # Restart simulation: a new drain reading only from the JSONL ledger does
    # not resend or hit external APIs.
    restarted = drain_issue_fix_reviewer_notification_queue(
        ledger_path=ledger,
        sinks_input=sinks_input,
        execute=True,
        delivery_observed_at="2026-07-20T01:06:00Z",
        metadata_loader=lambda **_: pytest.fail("empty queue must not read PR"),
        sink_adapters={"lark_chat": adapter},
    )
    assert restarted["ok"] is True
    assert restarted["status"] == "no_due_notifications"
    assert restarted["queued_receipt_count"] == 0
    assert restarted["external_reads_performed"] is False
    assert restarted["external_writes_performed"] is False
    assert deliveries == ["fixture-review-lane"]


def test_drain_suppresses_sent_unverified_without_requeue_or_duplicate_resend(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "unverified-pr-lifecycle.jsonl"
    sink = _sink()
    sinks_input = _sinks_input(sink)
    _seed_queued_pr(ledger, pr_number=102, sinks=[sink])

    unverified_calls: list[str] = []
    unverified_adapter = _make_sink_adapter(
        status="sent_unverified",
        ok=False,
        external_write_performed=True,
        notification_verified=False,
        blocker="lark_notification_not_verified",
        recorded_sinks=unverified_calls,
    )
    first = drain_issue_fix_reviewer_notification_queue(
        ledger_path=ledger,
        sinks_input=sinks_input,
        execute=True,
        delivery_observed_at="2026-07-20T01:05:00Z",
        metadata_loader=lambda *, repo, number: (_live_metadata(), None),
        sink_adapters={"lark_chat": unverified_adapter},
    )
    assert first["ok"] is False
    assert first["status"] == "blocked"
    assert first["blocked_pr_count"] == 1
    assert first["external_writes_performed"] is True
    assert first["state_write_performed"] is True
    assert first["remaining_due_pr_count"] == 0
    assert first["has_more_due"] is False
    assert first["items"][0]["status"] == "sent_unverified"
    assert first["items"][0]["notification_verified"] is False
    assert first["items"][0]["external_write_performed"] is True
    assert first["items"][0]["queue_write_performed"] is True
    assert first["items"][0]["blocker"] == "lark_notification_not_verified"
    assert unverified_calls == ["fixture-review-lane"]

    stored = read_issue_fix_pr_lifecycle_ledger_row(
        ledger, repo="owner/repo", pr_ref="pull_102"
    )
    assert stored is not None
    assert stored["reviewer_notification_queue"] == []
    assert stored.get("reviewer_notification_receipts", []) == []

    second = drain_issue_fix_reviewer_notification_queue(
        ledger_path=ledger,
        sinks_input=sinks_input,
        execute=True,
        delivery_observed_at="2026-07-20T01:06:00Z",
        metadata_loader=lambda **_: pytest.fail("suppressed queue must not read PR"),
        sink_adapters={"lark_chat": unverified_adapter},
    )
    assert second["ok"] is True
    assert second["status"] == "no_due_notifications"
    assert second["external_writes_performed"] is False
    assert unverified_calls == ["fixture-review-lane"]

    # Contrast: a pre-write failure (external_write_performed=False) preserves
    # the queued receipt so a later drain can retry cleanly.
    retry_ledger = tmp_path / "prewrite-failure-pr-lifecycle.jsonl"
    _, retry_queued = _seed_queued_pr(retry_ledger, pr_number=103, sinks=[sink])
    prewrite_failed = drain_issue_fix_reviewer_notification_queue(
        ledger_path=retry_ledger,
        sinks_input=sinks_input,
        execute=True,
        delivery_observed_at="2026-07-20T01:05:00Z",
        metadata_loader=lambda *, repo, number: (_live_metadata(), None),
        sink_adapters={
            "lark_chat": _make_sink_adapter(
                status="gate_required",
                ok=False,
                external_write_performed=False,
                notification_verified=False,
                blocker="reviewer_notification_provider_unavailable",
            )
        },
    )
    assert prewrite_failed["ok"] is False
    assert prewrite_failed["external_writes_performed"] is False
    assert prewrite_failed["remaining_due_pr_count"] == 1
    retry_stored = read_issue_fix_pr_lifecycle_ledger_row(
        retry_ledger, repo="owner/repo", pr_ref="pull_103"
    )
    assert retry_stored is not None
    assert retry_stored["reviewer_notification_queue"] == retry_queued


def test_drain_semantic_history_matcher_deduplicates_delivered_notifications(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "semantic-dedupe-pr-lifecycle.jsonl"
    sink = _sink()
    sinks_input = _sinks_input(sink)
    _, queued = _seed_queued_pr(ledger, pr_number=104, sinks=[sink])
    expected_key = queued[0]["idempotency_key"]

    def fail_runner(_args: Sequence[str]) -> Mapping[str, Any]:
        raise AssertionError("semantic history match must short-circuit before provider calls")

    matched = drain_issue_fix_reviewer_notification_queue(
        ledger_path=ledger,
        sinks_input=sinks_input,
        execute=True,
        delivery_observed_at="2026-07-20T01:05:00Z",
        runner=fail_runner,
        metadata_loader=lambda *, repo, number, runner=None: (_live_metadata(), None),
        semantic_history_matcher=lambda permalink, current_sink: (
            permalink == "https://github.com/owner/repo/pull/104"
            and current_sink["sink_instance_key"] == "fixture-review-lane"
        ),
        sink_adapters={"lark_chat": lark_reviewer_notification_sink},
    )
    assert matched["ok"] is True
    assert matched["status"] == "drained_verified"
    assert matched["verified_pr_count"] == 1
    assert matched["external_writes_performed"] is False
    assert matched["state_write_performed"] is True
    assert matched["items"][0]["status"] == "already_notified"
    assert matched["items"][0]["semantic_history_status"] == "matched"

    stored = read_issue_fix_pr_lifecycle_ledger_row(
        ledger, repo="owner/repo", pr_ref="pull_104"
    )
    assert stored is not None
    assert stored["reviewer_notification_queue"] == []
    assert stored["reviewer_notification_receipts"] == [expected_key]

    # Multi-sink scoping: matching sink-a suppresses only sink-a while sink-b delivers.
    multi_ledger = tmp_path / "multi-sink-semantic-pr-lifecycle.jsonl"
    sink_a = _sink("fixture-review-lane-a", destination_id="oc_fixture_chat_a")
    sink_b = _sink("fixture-review-lane-b", destination_id="oc_fixture_chat_b")
    _, multi_queued = _seed_queued_pr(
        multi_ledger, pr_number=105, sinks=[sink_a, sink_b]
    )
    delivered_lanes: list[str] = []
    multi_adapter = _make_sink_adapter(
        status="sent_verified",
        ok=True,
        external_write_performed=True,
        notification_verified=True,
        recorded_sinks=delivered_lanes,
    )
    multi = drain_issue_fix_reviewer_notification_queue(
        ledger_path=multi_ledger,
        sinks_input=_sinks_input(sink_a, sink_b),
        execute=True,
        delivery_observed_at="2026-07-20T01:05:00Z",
        metadata_loader=lambda *, repo, number: (_live_metadata(), None),
        semantic_history_matcher=lambda permalink, current_sink: (
            current_sink["sink_instance_key"] == "fixture-review-lane-a"
        ),
        sink_adapters={"lark_chat": multi_adapter},
    )
    assert multi["ok"] is True
    assert multi["status"] == "drained_verified"
    assert delivered_lanes == ["fixture-review-lane-b"]
    multi_stored = read_issue_fix_pr_lifecycle_ledger_row(
        multi_ledger, repo="owner/repo", pr_ref="pull_105"
    )
    assert multi_stored is not None
    assert multi_stored["reviewer_notification_queue"] == []
    multi_receipts = multi_stored["reviewer_notification_receipts"]
    assert isinstance(multi_receipts, list)
    assert set(multi_receipts) == {
        item["idempotency_key"] for item in multi_queued
    }

    # Ambiguous semantic history scope fails closed without suppressing or writing.
    ambiguous_ledger = tmp_path / "ambiguous-semantic-pr-lifecycle.jsonl"
    _, ambiguous_queued = _seed_queued_pr(
        ambiguous_ledger, pr_number=106, sinks=[sink]
    )
    ambiguous = drain_issue_fix_reviewer_notification_queue(
        ledger_path=ambiguous_ledger,
        sinks_input=sinks_input,
        execute=True,
        delivery_observed_at="2026-07-20T01:05:00Z",
        metadata_loader=lambda *, repo, number: (_live_metadata(), None),
        semantic_history_matcher=lambda permalink, current_sink: None,
        sink_adapters={"lark_chat": lark_reviewer_notification_sink},
    )
    assert ambiguous["ok"] is False
    assert ambiguous["status"] == "blocked"
    assert ambiguous["external_writes_performed"] is False
    assert ambiguous["items"][0]["semantic_history_status"] == "scope_ambiguous"
    assert (
        ambiguous["items"][0]["blocker"]
        == "reviewer_notification_semantic_history_scope_ambiguous"
    )
    ambiguous_stored = read_issue_fix_pr_lifecycle_ledger_row(
        ambiguous_ledger, repo="owner/repo", pr_ref="pull_106"
    )
    assert ambiguous_stored is not None
    assert ambiguous_stored["reviewer_notification_queue"] == ambiguous_queued


@pytest.mark.parametrize("terminal_state", ["MERGED", "CLOSED"])
def test_row_with_live_lifecycle_facts_preserves_open_correction_and_refreshes_terminal(
    tmp_path: Path,
    terminal_state: str,
) -> None:
    correction_row = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/owner/repo/pull/107",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "BLOCKED",
            "statusCheckRollup": [],
        },
        maintainer_correction_input={
            "schema_version": "issue_fix_maintainer_correction_input_v0",
            "correction_kind": "semantic_ambiguity",
            "source_kind": "maintainer_comment",
            "source_ref": "https://github.com/owner/repo/pull/107#issuecomment-1",
            "summary": "需要确认重试策略边界",
            "user_question": "发送失败时应立即重试还是等待下一窗口？",
        },
    )
    assert (
        correction_row["transition"]["action_kind"]
        == "clarify_issue_fix_maintainer_correction"
    )

    # 1) Full _lifecycle_packet path on an OPEN PR preserves maintainer_correction
    #    projection fields while updating fresh observation metadata.
    fresh_open_packet = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/owner/repo/pull/107",
        provider_payload={
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [],
        },
        generated_at="2026-07-20T02:00:00Z",
    )
    updated_open = _row_with_live_lifecycle_facts(
        correction_row,
        _live_metadata(lifecycle_packet=fresh_open_packet),
    )
    assert updated_open["generated_at"] == "2026-07-20T02:00:00Z"
    assert updated_open["observation"]["merge_state_status"] == "CLEAN"
    assert updated_open["observation_fingerprint"] == fresh_open_packet["observation_fingerprint"]
    assert updated_open["maintainer_correction"] == correction_row["maintainer_correction"]
    assert updated_open["transition"] == correction_row["transition"]
    assert (
        updated_open["grouped_monitor_projection"]
        == correction_row["grouped_monitor_projection"]
    )
    assert updated_open["first_screen"] == correction_row["first_screen"]
    assert updated_open["writeback_contract"] == correction_row["writeback_contract"]

    # 2) Compact metadata fallback on an OPEN PR also preserves grouped_monitor_projection.
    updated_open_compact = _row_with_live_lifecycle_facts(
        correction_row,
        _live_metadata(state="OPEN", review_decision="REVIEW_REQUIRED"),
    )
    assert (
        updated_open_compact["grouped_monitor_projection"]
        == correction_row["grouped_monitor_projection"]
    )
    assert (
        updated_open_compact["transition"]
        == correction_row["transition"]
    )

    # 3) Terminal MERGED/CLOSED via _lifecycle_packet refreshes transition,
    #    grouped_monitor_projection, first_screen, and writeback_contract.
    fresh_terminal_packet = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/owner/repo/pull/107",
        provider_payload={
            "state": terminal_state,
            "reviewDecision": "APPROVED" if terminal_state == "MERGED" else "REVIEW_REQUIRED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [],
        },
        generated_at="2026-07-20T03:00:00Z",
    )
    updated_terminal = _row_with_live_lifecycle_facts(
        correction_row,
        _live_metadata(
            state=terminal_state,
            review_decision="APPROVED" if terminal_state == "MERGED" else "REVIEW_REQUIRED",
            state_bucket="terminal",
            lifecycle_packet=fresh_terminal_packet,
        ),
    )
    assert updated_terminal["observation"]["state"] == terminal_state
    assert updated_terminal["transition"] == fresh_terminal_packet["transition"]
    assert updated_terminal["transition"]["decision"] == "no_followup"
    assert (
        updated_terminal["grouped_monitor_projection"]
        == fresh_terminal_packet["grouped_monitor_projection"]
    )
    assert updated_terminal["grouped_monitor_projection"]["state_bucket"] == "terminal"
    assert updated_terminal["grouped_monitor_projection"]["member_operation"] == "remove"
    assert updated_terminal["grouped_monitor_projection"]["target_key"] is None
    assert updated_terminal["first_screen"] == fresh_terminal_packet["first_screen"]
    assert (
        updated_terminal["writeback_contract"]
        == fresh_terminal_packet["writeback_contract"]
    )

    # 4) Terminal MERGED/CLOSED via compact metadata fallback clears active
    #    grouped monitor target cleanly.
    updated_terminal_compact = _row_with_live_lifecycle_facts(
        correction_row,
        _live_metadata(
            state=terminal_state,
            review_decision="APPROVED" if terminal_state == "MERGED" else "REVIEW_REQUIRED",
            state_bucket="terminal",
        ),
    )
    assert updated_terminal_compact["observation"]["state"] == terminal_state
    assert updated_terminal_compact["grouped_monitor_projection"] == {
        **correction_row["grouped_monitor_projection"],
        "repository": "owner/repo",
        "state_bucket": "terminal",
        "target_key": None,
        "action_kind": None,
        "member_operation": "remove",
        "materialize_nonempty_bucket_monitor": False,
    }

    # 5) End-to-end drain cancellation on a terminal correction PR persists the
    #    terminal grouped_monitor_projection and clears the stale queue.
    ledger = tmp_path / f"terminal-{terminal_state.lower()}-pr-lifecycle.jsonl"
    sink = _sink()
    _seed_queued_pr(
        ledger,
        pr_number=108,
        sinks=[sink],
        maintainer_correction_input={
            "schema_version": "issue_fix_maintainer_correction_input_v0",
            "correction_kind": "semantic_ambiguity",
            "source_kind": "maintainer_comment",
            "source_ref": "https://github.com/owner/repo/pull/108#issuecomment-1",
            "summary": "需要确认重试策略边界",
            "user_question": "发送失败时应立即重试还是等待下一窗口？",
        },
    )
    cancelled = drain_issue_fix_reviewer_notification_queue(
        ledger_path=ledger,
        sinks_input=_sinks_input(sink),
        execute=True,
        delivery_observed_at="2026-07-20T01:05:00Z",
        metadata_loader=lambda *, repo, number: (
            _live_metadata(
                state=terminal_state,
                review_decision="APPROVED" if terminal_state == "MERGED" else "REVIEW_REQUIRED",
                state_bucket="terminal",
            ),
            None,
        ),
        sink_adapters={
            "lark_chat": lambda **_: pytest.fail("terminal PR must cancel without send")
        },
    )
    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancelled_stale"
    assert cancelled["cancelled_pr_count"] == 1
    assert cancelled["items"][0]["cancellation_reason"] == "pr_not_open"
    stored_terminal = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert stored_terminal["reviewer_notification_queue"] == []
    assert stored_terminal["observation"]["state"] == terminal_state
    assert stored_terminal["grouped_monitor_projection"]["member_operation"] == "remove"
    assert stored_terminal["grouped_monitor_projection"]["target_key"] is None
