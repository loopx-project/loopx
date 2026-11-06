"""File-backed parity at the real queue/control readers, including invalidation."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.chat_store import ChatSessionStore, _atomic_write_json
from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.capabilities.native_chat.file_discovery import FileChatDiscovery


@pytest.fixture
def original_store(tmp_path):
    store = ChatSessionStore(tmp_path, startup_maintenance=False)
    session = store.create_session(goal_id="goal-one", agent_id="codex",
        executor_endpoint_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="thread-one", upstream_mode="chat")
    return store, session["session_id"]


def _turn(store, sid, client, status):
    turn, _ = store.create_turn(sid, client_turn_id=client, message="Original input")
    store.update_turn(sid, turn["turn_id"], status=status)
    store.update_session(sid, active_turn_id=None)
    return turn["turn_id"]


def test_terminal_history_stays_out_of_warm_queue_body_reads(original_store, monkeypatch):
    store, sid = original_store
    terminal = [_turn(store, sid, f"history-{i}", "completed") for i in range(12)]
    pending = _turn(store, sid, "pending", "queued")
    assert [t["turn_id"] for t in store.queued_turns(sid)] == [pending]
    reads = []
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path.parent.name == "turns":
            reads.append(path.stem)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert [t["turn_id"] for t in store.queued_turns(sid, strict=True)] == [pending]
    assert not set(terminal) & set(reads)
    assert pending in reads
    # Exact reads retain complete fresh records, independent of the hint.
    assert store.load_turn(sid, terminal[0])["message"] == "Original input"


@pytest.mark.parametrize("replace", [False, True])
def test_rewritten_terminal_turn_is_visible_even_with_preserved_mtime(original_store, replace):
    store, sid = original_store
    tid = _turn(store, sid, "original", "failed")
    assert store.queued_turns(sid, strict=True) == []
    path = store._turn_path(sid, tid)
    stamp = path.stat()
    value = store.load_turn(sid, tid)
    value["status"] = "queued"
    if replace:
        _atomic_write_json(path, value)
    else:
        path.write_text(json.dumps(value))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert [t["turn_id"] for t in store.queued_turns(sid, strict=True)] == [tid]


def test_new_queued_input_and_active_exclusion_stay_fresh(original_store):
    store, sid = original_store
    _turn(store, sid, "old", "completed")
    assert store.queued_turns(sid) == []
    tid = _turn(store, sid, "new", "queued")
    assert [t["turn_id"] for t in store.queued_turns(sid)] == [tid]
    store.update_session(sid, active_turn_id=tid)
    assert store.queued_turns(sid) == []


def test_concurrent_first_queue_observations_do_not_change_selection(original_store):
    store, sid = original_store
    _turn(store, sid, "history", "completed")
    tid = _turn(store, sid, "pending", "queued")
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: store.queued_turns(sid, strict=True), range(2)))
    assert [[turn["turn_id"] for turn in result] for result in rows] == [[tid], [tid]]


@pytest.mark.parametrize("fault", ["malformed", "foreign-session", "invalid-status"])
def test_warmed_terminal_does_not_hide_strict_read_failures(original_store, fault):
    store, sid = original_store
    tid = _turn(store, sid, "old", "completed")
    assert store.queued_turns(sid, strict=True) == []
    path = store._turn_path(sid, tid)
    value = store.load_turn(sid, tid)
    if fault == "malformed":
        path.write_text("{")
    else:
        value["session_id" if fault == "foreign-session" else "status"] = "foreign"
        _atomic_write_json(path, value)
    with pytest.raises(OSError, match="Queued Turn state"):
        store.queued_turns(sid, strict=True)


def _core(tmp_path):
    controller = SimpleNamespace(store=SimpleNamespace(root=tmp_path),
        project_contexts=SimpleNamespace(conversation_bindings=SimpleNamespace()))
    core = ChatExternalConversations(controller)
    core.root.mkdir()
    return core


def test_source_controls_reuse_only_fields_and_keep_stop_and_new_changes(tmp_path, monkeypatch):
    import loopx.capabilities.native_chat.external_conversations as module

    core = _core(tmp_path)
    source = {"source_ref": "original", "sender_ref": "owner"}
    first = {"binding_id": "binding", "source": source, "command": None,
             "created_at": "2026-01-01T00:00:00+00:00", "message": "BODY" * 1000}
    path = core.root / ("a" * 24 + ".json")
    _atomic_write_json(path, first)
    expected = [{key: value for key, value in first.items() if key != "message"}]
    assert core.source_controls() == expected
    original = module._read_json
    reads = []

    def read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(module, "_read_json", read)
    view = core.source_controls()
    assert view == expected and reads == []
    view[0]["source"]["source_ref"] = "mutated"
    assert core.source_controls() == expected
    row = {"binding_id": "binding", "source": source, "created_at": first["created_at"]}
    assert not core._source_stop_observed(row, {"status": "failed"}, [])
    _atomic_write_json(path, {**first, "command": "stop"})
    assert core._source_stop_observed(row, {"status": "failed"}, [])
    assert reads == [path]
    # A new independent source is still observed; it does not stop this input.
    other = core.root / ("b" * 24 + ".json")
    _atomic_write_json(other, {**first, "source": {"source_ref": "other"}, "command": "new"})
    path.unlink()
    assert not core._source_stop_observed(row, {"status": "failed"}, [])
    assert core.source_controls() == [{key: value for key, value in original(other).items() if key != "message"}]
    assert core.pending()[0]["message"] == first["message"]


def test_changed_during_read_is_not_saved_as_an_unchanged_hint(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text('{"value":1}')
    discovery = FileChatDiscovery()
    reads = []

    def read(path):
        row = json.loads(path.read_text())
        reads.append(row)
        if len(reads) == 1:
            _atomic_write_json(path, {"value": 2})
        return row

    def observe():
        return list(discovery.rows(tmp_path, [path], read=read, project=lambda _p, row: row))

    assert observe()[0][1] == {"value": 1}
    assert observe()[0][1] == {"value": 2}
    assert observe()[0][1] == {"value": 2}
    assert len(reads) == 2


def test_unavailable_stat_uses_original_reader_instead_of_saved_view(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    path.write_text('{"value":1}')
    discovery = FileChatDiscovery()

    def observe():
        return list(discovery.rows(tmp_path, [path], read=lambda p: json.loads(p.read_text()),
                                  project=lambda _p, row: row))

    assert observe()[0][1] == {"value": 1}
    path.unlink()
    with pytest.raises(FileNotFoundError):
        observe()
