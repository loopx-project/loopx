from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime as runtime


def serial_fingerprint(root: Path) -> str:
    """Independent reference: selected Python adapter, names, and raw bytes."""
    digest = hashlib.sha256()
    digest.update(sys.executable.encode("utf-8"))
    digest.update(str(sys.version_info[:3]).encode("ascii"))
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
