"""Native Goal bootstrap delegates policy to the current quota contract."""

import json
import os
import re
import shutil
import subprocess

import pytest

from loopx.heartbeat_prompt import build_heartbeat_prompt


@pytest.mark.parametrize(
    "host", ["codex_cli", "codex_app_ssh_goal", "ark_managed_agent_goal", "traex"]
)
def test_goal_prompt_has_one_live_execution_entry(host: str) -> None:
    kwargs = {"visible_goal_host": "traex-cli", "runtime_profile": "generic_cli"} if host == "traex" else {
        "runtime_profile": host
    }
    packet = build_heartbeat_prompt(
        goal_id="contract-fixture", agent_id="worker-a",
        registered_agents=["worker-a"], **kwargs,
    )
    body = packet["task_body"]
    assert "Each work iteration" in body
    assert "complete successful JSON" in body
    assert "interaction_contract" in body
    assert "settlement_plan.ordered_steps" in body
    assert "selection_command" in body
    assert "next_cli_actions" in body
    assert packet["quota_spend_command"] not in body
    assert packet["progress_refresh_state_command"] not in body
    assert "Do not reconstruct" in body
    assert "new host Goal" in body
    assert "terminal no-follow-up" in body
    assert "notification" in body
    assert "no work/spend" in body
    assert "No permission asks in a trusted session" not in body
    assert len(body) < 3300


def test_codex_wait_rule_is_not_exported_to_other_goal_hosts() -> None:
    codex = build_heartbeat_prompt(goal_id="contract-fixture", runtime_profile="codex_cli")
    ark = build_heartbeat_prompt(goal_id="contract-fixture", runtime_profile="ark_managed_agent_goal")
    assert "status=blocked" in codex["task_body"]
    assert "status=blocked" not in ark["task_body"]
    assert "do not invoke LoopX Turn" in ark["task_body"]


def test_explicit_goal_policy_is_preserved_not_replaced_by_bootstrap_defaults() -> None:
    packet = build_heartbeat_prompt(
        goal_id="contract-fixture", runtime_profile="codex_cli",
        permission_rule="Only edit the assigned workspace.",
        material_queue_rule="Use the approved reference set only.",
    )
    assert "Only edit the assigned workspace." in packet["task_body"]
    assert "Use the approved reference set only." in packet["task_body"]


@pytest.mark.parametrize("profile", ["codex_cli", None, "ark_managed_agent_goal"])
def test_shared_static_safety_and_exception_routing_survive_thinning(profile):
    body = build_heartbeat_prompt(goal_id="contract-fixture", runtime_profile=profile,
                                  thin=True)["task_body"]
    for obligation in ("repository rules", "credentials", "private material",
                       "Destructive Git", "production", "loopx-project", "loopx-self-repair"):
        assert obligation in body
    assert "project-specific workflow" not in body
    assert "No project branches" not in body
    assert "only the affected path" in body


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_emitted_heartbeat_bootstrap_expands_turn_before_real_guard(shell, tmp_path):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} unavailable")
    # The actual emitted shell block must work without any inherited Turn.
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    spec = spec_from_file_location("goal_runner", Path(__file__).resolve().parents[2] /
                                  "scripts/qualify-native-goal-release.py")
    runner = module_from_spec(spec)
    spec.loader.exec_module(runner)
    project, _, launcher = runner.setup(tmp_path)
    packet = runner.cli(launcher, "heartbeat-prompt", "--thin", "--codex-app",
                        "--goal-id", runner.GOAL, "--agent-id", runner.AGENT,
                        "--cli-bin", str(launcher))
    script = re.search(r"```sh\n(.*?)\n```", packet["task_body"], re.S).group(1)
    script = script.replace("<current_time_iso>", "2026-09-01T00:00:00Z")
    env = {k: v for k, v in os.environ.items() if k != "LOOPX_TURN"}
    result = subprocess.run([executable, "-c", script], cwd=project, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    guard = json.loads(result.stdout)
    assert guard["ok"] is True
    assert guard["goal_id"] == runner.GOAL
    assert guard["heartbeat_receipt"]["turn_instance_id"] == "2026-09-01T00:00:00Z"
    # Prefix assignment is a genuine shell failure, not a LoopX rejection.
    broken = script.replace("LOOPX_TURN=2026-09-01T00:00:00Z\n",
                            "LOOPX_TURN=2026-09-01T00:00:00Z ", 1)
    rejected = subprocess.run([executable, "-c", broken], cwd=project, env=env,
                              capture_output=True, text=True, timeout=120)
    assert rejected.returncode != 0 and "LOOPX_TURN" in rejected.stderr
