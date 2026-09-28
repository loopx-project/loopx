"""CLI tests for read-only shared goal alignment projection.

Verifies ``loopx shared-goal-alignment`` and its ``loopx goal-alignment`` alias:
positive projections, json/markdown format output, unregistered agent reject,
and missing goal/state fail-closed behaviors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.cli import main as cli_main

from tests.control_plane.test_shared_goal_alignment import (
    GOAL_ID,
    _default_todo_specs,
    _write_fixture,
)


def _run_alignment_cli(
    capsys: pytest.CaptureFixture[str],
    registry: Path,
    *argv: str,
) -> tuple[int, dict[str, Any], str]:
    exit_code = cli_main(["--registry", str(registry), *argv])
    captured = capsys.readouterr()
    payload: dict[str, Any] = {}
    if "--format" in argv and "json" in argv:
        payload = json.loads(captured.out)
    return exit_code, payload, captured.out


def test_cli_projects_shared_goal_alignment_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    exit_code, payload, _ = _run_alignment_cli(
        capsys,
        paths["registry"],
        "shared-goal-alignment",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        "agent-a",
        "--project",
        str(paths["project"]),
        "--format",
        "json",
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["schema_version"] == "shared_goal_alignment_v0"
    assert payload["goal_id"] == GOAL_ID
    assert payload["agent_id"] == "agent-a"
    assert payload["read_only"] is True
    assert payload["source_basis"]["state_event_basis_sequence"] == 0
    assert payload["frontier_basis"]["based_on_state_event_sequence"] is None
    assert payload["frontier_counts"]["current_agent_claimed_advancement_count"] == 1
    assert payload["unclaimed_eligible_work"][0]["todo_id"] == "todo_unclaimed"


def test_cli_projects_shared_goal_alignment_markdown(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    exit_code, _, stdout = _run_alignment_cli(
        capsys,
        paths["registry"],
        "shared-goal-alignment",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        "agent-a",
        "--project",
        str(paths["project"]),
        "--format",
        "markdown",
    )

    assert exit_code == 0
    assert "# LoopX Shared Goal Alignment" in stdout
    assert "- ok: `True`" in stdout
    assert f"- goal_id: `{GOAL_ID}`" in stdout
    assert "- agent_id: `agent-a`" in stdout
    assert "- read_only: `True`" in stdout


def test_cli_alias_goal_alignment(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    exit_code, payload, _ = _run_alignment_cli(
        capsys,
        paths["registry"],
        "goal-alignment",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        "agent-a",
        "--project",
        str(paths["project"]),
        "--format",
        "json",
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["goal_id"] == GOAL_ID


def test_cli_unregistered_agent_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    exit_code, payload, _ = _run_alignment_cli(
        capsys,
        paths["registry"],
        "shared-goal-alignment",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        "agent-unregistered",
        "--project",
        str(paths["project"]),
        "--format",
        "json",
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert "agent is not registered" in payload["error"]


def test_cli_missing_goal_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    exit_code, payload, _ = _run_alignment_cli(
        capsys,
        paths["registry"],
        "shared-goal-alignment",
        "--goal-id",
        "nonexistent-goal",
        "--agent-id",
        "agent-a",
        "--project",
        str(paths["project"]),
        "--format",
        "json",
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert "nonexistent-goal" in payload["error"]


def test_cli_missing_state_file_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )
    paths["state_file"].unlink()

    exit_code, payload, _ = _run_alignment_cli(
        capsys,
        paths["registry"],
        "shared-goal-alignment",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        "agent-a",
        "--project",
        str(paths["project"]),
        "--format",
        "json",
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert "missing" in payload["error"]
