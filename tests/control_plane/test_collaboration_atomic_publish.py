"""Windows record publication keeps complete bytes during ordinary observation."""
import json
import os
from threading import Timer
import time

import pytest

from loopx.control_plane.collaboration.inbox import _read, _write
from loopx.control_plane.runtime.file_paths import windows_extended_path


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-sharing boundary")
def test_observer_releases_file_before_atomic_publish_deadline(tmp_path):
    # Real CRT read handles deny DELETE sharing. Preserve the full opaque
    # storage address, including when the native path exceeds MAX_PATH.
    path = windows_extended_path(tmp_path / ("a" * 64) / ("b" * 64) / "operation.json")
    old, new = {"status": "prepared"}, {"status": "running", "complete": True}
    _write(path, old)
    reader = path.open("rb")
    release = Timer(0.15, reader.close)
    release.start()
    try:
        _write(path, new)
    finally:
        release.join()
        reader.close()
    assert _read(path) == new
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-sharing boundary")
def test_persistent_observer_denial_preserves_old_complete_record(tmp_path):
    path = tmp_path / "operation.json"
    old = {"status": "prepared", "identity": "original"}
    _write(path, old)
    begin = time.monotonic()
    with path.open("rb") as reader:
        with pytest.raises(PermissionError) as caught:
            _write(path, {"status": "running"})
        assert caught.value.winerror == 5
        assert json.loads(reader.read()) == old
    assert time.monotonic() - begin < 2, "access denial must remain bounded"
    assert _read(path) == old
    assert list(tmp_path.iterdir()) == [path]


def test_other_publish_error_propagates_without_changing_record(tmp_path, monkeypatch):
    from loopx.control_plane.collaboration import inbox

    path = tmp_path / "operation.json"
    old = {"status": "prepared"}
    _write(path, old)
    calls = []

    def reject(source, target):
        calls.append((source, target))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(inbox.os, "replace", reject)
    with pytest.raises(OSError, match="No space left"):
        _write(path, {"status": "running"})
    assert len(calls) == 1
    assert _read(path) == old
    assert list(tmp_path.iterdir()) == [path]
