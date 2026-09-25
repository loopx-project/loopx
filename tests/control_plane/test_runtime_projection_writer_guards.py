"""Fail-closed guards on the shared compact runtime projection writer.

The callers' own smokes drive the happy path end to end. This module pins what
those journeys never reach — the refusals — plus the unpatched success at the
writer's own boundary, which is the control that makes the readback refusal
mean what it claims: the fixture projects fine, and only a blind read side
turns it into a rejection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.runtime import runtime_projection_writer
from loopx.control_plane.runtime.runtime_projection_writer import (
    write_compact_runtime_projection,
)

GOAL_ID = "projection-writer-guards"
MARKER_FIELD = "shared_runtime_projection"
IDENTITY_FIELDS = ("source_generated_at", "source_projection_sha256_16")


def _record(**marker_overrides: Any) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "source_generated_at": "2026-09-25T09:00:00+08:00",
        "source_projection_sha256_16": "0123456789abcdef",
    }
    marker.update(marker_overrides)
    return {
        "generated_at": "2026-09-25T09:00:00+08:00",
        MARKER_FIELD: marker,
    }


def _index_record() -> dict[str, Any]:
    # The appended index row carries the same marker as the record, because that
    # is what the writer scans for on readback. Without it a fixture defect, not
    # a broken read side, is what makes the writer refuse.
    return {
        "goal_id": GOAL_ID,
        "kind": "projection",
        MARKER_FIELD: _record()[MARKER_FIELD],
    }


def _call(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "target_runtime_root": tmp_path / "runtime",
        "goal_id": GOAL_ID,
        "record": _record(),
        "index_record": _index_record(),
        "marker_field": MARKER_FIELD,
        "identity_fields": IDENTITY_FIELDS,
        "markdown_renderer": lambda record: "# projection\n",
        "dry_run": False,
    }
    params.update(overrides)
    return write_compact_runtime_projection(**params)


def _index_path(tmp_path: Path) -> Path:
    return tmp_path / "runtime" / "goals" / GOAL_ID / "runs" / "index.jsonl"


def _index_rows(tmp_path: Path) -> list[dict[str, Any]]:
    lines = _index_path(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_missing_marker_rejects_before_any_write(tmp_path: Path) -> None:
    record = _record()
    del record[MARKER_FIELD]

    with pytest.raises(ValueError, match="must include object marker"):
        _call(tmp_path, record=record)

    assert not _index_path(tmp_path).exists()
    assert not (tmp_path / "runtime" / "goals").exists()


def test_non_object_marker_rejects_before_any_write(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must include object marker"):
        _call(tmp_path, record={**_record(), MARKER_FIELD: "already-ran"})

    assert not _index_path(tmp_path).exists()


def test_blank_identity_field_rejects_before_any_write(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is missing identity fields") as raised:
        _call(tmp_path, record=_record(source_projection_sha256_16=""))

    # The rejection names which identities the caller has to supply, so a fix is
    # not a guess about which of the tuple was empty.
    for field in IDENTITY_FIELDS:
        assert field in str(raised.value)
    assert not _index_path(tmp_path).exists()


def test_identity_rejection_precedes_the_dry_run_report(tmp_path: Path) -> None:
    record = _record()
    del record[MARKER_FIELD]

    with pytest.raises(ValueError, match="must include object marker"):
        _call(tmp_path, record=record, dry_run=True)

    assert not (tmp_path / "runtime" / "goals").exists()


def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    result = _call(tmp_path, dry_run=True)

    assert result["status"] == "would_project"
    assert result["dry_run"] is True
    assert result["readback_verified"] is False
    assert "json_path" not in result
    assert not _index_path(tmp_path).exists()


def test_unpatched_append_projects_and_reads_back(tmp_path: Path) -> None:
    result = _call(tmp_path)

    assert result["status"] == "projected"
    assert result["readback_verified"] is True
    assert Path(result["json_path"]).exists()
    assert Path(result["markdown_path"]).exists()
    assert len(_index_rows(tmp_path)) == 1


def test_readback_mismatch_fails_closed_after_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The writer's own contract is "append, then prove it reads back". With the
    # read side blind, it must refuse the claim instead of reporting success.
    # The unpatched twin above is what makes this refusal about the read side
    # rather than about the fixture.
    monkeypatch.setattr(runtime_projection_writer, "load_index", lambda path: ([], 0))

    with pytest.raises(OSError, match="did not pass index readback"):
        _call(tmp_path)

    # The guard protects the claim, not the bytes: the append already landed, so
    # a caller that retries must be prepared to meet `already_current`.
    assert len(_index_rows(tmp_path)) == 1
