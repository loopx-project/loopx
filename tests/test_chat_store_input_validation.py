import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

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
    replayed = store.append_message(
        session_id,
        role="agent",
        text="durable completion",
        turn_id="turn-one",
        message_id="replay-same",
    )
    assert replayed == first
    assert store.messages(session_id) == [first]


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("role", {"role": "user"}),
        ("text", {"text": "rewritten text"}),
        ("turn_id", {"turn_id": "turn-two"}),
        ("origin", {"origin": "another-runtime"}),
        ("attachments", {"attachments": [{"name": "changed.txt"}]}),
        ("goal_draft", {"goal_draft": {"objective": "Changed objective"}}),
    ],
)
def test_same_message_id_different_payload_raises_conflict(
    tmp_path: Path,
    field: str,
    changed: dict[str, object],
) -> None:
    store, session_id = _dedup_store(tmp_path)
    original = {
        "role": "agent",
        "text": "original text",
        "turn_id": "turn-one",
        "origin": "runtime",
        "attachments": [{"name": "original.txt"}],
        "goal_draft": {"objective": "Original objective"},
    }
    first = store.append_message(
        session_id,
        **original,
        message_id="replay-text",
    )
    replay = {**original, **changed}

    with pytest.raises(ValueError, match=rf"message_id.*{field}"):
        store.append_message(
            session_id,
            **replay,
            message_id="replay-text",
        )

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


def test_concurrent_message_id_conflict_appends_only_one_payload(
    tmp_path: Path,
) -> None:
    first_store, session_id = _dedup_store(tmp_path)
    second_store = ChatSessionStore(tmp_path)
    barrier = Barrier(2)

    def append(store: ChatSessionStore, text: str) -> tuple[str, str]:
        barrier.wait()
        try:
            store.append_message(
                session_id,
                role="agent",
                text=text,
                message_id="concurrent-replay",
            )
        except ValueError as exc:
            return "conflict", str(exc)
        return "appended", text

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [
            future.result()
            for future in (
                pool.submit(append, first_store, "first candidate"),
                pool.submit(append, second_store, "second candidate"),
            )
        ]

    assert sorted(outcome[0] for outcome in outcomes) == ["appended", "conflict"]
    [stored] = first_store.messages(session_id)
    assert stored["text"] in {"first candidate", "second candidate"}
    assert ("appended", stored["text"]) in outcomes
    conflict = next(detail for status, detail in outcomes if status == "conflict")
    assert "message_id" in conflict and "text" in conflict
