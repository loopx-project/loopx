from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.content_ops.item_lifecycle import (
    apply_content_ops_item_event,
    build_content_ops_item,
    project_content_ops_item,
    validate_content_ops_item,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DIGEST_V1 = "sha256:" + "1" * 64
DIGEST_V2 = "sha256:" + "2" * 64


def _item() -> dict[str, object]:
    return build_content_ops_item(
        item_id="partner-launch-reply-v1",
        item_kind="reply",
        channel="x",
        content_digest=DIGEST_V1,
        content_ref="draft:partner-launch-reply-v1",
        source_refs=["source:partner-launch-post"],
        created_at="2026-08-03T09:00:00+08:00",
    )


def _event(
    item: dict[str, object],
    event_id: str,
    action: str,
    payload: dict[str, object] | None = None,
    occurred_at: str = "2026-08-03T09:05:00+08:00",
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "action": action,
        "expected_state": item["state"],
        "expected_revision": item["revision"],
        "occurred_at": occurred_at,
        "payload": payload or {},
    }


def _apply(
    item: dict[str, object],
    event_id: str,
    action: str,
    payload: dict[str, object] | None = None,
    occurred_at: str = "2026-08-03T09:05:00+08:00",
) -> dict[str, object]:
    packet = apply_content_ops_item_event(
        item,
        _event(item, event_id, action, payload, occurred_at),
    )
    assert packet["external_writes_performed"] is False
    return packet["item"]


def test_full_item_lifecycle_requires_bound_approval_and_exact_readback() -> None:
    item = _apply(_item(), "event-review", "submit_review")
    item = _apply(
        item,
        "event-approve",
        "approve",
        {
            "approval_ref": "decision:partner-launch-reply-v1",
            "revision": 1,
            "content_digest": DIGEST_V1,
            "effect_kind": "reply",
            "account_ref": "account:maintainer",
            "valid_from": "2026-08-03T09:00:00+08:00",
            "valid_until": "2026-08-03T12:00:00+08:00",
        },
    )
    item = _apply(
        item,
        "event-intent",
        "set_delivery_intent",
        {
            "provider_id": "ego-lite",
            "effect_kind": "reply",
            "account_ref": "account:maintainer",
            "not_before": "2026-08-03T09:30:00+08:00",
            "not_after": "2026-08-03T11:00:00+08:00",
        },
    )
    item = _apply(
        item,
        "event-delivery",
        "record_delivery",
        {
            "provider_id": "ego-lite",
            "effect_kind": "reply",
            "account_ref": "account:maintainer",
            "content_digest": DIGEST_V1,
            "public_url": "https://x.com/example/status/123",
            "receipt_ref": "receipt:x-reply-123",
        },
        occurred_at="2026-08-03T10:00:00+08:00",
    )
    item = _apply(
        item,
        "event-readback",
        "verify_readback",
        {
            "public_url": "https://x.com/example/status/123",
            "content_digest": DIGEST_V1,
            "readback_ref": "readback:x-reply-123",
        },
        occurred_at="2026-08-03T10:01:00+08:00",
    )

    assert validate_content_ops_item(item)["ok"] is True
    projection = project_content_ops_item(item)
    assert projection["state"] == "readback_verified"
    assert projection["terminal"] is True
    assert projection["readback_verified"] is True
    assert projection["published_url"] == "https://x.com/example/status/123"
    assert projection["truth_contract"]["external_effect_authority"] == "none"


def test_revision_invalidates_prior_approval_and_effect_intent() -> None:
    item = _apply(_item(), "event-review", "submit_review")
    item = _apply(
        item,
        "event-approve",
        "approve",
        {
            "approval_ref": "decision:partner-launch-reply-v1",
            "revision": 1,
            "content_digest": DIGEST_V1,
            "effect_kind": "reply",
        },
    )
    item = _apply(
        item,
        "event-revise",
        "revise",
        {
            "content_digest": DIGEST_V2,
            "content_ref": "draft:partner-launch-reply-v2",
        },
    )

    assert item["state"] == "draft"
    assert item["revision"] == 2
    assert item["approval"] is None
    assert item["delivery_intent"] is None


def test_approval_and_delivery_fail_closed_on_binding_or_window_mismatch() -> None:
    item = _apply(_item(), "event-review", "submit_review")
    with pytest.raises(ValueError, match="approval content_digest"):
        _apply(
            item,
            "event-bad-approval",
            "approve",
            {
                "approval_ref": "decision:partner-launch-reply-v1",
                "revision": 1,
                "content_digest": DIGEST_V2,
                "effect_kind": "reply",
            },
        )

    item = _apply(
        item,
        "event-approve",
        "approve",
        {
            "approval_ref": "decision:partner-launch-reply-v1",
            "revision": 1,
            "content_digest": DIGEST_V1,
            "effect_kind": "reply",
            "valid_until": "2026-08-03T09:30:00+08:00",
        },
    )
    with pytest.raises(ValueError, match="outside valid_until"):
        _apply(
            item,
            "event-late-delivery",
            "record_delivery",
            {
                "provider_id": "ego-lite",
                "effect_kind": "reply",
                "content_digest": DIGEST_V1,
                "public_url": "https://x.com/example/status/123",
                "receipt_ref": "receipt:x-reply-123",
            },
            occurred_at="2026-08-03T10:00:00+08:00",
        )


def test_event_retry_is_idempotent_and_changed_reuse_is_rejected() -> None:
    item = _item()
    event = _event(item, "event-review", "submit_review")
    first = apply_content_ops_item_event(item, event)
    retried = apply_content_ops_item_event(first["item"], event)
    assert retried["receipt"]["status"] == "already_applied"
    changed = dict(event)
    changed["occurred_at"] = "2026-08-03T09:06:00+08:00"
    with pytest.raises(ValueError, match="different content"):
        apply_content_ops_item_event(first["item"], changed)


def test_mismatched_expected_state_or_revision_is_rejected_not_silently_applied() -> None:
    """A fresh event_id (not a replay) whose expected_state/expected_revision
    disagree with the item's real current values must fail loudly. Nothing
    else in this codebase re-derives expected_state/expected_revision for the
    caller -- callers (including content-ops's CUA reducer) rely entirely on
    this rejection to catch a stale or forged optimistic-concurrency claim."""

    item = _apply(_item(), "event-review", "submit_review")

    with pytest.raises(ValueError, match="expected_state does not match"):
        apply_content_ops_item_event(
            item,
            _event(item, "event-wrong-state", "approve", {}, "2026-08-03T09:07:00+08:00")
            | {"expected_state": "captured"},
        )

    with pytest.raises(ValueError, match="expected_revision does not match"):
        apply_content_ops_item_event(
            item,
            _event(item, "event-wrong-revision", "approve", {}, "2026-08-03T09:07:00+08:00")
            | {"expected_revision": item["revision"] + 1},
        )


def test_approval_sequence_advances_on_reapproval_even_without_content_revise() -> None:
    item = _apply(_item(), "event-review", "submit_review")
    assert item["approval_sequence"] == 0

    item = _apply(
        item,
        "event-approve-1",
        "approve",
        {
            "approval_ref": "decision:partner-launch-reply-v1-a",
            "revision": 1,
            "content_digest": DIGEST_V1,
            "effect_kind": "reply",
        },
    )
    first_gate_revision = item["approval_sequence"]
    assert first_gate_revision == 1

    item = _apply(item, "event-revoke", "revoke_approval", {"reason": "owner asked to re-check tone"})
    assert item["state"] == "review_ready"
    assert item["approval"] is None
    assert item["approval_sequence"] == first_gate_revision, (
        "approval_sequence must survive revoke so a stale in-flight action "
        "request cannot be confused with the new approval below"
    )

    item = _apply(
        item,
        "event-approve-2",
        "approve",
        {
            "approval_ref": "decision:partner-launch-reply-v1-b",
            "revision": 1,
            "content_digest": DIGEST_V1,
            "effect_kind": "reply",
        },
    )
    second_gate_revision = item["approval_sequence"]

    assert second_gate_revision > first_gate_revision, (
        "content revision (1) never changed across the revoke/reapprove cycle, "
        "so only a dedicated approval_sequence counter -- not item.revision -- "
        "can distinguish the revoked approval from the live one"
    )


def _drop_approval_sequence(item: dict[str, object]) -> dict[str, object]:
    """Simulate a content_ops_item_v0 persisted before approval_sequence
    existed -- schema_version never changed, so old data has no such field."""

    legacy = dict(item)
    del legacy["approval_sequence"]
    return legacy


def test_legacy_v0_items_without_approval_sequence_are_still_readable() -> None:
    captured_legacy = _drop_approval_sequence(_item())
    assert validate_content_ops_item(captured_legacy)["ok"] is True
    assert project_content_ops_item(captured_legacy)["state"] == "captured"

    review_ready_legacy = _drop_approval_sequence(
        _apply(_item(), "event-review", "submit_review")
    )
    assert validate_content_ops_item(review_ready_legacy)["ok"] is True

    approved = _apply(
        _apply(_item(), "event-review", "submit_review"),
        "event-approve",
        "approve",
        {
            "approval_ref": "decision:partner-launch-reply-v1",
            "revision": 1,
            "content_digest": DIGEST_V1,
            "effect_kind": "reply",
        },
    )
    approved_legacy = _drop_approval_sequence(approved)
    # Unconditional default: 0, even though this item is currently approved.
    # No attempt to retroactively infer "1" for an approval that predates the
    # counter -- reading/validating must not crash, but that's all this
    # normalization promises. (Whether such an item can drive a CUA request
    # at approval_sequence=0 is computer_use_provider's own concern, tested
    # in tests/test_content_ops_computer_use.py, not this generic read path.)
    assert validate_content_ops_item(approved_legacy)["ok"] is True
    assert project_content_ops_item(approved_legacy)["approval_sequence"] == 0

    # A fresh approve event (even a plain re-approval, no revoke needed here
    # since it's still "approved") establishes the first real, CUA-aware
    # gate identity: 0 -> 1, same as any brand-new item's first approval.
    revoked = apply_content_ops_item_event(
        approved_legacy,
        {
            "event_id": "event-revoke",
            "action": "revoke_approval",
            "expected_state": "approved",
            "expected_revision": 1,
            "occurred_at": "2026-08-03T09:20:00+08:00",
            "payload": {"reason": "owner wants another look"},
        },
    )["item"]
    reapproved = _apply(
        revoked,
        "event-approve-2",
        "approve",
        {
            "approval_ref": "decision:partner-launch-reply-v1-b",
            "revision": 1,
            "content_digest": DIGEST_V1,
            "effect_kind": "reply",
        },
    )
    assert reapproved["approval_sequence"] == 1


def test_cli_item_transition_reads_a_legacy_item_missing_approval_sequence(
    tmp_path: Path,
) -> None:
    legacy_item_path = tmp_path / "legacy_item.json"
    legacy_item_path.write_text(
        json.dumps(_drop_approval_sequence(_item())), encoding="utf-8"
    )
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(_event(_item(), "event-review", "submit_review")),
        encoding="utf-8",
    )
    transition = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "content-ops",
            "item-transition",
            "--item-json",
            str(legacy_item_path),
            "--event-json",
            str(event_path),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    transitioned = json.loads(transition.stdout)
    assert transitioned["projection"]["state"] == "review_ready"
    assert transitioned["item"]["approval_sequence"] == 0


