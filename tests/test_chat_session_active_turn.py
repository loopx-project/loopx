from collections.abc import Callable
from pathlib import Path
import threading
import time

import pytest

import loopx.chat_store as chat_store
from loopx.chat_agent import CodexChatAgentError
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import (
    SESSION_QUEUE_MAX_PENDING,
    TERMINAL_TURN_STATES,
    ChatSessionStore,
)


class _BlockingChatAdapter:
    upstream_thread_id = "fake-upstream"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed = False

    def capabilities(self) -> dict[str, object]:
        return {}

    def start_turn(self, message: str, event_sink) -> dict[str, object]:
        del message, event_sink
        self.started.set()
        self.release.wait(timeout=2)
        return {"message": "late response"}

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        del turn_id
        self.release.set()

    def close_session(self) -> None:
        self.closed = True
        self.release.set()

    def healthcheck(self) -> bool:
        return True


class _HealthyChatAdapter:
    upstream_thread_id = "thread-one"

    def __init__(self) -> None:
        self.closed = False

    def healthcheck(self) -> bool:
        return True

    def close_session(self) -> None:
        self.closed = True


class _FailingChatAdapter(_HealthyChatAdapter):
    def start_turn(self, message: str, event_sink) -> dict[str, object]:
        del message, event_sink
        raise CodexChatAgentError(
            "transport disconnected",
            error_code="transport_disconnected",
            gate={"kind": "host_tool_gate"},
        )

    def healthcheck(self) -> bool:
        return False


def _slow_new_turn_writes(monkeypatch) -> set[str]:
    original_atomic_write = chat_store._atomic_write_json
    turn_write_threads: set[str] = set()
    condition = threading.Condition()

    def slow_new_turn_write(
        path: Path,
        payload: dict[str, object],
        *,
        preserve_mode: bool = False,
    ) -> None:
        is_new_turn_file = (
            path.parent.name == "turns"
            and path.name.endswith(".json")
            and not path.name.endswith(".events.json")
            and not preserve_mode
        )
        if is_new_turn_file and threading.current_thread().name.startswith("creator-"):
            with condition:
                turn_write_threads.add(threading.current_thread().name)
                condition.notify_all()
                deadline = time.monotonic() + 0.25
                while len(turn_write_threads) < 2 and time.monotonic() < deadline:
                    condition.wait(timeout=deadline - time.monotonic())
        original_atomic_write(path, payload, preserve_mode=preserve_mode)

    monkeypatch.setattr(chat_store, "_atomic_write_json", slow_new_turn_write)
    return turn_write_threads


