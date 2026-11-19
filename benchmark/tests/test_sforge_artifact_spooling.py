"""Collection keeps the native extraction, privacy and cleanup contracts."""
import errno
import io
import json
import tarfile
from types import SimpleNamespace

import pytest
import requests

pytest.importorskip("sforge")
pytest.importorskip("harbor")
from benchmark.runtime import sforge_backend as transport


def archive_bytes(*, unsafe=False):
    with io.BytesIO() as payload:
        with tarfile.open(fileobj=payload, mode="w") as archive:
            file = tarfile.TarInfo("artifact/nested/data")
            file.mode = 0o640
            file.size = 7
            archive.addfile(file, io.BytesIO(b"payload"))
            link = tarfile.TarInfo("artifact/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "/outside" if unsafe else "nested/data"
            archive.addfile(link)
        return payload.getvalue()


def collector(tmp_path):
    backend = object.__new__(transport.RecordingDockerBackend)
    backend.checkpoint = backend.feedback = None
    backend.research_sidecar = False
    backend.log_dir = tmp_path / "collected"
    return backend


@pytest.mark.parametrize("failure", [None, "http", "space", "unsafe-link"])
def test_collection_spool_closes_with_original_error_record_and_native_cleanup(
        tmp_path, monkeypatch, failure):
    backend = collector(tmp_path)
    payload = archive_bytes(unsafe=failure == "unsafe-link")
    streams, spools, cleaned = [], [], []

    def get_archive(remote):
        def chunks():
            try:
                yield payload[:512]
                if failure == "http":
                    raise requests.ConnectionError("synthetic interrupted download")
                yield payload[512:]
            finally:
                streams.append(remote)
        return chunks(), {}

    monkeypatch.setattr(backend, "_raw", lambda handle: SimpleNamespace(get_archive=get_archive))
    monkeypatch.setattr(backend, "copy_from_container", lambda *args: pytest.fail("bytes API used"))
    monkeypatch.setattr(transport.DockerBackend, "cleanup_container",
                        lambda self, handle, logger: cleaned.append(handle))
    real = transport.tempfile.TemporaryFile

    def temporary():
        spool = real(dir=tmp_path)
        spools.append(spool)
        if failure == "space":
            def full(*args):
                raise OSError(errno.ENOSPC, "synthetic spool full")
            monkeypatch.setattr(spool, "write", full)
        return spool

    monkeypatch.setattr(transport.tempfile, "TemporaryFile", temporary)
    backend.cleanup_container("owned-container")
    assert cleaned == ["owned-container"]
    assert len(streams) == len(spools) == 5
    assert all(spool.closed for spool in spools)
    observed = json.loads((backend.log_dir / "artifact-collection.json").read_text())
    assert all(x["collected"] is (failure is None) for x in observed)
    if failure:
        expected = {"http": "ConnectionError", "space": "OSError", "unsafe-link": "AbsoluteLinkError"}
        assert {x["error_kind"] for x in observed} == {expected[failure]}
        assert not (tmp_path / "outside").exists()
        if failure in ("http", "space"):
            assert not (backend.log_dir / "runtime").exists()
    else:
        file = backend.log_dir / "runtime/artifact/nested/data"
        assert file.read_bytes() == b"payload"
        assert file.stat().st_mode & 0o777 == 0o640
        assert (file.parents[1] / "link").is_symlink()


def test_native_retention_still_avoids_normal_stop_and_removal(tmp_path, monkeypatch):
    backend = collector(tmp_path)
    calls = []
    preservation = SimpleNamespace(retain_source=True, stop_source=lambda handle: calls.append("retain"))
    backend.checkpoint = SimpleNamespace(preservation=preservation, close=lambda: calls.append("close"))
    monkeypatch.setattr(backend, "_raw", lambda handle: SimpleNamespace(
        get_archive=lambda remote: (iter([archive_bytes()]), {})))
    monkeypatch.setattr(transport.DockerBackend, "cleanup_container",
                        lambda *args: pytest.fail("retained source removed"))
    backend.cleanup_container(SimpleNamespace(raw=object()))
    assert calls == ["retain", "close", "retain"]


def test_collection_failure_does_not_release_online_capacity_while_container_remains(
        tmp_path, monkeypatch):
    backend = collector(tmp_path)
    calls = []
    backend.feedback = SimpleNamespace(close=lambda: calls.append("observer-close"),
        release_registration=lambda: calls.append("released"))
    monkeypatch.setattr(backend, "_raw", lambda handle: SimpleNamespace(
        get_archive=lambda remote: (_ for _ in ()).throw(requests.ConnectionError("synthetic"))))
    monkeypatch.setattr(transport.DockerBackend, "cleanup_container", lambda *args: calls.append("native-cleanup"))
    monkeypatch.setattr(backend, "container_exists", lambda name: True)
    with pytest.raises(RuntimeError, match="capacity was not released"):
        backend.cleanup_container(SimpleNamespace(name="owned-container"))
    assert calls == ["observer-close", "native-cleanup"]


def test_interrupted_stream_closes_spool_and_still_calls_native_cleanup(tmp_path, monkeypatch):
    backend = collector(tmp_path)
    closed, cleaned, spools = [], [], []

    def chunks():
        try:
            yield b"partial archive"
            raise KeyboardInterrupt
        finally:
            closed.append(True)

    monkeypatch.setattr(backend, "_raw", lambda handle: SimpleNamespace(
        get_archive=lambda remote: (chunks(), {})))
    real = transport.tempfile.TemporaryFile

    def temporary():
        file = real(dir=tmp_path)
        spools.append(file)
        return file

    monkeypatch.setattr(transport.tempfile, "TemporaryFile", temporary)
    monkeypatch.setattr(transport.DockerBackend, "cleanup_container",
                        lambda self, handle, logger: cleaned.append(handle))
    with pytest.raises(KeyboardInterrupt):
        backend.cleanup_container("owned-container")
    assert closed == [True] and cleaned == ["owned-container"]
    assert len(spools) == 1 and spools[0].closed


@pytest.mark.parametrize("remains", [False, True])
def test_native_cleanup_error_keeps_original_capacity_release_rule(tmp_path, monkeypatch, remains):
    backend = collector(tmp_path)
    calls = []
    backend.feedback = SimpleNamespace(close=lambda: calls.append("observer-close"),
        release_registration=lambda: calls.append("released"))
    monkeypatch.setattr(backend, "_raw", lambda handle: SimpleNamespace(
        get_archive=lambda remote: (_ for _ in ()).throw(requests.ConnectionError("synthetic"))))

    def native_cleanup(*args):
        calls.append("native-cleanup")
        raise OSError("synthetic native removal failure")

    monkeypatch.setattr(transport.DockerBackend, "cleanup_container", native_cleanup)
    monkeypatch.setattr(backend, "container_exists", lambda name: remains)
    with pytest.raises(RuntimeError if remains else OSError):
        backend.cleanup_container(SimpleNamespace(name="owned-container"))
    assert calls == ["observer-close", "native-cleanup"] + ([] if remains else ["released"])
