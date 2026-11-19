from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.machine_configuration.store import machine_configuration_store_path
from loopx.capabilities.native_chat.codex_context import process_environment
from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.capabilities.steward_memory.runtime import (
    clear_steward_memory_cache,
    commit_steward_memory_after_turn,
    recall_steward_memory_for_turn,
)
from loopx.chat import CHAT_REVIEW_CLOSE_TAG, CHAT_REVIEW_OPEN_TAG, parse_agent_response


STEWARD_CTX = {
    "kind": "bound_steward",
    "binding_id": "111111111111111111111111",
    "project_ref": "222222222222222222222222",
    "workspace_path": "/tmp/steward-workspace",
    "source_ref": "333333333333333333333333",
    "provider_ref": "444444444444444444444444",
    "operator_ref": "555555555555555555555555",
    "audience": "bound_owner",
    "grant": "portfolio_read",
    "goal_ids": ["goal-alpha"],
}
PRIVATE_SCOPE = {
    "kind": "external_audience",
    "bound_steward": True,
    "private_conversation": True,
}


def _enable_steward_memory(runtime_root: Path, *, timeout: int = 3) -> None:
    cfg_path = machine_configuration_store_path(runtime_root)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_machine_configuration_v0",
                "namespaces": {
                    "steward_memory": {
                        "schema_version": "steward_memory_machine_defaults_v0",
                        "enabled": True,
                        "user_space": "steward",
                        "auto_recall": True,
                        "recall_timeout_seconds": timeout,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _write_steward_credentials(runtime_root: Path) -> None:
    cred_path = runtime_root / "machine" / "credentials" / "steward-openviking.json"
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    cred_path.write_text(
        json.dumps(
            {
                "service_url": "http://127.0.0.1:19999",
                "api_key": "test-steward-ov-secret",
                "ov_bin": "/usr/local/bin/ov.mjs",
            }
        ),
        encoding="utf-8",
    )


class _FakeOpenVikingServer:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.observed_config_files: list[str | None] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command))
        env = kwargs.get("env") or {}
        conf_path = env.get("OPENVIKING_CLI_CONFIG_FILE")
        assert conf_path and Path(conf_path).is_file()
        assert (Path(conf_path).stat().st_mode & 0o777) == 0o600
        self.observed_config_files.append(conf_path)
        assert "--actor-peer-id" in command
        idx = command.index("--actor-peer-id")
        peer_id = command[idx + 1]
        assert peer_id == f"steward-{STEWARD_CTX['binding_id']}"

        rest = command[idx + 2 :]
        sub = rest[0]
        if sub == "ls":
            prefix = rest[1].rstrip("/") + "/"
            entries = [
                {"uri": uri, "name": uri.rsplit("/", 1)[-1], "type": "file", "mtime": "2026-11-19T12:00:00Z"}
                for uri in sorted(self.files)
                if uri.startswith(prefix)
            ]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"ok": True, "result": entries}), stderr="")
        if sub == "read":
            uri = rest[1]
            if uri not in self.files:
                return subprocess.CompletedProcess(command, 1, stdout='{"ok":false,"error":{"code":"NOT_FOUND"}}', stderr="")
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"ok": True, "result": {"content": self.files[uri]}}), stderr=""
            )
        if sub == "write":
            uri = rest[1]
            from_idx = rest.index("--from-file")
            content = Path(rest[from_idx + 1]).read_text(encoding="utf-8")
            assert "--processing-mode" in rest and "vectors_only" in rest
            self.files[uri] = content
            return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}', stderr="")
        if sub == "rm":
            uri = rest[1]
            self.files.pop(uri, None)
            return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}', stderr="")
        if sub == "find":
            query = rest[1]
            u_idx = rest.index("-u")
            scope_prefix = rest[u_idx + 1].rstrip("/") + "/"
            hits = [
                {"uri": uri, "abstract": content[:120], "content": content}
                for uri, content in sorted(self.files.items(), reverse=True)
                if uri.startswith(scope_prefix) and any(tok in content.lower() for tok in query.lower().split())
            ]
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"ok": True, "result": {"memories": hits}}), stderr=""
            )
        raise AssertionError(f"unexpected ov command: {command}")


