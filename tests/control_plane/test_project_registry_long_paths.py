"""Logical registry identities retain real native long-path IO semantics."""

import json
from pathlib import Path

import pytest

from loopx.control_plane.projects import registry_codec as codec
from loopx.control_plane.runtime.file_paths import windows_extended_path
from tests.control_plane.test_project_registry_codec import _strict


def _target(root):
    while 293 - len(str(root / "registry.json")) > 121:
        root = root / ("p" * 100)
    root = root / ("p" * (293 - len(str(root / "registry.json")) - 1))
    path = root / "registry.json"
    assert len(str(path)) == 293 and not str(path).startswith("\\\\?\\")
    return path


def _seed(path, strict):
    payload = {"goals": [], "value": "before", "logical_registry": str(path)}
    raw = (json.dumps(_strict(payload) if strict else payload, indent=2) + "\n").encode()
    address = windows_extended_path(path)
    address.parent.mkdir(parents=True)
    address.write_bytes(raw)
    return payload, raw


@pytest.mark.parametrize("strict", [False, True])
def test_long_registry_commit_keeps_logical_path_and_wire_format(tmp_path, strict):
    path = _target(tmp_path)
    before, _ = _seed(path, strict)
    mode = windows_extended_path(path).stat().st_mode & 0o777
    codec.mutate_project_registry(path, operation="long_commit", reducer=lambda p: p.update(value="after"))
    assert codec.load_project_registry(path) == dict(before, value="after")
    address = windows_extended_path(path)
    assert isinstance(json.loads(address.read_bytes()), list) == strict
    assert address.stat().st_mode & 0o777 == mode
    assert not list(address.parent.glob(".registry.json.*.tmp"))


@pytest.mark.parametrize("strict", [False, True])
def test_long_registry_failed_readback_restores_original_bytes(tmp_path, monkeypatch, strict):
    path = _target(tmp_path)
    before, raw = _seed(path, strict)
    original = codec._read_document
    calls = 0

    def fail_once(candidate):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected post-write readback failure")
        return original(candidate)

    monkeypatch.setattr(codec, "_read_document", fail_once)
    with pytest.raises(codec.ProjectRegistryMutationError, match="exact preimage restored"):
        codec.mutate_project_registry(path, operation="long_restore", reducer=lambda p: p.update(value="unaccepted"))
    assert windows_extended_path(path).read_bytes() == raw
    assert codec.load_project_registry(path) == before
    assert not list(windows_extended_path(path.parent).glob(".registry.json.*.tmp"))


def test_long_registry_creation_and_removal(tmp_path):
    path = _target(tmp_path)
    payload = {"goals": [], "logical_registry": str(path)}
    with codec.project_registry_transaction(path, operation="long_create", create=lambda: payload) as tx:
        assert tx.commit(tx.payload_copy())
    assert codec.load_project_registry(path) == payload
    with codec.project_registry_transaction(path, operation="long_remove") as tx:
        assert tx.remove()
    assert not windows_extended_path(path).exists()


def test_long_registry_unknown_protocol_rejects_without_changes(tmp_path):
    path = _target(tmp_path)
    address = windows_extended_path(path)
    address.parent.mkdir(parents=True)
    raw = (json.dumps(_strict({"goals": []}, protocol="future_writer")) + "\n").encode()
    address.write_bytes(raw)
    with pytest.raises(codec.ProjectRegistryProtocolError):
        codec.mutate_project_registry(path, operation="long_reject", reducer=lambda p: p.update(value="forbidden"))
    assert address.read_bytes() == raw
