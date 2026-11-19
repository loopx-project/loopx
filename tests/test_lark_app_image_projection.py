"""App image projection uses the original Core input and provider outbox."""
import json
from pathlib import Path

import pytest
from test_chat_ordinary_project import ordinary  # noqa: F401
from test_chat_image_attachments import PNG_BYTES, PNG_DATA_URL
from test_lark_private_conversations import connect, _drain_app_projection

from loopx.extensions.lark.private_conversations import LarkPrivateConversations


class ImageIO:
    def __init__(self, provider):
        self.provider = provider
        self.resources = {}
        self.uploads = 0
        self.downloads = 0
        self.corrupt_download = False
        self.unknown_upload = False
        self.after_upload = None
        self.after_send_preview = None

    def __call__(self, args, cwd=None, timeout=None):
        if "images" in args and "create" in args:
            self.provider.calls.append(list(args))
            assert json.loads(args[args.index("--data") + 1]) == {"image_type": "message"}
            assert args[args.index("--as") + 1] == "bot"
            if "--dry-run" in args:
                return {"returncode": 0, "stdout": '{"ok":true}'}
            raw = (Path(cwd) / args[args.index("--file") + 1]).read_bytes()
            self.uploads += 1
            key = f"img_uploaded_{self.uploads}"
            self.resources[key] = raw
            if self.after_upload:
                self.after_upload()
            return {"returncode": 0, "stdout": json.dumps({"ok": True,
                "data": {} if self.unknown_upload else {"image_key": key}})}
        if "+messages-resources-download" in args:
            self.provider.calls.append(list(args))
            assert args[args.index("--type") + 1] == "image"
            key = args[args.index("--file-key") + 1]
            message = self.provider.messages[args[args.index("--message-id") + 1]]
            assert key in message["body"]["content"]
            raw = b"corrupt" if self.corrupt_download else self.resources[key]
            (Path(cwd) / args[args.index("--output") + 1]).write_bytes(raw)
            self.downloads += 1
            return {"returncode": 0, "stdout": '{"ok":true}'}
        result = self.provider(args, cwd, timeout)
        if "+messages-send" in args and "--dry-run" in args and self.after_send_preview:
            self.after_send_preview()
        return result


def _journey(fixture, *, caption="Inspect this diagram", count=1):
    store, runtime, provider, transport = connect(fixture)
    io = ImageIO(provider)
    transport.runner = io
    source = provider.event("notes-app", "app-image-source", "Original source")
    transport.admit("notes-app", source)
    original = transport.core.pending()[0]
    sid = original["session_id"]
    assert runtime.wait_for_turn(session_id=sid, turn_id=original["turn_id"], timeout_sec=10)["status"] == "completed"
    transport.reconcile()
    before = len(provider.writes)
    attachments = [{"id": f"app-image-{i}", "name": f"diagram-{i}.png",
                    "mime_type": "image/png", "size": len(PNG_BYTES), "data_url": PNG_DATA_URL}
                   for i in range(count)]
    status, result = fixture[3](f"/api/chat/sessions/{sid}/turns", {
        "message": caption, "attachments": attachments, "client_turn_id": "app-original-image"})
    assert status == 202
    assert runtime.wait_for_turn(session_id=sid, turn_id=result["turn_id"], timeout_sec=10)["status"] == "completed"
    transport._web_projection_scan_at = 0  # The next scheduled discovery, without a wall-clock sleep.
    return store, runtime, provider, transport, io, sid, before


def _projection(transport):
    return json.loads(next((transport.root / "web-projections").glob("*.json")).read_text())


def _restart(runtime, transport, io):
    return LarkPrivateConversations(controller=runtime, runtime_root=transport.runtime_root,
                                   runner=io, cli_bin="lark-cli")


