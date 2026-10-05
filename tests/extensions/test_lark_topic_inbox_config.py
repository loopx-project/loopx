"""Concurrent transport callers must publish a complete owner-private inbox config."""

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.extensions.lark import goal_topic_runtime as runtime


def _options(tmp_path):
    return {
        "runtime_root": tmp_path,
        "route": {
            "app_ref": "public-profile",
            "target_ref": "public-target",
            "topic_root_message_id": "om_public_topic",
            "conversation_kind": "manager",
        },
        "target_payload": {
            "schema_version": "loopx_goal_channel_provider_targets_v0",
            "targets": {
                "public-target": {
                    "name": "public-target",
                    "provider": "lark",
                    "enabled": True,
                    "channel": {"chat_id": "oc_public_chat"},
                    "identity": {
                        "sender_profile": "public-profile",
                        "bot_display_name": "Public Bot",
                        "bot_app_id": "cli_public_app",
                        "bot_open_id": "ou_public_bot",
                    },
                }
            },
        },
    }


def test_concurrent_topic_config_publish_uses_private_independent_temporaries(
    tmp_path, monkeypatch
):
    options = _options(tmp_path)
    barrier = threading.Barrier(2)
    observed = []
    original_os_replace = os.replace

    def before_publish(source):
        source = Path(source)
        observed.append((source.name, stat.S_IMODE(source.stat().st_mode)))
        barrier.wait(timeout=5)

    def os_replace(source, destination):
        before_publish(source)
        return original_os_replace(source, destination)

    monkeypatch.setattr(os, "replace", os_replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(runtime._inbox_config, **options) for _ in range(2)]
        results = [future.result(timeout=8) for future in futures]

    assert results[0] == results[1]
    config_path, config_ref = results[0]
    assert config_path == tmp_path / config_ref
    payload = json.loads(config_path.read_text())
    assert payload["topic_root_message_id"] == "om_public_topic"
    assert payload["material_review"]["enabled"] is True
    assert payload["reply"]["received_reaction_policy"] == "retain"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert len({name for name, _ in observed}) == 2
    assert all(mode == 0o600 for _, mode in observed)
    assert list(config_path.parent.glob("*.tmp")) == []


def test_failed_topic_config_publish_preserves_previous_config_and_cleans_temporary(
    tmp_path, monkeypatch
):
    options = _options(tmp_path)
    config_path, _ = runtime._inbox_config(**options)
    previous = config_path.read_bytes()

    def fail_publish(*_args, **_kwargs):
        raise OSError("synthetic publish failure")

    monkeypatch.setattr(os, "replace", fail_publish)
    changed = {**options, "route": {**options["route"], "conversation_kind": "worker"}}
    with pytest.raises(OSError, match="synthetic publish failure"):
        runtime._inbox_config(**changed)
    assert config_path.read_bytes() == previous
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert list(config_path.parent.glob("*.tmp")) == []
