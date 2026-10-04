#!/usr/bin/env python3
"""Smoke-test owner-local durable Chat session state."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.chat_store import ChatSessionStore  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-chat-store-") as raw_tmp:
        root = Path(raw_tmp)
        store = ChatSessionStore(root)
        session = store.create_session(
            goal_id="goal-one",
            agent_id="codex",
            adapter_kind="codex_app_server",
            upstream_thread_id="thread-private",
            upstream_mode="chat",
        )
        session_id = session["session_id"]
        public = store.public_session(session)
        assert public["resumable"] is True, public
        assert "upstream_thread_id" not in public, public
        assert "upstream_mode" not in public, public
        assert public["transcript_revision"] is None, public
        store.append_message(session_id, role="agent", text="First result", message_id="first-result")
        first_revision = store.public_session(session)["transcript_revision"]
        assert len(first_revision) == 64
        store.append_message(session_id, role="agent", text="Later result", message_id="later-result", origin="manager_followup")
        later = store.list_sessions(goal_id="goal-one", agent_id="codex")[0]
        assert later["updated_at"] == public["updated_at"], "Transcript writes must not invent execution activity"
        assert later["transcript_revision"] != first_revision, later
        assert store.session_snapshot(session_id)["session"]["transcript_revision"] == later["transcript_revision"]
        store.append_message(session_id, role="agent", text="Later result", message_id="later-result", origin="manager_followup")
        assert store.public_session(session)["transcript_revision"] == later["transcript_revision"], "Idempotent publication is quiet"
        assert session["upstream_mode"] == "chat", session
        assert store.latest_session(goal_id="goal-one", agent_id="codex") == session
        store.update_session(session_id, status="resume_failed", last_error_code="resume_failed")
        assert store.latest_session(goal_id="goal-one", agent_id="codex") is None
        assert store.public_session(store.load_session(session_id))["resumable"] is False
        failed_history = store.list_sessions(
            goal_id="goal-one",
            agent_id="codex",
            channel_id="goal.goal-one",
        )
        assert failed_history[0]["session_id"] == session_id, failed_history
        assert failed_history[0]["status"] == "resume_failed", failed_history
        store.update_session(session_id, status="ready", last_error_code=None)

        manager_session = store.create_session(
            goal_id="goal-one",
            agent_id="codex",
            adapter_kind="codex_app_server",
            upstream_thread_id="thread-manager-private",
            channel_id="manager",
        )
        assert store.latest_session(
            goal_id="goal-one",
            agent_id="codex",
            channel_id="manager",
        ) == manager_session
        assert store.latest_session(
            goal_id=None,
            agent_id="codex",
            channel_id="manager",
        ) == manager_session
        try:
            store.latest_session(goal_id=None, agent_id="codex")
        except ValueError as exc:
            assert "channel_id" in str(exc), exc
        else:
            raise AssertionError("goal-less latest session lookup accepted no channel")
        assert store.latest_session(
            goal_id="goal-one",
            agent_id="codex",
            channel_id="goal.goal-one",
        )["session_id"] == session_id
        assert store.list_sessions(
            goal_id=None,
            agent_id="codex",
            channel_id="manager",
        )[0]["session_id"] == manager_session["session_id"]

        turn, created = store.create_turn(
            session_id,
            client_turn_id="client-turn-one",
            message="visible user message",
        )
        assert created is True, turn
        duplicate, created = store.create_turn(
            session_id,
            client_turn_id="client-turn-one",
            message="visible user message",
        )
        assert created is False and duplicate["turn_id"] == turn["turn_id"], duplicate
        event = store.append_event(
            session_id,
            turn["turn_id"],
            kind="assistant.delta",
            payload={"text": "visible delta"},
        )
        assert event["event_id"] == "2", event
        assert [row["kind"] for row in store.events_after(session_id, turn["turn_id"], "1")] == [
            "assistant.delta"
        ]

        store.update_turn(
            session_id,
            turn["turn_id"],
            status="completed",
            response={"message": "visible final", "proposals": [], "gate": None},
            completed_at="2000-01-01T00:00:00Z",
        )
        store.append_message(
            session_id,
            role="agent",
            text="visible final",
            turn_id=turn["turn_id"],
        )
        store.update_session(session_id, status="ready", active_turn_id=None)
        snapshot = store.session_snapshot(session_id)
        assert [item["role"] for item in snapshot["messages"]] == ["agent", "agent", "user", "agent"], snapshot
        assert "thread-private" not in json.dumps(snapshot), snapshot
        store.append_message(
            manager_session["session_id"],
            role="user",
            text="visible manager message",
        )
        assert store.compact_completed_events(older_than_hours=24) == 1

        restarted_store = ChatSessionStore(root)
        restarted_manager = restarted_store.list_sessions(
            goal_id=None,
            agent_id="codex",
            channel_id="manager",
        )[0]
        restarted_snapshot = restarted_store.session_snapshot(restarted_manager["session_id"])
        assert [item["text"] for item in restarted_snapshot["messages"]] == [
            "visible manager message"
        ], restarted_snapshot

        session_path = root / "chat" / "sessions" / session_id / "session.json"
        legacy_session = json.loads(session_path.read_text(encoding="utf-8"))
        legacy_session.pop("channel_id", None)
        session_path.write_text(json.dumps(legacy_session) + "\n", encoding="utf-8")
        legacy_store = ChatSessionStore(root)
        legacy_history = legacy_store.list_sessions(
            goal_id="goal-one",
            agent_id="codex",
            channel_id="goal.goal-one",
        )
        assert legacy_history[0]["channel_id"] == "goal.goal-one", legacy_history
        assert not any(
            event["kind"] == "assistant.delta"
            for event in store.events_after(session_id, turn["turn_id"], None)
        )

        assert session_path.stat().st_mode & 0o777 == 0o600
        assert session_path.parent.stat().st_mode & 0o777 == 0o700

    print("loopx-chat-store-smoke: ok")


if __name__ == "__main__":
    main()
