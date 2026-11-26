"""Contract, Zero-Any, and boundary tests for Lark Goal Channel contracts."""

from __future__ import annotations

import ast
from collections.abc import Mapping
import json
from pathlib import Path
from types import MappingProxyType
import tokenize
from typing import cast

import pytest

from loopx.extensions.lark import goal_channel_contracts, goal_channel_gate_contracts
from loopx.registry import registry_goals as real_registry_goals

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ast_any_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "Any"]


def _suppression_lines(path: Path) -> list[int]:
    lines: list[int] = []
    with path.open("r", encoding="utf-8") as handle:
        for tok in tokenize.generate_tokens(handle.readline):
            if tok.type == tokenize.COMMENT and ("type: ignore" in tok.string or "noqa" in tok.string):
                lines.append(tok.start[0])
    return lines


def test_goal_channel_modules_meet_ceiling_zero_any_and_inventory_invariants() -> None:
    contracts_path = REPO_ROOT / "loopx/extensions/lark/goal_channel_contracts.py"
    gate_path = REPO_ROOT / "loopx/extensions/lark/goal_channel_gate_contracts.py"
    test_path = Path(__file__).resolve()

    assert len(contracts_path.read_text(encoding="utf-8").splitlines()) <= 550
    assert len(gate_path.read_text(encoding="utf-8").splitlines()) < 750
    assert len(test_path.read_text(encoding="utf-8").splitlines()) < 500
    for path in (contracts_path, gate_path, test_path):
        assert _ast_any_lines(path) == [], str(path)
        assert _suppression_lines(path) == [], str(path)

    inventory = json.loads(
        (REPO_ROOT / "loopx/semantics/goal_instance_binding_inventory_v1.json").read_text(encoding="utf-8")
    )
    owners = inventory.get("owners") or inventory.get("entries") or inventory
    assert "loopx/extensions/lark/goal_channel_contracts.py::save_goal_binding" in json.dumps(owners)
    assert goal_channel_contracts.save_goal_binding.__module__ == "loopx.extensions.lark.goal_channel_contracts"

    shared_gate_exports = (
        "DEFAULT_GATE_COOLDOWN_SECONDS",
        "HUMAN_GATE_AUTO_NOTIFY_SETTING",
        "BLOCKED_NOTICE_AUTO_NOTIFY_SETTING",
        "HUMAN_GATE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION",
        "BLOCKED_NOTICE_AUTO_NOTIFY_MARKER_SCHEMA_VERSION",
        "BlockedNoticeReceiptState",
        "BLOCKED_NOTICE_RETIRED_STATES",
        "blocked_notice_receipt_matches_target",
        "human_gate_auto_notify_enabled",
        "blocked_notice_auto_notify_enabled",
        "human_gate_auto_notify_marker_path",
        "human_gate_auto_notify_marker_enabled",
        "write_human_gate_auto_notify_marker",
        "clear_human_gate_auto_notify_marker",
        "blocked_notice_auto_notify_marker_path",
        "blocked_notice_auto_notify_marker_enabled",
        "write_blocked_notice_auto_notify_marker",
        "clear_blocked_notice_auto_notify_marker",
        "quota_human_gate_identity",
        "_quota_human_gate_items",
        "notification_request_snapshot",
        "_gate_notice_projection",
        "quota_human_gate_state_generation",
        "quota_human_gate_reminder_generation",
        "quota_human_gate_notification_suppressed",
        "quota_selects_human_gate",
        "semantic_key",
    )
    for name in shared_gate_exports:
        assert getattr(goal_channel_contracts, name) is getattr(goal_channel_gate_contracts, name), name


