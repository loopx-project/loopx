"""Native read-only handle semantics, independent of activity classification."""

import os

import pytest

from loopx.hosts.codex_rollout_file import open_rollout


def test_reader_is_binary_read_only_and_not_inherited(tmp_path):
    path = tmp_path / "rollout 中文.jsonl"
    content = b"old\r\n\x1a\x00\xff"
    path.write_bytes(content)
    with open_rollout(path) as reader:
        assert not os.get_inheritable(reader.fileno())
        assert not reader.writable()
        with pytest.raises(OSError):
            os.write(reader.fileno(), b"changed")
        assert reader.read() == content
    assert reader.closed
    assert path.read_bytes() == content


def test_host_can_rename_an_open_rollout_and_publish_a_new_one(tmp_path):
    path = tmp_path / "rollout 中文.jsonl"
    rotated = tmp_path / "previous.jsonl"
    path.write_bytes(b"old")
    with open_rollout(path) as reader:
        path.rename(rotated)
        path.write_bytes(b"new")
        assert reader.read() == b"old"
        assert path.read_bytes() == b"new"
    assert rotated.read_bytes() == b"old"


def test_missing_rollout_is_not_created(tmp_path):
    missing = tmp_path / "missing.jsonl"
    with pytest.raises(FileNotFoundError):
        open_rollout(missing)
    assert not missing.exists()


def test_exception_closes_reader_without_deleting_source(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(b"retained")
    with pytest.raises(ArithmeticError):
        with open_rollout(path) as reader:
            raise ArithmeticError("synthetic consumer failure")
    assert reader.closed
    assert path.read_bytes() == b"retained"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows handle ownership")
@pytest.mark.parametrize("failure_at", ["descriptor", "stream"])
def test_native_handle_is_released_when_reader_construction_fails(tmp_path, monkeypatch, failure_at):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    information = kernel32.GetHandleInformation
    information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(b"retained")
    handles, descriptors = [], []
    original = msvcrt.open_osfhandle
    failure = OSError("synthetic reader construction failure")

    def adopt(handle, flags):
        handles.append(handle)
        assert information(handle, ctypes.byref(wintypes.DWORD()))
        if failure_at == "descriptor":
            raise failure
        descriptor = original(handle, flags)
        descriptors.append(descriptor)
        return descriptor

    def stream_failure(*args, **kwargs):
        raise failure

    monkeypatch.setattr(msvcrt, "open_osfhandle", adopt)
    if failure_at == "stream":
        monkeypatch.setattr(os, "fdopen", stream_failure)
    try:
        with pytest.raises(OSError) as caught:
            open_rollout(path)
        assert caught.value is failure
        assert len(handles) == 1
        assert not information(handles[0], ctypes.byref(wintypes.DWORD()))
        assert ctypes.get_last_error() == 6  # ERROR_INVALID_HANDLE
        assert path.read_bytes() == b"retained"
    finally:
        # A failing regression must not itself leak the real native resource.
        for handle in handles:
            if information(handle, ctypes.byref(wintypes.DWORD())):
                if descriptors:
                    os.close(descriptors[0])
                else:
                    close_handle(handle)
