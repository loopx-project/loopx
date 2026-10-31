from __future__ import annotations

import hashlib
import json
import sys
import os
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime as runtime


def serial_fingerprint(root: Path) -> str:
    """Independent reference: selected adapter, release identity and source bytes."""
    digest = hashlib.sha256()
    digest.update(sys.executable.encode("utf-8"))
    digest.update(str(sys.version_info[:3]).encode("ascii"))
    digest.update(b"loopx_effect_runtime_source_instance_v1\0")
    digest.update(os.fsencode(os.path.normcase(os.fspath(root.resolve()))))
    digest.update(b"\0")
    for path in sorted(p for p in root.rglob("*") if p.suffix in {".ts", ".json"}):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_fingerprint_preserves_exact_bytes_and_observes_source_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: tmp_path)
    files = {"a.ts": b"// line\r\n", "nested/b.json": '{"text":"中文🙂"}'.encode(),
             "z.ts": b"// bytes\n\xff", "ignored.py": b"not runtime source"}
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    before = runtime._runtime_fingerprint()
    assert before == serial_fingerprint(tmp_path)
    (tmp_path / "a.ts").write_bytes(b"// line\n")
    modified = runtime._runtime_fingerprint()
    assert modified == serial_fingerprint(tmp_path) and modified != before
    (tmp_path / "new.ts").write_bytes(b"export {};\n")
    added = runtime._runtime_fingerprint()
    assert added == serial_fingerprint(tmp_path) and added != modified
    (tmp_path / "nested/b.json").unlink()
    removed = runtime._runtime_fingerprint()
    assert removed == serial_fingerprint(tmp_path) and removed != added
    (tmp_path / "ignored.py").write_bytes(b"still not runtime source")
    assert runtime._runtime_fingerprint() == removed


@pytest.mark.parametrize("error", [PermissionError("denied"), RuntimeError("unexpected")])
def test_source_read_failure_is_not_a_partial_fingerprint(tmp_path, monkeypatch, error):
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: tmp_path)
    for name in ("a.ts", "b.ts", "c.ts"):
        (tmp_path / name).write_bytes(b"export {};\n")
    original = Path.read_bytes

    def read(path):
        if path.name == "b.ts":
            raise error
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(type(error), match=str(error)):
        runtime._runtime_fingerprint()
    monkeypatch.setattr(Path, "read_bytes", original)
    assert runtime._runtime_fingerprint() == serial_fingerprint(tmp_path)


def test_deleted_source_retries_the_new_topology(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: tmp_path)
    removed = tmp_path / "a.ts"
    removed.write_bytes(b"before")
    (tmp_path / "b.json").write_bytes(b"{}")
    original = Path.read_bytes

    def read(path):
        if path == removed and path.exists():
            path.unlink()
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    assert runtime._runtime_fingerprint() == serial_fingerprint(tmp_path)


posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX ctime keys the memo")


def _private_namespace(tmp_path, monkeypatch, *, settle_ns=0):
    namespace = tmp_path / "runtime"
    namespace.mkdir(mode=0o700)
    monkeypatch.setattr(runtime, "_runtime_dir", lambda: namespace)
    monkeypatch.setattr(runtime, "_SOURCE_DIGEST_SETTLE_NS", settle_ns)
    return namespace


def _fresh_process_fingerprint():
    runtime._runtime_fingerprint_for_snapshot.cache_clear()
    return runtime._runtime_fingerprint()


def _count_source_reads(monkeypatch):
    original, reads = runtime.iter_binary_file_reads, []

    def counted(paths, **kwargs):
        for read in original(paths, **kwargs):
            reads.append(read.path.name)
            yield read

    monkeypatch.setattr(runtime, "iter_binary_file_reads", counted)
    return reads