def test_routing_enum_serializer_registry_and_binding_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert goal_channel_contracts.normalize_lark_topic_event_rejection_reason("matched") is None
    assert goal_channel_contracts.normalize_lark_topic_event_rejection_reason("unknown") is None
    assert (
        goal_channel_contracts.normalize_lark_topic_event_rejection_reason(" self_message ")
        == "self_message"
    )

    locks: list[Path] = []

    class _FakeLock:
        def __init__(self, path: Path) -> None:
            self._path = path

        def __enter__(self) -> None:
            locks.append(self._path)

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        goal_channel_contracts,
        "exclusive_file_lock",
        lambda path, operation="": _FakeLock(path),
    )

    @goal_channel_contracts.serialize_goal_binding_mutation
    def _mutate(*, binding_path: Path, execute: bool = True) -> Path:
        return binding_path

    with pytest.raises(TypeError, match="binding_path must be a Path"):
        _mutate(binding_path=cast(Path, "not-a-path"))
    target_file = tmp_path / ".loopx" / "goal-channel.json"
    assert _mutate(binding_path=target_file, execute=False) == target_file
    assert locks == []
    assert _mutate(binding_path=target_file) == target_file
    assert locks == [target_file]

    with pytest.raises(ValueError, match="project source registry"):
        goal_channel_contracts.default_goal_channel_binding_path(tmp_path / "other" / "registry.json")
    assert goal_channel_contracts.default_goal_channel_binding_path(
        tmp_path / ".loopx" / "registry.json"
    ) == (tmp_path / ".loopx" / "goal-channel.json").resolve()

    missing = goal_channel_contracts.read_goal_channel_binding(target_file)
    assert missing == {
        "schema_version": goal_channel_contracts.GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
        "bindings": {},
    }
    target_file.parent.mkdir(parents=True)
    for bad_text, msg in (
        ("[]", "JSON object"),
        (json.dumps({"schema_version": "old", "bindings": {}}), "must use"),
        (
            json.dumps(
                {"schema_version": goal_channel_contracts.GOAL_CHANNEL_BINDING_SCHEMA_VERSION, "bindings": []}
            ),
            "object `bindings`",
        ),
    ):
        target_file.write_text(bad_text, encoding="utf-8")
        with pytest.raises(ValueError, match=msg):
            goal_channel_contracts.read_goal_channel_binding(target_file)

    registry_dict: dict[str, object] = {"goals": [{"id": "g-1", "objective": "Deliver"}]}
    seen_registry_objs: list[object] = []

    def _spy_registry_goals(reg: dict[str, object]) -> list[dict[str, object]]:
        seen_registry_objs.append(reg)
        return list(real_registry_goals(reg))

    monkeypatch.setattr(goal_channel_contracts, "registry_goals", _spy_registry_goals)
    assert goal_channel_contracts.goal_from_registry(registry_dict, " g-1 ") == {"id": "g-1", "objective": "Deliver"}
    assert seen_registry_objs[0] is registry_dict
    assert goal_channel_contracts.goal_from_registry(MappingProxyType(registry_dict), "g-1") == {
        "id": "g-1",
        "objective": "Deliver",
    }
    with pytest.raises(ValueError, match="not registered"):
        goal_channel_contracts.goal_from_registry(registry_dict, "missing")


