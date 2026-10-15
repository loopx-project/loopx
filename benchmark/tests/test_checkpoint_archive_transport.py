"""The Docker reader must close before a failed export can release its writer."""
from __future__ import annotations

import base64
import errno
import json
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from benchmark.edgebench import checkpoint_worker as worker


class ArchiveStream:
    def __init__(self, error=None):
        self.error = error
        self.closed = False

    def __iter__(self):
        if self.error is not None:
            raise self.error
        yield b"incomplete archive"

    def close(self):
        self.closed = True


@pytest.mark.parametrize("failure", ["linked", "transport", "disk", "cancel", "invalid_tar"])
def test_failed_archive_always_closes_daemon_reader(tmp_path, monkeypatch, failure):
    error = {"transport": OSError("lost transport"), "cancel": KeyboardInterrupt()}.get(failure)
    stream = ArchiveStream(error)

    stat = {"linkTarget": "/elsewhere"} if failure == "linked" else {}
    stream.headers = {"x-docker-container-path-stat":
        base64.b64encode(json.dumps(stat).encode()).decode()}
    api = SimpleNamespace(_url=lambda *args: "archive", _get=lambda *args, **kwargs: stream,
        _raise_for_status=lambda response: None,
        _stream_raw_result=lambda response, *args: iter(response))

    class Container:
        id = "synthetic"
        client = SimpleNamespace(api=api)

        def get_archive(self, path):
            return stream, stat

    if failure == "disk":
        @contextmanager
        def full_disk():
            raise OSError(errno.ENOSPC, "synthetic full temporary volume")
            yield
        monkeypatch.setattr(worker.tempfile, "TemporaryFile", full_disk)
    expected = {"linked": ValueError, "transport": OSError, "disk": OSError,
                "cancel": KeyboardInterrupt, "invalid_tar": worker.tarfile.ReadError}[failure]
    with pytest.raises(expected):
        worker._archive(Container(), "/data", tmp_path / "export")
    assert stream.closed, "a live daemon reader can block this container's release"
