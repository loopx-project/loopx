from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.extensions.lark import goal_channel_lifecycle
from loopx.extensions.lark.goal_channel_contracts import (
    GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
    human_gate_auto_notify_marker_path,
    read_goal_channel_binding,
    write_human_gate_auto_notify_marker,
    write_goal_channel_binding,
)
from loopx.extensions.lark.goal_channel_targets import (
    GOAL_CHANNEL_TARGETS_SCHEMA_VERSION,
)
from loopx.extensions.lark.private_json import write_private_json_atomic


GOAL_ID = "goal-public-fixture"


def _registry(tmp_path: Path) -> Path:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "objective": "Deliver a public-safe collaboration channel.",
                        "repo": str(tmp_path),
                        "state_file": str(tmp_path / "ACTIVE_GOAL_STATE.md"),
                        "adapter": {"kind": "read_only_project_map_v0"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry_path


def _binding(registry_path: Path, *, enabled: bool) -> None:
    write_goal_channel_binding(
        registry_path.parent / "goal-channel.json",
        {
            "schema_version": GOAL_CHANNEL_BINDING_SCHEMA_VERSION,
            "bindings": {
                GOAL_ID: {
                    "goal_id": GOAL_ID,
                    "provider": "lark",
                    "enabled": True,
                    "automation": {
                        "human_gate_auto_notify_enabled": enabled,
                    },
                    "receipts": {},
                }
            },
        },
    )


def _target_binding(registry_path: Path) -> dict[str, Any]:
    payload = read_goal_channel_binding(registry_path.parent / "goal-channel.json")
    binding = payload["bindings"][GOAL_ID]
    binding["target_ref"] = "shared-lark"
    binding["channel"] = {}
    binding["identity"] = {}
    write_goal_channel_binding(registry_path.parent / "goal-channel.json", payload)
    target = {
        "name": "shared-lark",
        "provider": "lark",
        "enabled": True,
        "channel": {
            "chat_id": "oc_public_fixture",
            "chat_name": "Shared Goal Channel",
        },
        "identity": {
            "mode": "local_user",
            "sender_profile": "",
            "sender_identity": "bot",
            "bot_app_id": "cli_public_fixture",
            "bot_display_name": "",
            "cli_bin": "lark-cli",
        },
    }
    write_private_json_atomic(
        Path(json.loads(registry_path.read_text())["common_runtime_root"])
        / "goal-channel-targets.json",
        {
            "schema_version": GOAL_CHANNEL_TARGETS_SCHEMA_VERSION,
            "targets": {"shared-lark": target},
        },
    )
    return target


def test_refresh_lifecycle_unconfigured_does_not_require_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    calls: list[Path] = []

    def activate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(Path(kwargs["state_file"]))
        return {"status": "active"}

    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        activate,
    )

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
    )

    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["status"] == "not_configured"
    assert calls == []


def test_refresh_lifecycle_shared_registry_without_source_binding_is_safe_noop(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "runtime" / "registry.global.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "registry_role": "global-local",
                "common_runtime_root": str(registry_path.parent),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "objective": "Deliver a public-safe collaboration channel.",
                        "repo": str(tmp_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
    )

    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["status"] == "project_binding_unavailable"


def test_refresh_lifecycle_suppression_skips_extension_and_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    _binding(registry_path, enabled=True)

    activation_calls = 0

    def activate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal activation_calls
        activation_calls += 1
        return {"status": "active"}

    def unexpected(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("suppressed delivery must not rebuild quota")

    monkeypatch.setattr(goal_channel_lifecycle, "resolve_extension_activation", activate)
    monkeypatch.setattr(goal_channel_lifecycle, "collect_status", unexpected)

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id="agent-public-fixture",
        external_sink_delivery_authorized=False,
    )

    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["status"] == "external_sink_suppressed"
    assert activation_calls == 0


def test_refresh_lifecycle_no_gate_does_not_require_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    _binding(registry_path, enabled=True)
    status_reads: list[dict[str, Any]] = []

    def operational_status(**kwargs: Any) -> dict[str, Any]:
        status_reads.append(kwargs)
        return {"status": "fixture"}

    monkeypatch.setattr(
        goal_channel_lifecycle,
        "collect_status",
        operational_status,
    )
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "build_quota_should_run",
        lambda status, **kwargs: {"state": "eligible"},
    )

    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"status": "active"},
    )

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
    )

    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["status"] == "not_selected"
    assert "extension_activation" not in result
    assert len(status_reads) == 1
    assert status_reads[0]["goal_id"] == GOAL_ID
    assert status_reads[0]["include_public_boundary_scan"] is False


def test_refresh_lifecycle_unconfigured_binding_failure_does_not_require_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    binding_path = registry_path.parent / "goal-channel.json"
    binding_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("extension unavailable")
        ),
    )

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
    )

    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["status"] == "not_configured"


def test_refresh_lifecycle_unconfigured_malformed_binding_is_safe_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    binding_path = registry_path.parent / "goal-channel.json"
    binding_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"status": "active"},
    )

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
    )

    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["status"] == "not_configured"
    assert result["delivery_postcondition"] == {
        "satisfied": True,
        "blocks_delivery": False,
    }


