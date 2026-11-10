import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

import loopx.chat_store as chat_store
from loopx.chat_store import CHAT_SESSION_SCHEMA_VERSION, ChatSessionStore
from loopx.capabilities.manager_context.chat_history import page_chat_messages


def test_agent_model_value_keeps_provider_protocol_characters() -> None:
    assert chat_store.normalize_agent_model_id("vendor/model-fast") == "vendor/model-fast"
    assert chat_store.normalize_agent_model_id("vendor/model  fast") == "vendor/model  fast"


@pytest.mark.parametrize("value", ["", "model\nnext", "x" * 161])
def test_agent_model_value_rejects_empty_controls_and_unbounded_values(value: str) -> None:
    with pytest.raises(ValueError, match="agent_model_id"):
        chat_store.normalize_agent_model_id(value)


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


def test_queue_admission_reuses_terminal_observation_but_reads_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.capabilities.native_chat.file_discovery import file_change_fingerprint

    store, sid = _dedup_store(tmp_path)
    request = {"client_turn_id": "original-input", "message": "Original request"}
    turn, _ = store.create_queued_turn(sid, **request)
    tid = turn["turn_id"]
    store.update_turn(sid, tid, status="completed", response={"message": "Original answer"})
    path = store._turn_path(sid, tid)
    assert store.queued_turns(sid, strict=True) == []

    read_json = chat_store._read_json
    reads = []

    def observe_read(candidate: Path) -> dict:
        if candidate == path:
            reads.append(candidate)
        return read_json(candidate)

    monkeypatch.setattr(chat_store, "_read_json", observe_read)
    _, created = store.create_queued_turn(sid, client_turn_id="new-input", message="New request")
    assert created
    if file_change_fingerprint(path) is not None:
        assert not reads

    reads.clear()
    replay, created = store.create_queued_turn(sid, **request)
    assert not created
    assert reads
    assert replay["turn_id"] == tid
    assert replay["response"]["message"] == "Original answer"

    store.update_turn(sid, tid, response={"message": "Updated answer"})
    assert store.turn_for_client(sid, request["client_turn_id"])["response"]["message"] == "Updated answer"


