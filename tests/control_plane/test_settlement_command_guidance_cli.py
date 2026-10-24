"""Ordinary callers can fill the returned writeback without global CLI help."""
from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _initialize_fixture_authority,
    _run_cli, _run_generated_cli, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("profile", ["codex_app", "generic_cli"])
def test_returned_writeback_can_record_progress_and_settle_once(
    tmp_path: Path, provider: str, profile: str,
) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    host = ["--codex-app"] if profile == "codex_app" else ["--runtime-profile", profile]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", *host,
                        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                        "--todo-id", TODO_ID, "--turn-instance-id", "command-guidance")
    assert rc == 0, guard
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert build_turn_envelope(guard)["writeback"]["settlement_plan"] == plan
    step = next(s for s in plan["ordered_steps"] if s["kind"] == "durable_writeback")
    guidance = step["precondition"]
    # These are the inputs the caller needs for this actual bounded result.
    for value in ("test_only", "outcome_progress", "in_flight_continuation",
                  "--progress-result-class", "advanced", "--progress-evidence-id"):
        assert value in guidance
    assert "classification" in guidance
    command = step["command_template"]
    for placeholder, value in {
        "<validated_progress>": "focused_validation",
        "<scale>": "test_only",
        "<outcome>": "outcome_progress",
        "<boundary>": "in_flight_continuation",
    }.items():
        command = command.replace(placeholder, value)
    if "--delivery-boundary" not in shlex.split(command):
        command += " --delivery-boundary in_flight_continuation"
    command += " " + shlex.join([
        "--delivery-workspace-path", str(project),
        "--progress-result-class", "advanced",
        "--progress-evidence-id", "evidence:focused-validation",
        "--no-global-sync", "--suppress-external-sinks",
    ])
    # Discoverability must not weaken evidence or exactly-once settlement gates.
    rc, refused = _run_generated_cli(
        command.replace("evidence:focused-validation", ".local/not-an-evidence-id"),
        registry_path=registry,
    )
    assert rc != 0, refused
    spend = next(s["command_template"] for s in plan["ordered_steps"] if s["kind"] == "quota_spend")
    rc, premature = _run_generated_cli(spend, registry_path=registry)
    assert rc != 0, premature
    rc, writeback = _run_generated_cli(command, registry_path=registry)
    assert rc == 0, writeback
    assert writeback["progress_observation"]["result_class"] == "advanced"
    rc, spent = _run_generated_cli(spend, registry_path=registry)
    assert rc == 0, spent
    rc, replay = _run_generated_cli(spend, registry_path=registry)
    assert rc == 0 and replay["idempotent_replay"] is True, replay
    rc, todo = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID,
                        "--todo-id", TODO_ID)
    assert rc == 0 and todo["todo"]["status"] == "open", todo
