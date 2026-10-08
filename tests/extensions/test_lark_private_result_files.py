"""Real file snapshots and the original App's single-message result recovery."""

import json
import os
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from test_lark_private_manager_returns import private_return, return_root  # noqa: F401
from test_native_steward_private import steward, finish  # noqa: F401

from loopx.capabilities.manager_context import acknowledge, _root, _write, POLICY_SCHEMA
from loopx.capabilities.manager_context.roundtrip import drain, report, reply_status

pytestmark = pytest.mark.skipif(os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
                                reason="result file snapshots require POSIX no-follow directory opens")


@pytest.fixture
def file_return(private_return):  # noqa: F811
    sender, session, turn, route, row, provider, transport, replies = private_return
    registry = sender.server.registry_path
    goal = next(g for g in json.loads(registry.read_text())["goals"] if g["id"] == route["goal_id"])
    workspace = Path(goal["repo"])
    workspace.mkdir(parents=True, exist_ok=True)
    ref = "result-fixture.bin"
    raw = b"\x00binary result\xff\x01"
    (workspace / ref).write_bytes(raw)
    original_runner = transport.runner
    uploads, downloads = [], []
    state = {"wrong_bytes": False, "download_available": True, "remapped_key": False}

    def runner(args, cwd=None, timeout=None):
        if "files" in args and "create" in args:
            assert args[args.index("--profile") + 1] == "steward-app"
            assert args[args.index("--as") + 1] == "bot"
            if "--dry-run" not in args:
                uploads.append((Path(cwd) / args[args.index("--file") + 1]).read_bytes())
            return {"returncode": 0, "stdout": json.dumps({"ok": True, "data": {"file_key": "file_result_fixture"}})}
        if "+messages-resources-download" in args:
            downloads.append(args)
            expected = "file_message_fixture" if state["remapped_key"] else "file_result_fixture"
            assert args[args.index("--file-key") + 1] == expected
            if not state["download_available"]:
                return {"returncode": 1, "stdout": ""}
            raw_bytes = b"wrong bytes" if state["wrong_bytes"] else uploads[0]
            output = Path(cwd) / args[args.index("--output") + 1]
            # lark-cli infers a suffix when the caller omits one.
            if not output.suffix:
                output = output.with_suffix(".bin")
            output.write_bytes(raw_bytes)
            return {"returncode": 0, "stdout": json.dumps({"ok": True})}
        if "--attachment" in args:
            # The CLI merges its explicit keys into the post attachment zone.
            args = list(args)
            index = args.index("--content") + 1
            content = json.loads(args[index])
            content["files"] = [{"key": args[args.index("--attachment") + 1], "name": ref}]
            args[index] = json.dumps(content)
        result = original_runner(args, cwd, timeout)
        if state["remapped_key"] and "--attachment" in args and "--dry-run" not in args:
            message = provider.messages["om_out_worker"]
            content = json.loads(message["body"]["content"])
            content["files"][0]["key"] = "file_message_fixture"
            message["body"]["content"] = json.dumps(content)
        return result

    transport.runner = runner
    acknowledge(sender.root, route["goal_id"], route["agent_id"], route["request_id"], "adopt", "Return the requested file")
    def publish(refs=None, update_id=None):
        return report(sender.root, route["goal_id"], route["agent_id"], route["request_id"],
                      "conclusion", "The requested result is attached.", registry=registry,
                      attachment_refs=refs if refs is not None else [ref], update_id=update_id, workspace=workspace)
    return sender, session, turn, route, row, provider, transport, replies, workspace, raw, publish, uploads, downloads, state


def test_remapped_message_resource_recovers_without_upload_or_send(file_return):
    sender, _, _, route, _, provider, transport, replies, _, raw, publish, uploads, downloads, state = file_return
    state["remapped_key"] = True
    state["download_available"] = False
    publish()
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    assert len(replies) == len(uploads) == 1
    assert reply_status(sender.root, route)[0]["status"] == "verification_required"
    state["download_available"] = True
    # Reload the real store; recovery must use the saved message's resource key.
    from loopx.chat_store import ChatSessionStore
    from datetime import datetime, timedelta, timezone
    recovered = ChatSessionStore(transport.core.controller.store.root.parent)
    drain(sender.root, sender.server.registry_path, recovered, sender,
          now=datetime.now(timezone.utc) + timedelta(minutes=10))
    assert reply_status(sender.root, route)[0]["status"] == "delivered"
    assert len(replies) == len(uploads) == 1 and uploads == [raw]
    assert len(downloads) == 2


