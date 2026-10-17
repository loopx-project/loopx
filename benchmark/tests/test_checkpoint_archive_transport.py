"""Docker archive storage and reader lifetime belong to the owned export."""
from __future__ import annotations

import base64
import errno
import io
import json
import tarfile
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

    def iter_content(self, *args, **kwargs):
        return iter(self)

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
        _stream_raw_result=lambda *args: pytest.fail("archive must retain its socket timeout"))

    class Container:
        id = "synthetic"
        client = SimpleNamespace(api=api)

        def get_archive(self, path):
            return stream, stat

    if failure == "disk":
        @contextmanager
        def full_disk(**kwargs):
            raise OSError(errno.ENOSPC, "synthetic full temporary volume")
            yield
        monkeypatch.setattr(worker.tempfile, "TemporaryFile", full_disk)
    expected = {"linked": ValueError, "transport": OSError, "disk": OSError,
                "cancel": KeyboardInterrupt, "invalid_tar": worker.tarfile.ReadError}[failure]
    with pytest.raises(expected):
        worker._archive(Container(), "/data", tmp_path / "export")
    assert stream.closed, "a live daemon reader can block this container's release"


def test_export_storage_does_not_require_ambient_temporary_storage(tmp_path, monkeypatch):
    """An explicitly provisioned export volume owns its complete transfer."""
    unavailable = tmp_path / "unavailable-temp"
    unavailable.write_bytes(b"not a directory")
    monkeypatch.setattr(worker.tempfile, "tempdir", str(unavailable))
    payload = b"retained source bytes"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        entry = tarfile.TarInfo("data")
        entry.size, entry.mode = len(payload), 0o600
        archive.addfile(entry, io.BytesIO(payload))
    response = SimpleNamespace(headers={"x-docker-container-path-stat":
        base64.b64encode(json.dumps({"linkTarget": ""}).encode()).decode()}, closed=False)
    response.close = lambda: setattr(response, "closed", True)
    response.iter_content = lambda *args, **kwargs: iter([raw.getvalue()])
    api = SimpleNamespace(_url=lambda *args: "archive", _get=lambda *args, **kwargs: response,
        _raise_for_status=lambda value: None,
        _stream_raw_result=lambda *args: pytest.fail("archive must retain its socket timeout"))
    container = SimpleNamespace(id="synthetic", client=SimpleNamespace(api=api))

    destination = tmp_path / "provisioned-export"
    copied = worker._archive(container, "/data", destination)

    assert copied.read_bytes() == payload and copied.stat().st_mode & 0o777 == 0o600
    assert list(destination.iterdir()) == [copied], "no raw transfer file may escape its lifetime"
    assert response.closed
    assert unavailable.read_bytes() == b"not a directory"


def _tar_transport(payload):
    response = SimpleNamespace(headers={"x-docker-container-path-stat":
        base64.b64encode(json.dumps({"linkTarget": ""}).encode()).decode()}, closed=False)
    response.close = lambda: setattr(response, "closed", True)
    response.iter_content = lambda *args, **kwargs: iter([payload])
    api = SimpleNamespace(_url=lambda *args: "archive", _get=lambda *args, **kwargs: response,
        _raise_for_status=lambda value: None,
        _stream_raw_result=lambda *args: pytest.fail("archive must retain its socket timeout"))
    return SimpleNamespace(id="synthetic", client=SimpleNamespace(api=api)), response


def _research_tar():
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        directory = tarfile.TarInfo("data")
        directory.type, directory.mode = tarfile.DIRTYPE, 0o700
        archive.addfile(directory)
        for name in ("first", "second"):
            member = tarfile.TarInfo("data/" + name)
            member.size, member.mode = 256 * 1024, 0o600
            archive.addfile(member, io.BytesIO(b"x" * member.size))
    return raw.getvalue()


@pytest.mark.parametrize("stage", ["download", "headers", "file_read", "filter", "modes"])
def test_single_archive_stops_at_shared_deadline_and_closes_reader(tmp_path, monkeypatch, stage):
    """Progress in one component cannot renew the original capture clock."""
    container, response = _tar_transport(_research_tar())
    now = [100.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: now[0])
    triggered = []
    if stage == "download":
        def download(*args, **kwargs):
            yield b"first block"
            triggered.append(True)
            now[0] = 102
            yield b"must not be written"
            pytest.fail("expired stream must not be consumed further")
        response.iter_content = download
    elif stage in {"headers", "file_read"}:
        original = worker.tempfile.TemporaryFile
        @contextmanager
        def delayed_read(**kwargs):
            with original(**kwargs) as raw:
                class SlowRead:
                    def __getattr__(self, name):
                        return getattr(raw, name)
                    def read(self, size=-1):
                        # Header scan reads 512-byte records; file extraction
                        # reads blocks after validating the complete inventory.
                        data = raw.read(size)
                        if (stage == "headers" and size == 512 or
                                stage == "file_read" and size > 512):
                            triggered.append(True)
                            now[0] = 102
                        return data
                yield SlowRead()
        monkeypatch.setattr(worker.tempfile, "TemporaryFile", delayed_read)
    elif stage == "filter":
        original = worker.tarfile.data_filter
        def slow_filter(*args):
            member = original(*args)
            triggered.append(True)
            now[0] = 102
            return member
        monkeypatch.setattr(worker.tarfile, "data_filter", slow_filter)
    else:
        original = worker.tarfile.TarFile.extractall
        def finished_extraction(*args, **kwargs):
            original(*args, **kwargs)
            triggered.append(True)
            now[0] = 102
        monkeypatch.setattr(worker.tarfile.TarFile, "extractall", finished_extraction)
    with pytest.raises(TimeoutError, match="capture window closed"):
        worker._archive(container, "/data", tmp_path / "expired", deadline=101)
    assert triggered and response.closed


def test_expired_archive_does_not_open_a_daemon_reader(tmp_path, monkeypatch):
    container, response = _tar_transport(_research_tar())
    container.client.api._get = lambda *args, **kwargs: pytest.fail("expired operation must not start IO")
    monkeypatch.setattr(worker.time, "monotonic", lambda: 2)
    with pytest.raises(TimeoutError, match="capture window closed"):
        worker._archive(container, "/data", tmp_path / "expired", deadline=1)
    assert not (tmp_path / "expired").exists()


@pytest.mark.parametrize("unsafe", ["absolute", "parent", "foreign", "duplicate", "symlink", "hardlink", "fifo"])
def test_archive_rejects_unsafe_inventory_before_extracting_any_file(tmp_path, unsafe):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        first = tarfile.TarInfo("data/retained")
        first.size = 4
        archive.addfile(first, io.BytesIO(b"safe"))
        second = tarfile.TarInfo({"absolute": "/escape", "parent": "data/../escape",
            "foreign": "elsewhere/file", "duplicate": "data/retained"}.get(unsafe, "data/unsafe"))
        if unsafe in {"symlink", "hardlink", "fifo"}:
            second.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                           "fifo": tarfile.FIFOTYPE}[unsafe]
            second.linkname = "data/retained"
        archive.addfile(second)
    container, response = _tar_transport(raw.getvalue())
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError, match="undeclared, linked or special"):
        worker._archive(container, "/data", destination)
    assert response.closed and not (destination / "data").exists()
