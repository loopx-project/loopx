from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from threading import Event

import pytest

from loopx.control_plane.work_items.work_lane import (
    lark_inbox_reply_due_work_lane_contract,
    operator_inbox_material_review_due_work_lane_contract,
    work_lane_contract_is_lark_inbox_reply_due,
    work_lane_contract_is_operator_inbox_material_review_due,
)
from loopx.extensions.external_connector_runtime import EFFECT_RECEIPT_SCHEMA_VERSION
from loopx.extensions.lark import event_inbox as event_inbox_module
from loopx.extensions.lark.event_inbox import (
    acknowledge_lark_event_inbox,
    ingest_lark_event_inbox,
    inspect_lark_event_inbox,
    project_lark_event_inbox_urgency,
    settle_lark_event_inbox_material_review,
)


def _config(project: Path) -> Path:
    config = project / ".loopx" / "config" / "lark" / "event-inbox.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_config_v0",
                "enabled": True,
                "inbox_dir": ".loopx/inbox/lark/material-review",
                "capture_scope": "configured_chat_all",
                "reply": {
                    "enabled": True,
                    "sender_profile": "fixture-bot",
                    "sender_identity": "bot",
                    "bot_display_name": "Context Bot",
                    "chat_id": "oc_public_fixture",
                },
                "material_review": {"enabled": True, "drain_limit": 3},
            }
        ),
        encoding="utf-8",
    )
    return config


def _ingest(project: Path, config: Path) -> None:
    result = ingest_lark_event_inbox(
        project=project,
        config_path=config,
        events=[
            {
                "schema_version": "lark_event_inbox_event_v0",
                "event_id": "evt_material_text",
                "message_id": "om_material_text",
                "create_time": "2026-08-26T00:00:00Z",
                "content": "Iteration 42 is now planned.",
            },
            {
                "schema_version": "lark_event_inbox_event_v0",
                "event_id": "evt_material_attachment",
                "message_id": "om_material_attachment",
                "create_time": "2026-08-26T00:01:00Z",
                "content": "",
                "attachment_count": 1,
            },
            {
                "schema_version": "lark_event_inbox_event_v0",
                "event_id": "evt_direct",
                "message_id": "om_direct",
                "create_time": "2026-08-26T00:02:00Z",
                "content": "@Context Bot can you confirm?",
                "mentions": [{"name": "Context Bot"}],
            },
        ],
        execute=True,
    )
    assert result["accepted_count"] == 3


def _boundary(urgency: dict[str, object]) -> dict[str, object]:
    return {
        "capabilities": {
            "lark_event_inbox": {
                "urgency": urgency,
                "drain_command": "loopx lark-inbox drain --goal-id fixture",
            }
        }
    }


