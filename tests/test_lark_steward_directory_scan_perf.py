"""Regression and directory-scan efficiency checks for steward/chat hot paths."""
from __future__ import annotations

import gc
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tracemalloc

import pytest

from loopx.capabilities.native_chat.external_conversations import ChatExternalConversations
from loopx.chat_store import _atomic_write_json, _read_json as store_read_json
from loopx.extensions.lark.owner_attention import LarkOwnerAttention
from loopx.extensions.lark.private_ingress import PrivateIngressObserver


def _hex_ref(index: int) -> str:
    return f"{index:024x}"


def _capture_record(ref: str, *, binding_id: str = "binding_one", profile: str = "steward-app",
                    chat_type: str = "p2p") -> dict[str, object]:
    return {
        "request_ref": ref,
        "binding_id": binding_id,
        "profile": profile,
        "source": {"source_ref": "source_one", "sender_ref": "ou_owner"},
        "event": {
            "message_id": f"om_{ref}",
            "chat_id": "oc_owner",
            "chat_type": chat_type,
            "sender_type": "user",
            "sender_id": "ou_owner",
        },
    }


def _native_request(ref: str, *, binding_id: str = "binding_one", status: str = "accepted") -> dict[str, object]:
    return {
        "request_ref": ref,
        "binding_id": binding_id,
        "source": {"source_ref": "source_one", "sender_ref": "ou_owner"},
        "origin": "lark",
        "status": status,
        "session_id": f"session_{ref}",
    }


def test_owner_attention_route_stops_at_newest_matching_capture(tmp_path, monkeypatch):
    import loopx.extensions.lark.owner_attention as attention_module

    transport_root = tmp_path / "lark-private-deliveries"
    core_root = tmp_path / "external-requests"
    transport_root.mkdir()
    core_root.mkdir()

    binding = {
        "binding_id": "binding_one",
        "transport_ref": "steward-app",
        "enabled": True,
        "context_kind": "steward",
        "audience": "owner",
        "grant": "portfolio_read",
    }

    for i in range(2000):
        ref = _hex_ref(i)
        (transport_root / f"{ref}.json").write_text(json.dumps(_capture_record(ref)), encoding="utf-8")
        (core_root / f"{ref}.json").write_text(json.dumps(_native_request(ref)), encoding="utf-8")

    core = SimpleNamespace(
        root=core_root,
        controller=SimpleNamespace(store=SimpleNamespace(root=tmp_path)),
        read_request=lambda ref: store_read_json(core_root / f"{ref}.json"),
    )
    bindings = SimpleNamespace(
        resolve=lambda **_kwargs: {"binding": binding, "observation": {"bot_app_id": "cli_owner"}}
    )
    transport = SimpleNamespace(
        root=transport_root,
        core=core,
        bindings=bindings,
        _source_message=lambda _record, **_kwargs: True,
    )
    attention = LarkOwnerAttention(transport)

    reads: list[Path] = []
    original_read = attention_module._read_json

    def counted_read(path: Path) -> dict[str, object]:
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(attention_module, "_read_json", counted_read)

    route = attention._route(binding)
    assert route["request_ref"] == _hex_ref(1999)
    assert len(reads) == 1
    assert len(reads) <= 5

    # Fallback skips a non-matching newest capture and a capture deleted mid-scan.
    reads.clear()
    newest = _hex_ref(1999)
    second = _hex_ref(1998)
    third = _hex_ref(1997)
    (transport_root / f"{newest}.json").write_text(
        json.dumps(_capture_record(newest, chat_type="group")),
        encoding="utf-8",
    )

    def read_with_delete(path: Path) -> dict[str, object]:
        reads.append(path)
        if path.stem == newest and (transport_root / f"{second}.json").exists():
            (transport_root / f"{second}.json").unlink()
        return original_read(path)

    monkeypatch.setattr(attention_module, "_read_json", read_with_delete)

    fallback = attention._route(binding)
    assert fallback["request_ref"] == third
    assert [path.stem for path in reads] == [newest, second, third]
    assert len(reads) <= 5


def test_private_ingress_records_cache_unchanged_captures_and_fail_closed(tmp_path, monkeypatch):
    import loopx.extensions.lark.private_ingress as ingress_module

    root = tmp_path / "lark-private-deliveries"
    root.mkdir()
    refs = [_hex_ref(1), _hex_ref(2), _hex_ref(3)]
    for ref in refs:
        (root / f"{ref}.json").write_text(json.dumps(_capture_record(ref)), encoding="utf-8")

    transport = SimpleNamespace(root=root)
    observer = PrivateIngressObserver(transport)

    read_paths: list[Path] = []
    parse_count = 0
    original_read_text = Path.read_text
    original_loads = ingress_module.json.loads

    def counted_read_text(path: Path, *args, **kwargs):
        if path.parent == root:
            read_paths.append(path)
        return original_read_text(path, *args, **kwargs)

    def counted_loads(raw, *args, **kwargs):
        nonlocal parse_count
        parse_count += 1
        return original_loads(raw, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)
    monkeypatch.setattr(ingress_module.json, "loads", counted_loads)

    first = observer._records()
    assert [row["request_ref"] for row in first] == refs
    assert len(read_paths) == 3
    assert parse_count == 3

    read_paths.clear()
    parse_count = 0
    second = observer._records()
    assert second == first
    assert read_paths == []
    assert parse_count == 0

    # Caller mutation must not corrupt the cached capture view.
    second[0]["event"]["chat_id"] = "oc_mutated"
    third = observer._records()
    assert third[0]["event"]["chat_id"] == "oc_owner"
    assert read_paths == []
    assert parse_count == 0

    # Modifying one capture file refreshes only that file.
    modified = root / f"{refs[1]}.json"
    _atomic_write_json(modified, _capture_record(refs[1], profile="updated-app"))
    updated = observer._records()
    assert updated[1]["profile"] == "updated-app"
    assert read_paths == [modified]
    assert parse_count == 1

    # Deleting one capture file evicts it without re-reading remaining files.
    read_paths.clear()
    parse_count = 0
    (root / f"{refs[0]}.json").unlink()
    remaining = observer._records()
    assert [row["request_ref"] for row in remaining] == [refs[1], refs[2]]
    assert read_paths == []
    assert parse_count == 0

    # Incomplete rows fail closed and are never cached.
    incomplete = root / f"{refs[2]}.json"
    _atomic_write_json(incomplete, {"request_ref": refs[2], "binding_id": "binding_one"})
    with pytest.raises(ValueError, match="private capture read is incomplete"):
        observer._records()

    # Corrupt JSON also fails closed without poisoning the cache.
    incomplete.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        observer._records()


