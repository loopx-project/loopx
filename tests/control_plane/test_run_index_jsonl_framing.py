"""Record framing for the JSONL run index."""

from __future__ import annotations

import json
from pathlib import Path

from loopx.control_plane.runtime.run_index_rebuild import read_index_rows

NEL = "\x85"


def test_run_index_record_with_a_raw_separator_stays_one_record(tmp_path: Path) -> None:
    """`json.dumps(..., ensure_ascii=False)` keeps U+0085 verbatim in a value.

    `str.splitlines()` treats U+0085, U+2028 and U+2029 as line breaks, so the
    record arrived as two fragments that both failed to parse and the row was
    dropped instead of being read.
    """

    index = tmp_path / "run_index.jsonl"
    record = {"agent_id": "agent-a", "text": f"note{NEL}with-nel"}
    index.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    _, rows = read_index_rows(index)

    assert [row for _, row in rows] == [record]


def test_run_index_ordinary_records_are_unaffected(tmp_path: Path) -> None:
    index = tmp_path / "run_index.jsonl"
    records = [{"agent_id": "agent-a", "index": value} for value in range(3)]
    index.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )

    _, rows = read_index_rows(index)

    assert [row for _, row in rows] == records
