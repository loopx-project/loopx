"""Contract and modularization tests for Zero-Any ChatActionStore modules."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import pytest

from loopx import chat_action_store
from loopx.chat_action_store import (
    ACTION_KINDS,
    HUMAN_ACTOR_CLASS,
    ActionConflictError,
    ChatActionStore,
    _OPAQUE_ID,
    _bounded_text,
    _canonical_digest,
    _opaque_id,
    _safe_json_value,
    _utc_now,
    atomic_write_json,
    exclusive_file_lock,
    normalize_idempotency_key,
)


def test_chat_action_store_modularization_zero_any_and_exports() -> None:
    root = Path(__file__).resolve().parents[1] / "loopx"
    limits = {
        "chat_action_store.py": 650,
        "control_plane/collaboration/action_store_common.py": 700,
        "control_plane/collaboration/action_store_review_cards.py": 700,
        "control_plane/collaboration/action_store_operations.py": 700,
    }
    for filename, max_lines in limits.items():
        source = (root / filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        any_refs = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "Any"
        ]
        assert any_refs == [], f"{filename} must remain Zero-Any"
        assert "type: ignore" not in source
        assert "O_NOFOLLOW" not in source
        assert len(source.splitlines()) < max_lines

    assert callable(_utc_now)
    assert callable(_opaque_id)
    assert callable(normalize_idempotency_key)
    assert callable(_bounded_text)
    assert callable(_safe_json_value)
    assert callable(_canonical_digest)
    assert callable(atomic_write_json)
    assert callable(exclusive_file_lock)
    assert isinstance(ACTION_KINDS, set)
    assert _OPAQUE_ID.fullmatch("proposal_1")


def test_store_monkeypatch_hooks_propagate_across_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamps = iter(
        [
            "2026-11-26T00:00:01Z",
            "2026-11-26T00:00:02Z",
            "2026-11-26T00:00:03Z",
            "2026-11-26T00:00:04Z",
            "2026-11-26T00:00:05Z",
            "2026-11-26T00:00:06Z",
            "2026-11-26T00:00:07Z",
            "2026-11-26T00:00:08Z",
            "2026-11-26T00:00:09Z",
            "2026-11-26T00:00:10Z",
        ]
    )
    lock_calls: list[tuple[str | None, str | None]] = []
    real_lock = chat_action_store.exclusive_file_lock

    @contextmanager
    def spy_lock(path: Path, **kwargs: object) -> Iterator[Path]:
        agent_id = kwargs.get("agent_id")
        operation = kwargs.get("operation")
        lock_calls.append(
            (
                str(agent_id) if agent_id is not None else None,
                str(operation) if operation is not None else None,
            )
        )
        with real_lock(
            path,
            agent_id=str(agent_id) if isinstance(agent_id, str) else None,
            operation=str(operation) if isinstance(operation, str) else None,
        ) as locked:
            yield locked

    monkeypatch.setattr("loopx.chat_action_store._utc_now", lambda: next(stamps))
    monkeypatch.setattr(chat_action_store, "exclusive_file_lock", spy_lock)
    write_paths: list[Path] = []
    real_write = chat_action_store.atomic_write_json

    def spy_write(
        path: Path, payload: dict[str, object], *, preserve_mode: bool = False
    ) -> None:
        write_paths.append(path)
        real_write(path, payload, preserve_mode=preserve_mode)

    monkeypatch.setattr(chat_action_store, "atomic_write_json", spy_write)

    store = ChatActionStore(tmp_path / "actions")
    assert len(write_paths) == 1
    preview = store.create_preview(
        action_kind="team.plan",
        summary="Confirm team plan",
        normalized_parameters={"goal_id": "goal_1"},
        context={"kind": "goal", "goal_id": "goal_1"},
        expected_state_fingerprint="fp-1",
        permission_classification="owner_only",
        validation_evidence=["ok"],
        available_transitions=["apply", "cancel"],
        idempotency_key="idem-plan-1",
    )
    assert preview["created_at"] == "2026-11-26T00:00:01Z"

    proposal_id = str(preview["proposal_id"])
    prepared = store.prepare_review_card_delivery(
        proposal_id, audience_ids=["aud_a", "aud_b"], authorized_principal="user_1"
    )
    assert prepared["updated_at"] == "2026-11-26T00:00:02Z"

    card = {"title": "Plan"}
    card_digest = _canonical_digest(card)
    for audience, msg_id in (("aud_a", "om_a"), ("aud_b", "om_b")):
        store.record_review_card_delivery(
            proposal_id,
            audience_id=audience,
            delivery={
                "provider": "lark",
                "message_id": msg_id,
                "chat_id": "oc_1",
                "app_id": "cli_1",
                "cli_bin": "lark-cli",
                "sender_profile": "default",
                "binding_digest": "bind_1",
                "card_digest": card_digest,
                "submitted_card": card,
                "delivered_at": "2026-11-26T00:00:02Z",
                "authorized_principal": "user_1",
            },
        )

    decided = store.decide_review_card(
        proposal_id,
        decision="confirm",
        confirmation={
            "provider": "lark",
            "event_id": "evt_1",
            "principal": "user_1",
            "message_id": "om_a",
            "chat_id": "oc_1",
            "app_id": "cli_1",
            "audience_id": "aud_a",
            "state_fingerprint": "fp-1",
            "card_digest": card_digest,
            "confirmed_at": "2026-11-26T00:00:05Z",
            "actor_class": HUMAN_ACTOR_CLASS,
            "verification_kind": "callback_sig",
        },
    )
    assert decided["status"] == "applying"
    assert decided["updated_at"] == "2026-11-26T00:00:05Z"

    recorded = store.record_review_card_result_delivery(
        proposal_id,
        audience_id="aud_a",
        result={
            "card_digest": "res_1",
            "transport": "callback_update",
            "delivered_at": "2026-11-26T00:00:06Z",
        },
    )
    assert recorded["updated_at"] == "2026-11-26T00:00:06Z"
    assert ("loopx-chat", "decide_review_card") in lock_calls
    assert ("loopx-chat", "record_review_card_result_delivery") in lock_calls
    assert len(write_paths) >= 7


def test_operation_replay_cancel_and_stale_transitions(tmp_path: Path) -> None:
    store = ChatActionStore(tmp_path / "actions")
    preview = store.create_preview(
        action_kind="operation.execute",
        summary="Run transfer",
        normalized_parameters={
            "goal_id": "goal_1",
            "expires_at": "2099-01-01T00:00:00Z",
            "authorized_principals": ["user_1"],
        },
        context={"kind": "goal", "goal_id": "goal_1"},
        expected_state_fingerprint="fp-op",
        permission_classification="operator_confirmed",
        validation_evidence=["checked"],
        available_transitions=["cancel"],
        idempotency_key="op-key-1",
    )
    proposal_id = str(preview["proposal_id"])

    reused = store.replay_or_reuse_preview(
        action_kind="operation.execute",
        context={"kind": "goal", "goal_id": "goal_1"},
        normalized_parameters={
            "goal_id": "goal_1",
            "expires_at": "2099-01-01T00:00:00Z",
            "authorized_principals": ["user_1"],
        },
        idempotency_key="op-key-2",
        shared_fields=("goal_id",),
        reusable_states={"preview_ready"},
        matches_review_basis=lambda fp: fp == "fp-op",
    )
    assert reused is not None
    assert reused["proposal_id"] == proposal_id

    armed = store.arm_operation(proposal_id)
    assert armed["status"] == "gated"

    cancelled = store.cancel(proposal_id)
    assert cancelled["status"] == "cancelled"
    assert isinstance(cancelled["operation"], dict)
    assert cancelled["operation"]["lifecycle_state"] == "outcome_observed"
    assert cancelled["operation"]["outcome"]["outcome"] == "cancelled_before_confirmation"

    with pytest.raises(ActionConflictError):
        store.mark_rejected(proposal_id)

    drift = store.create_preview(
        action_kind="goal.update",
        summary="Update objective",
        normalized_parameters={"goal_id": "goal_1"},
        context={"kind": "goal", "goal_id": "goal_1"},
        expected_state_fingerprint="fp-expected",
        permission_classification="owner_only",
        validation_evidence=["ok"],
        available_transitions=["apply"],
        idempotency_key="goal-upd-1",
    )
    stale = store.apply(
        str(drift["proposal_id"]),
        current_state_fingerprint="fp-other",
        receipt={"receipt_id": "r_1", "outcome": "done", "projection_verified": True},
    )
    assert stale["status"] == "stale"
    assert isinstance(stale["stale"], dict)
    assert stale["stale"]["expected_state_fingerprint"] == "fp-expected"