def test_feature_off_and_non_steward_leakage_guards(tmp_path: Path) -> None:
    clear_steward_memory_cache()
    _write_steward_credentials(tmp_path)
    called = False

    def fail_runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("ov must not be called when disabled or ineligible")

    session = {"session_id": "s1", "channel_id": "manager.external.native", "steward_context": STEWARD_CTX}
    # 1. Default-off (no steward_memory enabled setting)
    assert recall_steward_memory_for_turn(
        runtime_root=tmp_path, session=session, scope=PRIVATE_SCOPE, turn={"origin": "external"},
        message="记住我的偏好", runner=fail_runner,
    ) is None
    assert not called

    # 2. Enabled, but group / project / native_loopx / answer_review scopes never trigger
    _enable_steward_memory(tmp_path)
    for bad_scope, bad_session, bad_turn in [
        ({"kind": "external_audience", "bound_steward": False, "private_conversation": False}, session, {}),
        (PRIVATE_SCOPE, {"session_id": "p1", "project_context": {"kind": "project_workspace"}}, {}),
        (PRIVATE_SCOPE, session, {"origin": "native_loopx", "loopx_execution": True}),
        (PRIVATE_SCOPE, {**session, "answer_review": True}, {}),
    ]:
        assert recall_steward_memory_for_turn(
            runtime_root=tmp_path, session=bad_session, scope=bad_scope, turn=bad_turn,
            message="请查进展", runner=fail_runner,
        ) is None
    assert not called


def test_owner_and_speaker_preferences_persist_cache_and_recall_across_sessions(tmp_path: Path) -> None:
    clear_steward_memory_cache()
    _enable_steward_memory(tmp_path)
    _write_steward_credentials(tmp_path)
    server = _FakeOpenVikingServer()

    session_a = {"session_id": "sess_a", "channel_id": "manager.external.native.1111.3333", "steward_context": STEWARD_CTX}
    session_b = {"session_id": "sess_b", "channel_id": "manager.external.native.1111.3333", "steward_context": STEWARD_CTX}

    # Owner states preference in session A -> parsed from envelope -> committed to /preferences/owner + event written
    parsed = parse_agent_response(
        "收到。"
        + CHAT_REVIEW_OPEN_TAG
        + json.dumps({
            "schema_version": "loopx_chat_agent_response_v0",
            "message": "收到，后续直接用中文短列表汇报 deploy 总结。",
            "memory_preferences": [
                {"operation": "upsert", "topic": "Report Format", "content": "Always answer deploy summaries in concise Chinese bullets."},
            ],
            "proposals": [],
            "protected_action": None,
            "goal_draft": None,
            "context_handoff": None,
            "gate": None,
            "unfinished_work": None,
        })
        + CHAT_REVIEW_CLOSE_TAG
    )
    receipt = commit_steward_memory_after_turn(
        runtime_root=tmp_path, session=session_a, scope=PRIVATE_SCOPE, turn={"origin": "external"},
        turn_id="turn_a1", user_message="记住以后 deploy 总结用中文短列表", response=parsed, runner=server,
    )
    assert receipt and receipt["status"] == "completed"
    assert receipt["preferences_applied"] == 1 and receipt["event_written"] is True
    assert all(not Path(p).exists() for p in server.observed_config_files if p)

    owner_uri = f"viking://user/steward/peers/steward-{STEWARD_CTX['binding_id']}/memories/preferences/owner/report-format.md"
    assert owner_uri in server.files

    # Fresh session B recalls owner preference + historical deploy event
    recalled = recall_steward_memory_for_turn(
        runtime_root=tmp_path, session=session_b, scope=PRIVATE_SCOPE, turn={"origin": "external"},
        message="今天 deploy 情况如何", runner=server,
    )
    assert recalled and recalled["status"] == "completed"
    assert recalled["owner_preferences"][0]["topic"] == "report-format"
    assert "concise Chinese bullets" in recalled["owner_preferences"][0]["content"]
    assert len(recalled["recalled_events"]) == 1

    # Cache hit avoids redundant `ls`/`read` calls until invalidated by remove
    ls_before = sum(1 for c in server.calls if "ls" in c)
    recalled_cached = recall_steward_memory_for_turn(
        runtime_root=tmp_path, session=session_b, scope=PRIVATE_SCOPE, turn={"origin": "external"},
        message="hi", runner=server,
    )
    assert recalled_cached and recalled_cached["owner_preferences"][0]["topic"] == "report-format"
    assert sum(1 for c in server.calls if "ls" in c) == ls_before

    # Guest speaker gets owner preferences as standing rules + separate hashed speaker directory
    guest_receipt = commit_steward_memory_after_turn(
        runtime_root=tmp_path, session=session_b, scope=PRIVATE_SCOPE, turn={"origin": "external"},
        turn_id="turn_b2", user_message="叫我小陈", response={
            "message": "好的",
            "memory_preferences": [{"operation": "upsert", "topic": "greeting", "content": "Call guest XiaoChen."}],
        },
        sender_ref="ou_guest_chen", sender_label="Alice", sender_type="user", runner=server,
    )
    assert guest_receipt and guest_receipt["preferences_applied"] == 1
    guest_recall = recall_steward_memory_for_turn(
        runtime_root=tmp_path, session=session_b, scope=PRIVATE_SCOPE, turn={"origin": "external"},
        message="hi", sender_ref="ou_guest_chen", sender_label="Alice", sender_type="user", runner=server,
    )
    assert guest_recall and not guest_recall["is_owner"]
    assert guest_recall["speaker_directory"].startswith("Alice~")
    assert guest_recall["owner_preferences"][0]["topic"] == "report-format"
    assert guest_recall["speaker_preferences"][0]["topic"] == "greeting"

    # Remove invalidates cache
    commit_steward_memory_after_turn(
        runtime_root=tmp_path, session=session_b, scope=PRIVATE_SCOPE, turn={"origin": "external"},
        turn_id="turn_b3", user_message="忘掉旧汇报格式", response={
            "message": "已忘掉",
            "memory_preferences": [{"operation": "remove", "topic": "Report Format"}],
        },
        runner=server,
    )
    after_remove = recall_steward_memory_for_turn(
        runtime_root=tmp_path, session=session_b, scope=PRIVATE_SCOPE, turn={"origin": "external"},
        message="hi", runner=server,
    )
    assert after_remove and after_remove["owner_preferences"] == []