def test_app_images_reach_same_original_chat_in_order_once_and_survive_restart(ordinary):  # noqa: F811
    store, runtime, provider, transport, io, sid, before = _journey(ordinary, count=2)
    requests = ordinary[4].read_text()
    try:
        _drain_app_projection(transport, lambda: len(provider.writes) >= before + 2)
        transport.reconcile()
        assert io.uploads == 2 and io.downloads == 2
        messages = [m for ref, m in provider.messages.items() if ref.startswith("om_out_")][before:]
        rows = json.loads(messages[0]["body"]["content"])["zh_cn"]["content"]
        assert rows[0] == [{"tag": "md", "text": "**你（App）**\n\nInspect this diagram"}]
        assert rows[1:] == [[{"tag": "img", "image_key": key}] for key in io.resources]
        assert messages[0]["chat_id"] == "oc_notes_app" and not messages[0].get("parent_id")
        assert _projection(transport)["status"] == "delivered"
        assert any(p.get("images_verified") for p in _projection(transport)["deliveries"].values())
        written = list(provider.writes)
        restarted = _restart(runtime, transport, io)
        for _ in range(3):
            restarted.reconcile()
        assert provider.writes == written and io.uploads == 2
        assert ordinary[4].read_text() == requests and len(store.list_sessions()) == 1
        assert len([m for m in store.messages(sid) if m.get("origin") == "web"]) == 1
    finally:
        runtime.close()


def test_image_byte_readback_failure_recovers_same_receipt_without_send_or_upload(ordinary):  # noqa: F811
    _, runtime, provider, transport, io, _, before = _journey(ordinary)
    io.corrupt_download = True
    try:
        transport.reconcile()
        assert len(provider.writes) == before + 1
        assert _projection(transport)["status"] == "pending"
        sent = list(provider.writes)
        transport.reconcile()
        assert provider.writes == sent and io.uploads == 1
        io.corrupt_download = False
        restarted = _restart(runtime, transport, io)
        _drain_app_projection(restarted, lambda: len(provider.writes) == before + 2)
        restarted.reconcile()
        assert _projection(restarted)["status"] == "delivered" and io.uploads == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("effect", ["upload", "send"])
def test_unknown_media_effect_never_reuploads_or_resends_after_restart(ordinary, effect):  # noqa: F811
    _, runtime, provider, transport, io, _, before = _journey(ordinary)
    io.unknown_upload = effect == "upload"
    provider.lose_next_reply = effect == "send"
    try:
        transport.reconcile()
        assert io.uploads == 1
        sent = list(provider.writes)
        assert len(sent) == before + (effect == "send")
        restarted = _restart(runtime, transport, io)
        for _ in range(3):
            restarted.reconcile()
        assert provider.writes == sent and io.uploads == 1
        assert _projection(restarted)["status"] == "pending"
    finally:
        runtime.close()


def test_revoked_image_source_after_upload_cannot_send_or_continue(ordinary):  # noqa: F811
    _, runtime, provider, transport, io, _, before = _journey(ordinary)
    binding = transport._binding("notes-app")
    io.after_upload = lambda: transport.bindings.disconnect(binding["binding_id"],
        expected_revision=transport.bindings.read()["revision"])
    try:
        transport.reconcile()
        assert io.uploads == 1 and len(provider.writes) == before
        restarted = _restart(runtime, transport, io)
        restarted.reconcile()
        assert io.uploads == 1 and len(provider.writes) == before
    finally:
        runtime.close()


@pytest.mark.parametrize("long_caption,preview_number,allowed_prefix", [
    (False, 1, 0), (True, 1, 0), (True, 2, 0), (True, 3, 1),
])
def test_disconnect_during_send_preview_stops_every_remaining_part(
        ordinary, long_caption, preview_number, allowed_prefix):  # noqa: F811
    caption = "Caption and evidence.\n" * 2100 if long_caption else "Inspect this diagram"
    _, runtime, provider, transport, io, _, before = _journey(ordinary, caption=caption)
    binding = transport._binding("notes-app")
    previews = 0

    def revoke():
        nonlocal previews
        previews += 1
        if previews == preview_number:
            transport.bindings.disconnect(binding["binding_id"],
                expected_revision=transport.bindings.read()["revision"])

    io.after_send_preview = revoke
    try:
        transport.reconcile()
        assert previews >= preview_number
        assert io.uploads == 1
        assert len(provider.writes) == before + allowed_prefix
        assert not any('"tag":"img"' in m.get("body", {}).get("content", "")
                       for m in provider.messages.values())
        row = _projection(transport)
        assert row["status"] != "delivered"
        # No provider write was attempted at the revoked boundary. Earlier
        # confirmed parts stay frozen; restart cannot upload or send the rest.
        attempts = list(provider.writes)
        _restart(runtime, transport, io).reconcile()
        assert provider.writes == attempts and io.uploads == 1
    finally:
        runtime.close()


