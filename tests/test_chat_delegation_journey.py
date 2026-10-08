"""Durable App handoff integration; scripted model, real Chat/inbox/return owners.

This qualifies transport and readback, not model routing quality, native worker
execution, publication, or live steering. Paid model qualification is release-only.
"""

import json

import pytest

from loopx.capabilities.manager_context import acknowledge
from loopx.capabilities.manager_context.roundtrip import (
    drain,
    project_chat_session_snapshot,
    report,
)
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.collaboration.peers import read_inbox


@pytest.mark.parametrize("channel", ["manager", "goal.community"])
def test_correction_and_late_draft_return_to_original_conversation(
    tmp_path, monkeypatch, channel
):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "community",
                        "repo": str(tmp_path),
                        "coordination": {"registered_agents": ["writer"]},
                    }
                ]
            }
        )
    )
    original_registry = registry.read_bytes()
    store = ChatSessionStore(tmp_path)
    controller = ChatRuntimeController(
        store=store,
        codex_bin="unused",
        registry_path=registry,
    )
    calls = []
    drafts = [
        ("给 LoopX 写份社区问卷草稿。", "Prepare a community survey draft", []),
        (
            "中文优先，先别发布。",
            "Revise the community survey draft",
            ["Chinese first", "Do not publish"],
        ),
    ]

    class ScriptedModel:
        upstream_thread_id = "scripted-model"

        def healthcheck(self):
            return True

        def close_session(self):
            pass

        def start_turn(self, message, sink):
            # Keep the runtime's scoped authority catalog and original input in
            # this integration; only model inference and broad status are faked.
            request, purpose, constraints = drafts[len(calls)]
            assert message.endswith(request)
            assert '"agent_id": "writer"' in message
            calls.append(request)
            return {
                "message": "Passing the brief to the existing owner.",
                "context_handoff": {
                    "goal_id": "community",
                    "agent_id": "writer",
                    "brief": {
                        "schema_version": "collaboration_brief_v0",
                        "purpose": purpose,
                        "context": "The requested work is a LoopX community survey draft.",
                        "constraints": constraints,
                        "inputs": [],
                        "acceptance": ["Return a readable Markdown draft"],
                        "return_requirement": "Return the draft to this conversation",
                    },
                },
                "proposals": [],
                "gate": None,
            }

    monkeypatch.setattr(
        controller,
        "capabilities",
        lambda: [
            {
                "agent_id": "codex",
                "available": True,
                "adapter_kind": "codex_app_server",
            }
        ],
    )
    monkeypatch.setattr(controller, "_start_adapter", lambda **_: ScriptedModel())
    monkeypatch.setattr(
        "loopx.chat_manager_context.collect_manager_turn_context",
        lambda *_, **__: {"coverage": {}, "goals": []},
    )
    try:
        session, _ = controller.open_session(
            goal_id="community",
            agent_id="codex",
            work_dir=tmp_path,
            objective="Community survey",
            mode="new",
            channel_id=channel,
        )
        sid = session["session_id"]
        receipts, turns = [], []
        for index, (message, _, _) in enumerate(drafts):
            arguments = dict(
                session_id=sid,
                client_turn_id=f"owner-input-{index}",
                message=message,
                work_dir=tmp_path,
                objective="Community survey",
            )
            accepted, created = controller.submit_turn(**arguments)
            assert created
            completed = controller.wait_for_turn(
                session_id=sid,
                turn_id=accepted["turn_id"],
                timeout_sec=10,
            )
            assert completed["status"] == "completed", completed
            reply = completed["response"]["message"]
            assert "**已转交给 `writer`。**" in reply
            assert drafts[index][1] in reply
            assert "是否已开始处理尚未核实" in reply
            if index:
                assert "Chinese first" in reply and "Do not publish" in reply
            receipt = completed["response"]["context_handoff_receipt"]
            assert receipt["agent_id"] == "writer"
            assert not completed["response"]["proposals"]
            assert completed["response"]["gate"] is None
            receipts.append(receipt)
            turns.append(accepted["turn_id"])

            # A lost HTTP response can replay the same ingress without running
            # another model turn or creating another recipient request.
            replay, created = controller.submit_turn(**arguments)
            assert not created and replay["turn_id"] == accepted["turn_id"]
            assert len(calls) == index + 1

            inbox = read_inbox(tmp_path, registry, "community", "writer")
            received = next(
                row
                for row in inbox["items"]
                if row["request_id"] == receipt["request_id"]
            )
            assert received["message"] == message
            assert received["brief"]["constraints"] == drafts[index][2]
            # This is a receiver decision, separate from supplied/delivered.
            acknowledge(
                tmp_path,
                "community",
                "writer",
                receipt["request_id"],
                "adopt",
                "Use these constraints for the existing draft.",
            )

        assert receipts[0]["request_id"] != receipts[1]["request_id"]
        snapshot = project_chat_session_snapshot(
            tmp_path, ChatSessionStore(tmp_path), sid, registry=registry
        )
        handed_off = [
            row["collaboration"]
            for row in snapshot["messages"]
            if row.get("collaboration")
        ]
        assert [row["decision"] for row in handed_off] == ["adopt", "adopt"]
        assert all(row["read_status"] == "supplied" for row in handed_off)
        assert all(not row["returns"] for row in handed_off)
        assert handed_off[1]["brief"]["constraints"] == [
            "Chinese first",
            "Do not publish",
        ]

        # A new active session must not steal late results from the source.
        replacement, _ = controller.open_session(
            goal_id="community",
            agent_id="codex",
            work_dir=tmp_path,
            objective="Community survey",
            mode="new",
            channel_id=channel,
        )
        assert replacement["session_id"] != sid
    finally:
        controller.close()

    draft = "# LoopX 社区问卷（草稿）\n\n1. 你最希望 Agent 帮你完成什么？\n2. 哪一步最费精力？\n\n尚未发布。"
    report(
        tmp_path,
        "community",
        "writer",
        receipts[0]["request_id"],
        "conclusion",
        "The requested draft incorporates the later Chinese-first correction; nothing was published.",
    )
    report(
        tmp_path, "community", "writer", receipts[1]["request_id"], "conclusion", draft
    )

    def no_external_write(*_):
        pytest.fail("Private App result must not be sent to an external audience")

    # Reload the real durable store and repeat delivery: no in-memory dedupe.
    for _ in range(2):
        drain(tmp_path, registry, ChatSessionStore(tmp_path), no_external_write)
    snapshot = project_chat_session_snapshot(tmp_path, ChatSessionStore(tmp_path), sid, registry=registry)
    returned = [
        row for row in snapshot["messages"] if row.get("origin") == "manager_followup"
    ]
    assert len(returned) == 2
    assert {row["turn_id"] for row in returned} == set(turns)
    assert sum(draft in row["text"] for row in returned) == 1
    assert all(row["return_delivery"]["status"] == "delivered" for row in returned)
    assert not ChatSessionStore(tmp_path).messages(replacement["session_id"])
    assert len(list((store.root / "sessions" / sid / "turns").glob("*.json"))) == 2
    assert calls == [entry[0] for entry in drafts]
    assert registry.read_bytes() == original_registry