def test_timeout_and_missing_credentials_fail_open_and_env_is_scrubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_steward_memory_cache()
    _enable_steward_memory(tmp_path, timeout=1)
    session = {"session_id": "s1", "channel_id": "manager.external.native", "steward_context": STEWARD_CTX}

    missing = recall_steward_memory_for_turn(
        runtime_root=tmp_path, session=session, scope=PRIVATE_SCOPE, turn={"origin": "external"}, message="hello world",
    )
    assert missing and missing["status"] == "not_configured"

    _write_steward_credentials(tmp_path)

    def slow_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    timed_out = recall_steward_memory_for_turn(
        runtime_root=tmp_path, session=session, scope=PRIVATE_SCOPE, turn={"origin": "external"},
        message="hello world", runner=slow_runner,
    )
    assert timed_out and timed_out["status"] == "timeout"

    monkeypatch.setenv("OPENVIKING_API_KEY", "ambient-secret")
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", "/tmp/ambient.conf")
    env = process_environment(tmp_path / ".codex", isolated=False)
    assert "OPENVIKING_API_KEY" not in env
    assert "OPENVIKING_CLI_CONFIG_FILE" not in env


def test_parse_agent_response_rejects_memory_override_and_review_blocks_memory_preferences() -> None:
    bad = parse_agent_response(
        "已记录"
        + CHAT_REVIEW_OPEN_TAG
        + json.dumps({
            "schema_version": "loopx_chat_agent_response_v0",
            "message": "已记录",
            "memory_preferences": [{"operation": "upsert", "topic": "style", "content": "x", "speaker": "other"}],
            "proposals": [], "protected_action": None, "goal_draft": None, "context_handoff": None, "gate": None,
        })
        + CHAT_REVIEW_CLOSE_TAG
    )
    assert "memory_preferences" not in bad
    assert bad["gate"] and bad["gate"]["kind"] == "response_metadata_invalid"
    assert isinstance(ChatExternalConversations, type)