def test_managed_admission_uses_terminal_observations_and_full_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.capabilities.native_chat.file_discovery import file_change_fingerprint

    store, sid = _dedup_store(tmp_path)
    request = {"client_turn_id": "original-managed", "message": "Original App request"}
    original, _ = store.create_turn(sid, **request)
    tid = original["turn_id"]
    store.update_turn(sid, tid, status="completed", response={"message": "Original App answer"})
    assert store.release_active_turn(sid, tid)
    path = store._turn_path(sid, tid)
    assert store.queued_turns(sid, strict=True) == []

    read_json = chat_store._read_json
    reads = []

    def observe_read(candidate: Path) -> dict:
        if candidate == path:
            reads.append(candidate)
        return read_json(candidate)

    monkeypatch.setattr(chat_store, "_read_json", observe_read)
    _, created = store.create_turn(sid, client_turn_id="new-managed", message="New App request")
    assert created
    if file_change_fingerprint(path) is not None:
        assert not reads

    store.update_turn(sid, tid, response={"message": "Updated App answer"})
    replay, created = store.create_turn(sid, **request)
    assert not created
    assert replay["turn_id"] == tid and replay["response"]["message"] == "Updated App answer"
    with pytest.raises(ValueError, match="different request"):
        store.create_turn(sid, **{**request, "message": "Conflicting App request"})


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("state", ["queued", "completed"])
@pytest.mark.parametrize("warm", [False, True])
def test_matched_request_read_failure_never_admits_a_duplicate(tmp_path, monkeypatch, managed, state, warm):
    import errno

    store, sid = _dedup_store(tmp_path)
    create = store.create_turn if managed else store.create_queued_turn
    request = {"client_turn_id": "original-request", "message": "Same original request"}
    first, _ = create(sid, **request)
    path = store._turn_path(sid, first["turn_id"])
    if state == "completed":
        store.update_turn(sid, first["turn_id"], status=state, response={"message": "Original answer"})
        store.release_active_turn(sid, first["turn_id"])
    if warm:
        store.queued_turns(sid, strict=True)
    before_messages = store.messages(sid)
    read_text = Path.read_text
    reads = 0

    def faulty_read(candidate, *args, **kwargs):
        nonlocal reads
        if candidate == path:
            reads += 1
            if reads == (1 if warm and state == "completed" else 2):
                raise OSError(errno.EIO, "synthetic transient read failure")
        return read_text(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", faulty_read)
        try:
            replay, created = create(sid, **request)
            assert not created
            assert replay["turn_id"] == first["turn_id"]
        except ValueError:
            pass  # A known match may fail closed; its identity must survive.
    assert store.messages(sid) == before_messages
    turns = [json.loads(p.read_text()) for p in path.parent.glob("*.json") if not p.name.endswith(".events.json")]
    assert [t["turn_id"] for t in turns if t.get("client_turn_id") == request["client_turn_id"]] == [first["turn_id"]]
    healthy, created = create(sid, **request)
    assert not created and healthy["turn_id"] == first["turn_id"]


@pytest.mark.parametrize("fault", [None, "changed-turn", "failed", "wrong-invocation", "immutable"])
def test_completed_native_retention_is_fenced_and_private(tmp_path: Path, fault: str | None) -> None:
    store, sid = _dedup_store(tmp_path)
    turn, _ = store.create_turn(sid, client_turn_id="original-input", message="Original visible request")
    tid = turn["turn_id"]
    store.update_turn(sid, tid, status="completed", upstream_turn_id="invocation-one",
        response={"message": "Original visible answer"})
    original = store.load_turn(sid, tid)
    result = {"thread_id": "thread-one", "upstream_turn_id": "invocation-one",
        "steps": [{"text": "PRIVATE_NATIVE_COMPLETION_SENTINEL"}]}
    if fault == "changed-turn":
        store.update_turn(sid, tid, response={"message": "A changed result"})
    elif fault == "failed":
        store.update_turn(sid, tid, status="failed")
        original = store.load_turn(sid, tid)
    elif fault == "wrong-invocation":
        result["upstream_turn_id"] = "foreign-invocation"
    before = store._turn_path(sid, tid).read_bytes()
    if fault in {"changed-turn", "failed", "wrong-invocation"}:
        with pytest.raises(ValueError):
            store.retain_native_result(sid, tid, result, completed_turn=original)
        assert not store.native_result(sid, tid)
    else:
        store.retain_native_result(sid, tid, result, completed_turn=original)
        path = store._session_dir(sid) / "native-results" / f"{tid}.json"
        stamp = path.stat().st_mtime_ns
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        store.retain_native_result(sid, tid, result, completed_turn=original)
        assert path.stat().st_mtime_ns == stamp
        if fault == "immutable":
            with pytest.raises(ValueError, match="immutable"):
                store.retain_native_result(sid, tid, {**result, "steps": []}, completed_turn=original)
        assert store.native_result(sid, tid) == result
    assert store._turn_path(sid, tid).read_bytes() == before
    assert "PRIVATE_NATIVE_COMPLETION_SENTINEL" not in json.dumps(store.messages(sid))


def test_history_cursor_rejects_other_session_and_rewritten_prefix(tmp_path: Path) -> None:
    store, sid = _dedup_store(tmp_path)
    for i in range(5):
        store.append_message(sid, role="user", text=f"row {i}")
    page = page_chat_messages(sid, store.messages(sid, strict=True), limit=2)
    cursor = page["message_page"]["next_cursor"]
    other = store.create_session(goal_id="goal-one", agent_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="other", upstream_mode="chat")["session_id"]
    with pytest.raises(ValueError, match="cursor"):
        page_chat_messages(other, store.messages(other, strict=True), limit=2, cursor=cursor)
    path = store._session_dir(sid) / "messages.jsonl"
    original = path.read_bytes()
    rows = store.messages(sid)
    rows[0]["text"] = "Rewritten source"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="cursor"):
        page_chat_messages(sid, store.messages(sid, strict=True), limit=2, cursor=cursor)
    path.write_bytes(original + b"{broken record}\n")
    with pytest.raises(ValueError, match="journal record"):
        page_chat_messages(sid, store.messages(sid, strict=True), limit=2)
    path.write_bytes(original)
    assert page_chat_messages(sid, store.messages(sid, strict=True), limit=2, cursor=cursor)["messages"] == store.messages(sid)[2:4]
    path.write_text("\n".join(path.read_text().splitlines()[:3]) + "\n")
    with pytest.raises(ValueError, match="cursor"):
        page_chat_messages(sid, store.messages(sid, strict=True), limit=2, cursor=cursor)


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
