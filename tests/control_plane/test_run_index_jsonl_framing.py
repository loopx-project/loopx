"""Record framing for the JSONL run index.

Records are LF-delimited. A JSON string value may hold U+0085, U+2028 or U+2029
(the writer uses `ensure_ascii=False`), and `str.splitlines()` used to treat
those characters as record boundaries, so a valid record arrived as two
unparseable fragments and was dropped.

This file pins both halves of the contract: those characters stay inside the
value, and the physical line shape of a file does not change when a reader
splits it, so the two rewrite consumers (duplicate repair and collision
rebuild) cannot grow a trailing blank row.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx import history
from loopx.control_plane.runtime import run_index_rebuild

NEL = "\x85"
GOAL_ID = "goal-jsonl-framing"
GENERATED_AT = "2026-09-16T00:00:00+00:00"


def test_run_index_record_with_a_raw_separator_stays_one_record(tmp_path: Path) -> None:
    """`json.dumps(..., ensure_ascii=False)` keeps U+0085 verbatim in a value.

    `str.splitlines()` treats U+0085, U+2028 and U+2029 as line breaks, so the
    record arrived as two fragments that both failed to parse and the row was
    dropped instead of being read.
    """

    index = tmp_path / "run_index.jsonl"
    record = {"agent_id": "agent-a", "text": f"note{NEL}with-nel"}
    index.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    _, rows = run_index_rebuild.read_index_rows(index)

    assert [row for _, row in rows] == [record]


def test_run_index_ordinary_records_are_unaffected(tmp_path: Path) -> None:
    index = tmp_path / "run_index.jsonl"
    records = [{"agent_id": "agent-a", "index": value} for value in range(3)]
    index.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )

    _, rows = run_index_rebuild.read_index_rows(index)

    assert [row for _, row in rows] == records


@pytest.mark.parametrize(
    ("tail", "expected_goal_ids"),
    [
        (b'{"goal_id":"interrupted"', [GOAL_ID]),
        (
            json.dumps(
                {"goal_id": "previous-goal", "generated_at": "2026-09-15T00:00:00Z"}
            ).encode("utf-8"),
            ["previous-goal", GOAL_ID],
        ),
    ],
    ids=["torn-tail", "valid-unterminated-tail"],
)
def test_history_append_starts_a_new_record_after_an_unterminated_tail(
    tmp_path: Path,
    tail: bytes,
    expected_goal_ids: list[str],
) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    index_path = runs_dir / "index.jsonl"
    index_path.write_bytes(tail)
    record = {"goal_id": GOAL_ID, "generated_at": GENERATED_AT}

    history.write_reserved_run_artifacts(
        runs_dir=runs_dir,
        generated_at=GENERATED_AT,
        record=record.copy(),
        index_record=record.copy(),
        payload=record.copy(),
        render_markdown=lambda _payload: "# run",
    )

    snapshot = history.load_index_snapshot(index_path)
    assert tail + b"\n" in index_path.read_bytes()
    assert [row["goal_id"] for row in snapshot.records] == expected_goal_ids


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n",
        '{"a": 1}\n',
        '{"a": 1}',
        f'{{"a": "nel{NEL}value"}}\n',
        '{"a": 1}\n\n{"b": 2}\n',
        '{"a": 1}\n{"b": 2}',
        '{"a": 1}\n\n',
    ],
)
def test_split_index_lines_keeps_every_physical_line(text: str) -> None:
    """A terminating LF ends the last record, while an interior empty row stays."""

    lines = run_index_rebuild.split_index_lines(text)

    assert "\n".join(lines) + ("\n" if text.endswith("\n") else "") == text


def _history_fixture(root: Path) -> tuple[Path, Path, Path]:
    runtime_root = root / "runtime"
    index_path = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path = root / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime_root),
                "projects": [],
                "goals": [],
            }
        ),
        encoding="utf-8",
    )
    return registry_path, runtime_root, index_path


def _write_duplicate_rows(index_path: Path, *, separator: str, terminal_newline: bool) -> None:
    duplicate = json.dumps(
        {"goal_id": GOAL_ID, "generated_at": GENERATED_AT, "note": f"nel{NEL}value"},
        ensure_ascii=False,
    )
    text = separator.join([duplicate, duplicate]) + (separator if terminal_newline else "")
    index_path.write_bytes(text.encode("utf-8"))


@pytest.mark.parametrize("separator", ["\n", "\r\n"])
@pytest.mark.parametrize("terminal_newline", [True, False])
def test_duplicate_repair_keeps_the_physical_line_shape(
    tmp_path: Path,
    separator: str,
    terminal_newline: bool,
) -> None:
    """A repair must not serialize the LF that terminated the file as a row."""

    registry_path, runtime_root, index_path = _history_fixture(tmp_path)
    _write_duplicate_rows(index_path, separator=separator, terminal_newline=terminal_newline)
    assert index_path.read_bytes().count(b"\n") == (2 if terminal_newline else 1)

    first = history.repair_index_duplicates(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=GOAL_ID,
        limit=10,
        execute=True,
    )
    assert first["removed_row_count"] == 1

    repaired = index_path.read_text(encoding="utf-8")
    assert repaired.endswith("\n")
    assert not repaired.endswith("\n\n")
    physical_lines = run_index_rebuild.split_index_lines(repaired)
    assert len(physical_lines) == 1
    assert json.loads(physical_lines[0])["note"] == f"nel{NEL}value"

    again = history.repair_index_duplicates(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=GOAL_ID,
        limit=10,
        execute=True,
    )
    assert again["removed_row_count"] == 0
    assert index_path.read_text(encoding="utf-8") == repaired


def test_collision_rebuild_keeps_the_backup_and_index_bytes(tmp_path: Path) -> None:
    """Both consumers of raw index lines keep the original file shape."""

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    index_path = runs_dir / "index.jsonl"
    shared_paths = {
        "json_path": str(runs_dir / "legacy.json"),
        "markdown_path": str(runs_dir / "legacy.md"),
    }
    rows = [
        {
            "goal_id": GOAL_ID,
            "generated_at": GENERATED_AT,
            "classification": "quota_monitor_poll",
            "todo_id": todo_id,
            **shared_paths,
        }
        for todo_id in ("todo-a", "todo-b")
    ]
    original = "".join(json.dumps(row) + "\n" for row in rows)
    index_path.write_text(original, encoding="utf-8")

    groups = run_index_rebuild.collision_review_groups(index_path, GOAL_ID)
    plan = run_index_rebuild.build_collision_rebuild_plan(
        groups,
        goal_filter=GOAL_ID,
        total_collision_group_count=len(groups),
        truncated=False,
    )

    rebuilt = run_index_rebuild.apply_reviewed_collision_rebuild(
        plan,
        plan_sha256=plan["plan_sha256"],
    )

    assert Path(rebuilt[0]["backup_path"]).read_text(encoding="utf-8") == original
    rebuilt_content = index_path.read_text(encoding="utf-8")
    assert rebuilt_content.endswith("\n")
    assert not rebuilt_content.endswith("\n\n")
    physical_lines = run_index_rebuild.split_index_lines(rebuilt_content)
    assert len(physical_lines) == 2
    assert [json.loads(line)["todo_id"] for line in physical_lines] == ["todo-a", "todo-b"]