def test_connection_set_selection_legacy_migration_and_save_preservation(tmp_path: Path) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    binding_path.parent.mkdir(parents=True)
    legacy_payload: dict[str, object] = {
        "schema_version": goal_channel_contracts.GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
        "bindings": {
            "g-1": {
                "enabled": True,
                "agent_id": "author",
                "target_ref": "main-group",
                "provider": "lark",
                "channel": {"pinned_message_id": "om_root1", "chat_id": "oc_old"},
            }
        },
    }
    legacy_cid = goal_channel_contracts.goal_channel_connection_id("g-1", "author")
    legacy_list = goal_channel_contracts.bindings_for_goal(legacy_payload, "g-1")
    assert len(legacy_list) == 1 and legacy_list[0]["connection_id"] == legacy_cid
    assert goal_channel_contracts.binding_for_goal(legacy_payload, "g-1", agent_id="other") is None
    assert goal_channel_contracts.binding_for_goal(legacy_payload, "g-1", connection_id="other") is None

    resolved = goal_channel_contracts.binding_for_goal(
        legacy_payload,
        "g-1",
        agent_id="author",
        provider_target={
            "name": "main-group",
            "provider": "lark",
            "channel": {"chat_id": "oc_new"},
            "identity": {"bot_app_id": "cli_123"},
        },
    )
    assert resolved is not None
    assert resolved["channel"] == {"chat_id": "oc_new", "pinned_message_id": "om_root1"}
    assert resolved["identity"] == {"bot_app_id": "cli_123"}

    with pytest.raises(ValueError, match="target_ref"):
        goal_channel_contracts.binding_for_goal(
            legacy_payload, "g-1", provider_target={"name": "other", "provider": "lark"}
        )

    peer_cid = goal_channel_contracts.save_goal_connection(
        binding_path=binding_path,
        payload=legacy_payload,
        goal_id="g-1",
        binding={"enabled": True, "agent_id": "peer", "target_ref": "peer-group", "provider": "lark"},
        make_default=False,
    )
    migrated = goal_channel_contracts.read_goal_channel_binding(binding_path)
    selected_peer = goal_channel_contracts.binding_for_goal(
        migrated,
        "g-1",
        agent_id="peer",
        provider_target={
            "name": "peer-group",
            "provider": "lark",
            "channel": {"chat_id": "oc_peer"},
            "identity": {"bot_app_id": "cli_peer"},
        },
    )
    assert selected_peer is not None and selected_peer["connection_id"] == peer_cid

    # Mutate default_connection_id to a stale value: fallback must deterministically pick min(connection_id).
    migrated_bindings = migrated["bindings"]
    assert isinstance(migrated_bindings, Mapping)
    raw_stored_set = migrated_bindings["g-1"]
    assert isinstance(raw_stored_set, Mapping)
    stored_set = dict(raw_stored_set)
    stored_set["default_connection_id"] = "missing_cid"
    fallback_payload: dict[str, object] = {**migrated, "bindings": {"g-1": stored_set}}
    fallback = goal_channel_contracts.binding_for_goal(fallback_payload, "g-1")
    assert fallback is not None and fallback["connection_id"] == min(legacy_cid, peer_cid)

    goal_channel_contracts.save_goal_binding(
        binding_path=binding_path,
        payload=migrated,
        goal_id="g-1",
        binding={"enabled": True, "agent_id": "author", "target_ref": "updated-default", "provider": "lark"},
    )
    after_save = goal_channel_contracts.read_goal_channel_binding(binding_path)
    assert len(goal_channel_contracts.bindings_for_goal(after_save, "g-1")) == 2


def test_public_packets_objective_redaction_and_reusable_topic_root() -> None:
    packet = goal_channel_contracts.operation_packet(
        ok=True,
        goal_id="g-1",
        operation="inspect",
        execute=False,
        status="ok",
        public_summary="clean summary",
        details={"note": "safe"},
    )
    assert packet["private_provider_payload_captured"] is False

    for bad_details in (
        {"chat_id": "secret"},
        {"nested": [{"text": "leak oc_abc123"}]},
        {"nested": {"msg": "om_xyz987"}},
    ):
        with pytest.raises(ValueError, match="public Goal Channel packet"):
            goal_channel_contracts.operation_packet(
                ok=True,
                goal_id="g-1",
                operation="inspect",
                execute=False,
                status="ok",
                public_summary="summary",
                details=bad_details,
            )

    assert goal_channel_contracts.goal_objective({}) is None
    assert goal_channel_contracts.goal_objective({"objective": "api_key=sk-secret1234567890"}) is None
    msg = goal_channel_contracts.control_message(goal_id="g-1", objective="Ship fix", kanban_url="https://example.test")
    assert "Kanban: https://example.test" in msg

    payload: dict[str, object] = {
        "schema_version": goal_channel_contracts.GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
        "bindings": {
            "g-1": {
                "enabled": True,
                "target_ref": "grp",
                "provider": "lark",
                "topic": {"root_message_id": "om_root99"},
                "channel": {"chat_id": "oc_chat1"},
            }
        },
    }
    cid = goal_channel_contracts.goal_channel_connection_id("g-1", None)
    assert (
        goal_channel_contracts.reusable_goal_topic_root(
            payload,
            "g-1",
            connection_id=cid,
            provider_target={
                "name": "grp",
                "provider": "lark",
                "channel": {"chat_id": "oc_chat1"},
                "identity": {"bot_app_id": "cli_1"},
            },
            chat_id="oc_chat1",
        )
        == "om_root99"
    )
    assert (
        goal_channel_contracts.reusable_goal_topic_root(
            payload,
            "g-1",
            connection_id=cid,
            provider_target={"name": "wrong", "provider": "lark"},
            chat_id="oc_chat1",
        )
        == ""
    )


