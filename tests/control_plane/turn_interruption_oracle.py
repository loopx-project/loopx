"""Black-box assertions from the export requirement; no LoopX implementation imports."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

REQUIREMENT = (
    "R1: Preserve every input row in its original order, including equal scores."
)
INPUT_CSV = "name,score\nZ,3\nA,1\nM,1\n"


def assert_export(workspace: Path, downstream: Path, expected_calls: int) -> None:
    expected = list(csv.reader(io.StringIO(INPUT_CSV)))
    with sqlite3.connect(downstream) as db:
        observed = db.execute(
            "SELECT body FROM operations ORDER BY sequence"
        ).fetchall()
    assert len(observed) == expected_calls, observed
    for (body,) in observed:
        assert list(csv.reader(io.StringIO(body))) == expected
    if expected_calls:
        with (workspace / "docs/export.csv").open() as stream:
            assert list(csv.reader(stream)) == expected
        assert (workspace / "requirements-read.txt").read_text() == REQUIREMENT
        assert REQUIREMENT in (workspace / "received.txt").read_text()


def spend_records(runtime: Path, goal: str) -> list[dict]:
    path = runtime / "goals" / goal / "runs/index.jsonl"
    return (
        [
            row
            for line in path.read_text().splitlines()
            if (row := json.loads(line)).get("classification") == "quota_slot_spent"
        ]
        if path.exists()
        else []
    )


def assert_host_stopped(workspace: Path) -> None:
    """Observe the injected local process independently of LoopX status fields."""
    import os
    import time

    pid_file = workspace / "host.pid"
    if not pid_file.exists():
        return
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        proc_stat = Path(f"/proc/{pid}/stat")
        if proc_stat.exists() and proc_stat.read_text().split(") ", 1)[1].startswith(
            "Z "
        ):
            return  # A zombie cannot execute tools.
        assert time.monotonic() < deadline, f"old Host {pid} still executes"
        time.sleep(0.02)
