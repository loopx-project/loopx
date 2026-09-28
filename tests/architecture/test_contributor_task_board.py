"""Semantic negative controls for the shipped contributor board validator."""

from __future__ import annotations

import re
import runpy
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def check_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    smoke = runpy.run_path(str(ROOT / "examples/docs-governance-smoke.py"))
    check = smoke["assert_contributor_task_board_is_current"]
    scope = check.__globals__

    def original_read(path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    board = original_read("docs/development/contributor-tasks.md")
    # The board consumes the real generator and real local RFC files. Changes to
    # lifecycle in these disposable files must affect admission immediately.
    shutil.copytree(
        ROOT / "docs/architecture/rfcs", tmp_path / "docs/architecture/rfcs"
    )
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/generate_rfc_status_index.py",
        tmp_path / "scripts/generate_rfc_status_index.py",
    )
    monkeypatch.setitem(scope, "REPO_ROOT", tmp_path)

    def run(updated: str = board) -> None:
        monkeypatch.setitem(
            scope,
            "read",
            lambda path: (
                updated
                if path == "docs/development/contributor-tasks.md"
                else original_read(path)
            ),
        )
        check()

    return run, board, tmp_path


def edit_row(
    board: str,
    *,
    anchor: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    gap: str | None = None,
) -> str:
    line = next(line for line in board.splitlines() if line.startswith("| GH-C89b |"))
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    for index, value in ((0, task_id), (1, anchor), (2, gap), (4, status)):
        if value is not None:
            cells[index] = value
    return board.replace(line, "| " + " | ".join(cells) + " |")


def test_current_board_consumes_canonical_accepted_rfcs(check_board) -> None:
    run, _, _ = check_board
    run()


@pytest.mark.parametrize(
    "anchor",
    [
        "S99",
        "R99",
        "G99",
        "S99 / [RFC](../architecture/rfcs/goal-direction-baseline-v0.md)",
        "[RFC](../architecture/rfcs/missing-v0.md)",
        "catalog entry only",
    ],
)
def test_invalid_or_missing_canonical_anchor_is_rejected(
    check_board, anchor: str
) -> None:
    run, board, _ = check_board
    with pytest.raises(AssertionError):
        run(edit_row(board, anchor=anchor))


@pytest.mark.parametrize(
    "anchor",
    [
        "S1",
        "R1",
        "G1",
        "[RFC](../architecture/rfcs/goal-direction-baseline-v0.md)",
        "[#4941](https://github.com/loopx-project/loopx/issues/4941)",
    ],
)
def test_real_canonical_anchor_is_accepted(check_board, anchor: str) -> None:
    run, board, _ = check_board
    run(edit_row(board, anchor=anchor))


@pytest.mark.parametrize(
    "status", ["Needs banana", "Availableish", "Claimed nonsense", "Blockedmaybe"]
)
def test_invalid_status_prefix_does_not_admit_a_row(check_board, status: str) -> None:
    run, board, _ = check_board
    with pytest.raises(AssertionError, match="unexpected contributor task status"):
        run(edit_row(board, status=status))


@pytest.mark.parametrize(
    "state", ["Draft", "Under review", "Rejected", "Retired", "Superseded"]
)
def test_inactive_or_unmerged_design_cannot_be_available(
    check_board, state: str
) -> None:
    run, board, root = check_board
    path = root / "docs/architecture/rfcs/goal-direction-baseline-v0.md"
    text = path.read_text()
    changed = re.sub(r"(?<=RFC status:\*\* )[^\n]+", state, text, count=1)
    assert changed != text
    path.write_text(changed)
    with pytest.raises(AssertionError, match="not an Accepted claimable design"):
        run(board)


def test_completed_task_cannot_reappear_as_claimable(check_board) -> None:
    run, board, _ = check_board
    with pytest.raises(AssertionError, match="already landed"):
        run(edit_row(board, task_id="GH-C02"))


def test_curated_row_still_needs_an_exit(check_board) -> None:
    run, board, _ = check_board
    with pytest.raises(AssertionError, match="must state an exit"):
        run(
            edit_row(
                board, gap="A concrete reproduced gap without completion criteria."
            )
        )
