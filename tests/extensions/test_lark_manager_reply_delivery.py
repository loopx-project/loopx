from __future__ import annotations

import stat
import json
from datetime import UTC, datetime

import pytest

from loopx.extensions.lark.manager_reply_delivery import (
    load_delivery,
    pending_delivery,
    write_delivery,
)
from loopx.extensions.lark.event_inbox import ingest_lark_event_inbox, inspect_lark_event_inbox
from loopx.extensions.lark.manager_context import (
    compact_manager_context,
    manager_context_materials_for_ids,
)
from test_lark_inbox_reactions import _fixture


def test_manager_delivery_state_is_private_event_bound_and_tamper_evident(
    tmp_path,
):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    event = {
        "event_id": "evt_reply_fixture",
        "message_id": "om_reaction_fixture",
        "sender_id": "ou_owner_fixture",
        "content": "Give me a status update.",
    }
    path, existing = load_delivery(
        project=project, config_path=config, event=event
    )
    assert existing is None
    payload = pending_delivery(
        event=event,
        text="Complete answer.",
        content_format="markdown",
        effect_receipt=None,
        failure_code=None,
    )

    write_delivery(path, payload)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    _, loaded = load_delivery(project=project, config_path=config, event=event)
    assert loaded == payload

    tampered = {**payload, "delivery_text": "Changed answer."}
    write_delivery(path, tampered)
    with pytest.raises(ValueError, match="pending delivery content"):
        load_delivery(project=project, config_path=config, event=event)


def test_sent_verified_state_requires_readback_and_idempotency_receipt(tmp_path):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    event = {
        "event_id": "evt_reply_fixture",
        "message_id": "om_reaction_fixture",
        "sender_id": "ou_owner_fixture",
        "content": "Give me a status update.",
    }
    path, _ = load_delivery(project=project, config_path=config, event=event)
    payload = pending_delivery(
        event=event,
        text="Complete answer.",
        content_format="text",
        effect_receipt=None,
        failure_code=None,
    )
    payload["status"] = "sent_verified"
    write_delivery(path, payload)

    with pytest.raises(ValueError, match="verified delivery receipt"):
        load_delivery(project=project, config_path=config, event=event)


def test_manager_delivery_persists_and_validates_exact_context_ids(tmp_path):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    event = {
        "event_id": "evt_reply_fixture",
        "message_id": "om_reaction_fixture",
        "sender_id": "ou_owner_fixture",
        "content": "Give me a status update.",
    }
    path, _ = load_delivery(project=project, config_path=config, event=event)
    payload = pending_delivery(
        event=event,
        text="Complete answer.",
        content_format="markdown",
        effect_receipt=None,
        failure_code=None,
        context_material_ids=["om_context_a", "om_context_b"],
    )
    write_delivery(path, payload)

    _, loaded = load_delivery(project=project, config_path=config, event=event)
    assert loaded["context_material_ids"] == ["om_context_a", "om_context_b"]

    write_delivery(path, {**payload, "context_material_ids": ["om_context_a", "om_context_a"]})
    with pytest.raises(ValueError, match="context material ids"):
        load_delivery(project=project, config_path=config, event=event)


def test_manager_delivery_rejects_an_unverified_proposal_delivery_receipt(tmp_path):
    config, _, project = _fixture(tmp_path, lifecycle=False)
    event = {
        "event_id": "evt_reply_fixture",
        "message_id": "om_reaction_fixture",
        "sender_id": "ou_owner_fixture",
        "content": "Prepare the team plan.",
    }
    proposal_id = "proposal-" + "a" * 32
    path, _ = load_delivery(project=project, config_path=config, event=event)
    payload = pending_delivery(
        event=event,
        text="Plan ready.",
        content_format="markdown",
        effect_receipt=None,
        failure_code=None,
        proposal_ids=[proposal_id],
    )
    payload["proposal_delivery"] = {"ok": True}
    write_delivery(path, payload)

    with pytest.raises(ValueError, match="proposal delivery receipt"):
        load_delivery(project=project, config_path=config, event=event)

    payload["proposal_delivery"] = {
        "schema_version": "lark_team_plan_review_delivery_v0",
        "ok": True,
        "status": "team_plan_review_cards_delivered",
        "proposal_ids": [proposal_id],
        "proposal_count": 1,
        "audience_count": 2,
        "readback_verified": True,
        "external_write_count": 2,
    }
    write_delivery(path, payload)

    _, loaded = load_delivery(project=project, config_path=config, event=event)
    assert loaded["proposal_delivery"]["proposal_ids"] == [proposal_id]


def test_manager_context_retention_discards_oldest_with_reason(tmp_path):
    config = tmp_path / ".loopx" / "config" / "lark.json"
    inbox = tmp_path / ".loopx" / "inbox" / "lark"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "schema_version": "lark_event_inbox_config_v0",
                "enabled": True,
                "inbox_dir": ".loopx/inbox/lark",
                "capture_scope": "configured_chat_all",
                "reply": {
                    "enabled": True,
                    "sender_profile": "fixture-bot",
                    "sender_identity": "bot",
                    "bot_display_name": "Fixture Bot",
                    "chat_id": "oc_public_fixture",
                },
                "material_review": {"enabled": True, "drain_limit": 8},
            }
        ),
        encoding="utf-8",
    )
    events = [
        {
            "schema_version": "lark_event_inbox_event_v0",
            "event_id": f"evt_context_{index}",
            "message_id": f"om_context_{index:02d}",
            "create_time": f"2026-09-13T00:{index:02d}:00Z",
            "content": f"background {index}",
            "mentions": [],
            "sender_type": "user",
        }
        for index in range(40)
    ]
    assert ingest_lark_event_inbox(
        project=tmp_path, config_path=config, events=events, execute=True
    )["accepted_count"] == 40
    projection = inspect_lark_event_inbox(
        project=tmp_path, config_path=config, limit=0
    )
    result = compact_manager_context(
        project=tmp_path,
        config_path=config,
        projection=projection,
        current_message_id="om_current",
        now=datetime(2026, 9, 14, tzinfo=UTC),
    )
    assert result["discarded_count"] == 8
    assert result["overflow_count"] == 8
    remaining = inspect_lark_event_inbox(
        project=tmp_path, config_path=config, limit=0
    )
    assert remaining["pending_count"] == 32
    retention = json.loads(
        (inbox / "material-review" / "retention.json").read_text(encoding="utf-8")
    )
    assert len(retention["receipts"]) == 8
    assert {item["reason"] for item in retention["receipts"].values()} == {
        "retention_overflow"
    }


def test_manager_context_retry_reuses_recorded_ids_over_newer_arrivals():
    projection = {
        "items": [
            {
                "message_id": "om_context_old",
                "create_time": "2026-09-13T00:00:00Z",
                "content": "old context",
                "addressed_to_bot": False,
            },
            {
                "message_id": "om_context_new",
                "create_time": "2026-09-13T01:00:00Z",
                "content": "newer arrival",
                "addressed_to_bot": False,
            },
        ]
    }
    selected = manager_context_materials_for_ids(
        projection,
        current_message_id="om_current",
        message_ids=["om_context_old"],
    )
    assert [item["message_id"] for item in selected] == ["om_context_old"]
