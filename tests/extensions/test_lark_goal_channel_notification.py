from __future__ import annotations

import json
from pathlib import Path

from loopx.extensions.lark.goal_channel_contracts import (
    GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
    human_gate_auto_notify_marker_path,
    write_goal_channel_binding,
    write_human_gate_auto_notify_marker,
)
from loopx.extensions.lark.goal_channel_notification import (
    GOAL_CHANNEL_NOTIFICATION_PROJECTION_SCHEMA_VERSION,
    build_goal_channel_notification_projection,
)


GOAL_ID = "goal-notification-fixture"
OTHER_GOAL_ID = "goal-notification-other"


def _registry(tmp_path: Path, goal_ids: list[str] | None = None) -> Path:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": goal_id,
                        "objective": "Deliver public-safe notification state.",
                        "repo": str(tmp_path),
                        "state_file": str(tmp_path / "ACTIVE_GOAL_STATE.md"),
                        "adapter": {"kind": "read_only_project_map_v0"},
                    }
                    for goal_id in (goal_ids or [GOAL_ID])
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry_path


def _write_binding(tmp_path: Path, binding: dict) -> Path:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    write_goal_channel_binding(
        binding_path,
        {
            "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
            "bindings": {GOAL_ID: binding},
        },
    )
    return binding_path


def test_missing_registry_degrades_to_empty_goals(tmp_path: Path) -> None:
    projection = build_goal_channel_notification_projection(
        registry_path=tmp_path / "missing.json"
    )

    assert projection["schema_version"] == (
        GOAL_CHANNEL_NOTIFICATION_PROJECTION_SCHEMA_VERSION
    )
    assert projection["goals"] == []


def test_goal_without_binding_is_unconfigured(tmp_path: Path) -> None:
    registry_path = _registry(tmp_path)

    projection = build_goal_channel_notification_projection(
        registry_path=registry_path
    )

    assert projection["goals"] == [
        {
            "goal_id": GOAL_ID,
            "configured": False,
            "enabled": False,
            "human_gate_auto_notify_enabled": False,
            "blocked_notice_auto_notify_enabled": False,
            "receipt_count": 0,
        }
    ]


def test_configured_binding_projects_public_state(tmp_path: Path, monkeypatch) -> None:
    def unexpected_target_read(*args):
        raise AssertionError("A human-gate-only binding must not read blocked-notice targets")
    monkeypatch.setattr(
        "loopx.extensions.lark.goal_channel_notification.read_goal_channel_targets",
        unexpected_target_read,
    )
    registry_path = _registry(tmp_path)
    _write_binding(
        tmp_path,
        {
            "goal_id": GOAL_ID,
            "provider": "lark",
            "enabled": True,
            "target_ref": "shared-lark",
            "automation": {"human_gate_auto_notify_enabled": True},
            "receipts": {
                "gate-1": {
                    "verified_at": "2026-08-10T09:00:00+00:00",
                    "message_id": "om_secret_message",
                    "chat_id": "oc_secret_chat",
                },
                "gate-2": {
                    "verified_at": "2026-08-11T10:30:00+00:00",
                    "message_id": "om_secret_message_2",
                },
            },
        },
    )

    projection = build_goal_channel_notification_projection(
        registry_path=registry_path
    )

    row = projection["goals"][0]
    assert row["goal_id"] == GOAL_ID
    assert row["configured"] is True
    assert row["enabled"] is True
    assert row["human_gate_auto_notify_enabled"] is True
    assert row["target_ref"] == "shared-lark"
    assert row["receipt_count"] == 2
    assert row["last_notified_at"] == "2026-08-11T10:30:00+00:00"
    encoded = json.dumps(projection)
    assert "om_secret" not in encoded
    assert "oc_secret" not in encoded


def test_marker_file_enables_auto_notify(tmp_path: Path) -> None:
    registry_path = _registry(tmp_path)
    binding_path = _write_binding(
        tmp_path,
        {
            "goal_id": GOAL_ID,
            "provider": "lark",
            "enabled": True,
            "automation": {"human_gate_auto_notify_enabled": False},
            "receipts": {},
        },
    )
    write_human_gate_auto_notify_marker(
        human_gate_auto_notify_marker_path(binding_path, GOAL_ID)
    )

    projection = build_goal_channel_notification_projection(
        registry_path=registry_path
    )

    row = projection["goals"][0]
    assert row["configured"] is True
    assert row["human_gate_auto_notify_enabled"] is True


def test_corrupt_binding_degrades_to_unconfigured(tmp_path: Path) -> None:
    registry_path = _registry(tmp_path)
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.write_text("{not json", encoding="utf-8")

    projection = build_goal_channel_notification_projection(
        registry_path=registry_path
    )

    row = projection["goals"][0]
    assert row["configured"] is False
    assert row["enabled"] is False


def test_private_target_ref_is_dropped(tmp_path: Path) -> None:
    registry_path = _registry(tmp_path)
    _write_binding(
        tmp_path,
        {
            "goal_id": GOAL_ID,
            "provider": "lark",
            "enabled": True,
            "target_ref": "oc_private_chat_id",
            "automation": {},
            "receipts": {},
        },
    )

    projection = build_goal_channel_notification_projection(
        registry_path=registry_path
    )

    row = projection["goals"][0]
    assert row["configured"] is True
    assert "target_ref" not in row
    assert "oc_private" not in json.dumps(projection)


def test_goal_id_filter_selects_one_goal(tmp_path: Path) -> None:
    registry_path = _registry(tmp_path, goal_ids=[GOAL_ID, OTHER_GOAL_ID])

    projection = build_goal_channel_notification_projection(
        registry_path=registry_path,
        goal_id=OTHER_GOAL_ID,
    )

    assert [row["goal_id"] for row in projection["goals"]] == [OTHER_GOAL_ID]
