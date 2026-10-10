"""Default media journeys and failed downloads must never silently lose input."""
import json
from pathlib import Path

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_chat_image_attachments import PNG_BYTES, PNG_DATA_URL
from test_lark_private_conversations import connect

from loopx.extensions.lark.private_images import private_message_images
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore


def image_runner(provider, *, fail=False, content=PNG_BYTES, revoke=None):
    def run(args, cwd=None, timeout=None):
        if "+messages-resources-download" not in args:
            return provider(args, cwd, timeout)
        if provider is not None:
            provider.calls.append(list(args))
        assert args[args.index("--as") + 1] == "bot"
        if revoke:
            revoke()
        if fail:
            return {"returncode": 1, "stdout": '{"ok":false}', "stderr": ""}
        path = Path(cwd) / args[args.index("--output") + 1]
        path.write_bytes(content)
        return {"returncode": 0, "stdout": json.dumps({"ok": True,
            "data": {"saved_path": str(path), "size_bytes": len(content)}})}
    return run


@pytest.mark.parametrize("kind,content", [("image", "[Image: img_example]"), ("image", "![Image](img_example)"),
    ("post", "Inspect this diagram\n![Image](img_example)\nKeep the caption")])
def test_default_images_reach_codex_in_original_session_and_replay_once(ordinary, kind, content):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    capture = ordinary[4]
    transport.runner = image_runner(provider)
    try:
        transport.admit("notes-app", provider.event("notes-app", "first", "Remember our context"))
        first = transport.core.pending()[0]
        runtime.wait_for_turn(session_id=first["session_id"], turn_id=first["turn_id"], timeout_sec=10)
        event = provider.event("notes-app", "diagram", content, kind=kind)
        assert transport.admit("notes-app", event)["status"] == "durably_accepted"
        row = next(r for r in transport.core.pending() if r.get("attachments"))
        assert row["session_id"] == first["session_id"]
        result = runtime.wait_for_turn(session_id=row["session_id"], turn_id=row["turn_id"], timeout_sec=10)
        assert result["status"] == "completed"
        assert result["attachments"][0]["data_url"] == PNG_DATA_URL
        assert transport.admit("notes-app", {**event, "event_id": "redelivery"})["status"] == "durably_accepted"
        assert len([c for c in provider.calls if "+messages-resources-download" in c]) == 1
        transcript = store.messages(row["session_id"])
        assert len([m for m in transcript if m.get("turn_id") == row["turn_id"] and m["role"] == "user"]) == 1
        requests = [json.loads(line) for line in capture.read_text().splitlines()]
        wire = [r["params"]["input"] for r in requests if r.get("method") == "turn/start"][-1]
        assert any(part.get("type") == "image" and part["url"] == PNG_DATA_URL for part in wire)
        assert any("[图片 1]" in part.get("text", "") for part in wire)
        if kind == "post":
            assert "Keep the caption" in row["message"]
        assert all(s["goal_id"] is None for s in store.list_sessions())
    finally:
        runtime.close()


@pytest.mark.parametrize("failure,content,notice", [(True, "![Image](img_example)", "下载失败"),
    (False, "![Image](img_example)\n<file key=\"file_example\"/>", "文件或音视频"),
    (False, "\n".join(f"![Image](img_{i})" for i in range(5)), "最多支持 4"),
    (False, "", "资源信息")])
def test_unavailable_or_partial_media_never_executes_text_alone(ordinary, failure, content, notice):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    transport.runner = image_runner(provider, fail=failure)
    try:
        event = provider.event("notes-app", "unavailable", content, kind="image" if not content else "post")
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        transport.reconcile()
        assert store.list_sessions() == []
        assert any(notice in text and "未提交执行" in text for _, text in provider.writes)
    finally:
        runtime.close()