def test_refresh_lifecycle_configured_malformed_binding_blocks_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    binding_path = registry_path.parent / "goal-channel.json"
    binding_path.write_text("not-json", encoding="utf-8")
    write_human_gate_auto_notify_marker(
        human_gate_auto_notify_marker_path(binding_path, GOAL_ID)
    )
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"status": "active"},
    )

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
    )

    assert result["ok"] is False
    assert result["enabled"] is True
    assert result["status"] == "failed"
    assert result["blocker"] == "invalid_goal_channel_binding"
    assert result["delivery_postcondition"] == {
        "satisfied": False,
        "blocks_delivery": True,
    }


def test_refresh_lifecycle_configured_extension_failure_blocks_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    binding_path = registry_path.parent / "goal-channel.json"
    _binding(registry_path, enabled=True)
    write_human_gate_auto_notify_marker(
        human_gate_auto_notify_marker_path(binding_path, GOAL_ID)
    )
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("extension unavailable")
        ),
    )

    _target_binding(registry_path)
    monkeypatch.setattr(goal_channel_lifecycle, "collect_status", lambda **kwargs: {})
    monkeypatch.setattr(goal_channel_lifecycle, "build_quota_should_run",
                        lambda *args, **kwargs: {"state": "operator_gate"})

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
    )

    assert result["ok"] is False
    assert result["enabled"] is True
    assert result["status"] == "extension_unavailable"
    assert result["blocker"] == "extension_unavailable"
    assert result["delivery_postcondition"] == {
        "satisfied": False,
        "blocks_delivery": True,
    }


def test_refresh_lifecycle_reads_quota_then_delivers_selected_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    _binding(registry_path, enabled=True)
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"status": "active"},
    )
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "collect_status",
        lambda **kwargs: {"status": "fixture"},
    )
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "build_quota_should_run",
        lambda status, **kwargs: {
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the bounded external write.",
        },
    )

    def deliver(**kwargs: Any) -> dict[str, Any]:
        kwargs["admit_delivery"]()
        captured.update(kwargs)
        return {
            "schema_version": "loopx_goal_channel_gate_auto_delivery_v0",
            "ok": True,
            "enabled": True,
            "status": "sent_verified",
            "external_write_performed": True,
            "readback_verified": True,
            "delivery_postcondition": {
                "satisfied": True,
                "blocks_delivery": False,
            },
        }

    monkeypatch.setattr(
        goal_channel_lifecycle,
        "auto_notify_lark_goal_channel_gate",
        deliver,
    )

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id="agent-public-fixture",
        external_sink_delivery_authorized=True,
    )

    assert result["status"] == "sent_verified"
    assert result["extension_activation"] == {"status": "active"}
    assert captured["quota_packet"]["state"] == "operator_gate"
    assert captured["external_sink_delivery_authorized"] is True
    assert (
        read_goal_channel_binding(registry_path.parent / "goal-channel.json")[
            "bindings"
        ][GOAL_ID]["automation"]["human_gate_auto_notify_enabled"]
        is True
    )


def test_refresh_lifecycle_resolves_shared_provider_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    _binding(registry_path, enabled=True)
    expected_target = _target_binding(registry_path)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"status": "active"},
    )
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "collect_status",
        lambda **kwargs: {"status": "fixture"},
    )
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "build_quota_should_run",
        lambda status, **kwargs: {
            "state": "operator_gate",
            "notify_user_on_gate": True,
            "gate_prompt": "Approve the bounded external write.",
        },
    )

    def deliver(**kwargs: Any) -> dict[str, Any]:
        kwargs["admit_delivery"]()
        captured.update(kwargs)
        return {
            "ok": True,
            "enabled": True,
            "status": "sent_verified",
            "delivery_postcondition": {
                "satisfied": True,
                "blocks_delivery": False,
            },
        }

    monkeypatch.setattr(
        goal_channel_lifecycle,
        "auto_notify_lark_goal_channel_gate",
        deliver,
    )

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
    )

    assert result["status"] == "sent_verified"
    assert captured["provider_target"] == expected_target
    raw_binding = read_goal_channel_binding(
        registry_path.parent / "goal-channel.json"
    )["bindings"][GOAL_ID]
    assert raw_binding["target_ref"] == "shared-lark"
    assert raw_binding["channel"] == {}
    assert raw_binding["identity"] == {}


def test_refresh_lifecycle_missing_shared_target_blocks_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = _registry(tmp_path)
    _binding(registry_path, enabled=True)
    payload = read_goal_channel_binding(registry_path.parent / "goal-channel.json")
    payload["bindings"][GOAL_ID]["target_ref"] = "missing-target"
    write_goal_channel_binding(registry_path.parent / "goal-channel.json", payload)
    monkeypatch.setattr(
        goal_channel_lifecycle,
        "resolve_extension_activation",
        lambda *args, **kwargs: {"status": "active"},
    )

    result = goal_channel_lifecycle.sync_human_gate_after_refresh(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id=GOAL_ID,
        agent_id=None,
        external_sink_delivery_authorized=True,
    )

    assert result["ok"] is False
    assert result["enabled"] is True
    assert result["status"] == "provider_target_missing"
    assert result["delivery_postcondition"] == {
        "satisfied": False,
        "blocks_delivery": True,
    }