def test_long_app_caption_puts_images_only_in_last_concrete_post(ordinary):  # noqa: F811
    caption = "Full caption and evidence.\n" * 2100 + "IMAGE CAPTION END"
    _, runtime, provider, transport, io, _, before = _journey(ordinary, caption=caption)
    try:
        _drain_app_projection(transport, lambda: any("IMAGE CAPTION END" in text for _, text in provider.writes[before:]))
        transport.reconcile()
        image_messages = [m for m in provider.messages.values() if '"tag":"img"' in m.get("body", {}).get("content", "")]
        assert len(image_messages) == 1 and "IMAGE CAPTION END" in image_messages[0]["body"]["content"]
        assert io.uploads == 1 and io.downloads == 1
        _drain_app_projection(transport, lambda: _projection(transport)["status"] == "delivered")
        requests = ordinary[4].read_text()
        sent = list(provider.writes)
        _restart(runtime, transport, io).reconcile()
        assert provider.writes == sent and ordinary[4].read_text() == requests
    finally:
        runtime.close()


def test_resumed_parts_recheck_disconnect_after_preview_without_replaying_prefix(ordinary):  # noqa: F811
    _, runtime, provider, transport, io, _, before = _journey(
        ordinary, caption="Caption and evidence.\n" * 2100)
    binding = transport._binding("notes-app")
    provider.verify_replies = False
    try:
        transport.reconcile()
        assert len(provider.writes) == before + 1 and io.uploads == 1
        assert _projection(transport)["status"] == "pending"
        provider.verify_replies = True
        previews = 0

        def revoke():
            nonlocal previews
            previews += 1
            transport.bindings.disconnect(binding["binding_id"],
                expected_revision=transport.bindings.read()["revision"])

        io.after_send_preview = revoke
        sent = list(provider.writes)
        restarted = _restart(runtime, transport, io)
        restarted.reconcile()
        assert previews == 1
        assert provider.writes == sent and io.uploads == 1
        assert _projection(restarted)["status"] != "delivered"
        restarted.reconcile()
        assert provider.writes == sent and io.uploads == 1
    finally:
        runtime.close()


def test_old_verified_placeholder_remains_partial_without_supplemental_send(ordinary):  # noqa: F811
    store, runtime, provider, transport, io, sid, _ = _journey(ordinary)
    try:
        path = transport._web_projection_paths()[0]
        row = json.loads(path.read_text())
        message = next(m for m in store.messages(sid) if m.get("origin") == "web")
        phase = "web." + message["message_id"]
        # Freeze an actual historical text-only send through the unchanged
        # sender. Upgrading cannot turn that receipt into image delivery.
        text = "**你（App）**\n\n" + message["text"] + "\n\n（此条 App 附件尚未同步，请在原 App 查看。）"
        assert transport._deliver(path, row, phase, text, inbox=lambda: transport._inbox(row),
            authorization_check=lambda: True, reply_placement="chat_root", delivery_id="legacy-image-placeholder",
            finalize_source=False)
        sent_placeholder = provider.writes[-1]
        restarted = _restart(runtime, transport, io)
        _drain_app_projection(restarted, lambda: _projection(restarted)["status"] == "partial")
        assert provider.writes.count(sent_placeholder) == 1 and io.uploads == 0
        assert _projection(restarted)["deliveries"][phase]["text"] == text
    finally:
        runtime.close()
