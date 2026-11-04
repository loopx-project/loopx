"""Windows atomic holder publication with real file-sharing boundaries."""
from contextlib import contextmanager
import ctypes as ct
from ctypes import wintypes as w
import errno
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
import loopx.file_lock as locks

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing boundary')

@contextmanager
def reader_without_delete_share(path):
    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,ct.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateFileW(str(path),0x80000000,0x3,None,3,0,None)
    assert handle != ct.c_void_p(-1).value
    closed = False
    def close():
        nonlocal closed
        if not closed:
            closed = True
            assert kernel.CloseHandle(handle)
    try:
        yield close
    finally:
        close()

@contextmanager
def transient_reader(path, monkeypatch):
    attempts = []
    first_attempt = threading.Event()
    replace = os.replace
    def observed_replace(source,destination):
        if Path(destination) == path:
            attempts.append(time.monotonic())
            first_attempt.set()
        return replace(source,destination)
    with reader_without_delete_share(path) as close:
        def release():
            first_attempt.wait(2)
            time.sleep(0.1)
            close()
        worker = threading.Thread(target=release)
        worker.start()
        monkeypatch.setattr(locks.os,'replace',observed_replace)
        try:
            yield attempts
        finally:
            first_attempt.set()
            worker.join(3)
            assert not worker.is_alive()

def test_transient_reader_allows_atomic_holder_replace(tmp_path,monkeypatch):
    path = tmp_path / "holder.json"
    path.write_text(json.dumps({"sequence": 0}))
    with transient_reader(path,monkeypatch) as attempts:
        locks._write_holder_sidecar(path,{'sequence':1})
    assert len(attempts)>=2
    assert json.loads(path.read_text())=={'sequence':1}
    assert not list(tmp_path.glob('*.tmp'))

def test_transient_reader_preserves_real_kernel_lock_body(tmp_path,monkeypatch):
    target=tmp_path/'state.json'
    with locks.exclusive_file_lock(target):
        pass
    holder = locks.lock_holder_path(target)
    effects = []
    with transient_reader(holder,monkeypatch):
        with locks.exclusive_file_lock(target,agent_id='writer',operation='atomic-publish'):
            effects.append('business')
    assert effects==['business']
    assert json.loads(holder.read_text())['released_at']
    with locks.try_exclusive_file_lock(target) as acquired:
        assert acquired is not None


def test_persistent_reader_keeps_old_bytes_and_propagates_denial(tmp_path):
    path = tmp_path / "holder.json"
    path.write_text(json.dumps({"sequence": 0}))
    before = path.read_bytes()
    with reader_without_delete_share(path):
        start=time.monotonic()
        with pytest.raises(OSError) as failure:
            locks._write_holder_sidecar(path, {"sequence": 1})
        assert failure.value.winerror==5 and time.monotonic()-start<1.2
        assert path.read_bytes()==before
        assert not list(tmp_path.glob('*.tmp'))

def test_failed_holder_publication_releases_kernel_without_business(tmp_path):
    target=tmp_path/'state.json'
    with locks.exclusive_file_lock(target):
        pass
    holder = locks.lock_holder_path(target)
    effects = []
    with reader_without_delete_share(holder):
        start=time.monotonic()
        with pytest.raises(OSError) as failure:
            with locks.exclusive_file_lock(target):
                effects.append("business")
        assert failure.value.winerror==5 and time.monotonic()-start<2
        assert effects==[]
    with locks.try_exclusive_file_lock(target) as acquired:
        assert acquired is not None
    assert not list(tmp_path.glob('*.tmp'))

def test_unrelated_error_has_no_retry_or_fallback(tmp_path,monkeypatch):
    attempts=[]
    def fail(source,destination):
        attempts.append(1)
        raise FileNotFoundError(errno.ENOENT,'unrelated source error')
    monkeypatch.setattr(locks.os,'replace',fail)
    monkeypatch.setattr(locks.time,'sleep',lambda _:pytest.fail('unrelated error slept'))
    with pytest.raises(FileNotFoundError):
        locks._write_holder_sidecar(tmp_path / "holder.json", {"sequence": 1})
    assert attempts==[1] and not list(tmp_path.glob('*.tmp'))

def test_successful_holder_publication_does_not_sleep(tmp_path,monkeypatch):
    path = tmp_path / "holder.json"
    path.write_text(json.dumps({"sequence": 0}))
    monkeypatch.setattr(locks.time,'sleep',lambda _:pytest.fail('success slept'))
    locks._write_holder_sidecar(path,{'sequence':1})
    assert json.loads(path.read_text())=={'sequence':1}

def test_posix_adapter_permission_error_remains_immediate(tmp_path,monkeypatch):
    attempts=[]
    def fail(source,destination):
        attempts.append(1)
        error = PermissionError(errno.EACCES, "denied")
        error.winerror = 5
        raise error

    adapter=SimpleNamespace(**{name:getattr(os,name) for name in ('fdopen','fsync')},name='posix',replace=fail)
    monkeypatch.setattr(locks,'os',adapter)
    monkeypatch.setattr(locks.time,'sleep',lambda _:pytest.fail('POSIX denial slept'))
    with pytest.raises(PermissionError):
        locks._write_holder_sidecar(tmp_path / "holder.json", {"sequence": 1})
    assert attempts==[1] and not list(tmp_path.glob('*.tmp'))