def test_file_completion_feedback_waits_for_verified_bytes(file_return, monkeypatch):
    from loopx.extensions.lark import inbox_reply

    sender, _, _, route, _, _, transport, _, _, _, publish, _, _, state = file_return
    cleanups = []
    def cleanup(**kwargs):
        cleanups.append(kwargs["message_id"])
        return {"ok": True}
    monkeypatch.setattr(inbox_reply, "complete_lark_event_inbox_reactions", cleanup)
    state["download_available"] = False
    publish()
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    assert cleanups == []
    state["download_available"] = True
    from datetime import datetime, timedelta, timezone
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender,
          now=datetime.now(timezone.utc) + timedelta(minutes=10))
    assert reply_status(sender.root, route)[0]["status"] == "delivered"
    assert len(cleanups) == 1


@pytest.mark.parametrize("update_id", [None, "file-result"])
def test_route_failure_after_send_preserves_attempt_without_resending(file_return, monkeypatch, update_id):
    from datetime import datetime, timedelta, timezone

    sender, _, _, route, _, _, transport, replies, _, _, publish, uploads, _, state = file_return
    store = transport.core.controller.store
    if update_id is not None:
        publish(refs=[])
        drain(sender.root, sender.server.registry_path, store, sender)
    prior_replies = len(replies)
    state["download_available"] = False
    published = publish(update_id=update_id)
    drain(sender.root, sender.server.registry_path, store, sender)
    key = published.get("result_key", "conclusion")
    path = _root(sender.root) / "replies" / route["request_id"] / (key + ".delivery.json")
    before = json.loads(path.read_text())
    assert before["attempt"]["message_ref"] == "om_out_worker"
    original = transport.return_inbox
    def unavailable(**kwargs):
        raise ValueError("source unavailable")
    monkeypatch.setattr(transport, "return_inbox", unavailable)
    now = datetime.now(timezone.utc) + timedelta(minutes=10)
    drain(sender.root, sender.server.registry_path, store, sender, now=now)
    after = json.loads(path.read_text())
    assert after["status"] == "explicit_unverified"
    assert after["error"] == "original_route_unavailable"
    assert after["attempt"] == before["attempt"]
    for field in ("goal_ref", "result_key"):
        if field in before:
            assert after[field] == before[field]
    monkeypatch.setattr(transport, "return_inbox", original)
    state["download_available"] = True
    drain(sender.root, sender.server.registry_path, store, sender, now=now + timedelta(minutes=10))
    assert json.loads(path.read_text())["status"] == "explicit_unverified"
    assert len(replies) == prior_replies + 1 and len(uploads) == 1


def test_snapshot_is_returned_once_and_restart_only_reads_the_saved_message(file_return):
    sender, session, turn, route, row, provider, transport, replies, workspace, raw, publish, uploads, downloads, state = file_return
    assert publish()["ok"]
    (workspace / "result-fixture.bin").write_bytes(b"later workspace edit")
    state["download_available"] = False
    store = transport.core.controller.store
    drain(sender.root, sender.server.registry_path, store, sender)
    assert len(replies) == 1 and uploads == [raw]
    saved = _root(sender.root) / "replies" / route["request_id"] / "conclusion.delivery.json"
    assert json.loads(saved.read_text())["status"] == "verification_required"
    state["download_available"] = True
    drain(sender.root, sender.server.registry_path, store, sender)
    assert reply_status(sender.root, {"request_id": route["request_id"]})[0]["status"] == "delivered"
    assert len(replies) == 1 and uploads == [raw] and len(downloads) == 2
    assert drain(sender.root, sender.server.registry_path, store, sender) == 0
    with pytest.raises(ValueError, match="conflicting replacement"):
        publish()


