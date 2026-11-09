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
import stat

import pytest

from loopx.control_plane.state_recovery_io import StateRecoveryError, _regular_input
from loopx.control_plane.turn_driver.codex_input_trace import read_native_trace_file
from loopx.hosts.checkpoint_retention import _sync

OWNER_MODEL = pytest.mark.skipif(
    getattr(os, "getuid", None) is None,
    reason="this host has no owner model to check the private regular file against",
)
POSIX_SYNC = pytest.mark.skipif(
    os.name != "posix",
    reason="_sync only qualifies a POSIX bank; other hosts are claimed by the no-op case",
)


def _symlink(path, target) -> None:
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a file symlink")


def test_native_trace_read_survives_a_host_without_the_posix_flags(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    trace.write_bytes(b'{"seq": 1}\n')
    with monkeypatch.context() as flags:
        flags.delattr(os, "O_NOFOLLOW", raising=False)
        flags.delattr(os, "O_NONBLOCK", raising=False)
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


@POSIX_SYNC
def test_checkpoint_sync_flushes_the_directory_without_the_flag(tmp_path, monkeypatch):
    flushed, real = [], os.fsync

    def recording(descriptor):
        flushed.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        return real(descriptor)

    monkeypatch.setattr(os, "fsync", recording)
    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)
    _sync(tmp_path)
    assert flushed == [True], "the directory must be flushed, not silently skipped"


@POSIX_SYNC
def test_checkpoint_sync_refuses_a_target_that_is_not_a_directory(tmp_path, monkeypatch):
    target = tmp_path / "bank.json"
    target.write_text("{}")
    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)
    with pytest.raises(ValueError, match="not a directory"):
        _sync(target)


def test_checkpoint_sync_claims_nothing_off_posix(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(os, "fsync", lambda descriptor: calls.append(descriptor))
    monkeypatch.setattr(os, "name", "nt")
    target = tmp_path / "bank.json"
    target.write_text("{}")
    _sync(tmp_path)
    _sync(target)
    assert calls == [], "a host outside the POSIX contract must not report a flush"


def test_checkpoint_sync_still_syncs_where_the_directory_flag_exists(tmp_path, monkeypatch):
    if not getattr(os, "O_DIRECTORY", 0):
        pytest.skip("this host does not define O_DIRECTORY")
    synced, real = [], os.fsync
    monkeypatch.setattr(os, "fsync", lambda descriptor: synced.append(descriptor) or real(descriptor))
    _sync(tmp_path)
    assert len(synced) == 1


def _swap_at_open(monkeypatch, path, replacement):
    """Replace `path` with a link to `replacement` the instant it is opened."""
    real_open = os.open

    def swapping(target, flags, *args, **kwargs):
        if os.fspath(target) == os.fspath(path) and not os.path.islink(path):
            path.unlink()
            try:
                path.symlink_to(replacement)
            except (OSError, NotImplementedError):
                pytest.skip("this host cannot create a file symlink")
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping)


def test_native_trace_read_refuses_a_link_swapped_in_at_the_open(tmp_path, monkeypatch):
    """Binding the descriptor refuses the entry the check-then-open window admits."""
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b'{"external": true}\n')
    trace = tmp_path / "trace.jsonl"
    trace.write_bytes(b'{"seq": 1}\n')
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    _swap_at_open(monkeypatch, trace, outside)
    with pytest.raises(ValueError, match="symlink"):
        read_native_trace_file(trace)


def test_native_trace_read_refuses_the_same_swap_with_the_flag(tmp_path, monkeypatch):
    """The native flag refuses it in the open, so the no-flag branch must match."""
    if not getattr(os, "O_NOFOLLOW", 0):
        pytest.skip("this host has no no-follow flag to compare against")
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b'{"external": true}\n')
    trace = tmp_path / "trace.jsonl"
    trace.write_bytes(b'{"seq": 1}\n')
    _swap_at_open(monkeypatch, trace, outside)
    with pytest.raises(OSError):
        read_native_trace_file(trace)


@OWNER_MODEL
def test_recovery_readback_refuses_a_link_swapped_in_at_the_open(tmp_path, monkeypatch):
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"external": true}')
    outside.chmod(0o600)
    payload = tmp_path / "recovery-audit.json"
    payload.write_bytes(b'{"ok": false}')
    payload.chmod(0o600)
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    _swap_at_open(monkeypatch, payload, outside)
    with pytest.raises(StateRecoveryError, match="recovery_input_unavailable"):
        with _regular_input(payload, 1024):
            pass
