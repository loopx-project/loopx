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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("command", ["shared-goal-alignment", "goal-alignment"])
def test_cli_preserves_selected_canonical_basis_without_display(tmp_path, monkeypatch, capsys, provider, command):
    from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state_file = promoted_create_fixture(tmp_path, provider=provider)
    state_file.unlink()
    code, payload, _ = _run_alignment_cli(capsys, registry, command,
        "--goal-id", "goal-a", "--agent-id", "agent-a", "--format", "json")
    assert code == 0, payload
    assert payload["ok"] and payload["read_only"]
    basis = payload["source_basis"]
    assert basis["revision_basis"] == "canonical_todo_snapshot"
    assert basis["todo_basis"]["source_authority"] == f"{provider}_v0"
    assert basis["todo_basis"]["provider_revision"].startswith(f"{provider}:")
    # Independent digest of the fixture's complete empty records, not the output under test.
    from hashlib import sha256
    assert basis["todo_basis"]["records_sha256"] == sha256(b"[]").hexdigest()
    assert payload["frontier_counts"] == {"current_agent_claimed_advancement_count": 0,
        "unclaimed_advancement_count": 0, "other_agent_claimed_advancement_count": 0}
    assert payload["frontier_basis"] == {"basis_source": "unbound",
        "based_on_state_event_sequence": None, "last_agent_event_id": None}
    assert not state_file.exists()