def test_superseded_item_is_terminal_and_links_successor() -> None:
    item = _apply(
        _item(),
        "event-supersede",
        "supersede",
        {
            "successor_item_id": "partner-launch-reply-v2",
            "reason": "A newer draft replaces this review packet.",
        },
    )
    assert item["state"] == "superseded"
    assert item["superseded_by"] == "partner-launch-reply-v2"
    assert project_content_ops_item(item)["next_actions"] == []
    with pytest.raises(ValueError, match="submit_review is not allowed"):
        _apply(item, "event-review", "submit_review")


def test_forged_state_or_embedded_body_is_rejected() -> None:
    forged = _item()
    forged["state"] = "published"
    validation = validate_content_ops_item(forged)
    assert validation["ok"] is False
    assert "published item requires approval" in validation["errors"][0]

    embedded = _item()
    embedded["post_body"] = "private draft body"
    validation = validate_content_ops_item(embedded)
    assert validation["ok"] is False
    assert "unsupported fields" in validation["errors"][0]


def test_cli_creates_and_transitions_an_item_without_external_effects(
    tmp_path: Path,
) -> None:
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "content-ops",
            "item-create",
            "--item-id",
            "launch-post-v1",
            "--item-kind",
            "post",
            "--channel",
            "x",
            "--content-digest",
            DIGEST_V1,
            "--content-ref",
            "draft:launch-post-v1",
            "--created-at",
            "2026-08-03T09:00:00+08:00",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    created = json.loads(create.stdout)
    assert created["projection"]["state"] == "captured"
    assert created["external_writes_performed"] is False

    item_path = tmp_path / "item.json"
    event_path = tmp_path / "event.json"
    item_path.write_text(json.dumps(created["item"]), encoding="utf-8")
    event_path.write_text(
        json.dumps(_event(created["item"], "event-review", "submit_review")),
        encoding="utf-8",
    )
    transition = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "content-ops",
            "item-transition",
            "--item-json",
            str(item_path),
            "--event-json",
            str(event_path),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    transitioned = json.loads(transition.stdout)
    assert transitioned["projection"]["state"] == "review_ready"
    assert transitioned["receipt"]["status"] == "applied"
    assert transitioned["external_writes_performed"] is False