def test_gate_markers_receipt_matching_and_quota_projections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding_path = tmp_path / ".loopx" / "goal-channel.json"
    for bad_goal in ("", ".", "..", "a/b", r"a\b"):
        with pytest.raises(ValueError, match="single path segment"):
            goal_channel_contracts.human_gate_auto_notify_marker_path(binding_path, bad_goal)

    h_marker = goal_channel_contracts.human_gate_auto_notify_marker_path(binding_path, "g-1")
    b_marker = goal_channel_contracts.blocked_notice_auto_notify_marker_path(binding_path, "g-1")
    assert not goal_channel_contracts.human_gate_auto_notify_marker_enabled(h_marker)
    goal_channel_contracts.write_human_gate_auto_notify_marker(h_marker)
    assert goal_channel_contracts.human_gate_auto_notify_marker_enabled(h_marker)
    goal_channel_contracts.clear_human_gate_auto_notify_marker(h_marker)
    assert not goal_channel_contracts.human_gate_auto_notify_marker_enabled(h_marker)
    b_marker.parent.mkdir(parents=True, exist_ok=True)
    b_marker.write_text("{broken-json", encoding="utf-8")
    assert goal_channel_contracts.blocked_notice_auto_notify_marker_enabled(b_marker)

    legacy_key = goal_channel_contracts.semantic_key("g-1", "lark", "blocked_notice", "b1", "r1", "oc_1")
    assert goal_channel_contracts.blocked_notice_receipt_matches_target(
        legacy_key, {"kind": "blocked_notice", "blocker_identity": "b1", "blocker_revision": "r1"}, goal_id="g-1", chat_id="oc_1"
    )
    assert not goal_channel_contracts.blocked_notice_receipt_matches_target(
        legacy_key, {"kind": "blocked_notice", "chat_id": "oc_other"}, goal_id="g-1", chat_id="oc_1"
    )

    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.effect_runtime_result",
        lambda _m, payload: {"goal_id": payload["goal_id"], "items": payload["requests"]},
    )
    quota_packet: dict[str, object] = {
        "goal_id": "g-1",
        "state": "operator_gate",
        "user_todo_summary": {
            "gate_open_items": [
                {"status": "open", "text": "item 0"},
                {"status": "open", "text": "item 1"},
                {"status": "open", "text": "item 2"},
                {"todo_id": "t4", "status": "open", "text": "item 3"},
            ]
        },
        "request_snapshot": {"goal_id": "g-1", "items": [{"todo_id": "t4", "note": "need review"}]},
        "user_gate_notification_cooldown": {"notification_due": True, "policy": "daily", "cooldown_minutes": 60},
    }
    assert goal_channel_contracts.quota_human_gate_identity(quota_packet) == "t4"
    assert len(goal_channel_contracts._quota_human_gate_items(quota_packet)) == 3
    gen = goal_channel_contracts.quota_human_gate_state_generation(quota_packet)
    assert gen.startswith("sha256:")
    assert goal_channel_contracts.quota_human_gate_reminder_generation(quota_packet) is not None
    assert goal_channel_contracts.quota_selects_human_gate(quota_packet) is True
    assert (
        goal_channel_contracts.quota_selects_human_gate(
            {**quota_packet, "user_gate_notification_cooldown": {"notification_suppressed": True}}
        )
        is False
    )
    snap = goal_channel_contracts.notification_request_snapshot(
        {"attention_queue": {"items": [{"goal_id": "g-1", "user_todos": {"items": [{"todo_id": "t4"}]}}]}},
        "g-1",
    )
    assert snap == {"goal_id": "g-1", "items": [{"todo_id": "t4"}]}
