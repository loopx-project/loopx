from __future__ import annotations

import ast
import io
import json
import os
from pathlib import Path
from types import MappingProxyType
import pytest

from loopx.control_plane.turn_driver import (
    codex_context_history as history_io,
    codex_context_history_json as history_json,
    codex_context_history_replay as history_replay,
)
from loopx.control_plane.turn_driver.codex_context_history import (
    _IO_CHUNK_BYTES,
    _MAX_PRIMITIVE_BYTES,
    _MAX_RECORD_BYTES,
    _MAX_SKIP_DEPTH,
    _JsonSliceCursor,
    _digest,
    _native_reader,
    _reader_slices,
    _reader_spans,
    _records,
    _region_digest,
    _scan_selected_object,
    _scan_turn_prefix_record,
    _turn_boundary,
    checkpoint_native_read,
    checkpoint_native_selection,
    native_resume_sections,
    native_resume_verified,
    observe_completed_native_reread,
    observe_native_read,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ast_any_nodes(source: str, filename: str) -> list[tuple[int, str]]:
    tree = ast.parse(source, filename=filename)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            hits.append((node.lineno, "Name"))
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            hits.append((node.lineno, "Attribute"))
        elif isinstance(node, ast.alias) and node.name == "Any":
            hits.append((node.lineno, "alias"))
    return hits


def test_history_modules_satisfy_zero_any_loc_and_posix_budgets() -> None:
    from tests.architecture.test_posix_flag_portability import bare_flag_uses

    root = _repo_root()
    budgets = {
        "loopx/control_plane/turn_driver/codex_context_history.py": (550, 2),
        "loopx/control_plane/turn_driver/codex_context_history_json.py": (749, 0),
        "loopx/control_plane/turn_driver/codex_context_history_replay.py": (749, 0),
        "tests/control_plane/test_codex_context_history_contract.py": (749, 0),
    }
    type_ignore_marker = "type" + ": ignore"
    for rel_path, (max_loc, max_posix) in budgets.items():
        source = (root / rel_path).read_text(encoding="utf-8")
        lines = source.splitlines()
        assert len(lines) <= max_loc, f"{rel_path} exceeded {max_loc} LOC: {len(lines)}"
        assert _ast_any_nodes(source, rel_path) == [], f"{rel_path} uses Any"
        ignores = [idx + 1 for idx, line in enumerate(lines) if type_ignore_marker in line]
        assert ignores == [], f"{rel_path} has type-ignore comments on {ignores}"
        posix_uses = bare_flag_uses(source)
        assert len(posix_uses) <= max_posix, f"{rel_path} bare POSIX flags: {posix_uses}"


def test_codex_context_history_reexport_identities() -> None:
    assert _IO_CHUNK_BYTES == history_json._IO_CHUNK_BYTES
    assert _MAX_RECORD_BYTES == history_json._MAX_RECORD_BYTES
    assert _JsonSliceCursor is history_json._JsonSliceCursor
    assert _digest is history_json._digest
    assert _scan_selected_object is history_json._scan_selected_object
    assert _scan_turn_prefix_record is history_json._scan_turn_prefix_record
    assert _records is history_replay._records
    assert _region_digest is history_replay._region_digest
    assert _reader_slices is history_replay._reader_slices
    assert _reader_spans is history_replay._reader_spans
    assert _native_reader is history_replay._native_reader
    assert _turn_boundary is history_replay._turn_boundary
    assert history_io.observe_native_read is observe_native_read
    assert history_io.observe_completed_native_reread is observe_completed_native_reread
    assert history_io.native_resume_verified is native_resume_verified
    assert history_io.native_resume_sections is native_resume_sections
    assert history_io.checkpoint_native_selection is checkpoint_native_selection
    assert history_io.checkpoint_native_read is checkpoint_native_read


class _CountingBytesIO(io.BytesIO):
    def __init__(self, initial_bytes: bytes) -> None:
        super().__init__(initial_bytes)
        self.seek_calls = 0
        self.read_calls = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        self.seek_calls += 1
        return super().seek(offset, whence)

    def read(self, size: int | None = -1) -> bytes:
        self.read_calls += 1
        return super().read(size)


def test_native_reader_reuses_chunk_slices_without_per_line_seeks() -> None:
    lines: list[bytes] = [
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "s1", "cwd": "/w", "cli_version": "0.169.0-alpha.11.1", "history_mode": "paginated"},
            }
        ).encode(),
        json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}}).encode(),
    ]
    call_record = {
        "type": "response_item",
        "payload": {"type": "function_call", "call_id": "call-target", "arguments": "{}"},
    }
    call_bytes = json.dumps(call_record).encode()
    lines.append(call_bytes)
    for idx in range(120):
        lines.append(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_message_delta", "delta": f"tok-{idx}"},
                }
            ).encode()
        )
    raw = b"\n".join(lines) + b"\n"
    assert len(raw) < _IO_CHUNK_BYTES
    expected_offset = raw.index(call_bytes)

    stream = _CountingBytesIO(raw)
    result = _native_reader(stream, len(raw), {"tool_use_id": "call-target", "turn_id": "t1"})
    assert result == (expected_offset, _digest(call_record))
    # One EOF newline check + one chunk read (2 seeks, 2 reads) vs 120+ per-line seeks/reads.
    assert stream.seek_calls <= 3
    assert stream.read_calls <= 3
    spans = list(_reader_spans(io.BytesIO(raw), len(raw)))
    assert len(spans) == len(lines)
    assert spans[-1][0] == 0 and spans[0][1] == len(raw)


