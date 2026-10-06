import json
from pathlib import Path

import pytest

import loopx.chat_store as chat_store
from loopx.chat_store import CHAT_SESSION_SCHEMA_VERSION, ChatSessionStore


def test_session_id_cannot_escape_sessions_directory(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    (store.root / "session.json").write_text(
        json.dumps(
            {
                "schema_version": CHAT_SESSION_SCHEMA_VERSION,
                "session_id": "outside-sessions",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="session_id"):
        store.load_session("..")


def _dedup_store(tmp_path: Path) -> tuple[ChatSessionStore, str]:
    store = ChatSessionStore(tmp_path)
    session_id = str(
        store.create_session(
            goal_id="goal-one",
            agent_id="codex",
            executor_endpoint_id="codex",
            adapter_kind="codex_app_server",
            upstream_thread_id="thread-one",
            upstream_mode="chat",
        )["session_id"]
    )
    return store, session_id


def test_same_message_id_same_payload_returns_existing_row(tmp_path: Path) -> None:
    store, session_id = _dedup_store(tmp_path)
    first = store.append_message(
        session_id,
        role="agent",
        text="durable completion",
        turn_id="turn-one",
        message_id="replay-same",
    )
    # Append-side replay-ignore is intentional base semantics: a same-id
    # retry stays idempotent at the transcript layer, whatever the payload.
    replayed = store.append_message(
        session_id,
        role="agent",
        text="durable completion",
        turn_id="turn-one",
        message_id="replay-same",
    )
    assert replayed == first
    assert store.messages(session_id) == [first]


def test_same_message_id_different_text_replay_returns_existing_row(
    tmp_path: Path,
) -> None:
    store, session_id = _dedup_store(tmp_path)
    first = store.append_message(
        session_id,
        role="agent",
        text="original text",
        message_id="replay-text",
    )
    # The store deliberately ignores same-id replays without comparing
    # payloads: conflict detection for manager return delivery lives on the
    # drain side, so the append path must stay replay-ignore (this pins the
    # base semantics the generic transcript tests rely on).
    replayed = store.append_message(
        session_id,
        role="agent",
        text="rewritten text",
        message_id="replay-text",
    )
    assert replayed == first
    assert store.messages(session_id) == [first]


def test_same_message_id_different_role_replay_returns_existing_row(
    tmp_path: Path,
) -> None:
    store, session_id = _dedup_store(tmp_path)
    first = store.append_message(
        session_id,
        role="user",
        text="hello there",
        message_id="replay-role",
    )
    replayed = store.append_message(
        session_id,
        role="agent",
        text="hello there",
        message_id="replay-role",
    )
    assert replayed == first
    assert store.messages(session_id) == [first]


def test_only_created_at_difference_is_not_a_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, session_id = _dedup_store(tmp_path)
    stamps = iter(
        [
            "2026-01-01T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00",
        ]
    )
    monkeypatch.setattr(chat_store, "utc_now", lambda: next(stamps))
    first = store.append_message(
        session_id,
        role="user",
        text="identical body",
        message_id="replay-time",
    )
    replayed = store.append_message(
        session_id,
        role="user",
        text="identical body",
        message_id="replay-time",
    )
    assert replayed == first
    assert replayed["created_at"] == "2026-01-01T00:00:00+00:00"
    assert store.messages(session_id) == [first]
