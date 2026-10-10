"""Generated host commands keep the selected authority, including from another cwd."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import test_quota_settlement_cli as cli
import test_quota_authority_settlement_journey as journey

from loopx.heartbeat_prompt import build_heartbeat_prompt, build_heartbeat_prompt_error_payload
from loopx.control_plane.heartbeat.budget import build_interface_budget
from loopx.control_plane.quota.settlement import read_heartbeat_settlement


def _route(command: str, registry: Path, runtime: Path) -> None:
    argv = shlex.split(command)
    assert argv.count("--registry") == 1
    assert argv[argv.index("--registry") + 1] == str(registry)
    assert argv[argv.index("--runtime-root") + 1] == str(runtime)


def test_budget_normalizes_shell_quoted_registry_before_goal_substrings():
    registry = "/fixture/route-fixture's authority/" + "long-directory/" * 300 + "registry.json"
    body = "loopx --registry " + shlex.quote(registry) + " quota should-run --goal-id route-fixture"
    budget = build_interface_budget(
        task_body=body, goal_id="route-fixture", active_state="/fixture/state.md",
        registry_path=registry, thin=True,
    )
    assert budget["char_count"] == len(body) > budget["max_chars"]
    expected = "loopx --registry <REGISTRY_PATH> quota should-run --goal-id <GOAL_ID>"
    assert budget["budget_char_count"] == len(expected)
    assert budget["within_budget"] is True


@pytest.mark.parametrize("mode", ["full", "compact", "brief", "thin"])
def test_prompt_and_recovery_commands_keep_explicit_route(tmp_path, mode):
    registry = tmp_path / "selected authority" / "registry.json"
    runtime = tmp_path / "selected runtime"
    payload = build_heartbeat_prompt(
        goal_id="route-fixture", registry_path=registry, runtime_root=runtime,
        cli_bin="/fixture tools/loopx", agent_id="worker-a", registered_agents=["worker-a"],
        available_capabilities=["shell", "external_evidence_poll"],
        runtime_profile="codex_app_heartbeat", **{mode: True},
    )
    for key in ("quota_guard_command", "quota_spend_command", "refresh_state_command",
                "progress_refresh_state_command", "pr_review_pre_quota_command",
                "expanded_prompt_command", "compact_prompt_command",
                "brief_prompt_command", "thin_prompt_command"):
        if key not in payload:
            continue  # thin intentionally omits redundant regeneration metadata
        _route(payload[key], registry, runtime)
        assert shlex.split(payload[key])[0] == "/fixture tools/loopx"
    assert '--turn-instance-id "${LOOPX_TURN:?}"' in payload["quota_guard_command"]
    error = build_heartbeat_prompt_error_payload(
        goal_id="route-fixture", error="synthetic missing registration",
        registry_path=registry, runtime_root=runtime,
    )
    assert error["ok"] is False
    assert error["quota_guard_command"] is None
    for key in ("expanded_prompt_command", "compact_prompt_command",
                "brief_prompt_command", "thin_prompt_command"):
        _route(error[key], registry, runtime)


def _execute(argv: list[str], cwd: Path):
    env = dict(os.environ, PYTHONPATH=str(cli.REPO_ROOT))
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", *argv],
                            cwd=cwd, env=env, text=True, capture_output=True, check=False)
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_generated_guard_selection_and_settlement_ignore_conflicting_cwd_registry(tmp_path, provider):
    project, runtime, original, _, _ = journey._source(tmp_path, provider=provider)
    registry = tmp_path / "selected authority's directory" / "registry.json"
    registry.parent.mkdir()
    registry.write_text(original.read_text())
    conflict = json.loads(original.read_text())
    conflict["goals"][0]["coordination"]["registered_agents"] = ["another-worker"]
    original.write_text(json.dumps(conflict))
    code, remembered = _execute([
        "--registry", str(registry), "--runtime-root", str(runtime),
        "semantic-preference", "agent", "remember",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
        "--key", "review.collaboration", "--statement", "Use the designated reviewer.",
        "--source-ref", "owner-message-1", "--source-quote", "Use the designated reviewer.",
        "--expected-revision", "none", "--operation-id", "remember-1", "--execute",
    ], project)
    assert code == 0 and remembered["status"] == "applied", remembered
    code, prompt = _execute([
        "--registry", str(registry), "--runtime-root", str(runtime), "heartbeat-prompt",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID, "--codex-app", "--full",
        "--turn-instance-id", cli.TURN_ID,
    ], project)
    assert code == 0, prompt
    _route(prompt["quota_guard_command"], registry, runtime)
    guard_argv = shlex.split(prompt["quota_guard_command"])[1:]
    guard_argv = [cli.TURN_ID if arg == "${LOOPX_TURN:?}" else arg for arg in guard_argv]
    # No test harness adds the registry/runtime or a missing Turn identity.
    code, unbound = _execute(guard_argv, project)
    assert code == 0, unbound
    channel = unbound["interaction_contract"]["cli_channel"]
    selection = (channel["selection_command"]["route_prefix"] + " "
                 + channel["selection_command"]["command_args_template"])
    _route(selection, registry, runtime)
    # Rejected selection's reentry also keeps the same route and Turn.
    rejected_argv = guard_argv + ["--todo-id", "todo_absent"]
    code, rejected = _execute(rejected_argv, project)
    assert code != 0 or rejected["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    recovery = rejected["interaction_contract"]["agent_channel"]["primary_action"]
    _route(recovery, registry, runtime)
    assert cli.TURN_ID in recovery

    code, guard = _execute(shlex.split(selection.replace("{todo_id}", cli.TODO_ID))[1:], project)
    assert code == 0, guard
    assert guard["selected_todo"]["todo_id"] == cli.TODO_ID
    work_context = guard["interaction_contract"]["agent_channel"]["work_context"]
    preference = next(source for source in work_context["sources"]
                      if source.get("kind") == "agent_preferences")
    assert preference["content"]["current"]["items"][0]["statement"] == "Use the designated reviewer."
    assert not any(read.get("kind") == "agent_preferences"
                   for read in guard["interaction_contract"]["agent_channel"]["required_reads"])
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    # A later no-argument reentry must retain the original choice and authority.
    code, guard = _execute(guard_argv, project)
    assert code == 0, guard
    assert guard["heartbeat_receipt"]["settlement_identity"] == identity
    channel = guard["interaction_contract"]["cli_channel"]
    for command in channel["next_cli_actions"]:
        if command.startswith("loopx "):
            _route(command, registry, runtime)
    plan = channel["settlement_plan"]
    for step in plan["ordered_steps"]:
        if isinstance(step, dict) and isinstance(step.get("command_template"), str):
            _route(step["command_template"], registry, runtime)
    assert cli._spend_run_count(runtime) == 0

    refresh = next(c for c in channel["next_cli_actions"] if "refresh-state" in c)
    replacements = {
        "<validated_progress>": "validated_progress", "<scale>": "implementation",
        "<outcome>": "outcome_progress",
    }
    for placeholder, value in replacements.items():
        refresh = refresh.replace(placeholder, value)
    code, refreshed = _execute(shlex.split(refresh)[1:] + [
        "--vision-state", "vision_on_track",
        "--vision-summary", "Keep validating the selected work.",
        "--vision-acceptance", "The same Turn settles once against the selected authority.",
        "--no-global-sync", "--suppress-external-sinks",
    ], project)
    assert code == 0 and refreshed["settlement_result"]["ok"], refreshed
    spend = next(c for c in channel["next_cli_actions"] if "spend-slot" in c)
    for replay in (False, True):
        code, spent = _execute(shlex.split(spend)[1:], project)
        assert code == 0 and spent["settlement_result"]["ok"], spent
        if replay:
            assert spent["idempotent_replay"] and not spent["appended"]
    assert cli._spend_run_count(runtime) == 1
    code, settled = _execute(guard_argv, project)
    assert code == 0 and settled["effective_action"] == "heartbeat_settled_skip", settled
    assert settled["heartbeat_receipt"]["settlement_identity"] == identity
    readback = read_heartbeat_settlement(
        runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID, turn_instance_id=cli.TURN_ID,
    )
    assert readback is not None and readback.replay_phase.value == "settled"


def test_invalid_selected_registry_does_not_fall_back_to_valid_local_roster(tmp_path):
    project, runtime, original = cli._write_fixture(tmp_path)
    registry = tmp_path / "selected authority" / "registry.json"
    registry.parent.mkdir()
    selected = json.loads(original.read_text())
    selected["goals"][0]["coordination"]["registered_agents"] = ["another-worker"]
    registry.write_text(json.dumps(selected))
    code, error = _execute([
        "--registry", str(registry), "--runtime-root", str(runtime), "heartbeat-prompt",
        "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID, "--codex-app", "--full",
    ], project)
    assert code != 0
    assert error["ok"] is False
    assert error["quota_guard_command"] is None
    _route(error["thin_prompt_command"], registry, runtime)