def test_external_conversations_recover_caches_metadata_only_and_drops_image_bodies(tmp_path, monkeypatch):
    import loopx.capabilities.native_chat.external_conversations as external_module

    controller = SimpleNamespace(
        store=SimpleNamespace(root=tmp_path),
        project_contexts=SimpleNamespace(conversation_bindings=SimpleNamespace()),
    )
    core = ChatExternalConversations(controller)
    core.root.mkdir()

    refs = [_hex_ref(10), _hex_ref(11), _hex_ref(12)]
    for ref in refs:
        row = _native_request(ref, status="command_completed")
        row["delivery_verified"] = True
        row["message"] = "retained body " + ref
        _atomic_write_json(core.root / f"{ref}.json", row)
    (core.root / "ignored-note.json").write_text(json.dumps({"ignored": True}), encoding="utf-8")

    # pending() still returns full canonical rows including message bodies.
    assert [row["message"] for row in core.pending()] == ["retained body " + ref for ref in refs]

    reads: list[Path] = []
    original_read = external_module._read_json

    def counted_read(path: Path) -> dict[str, object]:
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(external_module, "_read_json", counted_read)

    core.recover()
    assert len(reads) == 3

    reads.clear()
    core.recover()
    assert reads == []

    # Mutating returned metadata rows must not corrupt the discovery cache.
    snapshot = core._recovery_rows()
    assert [row["request_ref"] for row in snapshot] == refs
    assert all("message" not in row and "attachments" not in row for row in snapshot)
    snapshot[0]["status"] = "mutated"
    snapshot[0]["source"]["source_ref"] = "tampered"
    assert [row["status"] for row in core._recovery_rows()] == ["command_completed"] * 3
    assert core._recovery_rows()[0]["source"]["source_ref"] == "source_one"
    assert reads == []

    # Replacing a file (even with preserved mtime) invalidates only that file.
    target = core.root / f"{refs[1]}.json"
    stamp = target.stat()
    updated = _native_request(refs[1], status="command_completed")
    updated["delivery_verified"] = False
    _atomic_write_json(target, updated)
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    core.recover()
    assert reads == [target]

    # Deleting a file removes it without re-reading unchanged siblings.
    reads.clear()
    (core.root / f"{refs[0]}.json").unlink()
    remaining = core._recovery_rows()
    assert [row["request_ref"] for row in remaining] == [refs[1], refs[2]]
    assert reads == []


def test_external_conversations_recover_does_not_retain_delivered_image_payloads(tmp_path):
    controller = SimpleNamespace(
        store=SimpleNamespace(root=tmp_path),
        project_contexts=SimpleNamespace(conversation_bindings=SimpleNamespace()),
    )
    core = ChatExternalConversations(controller)
    core.root.mkdir()

    # 24 delivered requests each carrying ~1.5 MiB synthetic data_url image attachments.
    payload_blob = "data:image/png;base64," + ("A" * (1_500_000))
    for i in range(24):
        ref = _hex_ref(100 + i)
        row = _native_request(ref, status="command_completed")
        row["delivery_verified"] = True
        row["attachments"] = [{"kind": "image", "mime_type": "image/png", "data_url": payload_blob}]
        _atomic_write_json(core.root / f"{ref}.json", row)
    del payload_blob
    gc.collect()

    tracemalloc.start()
    try:
        before = tracemalloc.take_snapshot()
        core.recover()
        gc.collect()
        after = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()

    retained_bytes = sum(stat.size_diff for stat in after.compare_to(before, "lineno") if stat.size_diff > 0)
    assert retained_bytes < 512 * 1024, f"recover() retained {retained_bytes} bytes of delivered image payloads"


