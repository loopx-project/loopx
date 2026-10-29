"""Original-capture paging never consults current authority or reruns a guard."""
import argparse
import hashlib
import json
import os
import shlex

import pytest

from loopx.cli_commands.quota_capture import read_context_capture
from test_quota_settlement_cli import _run_cli


def _args(path, raw, **changes):
    return argparse.Namespace(capture_file=str(path), capture_sha256=hashlib.sha256(raw).hexdigest(),
        **{"offset": 0, "page_bytes": 256, **changes})


def test_cli_pages_reconstruct_unicode_capture_without_goal_or_runtime(tmp_path):
    path = tmp_path / "capture with ' quote.json"
    original = {"requirements": ("原始要求🙂\n保留权限、停止条件与证据。" * 70), "late": "stop now", "null": None}
    raw = (json.dumps(original, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    runtime, registry = tmp_path / "absent-runtime", tmp_path / "absent-registry.json"
    args = ["quota", "context-read", "--capture-file", str(path), "--capture-sha256",
            hashlib.sha256(raw).hexdigest(), "--page-bytes", "256"]
    chunks, offset = [], 0
    while True:
        code, page = _run_cli(registry, runtime, *args, cwd=tmp_path)
        assert code == 0 and page["ok"], page
        assert page["offset"] == offset and page["total_bytes"] == len(raw)
        chunk = page["content"].encode()
        assert 0 < len(chunk) <= 256
        assert chunk == raw[offset:offset + len(chunk)]
        chunks.append(chunk)
        offset += len(chunk)
        if page["eof"]:
            assert page["next_offset"] is None and "next_command" not in page
            break
        assert page["next_offset"] == offset
        command = shlex.split(page["next_command"])
        assert command[:3] == ["loopx", "--format", "json"]
        args = command[3:]
    assert b"".join(chunks) == raw and json.loads(b"".join(chunks)) == original
    assert path.read_bytes() == raw and not runtime.exists() and not registry.exists()


@pytest.mark.parametrize("kind", ["digest", "digest_uppercase", "digest_envelope", "digest_newline", "digest_short",
    "changed_between_pages", "missing", "symlink", "directory",
    "not_json", "not_object", "not_utf8", "offset", "utf8_offset", "small_page", "large_page"])
def test_invalid_capture_never_returns_partial_context(tmp_path, kind):
    path = tmp_path / "projection.json"
    raw = json.dumps({"source": "原始🙂" * 100}, ensure_ascii=False).encode()
    path.write_bytes(raw)
    args = _args(path, raw)
    if kind == "digest":
        args.capture_sha256 = "0" * 64
    elif kind in {"digest_uppercase", "digest_envelope", "digest_newline", "digest_short"}:
        original_digest = args.capture_sha256
        args.capture_sha256 = {
            "digest_uppercase": original_digest.upper(),
            "digest_envelope": "sha256:" + original_digest,
            "digest_newline": original_digest + "\n",
            "digest_short": original_digest[:-1],
        }[kind]
    elif kind == "changed_between_pages":
        first = read_context_capture(args)
        assert first["ok"] and not first["eof"]
        args.offset = first["next_offset"]
        path.write_bytes(raw.replace(b"source", b"target"))
    elif kind == "missing":
        path.unlink()
    elif kind == "symlink":
        target = tmp_path / "original.json"
        path.rename(target)
        path.symlink_to(target)
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind in {"not_json", "not_object", "not_utf8"}:
        raw = {"not_json": b'{"missing":', "not_object": b'[]', "not_utf8": b'{"x":"\xff"}'}[kind]
        path.write_bytes(raw)
        args.capture_sha256 = hashlib.sha256(raw).hexdigest()
    elif kind == "offset":
        args.offset = len(raw) + 1
    elif kind == "utf8_offset":
        args.offset = raw.index("原".encode()) + 1
    else:
        args.page_bytes = 255 if kind == "small_page" else 8193
    result = read_context_capture(args)
    assert result["ok"] is False and "content" not in result and "next_command" not in result


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipe")
def test_capture_reader_rejects_fifo_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "projection.json"
    os.mkfifo(path)
    assert read_context_capture(_args(path, b"{}"))["ok"] is False


def test_capture_reader_accepts_parent_directory_alias(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(original, target_is_directory=True)
    raw = b'{"requirement":"preserve the complete original capture"}'
    (original / "projection.json").write_bytes(raw)
    result = read_context_capture(_args(alias / "projection.json", raw))
    assert result["ok"] and result["eof"] and result["content"].encode() == raw


def test_recovery_page_keeps_invocation_executable_and_resets_afterwards(tmp_path):
    from loopx.command_invocation import loopx_cli_route
    path = tmp_path / "projection.json"
    raw = json.dumps({"requirements": "preserve " * 100}).encode()
    path.write_bytes(raw)
    executable = str(tmp_path / "pinned release" / "loopx")
    with loopx_cli_route(executable):
        page = read_context_capture(_args(path, raw))
    assert shlex.split(page["next_command"])[:3] == [executable, "--cli-bin", executable]
    plain = read_context_capture(_args(path, raw))
    assert shlex.split(plain["next_command"])[:3] == ["loopx", "--format", "json"]