def _run_two_client_turn_creators(
    create_turn: Callable[[str], tuple[dict[str, object], bool]],
) -> tuple[list[tuple[str, str, bool]], list[str]]:
    results: list[tuple[str, str, bool]] = []
    errors: list[str] = []

    def create(client_turn_id: str) -> None:
        try:
            turn, created = create_turn(client_turn_id)
            results.append((client_turn_id, str(turn["turn_id"]), created))
        except RuntimeError as exc:
            errors.append(str(exc))

    threads = [
        threading.Thread(
            target=create,
            args=(f"client-{index}",),
            name=f"creator-{index}",
        )
        for index in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not any(thread.is_alive() for thread in threads)
    return results, errors


def test_concurrent_managed_turn_creation_claims_active_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    turn_write_threads = _slow_new_turn_writes(monkeypatch)
    results, errors = _run_two_client_turn_creators(
        lambda client_turn_id: store.create_turn(
            session_id,
            client_turn_id=client_turn_id,
            message=f"message from {client_turn_id}",
        )
    )

    assert len(results) == 1
    assert len(errors) == 1
    active_turn_id = results[0][1]
    assert errors == [active_turn_id]
    assert len(turn_write_threads) == 1
    assert [
        item["text"] for item in store.messages(session_id) if item["role"] == "user"
    ] == [f"message from {results[0][0]}"]
    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "busy"
    assert current["active_turn_id"] == active_turn_id


def test_chat_idempotency_keys_reject_different_requests(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    store.create_turn(session_id, client_turn_id="turn-key", message="first")
    store.create_queued_turn(session_id, client_turn_id="queue-key", message="first")
    store.create_ingress_receipt(
        session_id,
        client_ingress_id="ingress-key",
        mode="live_steering",
        message="first",
    )

    with pytest.raises(ValueError, match="client_turn_id already belongs"):
        store.create_turn(session_id, client_turn_id="turn-key", message="second")
    with pytest.raises(ValueError, match="client_turn_id already belongs"):
        store.create_queued_turn(session_id, client_turn_id="queue-key", message="second")
    with pytest.raises(ValueError, match="client_ingress_id already belongs"):
        store.create_ingress_receipt(
            session_id,
            client_ingress_id="ingress-key",
            mode="live_steering",
            message="second",
        )


def test_generated_message_id_skips_transcript_deduplication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    expected = store.append_message(
        session_id,
        role="agent",
        text="durable completion",
        message_id="completion-one",
    )
    assert (
        store.append_message(
            session_id,
            role="agent",
            text="ignored replay",
            message_id="completion-one",
        )
        == expected
    )

    read_messages = chat_store._read_jsonl
    monkeypatch.setattr(
        chat_store,
        "_read_jsonl",
        lambda _path: pytest.fail("generated message IDs must not scan the transcript"),
    )
    appended = store.append_message(session_id, role="user", text="new message")

    assert appended["message_id"] != "completion-one"
    assert read_messages(store._session_dir(session_id) / "messages.jsonl") == [
        expected,
        appended,
    ]


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("with_image", [False, True])
def test_managed_replay_compares_durable_attachments(
    tmp_path: Path, restart: bool, with_image: bool,
) -> None:
    store = ChatSessionStore(tmp_path)
    session_id = store.create_session(
        goal_id="goal-one", agent_id="codex", executor_endpoint_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="thread-one",
        upstream_mode="chat",
    )["session_id"]
    image = {"id": "image-one", "mime_type": "image/png", "name": "example.png"}
    attachments = [image] if with_image else []
    original, created = store.create_turn(
        session_id, client_turn_id="request", message="inspect",
        attachments=attachments,
    )
    assert created
    assert "attachments" not in original  # No duplicate image payload in Turn state.
    if restart:
        store = ChatSessionStore(tmp_path)
    replay, created = store.create_turn(
        session_id, client_turn_id="request", message="inspect",
        attachments=attachments or None,
    )
    assert not created
    assert replay["turn_id"] == original["turn_id"]
    for changed in ([{**image, "id": "different"}], [] if with_image else [image]):
        with pytest.raises(ValueError, match="different request"):
            store.create_turn(
                session_id, client_turn_id="request", message="inspect", attachments=changed,
            )
    with pytest.raises(ValueError, match="different request"):
        store.create_turn(
            session_id, client_turn_id="request", message="inspect",
            attachments=attachments, origin="external",
        )
    assert len(store.messages(session_id)) == 1


def test_managed_replay_repairs_missing_original_message(tmp_path: Path, monkeypatch) -> None:
    store = ChatSessionStore(tmp_path)
    session_id = store.create_session(
        goal_id="goal-one", agent_id="codex", executor_endpoint_id="codex",
        adapter_kind="codex_app_server", upstream_thread_id="thread-one",
        upstream_mode="chat",
    )["session_id"]
    # Model an interrupted creation after Turn persistence but before transcript append.
    def interrupted_append(*args, **kwargs):
        raise OSError("interrupted transcript write")

    monkeypatch.setattr(store, "append_message", interrupted_append)
    with pytest.raises(OSError, match="interrupted transcript"):
        store.create_turn(session_id, client_turn_id="request", message="inspect")
    replay, created = ChatSessionStore(tmp_path).create_turn(
        session_id,
        client_turn_id="request",
        message="inspect",
    )

    assert created is False
    assert replay["status"] == "queued"
    assert "_acceptance" not in replay


def test_legacy_turn_without_original_message_still_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def interrupted_append(*args, **kwargs):
        raise OSError("interrupted transcript write")

    monkeypatch.setattr(store, "append_message", interrupted_append)
    with pytest.raises(OSError, match="interrupted transcript"):
        store.create_turn(
            session_id,
            client_turn_id="legacy-request",
            message="inspect",
        )
    interrupted = store.turn_for_client(session_id, "legacy-request")
    assert interrupted is not None
    interrupted.pop("_acceptance")
    chat_store._atomic_write_json(
        store._turn_path(session_id, str(interrupted["turn_id"])),
        interrupted,
        preserve_mode=True,
    )

    with pytest.raises(ValueError, match="original request is unavailable"):
        ChatSessionStore(tmp_path).create_turn(
            session_id,
            client_turn_id="legacy-request",
            message="inspect",
        )


@pytest.mark.parametrize(
    "boundary",
    [
        "prepare_turn",
        "activate_session",
        "append_message",
        "append_queued_event",
        "settle_turn",
    ],
)
def test_managed_acceptance_repairs_every_durable_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
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
    failed = False

    if boundary in {"prepare_turn", "activate_session", "settle_turn"}:
        original_atomic_write = chat_store._atomic_write_json

        def fail_after_atomic_write(
            path: Path,
            payload: dict[str, object],
            *,
            preserve_mode: bool = False,
        ) -> None:
            nonlocal failed
            original_atomic_write(
                path,
                payload,
                preserve_mode=preserve_mode,
            )
            is_turn = path.parent.name == "turns"
            matches = {
                "prepare_turn": is_turn and "_acceptance" in payload,
                "activate_session": (
                    path.name == "session.json"
                    and payload.get("active_turn_id") is not None
                ),
                "settle_turn": (
                    is_turn
                    and preserve_mode
                    and payload.get("client_turn_id") == "fault-request"
                    and "_acceptance" not in payload
                ),
            }[boundary]
            if matches and not failed:
                failed = True
                raise OSError(f"interrupted after {boundary}")

        monkeypatch.setattr(
            chat_store,
            "_atomic_write_json",
            fail_after_atomic_write,
        )
    elif boundary == "append_message":
        original_append_message = store.append_message

        def fail_after_message(*args, **kwargs):
            nonlocal failed
            result = original_append_message(*args, **kwargs)
            if not failed:
                failed = True
                raise OSError("interrupted after append_message")
            return result

        monkeypatch.setattr(store, "append_message", fail_after_message)
    else:
        original_append_event = store.append_event

        def fail_after_event(*args, **kwargs):
            nonlocal failed
            result = original_append_event(*args, **kwargs)
            if kwargs.get("kind") == "turn.queued" and not failed:
                failed = True
                raise OSError("interrupted after append_queued_event")
            return result

        monkeypatch.setattr(store, "append_event", fail_after_event)

    with pytest.raises(OSError, match=f"interrupted after {boundary}"):
        store.create_turn(
            session_id,
            client_turn_id="fault-request",
            message="recover this request",
            attachments=[{"id": "image-one", "mime_type": "image/png"}],
        )
    assert failed

    restarted = ChatSessionStore(tmp_path)
    partial = restarted.session_snapshot(session_id)
    if partial["active_turn"] is not None:
        assert "_acceptance" not in partial["active_turn"]
    replay, created = restarted.create_turn(
        session_id,
        client_turn_id="fault-request",
        message="recover this request",
        attachments=[{"id": "image-one", "mime_type": "image/png"}],
    )

    assert created is False
    assert replay["status"] == "queued"
    assert "_acceptance" not in replay
    assert restarted.load_session(session_id)["active_turn_id"] == replay["turn_id"]  # type: ignore[index]
    assert [
        message["text"]
        for message in restarted.messages(session_id)
        if message["role"] == "user"
    ] == ["recover this request"]
    assert [
        event["kind"]
        for event in restarted.events_after(
            session_id,
            str(replay["turn_id"]),
            None,
        )
    ] == ["turn.queued"]


@pytest.mark.parametrize("interrupted_log", ["message", "event"])
def test_managed_acceptance_repairs_an_incomplete_jsonl_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupted_log: str,
) -> None:
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
    original_append = chat_store._append_jsonl_rows
    failed = False

    def append_with_interruption(
        path: Path,
        rows: list[dict[str, object]],
    ) -> None:
        nonlocal failed
        selected = (
            path.name == "messages.jsonl"
            if interrupted_log == "message"
            else path.name.endswith(".events.jsonl")
        )
        if selected and not failed:
            failed = True
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(b'{"text":"\xe4')
                handle.flush()
            raise OSError(f"interrupted {interrupted_log} append")
        original_append(path, rows)

    monkeypatch.setattr(
        chat_store,
        "_append_jsonl_rows",
        append_with_interruption,
    )
    with pytest.raises(
        OSError,
        match=f"interrupted {interrupted_log} append",
    ):
        store.create_turn(
            session_id,
            client_turn_id="partial-jsonl",
            message="repair the incomplete tail",
        )

    restarted = ChatSessionStore(tmp_path)
    replay, created = restarted.create_turn(
        session_id,
        client_turn_id="partial-jsonl",
        message="repair the incomplete tail",
    )

    assert created is False
    assert "_acceptance" not in replay
    assert [
        message["text"]
        for message in restarted.messages(session_id)
        if message["role"] == "user"
    ] == ["repair the incomplete tail"]
    assert [
        event["kind"]
        for event in restarted.events_after(
            session_id,
            str(replay["turn_id"]),
            None,
        )
    ] == ["turn.queued"]


def test_jsonl_append_preserves_a_valid_final_record_without_newline(
    tmp_path: Path,
) -> None:
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
    store.append_message(session_id, role="user", text="first")
    path = store._session_dir(session_id) / "messages.jsonl"
    path.write_bytes(path.read_bytes().removesuffix(b"\n"))

    store.append_message(session_id, role="agent", text="second")

    assert [
        message["text"]
        for message in store.messages(session_id)
    ] == ["first", "second"]


def test_same_process_queued_replays_register_one_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    runtime.adapters[session_id] = _HealthyChatAdapter()  # type: ignore[assignment]
    started = threading.Event()
    release = threading.Event()
    starts = 0

    def blocked_run_turn(**kwargs: object) -> None:
        nonlocal starts
        starts += 1
        started.set()
        release.wait(timeout=2)
        done_event = kwargs["done_event"]
        assert isinstance(done_event, threading.Event)
        with runtime.lock:
            runtime.turn_done_events.pop(
                (session_id, str(kwargs["turn_id"])),
                None,
            )
        done_event.set()

    monkeypatch.setattr(runtime, "_run_turn", blocked_run_turn)
    first, first_created = runtime.submit_turn(
        session_id=session_id,
        client_turn_id="one-local-worker",
        message="run once",
        work_dir=tmp_path,
        objective="sample objective",
    )
    assert first_created is True
    assert started.wait(timeout=2)

    replay, replay_created = runtime.submit_turn(
        session_id=session_id,
        client_turn_id="one-local-worker",
        message="run once",
        work_dir=tmp_path,
        objective="sample objective",
    )

    assert replay_created is False
    assert replay["turn_id"] == first["turn_id"]
    assert starts == 1
    with runtime.lock:
        assert (session_id, str(first["turn_id"])) in runtime.turn_done_events
    release.set()


def test_concurrent_managed_retries_dispatch_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    accepted, created = store.create_turn(
        session_id,
        client_turn_id="response-lost",
        message="continue exactly once",
    )
    assert created is True

    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _BlockingChatAdapter()
    start_calls = 0
    start_lock = threading.Lock()
    original_start_turn = adapter.start_turn

    def counted_start_turn(message: str, event_sink):
        nonlocal start_calls
        with start_lock:
            start_calls += 1
        return original_start_turn(message, event_sink)

    monkeypatch.setattr(adapter, "start_turn", counted_start_turn)
    runtime.adapters[session_id] = adapter  # type: ignore[assignment]
    results: list[tuple[dict[str, object], bool]] = []

    def retry() -> None:
        results.append(
            runtime.submit_turn(
                session_id=session_id,
                client_turn_id="response-lost",
                message="continue exactly once",
                work_dir=tmp_path,
                objective="sample objective",
            )
        )

    retries = [threading.Thread(target=retry) for _ in range(2)]
    for thread in retries:
        thread.start()
    for thread in retries:
        thread.join(timeout=2)

    assert not any(thread.is_alive() for thread in retries)
    assert len(results) == 2
    assert all(created is False for _turn, created in results)
    assert {turn["turn_id"] for turn, _created in results} == {
        accepted["turn_id"]
    }
    assert adapter.started.wait(timeout=2)
    assert start_calls == 1

    adapter.release.set()
    assert runtime.wait_for_turn(
        session_id=session_id,
        turn_id=str(accepted["turn_id"]),
        timeout_sec=2,
    )["status"] == "completed"


def test_adapter_start_failure_keeps_accepted_turn_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _BlockingChatAdapter()
    attempts = 0

    def start_adapter(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("adapter startup interrupted")
        return adapter

    monkeypatch.setattr(runtime, "_start_adapter", start_adapter)
    with pytest.raises(CodexChatAgentError, match="could not be restored"):
        runtime.submit_turn(
            session_id=session_id,
            client_turn_id="adapter-retry",
            message="survive adapter startup",
            work_dir=tmp_path,
            objective="sample objective",
        )

    persisted = store.turn_for_client(session_id, "adapter-retry")
    assert persisted is not None
    assert persisted["status"] == "queued"
    assert "_acceptance" not in persisted
    assert store.load_session(session_id)["active_turn_id"] == persisted["turn_id"]  # type: ignore[index]

    replay, created = runtime.submit_turn(
        session_id=session_id,
        client_turn_id="adapter-retry",
        message="survive adapter startup",
        work_dir=tmp_path,
        objective="sample objective",
    )

    assert created is False
    assert replay["turn_id"] == persisted["turn_id"]
    assert adapter.started.wait(timeout=2)
    adapter.release.set()
    assert runtime.wait_for_turn(
        session_id=session_id,
        turn_id=str(replay["turn_id"]),
        timeout_sec=2,
    )["status"] == "completed"


def test_resume_repairs_and_dispatches_a_prepared_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    original_append_message = store.append_message

    def interrupted_append(*args, **kwargs):
        raise OSError("interrupted before transcript")

    monkeypatch.setattr(store, "append_message", interrupted_append)
    with pytest.raises(OSError, match="interrupted before transcript"):
        store.create_turn(
            session_id,
            client_turn_id="resume-prepared",
            message="resume the accepted request",
        )
    monkeypatch.setattr(store, "append_message", original_append_message)

    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _BlockingChatAdapter()
    monkeypatch.setattr(runtime, "_start_adapter", lambda **_kwargs: adapter)

    restored = runtime.resume_session(
        session_id=session_id,
        work_dir=tmp_path,
        objective="sample objective",
    )

    prepared = store.turn_for_client(session_id, "resume-prepared")
    assert prepared is not None
    assert "_acceptance" not in prepared
    assert restored["status"] == "busy"
    assert restored["active_turn_id"] == prepared["turn_id"]
    assert adapter.started.wait(timeout=2)
    adapter.release.set()
    assert runtime.wait_for_turn(
        session_id=session_id,
        turn_id=str(prepared["turn_id"]),
        timeout_sec=2,
    )["status"] == "completed"


def test_replay_after_process_loss_fails_an_unowned_started_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    turn, _created = store.create_turn(
        session_id,
        client_turn_id="lost-running-worker",
        message="do not leave this running",
    )
    store.update_turn(
        session_id,
        str(turn["turn_id"]),
        expected_statuses={"queued"},
        status="starting",
    )
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    monkeypatch.setattr(
        runtime,
        "_start_adapter",
        lambda **_kwargs: _HealthyChatAdapter(),
    )

    replay, created = runtime.submit_turn(
        session_id=session_id,
        client_turn_id="lost-running-worker",
        message="do not leave this running",
        work_dir=tmp_path,
        objective="sample objective",
    )

    assert created is False
    assert replay["status"] == "failed"
    assert replay["error_code"] == "server_restarted"
    restored = store.load_session(session_id)
    assert restored is not None
    assert restored["status"] == "ready"
    assert restored["active_turn_id"] is None


def test_completed_turn_cannot_release_a_newer_active_turn(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    first, _ = store.create_turn(
        session_id,
        client_turn_id="first-turn",
        message="first",
    )
    store.update_turn(
        session_id,
        str(first["turn_id"]),
        status="completed",
        completed_at="2026-08-23T00:00:00Z",
    )
    second, _ = store.create_turn(
        session_id,
        client_turn_id="second-turn",
        message="second",
    )

    released = store.release_active_turn(
        session_id,
        str(first["turn_id"]),
        last_activity_at="2026-08-23T00:00:01Z",
        last_error_code=None,
    )

    assert released is False
    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "busy"
    assert current["active_turn_id"] == second["turn_id"]

    assert store.release_active_turn(
        session_id,
        str(second["turn_id"]),
        last_activity_at="2026-08-23T00:00:02Z",
        last_error_code=None,
    )
    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "ready"
    assert current["active_turn_id"] is None


def test_concurrent_queued_turn_creation_is_atomic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    for index in range(SESSION_QUEUE_MAX_PENDING - 1):
        queued, created = store.create_queued_turn(
            session_id,
            client_turn_id=f"prefill-{index}",
            message=f"prefill message {index}",
        )
        assert created
        assert queued["status"] == "queued"
    turn_write_threads = _slow_new_turn_writes(monkeypatch)
    results, errors = _run_two_client_turn_creators(
        lambda client_turn_id: store.create_queued_turn(
            session_id,
            client_turn_id=client_turn_id,
            message=f"message from {client_turn_id}",
        )
    )

    assert len(results) == 1
    assert len(errors) == 1
    assert errors == ["session_queue_full"]
    assert len(turn_write_threads) == 1
    assert [
        item["text"] for item in store.messages(session_id) if item["role"] == "user"
    ][-2:] == [f"prefill message {SESSION_QUEUE_MAX_PENDING - 2}", f"message from {results[0][0]}"]
    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "ready"
    assert current["active_turn_id"] is None


def test_concurrent_queued_turn_creation_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    turn_write_threads = _slow_new_turn_writes(monkeypatch)
    results: list[tuple[str, str, bool]] = []

    def create() -> None:
        turn, created = store.create_queued_turn(
            session_id,
            client_turn_id="shared-client",
            message="same message",
        )
        results.append((str(turn["turn_id"]), created))

    threads = [
        threading.Thread(target=create, name=f"creator-{index}")
        for index in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not any(thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert {created for _turn_id, created in results} == {True, False}
    assert len({turn_id for turn_id, _created in results}) == 1
    assert len(turn_write_threads) == 1
    assert [
        item["text"] for item in store.messages(session_id) if item["role"] == "user"
    ] == ["same message"]
    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "ready"
    assert current["active_turn_id"] is None


def test_enqueue_wakes_worker_after_empty_queue_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _BlockingChatAdapter()
    adapter.release.set()
    runtime.adapters[session_id] = adapter  # type: ignore[assignment]
    empty_queue_observed = threading.Event()
    release_empty_observation = threading.Event()
    original_claim = store.claim_next_queued_turn
    claim_count = 0

    def pause_after_first_empty_claim(
        requested_session_id: str,
        *,
        host_claim_id: str | None = None,
    ) -> dict[str, object] | None:
        nonlocal claim_count
        turn = original_claim(
            requested_session_id,
            host_claim_id=host_claim_id,
        )
        claim_count += 1
        if claim_count == 1:
            assert turn is None
            empty_queue_observed.set()
            assert release_empty_observation.wait(timeout=2)
        return turn

    monkeypatch.setattr(store, "claim_next_queued_turn", pause_after_first_empty_claim)
    runtime.resume_session_queue(
        session_id=session_id,
        work_dir=tmp_path,
        objective="keep queued work moving",
    )
    worker = runtime.session_queue_threads[session_id]
    assert empty_queue_observed.wait(timeout=2)

    queued, created = runtime.enqueue_turn(
        session_id=session_id,
        client_turn_id="enqueue-during-worker-retirement",
        message="process after the empty observation",
        work_dir=tmp_path,
        objective="keep queued work moving",
    )
    assert created is True
    release_empty_observation.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    completed = store.load_turn(session_id, str(queued["turn_id"]))
    assert completed is not None
    assert completed["status"] == "completed"
    assert claim_count >= 2
    assert session_id not in runtime.session_queue_workers
    assert session_id not in runtime.session_queue_threads
    assert session_id not in runtime.session_queue_wakeups


def test_queued_turn_rejects_closed_session(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    store.update_session(session_id, status="closed", active_turn_id=None)

    try:
        store.create_queued_turn(
            session_id,
            client_turn_id="closed-session",
            message="should not queue",
        )
    except KeyError as exc:
        assert str(exc) == "'chat session was not found'"
    else:  # pragma: no cover - safety net for unexpected passes.
        raise AssertionError("closed session should not accept queued turns")


def test_managed_close_rejects_active_turn_before_closing_adapter(
    tmp_path: Path,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _BlockingChatAdapter()
    runtime.adapters[session_id] = adapter  # type: ignore[assignment]
    turn, created = runtime.submit_turn(
        session_id=session_id,
        client_turn_id="managed-close-active",
        message="keep running",
        work_dir=tmp_path,
        objective="sample objective",
    )

    assert created is True
    assert adapter.started.wait(timeout=2)
    with pytest.raises(RuntimeError, match="managed_session_turn_active"):
        runtime.close_session(session_id)

    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "busy"
    assert current["active_turn_id"] == turn["turn_id"]
    assert adapter.closed is False

    adapter.release.set()
    assert runtime.wait_for_turn(
        session_id=session_id,
        turn_id=str(turn["turn_id"]),
        timeout_sec=2,
    )["status"] == "completed"
    assert runtime.close_session(session_id) is True


def test_interrupt_does_not_overwrite_a_completed_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _BlockingChatAdapter()
    runtime.adapters[session_id] = adapter  # type: ignore[assignment]
    turn, created = runtime.submit_turn(
        session_id=session_id,
        client_turn_id="interrupt-completion-race",
        message="finish before interruption commits",
        work_dir=tmp_path,
        objective="sample objective",
    )
    turn_id = str(turn["turn_id"])
    assert created is True
    assert adapter.started.wait(timeout=2)

    original_load_turn = store.load_turn
    interrupt_read = threading.Event()
    resume_interrupt = threading.Event()

    def pause_after_interrupt_read(
        requested_session_id: str,
        requested_turn_id: str,
    ) -> dict[str, object] | None:
        loaded = original_load_turn(requested_session_id, requested_turn_id)
        if threading.current_thread().name == "interrupt-turn":
            interrupt_read.set()
            resume_interrupt.wait(timeout=2)
        return loaded

    monkeypatch.setattr(store, "load_turn", pause_after_interrupt_read)
    interrupted: list[dict[str, object]] = []
    interrupt_thread = threading.Thread(
        target=lambda: interrupted.append(
            runtime.interrupt_turn(session_id=session_id, turn_id=turn_id)
        ),
        name="interrupt-turn",
        daemon=True,
    )
    interrupt_thread.start()
    assert interrupt_read.wait(timeout=2)

    adapter.release.set()
    assert runtime.wait_for_turn(
        session_id=session_id,
        turn_id=turn_id,
        timeout_sec=2,
    )["status"] == "completed"
    resume_interrupt.set()
    interrupt_thread.join(timeout=2)

    assert not interrupt_thread.is_alive()
    assert interrupted[0]["status"] == "completed"
    completed = store.load_turn(session_id, turn_id)
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["response"] == {"message": "late response"}
    assert [
        event["kind"] for event in store.events_after(session_id, turn_id, None)
        if event["kind"].startswith("turn.")
    ] == ["turn.queued", "turn.completed"]
    assert [
        message["text"]
        for message in store.messages(session_id)
        if message["role"] == "agent"
    ] == ["late response"]
    assert (session_id, turn_id) not in runtime.cancelled_turns


def test_interrupting_a_queued_turn_does_not_touch_the_active_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _BlockingChatAdapter()
    interrupt_calls: list[str | None] = []
    monkeypatch.setattr(adapter, "interrupt_turn", interrupt_calls.append)
    runtime.adapters[session_id] = adapter  # type: ignore[assignment]
    active, created = runtime.submit_turn(
        session_id=session_id,
        client_turn_id="active-turn",
        message="keep running",
        work_dir=tmp_path,
        objective="sample objective",
    )
    assert created is True
    assert adapter.started.wait(timeout=2)
    queued, created = store.create_queued_turn(
        session_id,
        client_turn_id="queued-turn",
        message="cancel only this queued turn",
    )
    assert created is True

    interrupted = runtime.interrupt_turn(
        session_id=session_id,
        turn_id=str(queued["turn_id"]),
    )

    assert interrupted["status"] == "interrupted"
    assert interrupt_calls == []
    current = store.load_session(session_id)
    assert current is not None
    assert current["active_turn_id"] == active["turn_id"]
    assert store.load_turn(session_id, str(active["turn_id"]))["status"] in {  # type: ignore[index]
        "starting",
        "running",
    }
    adapter.release.set()
    assert runtime.wait_for_turn(
        session_id=session_id,
        turn_id=str(active["turn_id"]),
        timeout_sec=2,
    )["status"] == "completed"


def test_completed_turn_is_visible_only_after_its_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _BlockingChatAdapter()
    runtime.adapters[session_id] = adapter  # type: ignore[assignment]
    turn, created = runtime.submit_turn(
        session_id=session_id,
        client_turn_id="completion-publication-order",
        message="publish only after closeout",
        work_dir=tmp_path,
        objective="sample objective",
    )
    turn_id = str(turn["turn_id"])
    assert created is True
    assert adapter.started.wait(timeout=2)

    original_append_message = store.append_message
    completion_closeout_started = threading.Event()
    resume_completion = threading.Event()

    def pause_agent_message(*args: object, **kwargs: object) -> dict[str, object]:
        if kwargs.get("role") == "agent" and kwargs.get("turn_id") == turn_id:
            completion_closeout_started.set()
            resume_completion.wait(timeout=2)
        return original_append_message(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "append_message", pause_agent_message)
    adapter.release.set()
    assert completion_closeout_started.wait(timeout=2)

    visible = store.load_turn(session_id, turn_id)
    assert visible is not None
    assert visible["status"] == "completing"
    with pytest.raises(TimeoutError):
        runtime.wait_for_turn(session_id=session_id, turn_id=turn_id, timeout_sec=0.05)
    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "busy"
    assert current["active_turn_id"] == turn_id
    # Evidence-read events can precede completion; terminal publication cannot.
    assert [event["kind"] for event in store.events_after(session_id, turn_id, None)
            if event["kind"].startswith("turn.")] == ["turn.queued"]

    resume_completion.set()
    completed = runtime.wait_for_turn(
        session_id=session_id,
        turn_id=turn_id,
        timeout_sec=2,
    )
    assert completed["status"] == "completed"
    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "ready"
    assert current["active_turn_id"] is None
    assert [
        message["text"]
        for message in store.messages(session_id)
        if message["role"] == "agent"
    ] == ["late response"]
    assert [
        event["kind"] for event in store.events_after(session_id, turn_id, None)
        if event["kind"].startswith("turn.")
    ] == ["turn.queued", "turn.completed"]


def test_completing_turn_replays_closeout_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    turn, created = store.create_turn(
        session_id,
        client_turn_id="restart-completion-closeout",
        message="recover this completion",
    )
    turn_id = str(turn["turn_id"])
    assert created is True
    store.update_turn(session_id, turn_id, status="starting")
    store.update_turn(
        session_id,
        turn_id,
        expected_statuses={"starting"},
        status="completing",
        response={"message": "durable response"},
        completed_at="2026-09-05T00:00:00Z",
        last_activity_at="2026-09-05T00:00:00Z",
    )
    monkeypatch.setattr(
        store,
        "release_active_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crash")),
    )

    with pytest.raises(RuntimeError, match="crash"):
        store.finalize_managed_turn_completion(session_id, turn_id)
    assert store.load_turn(session_id, turn_id)["status"] == "completing"  # type: ignore[index]

    restarted = ChatSessionStore(tmp_path)

    completed = restarted.load_turn(session_id, turn_id)
    assert completed is not None
    assert completed["status"] == "completed"
    assert [
        message["text"]
        for message in restarted.messages(session_id)
        if message["role"] == "agent"
    ] == ["durable response"]
    assert [
        event["kind"] for event in restarted.events_after(session_id, turn_id, None)
    ] == ["turn.queued", "turn.completed"]
    current = restarted.load_session(session_id)
    assert current is not None
    assert current["status"] == "ready"
    assert current["active_turn_id"] is None


def test_resume_with_healthy_adapter_restores_persisted_ready_state(
    tmp_path: Path,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _HealthyChatAdapter()
    runtime.adapters[session_id] = adapter  # type: ignore[assignment]

    restored = runtime.resume_session(
        session_id=session_id,
        work_dir=tmp_path,
        objective="resume this session",
    )

    assert restored["status"] == "ready"
    persisted = store.load_session(session_id)
    assert persisted is not None
    assert persisted["status"] == "ready"
    assert persisted["active_turn_id"] is None


def test_legacy_codex_resume_persists_chat_mode_after_one_time_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="legacy-thread",
        upstream_mode="default",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _HealthyChatAdapter()
    start_calls: list[dict[str, object]] = []

    def start_adapter(**kwargs: object) -> _HealthyChatAdapter:
        start_calls.append(kwargs)
        return adapter

    monkeypatch.setattr(runtime, "_start_adapter", start_adapter)

    restored = runtime.resume_session(
        session_id=session_id,
        work_dir=tmp_path,
        objective="resume this session",
    )

    assert restored["status"] == "ready"
    persisted = store.load_session(session_id)
    assert persisted is not None
    assert persisted["upstream_thread_id"] == "thread-one"
    assert persisted["upstream_mode"] == "chat"
    assert start_calls[0]["resume_thread_id"] is None

    runtime.resume_session(
        session_id=session_id,
        work_dir=tmp_path,
        objective="resume this session",
    )
    assert len(start_calls) == 1


def test_legacy_codex_resume_commits_identity_when_new_turn_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="legacy-thread",
        upstream_mode="default",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _HealthyChatAdapter()
    start_calls: list[dict[str, object]] = []
    winning_turn: dict[str, object] = {}

    def start_adapter(**kwargs: object) -> _HealthyChatAdapter:
        start_calls.append(kwargs)
        turn, _created = store.create_turn(
            session_id,
            client_turn_id="identity-race-turn",
            message="new turn wins before restore",
        )
        winning_turn.update(turn)
        store.update_turn(session_id, str(turn["turn_id"]), status="running")
        return adapter

    monkeypatch.setattr(runtime, "_start_adapter", start_adapter)

    restored = runtime.resume_session(
        session_id=session_id,
        work_dir=tmp_path,
        objective="resume this session",
    )

    assert restored["status"] == "busy"
    assert restored["active_turn_id"] == winning_turn["turn_id"]
    persisted = store.load_session(session_id)
    assert persisted is not None
    assert persisted["upstream_thread_id"] == "thread-one"
    assert persisted["upstream_mode"] == "chat"
    assert start_calls[0]["resume_thread_id"] is None

    store.update_turn(
        session_id,
        str(winning_turn["turn_id"]),
        status="completed",
        completed_at="2026-09-01T00:00:00Z",
    )
    assert store.release_active_turn(
        session_id,
        str(winning_turn["turn_id"]),
        last_activity_at="2026-09-01T00:00:01Z",
        last_error_code=None,
    )
    runtime.adapters.pop(session_id)
    runtime.resume_session(
        session_id=session_id,
        work_dir=tmp_path,
        objective="resume this session",
    )

    assert start_calls[1]["resume_thread_id"] == "thread-one"
    assert start_calls[1]["history"] is None


def test_resume_does_not_clear_a_healthy_active_turn(
    tmp_path: Path,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    turn, _created = store.create_turn(
        session_id,
        client_turn_id="active-resume-turn",
        message="keep this active",
    )
    store.update_turn(session_id, str(turn["turn_id"]), status="running")
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    runtime.adapters[session_id] = _HealthyChatAdapter()  # type: ignore[assignment]

    restored = runtime.resume_session(
        session_id=session_id,
        work_dir=tmp_path,
        objective="resume this session",
    )

    assert restored["status"] == "busy"
    assert restored["active_turn_id"] == turn["turn_id"]
    with pytest.raises(RuntimeError, match=str(turn["turn_id"])):
        runtime.submit_turn(
            session_id=session_id,
            client_turn_id="blocked-during-active-resume",
            message="must wait",
            work_dir=tmp_path,
            objective="resume this session",
        )


def test_resume_with_unhealthy_adapter_fails_interrupted_turn_before_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    turn, _created = store.create_turn(
        session_id,
        client_turn_id="interrupted-resume-turn",
        message="fail this interrupted turn",
    )
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    unhealthy_adapter = _HealthyChatAdapter()
    monkeypatch.setattr(unhealthy_adapter, "healthcheck", lambda: False)
    runtime.adapters[session_id] = unhealthy_adapter  # type: ignore[assignment]
    replacement_adapter = _HealthyChatAdapter()
    monkeypatch.setattr(runtime, "_start_adapter", lambda **_kwargs: replacement_adapter)

    restored = runtime.resume_session(
        session_id=session_id,
        work_dir=tmp_path,
        objective="resume this session",
    )

    interrupted = store.load_turn(session_id, str(turn["turn_id"]))
    assert interrupted is not None
    assert interrupted["status"] == "failed"
    assert interrupted["error_code"] == "server_restarted"
    assert restored["status"] == "ready"
    assert restored["active_turn_id"] is None
    assert unhealthy_adapter.closed is True


def test_resume_cannot_reopen_a_session_closed_during_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _HealthyChatAdapter()

    def close_before_restore(*_args: object, **_kwargs: object) -> object:
        store.update_session(session_id, status="closed", active_turn_id=None)
        runtime.adapters[session_id] = adapter  # type: ignore[assignment]
        return adapter

    monkeypatch.setattr(runtime, "_ensure_adapter_locked", close_before_restore)

    with pytest.raises(KeyError, match="chat session was not found"):
        runtime.resume_session(
            session_id=session_id,
            work_dir=tmp_path,
            objective="resume this session",
        )

    persisted = store.load_session(session_id)
    assert persisted is not None
    assert persisted["status"] == "closed"
    assert persisted["active_turn_id"] is None
    assert session_id not in runtime.adapters
    assert adapter.closed is True


def test_close_wins_during_adapter_start_without_reopening_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _HealthyChatAdapter()
    start_entered = threading.Event()
    release_start = threading.Event()

    def start_adapter(*_args: object, **_kwargs: object) -> _HealthyChatAdapter:
        start_entered.set()
        release_start.wait(timeout=2)
        return adapter

    monkeypatch.setattr(runtime, "_start_adapter", start_adapter)
    resume_error: list[BaseException] = []

    def resume() -> None:
        try:
            runtime.resume_session(
                session_id=session_id,
                work_dir=tmp_path,
                objective="resume this session",
            )
        except BaseException as exc:  # pragma: no cover - assertion captures it.
            resume_error.append(exc)

    worker = threading.Thread(target=resume)
    worker.start()
    assert start_entered.wait(timeout=2)

    close_result: list[bool] = []
    close_worker = threading.Thread(
        target=lambda: close_result.append(runtime.close_session(session_id))
    )
    close_worker.start()
    time.sleep(0.1)
    assert close_result == []
    release_start.set()
    worker.join(timeout=2)
    close_worker.join(timeout=2)

    assert not close_worker.is_alive()
    assert resume_error == []
    assert close_result == [True]
    persisted = store.load_session(session_id)
    assert persisted is not None
    assert persisted["status"] == "closed"
    assert session_id not in runtime.adapters
    assert adapter.closed is True


def test_concurrent_resume_starts_one_managed_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    first_start = threading.Event()
    allow_first_start = threading.Event()
    start_calls: list[dict[str, object]] = []

    def start_adapter(**kwargs: object) -> _HealthyChatAdapter:
        start_calls.append(kwargs)
        if len(start_calls) == 1:
            first_start.set()
            assert allow_first_start.wait(timeout=2)
        return _HealthyChatAdapter()

    monkeypatch.setattr(runtime, "_start_adapter", start_adapter)
    errors: list[Exception] = []

    def resume() -> None:
        try:
            runtime.resume_session(
                session_id=session_id,
                work_dir=tmp_path,
                objective="resume this session",
            )
        except Exception as exc:  # pragma: no cover - surfaced by the assertion below.
            errors.append(exc)

    first = threading.Thread(target=resume)
    second = threading.Thread(target=resume)
    first.start()
    assert first_start.wait(timeout=2)
    second.start()
    time.sleep(0.1)
    assert len(start_calls) == 1
    allow_first_start.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(start_calls) == 1
    assert runtime.adapters[session_id].upstream_thread_id == "thread-one"


def test_restore_ready_does_not_clear_a_new_active_turn(
    tmp_path: Path,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    store.update_session(session_id, status="stale")
    turn, _created = store.create_turn(
        session_id,
        client_turn_id="resume-race-turn",
        message="new turn wins",
    )

    restored = store.restore_managed_session_if_idle(
        session_id,
        upstream_thread_id="thread-one",
    )

    assert restored["status"] == "busy"
    assert restored["active_turn_id"] == turn["turn_id"]


def test_managed_close_rejects_pending_queue_without_stranding_it(
    tmp_path: Path,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    queued, created = store.create_queued_turn(
        session_id,
        client_turn_id="managed-close-queued",
        message="do not strand me",
    )

    assert created is True
    with pytest.raises(RuntimeError, match="managed_session_queue_pending"):
        runtime.close_session(session_id)

    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "ready"
    assert current["active_turn_id"] is None
    assert store.load_turn(session_id, str(queued["turn_id"]))["status"] == "queued"  # type: ignore[index]


def test_failed_old_turn_does_not_remove_replacement_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    first_turn, _created = store.create_turn(
        session_id,
        client_turn_id="failed-old-turn",
        message="fail on the old adapter",
    )
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    old_adapter = _FailingChatAdapter()
    replacement_adapter = _HealthyChatAdapter()
    runtime.adapters[session_id] = old_adapter
    original_fail_turn = runtime._fail_turn
    replacement_turn: dict[str, object] = {}

    def fail_then_replace(*args: object, **kwargs: object) -> None:
        original_fail_turn(*args, **kwargs)  # type: ignore[arg-type]
        with runtime.lock:
            runtime.adapters[session_id] = replacement_adapter  # type: ignore[assignment]
        replacement_turn.update(
            store.create_turn(
                session_id,
                client_turn_id="replacement-turn",
                message="continue on the replacement adapter",
            )[0]
        )

    monkeypatch.setattr(runtime, "_fail_turn", fail_then_replace)

    runtime._run_turn(
        session_id=session_id,
        turn_id=str(first_turn["turn_id"]),
        message="fail on the old adapter",
        attachments=[],
        adapter=old_adapter,
    )

    current = store.load_session(session_id)
    assert runtime.adapters[session_id] is replacement_adapter
    assert current is not None and current["status"] == "busy"
    assert current["active_turn_id"] == replacement_turn["turn_id"]


@pytest.mark.parametrize("terminal_status", sorted(TERMINAL_TURN_STATES))
def test_managed_close_clears_terminal_active_turn_left_by_crash(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        executor_endpoint_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
        upstream_mode="chat",
    )
    session_id = str(session["session_id"])
    turn, created = store.create_turn(
        session_id,
        client_turn_id=f"managed-close-{terminal_status}",
        message="terminal state persisted before owner release",
    )
    assert created is True
    store.update_turn(session_id, str(turn["turn_id"]), status=terminal_status)
    runtime = ChatRuntimeController(store=store, codex_bin="missing-codex")
    adapter = _BlockingChatAdapter()
    runtime.adapters[session_id] = adapter  # type: ignore[assignment]

    assert runtime.close_session(session_id) is True

    current = store.load_session(session_id)
    assert current is not None
    assert current["status"] == "closed"
    assert current["active_turn_id"] is None
    assert adapter.closed is True