def test_reader_slices_fallback_across_chunk_boundary_and_large_record_bounds() -> None:
    prefix = json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}}).encode()
    # Span across a 64 KiB chunk boundary with one medium JSON line (> 64 KiB, < 16 MiB).
    crossing = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "call-wide",
                "padding": "p" * (_IO_CHUNK_BYTES + 2048),
            },
        }
    ).encode()
    tail = json.dumps({"type": "event_msg", "payload": {"type": "background_event"}}).encode()
    raw = prefix + b"\n" + crossing + b"\n" + tail + b"\n"
    expected_offset = len(prefix) + 1
    result = _native_reader(io.BytesIO(raw), len(raw), {"tool_use_id": "call-wide", "turn_id": "t1"})
    assert result is not None
    assert result[0] == expected_offset

    # Unterminated EOF raises cleanly.
    with pytest.raises(ValueError, match="incomplete native reader record"):
        list(_reader_slices(io.BytesIO(b'{"a":1}'), 7))

    # Truncated stream mid-digest fails closed.
    class _EmptyRead(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            return b""

    with pytest.raises(ValueError, match="truncated"):
        _region_digest(_EmptyRead(b"abc"), 0, 3)


def test_json_slice_cursor_rejects_malformed_constants_overflow_and_depth() -> None:
    for bad_const in (b"NaN", b"Infinity", b"-Infinity"):
        selected = b'{"type":"event_msg","payload":{"type":' + bad_const + b"}}"
        with pytest.raises(ValueError, match="unsupported JSON constant"):
            _scan_turn_prefix_record(io.BytesIO(selected), 0, len(selected))
        skipped = b'{"type":"event_msg","payload":{"type":"background_event","x":' + bad_const + b"}}"
        with pytest.raises(ValueError, match="invalid JSON number"):
            _scan_turn_prefix_record(io.BytesIO(skipped), 0, len(skipped))

    with pytest.raises(ValueError, match="trailing data"):
        _scan_turn_prefix_record(io.BytesIO(b'{"type":"event_msg","payload":{}} []'), 0, 36)

    with pytest.raises(ValueError, match="JSON object"):
        _scan_turn_prefix_record(io.BytesIO(b"[]"), 0, 2)

    over_prim = b"1" * (_MAX_PRIMITIVE_BYTES + 1)
    with pytest.raises(ValueError, match="oversized JSON primitive"):
        _JsonSliceCursor(io.BytesIO(over_prim), 0, len(over_prim)).read_primitive()

    deep = (b"[" * (_MAX_SKIP_DEPTH + 1)) + (b"]" * (_MAX_SKIP_DEPTH + 1))
    with pytest.raises(RecursionError, match="JSON nesting too deep"):
        _JsonSliceCursor(io.BytesIO(deep), 0, len(deep)).skip_value()


def test_records_and_turn_boundary_reject_non_object_json_and_null_payload() -> None:
    with pytest.raises(TypeError, match="JSON object"):
        list(_records(io.BytesIO(b"123\n"), 0, 4))

    assert _turn_boundary({"type": "event_msg", "payload": {"type": "task_started"}}) is True
    # Missing payload key falls back to {}, while explicit null/scalar fails closed cleanly.
    assert _turn_boundary({"type": "event_msg"}) is False
    with pytest.raises(AttributeError, match="payload"):
        _turn_boundary({"type": "event_msg", "payload": None})


def _make_valid_rollout(path: Path, *, session_id: str, cwd: str, call_id: str) -> bytes:
    rows = [
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "cwd": cwd,
                    "cli_version": "0.169.0-alpha.11.1",
                    "history_mode": "paginated",
                },
            }
        ).encode(),
        json.dumps(
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}}
        ).encode(),
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "parent-open",
                    "name": "exec",
                    "internal_chat_message_metadata_passthrough": None,
                },
            }
        ).encode(),
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn-1",
                    "item": {"type": "CommandExecution", "id": call_id, "exit_code": 0},
                },
            }
        ).encode(),
    ]
    data = b"\n".join(rows) + b"\n"
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return data