def test_material_review_projection_is_separate_from_reply_due(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _ingest(tmp_path, config)

    urgency = project_lark_event_inbox_urgency(
        project=tmp_path,
        config_path=config,
    )

    assert urgency["pending_count"] == 3
    assert urgency["attention_required_count"] == 1
    assert urgency["reply_due"] is True
    assert urgency["material_review_count"] == 2
    assert urgency["material_attachment_count"] == 1
    assert urgency["material_review_due"] is True
    assert urgency["material_review_drain_limit"] == 3
    assert urgency["local_private_content_returned"] is False

    material = operator_inbox_material_review_due_work_lane_contract(
        _boundary(urgency),
        current_contract={"lane": "advancement_task"},
    )
    assert work_lane_contract_is_operator_inbox_material_review_due(material)
    assert material["drain_limit"] == 3
    assert str(material["drain_command"]).endswith("--limit 3")
    assert material["semantic_triage_required"] is True
    assert material["allowed_dispositions"] == [
        "steer_current_turn",
        "replan_goal",
        "record_context",
        "continue_current_work",
        "no_follow_up",
    ]
    assert "before ordinary work" in str(material["action"])

    reply = lark_inbox_reply_due_work_lane_contract(
        _boundary(urgency),
        current_contract=material,
    )
    assert work_lane_contract_is_lark_inbox_reply_due(reply)
    assert reply["next_lane"] == "operator_inbox_material_review"
    assert reply["semantic_triage_required"] is True
    assert reply["allowed_dispositions"] == [
        "steer_current_turn",
        "replan_goal",
        "record_context",
        "continue_current_work",
    ]
    assert "before ordinary work" in str(reply["action"])


def test_configured_chat_reply_due_requires_typed_bot_addressing(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    result = ingest_lark_event_inbox(
        project=tmp_path,
        config_path=config,
        events=[
            {
                "schema_version": "lark_event_inbox_event_v0",
                "event_id": "evt_other_mention",
                "message_id": "om_other_mention",
                "create_time": "2026-08-26T00:00:00Z",
                "content": "@Alice can LoopX handle this?",
                "mentions": [{"name": "Alice"}],
                "mentioned": False,
            },
            {
                "schema_version": "lark_event_inbox_event_v0",
                "event_id": "evt_bot_prose",
                "message_id": "om_bot_prose",
                "create_time": "2026-08-26T00:01:00Z",
                "content": "Context Bot is part of the LoopX discussion.",
                "mentions": [],
                "mentioned": False,
            },
            {
                "schema_version": "lark_event_inbox_event_v0",
                "event_id": "evt_verified_mention",
                "message_id": "om_verified_mention",
                "create_time": "2026-08-26T00:02:00Z",
                "content": "@Context Bot can you confirm?",
                "mentions": [{"name": "Context Bot"}],
            },
        ],
        execute=True,
    )

    urgency = project_lark_event_inbox_urgency(
        project=tmp_path,
        config_path=config,
    )
    inbox = tmp_path / ".loopx" / "inbox" / "lark" / "material-review"
    other = json.loads((inbox / "om_other_mention.json").read_text(encoding="utf-8"))
    direct = json.loads(
        (inbox / "om_verified_mention.json").read_text(encoding="utf-8")
    )

    assert result["accepted_count"] == 3
    assert urgency["pending_count"] == 3
    assert urgency["direct_question_count"] == 1
    assert urgency["direct_mention_count"] == 0
    assert urgency["attention_required_count"] == 1
    assert urgency["material_review_count"] == 2
    assert urgency["reply_due"] is True
    assert other["addressed_to_bot"] is False
    assert direct["addressed_to_bot"] is True
    assert "mentions" not in direct
    assert "mentioned" not in direct


def test_legacy_configured_chat_event_without_addressing_evidence_fails_closed(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    inbox = tmp_path / ".loopx" / "inbox" / "lark" / "material-review"
    inbox.mkdir(parents=True)
    (inbox / "om_legacy_text_only.json").write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_event_v0",
                "event_id": "evt_legacy_text_only",
                "message_id": "om_legacy_text_only",
                "create_time": "2026-08-26T00:00:00Z",
                "content": "@Context Bot can you confirm?",
                "reply_context_verified": False,
                "reply_to_bot": False,
            }
        ),
        encoding="utf-8",
    )

    urgency = project_lark_event_inbox_urgency(
        project=tmp_path,
        config_path=config,
    )

    assert urgency["pending_count"] == 1
    assert urgency["attention_required_count"] == 0
    assert urgency["reply_due"] is False
    assert urgency["material_review_count"] == 1
    assert urgency["material_review_due"] is True


def test_material_review_no_follow_up_settlement_is_idempotent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _ingest(tmp_path, config)

    preview = settle_lark_event_inbox_material_review(
        project=tmp_path,
        config_path=config,
        message_id="om_material_text",
        no_follow_up_reason="Informational context already captured.",
    )
    assert preview["ok"] is True
    assert preview["status"] == "preview_ready"
    assert preview["receipt_recorded"] is False
    assert preview["write_performed"] is False

    first = settle_lark_event_inbox_material_review(
        project=tmp_path,
        config_path=config,
        message_id="om_material_text",
        no_follow_up_reason="Informational context already captured.",
        execute=True,
    )
    replay = settle_lark_event_inbox_material_review(
        project=tmp_path,
        config_path=config,
        message_id="om_material_text",
        no_follow_up_reason="Informational context already captured.",
        execute=True,
    )

    assert first["status"] == "settled"
    assert first["effect_kind"] == "no_follow_up"
    assert first["receipt_recorded"] is True
    assert first["write_performed"] is True
    assert replay["status"] == "already_settled"
    assert replay["write_performed"] is False
    conflict = settle_lark_event_inbox_material_review(
        project=tmp_path,
        config_path=config,
        message_id="om_material_text",
        no_follow_up_reason="A conflicting replay rationale.",
        execute=True,
    )
    assert conflict["ok"] is False
    assert conflict["status"] == "material_review_receipt_conflict"
    assert inspect_lark_event_inbox(
        project=tmp_path,
        config_path=config,
    )["pending_count"] == 2