def test_private_conversations_caches_historical_repairs_and_staggers_recovery_probes(tmp_path, monkeypatch):
    from loopx.extensions.lark import private_conversations as private
    from loopx.extensions.lark.private_delivery_discovery import PrivateDeliveryDiscovery

    repairs_dir = tmp_path / "historical-answer-images"
    repairs_dir.mkdir()
    _atomic_write_json(repairs_dir / f"{_hex_ref(1)}.json", {"turn_id": "t1"})

    clock = [100.0]
    monkeypatch.setattr(private, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    transport = private.LarkPrivateConversations.__new__(private.LarkPrivateConversations)
    transport.root = tmp_path
    transport._context_recovery_probe_at = {}
    transport._historical_image_probe_at = {}
    transport._delivery_discovery = PrivateDeliveryDiscovery(tmp_path)
    transport._web_projection_paths = lambda: []
    transport.core = SimpleNamespace(context_recovery_observer=lambda: lambda _ref: False)

    for idx in range(2, 62):
        ref = _hex_ref(idx)
        _atomic_write_json(
            tmp_path / f"{ref}.json",
            {
                "status": "delivered",
                "request_ref": ref,
                "session_id": f"sess_{idx}",
                "turn_id": f"turn_{idx}",
                "profile": "fixture",
                "event": {"message_id": f"om_{idx}"},
            },
        )

    glob_calls = 0
    original_glob = Path.glob

    def counted_glob(self: Path, pattern: str):
        nonlocal glob_calls
        if self == repairs_dir:
            glob_calls += 1
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", counted_glob)

    assert transport.pending_delivery_paths() == []
    assert glob_calls == 1
    assert transport.pending_delivery_paths() == []
    assert glob_calls == 1, "unchanged historical-answer-images dir must hit mtime cache without globbing"

    # Adding a new repair manifest updates dir mtime_ns and refreshes the cached set once.
    _atomic_write_json(repairs_dir / f"{_hex_ref(2)}.json", {"turn_id": "t2"})
    assert transport.pending_delivery_paths() == [tmp_path / f"{_hex_ref(2)}.json"]
    assert glob_calls == 2

    # Delivered probe deadlines must stagger across [60, 120)s rather than locking every file onto T+60.
    deadlines = set(transport._context_recovery_probe_at.values())
    assert len(deadlines) > 10
    assert all(160.0 <= value < 220.0 for value in deadlines)


def _recovery_session_store(tmp_path: Path):
    from loopx.chat_store import ChatSessionStore

    return ChatSessionStore(tmp_path / "runtime", startup_maintenance=False)


def _seed_recovery_inputs(store, session_id: str, turn_id: str) -> dict[str, Path]:
    """Lay out Turn, session, and events files on the chat store paths."""
    paths = {
        "turn": store._turn_path(session_id, turn_id),
        "session": store._session_path(session_id),
        "events": store._event_path(session_id, turn_id),
    }
    paths["turn"].parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(paths["turn"], {"turn_id": turn_id, "status": "failed"})
    _atomic_write_json(paths["session"], {"session_id": session_id, "status": "ready"})
    paths["events"].write_text('{"kind":"turn.failed"}\n', encoding="utf-8")
    return paths


def _replace_recovery_input(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def test_reconcile_request_backs_off_repeated_noop_recovery_and_resets_on_change(tmp_path, monkeypatch):
    import zlib
    from loopx.extensions.lark import private_conversations as private
    from loopx.extensions.lark.private_delivery_discovery import PrivateDeliveryDiscovery

    deliv_dir = tmp_path / "lark-private-deliveries"
    ext_dir = tmp_path / "external-requests"
    deliv_dir.mkdir()
    ext_dir.mkdir()

    ref = _hex_ref(42)
    session_id = f"sess_{ref}"
    turn_id = f"turn_{ref}"
    deliv_path = deliv_dir / f"{ref}.json"
    ext_path = ext_dir / f"{ref}.json"
    deliv_row = {
        "status": "delivered",
        "request_ref": ref,
        "session_id": session_id,
        "turn_id": turn_id,
        "profile": "fixture",
        "event": {"message_id": f"om_{ref}"},
    }
    _atomic_write_json(deliv_path, deliv_row)
    _atomic_write_json(ext_path, {"request_ref": ref, "status": "accepted"})
    store = _recovery_session_store(tmp_path)

    clock = [1000.0]
    monkeypatch.setattr(private, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(private, "record_event_timing", lambda *_a, **_kw: None)

    transport = private.LarkPrivateConversations.__new__(private.LarkPrivateConversations)
    transport.root = deliv_dir
    transport.reaction_feedback = False
    transport._context_recovery_probe_at = {}
    transport._historical_image_probe_at = {}
    transport._delivery_discovery = PrivateDeliveryDiscovery(deliv_dir)
    transport._web_projection_paths = lambda: []

    noop_calls = 0

    def fake_recover(_request_ref: str, **_kwargs):
        nonlocal noop_calls
        noop_calls += 1
        return {"request_ref": ref, "status": "accepted", "turn_id": turn_id, "delivery_verified": True}

    transport.core = SimpleNamespace(
        root=ext_dir,
        controller=SimpleNamespace(store=store),
        context_recovery_observer=lambda: lambda candidate_ref: candidate_ref == ref,
        context_recovery_candidate=lambda candidate_ref: candidate_ref == ref,
        recover_failed_request=fake_recover,
        recover=lambda **_kwargs: None,
    )

    jitter = zlib.crc32(deliv_path.name.encode("utf-8")) % 60

    # Discovery finds candidate -> first noop reconcile keeps exact +60s retry.
    assert transport.pending_delivery_paths() == [deliv_path]
    assert transport.reconcile_request(deliv_path) == 0
    assert noop_calls == 1
    assert transport._context_recovery_probe_at[deliv_path] == clock[0] + 60.0

    # Before +60s, unchanged fingerprints remain backed off.
    clock[0] = 1059.0
    assert transport.pending_delivery_paths() == []

    # Second consecutive noop backs off to 120 + jitter; third to 240 + jitter.
    clock[0] = 1060.0
    assert transport.pending_delivery_paths() == [deliv_path]
    assert transport.reconcile_request(deliv_path) == 0
    assert transport._context_recovery_probe_at[deliv_path] == 1060.0 + 120.0 + jitter

    clock[0] = 1060.0 + 120.0 + jitter
    assert transport.pending_delivery_paths() == [deliv_path]
    assert transport.reconcile_request(deliv_path) == 0
    assert transport._context_recovery_probe_at[deliv_path] == clock[0] + 240.0 + jitter

    # Changing the external-request file resets backoff immediately on the next discovery.
    clock[0] += 5.0
    _atomic_write_json(ext_path, {"request_ref": ref, "status": "accepted", "updated": 1})
    assert transport.pending_delivery_paths() == [deliv_path]


def _noop_recovery_transport(tmp_path, monkeypatch):
    from loopx.extensions.lark import private_conversations as private
    from loopx.extensions.lark.private_delivery_discovery import PrivateDeliveryDiscovery

    deliv_dir = tmp_path / "lark-private-deliveries"
    ext_dir = tmp_path / "external-requests"
    deliv_dir.mkdir()
    ext_dir.mkdir()
    ref = _hex_ref(42)
    session_id = f"sess_{ref}"
    turn_id = f"turn_{ref}"
    deliv_path = deliv_dir / f"{ref}.json"
    ext_path = ext_dir / f"{ref}.json"
    _atomic_write_json(deliv_path, {
        "status": "delivered",
        "request_ref": ref,
        "session_id": session_id,
        "turn_id": turn_id,
        "profile": "fixture",
        "event": {"message_id": f"om_{ref}"},
    })
    _atomic_write_json(ext_path, {"request_ref": ref, "status": "accepted"})
    store = _recovery_session_store(tmp_path)
    inputs = _seed_recovery_inputs(store, session_id, turn_id)
    clock = [1000.0]
    monkeypatch.setattr(private, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(private, "record_event_timing", lambda *_a, **_kw: None)
    transport = private.LarkPrivateConversations.__new__(private.LarkPrivateConversations)
    transport.root = deliv_dir
    transport.reaction_feedback = False
    transport._context_recovery_probe_at = {}
    transport._historical_image_probe_at = {}
    transport._delivery_discovery = PrivateDeliveryDiscovery(deliv_dir)
    transport._web_projection_paths = lambda: []
    transport.core = SimpleNamespace(
        root=ext_dir,
        controller=SimpleNamespace(store=store),
        context_recovery_observer=lambda: lambda candidate_ref: candidate_ref == ref,
        context_recovery_candidate=lambda candidate_ref: candidate_ref == ref,
        recover_failed_request=lambda _request_ref, **_kwargs: {
            "request_ref": ref, "status": "accepted", "turn_id": turn_id, "delivery_verified": True,
        },
        recover=lambda **_kwargs: None,
    )
    return transport, deliv_path, ext_path, inputs, clock


def _arm_noop_backoff(transport, deliv_path, clock):
    assert transport.pending_delivery_paths() == [deliv_path]
    assert transport.reconcile_request(deliv_path) == 0
    clock[0] += 5.0
    assert transport.pending_delivery_paths() == []


def test_noop_recovery_backoff_resets_when_turn_session_or_events_change(tmp_path, monkeypatch):
    transport, deliv_path, _ext_path, inputs, clock = _noop_recovery_transport(tmp_path, monkeypatch)

    _arm_noop_backoff(transport, deliv_path, clock)
    _replace_recovery_input(inputs["turn"], '{"turn_id":"changed","status":"failed","marker":"turn"}\n')
    _arm_noop_backoff(transport, deliv_path, clock)
    _replace_recovery_input(inputs["session"], '{"session_id":"changed","status":"ready","marker":"session"}\n')
    _arm_noop_backoff(transport, deliv_path, clock)
    _replace_recovery_input(inputs["events"], '{"kind":"turn.failed","marker":"events"}\n')
    assert transport.pending_delivery_paths() == [deliv_path]


def test_noop_recovery_backoff_does_not_pin_when_fingerprint_unavailable(tmp_path, monkeypatch):
    import zlib
    import loopx.capabilities.native_chat.file_discovery as discovery

    transport, deliv_path, ext_path, _inputs, clock = _noop_recovery_transport(tmp_path, monkeypatch)
    monkeypatch.setattr(discovery, "file_change_fingerprint", lambda _path: None)

    assert transport.pending_delivery_paths() == [deliv_path]
    assert transport.reconcile_request(deliv_path) == 0
    assert transport._context_recovery_probe_at[deliv_path] == clock[0] + 60.0

    clock[0] += 60.0
    assert transport.pending_delivery_paths() == [deliv_path]
    assert transport.reconcile_request(deliv_path) == 0
    # Unavailable tokens are not an identity, so a second no-op stays on the 60s probe.
    assert transport._context_recovery_probe_at[deliv_path] == clock[0] + 60.0
    exponential = clock[0] + 120.0 + (zlib.crc32(deliv_path.name.encode("utf-8")) % 60)
    assert transport._context_recovery_probe_at[deliv_path] < exponential

    clock[0] += 5.0
    _atomic_write_json(ext_path, {"request_ref": store_read_json(ext_path)["request_ref"], "status": "accepted", "updated": 1})
    assert transport.pending_delivery_paths() == []

    clock[0] += 55.0
    assert clock[0] < exponential
    assert transport.pending_delivery_paths() == [deliv_path]


def test_source_request_digests_cache_unchanged_requests_and_detect_mutation(tmp_path, monkeypatch):
    import loopx.capabilities.native_chat.external_conversations as ext_mod

    core = ChatExternalConversations.__new__(ChatExternalConversations)
    core.root = tmp_path
    refs = [_hex_ref(1), _hex_ref(2), _hex_ref(3)]
    for idx, ref in enumerate(refs):
        _atomic_write_json(
            tmp_path / f"{ref}.json",
            {
                "request_ref": ref,
                "binding_id": "binding_one",
                "source": {"source_ref": "source_one"},
                "seq": idx,
            },
        )
    target = {"binding_id": "binding_one", "source": {"source_ref": "source_one"}}

    reads: list[Path] = []
    original_read = ext_mod._read_json

    def counted_read(path: Path):
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(ext_mod, "_read_json", counted_read)

    first = core._source_request_digests(target)
    assert [row["request_ref"] for row in first] == refs
    assert len(reads) == 3

    reads.clear()
    second = core._source_request_digests(target)
    assert second == first
    assert reads == [], "unchanged external-request files must not be re-read"

    # Mutating a single request invalidates only that file and changes its digest.
    mutated_path = tmp_path / f"{refs[1]}.json"
    _atomic_write_json(
        mutated_path,
        {
            "request_ref": refs[1],
            "binding_id": "binding_one",
            "source": {"source_ref": "source_one"},
            "seq": 99,
        },
    )
    third = core._source_request_digests(target)
    assert reads == [mutated_path]
    assert third[1]["sha256"] != first[1]["sha256"]
    assert third[0]["sha256"] == first[0]["sha256"]


def test_recovery_discovery_reads_caches_session_user_messages_without_attachments(tmp_path, monkeypatch):
    from loopx.capabilities.native_chat.external_conversations import _RecoveryDiscoveryReads
    from loopx.chat_store import ChatSessionStore

    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-perf-test",
        agent_id="codex",
        adapter_kind="codex",
        upstream_thread_id="thread_perf_1",
    )
    sid = session["session_id"]
    png_1x1 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )
    store.append_message(
        sid,
        role="user",
        text="hello",
        origin="lark",
        turn_id="turn_1",
        attachments=[{"id": "att_1", "name": "a.png", "mime_type": "image/png", "size": 68, "data_url": f"data:image/png;base64,{png_1x1}"}],
    )
    store.append_message(sid, role="agent", text="reply", turn_id="turn_1")

    core = ChatExternalConversations.__new__(ChatExternalConversations)
    core.controller = SimpleNamespace(store=store)

    msg_reads = 0
    original_messages = ChatSessionStore.messages

    def counted_messages(self, session_id: str, *, strict: bool = False):
        nonlocal msg_reads
        msg_reads += 1
        return original_messages(self, session_id, strict=strict)

    monkeypatch.setattr(ChatSessionStore, "messages", counted_messages)

    msgs_pass1 = _RecoveryDiscoveryReads(core).user_messages(sid)
    assert msg_reads == 1
    assert [m["text"] for m in msgs_pass1] == ["hello"]

    # Fresh discovery pass must hit fingerprint cache without re-reading messages.jsonl.
    msgs_pass2 = _RecoveryDiscoveryReads(core).user_messages(sid)
    assert msg_reads == 1
    assert len(msgs_pass2) == 1
    assert msgs_pass2[0]["text"] == "hello"
    assert "attachments" not in msgs_pass2[0]

    # Appending a new user message updates messages.jsonl fingerprint and refreshes cache.
    store.append_message(sid, role="user", text="world", origin="lark", turn_id="turn_2")
    msgs_pass3 = _RecoveryDiscoveryReads(core).user_messages(sid)
    assert msg_reads == 2
    assert [m["text"] for m in msgs_pass3] == ["hello", "world"]


def test_private_delivery_discovery_caches_retired_triage_captures(tmp_path, monkeypatch):
    import loopx.extensions.lark.private_delivery_discovery as pdd_mod
    from loopx.extensions.lark.private_delivery_discovery import PrivateDeliveryDiscovery

    retired_path = tmp_path / f"{_hex_ref(10)}.json"
    active_path = tmp_path / f"{_hex_ref(11)}.json"
    _atomic_write_json(retired_path, {"status": "captured", "triage": {"retired": "card_delivery_expired"}})
    _atomic_write_json(active_path, {"status": "captured", "profile": "steward"})

    reads: list[Path] = []
    original_read = pdd_mod._read_json

    def counted_read(path: Path):
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(pdd_mod, "_read_json", counted_read)

    discovery = PrivateDeliveryDiscovery(tmp_path)
    first = {path: row for path, row, _ in discovery.rows()}
    assert set(first) == {retired_path, active_path}
    assert sorted(reads) == sorted([retired_path, active_path])

    reads.clear()
    second = {path: row for path, row, _ in discovery.rows()}
    assert second[retired_path]["triage"]["retired"] == "card_delivery_expired"
    assert reads == [active_path], "retired triage capture must stay cached while active capture stays fresh"


def _seed_backlog_health_store(tmp_path: Path, *, failed_count: int = 20, completed_count: int = 20):
    from loopx.chat_store import CHAT_TURN_SCHEMA_VERSION, ChatSessionStore
    from loopx.extensions.lark.private_conversations import LarkPrivateConversations

    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="goal-health-cache",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-health-cache",
        session_id="a" * 32,
    )
    sid = session["session_id"]
    controller = SimpleNamespace(
        store=store,
        project_contexts=SimpleNamespace(conversation_bindings=SimpleNamespace()),
    )
    core = ChatExternalConversations(controller)
    transport = LarkPrivateConversations.__new__(LarkPrivateConversations)
    transport.core = core
    transport.root = store.root / "lark-private-deliveries"
    core.root.mkdir(parents=True, exist_ok=True)
    transport.root.mkdir(parents=True, exist_ok=True)

    failed_rows: list[dict[str, object]] = []
    for idx in range(failed_count + completed_count):
        is_failed = idx < failed_count
        ref = _hex_ref(1000 + idx)
        tid = f"{1000 + idx:032x}"
        row = {
            "request_ref": ref,
            "binding_id": "b" * 24,
            "source": {"source_ref": "c" * 24},
            "session_id": sid,
            "turn_id": tid,
            "command": None,
            "status": "accepted",
            "delivery_verified": True,
            "message": f"req {idx}",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        turn = {
            "schema_version": CHAT_TURN_SCHEMA_VERSION,
            "session_id": sid,
            "turn_id": tid,
            "client_turn_id": f"external-{ref}",
            "status": "failed" if is_failed else "completed",
            **({"error_code": "usage_limit_exceeded", "response": None} if is_failed else {
                "error_code": None,
                "response": {"message": "done", "proposals": []},
            }),
        }
        _atomic_write_json(store._turn_path(sid, tid), turn)
        _atomic_write_json(core.root / f"{ref}.json", row)
        _atomic_write_json(transport.root / f"{ref}.json", {**row, "status": "delivered", "deliveries": {}})
        if is_failed:
            failed_rows.append(row)
    return store, core, transport, sid, failed_rows


def test_backlog_health_caches_unchanged_turns_and_avoids_ipc_and_event_lock_churn(tmp_path, monkeypatch):
    from loopx.chat_store import ChatSessionStore
    from loopx.control_plane import effect_runtime

    store, core, transport, _sid, failed_rows = _seed_backlog_health_store(tmp_path)
    original_rpc = effect_runtime.effect_runtime_result
    original_load_turn = ChatSessionStore.load_turn
    original_events_after = ChatSessionStore.events_after
    counts = {"rpc": 0, "load_turn": 0, "events_after": 0}

    def counted_rpc(method, params, **kwargs):
        if method == "collaboration.conversation.request":
            counts["rpc"] += 1
        return original_rpc(method, params, **kwargs)

    def counted_load_turn(self, session_id: str, turn_id: str):
        counts["load_turn"] += 1
        return original_load_turn(self, session_id, turn_id)

    def counted_events_after(self, session_id: str, turn_id: str, event_id: str | None):
        counts["events_after"] += 1
        return original_events_after(self, session_id, turn_id, event_id)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", counted_rpc)
    monkeypatch.setattr(ChatSessionStore, "load_turn", counted_load_turn)
    monkeypatch.setattr(ChatSessionStore, "events_after", counted_events_after)

    assert transport.health()["b" * 24] == {"pending_count": 20, "recovery_count": 0}
    assert counts["rpc"] == 1
    assert counts["load_turn"] == 40
    assert counts["events_after"] == 20

    counts = {"rpc": 0, "load_turn": 0, "events_after": 0}
    assert transport.health()["b" * 24] == {"pending_count": 20, "recovery_count": 0}
    assert counts == {"rpc": 0, "load_turn": 0, "events_after": 0}

    # Uncached direct observer (default use_cache=False) still evaluates in-memory mutations fresh.
    mutated = [{**failed_rows[0], "command": "stop"}]
    assert core.quota_backlog_observer()(mutated) == [False]


def test_backlog_health_cache_invalidates_on_turn_event_recovery_session_and_stop_mutations(tmp_path, monkeypatch):
    from loopx.chat_store import CHAT_TURN_SCHEMA_VERSION
    from loopx.control_plane import effect_runtime

    store, core, transport, sid, failed_rows = _seed_backlog_health_store(tmp_path, failed_count=6, completed_count=4)
    original_rpc = effect_runtime.effect_runtime_result
    rpc_calls = 0

    def counted_rpc(method, params, **kwargs):
        nonlocal rpc_calls
        if method == "collaboration.conversation.request":
            rpc_calls += 1
        return original_rpc(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", counted_rpc)
    assert transport.health()["b" * 24]["pending_count"] == 6
    assert rpc_calls == 1

    # 1. Completing one failed Turn invalidates only that row immediately.
    r0 = failed_rows[0]
    _atomic_write_json(store._turn_path(sid, str(r0["turn_id"])), {
        "schema_version": CHAT_TURN_SCHEMA_VERSION,
        "session_id": sid,
        "turn_id": r0["turn_id"],
        "client_turn_id": f"external-{r0['request_ref']}",
        "status": "completed",
        "error_code": None,
        "response": {"message": "ok", "proposals": []},
    })
    assert transport.health()["b" * 24]["pending_count"] == 5

    # 2. Appending a flushed turn.interrupted event invalidates that Turn immediately.
    r1 = failed_rows[1]
    store.append_event(sid, str(r1["turn_id"]), kind="turn.interrupted", payload={}, buffered=False)
    assert transport.health()["b" * 24]["pending_count"] == 4

    # 3. Buffered (unflushed) turn.interrupt_requested in _event_pending must never serve stale cache.
    r2 = failed_rows[2]
    store.append_event(sid, str(r2["turn_id"]), kind="turn.interrupt_requested", payload={}, buffered=True)
    assert transport.health()["b" * 24]["pending_count"] == 3

    # 4. Recording a verified native_result_recovery on disk invalidates that request immediately.
    r3 = failed_rows[3]
    _atomic_write_json(core.root / f"{r3['request_ref']}.json", {
        **r3,
        "native_result_recovery": {
            "turn_id": r3["turn_id"],
            "message_id": f"native.{r3['turn_id']}.recovered",
            "message_text": "recovered ok",
            "delivery_verified": True,
            "native_observation": {"response": {"message": "recovered ok", "proposals": []}},
        },
    })
    assert transport.health()["b" * 24]["pending_count"] == 2

    # 5. Writing a later /stop command clears remaining pending items using only stopped RPC, then hits _stopped cache.
    rpc_calls = 0
    _atomic_write_json(core.root / f"{_hex_ref(9999)}.json", {
        "request_ref": _hex_ref(9999),
        "binding_id": "b" * 24,
        "source": {"source_ref": "c" * 24},
        "session_id": sid,
        "turn_id": None,
        "command": "stop",
        "status": "command_completed",
        "created_at": "2026-01-02T00:00:00+00:00",
    })
    assert transport.health()["b" * 24]["pending_count"] == 0
    assert rpc_calls == 1

    rpc_calls = 0
    assert transport.health()["b" * 24]["pending_count"] == 0
    assert rpc_calls == 0

    # Replacing effect_runtime_result invalidates cached verdicts by evaluator identity.
    def wrapper_rpc(method, params, **kwargs):
        nonlocal rpc_calls
        if method == "collaboration.conversation.request":
            rpc_calls += 1
        return original_rpc(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", wrapper_rpc)
    assert transport.health()["b" * 24]["pending_count"] == 0
    assert rpc_calls > 0
    rpc_calls = 0
    assert transport.health()["b" * 24]["pending_count"] == 0
    assert rpc_calls == 0

    # 6. Closing a Session also drops remaining cached pending counts.
    _store2, _core2, transport2, sid2, _ = _seed_backlog_health_store(tmp_path / "s2", failed_count=3, completed_count=0)
    assert transport2.health()["b" * 24]["pending_count"] == 3
    sess_path = _store2._session_path(sid2)
    _atomic_write_json(sess_path, {**store_read_json(sess_path), "status": "closed"})
    assert transport2.health()["b" * 24]["pending_count"] == 0


def test_listener_snapshot_coalesces_concurrent_watchers_and_invalidates_on_refresh(tmp_path, monkeypatch):
    import threading
    from loopx.extensions.lark import goal_topic_runtime_service as svc_mod
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    clock = [100.0]
    monkeypatch.setattr(svc_mod.time, "monotonic", lambda: clock[0])

    entered = threading.Event()
    release = threading.Event()
    provider_calls = 0
    snapshot_seq = 0
    fail_next = False

    def snapshot_provider():
        nonlocal provider_calls, snapshot_seq, fail_next
        provider_calls += 1
        if fail_next:
            fail_next = False
            raise RuntimeError("transient snapshot error")
        if provider_calls == 1:
            entered.set()
            assert release.wait(2)
        snapshot_seq += 1
        return {
            "private_profiles": {},
            "target_payload": {"targets": {}},
            "binding_payloads": {},
            "seq": snapshot_seq,
        }

    service = LarkGoalTopicRuntimeService(
        snapshot_provider=snapshot_provider,
        runtime_root=tmp_path,
        runtime_controller=SimpleNamespace(store=None),
        profile_poller=lambda _profile, stop: stop.wait(),
    )
    results: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def reader():
        try:
            results.append(dict(service._read_listener_snapshot()))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    assert entered.wait(2)
    # Give sibling threads a moment to block on single-flight condition before releasing.
    threading.Event().wait(0.05)
    release.set()
    for t in threads:
        t.join(2)
    assert not errors
    assert provider_calls == 1
    assert [row["seq"] for row in results] == [1, 1, 1]

    # Within TTL, repeated calls hit the coalesced cache.
    clock[0] += 4.5
    assert service._read_listener_snapshot()["seq"] == 1
    assert provider_calls == 1

    # Past TTL (5.0s), failures are not cached and the following call retries cleanly.
    clock[0] += 0.6
    fail_next = True
    with pytest.raises(RuntimeError, match="transient snapshot error"):
        service._read_listener_snapshot()
    assert provider_calls == 2
    assert service._read_listener_snapshot()["seq"] == 2
    assert provider_calls == 3

    # refresh() invalidates and seeds the cache atomically.
    assert service._refresh_profile_workers(resume_queues=False) is False
    assert provider_calls == 4
    assert service._read_listener_snapshot()["seq"] == 3
    assert provider_calls == 4
    service.close()


def test_context_recovery_discovery_caches_turn_event_summary_without_event_lock_churn(tmp_path, monkeypatch):
    from loopx.chat_manager import MANAGER_CONTEXT_VERSION
    from loopx.chat_store import CHAT_TURN_SCHEMA_VERSION, ChatSessionStore

    store = ChatSessionStore(tmp_path)
    binding_id = "b" * 24
    source_ref = "c" * 24
    binding = {
        "schema_version": "loopx_chat_conversation_binding_v0",
        "binding_id": binding_id,
        "enabled": True,
        "transport_ref": "steward-app",
        "context_kind": "steward",
        "goal_ids": [],
        "grant": "portfolio_read",
        "provider_ref": "a" * 24,
        "operator_ref": "e" * 24,
        "project_ref": "f" * 24,
        "executor_endpoint_id": "antigravity",
    }
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="antigravity",
        adapter_kind="antigravity",
        upstream_thread_id="thread-recovery-events",
        upstream_mode="default",
        session_id="d" * 32,
        channel_id=f"manager.external.native.{binding_id}.{source_ref}",
        steward_context={
            "kind": "bound_steward",
            "binding_id": binding_id,
            "audience": "bound_owner",
            "grant": "portfolio_read",
            "goal_ids": [],
            "provider_ref": "a" * 24,
            "operator_ref": "e" * 24,
            "project_ref": "f" * 24,
            "source_ref": source_ref,
            "workspace_path": str(tmp_path),
        },
    )
    sid = session["session_id"]
    sess_path = store._session_path(sid)
    _atomic_write_json(sess_path, {
        **store.load_session(sid),
        "session_mode": "managed_runtime",
        "executor_endpoint_id": "antigravity",
        "manager_context_version": MANAGER_CONTEXT_VERSION,
    })
    controller = SimpleNamespace(
        store=store,
        lock=SimpleNamespace(__enter__=lambda s: None, __exit__=lambda s, *a: False),
        adapters={},
        codex_home=tmp_path,
        project_contexts=SimpleNamespace(
            conversation_bindings=SimpleNamespace(
                read=lambda: {"bindings": [binding]},
                resolve=lambda **_: {"binding": binding},
            ),
        ),
    )
    core = ChatExternalConversations(controller)
    core.root.mkdir(parents=True, exist_ok=True)

    refs: list[str] = []
    tids: list[str] = []
    # 12 failed turns (> ChatEventCache's 8-turn limit) would thrash events_after without summary caching.
    for idx in range(12):
        ref = _hex_ref(2000 + idx)
        tid = f"{2000 + idx:032x}"
        refs.append(ref)
        tids.append(tid)
        _atomic_write_json(store._turn_path(sid, tid), {
            "schema_version": CHAT_TURN_SCHEMA_VERSION,
            "session_id": sid,
            "turn_id": tid,
            "client_turn_id": f"external-{ref}",
            "message": f"question {idx}",
            "origin": "lark",
            "status": "failed",
            "error_code": "antigravity_step_error",
            "delta_count": 0,
            "upstream_turn_id": f"upstream-{idx}",
            "created_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:01:00+00:00",
            "response": None,
        })
        store.append_event(sid, tid, kind="turn.started", payload={
            "native_input": {
                "thread_id": "thread-recovery-events",
                "start_step_index": idx,
                "input_sha256": "a" * 64,
            },
        }, buffered=False)
        if 4 <= idx < 8:
            store.append_event(sid, tid, kind="turn.steered", payload={}, buffered=False)
        elif idx >= 8:
            store.append_event(sid, tid, kind="turn.interrupted", payload={}, buffered=False)
        _atomic_write_json(core.root / f"{ref}.json", {
            "request_ref": ref,
            "binding_id": binding_id,
            "source": {"source_ref": source_ref, "sender_ref": "e" * 24, "private_human_message": True},
            "session_id": sid,
            "turn_id": tid,
            "command": None,
            "origin": "lark",
            "status": "accepted",
            "delivery_verified": True,
            "message": f"question {idx}",
            "created_at": "2026-01-01T00:00:00+00:00",
        })
    store.update_session(sid, manager_context_version=MANAGER_CONTEXT_VERSION)

    event_reads: list[str] = []
    original_events_after = ChatSessionStore.events_after

    def counted_events_after(self_store, session_id: str, turn_id: str, event_id: str | None):
        event_reads.append(turn_id)
        return original_events_after(self_store, session_id, turn_id, event_id)

    monkeypatch.setattr(ChatSessionStore, "events_after", counted_events_after)

    observe1 = core.context_recovery_observer()
    verdicts1 = [observe1(ref) for ref in refs]
    assert len(event_reads) == 12
    assert verdicts1 == [True] * 4 + [False] * 8

    # Pass 2 across all 12 historical turns hits _event_summaries with zero events_after reads.
    event_reads.clear()
    observe2 = core.context_recovery_observer()
    assert [observe2(ref) for ref in refs] == verdicts1
    assert event_reads == []

    # Flushed turn.interrupted event invalidates only that Turn immediately.
    store.append_event(sid, tids[0], kind="turn.interrupted", payload={}, buffered=False)
    observe3 = core.context_recovery_observer()
    assert observe3(refs[0]) is False
    assert observe3(refs[1]) is True
    assert event_reads == [tids[0]]

    # Buffered unflushed event in _event_pending bypasses cache immediately.
    event_reads.clear()
    store.append_event(sid, tids[1], kind="turn.interrupt_requested", payload={}, buffered=True)
    observe4 = core.context_recovery_observer()
    assert observe4(refs[1]) is False
    assert event_reads == [tids[1]]

    # Non-discovery admission and preparation failures always read fresh full events_after.
    event_reads.clear()
    row2 = core.read_request(refs[2])
    core._context_recovery_plan(row2, discovery_only=False, selected_binding=binding)
    assert event_reads == [tids[2]]

    event_reads.clear()
    _atomic_write_json(store._turn_path(sid, tids[3]), {
        "schema_version": CHAT_TURN_SCHEMA_VERSION,
        "session_id": sid,
        "turn_id": tids[3],
        "client_turn_id": f"external-{refs[3]}",
        "status": "failed",
        "error_code": "manager_authorization_unavailable",
        "created_at": "2026-01-01T00:00:00+00:00",
        "response": None,
    })
    core.context_recovery_observer()(refs[3])
    assert event_reads == [tids[3]]


def test_backlog_health_defers_recovery_body_reads_and_reuses_request_fingerprints(tmp_path, monkeypatch):
    import loopx.capabilities.native_chat.external_conversations as ext_mod
    import loopx.capabilities.native_chat.file_discovery as fd_mod

    store, core, transport, sid, failed_rows = _seed_backlog_health_store(tmp_path, failed_count=3, completed_count=2)
    # Add two requests carrying context_recovery metadata so deferred recovery reads are exercised.
    for row in failed_rows[:2]:
        _atomic_write_json(core.root / f"{row['request_ref']}.json", {
            **row,
            "context_recovery": {"failed_turn_id": row["turn_id"], "client_turn_id": f"external-{row['request_ref']}-recovery-1"},
        })
    # Add an earlier /stop command for an unrelated source so stop_controls() is queried while pending stays 3.
    _atomic_write_json(core.root / f"{_hex_ref(8888)}.json", {
        "request_ref": _hex_ref(8888),
        "binding_id": "b" * 24,
        "source": {"source_ref": "e" * 24},
        "session_id": sid,
        "turn_id": None,
        "command": "stop",
        "status": "command_completed",
        "created_at": "2026-01-01T00:00:00+00:00",
    })

    # Cold pass warms both FileChatDiscovery and QuotaBacklogObservationCache.
    assert transport.health()["b" * 24]["pending_count"] == 3

    reads: list[Path] = []
    stats_by_path: dict[Path, int] = {}
    original_read = ext_mod._read_json
    original_fp = fd_mod.file_change_fingerprint

    def counted_read(path: Path):
        reads.append(path)
        return original_read(path)

    def counted_fp(path: Path):
        stats_by_path[path] = stats_by_path.get(path, 0) + 1
        return original_fp(path)

    monkeypatch.setattr(ext_mod, "_read_json", counted_read)
    monkeypatch.setattr(fd_mod, "file_change_fingerprint", counted_fp)

    assert transport.health()["b" * 24]["pending_count"] == 3
    assert reads == [], "warm health() must not re-read recovery requests"
    for row in failed_rows:
        req_path = core.root / f"{row['request_ref']}.json"
        assert stats_by_path.get(req_path) == 1, f"expected 1 stat for {req_path}, got {stats_by_path.get(req_path)}"

    # Mutating a recovery request (marking recovered on row 0) with preserved mtime invalidates and drops pending to 2.
    target_path = core.root / f"{failed_rows[0]['request_ref']}.json"
    stamp = target_path.stat()
    _atomic_write_json(target_path, {
        **store_read_json(target_path),
        "native_result_recovery": {
            "turn_id": failed_rows[0]["turn_id"],
            "message_id": f"native.{failed_rows[0]['turn_id']}.recovered",
            "message_text": "recovered ok",
            "delivery_verified": True,
            "native_observation": {"response": {"message": "recovered ok", "proposals": []}},
        },
    })
    os.utime(target_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    reads.clear()
    assert transport.health()["b" * 24]["pending_count"] == 2
    assert set(reads) == {target_path}
