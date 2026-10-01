"""Image requests must survive HTTP admission, persistence and Codex transport."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
from pathlib import Path
import random
import runpy
import stat
import struct
import threading
import zlib

import pytest

from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_attachments import CHAT_TURN_MAX_BODY_BYTES
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore


def screenshot_attachment():
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    pixels = random.Random(0).randbytes(256 * 256 * 3)
    scanlines = b"".join(
        b"\0" + pixels[row * 768 : (row + 1) * 768] for row in range(256)
    )
    png = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 2, 0, 0, 0)
    )
    png += chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")
    return {
        "id": "screenshot",
        "mime_type": "image/png",
        "name": "screenshot.png",
        "size": len(png),
        "data_url": "data:image/png;base64," + base64.b64encode(png).decode(),
    }


@pytest.fixture
def image_conversation(tmp_path):
    fixture = runpy.run_path(
        str(Path(__file__).parents[1] / "examples/loopx-chat-runtime-smoke.py")
    )
    capture = tmp_path / "image-digests.json"
    source = fixture["FAKE_CODEX"].replace(
        "        turn_count += 1",
        "        import hashlib\n"
        f'        with open({str(capture)!r}, "w") as output:\n'
        '            json.dump([hashlib.sha256(i["url"].encode()).hexdigest() for i in request["params"]["input"] if i["type"] == "image"], output)\n'
        "        turn_count += 1",
    )
    executable = tmp_path / "codex"
    executable.write_text(source, encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {"goals": [{"id": "fixture", "repo": str(tmp_path), "status": "active"}]}
        )
    )
    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin=str(executable))
    session, _ = runtime.open_session(
        goal_id="fixture",
        agent_id="codex",
        work_dir=tmp_path,
        objective="Inspect a screenshot.",
        mode="resume_latest",
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.chat_store, server.runtime_controller, server.registry_path = (
        store,
        runtime,
        registry,
    )
    server.verbose = False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    session_id = session["session_id"]

    def post(body, route=None, content_length=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            connection.request(
                "POST",
                route or f"/api/chat/sessions/{session_id}/turns",
                data,
                headers={
                    "Content-Type": "application/json",
                    **(
                        {"Content-Length": str(content_length)}
                        if content_length
                        else {}
                    ),
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    yield store, runtime, session_id, post, capture
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)
    runtime.close()


def test_http_screenshot_reaches_codex_once_and_survives_readback(image_conversation):
    store, runtime, session, post, capture = image_conversation
    attachment = screenshot_attachment()
    body = {
        "message": "Explain this screenshot.",
        "client_turn_id": "image-request",
        "attachments": [attachment],
    }
    assert len(json.dumps(body).encode()) > 64_000
    status, receipt = post(body)
    assert status == 202, receipt
    completed = runtime.wait_for_turn(
        session_id=session, turn_id=receipt["turn_id"], timeout_sec=10
    )
    assert completed["status"] == "completed", completed
    assert post(body)[1]["created"] is False
    user_rows = [row for row in store.messages(session) if row["role"] == "user"]
    assert len(user_rows) == 1
    assert user_rows[0]["attachments"] == [attachment]
    assert json.loads(capture.read_text()) == [
        hashlib.sha256(attachment["data_url"].encode()).hexdigest()
    ]
    reopened = ChatSessionStore(store.root.parent)
    assert reopened.messages(session)[0]["attachments"] == [attachment]


@pytest.mark.parametrize(
    "body",
    [
        {"message": "x" * 64_001, "client_turn_id": "text"},
        {
            "message": "Inspect",
            "client_turn_id": "metadata",
            "attachments": [{**screenshot_attachment(), "name": "x" * 64_001}],
        },
        {
            "message": "Inspect",
            "client_turn_id": "count",
            "attachments": [screenshot_attachment()] * 5,
        },
        {
            "message": "Inspect",
            "client_turn_id": "malformed",
            "attachments": [{**screenshot_attachment(), "size": 1}],
        },
    ],
)
def test_invalid_image_request_never_creates_work(image_conversation, body):
    store, _, session, post, capture = image_conversation
    status, error = post(body)
    assert status in {400, 413}, error
    assert error.get("turn_replay_safe") is True
    assert store.messages(session) == []
    assert not capture.exists()


def test_image_allowance_does_not_expand_other_endpoints(image_conversation):
    _, _, _, post, _ = image_conversation
    status, error = post({"question": "x" * 64_001}, "/api/chat/projection-messages")
    assert status == 400
    assert error["error"] == "request body is too large"


def test_turn_wire_budget_rejects_before_reading_or_creating_work(image_conversation):
    store, _, session, post, capture = image_conversation
    status, error = post(
        {"message": "Inspect"}, content_length=CHAT_TURN_MAX_BODY_BYTES + 1
    )
    assert status == 400
    assert error["delivery_state"] == "not_delivered"
    assert error["turn_replay_safe"] is True
    assert store.messages(session) == []
    assert not capture.exists()


def test_runtime_failure_is_not_reported_as_safe_to_replay(
    image_conversation, monkeypatch
):
    _, runtime, _, post, _ = image_conversation

    def uncertain_submission(**kwargs):
        raise ValueError("Synthetic failure while accepting work")

    monkeypatch.setattr(runtime, "submit_turn", uncertain_submission)
    status, error = post({"message": "Inspect", "client_turn_id": "uncertain"})
    assert status == 400
    assert error.get("delivery_state") != "not_delivered"
    assert error.get("turn_replay_safe") is not True