def test_public_entrypoints_preserve_mapping_proxy_and_fail_closed_boundaries(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "rollout.jsonl"
    _make_valid_rollout(rollout, session_id="sess-1", cwd=str(tmp_path), call_id="cmd-1")

    event = MappingProxyType(
        {
            "transcript_path": str(rollout),
            "session_id": "sess-1",
            "cwd": str(tmp_path),
            "model": "gpt-test",
            "permission_mode": "default",
            "turn_id": "turn-1",
            "tool_use_id": "cmd-1",
            "source": "resume",
        }
    )
    pages = (
        MappingProxyType({"ok": True, "sha256": "abc"}),
    )
    # Null passthrough metadata matches missing passthrough in _native_reader and updates state cleanly.
    updated_state = observe_native_read(
        MappingProxyType({"pending": {}, "recovery": {"sha256": "abc"}}),
        event,
        pages,
    )
    assert isinstance(updated_state["pending"], dict)
    assert "native_reads" in updated_state["pending"]

    # Symlink transcript path fails snapshot check and leaves state unchanged.
    symlink_path = tmp_path / "symlink.jsonl"
    symlink_path.symlink_to(rollout)
    unchanged = observe_native_read(
        MappingProxyType({"pending": {}}),
        {**dict(event), "transcript_path": str(symlink_path)},
        pages,
    )
    assert unchanged == {"pending": {}}

    # Malformed candidate / anchor / empty legacy dicts fail closed cleanly without raising.
    assert native_resume_verified({"candidate": None, "session_id": "sess-1", "cwd": str(tmp_path)}, event) is False
    assert native_resume_sections({"selected_resume": {}, "session_id": "sess-1", "cwd": str(tmp_path)}, event) == {}

    mutated: dict[str, object] = {
        "candidate": {"native_resume": {"read_source": {"sha256": "abc"}}},
        "consumed_pointers": {},
    }
    observe_completed_native_reread({"candidate": None}, mutated, event, pages)
    checkpoint_native_read({"candidate": None}, mutated, event, pages)
    checkpoint_native_selection({}, mutated, event, pages, pages)
    assert mutated["consumed_pointers"] == {}
