"""Readers keep working where the host does not define a POSIX-only attribute.

`os.O_NOFOLLOW`, `os.O_NONBLOCK`, `os.O_DIRECTORY` and `os.getuid` are POSIX
attributes. Removing one for the duration of a single call is what
`getattr(os, "<attr>", ...)` sees on a host that lacks it, and it is the same
branch a native Windows host takes. Each reader must refuse on its own terms
there: an untyped AttributeError, or an open that quietly follows a symlink,
would replace a diagnosis the caller asked for.
"""

from __future__ import annotations

import os

import pytest

from loopx.control_plane.state_recovery_io import StateRecoveryError, _regular_input
from loopx.control_plane.turn_driver.codex_input_trace import read_native_trace_file
from loopx.hosts.checkpoint_retention import _sync

OWNER_MODEL = pytest.mark.skipif(
    getattr(os, "getuid", None) is None,
    reason="this host has no owner model to check the private regular file against",
)


def _symlink(path, target) -> None:
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a file symlink")


def test_native_trace_read_survives_a_host_without_the_posix_flags(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    trace.write_bytes(b'{"seq": 1}\n')
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)
    assert read_native_trace_file(trace) == b'{"seq": 1}\n'


def test_native_trace_read_refuses_a_symlink_without_the_flag(tmp_path, monkeypatch):
    original = tmp_path / "original.jsonl"
    original.write_bytes(b'{"seq": 1}\n')
    trace = tmp_path / "trace.jsonl"
    _symlink(trace, original)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(ValueError, match="symlink"):
        read_native_trace_file(trace)


@OWNER_MODEL
def test_recovery_readback_refuses_a_host_without_an_owner_model(tmp_path, monkeypatch):
    payload = tmp_path / "recovery-audit.json"
    payload.write_bytes(b'{"ok": false}')
    payload.chmod(0o600)
    monkeypatch.delattr(os, "getuid", raising=False)
    with pytest.raises(StateRecoveryError, match="recovery_input_unavailable"):
        with _regular_input(payload, 1024):
            pass


@OWNER_MODEL
def test_recovery_readback_survives_a_host_without_the_no_follow_flag(tmp_path, monkeypatch):
    payload = tmp_path / "recovery-audit.json"
    payload.write_bytes(b'{"ok": false}')
    payload.chmod(0o600)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with _regular_input(payload, 1024) as handle:
        assert handle.read() == b'{"ok": false}'


@OWNER_MODEL
def test_recovery_readback_refuses_a_symlink_without_the_flag(tmp_path, monkeypatch):
    original = tmp_path / "original.json"
    original.write_bytes(b'{"ok": false}')
    original.chmod(0o600)
    payload = tmp_path / "recovery-audit.json"
    _symlink(payload, original)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(StateRecoveryError, match="recovery_input_unavailable"):
        with _regular_input(payload, 1024):
            pass


def test_checkpoint_sync_skips_a_host_without_the_directory_flag(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(os, "fsync", synced.append)
    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)
    _sync(tmp_path)
    assert synced == []


def test_checkpoint_sync_still_syncs_where_the_directory_flag_exists(tmp_path, monkeypatch):
    if not getattr(os, "O_DIRECTORY", 0):
        pytest.skip("this host does not define O_DIRECTORY")
    synced, real = [], os.fsync
    monkeypatch.setattr(os, "fsync", lambda descriptor: synced.append(descriptor) or real(descriptor))
    _sync(tmp_path)
    assert len(synced) == 1
