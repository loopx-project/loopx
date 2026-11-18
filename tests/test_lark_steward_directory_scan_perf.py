"""Regression and directory-scan efficiency checks for steward/chat hot paths."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

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


def test_external_conversations_pending_caches_unchanged_journals_and_isolates_mutations(tmp_path, monkeypatch):
    import loopx.capabilities.native_chat.external_conversations as external_module

    controller = SimpleNamespace(
        store=SimpleNamespace(root=tmp_path),
        project_contexts=SimpleNamespace(conversation_bindings=SimpleNamespace()),
    )
    core = ChatExternalConversations(controller)
    core.root.mkdir()

    refs = [_hex_ref(10), _hex_ref(11), _hex_ref(12)]
    for ref in refs:
        _atomic_write_json(core.root / f"{ref}.json", _native_request(ref, status="prepared"))
    (core.root / "ignored-note.json").write_text(json.dumps({"ignored": True}), encoding="utf-8")

    reads: list[Path] = []
    original_read = external_module._read_json

    def counted_read(path: Path) -> dict[str, object]:
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(external_module, "_read_json", counted_read)

    first = core.pending()
    assert [row["request_ref"] for row in first] == refs
    assert len(reads) == 3

    reads.clear()
    second = core.pending()
    assert second == first
    assert reads == []

    # Mutating returned dicts in caller memory must not pollute subsequent reads.
    second[0]["status"] = "mutated"
    second[0]["source"]["source_ref"] = "tampered"
    third = core.pending()
    assert third[0]["status"] == "prepared"
    assert third[0]["source"]["source_ref"] == "source_one"
    assert reads == []

    # Replacing a file (even with preserved mtime) invalidates only that file.
    target = core.root / f"{refs[1]}.json"
    stamp = target.stat()
    _atomic_write_json(target, _native_request(refs[1], status="accepted"))
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    fourth = core.pending()
    assert fourth[1]["status"] == "accepted"
    assert reads == [target]

    # Deleting a file removes it without re-reading unchanged siblings.
    reads.clear()
    (core.root / f"{refs[0]}.json").unlink()
    fifth = core.pending()
    assert [row["request_ref"] for row in fifth] == [refs[1], refs[2]]
    assert reads == []
