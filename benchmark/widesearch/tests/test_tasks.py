from __future__ import annotations

import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tasks import answer_is_fresh, fresh_workspace, stamp_run_start


def test_fresh_workspace_starts_clean(tmp_path: Path) -> None:
    case = tmp_path / "cases" / "c1"
    case.mkdir(parents=True)
    (case / "task.json").write_text("{}", encoding="utf-8")
    (case / "instruction.md").write_text("do it", encoding="utf-8")
    ws = fresh_workspace(cases_root=tmp_path / "cases", case_id="c1", run_id="r1")
    assert not (ws / "final_answer.md").exists()


def test_answer_freshness(tmp_path: Path) -> None:
    case = tmp_path / "cases" / "c1"
    case.mkdir(parents=True)
    (case / "task.json").write_text("{}", encoding="utf-8")
    (case / "instruction.md").write_text("do it", encoding="utf-8")
    ws = fresh_workspace(cases_root=tmp_path / "cases", case_id="c1", run_id="r2")
    started = stamp_run_start(ws)

    (ws / "final_answer.md").write_text("new", encoding="utf-8")
    fresh, _ = answer_is_fresh(ws, started)
    assert fresh is True

    os.utime(ws / "final_answer.md", (1, 1))  # make it stale
    fresh2, reason = answer_is_fresh(ws, started)
    assert fresh2 is False
    assert "predates" in reason
