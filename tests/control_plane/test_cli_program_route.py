"""An explicitly selected runtime must execute its own generated control loop."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import test_quota_authority_settlement_journey as journey

cli = journey.cli


def test_manager_required_read_uses_the_same_invocation_route(tmp_path):
    from loopx.command_invocation import loopx_cli_route
    from loopx.capabilities.manager_context import turn_start_hook

    with loopx_cli_route("candidate-cli"):
        hook = turn_start_hook(tmp_path, tmp_path / "registry.json", "example", "worker")
    assert shlex.split(hook.required_read["command"])[:3] == [
        "candidate-cli", "--cli-bin", "candidate-cli"]
    assert shlex.split(hook.required_read["command"])[-4:] == [
        "--goal-id", "example", "--agent-id", "worker"]


@pytest.mark.skipif(os.name == "nt", reason="Exercises a POSIX console alias and PATH")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("program", ["candidate-cli", "candidate's cli"])
@pytest.mark.parametrize("handoff_mode", ["soft_claim", "hard_lease"])
def test_generated_commands_stay_on_candidate_through_once_only_settlement(
    tmp_path, monkeypatch, provider, program, handoff_mode,
):
    project, runtime, registry, _, _ = journey._source(
        tmp_path, provider=provider, handoff_mode=handoff_mode,
        extra=f"claimed_by={cli.AGENT_ID}",
    )
    console = Path(sys.executable).parent / "loopx"
    assert console.is_file(), "Run through the checkout's installed test environment"
    bin_dir = tmp_path / "candidate bin"
    bin_dir.mkdir()
    (bin_dir / program).symlink_to(console)
    poison = bin_dir / "loopx"
    marker = tmp_path / "wrong-runtime"
    poison.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('wrong runtime')\nraise SystemExit(97)\n",
    )
    poison.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))

    def run(argv):
        result = subprocess.run(argv, cwd=project, capture_output=True, text=True, timeout=60)
        payload = json.loads(result.stdout)
        assert result.returncode == 0, {k: payload.get(k) for k in (
            "error", "error_code", "reason", "recovery")}
        return payload

    prefix = [program, "--cli-bin", program, "--format", "json", "--registry", str(registry),
              "--runtime-root", str(runtime)]
    lease = None
    if handoff_mode == "hard_lease":
        lease = run([
            *prefix, "task-lease", "acquire", "--goal-id", cli.GOAL_ID,
            "--todo-id", cli.TODO_ID, "--owner", cli.AGENT_ID,
            "--idempotency-key", "candidate-work", "--expected-version", "0",
            "--write-scope", "tests/**",
        ])["lease"]
    bootstrap = run([
        *prefix, "heartbeat-prompt", "--thin", "--codex-app", "--goal-id", cli.GOAL_ID,
        "--agent-id", cli.AGENT_ID, "--cli-bin", program,
        "--turn-instance-id", cli.TURN_ID,
    ])
    guard_command = next(line for line in bootstrap["task_body"].splitlines() if " quota should-run " in line)
    # The bootstrap binds this shell variable once per wake; argv execution
    # below supplies that same binding without reconstructing any CLI fields.
    guard_args = [cli.TURN_ID if arg == "${LOOPX_TURN:?}" else arg
                  for arg in shlex.split(guard_command)]
    assert guard_args[0] == program
    assert guard_args[guard_args.index("--cli-bin") + 1] == program
    guard = run(guard_args)
    channel = guard["interaction_contract"]["cli_channel"]
    if channel.get("selection_required"):
        selection = channel["selection_command"]
        guard = run(shlex.split(selection["route_prefix"]) + shlex.split(
            selection["command_args_template"].replace("{todo_id}", cli.TODO_ID)))
        channel = guard["interaction_contract"]["cli_channel"]
    assert guard["decision"] == "run", guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity["todo_id"] == cli.TODO_ID and identity["turn_instance_id"] == cli.TURN_ID
    commands = {s["kind"]: s["command_template"] for s in channel["settlement_plan"]["ordered_steps"]}
    for command in [*channel["next_cli_actions"], *commands.values()]:
        assert shlex.split(command)[0] == program
    for read in guard["interaction_contract"]["agent_channel"].get("required_reads", []):
        assert shlex.split(read["command"])[0] != "loopx"

    refresh = commands["durable_writeback"].replace("<validated_progress>", "validated_progress")
    refresh = refresh.replace("<scale>", "implementation").replace("<outcome>", "outcome_progress")
    refreshed = run([
        *shlex.split(refresh), "--vision-state", "vision_on_track",
        "--vision-summary", "Continue eligible work.",
        "--vision-acceptance", "The original runtime settles its selected work once.",
        "--no-global-sync", "--suppress-external-sinks",
    ])
    assert refreshed["vision_checkpoint"]["satisfied"]
    owed = refreshed["settlement_owed"]["command"]
    assert shlex.split(owed)[0] == program
    first, replay = run(shlex.split(owed)), run(shlex.split(owed))
    assert first["settlement_progress"]["state"] == "settled" and first["appended"]
    assert replay["idempotent_replay"] and not replay["appended"]
    assert first["settlement_identity"] == identity
    assert cli._spend_run_count(runtime) == 1
    if lease:
        released = run([
            *prefix, "task-lease", "release", "--goal-id", cli.GOAL_ID,
            "--todo-id", cli.TODO_ID, "--owner", cli.AGENT_ID,
            "--idempotency-key", lease["idempotency_key"],
            "--expected-version", str(lease["version"]),
        ])
        assert released["released"]
    settled = run(guard_args)
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert not marker.exists(), "A generated command escaped to the global runtime"