def test_wrong_download_is_not_file_delivery_and_cannot_trigger_a_resend(file_return):
    sender, _, _, route, _, _, transport, replies, _, _, publish, uploads, _, state = file_return
    publish()
    state["wrong_bytes"] = True
    store = transport.core.controller.store
    drain(sender.root, sender.server.registry_path, store, sender)
    drain(sender.root, sender.server.registry_path, store, sender)
    assert reply_status(sender.root, {"request_id": route["request_id"]})[0]["status"] == "explicit_unverified"
    assert len(replies) == len(uploads) == 1


def test_file_intake_rejects_escape_symlink_special_files_and_revoked_return(file_return, tmp_path):
    sender, session, _, route, _, _, _, replies, workspace, _, publish, uploads, _, _ = file_return
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"private outside")
    (workspace / "linked.bin").symlink_to(outside)
    (workspace / "directory-link").symlink_to(tmp_path, target_is_directory=True)
    (workspace / "empty.bin").touch()
    os.mkfifo(workspace / "pipe")
    for ref in ["../outside.bin", str(outside), "linked.bin", "directory-link/outside.bin", "empty.bin", "pipe", "."]:
        with pytest.raises((ValueError, OSError)):
            publish([ref])
    assert not uploads and not replies
    _write(_root(sender.root) / "policy.json", {"schema_version": POLICY_SCHEMA, "sources": {}})
    publish()
    drain(sender.root, sender.server.registry_path, sender.server.lark_private_conversations.core.controller.store, sender)
    assert not uploads and not replies


def test_changed_snapshot_cannot_upload_or_send(file_return):
    from loopx.control_plane.collaboration.result_files import result_file_path

    sender, _, _, route, _, _, transport, replies, _, _, publish, uploads, _, _ = file_return
    publish()
    result = json.loads((_root(sender.root) / "replies" / route["request_id"] / "conclusion.json").read_text())
    result_file_path(sender.root, result["attachments"][0]).write_bytes(b"changed snapshot")
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    assert not uploads and not replies


def test_lost_resource_record_after_send_never_uploads_or_resends(file_return):
    sender, _, _, route, _, _, transport, replies, _, _, publish, uploads, _, state = file_return
    publish()
    state["download_available"] = False
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    for path in (_root(sender.root) / "lark-result-files").glob("*.json"):
        path.unlink()
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    assert reply_status(sender.root, {"request_id": route["request_id"]})[0]["status"] == "explicit_unverified"
    assert len(replies) == len(uploads) == 1


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_worker_ingress_returns_the_file_through_the_original_app(file_return, surface):
    sender, _, _, route, _, _, transport, replies, workspace, raw, _, uploads, _, _ = file_return
    if surface == "cli":
        result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
            "--registry", str(sender.server.registry_path), "--runtime-root", str(sender.root),
            "manager-inbox", "report", "--goal-id", route["goal_id"], "--agent-id", route["agent_id"],
            "--request-id", route["request_id"], "--reply-text", "The report is attached.",
            "--attachment-ref", "result-fixture.bin"], cwd=workspace, text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["ok"]
    else:
        mcp = pytest.importorskip("mcp")
        ClientSession, StdioServerParameters = mcp.ClientSession, mcp.StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def exercise():
            params = StdioServerParameters(command=sys.executable, args=["-m", "loopx.collaboration_mcp",
                "--registry", str(sender.server.registry_path), "--runtime-root", str(sender.root),
                "--goal-id", route["goal_id"], "--agent-id", route["agent_id"], "--workspace", str(workspace)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("return_result", {"request_id": route["request_id"],
                    "text": "The report is attached.", "attachment_refs": ["result-fixture.bin"]})
                assert not result.isError

        asyncio.run(exercise())
    drain(sender.root, sender.server.registry_path, transport.core.controller.store, sender)
    assert reply_status(sender.root, {"request_id": route["request_id"]})[0]["status"] == "delivered"
    assert len(replies) == 1 and uploads == [raw]