def test_revoked_during_image_download_cannot_create_native_work(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    binding = transport.bindings.read()["bindings"][0]
    transport.runner = image_runner(provider, revoke=lambda: transport.bindings.disconnect(
        binding["binding_id"], expected_revision=transport.bindings.read()["revision"]))
    try:
        event = provider.event("notes-app", "revoked-image", "![Image](img_example)", kind="image")
        assert transport.admit("notes-app", event)["status"] == "command_rejected"
        assert store.list_sessions() == [] and transport.core.pending() == []
    finally:
        runtime.close()


def test_queued_image_survives_runtime_restart_and_changed_replay_is_rejected(ordinary, monkeypatch):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    transport.runner = image_runner(provider)
    monkeypatch.setattr(runtime, "resume_session_queue", lambda **kw: None)
    event = provider.event("notes-app", "persisted", "Caption\n![Image](img_example)", kind="post")
    assert transport.admit("notes-app", event)["status"] == "durably_accepted"
    row = transport.core.pending()[0]
    sid, tid = row["session_id"], row["turn_id"]
    thread = store.load_session(sid)["upstream_thread_id"]
    assert store.load_turn(sid, tid)["status"] == "queued"
    runtime.close()
    restored = ChatSessionStore(store.root.parent)
    original = restored.load_turn(sid, tid)
    assert original["attachments"] == row["attachments"]
    repeated, created = restored.create_queued_turn(sid, client_turn_id=original["client_turn_id"],
        message=original["message"], attachments=original["attachments"], origin="lark")
    assert not created and repeated["turn_id"] == tid
    with pytest.raises(ValueError, match="different request"):
        restored.create_queued_turn(sid, client_turn_id=original["client_turn_id"],
            message=original["message"], attachments=[], origin="lark")
    restarted = ChatRuntimeController(store=restored, codex_bin=str(ordinary[5]),
        project_contexts=ordinary[2], registry_path=runtime.registry_path)
    try:
        restarted.resume_session_queue(session_id=sid, work_dir=ordinary[6], objective="")
        assert restarted.wait_for_turn(session_id=sid, turn_id=tid, timeout_sec=10)["status"] == "completed"
        assert restored.load_session(sid)["upstream_thread_id"] == thread
        requests = [json.loads(line) for line in ordinary[4].read_text().splitlines()]
        wire = [r["params"]["input"] for r in requests if r.get("method") == "turn/start"][-1]
        assert any(part.get("type") == "image" and part["url"] == PNG_DATA_URL for part in wire)
    finally:
        restarted.close()


@pytest.mark.parametrize("raw,notice", [(b"<svg/>", "支持 PNG"),
    (PNG_BYTES + b"x" * (5 * 1024 * 1024), "单张图片")], ids=["unsupported-type", "oversize"])
def test_resource_bytes_are_checked_before_core_admission(raw, notice):
    with pytest.raises(ValueError, match=notice):
        private_message_images(content="![Image](img_example)", message_type="image",
            message_id="om_example", profile="notes-app", cli_bin="lark-cli",
            runner=image_runner(None, content=raw))


@pytest.mark.parametrize("command", ["/status", "/stop", "/new", "/project", "/agents"])
def test_image_control_caption_never_starts_or_controls_native_work(ordinary, command):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    transport.runner = image_runner(provider)
    try:
        event = provider.event("notes-app", "image-control",
            command + "\n![Image](img_example)", kind="post")
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        transport.reconcile()
        assert store.list_sessions() == []
        assert any("未提交执行" in text for _, text in provider.writes)
    finally:
        runtime.close()


def test_help_describes_default_images_without_creating_work(ordinary):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    try:
        event = provider.event("notes-app", "image-help", "/help")
        assert transport.admit("notes-app", event)["status"] == "command_recorded"
        transport.reconcile()
        answer = provider.writes[-1][1]
        assert "PNG/JPEG/GIF/WebP" in answer
        assert "文件与音视频暂不支持" in answer
        assert "仅支持文字" in answer
        assert store.list_sessions() == []
    finally:
        runtime.close()
