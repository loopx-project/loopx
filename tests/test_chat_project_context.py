"""Source-reference guidance reaches ordinary project and returned answers."""

import json

from loopx.capabilities.manager_context.answer_contract import manager_answer_contract_instruction
from loopx.presentation.answer_instruction import collaboration_answer_instruction
from test_chat_ordinary_project import ordinary  # noqa: F401


def test_source_reference_contract_reaches_native_project_turn_without_expanding_authority(ordinary):  # noqa: F811
    store, runtime, contexts, request, capture, _, workspace = ordinary
    ref = contexts.available()[0]["project_ref"]
    status, opened = request("/api/chat/sessions", {"context_kind": "project", "project_ref": ref})
    assert status == 201
    session_id = opened["session_id"]
    status, accepted = request(f"/api/chat/sessions/{session_id}/turns", {
        "message": "Explain this project and cite the source.", "client_turn_id": "source-reference",
    })
    assert status == 202
    assert runtime.wait_for_turn(session_id=session_id, turn_id=accepted["turn_id"], timeout_sec=10)["status"] == "completed"
    packets = [json.loads(line) for line in capture.read_text().splitlines()]
    start = next(row["params"] for row in packets if row.get("method") == "thread/start")
    turn = next(row["params"] for row in packets if row.get("method") == "turn/start")
    text = turn["input"][0]["text"]
    assert "repository-relative Markdown destinations are not source URLs in chat" in text
    assert "canonical repository and the exact revision actually read" in text
    assert "private, ignored, unpublished or modified local content" in text
    assert "only when this channel can resolve that authorized artifact" in text
    assert "never upload or publish content merely to make a citation clickable" in text
    # Citation usability is guidance, not an extra grant or hidden Goal.
    assert start["cwd"] == turn["cwd"] == str(workspace)
    assert start["sandbox"] == "read-only"
    assert start["approvalPolicy"] == turn["approvalPolicy"] == "never"
    assert store.load_session(session_id)["goal_id"] is None
    assert not any(row.get("method", "").startswith("thread/goal/") for row in packets)


def test_steward_and_collaboration_preserve_the_same_receiver_source_boundaries():
    steward = manager_answer_contract_instruction()
    collaboration = collaboration_answer_instruction()
    assert steward in collaboration
    assert "do not guess the hosting organization or substitute a moving branch" in steward
    assert "plain file/section reference" in steward
    assert "never upload or publish content merely to make a citation clickable" in steward