@posix_only
def test_fresh_process_reuses_the_private_digest_until_sources_change(tmp_path, monkeypatch):
    root = tmp_path / "release"
    (root / "nested").mkdir(parents=True)
    (root / "a.ts").write_bytes(b"export {};\n")
    (root / "nested/b.json").write_bytes(b"{}")
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: root)
    namespace = _private_namespace(tmp_path, monkeypatch)
    reads = _count_source_reads(monkeypatch)

    first = _fresh_process_fingerprint()
    assert first == serial_fingerprint(root) and len(reads) == 2
    [record] = namespace.iterdir()
    # Locator retirement and fixture cleanup own only runtime-*.json names.
    assert record.name.startswith("source-digest-") and not record.match("runtime-*.json")
    assert record.stat().st_mode & 0o777 == 0o600
    assert _fresh_process_fingerprint() == first and len(reads) == 2

    (root / "a.ts").write_bytes(b"export const edited = true;\n")
    edited = _fresh_process_fingerprint()
    assert edited == serial_fingerprint(root) != first and len(reads) == 4
    (root / "c.ts").write_bytes(b"export {};\n")
    added = _fresh_process_fingerprint()
    assert added == serial_fingerprint(root) != edited
    (root / "nested/b.json").unlink()
    removed = _fresh_process_fingerprint()
    assert removed == serial_fingerprint(root) != added
    assert [path.name for path in namespace.iterdir()] == [record.name]

    copy = tmp_path / "copy"
    copy.mkdir()
    for name in ("a.ts", "c.ts"):
        (copy / name).write_bytes((root / name).read_bytes())
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: copy)
    assert _fresh_process_fingerprint() == serial_fingerprint(copy) != removed


@posix_only
def test_source_change_before_a_digest_hit_is_not_adopted(tmp_path, monkeypatch):
    root = tmp_path / "release"
    root.mkdir()
    edited = root / "a.ts"
    edited.write_bytes(b"export {};\n")
    (root / "b.json").write_bytes(b"{}")
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: root)
    namespace = _private_namespace(tmp_path, monkeypatch)
    first = _fresh_process_fingerprint()
    assert first == serial_fingerprint(root) and len(list(namespace.iterdir())) == 1
    original, hits = runtime._read_source_digest, []

    def edit_after_snapshot(path, key):
        # The caller's snapshot is already taken; the stored key still matches it.
        recorded = original(path, key)
        if not hits:
            edited.write_bytes(b"export const edited = true;\n")
        hits.append(recorded)
        return recorded

    monkeypatch.setattr(runtime, "_read_source_digest", edit_after_snapshot)
    reads = _count_source_reads(monkeypatch)

    assert _fresh_process_fingerprint() == serial_fingerprint(root) != first
    assert hits[0] == first and hits[1:] == [None] and reads == ["a.ts", "b.json"]


@posix_only
@pytest.mark.parametrize("defect", ["foreign_mode", "symlink", "shared_namespace",
                                    "corrupt", "stale_key", "invalid_digest"])
def test_untrusted_digest_record_is_recomputed(tmp_path, monkeypatch, defect):
    root = tmp_path / "release"
    root.mkdir()
    (root / "a.ts").write_bytes(b"export {};\n")
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: root)
    namespace = _private_namespace(tmp_path, monkeypatch)
    path, key = runtime._source_digest_location(
        os.fspath(root.resolve()), runtime._runtime_source_snapshot(root))
    forged = {"schema_version": runtime._SOURCE_DIGEST_SCHEMA_VERSION,
              "key": "0" * 64 if defect == "stale_key" else key,
              "fingerprint": "F" * 64 if defect == "invalid_digest" else "0" * 64}
    target = tmp_path / "elsewhere.json" if defect == "symlink" else path
    target.write_text("{" if defect == "corrupt" else json.dumps(forged))
    target.chmod(0o644 if defect == "foreign_mode" else 0o600)
    if defect == "symlink":
        path.symlink_to(target)
    if defect == "shared_namespace":
        namespace.chmod(0o755)
    reads = _count_source_reads(monkeypatch)

    assert _fresh_process_fingerprint() == serial_fingerprint(root)
    assert reads == ["a.ts"]


@posix_only
def test_unsettled_or_failed_hash_records_no_digest(tmp_path, monkeypatch):
    root = tmp_path / "release"
    root.mkdir()
    for name in ("a.ts", "b.ts"):
        (root / name).write_bytes(b"export {};\n")
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: root)
    namespace = _private_namespace(tmp_path, monkeypatch, settle_ns=3600 * 10**9)
    assert _fresh_process_fingerprint() == serial_fingerprint(root)
    assert list(namespace.iterdir()) == []

    monkeypatch.setattr(runtime, "_SOURCE_DIGEST_SETTLE_NS", 0)
    original = Path.read_bytes

    def read(path):
        if path.name == "b.ts":
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(PermissionError):
        _fresh_process_fingerprint()
    assert list(namespace.iterdir()) == []

    namespace.rmdir()
    monkeypatch.setattr(Path, "read_bytes", original)
    assert _fresh_process_fingerprint() == serial_fingerprint(root)
    assert not namespace.exists(), "the memo never creates the runtime namespace"
