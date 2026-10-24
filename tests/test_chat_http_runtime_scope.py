"""HTTP admission reuses code identity while grants and source reads stay fresh."""

import os
from pathlib import Path

import pytest

from test_chat_ordinary_project import ordinary  # noqa: F401
from test_lark_private_conversations import connect
from loopx.control_plane import effect_runtime


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_http_reuses_code_scan_but_rereads_revoked_grants(ordinary, monkeypatch, method):  # noqa: F811
    store, runtime, provider, transport = connect(ordinary)
    _, _, contexts, request, capture, _, workspace = ordinary
    binding = transport._binding("notes-app")
    session, _ = runtime.open_session(
        goal_id=None, agent_id="codex", work_dir=workspace, objective="",
        mode="resume_latest", conversation_binding_id=binding["binding_id"],
        source_context={"source_ref": "a" * 24, "sender_ref": binding["operator_ref"],
                        "private_human_message": True},
    )
    path = "/api/chat/sessions"
    body = None
    if method == "GET":
        path += "?conversation_binding_id=" + binding["binding_id"]
    else:
        body = {"context_kind": "project", "project_ref": contexts.available()[0]["project_ref"]}
    # Warm the real source hash before counting filesystem traversals.
    assert request(path, body)[0] in {200, 201}
    source_root = Path(effect_runtime.__file__).parent
    walk = os.walk
    scans = []

    def counted_walk(root, *args, **kwargs):
        if Path(root) == source_root:
            scans.append(root)
        return walk(root, *args, **kwargs)

    monkeypatch.setattr(os, "walk", counted_walk)
    try:
        for _ in range(2):
            scans.clear()
            status, payload = request(path, body)
            assert status == 200, payload
            if method == "GET":
                assert [row["session_id"] for row in payload["sessions"]] == [session["session_id"]]
            else:
                assert payload["resumed"] is True
                assert payload["session"]["project_ref"] == body["project_ref"]
            # A new HTTP request scans again; its typed calls share that scan.
            assert len(scans) == 1
        before = store.list_sessions()
        contexts.roots = []
        status, _ = request(path, body)
        assert status == (409 if method == "GET" else 400)
        assert store.list_sessions() == before
        assert '"method": "turn/start"' not in capture.read_text()
    finally:
        runtime.close()
