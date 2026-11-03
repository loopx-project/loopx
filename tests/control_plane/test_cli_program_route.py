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

from codex_checkpoint_fixture import read_cli_policy_payload

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

    def run(argv, *, expected_code=0):
        result = subprocess.run(argv, cwd=project, capture_output=True, text=True, timeout=60)
        assert result.returncode == expected_code, (result.returncode, result.stdout, result.stderr)
        return read_cli_policy_payload(result.stdout, returncode=result.returncode)

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
    rejected = run([
        *prefix, "quota", "should-run", "--codex-app", "--goal-id", cli.GOAL_ID,
        "--agent-id", cli.AGENT_ID, "--turn-instance-id", cli.TURN_ID,
        "--todo-id", "todo_not_projected",
    ], expected_code=1)
    assert rejected["error_code"] == "quota_action_selection_rejected"
    assert not rejected["ok"] and not rejected["should_run"]
    assert rejected["heartbeat_receipt"]["status"] == "not_committed"
    assert cli._heartbeat_receipt_count(runtime, cli.TURN_ID) == 0
    assert cli._spend_run_count(runtime) == 0
    [recovery] = rejected["next_cli_actions"]
    recovery_args = shlex.split(recovery)
    # Execute the returned recovery unchanged; a lost route must hit the
    # poisoned global executable instead of being repaired by the test.
    recovered = run(recovery_args)
    assert recovered["ok"] and not marker.exists()
    assert recovery_args[:3] == [program, "--cli-bin", program]
    for flag, value in {"--registry": str(registry), "--runtime-root": str(runtime),
                        "--goal-id": cli.GOAL_ID, "--agent-id": cli.AGENT_ID,
                        "--turn-instance-id": cli.TURN_ID}.items():
        assert recovery_args[recovery_args.index(flag) + 1] == value
    assert "--todo-id" not in recovery_args
    assert "--codex-app" in recovery_args
    assert cli._spend_run_count(runtime) == 0
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


@pytest.mark.skipif(os.name == "nt", reason="Exercises a POSIX console alias and poisoned PATH")
@pytest.mark.parametrize("program", ["module", "candidate's cli"])
@pytest.mark.parametrize("output_limit", [8192, 64000])
@pytest.mark.parametrize("selection", ["raw", "selected", "index", "rejected"])
def test_native_reader_continuation_and_recovery_execute_the_same_runtime(
    tmp_path, monkeypatch, program, output_limit, selection,
):
    from loopx.cli_commands.quota_capture import native_context_capture_view, _native_context_read_bytes
    from loopx.control_plane.turn_driver import codex_context as context
    from test_codex_context_lifecycle import project, setup

    setup(tmp_path, monkeypatch)
    payload = {"ok": True, "goal_id": "fixture", "requirements": "Keep the complete 原始条件。\n" * 8000,
               "items": [{"source": "original"} for _ in range(1000)]}
    _, wire = project(tmp_path, payload=payload)
    view = native_context_capture_view(wire, Path(wire["recovery"]["path"]).parent,
                                      context_directory=str(tmp_path))
    capture = Path(view["recovery"]["path"])
    original, state = capture.read_bytes(), (tmp_path / "session.json").read_bytes()
    bin_dir = tmp_path / "candidate bin"
    bin_dir.mkdir()
    marker = tmp_path / "wrong-runtime"
    poison = bin_dir / "loopx"
    poison.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                      f"Path({str(marker)!r}).touch()\nraise SystemExit(97)\n")
    poison.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    prefix = [sys.executable, "-m", "loopx.entrypoint"]
    if program != "module":
        alias = bin_dir / program
        alias.symlink_to(Path(sys.executable).parent / "loopx")
        prefix = [str(alias), "--cli-bin", str(alias)]
    instructions = context.codex_context_recovery_instructions(str(tmp_path), goal_id="fixture",
        agent_id="agent", cli_argv=prefix)
    command = instructions.split("Read missing necessary facts from this same capture with: ", 1)[1]
    words = shlex.split(command.split(" --select '<pointer>'.", 1)[0])
    for option in ("--page-bytes", "--max-page-output-bytes"):
        words[words.index(option) + 1] = str(output_limit)
    if selection == "selected":
        words += ["--select", "/requirements"]
    elif selection == "index":
        words += ["--select", "/items", "--list"]
    elif selection == "rejected":
        words += ["--select", "/missing"]

    content, pages, page_bytes = b"", 0, 0
    while True:
        assert words[:len(prefix)] == prefix
        # Execute generated commands unchanged; do not strip or repair their launcher.
        result = subprocess.run(words, cwd=tmp_path, capture_output=True, timeout=30)
        page = json.loads(result.stdout)
        assert len(result.stdout) <= output_limit
        assert not marker.exists(), "The reader escaped to the global installation"
        if not page["ok"]:
            assert selection == "rejected" and pages == 0 and result.returncode == 1
            assert "content" not in page
            words = shlex.split(page["recovery_command"])
            continue
        assert result.returncode == 0 and page["offset"] == len(content), page
        content += page["content"].encode()
        pages += 1
        page_bytes += len(result.stdout)
        if page["eof"]:
            break
        assert page["next_offset"] == len(content)
        words = shlex.split(page["next_command"])

    if selection == "raw":
        assert content == original and pages > 1
        # Compare the real public printer against the production full/delta cost oracle.
        from loopx.command_invocation import loopx_cli_route
        with loopx_cli_route(None if program == "module" else prefix[0]):
            reference_bytes = len((json.dumps(view["recovery"], ensure_ascii=False,
                separators=(",", ":")) + "\n").encode())
            assert _native_context_read_bytes(view["recovery"], original,
                page_bytes=output_limit, output_bytes=output_limit) == reference_bytes + page_bytes
    else:
        [selected] = json.loads(content)["selections"]
        if selection == "selected":
            assert selected == {"pointer": "/requirements", "value": payload["requirements"]} and pages > 1
        elif selection == "index":
            assert [field["pointer"] for field in selected["fields"]] == [f"/items/{i}" for i in range(1000)]
            assert pages > 1
        else:
            assert {field["pointer"] for field in selected["fields"]} == {"/ok", "/goal_id", "/requirements", "/items"}
    assert capture.read_bytes() == original and (tmp_path / "session.json").read_bytes() == state
