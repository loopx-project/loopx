"""Collection keeps the native extraction, privacy and cleanup contracts."""
import errno
import io
import json
import tarfile
from pathlib import PurePosixPath
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


def test_research_collection_respects_disk_floor_and_filters_ephemeral_dumps(tmp_path, monkeypatch):
    elf_core = b"\x7fELF\x02\x01" + b"\x00" * 10 + (4).to_bytes(2, "little") + b"\x00" * 46
    elf_exec = b"\x7fELF\x02\x01" + b"\x00" * 10 + (2).to_bytes(2, "little") + b"\x00" * 46
    macho_core = b"\xcf\xfa\xed\xfe" + b"\x00" * 8 + (4).to_bytes(4, "little") + b"\x00" * 16
    payload_buf = io.BytesIO()
    with tarfile.open(fileobj=payload_buf, mode="w") as archive:
        for rel, body in (
            ("loopx-benchmark-research/candidate.py", b"print('ok')\n"),
            ("loopx-benchmark-research/core/analysis.md", b"# real notes\n"),
            ("loopx-benchmark-research/topic/core", b"plain topic evidence\n"),
            ("loopx-benchmark-research/exec_dir/core", elf_exec),
            ("loopx-benchmark-research/dump.raw.gz", b"gz" * 100),
            ("loopx-benchmark-research/core", elf_core),
            ("loopx-benchmark-research/sub/core.99", macho_core),
            ("loopx-benchmark-research/target/release/bin", b"elf"),
            ("loopx-benchmark-research/.cargo-target/x.rlib", b"rlib"),
        ):
            info = tarfile.TarInfo(rel)
            info.size = len(body)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(body))
    research_tar = payload_buf.getvalue()

    def build_backend(name: str):
        backend = object.__new__(transport.RecordingDockerBackend)
        backend.checkpoint = backend.feedback = None
        backend.research_sidecar = True
        backend.log_dir = tmp_path / name
        monkeypatch.setattr(transport.DockerBackend, "cleanup_container", lambda *a: None)

        def get_archive(remote):
            data = research_tar if "research" in str(remote) else archive_bytes()
            return iter([data]), {}

        monkeypatch.setattr(backend, "_raw", lambda handle: SimpleNamespace(get_archive=get_archive))
        return backend

    # Below 10 GiB headroom floor: skip research archive before calling get_archive.
    low_backend = build_backend("low-disk")
    monkeypatch.setattr(
        transport, "_minimum_free_bytes", lambda *_: transport._RESEARCH_DISK_HEADROOM_FLOOR_BYTES
    )
    low_backend.cleanup_container("owned-container")
    low_obs = json.loads((low_backend.log_dir / "artifact-collection.json").read_text())
    research_entry = [row for row in low_obs if row["artifact"] == "research"][0]
    assert research_entry == {
        "artifact": "research",
        "collected": False,
        "error_kind": "InsufficientDiskHeadroomError",
        "reason": "insufficient_host_disk_headroom",
    }

    # Above 10 GiB floor: keep real notes/non-dump files named core while stripping confirmed dumps/build dirs.
    ok_backend = build_backend("ok-disk")
    monkeypatch.setattr(
        transport, "_minimum_free_bytes", lambda *_: transport._RESEARCH_DISK_HEADROOM_FLOOR_BYTES + 100_000_000
    )
    ok_backend.cleanup_container("owned-container")
    root = ok_backend.log_dir / "research" / "loopx-benchmark-research"
    assert (root / "candidate.py").read_bytes() == b"print('ok')\n"
    assert (root / "core/analysis.md").read_bytes() == b"# real notes\n"
    assert (root / "topic/core").read_bytes() == b"plain topic evidence\n"
    assert (root / "exec_dir/core").read_bytes() == elf_exec
    assert not (root / "dump.raw.gz").exists()
    assert not (root / "core").is_file()
    assert not (root / "sub/core.99").exists()
    assert not (root / "target").exists()
    assert not (root / ".cargo-target").exists()


@pytest.mark.parametrize("data_name", ["core", "evidence.bin"])
def test_research_hardlinks_keep_the_same_evidence_in_terminal_receipt(tmp_path, monkeypatch, data_name):
    from benchmark.runtime.research_artifacts import RESEARCH_DIRECTORY

    root = PurePosixPath(RESEARCH_DIRECTORY).name
    names = ["core", "core.5", "evidence.bin", "another-evidence.bin"]
    content = b"\x7fELF\x02\x01" + bytes(10) + (4).to_bytes(2, "little") + bytes(46)
    data = tmp_path / data_name
    data.write_bytes(content)
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        archive.add(data, arcname=f"{root}/{data_name}")
        for name in names:
            if name != data_name:
                alias = tmp_path / name
                alias.hardlink_to(data)
                archive.add(alias, arcname=f"{root}/{name}")

    backend = collector(tmp_path)
    backend.research_sidecar = True
    monkeypatch.setattr(transport, "_minimum_free_bytes", lambda *_: 20 * 1024**3)
    monkeypatch.setattr(transport.DockerBackend, "cleanup_container", lambda *args: None)

    def get_archive(remote):
        return iter([payload.getvalue() if remote == RESEARCH_DIRECTORY else archive_bytes()]), {}

    monkeypatch.setattr(backend, "_raw", lambda _: SimpleNamespace(get_archive=get_archive))
    backend.cleanup_container("owned-container")

    receipt = json.loads((backend.log_dir / "artifact-collection.json").read_text())
    assert {"artifact": "research", "collected": True} in receipt
    collected = backend.log_dir / "research" / root
    assert sorted(p.name for p in collected.iterdir()) == ["another-evidence.bin", "evidence.bin"]
    assert (collected / "evidence.bin").read_bytes() == content
    assert (collected / "another-evidence.bin").samefile(collected / "evidence.bin")
