"""Permanent Host IO obligations survive retirement of Todo editing effects."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import stat
import threading

import pytest

from loopx.control_plane.runtime import document_io as io
from loopx.control_plane.todos import active_state_editing


def test_todo_editor_no_longer_owns_durable_document_effects():
    for name in ("atomic_write_state_text", "verify_state_text_durable", "fsync_state_directory"):
        assert not hasattr(active_state_editing, name)
    # Read/edit compatibility remains; only the IO ownership moved.
    assert active_state_editing.section_bounds(["## Agent Todo", "- [ ] Read."], "agent")


def test_document_bytes_and_permissions_survive_publish_and_readback(tmp_path):
    path = tmp_path / "nested" / "document.jsonl"
    text = '私有文档\r\n{"value":"before\u2028after"}\n'
    io.atomic_write_state_text(path, text, create_only=True)
    assert path.read_bytes() == text.encode("utf-8")
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        path.chmod(0o640)
    io.atomic_write_state_text(path, text + "tail\r\n")
    io.verify_state_text_durable(path, text + "tail\r\n")
    assert path.read_bytes() == (text + "tail\r\n").encode("utf-8")
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert list(path.parent.iterdir()) == [path]


def test_create_only_competing_publishers_preserve_the_winner(tmp_path):
    path = tmp_path / "document.md"
    ready = threading.Barrier(2)

    def publish(text):
        ready.wait(timeout=10)
        try:
            io.atomic_write_state_text(path, text, create_only=True)
            return text
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(publish, ["first\r\n", "second\n"]))
    winners = [text for text in outcomes if text is not None]
    assert len(winners) == 1
    assert path.read_bytes() == winners[0].encode()
    io.verify_state_text_durable(path, winners[0])
    assert list(tmp_path.iterdir()) == [path]


def test_durable_readback_never_repairs_mismatched_bytes(tmp_path):
    path = tmp_path / "document.md"
    path.write_bytes(b"original\r\n")
    with pytest.raises(RuntimeError, match="readback mismatch"):
        io.verify_state_text_durable(path, "original\n")
    assert path.read_bytes() == b"original\r\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory fsync boundary")
def test_directory_sync_closes_descriptor_on_failure(tmp_path, monkeypatch):
    closed = []
    original_close = io.os.close

    def close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)

    def fail(_descriptor):
        raise OSError("directory sync failed")

    monkeypatch.setattr(io.os, "close", close)
    monkeypatch.setattr(io.os, "fsync", fail)
    with pytest.raises(OSError, match="directory sync failed"):
        io.fsync_state_directory(Path(tmp_path / "document.md"))
    assert len(closed) == 1
    with pytest.raises(OSError):
        os.fstat(closed[0])
