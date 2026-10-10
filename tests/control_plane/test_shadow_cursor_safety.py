"""Safety properties of the outbox cursor, independently of delivery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.coordination import local_authority_shadow_outbox as outbox


def valid_cursor() -> dict[str, object]:
    return {
        "schema_version": outbox.DRAIN_CURSOR_SCHEMA,
        "partition": "todos",
        "last_seq": 1,
        "last_entry_id": "local-shadow-tx-" + "a" * 64,
        "last_partition_digest": "sha256:" + "b" * 64,
        "last_cursor": "opaque-cursor",
        "last_provider_revision": "opaque-revision",
        "updated_at": "2026-09-05T01:02:03.123456+00:00",
    }


def write_cursor_fixture(tmp_path: Path, record: object) -> Path:
    directory = tmp_path / "outbox" / "goal" / "todos"
    directory.mkdir(parents=True)
    (directory / "drain-cursor.json").write_text(json.dumps(record), encoding="utf-8")
    return directory


@pytest.mark.parametrize("value", [True, False, "1", None, -1, 0, 1.5, 10_000_000_000])
def test_cursor_sequence_rejects_non_integer_or_out_of_filename_range(
    tmp_path: Path, value: object
) -> None:
    cursor = valid_cursor()
    cursor["last_seq"] = value
    directory = write_cursor_fixture(tmp_path, cursor)
    before = outbox.cursor_path(directory).read_bytes()
    with pytest.raises(outbox.OutboxError, match="cursor") as failure:
        outbox.read_cursor(directory)
    assert failure.value.reason_code == "outbox_file_invalid"
    assert outbox.cursor_path(directory).read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("partition", "leases"),
        ("last_entry_id", "local-shadow-tx-short"),
        ("last_partition_digest", "sha256:short"),
        ("last_cursor", None),
        ("last_provider_revision", ""),
        ("updated_at", "yesterday"),
        ("unrecognized", True),
    ],
)
def test_cursor_rejects_incomplete_or_foreign_binding(
    tmp_path: Path, field: str, value: object
) -> None:
    cursor = valid_cursor()
    cursor[field] = value
    directory = write_cursor_fixture(tmp_path, cursor)
    with pytest.raises(outbox.OutboxError) as failure:
        outbox.read_cursor(directory)
    assert failure.value.reason_code == "outbox_file_invalid"


@pytest.mark.parametrize("raw", [b"{", b"\xff", b"[]", b"null"])
def test_cursor_parse_errors_are_typed_and_preserve_bytes(
    tmp_path: Path, raw: bytes
) -> None:
    directory = tmp_path / "todos"
    directory.mkdir()
    path = directory / "drain-cursor.json"
    path.write_bytes(raw)
    with pytest.raises(outbox.OutboxError) as failure:
        outbox.read_cursor(directory)
    assert failure.value.reason_code == "outbox_file_invalid"
    assert path.read_bytes() == raw


def test_cursor_accepts_opaque_revisions_and_equivalent_json_integer(
    tmp_path: Path,
) -> None:
    cursor = valid_cursor()
    cursor["last_seq"] = 1.0
    directory = write_cursor_fixture(tmp_path, cursor)
    decoded = outbox.read_cursor(directory)
    assert decoded is not None
    assert decoded["last_seq"] == 1
    assert decoded["last_cursor"] == "opaque-cursor"
    assert decoded["last_provider_revision"] == "opaque-revision"


def test_cursor_cannot_hide_unvalidated_prepared_bytes(tmp_path: Path) -> None:
    directory = write_cursor_fixture(tmp_path, valid_cursor())
    path = directory / ("0000000001-local-shadow-tx-" + "a" * 64 + ".prepared.json")
    path.write_bytes(b"{not a prepared transaction}")
    with pytest.raises(outbox.OutboxError):
        outbox.list_entries(directory)
    assert path.read_bytes() == b"{not a prepared transaction}"


def test_status_contains_cursor_error_without_raising(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    directory = outbox.partition_directory(root, "goal", "todos")
    directory.mkdir(parents=True)
    (directory / "drain-cursor.json").write_bytes(b"{")
    summary = outbox.outbox_summary(root, "goal")
    assert summary["todos"]["invalid"] is not None


def test_zero_wait_cross_runtime_lock_reclaims_dead_holder_and_acquires(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys
    from loopx.file_lock import exclusive_cross_runtime_file_lock

    target = tmp_path / "primary.md"
    child = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    (tmp_path / "primary.md.ts-effect.lock").write_text(
        json.dumps(
            {
                "pid": int(child.stdout),
                "token": "terminated-process",
            }
        )
    )
    with exclusive_cross_runtime_file_lock(target, timeout_seconds=0):
        assert (
            json.loads((tmp_path / "primary.md.ts-effect.lock").read_text())["token"]
            != "terminated-process"
        )


@pytest.mark.parametrize(
    "kind", ["unexpected_json", "temporary_bytes", "partition_is_file", "symlink"]
)
def test_outbox_inventory_cannot_hide_unclassified_or_unreadable_evidence(
    tmp_path: Path, kind: str
) -> None:
    directory = tmp_path / "goal" / "todos"
    directory.parent.mkdir()
    if kind == "partition_is_file":
        directory.write_bytes(b"not a directory")
    else:
        directory.mkdir()
        if kind == "symlink":
            target = tmp_path / "outside.json"
            target.write_bytes(b"{}")
            (
                directory
                / ("0000000001-local-shadow-tx-" + "a" * 64 + ".prepared.json")
            ).symlink_to(target)
        else:
            (
                directory
                / (
                    "unknown.json"
                    if kind == "unexpected_json"
                    else "entry.prepared.json.tmp-dead"
                )
            ).write_bytes(b"partial")
    with pytest.raises(outbox.OutboxError):
        outbox.list_entries(directory)