def test_material_review_requires_event_bound_effect_and_rejects_reply_items(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _ingest(tmp_path, config)
    mismatched = {
        "schema_version": EFFECT_RECEIPT_SCHEMA_VERSION,
        "event_id": "evt_other",
        "effect_id": "todo-update-fixture",
        "effect_kind": "todo_update",
        "status": "committed",
    }
    rejected = settle_lark_event_inbox_material_review(
        project=tmp_path,
        config_path=config,
        message_id="om_material_attachment",
        effect_receipt=mismatched,
        execute=True,
    )
    assert rejected["ok"] is False
    assert rejected["status"] == "durable_effect_required"
    assert rejected["write_performed"] is False

    committed = {**mismatched, "event_id": "evt_material_attachment"}
    settled = settle_lark_event_inbox_material_review(
        project=tmp_path,
        config_path=config,
        message_id="om_material_attachment",
        effect_receipt=committed,
        execute=True,
    )
    assert settled["ok"] is True
    assert settled["effect_kind"] == "todo_update"

    with pytest.raises(ValueError, match="reply_due"):
        settle_lark_event_inbox_material_review(
            project=tmp_path,
            config_path=config,
            message_id="om_direct",
            no_follow_up_reason="Not a material review item.",
        )


def test_concurrent_material_settlements_preserve_both_state_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    _ingest(tmp_path, config)
    writer_entered = Event()
    release_writer = Event()
    original_writer = event_inbox_module._write_material_review_ledger

    def blocked_first_writer(path: Path, payload: object) -> None:
        if not writer_entered.is_set():
            writer_entered.set()
            assert release_writer.wait(timeout=5)
        original_writer(path, payload)  # type: ignore[arg-type]

    monkeypatch.setattr(
        event_inbox_module,
        "_write_material_review_ledger",
        blocked_first_writer,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            settle_lark_event_inbox_material_review,
            project=tmp_path,
            config_path=config,
            message_id="om_material_text",
            no_follow_up_reason="Text reviewed.",
            execute=True,
        )
        assert writer_entered.wait(timeout=5)
        second = pool.submit(
            settle_lark_event_inbox_material_review,
            project=tmp_path,
            config_path=config,
            message_id="om_material_attachment",
            no_follow_up_reason="Attachment reviewed.",
            execute=True,
        )
        with pytest.raises(FutureTimeout):
            second.result(timeout=0.05)
        release_writer.set()
        assert first.result(timeout=5)["status"] == "settled"
        assert second.result(timeout=5)["status"] == "settled"

    inbox = tmp_path / ".loopx" / "inbox" / "lark" / "material-review"
    ledger = json.loads(
        (inbox / "material-review" / "receipts.json").read_text(encoding="utf-8")
    )
    processed = json.loads((inbox / "processed.json").read_text(encoding="utf-8"))
    assert set(ledger["receipts"]) == {
        "om_material_text",
        "om_material_attachment",
    }
    assert set(processed["message_ids"]) == {
        "om_material_text",
        "om_material_attachment",
    }


def test_concurrent_same_message_replay_and_conflict_are_serialized(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _ingest(tmp_path, config)

    with ThreadPoolExecutor(max_workers=2) as pool:
        same = [
            pool.submit(
                settle_lark_event_inbox_material_review,
                project=tmp_path,
                config_path=config,
                message_id="om_material_text",
                no_follow_up_reason="Reviewed once.",
                execute=True,
            )
            for _ in range(2)
        ]
    assert {future.result()["status"] for future in same} == {
        "settled",
        "already_settled",
    }

    receipts = [
        {
            "schema_version": EFFECT_RECEIPT_SCHEMA_VERSION,
            "event_id": "evt_material_attachment",
            "effect_id": effect_id,
            "effect_kind": "todo_update",
            "status": "committed",
        }
        for effect_id in ("todo-update-one", "todo-update-two")
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        conflicting = [
            pool.submit(
                settle_lark_event_inbox_material_review,
                project=tmp_path,
                config_path=config,
                message_id="om_material_attachment",
                effect_receipt=receipt,
                execute=True,
            )
            for receipt in receipts
        ]
    results = [future.result() for future in conflicting]
    assert sum(result["ok"] is True for result in results) == 1
    assert sum(result["status"] == "material_review_receipt_conflict" for result in results) == 1


def test_material_settlement_repairs_ledger_written_ack_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    _ingest(tmp_path, config)
    original_ack = event_inbox_module._acknowledge_lark_event_inbox_state

    def interrupted_ack(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("simulated interruption after ledger commit")

    monkeypatch.setattr(
        event_inbox_module,
        "_acknowledge_lark_event_inbox_state",
        interrupted_ack,
    )
    with pytest.raises(RuntimeError, match="simulated interruption"):
        settle_lark_event_inbox_material_review(
            project=tmp_path,
            config_path=config,
            message_id="om_material_text",
            no_follow_up_reason="Reviewed before interruption.",
            execute=True,
        )
    inbox = tmp_path / ".loopx" / "inbox" / "lark" / "material-review"
    assert (inbox / "material-review" / "receipts.json").is_file()
    assert not (inbox / "processed.json").is_file()

    monkeypatch.setattr(
        event_inbox_module,
        "_acknowledge_lark_event_inbox_state",
        original_ack,
    )
    repaired = settle_lark_event_inbox_material_review(
        project=tmp_path,
        config_path=config,
        message_id="om_material_text",
        no_follow_up_reason="Reviewed before interruption.",
        execute=True,
    )
    assert repaired["status"] == "settled"
    assert repaired["receipt_recorded"] is True
    assert json.loads((inbox / "processed.json").read_text(encoding="utf-8"))[
        "message_ids"
    ] == ["om_material_text"]


def test_generic_ack_and_material_settlement_share_state_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    _ingest(tmp_path, config)
    writer_entered = Event()
    release_writer = Event()
    original_writer = event_inbox_module._write_material_review_ledger

    def blocked_writer(path: Path, payload: object) -> None:
        writer_entered.set()
        assert release_writer.wait(timeout=5)
        original_writer(path, payload)  # type: ignore[arg-type]

    monkeypatch.setattr(
        event_inbox_module,
        "_write_material_review_ledger",
        blocked_writer,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        material = pool.submit(
            settle_lark_event_inbox_material_review,
            project=tmp_path,
            config_path=config,
            message_id="om_material_text",
            no_follow_up_reason="Material reviewed.",
            execute=True,
        )
        assert writer_entered.wait(timeout=5)
        generic = pool.submit(
            acknowledge_lark_event_inbox,
            project=tmp_path,
            config_path=config,
            message_ids=["om_direct"],
            execute=True,
        )
        with pytest.raises(FutureTimeout):
            generic.result(timeout=0.05)
        release_writer.set()
        assert material.result(timeout=5)["status"] == "settled"
        assert generic.result(timeout=5)["new_count"] == 1

    inbox = tmp_path / ".loopx" / "inbox" / "lark" / "material-review"
    processed = json.loads((inbox / "processed.json").read_text(encoding="utf-8"))
    assert set(processed["message_ids"]) == {"om_material_text", "om_direct"}


def test_processed_state_path_damage_never_reopens_acknowledged_events(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _ingest(tmp_path, config)
    acknowledge_lark_event_inbox(
        project=tmp_path,
        config_path=config,
        message_ids=["om_direct"],
        execute=True,
    )
    inbox = tmp_path / ".loopx" / "inbox" / "lark" / "material-review"
    processed = inbox / "processed.json"
    processed.unlink()
    processed.mkdir()

    with pytest.raises(IsADirectoryError):
        inspect_lark_event_inbox(project=tmp_path, config_path=config)
    with pytest.raises(IsADirectoryError):
        project_lark_event_inbox_urgency(project=tmp_path, config_path=config)
    with pytest.raises(IsADirectoryError):
        acknowledge_lark_event_inbox(
            project=tmp_path,
            config_path=config,
            message_ids=["om_direct"],
        )


def test_material_review_receipt_path_damage_is_not_an_empty_ledger(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _ingest(tmp_path, config)
    receipts = (
        tmp_path
        / ".loopx"
        / "inbox"
        / "lark"
        / "material-review"
        / "material-review"
        / "receipts.json"
    )
    receipts.mkdir(parents=True)

    with pytest.raises(IsADirectoryError):
        settle_lark_event_inbox_material_review(
            project=tmp_path,
            config_path=config,
            message_id="om_material_text",
            no_follow_up_reason="Reviewed before ledger damage.",
        )
