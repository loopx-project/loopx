"""Canonical CLI fixtures retain real public-boundary checks on owned inputs."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    _run_cli,
    _spend_run_count,
    _write_fixture,
)


def _fixture_with_public_leak(tmp_path: Path) -> tuple[Path, Path, Path]:
    project, runtime, registry = _write_fixture(tmp_path)
    # A separate repository retains this public namespace even when a caller's
    # pytest basetemp lives under a private ancestor.
    subprocess.run(
        ["git", "init", "--quiet", str(project)],
        check=True, capture_output=True, text=True,
    )
    address = ".".join(str(octet) for octet in (10, 0, 0, 7))
    (project / "PUBLIC.md").write_text(f"synthetic host {address}\n", encoding="utf-8")
    return project, runtime, registry


def test_quota_fixture_checks_its_own_public_files(tmp_path: Path) -> None:
    project, runtime, registry = _fixture_with_public_leak(tmp_path)

    rc, result = _run_cli(
        registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, cwd=project,
    )

    assert rc == 1
    assert result["status_health_ok"] is False
    assert result["should_run"] is False
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("option", ["--scan-path", "--scan-root"])
@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_quota_fixture_preserves_explicit_scan_inputs(
    tmp_path: Path, option: str, inline: bool, missing: bool,
) -> None:
    project, runtime, registry = _fixture_with_public_leak(tmp_path)
    selected = tmp_path / "explicit-public-root"
    if not missing:
        selected.mkdir()
        (selected / "PUBLIC.md").write_text("Public fixture.\n", encoding="utf-8")
    arguments = [f"{option}={selected}"] if inline else [option, str(selected)]

    rc, result = _run_cli(
        registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, *arguments, cwd=project,
    )

    assert rc == (1 if missing else 0)
    assert result["status_health_ok"] is not missing
    assert result["should_run"] is not missing
    assert _spend_run_count(runtime) == 0
