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
