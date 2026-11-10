"""Execute rejected claim repairs without weakening the real authority owner."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.cli_commands import todo as todo_command
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
    LocalCoordinationAuthorityUnavailable,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

REPO = Path(__file__).resolve().parents[2]
GOAL, TODO = "claim-arguments", "todo_claim_arguments"


@pytest.fixture(params=["file", "sqlite"])
def claim_cli(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    provider = request.param
    project = tmp_path / "project with spaces"
    project.mkdir()
    state, runtime, registry = project / "state.md", tmp_path / "runtime", tmp_path / "registry.json"
    state.write_text(
        "# Claim\n\n## Agent Todo\n\n- [ ] Claim one open task.\n"
        f"  <!-- loopx:todo todo_id={TODO} status=open task_class=advancement_task -->\n"
    )
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(project), "state_file": state.name,
        "coordination": {"registered_agents": ["peer-a", "peer-b"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL, handoff_mode="hard_lease", leases=[], todos=[{
            "schema_version": "todo_item_v0", "todo_id": TODO, "role": "agent",
            "status": "open", "done": False, "text": "Claim one open task.",
            "archive_state": "active", "source_section": "Agent Todo", "index": 1,
            "task_class": "advancement_task", "required_write_scopes": ["src/**"],
        }],
    )
    initialize_canonical_authority(
        runtime, GOAL, projection, state_path=state, provider=provider,
    )
    state.unlink()

    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    claim = ["todo", "claim", "--goal-id", GOAL, "--todo-id", TODO, "--agent-id", "peer-a"]
    lease = [
        "--claim-operation-id", "original-claim", "--task-lease-idempotency-key", "original-lease",
        "--task-lease-expected-version", "0",
    ]

    def run(arguments, expected_exit=0):
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", *arguments], cwd=REPO,
            capture_output=True, text=True, timeout=90, check=False,
        )
        assert result.returncode == expected_exit, result.stdout + result.stderr
        return json.loads(result.stdout)

    def snapshot():
        readback = run([*prefix, "todo", "list", "--goal-id", GOAL])
        return readback["todos"], readback.get("provider_revision"), state.read_bytes() if state.exists() else None

    yield provider, run, snapshot, prefix, claim, lease, project, state
    subprocess.run(
        [sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; "
         "effect_runtime_result('runtime.shutdown', {}, retry_safe=False)"],
        cwd=REPO, capture_output=True, text=True, timeout=30, check=True,
    )


def test_one_packet_repairs_all_grammar_errors_and_preserves_route(claim_cli):
    provider, run, snapshot, prefix, claim, lease, project, state = claim_cli
    before = snapshot()
    rejected = run([
        *prefix, *claim, *lease, "--turn-instance-id", "unrelated-turn", "--note", "not a claim field",
        "--project", str(project), "--state-file", str(state), "--role", "agent", "--dry-run",
    ], expected_exit=1)
    assert snapshot() == before
    assert rejected["error_code"] == "todo_claim_invalid_arguments"
    recovery = rejected["recovery"]
    assert recovery["command"] == "loopx todo claim"
    assert recovery["requires_flags"] == ["--claimed-by"]
    assert recovery["remove_flags"] == ["--turn-instance-id", "--note"]
    repaired = recovery["cli_args"]
    # No executor inference, shell quoting, route changes or weakened preview/CAS.
    assert "--claimed-by" not in repaired
    for flag, value in (
        ("--registry", prefix[1]), ("--runtime-root", prefix[3]), ("--goal-id", GOAL),
        ("--todo-id", TODO), ("--agent-id", "peer-a"), ("--project", str(project)),
        ("--state-file", str(state)), ("--role", "agent"),
    ):
        assert repaired[repaired.index(flag) + 1] == value
    assert "--dry-run" in repaired
    if lease:
        for flag, value in zip(lease[::2], lease[1::2]):
            assert repaired[repaired.index(flag) + 1] == value
    preview = run([*repaired, "--claimed-by", "peer-a"])
    assert preview["ok"] is True
    assert snapshot() == before
    repaired.remove("--dry-run")
    applied = run([*repaired, "--claimed-by", "peer-a"])
    assert applied["ok"] is True
    assert snapshot()[0][0]["claimed_by"] == "peer-a"
    assert applied["lease"]["version"] == 1
    assert applied["lease"]["write_scopes"] == ["src/**"]
    replay = run([*repaired, "--claimed-by", "peer-a"])
    assert replay["status"] == "replayed"
    assert replay["changed"] is False


def test_syntax_repair_does_not_authorize_a_different_executor(claim_cli):
    _, run, snapshot, prefix, claim, lease, _, _ = claim_cli
    before = snapshot()
    rejected = run([*prefix, *claim, *lease, "--claimed-by", "peer-b", "--note", "invalid"], expected_exit=1)
    repaired = run(rejected["recovery"]["cli_args"], expected_exit=1)
    assert repaired["ok"] is False
    assert repaired.get("error_code") != "todo_claim_invalid_arguments"
    assert snapshot() == before


def test_missing_required_inputs_are_reported_together(claim_cli):
    _, run, snapshot, prefix, _, _, _, _ = claim_cli
    before = snapshot()
    rejected = run([*prefix, "todo", "claim", "--goal-id", GOAL, "--agent-id", "peer-a"], expected_exit=1)
    assert rejected["recovery"]["requires_flags"] == ["--todo-id", "--claimed-by"]
    assert "--claimed-by" not in rejected["recovery"]["cli_args"]
    assert snapshot() == before


def test_repair_preserves_an_invalid_cas_instead_of_weakening_it(claim_cli):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    before = snapshot()
    rejected = run([
        *prefix, *claim, "--claimed-by", "peer-a", "--note", "invalid",
        "--task-lease-idempotency-key", "original-key", "--task-lease-expected-version", "3",
    ], expected_exit=1)
    repaired = rejected["recovery"]["cli_args"]
    assert repaired[repaired.index("--task-lease-expected-version") + 1] == "3"
    assert repaired[repaired.index("--task-lease-idempotency-key") + 1] == "original-key"
    assert run(repaired, expected_exit=1)["ok"] is False
    assert snapshot() == before


def test_lease_input_requirement_preserves_zero_cas(claim_cli):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    before = snapshot()
    rejected = run([
        *prefix, *claim, "--claimed-by", "peer-a", "--task-lease-expected-version", "0",
    ], expected_exit=1)
    assert snapshot() == before
    recovery = rejected["recovery"]
    assert recovery["requires_flags"] == ["--task-lease-idempotency-key"]
    repaired = recovery["cli_args"]
    assert repaired[repaired.index("--task-lease-expected-version") + 1] == "0"
    completed_args = [*repaired, "--task-lease-idempotency-key", "explicit-execution"]
    applied = run(completed_args)
    assert applied["lease"]["version"] == 1


def test_grammar_packet_does_not_guess_execution_actor(claim_cli):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    before = snapshot()
    flags = ["--claimed-by", "peer-b"]
    rejected = run([*prefix, *claim, *flags], expected_exit=1)
    assert rejected.get("error_code") != "todo_claim_invalid_arguments"
    assert snapshot() == before
    assert rejected["error_code"] == "claim_actor_mismatch"


def test_markdown_exposes_the_same_required_input():
    from loopx.cli import build_parser
    from loopx.cli_commands.todo_argument_validation import TodoClaimArgumentError, validate_todo_claim_options
    from loopx.cli_commands.todo_event import todo_error_payload
    from loopx.control_plane.todos.markdown import render_todo_markdown

    args = build_parser().parse_args(["todo", "claim", "--goal-id", GOAL, "--todo-id", TODO])
    with pytest.raises(TodoClaimArgumentError) as error:
        validate_todo_claim_options(args)
    payload = todo_error_payload(args, error.value, registry_path=Path("registry.json"), runtime_root_arg=None)
    rendered = render_todo_markdown(payload)
    assert "requires_flags: `--claimed-by`" in rendered
    assert json.dumps(payload["recovery"]["cli_args"]) in rendered


def test_ordinary_claim_progress_completion_need_no_lease_flags(claim_cli):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    arguments = [*prefix, *claim, "--claimed-by", "peer-a", "--claim-operation-id", "managed-claim"]
    accepted = run(arguments)
    assert accepted["ok"] is True
    assert accepted["execution_managed"] is True
    assert "lease" not in accepted and "original_receipt" not in accepted
    from loopx.control_plane.todos.markdown import render_todo_markdown
    rendered = render_todo_markdown(accepted)
    assert all(action in rendered for action in accepted["next_cli_actions"])
    assert accepted["claim_operation_id"] == "managed-claim"
    assert len(accepted["next_cli_actions"]) == 2
    assert all(shlex.split(action)[0] == "loopx" and "--cli-bin" not in shlex.split(action)
               for action in accepted["next_cli_actions"])
    assert all("--claim-operation-id managed-claim" in action for action in accepted["next_cli_actions"])
    assert all("task-lease" not in action for action in accepted["next_cli_actions"])
    common = ["--goal-id", GOAL, "--todo-id", TODO, "--agent-id", "peer-a", "--claim-operation-id", "managed-claim"]
    progress_args = shlex.split(accepted["next_cli_actions"][0])[1:]
    progress_args = ["managed-progress" if part == "<progress-operation-id>" else "Validation passed" if part == "<progress>" else part for part in progress_args]
    progress = run(progress_args)
    assert progress["ok"] is True
    complete_args = ["evidence:validated-output" if part == "<validated-evidence>" else part for part in shlex.split(accepted["next_cli_actions"][1])[1:]]
    completed = run(complete_args)
    assert completed["ok"] is True
    before = snapshot()
    replay = run([*prefix, "todo", "complete", *common, "--evidence", "evidence:validated-output"])
    assert replay["ok"] is True
    assert snapshot() == before


@pytest.fixture
def selected_runtime(claim_cli, tmp_path, monkeypatch):
    _, _, _, _, _, _, project, _ = claim_cli
    program = "candidate's cli"
    binaries = tmp_path / "candidate bin"
    binaries.mkdir()
    (binaries / program).symlink_to(Path(sys.executable).parent / "loopx")
    marker = tmp_path / "wrong-runtime"
    poison = binaries / "loopx"
    poison.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                      f"Path({str(marker)!r}).write_text('wrong runtime')\nraise SystemExit(97)\n")
    poison.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ.get("PATH", ""))
    # The external cwd must import this candidate, even with an ambient installed package.
    monkeypatch.setenv("PYTHONPATH", str(REPO))

    def execute(argv, expected_exit=0):
        result = subprocess.run(argv, cwd=project, capture_output=True, text=True, timeout=90)
        assert result.returncode == expected_exit, result.stdout + result.stderr
        assert not marker.exists()
        return json.loads(result.stdout)

    return program, execute


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable alias and PATH fixture")
def test_explicit_runtime_survives_repair_progress_and_completion(claim_cli, selected_runtime):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    program, execute = selected_runtime
    before = snapshot()
    rejected = run(["--cli-bin", program, *prefix, *claim, "--note", "invalid"], expected_exit=1)
    assert snapshot() == before
    recovery = rejected["recovery"]
    assert recovery["requires_flags"] == ["--claimed-by"]
    assert recovery["remove_flags"] == ["--note"]
    assert shlex.split(recovery["command"]) == [program, "--cli-bin", program, "todo", "claim"]
    repaired = [program, *recovery["cli_args"], "--claimed-by", "peer-a",
                "--claim-operation-id", "selected-runtime-claim"]
    accepted = execute(repaired)
    assert accepted["execution_managed"] is True
    from loopx.control_plane.todos.markdown import render_todo_markdown
    assert all(action in render_todo_markdown(accepted) for action in accepted["next_cli_actions"])
    actions = [shlex.split(action) for action in accepted["next_cli_actions"]]
    assert all(argv[:3] == [program, "--cli-bin", program] for argv in actions)
    assert all(argv[argv.index("--claim-operation-id") + 1] == "selected-runtime-claim"
               for argv in actions)
    progress = [{"<progress-operation-id>": "selected-runtime-progress", "<progress>": "Validation passed"}
                .get(part, part) for part in actions[0]]
    claimed = snapshot()
    wrong_actor = ["peer-b" if i and progress[i - 1] == "--agent-id" else part
                   for i, part in enumerate(progress)]
    assert execute(wrong_actor, expected_exit=1)["ok"] is False
    assert snapshot() == claimed
    assert execute(progress)["ok"] is True
    progressed = snapshot()
    assert execute(progress)["ok"] is True
    assert snapshot() == progressed
    complete = ["evidence:validated-output" if part == "<validated-evidence>" else part
                for part in actions[1]]
    assert execute(complete)["ok"] is True
    completed = snapshot()
    assert completed[0][0]["status"] == "done"
    assert execute(complete)["ok"] is True
    assert snapshot() == completed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable alias and PATH fixture")
@pytest.mark.parametrize("command", ["update", "complete"])
def test_native_refusal_preserves_runtime_and_original_operation(claim_cli, selected_runtime, command):
    _, run, snapshot, prefix, claim, _, project, state = claim_cli
    program, execute = selected_runtime
    if command == "update":
        # Copy edits need no lease. Clearing a wait retains the execution fence;
        # exercise omitted proof while this actor owns a current execution.
        assert run([*prefix, *claim, "--claimed-by", "peer-a",
                    "--claim-operation-id", "original-execution"])["ok"]
    original = ["--cli-bin", program, *prefix, "todo", command, "--goal-id", GOAL,
        "--todo-id", TODO, "--agent-id", "peer-a", "--role", "agent",
        "--project", str(project), "--state-file", str(state),
        "--note=--keep the original 中文 'note' and evidence.", "--evidence", "evidence:original-output"]
    if command == "update":
        # Preserve the caller's operation across a lost response and replay.
        original += ["--update-operation-id", "original-native-update", "--clear-resume-when"]
    before = snapshot()
    rejected = run(original, expected_exit=1)
    assert snapshot() == before
    recovery = rejected["recovery"]
    assert recovery["action"] == "claim_task"
    assert recovery["execution_authority_granted"] is False
    from loopx.control_plane.todos.markdown import render_todo_markdown
    markdown_args = list(original)
    markdown_args[markdown_args.index("--format") + 1] = "markdown"
    markdown = subprocess.run([sys.executable, "-m", "loopx.cli", *markdown_args],
        cwd=REPO, capture_output=True, text=True, timeout=90)
    assert markdown.returncode == 1, markdown.stdout + markdown.stderr
    assert snapshot() == before
    rendered = markdown.stdout.rstrip("\n")
    assert rendered == render_todo_markdown(rejected)

    def emitted(step):
        line = next(line for line in rendered.splitlines() if line.startswith(f"- {step}: `"))
        argv = shlex.split(line.split("`", 2)[1])
        # JSON and Markdown must carry the same executable arguments.
        assert argv[1:] == recovery[step]["cli_args"]
        return argv

    # Execute the emitted claim unchanged from outside the checkout. An ambient
    # global CLI is poisoned; both route selectors must reach the real backend.
    claim_argv = emitted("claim")
    prepared = execute(claim_argv)
    assert prepared["ok"] and prepared["execution_managed"]
    claimed = snapshot()
    assert execute([*claim_argv, "--claim-operation-id", prepared["claim_operation_id"]])["ok"]
    assert snapshot() == claimed
    retry = [*emitted("retry"), "--claim-operation-id", prepared["claim_operation_id"]]
    for flag, value in (("--project", str(project)), ("--state-file", str(state)), ("--role", "agent")):
        assert retry[retry.index(flag) + 1] == value
    if command == "update":
        assert "--clear-resume-when" in retry
        assert retry[retry.index("--update-operation-id") + 1] == "original-native-update"
    wrong_actor = ["peer-b" if i and retry[i - 1] == "--agent-id" else part
                   for i, part in enumerate(retry)]
    assert execute(wrong_actor, expected_exit=1)["ok"] is False
    assert snapshot() == claimed
    assert execute(retry)["ok"]
    after = snapshot()
    task = after[0][0]
    assert task["note"] == "--keep the original 中文 'note' and evidence."
    assert task["evidence"] == "evidence:original-output"
    assert task["status"] == ("open" if command == "update" else "done")
    assert execute(retry)["ok"]
    assert snapshot() == after


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable alias and PATH fixture")
def test_native_inspection_keeps_route_and_rejects_stale_proof(claim_cli, selected_runtime):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    program, execute = selected_runtime
    assert run([*prefix, *claim, "--claimed-by", "peer-a",
                "--claim-operation-id", "before-stale-proof"])["ok"]
    before = snapshot()
    rejected = run(["--cli-bin", program, *prefix, "todo", "update", "--goal-id", GOAL,
        "--todo-id", TODO, "--agent-id", "peer-a", "--note", "Do not weaken CAS.",
        "--task-lease-idempotency-key", "wrong-key", "--task-lease-expected-version", "0"], expected_exit=1)
    assert snapshot() == before
    from loopx.control_plane.todos.markdown import render_todo_markdown
    rendered = render_todo_markdown(rejected)
    line = next(line for line in rendered.splitlines() if line.startswith("- inspect: `"))
    inspected = execute(shlex.split(line.split("`", 2)[1]))
    assert inspected["active"] is True
    assert inspected["lease"]["version"] == 1
    assert snapshot() == before


def test_native_refusal_preview_preserves_no_write_intent(claim_cli):
    _, run, snapshot, prefix, _, _, _, _ = claim_cli
    before = snapshot()
    rejected = run([*prefix, "todo", "complete", "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", "peer-a", "--evidence", "evidence:preview", "--dry-run"], expected_exit=1)
    recovery = rejected["recovery"]
    assert recovery["execution_authority_granted"] is False
    assert "--dry-run" in recovery["claim"]["cli_args"]
    assert "--dry-run" in recovery["retry"]["cli_args"]
    preview = run(recovery["claim"]["cli_args"])
    assert preview["dry_run"] is True
    assert snapshot() == before


def test_terminal_refusal_recovers_through_ordinary_claim(claim_cli):
    _, run, snapshot, prefix, _, _, _, _ = claim_cli
    complete = [*prefix, "todo", "complete", "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", "peer-a", "--evidence", "evidence:validated-output"]
    before = snapshot()
    rejected = run(complete, expected_exit=1)
    assert snapshot() == before
    assert rejected["error_code"] == "handoff_mode_requires_lease"
    recovery = rejected["recovery"]
    assert recovery["action"] == "claim_task"
    assert recovery["execution_authority_granted"] is False
    from loopx.control_plane.todos.markdown import render_todo_markdown
    rendered = render_todo_markdown(rejected)
    assert recovery["claim"]["command"] == "loopx todo claim"
    assert "- claim: `loopx " in rendered and "--claim-operation-id" in rendered
    assert "--cli-bin" not in recovery["claim"]["cli_args"]
    assert "task-lease" not in rendered and "handoff_mode:" not in rendered
    prepared = run([*recovery["claim"]["cli_args"],
        "--claim-operation-id", "after-terminal-refusal"])
    assert prepared["ok"] and prepared["execution_managed"]
    retry = [*recovery["retry"]["cli_args"], "--claim-operation-id", prepared["claim_operation_id"]]
    assert run(retry)["ok"]
    completed = snapshot()
    assert run(retry)["ok"]
    assert snapshot() == completed


@pytest.mark.parametrize("command", ["update", "complete"])
def test_explicit_one_command_execution_recovery(claim_cli, command):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    if command == "update":
        assert run([
            *prefix, *claim, "--claimed-by", "peer-a",
            "--claim-operation-id", "active-before-one-command-recovery",
        ])["ok"]
    arguments = [
        *prefix,
        "todo",
        command,
        "--goal-id",
        GOAL,
        "--todo-id",
        TODO,
        "--agent-id",
        "peer-a",
        "--recover-execution",
    ]
    arguments += (
        [
            "--note", "Recovered without a caller-visible failed command.",
            "--clear-resume-when",
        ]
        if command == "update"
        else ["--evidence", "evidence:validated-output"]
    )
    result = run(arguments)
    assert result["ok"] is True
    recovery = result["execution_recovery"]
    assert recovery == {
        "schema_version": "todo_execution_recovery_v0",
        "status": "recovered",
        "initial_error_code": (
            "lease_fence_required"
            if command == "update"
            else "handoff_mode_requires_lease"
        ),
        "claim_operation_id": recovery["claim_operation_id"],
        "claim_status": "no_change" if command == "update" else "applied",
        "external_invocations": 1,
        "external_failed_calls": 0,
    }
    task = snapshot()[0][0]
    assert task["status"] == ("open" if command == "update" else "done")
    if command == "update":
        assert task["note"] == "Recovered without a caller-visible failed command."
    else:
        replayed = run(arguments)
        assert replayed["ok"] is True
        assert replayed["changed"] is False
        assert replayed["execution_recovery"]["status"] == "replayed"
        assert replayed["execution_recovery"]["claim_operation_id"] == recovery["claim_operation_id"]
        assert snapshot()[0][0] == task


def test_one_command_recovery_never_replaces_explicit_stale_proof(claim_cli):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    assert run([
        *prefix, *claim, "--claimed-by", "peer-a",
        "--claim-operation-id", "current-execution",
    ])["ok"]
    before = snapshot()
    rejected = run([
        *prefix, "todo", "update", "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", "peer-a", "--note", "Do not replace proof.",
        "--recover-execution", "--task-lease-idempotency-key", "stale-proof",
        "--task-lease-expected-version", "0",
    ], expected_exit=1)
    assert "accepts only omitted execution proof" in rejected["error"]
    assert "execution_recovery" not in rejected
    assert snapshot() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_one_command_completion_replays_the_original_admitted_turn(tmp_path, monkeypatch, provider):
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, TODO_ID, _initialize_fixture_authority, _run_cli, _write_fixture,
    )
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, claimed=False)
    _initialize_fixture_authority(project, runtime, provider=provider, handoff_mode="hard_lease")
    turn = "turn-original-recovered-completion"
    code, guard = _run_cli(registry, runtime, "quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", turn, "--runtime-profile", "generic_cli")
    assert code == 0 and guard["should_run"], guard.get("state")
    command = ("todo", "complete", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", turn,
        "--recover-execution", "--evidence", "evidence:original-completion")
    code, first = _run_cli(registry, runtime, *command)
    assert code == 0, json.dumps(first)
    after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    code, second = _run_cli(registry, runtime, *command)
    assert code == 0 and second["ok"] and second["changed"] is False, json.dumps(second)
    assert second["execution_recovery"]["claim_operation_id"] == first["execution_recovery"]["claim_operation_id"]
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) == after


def test_one_command_recovery_does_not_take_foreign_owner(claim_cli):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    assert run([
        *prefix, *claim, "--claimed-by", "peer-b", "--agent-id", "peer-b",
        "--claim-operation-id", "foreign-execution",
    ])["ok"]
    before = snapshot()
    rejected = run([
        *prefix, "todo", "update", "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", "peer-a", "--note", "Forbidden takeover.",
        "--recover-execution",
    ], expected_exit=1)
    assert rejected["error_code"] == "update_owner_mismatch"
    assert snapshot() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_one_command_recovery_preserves_missing_dependency(
    tmp_path, monkeypatch, provider,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "dependency-project"
    project.mkdir()
    runtime, state = tmp_path / "runtime", project / "state.md"
    registry = tmp_path / "registry.json"
    state.write_text("# Synthetic dependency\n", encoding="utf-8")
    registry.write_text(json.dumps({
        "common_runtime_root": str(runtime),
        "goals": [{
            "id": "dependency-goal",
            "repo": str(project),
            "state_file": state.name,
            "coordination": {"registered_agents": ["peer-a"]},
        }],
    }), encoding="utf-8")
    projection = build_todo_runtime_shadow_projection(
        goal_id="dependency-goal",
        handoff_mode="hard_lease",
        leases=[],
        todos=[{
            "schema_version": "todo_item_v0",
            "todo_id": "todo_dependency_wait",
            "role": "agent",
            "status": "open",
            "done": False,
            "text": "Wait for a missing prerequisite.",
            "archive_state": "active",
            "source_section": "Agent Todo",
            "index": 1,
            "task_class": "advancement_task",
            "claimed_by": "peer-a",
            "resume_when": "todo_done:todo_missing_dependency",
        }],
    )
    initialize_canonical_authority(
        runtime,
        "dependency-goal",
        projection,
        state_path=state,
        provider=provider,
    )
    state.unlink()
    before = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", "todo", "list",
        "--goal-id", "dependency-goal", "--todo-id", "todo_dependency_wait",
    ], cwd=REPO, capture_output=True, text=True, timeout=90, check=True).stdout
    completed = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", "todo", "complete",
        "--goal-id", "dependency-goal", "--todo-id", "todo_dependency_wait",
        "--agent-id", "peer-a", "--evidence", "evidence:must-not-commit",
        "--recover-execution",
    ], cwd=REPO, capture_output=True, text=True, timeout=90)
    assert completed.returncode == 1, completed.stdout + completed.stderr
    rejected = json.loads(completed.stdout)
    assert rejected["error_code"] == "handoff_mode_requires_lease"
    assert rejected["recovery"]["action"] == "resolve_acquire_rejection"
    assert rejected["recovery"]["reason_code"] == "todo_dependency_pending"
    assert "execution_recovery" not in rejected
    after = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", "todo", "list",
        "--goal-id", "dependency-goal", "--todo-id", "todo_dependency_wait",
    ], cwd=REPO, capture_output=True, text=True, timeout=90, check=True).stdout
    assert json.loads(after)["todo"] == json.loads(before)["todo"]


def test_one_command_recovery_reports_committed_claim_when_retry_fails(monkeypatch):
    initial = LocalCoordinationAuthorityRejection(
        "ordinary lifecycle operation needs a managed claim",
        code="lease_fence_required",
        payload={"recovery": {
            "action": "claim_task",
            "execution_authority_granted": False,
            "claim": {"command": "loopx todo claim"},
            "retry": {
                "reference_source": "claim_operation_id_from_claim",
                "requires_flags": ["--claim-operation-id"],
            },
        }},
    )
    args = Namespace(
        goal_id="goal-retry-race",
        todo_id="todo-retry-race",
        role="agent",
        agent_id="peer-a",
        project=None,
        state_file=None,
    )

    def claim(**kwargs):
        assert kwargs["claim_only"] is True
        assert kwargs["manage_execution"] is True
        assert kwargs["claimed_by"] == kwargs["agent_id"] == "peer-a"
        return {
            "ok": True,
            "execution_managed": True,
            "claim_operation_id": "committed-claim",
            "status": "applied",
        }

    monkeypatch.setattr(todo_command, "update_goal_todo", claim)

    def retry(reference):
        assert reference == "committed-claim"
        raise LocalCoordinationAuthorityRejection(
            "the unchanged retry lost a provider race",
            code="provider_revision_mismatch",
            payload={"expected_provider_revision": "1", "actual_provider_revision": "2"},
        )

    with pytest.raises(LocalCoordinationAuthorityUnavailable) as caught:
        todo_command._recover_todo_execution_once(
            args,
            initial,
            registry_path=Path("registry.json"),
            runtime_root_arg=None,
            retry_operation=retry,
        )

    assert caught.value.code == "todo_execution_recovery_retry_failed"
    assert caught.value.payload == {
        "execution_recovery": {
            "schema_version": "todo_execution_recovery_v0",
            "status": "claim_committed_retry_failed",
            "initial_error_code": "lease_fence_required",
            "claim_operation_id": "committed-claim",
            "claim_status": "applied",
            "retry_error_code": "provider_revision_mismatch",
        },
        "retry_failure": {
            "expected_provider_revision": "1",
            "actual_provider_revision": "2",
        },
    }


@pytest.mark.parametrize(
    "flags,missing",
    [
        (["--resume-when", "capacity_available:test"], "--reason"),
        (["--reason", "Wait for capacity"], "--resume-when"),
    ],
)
def test_deferred_transition_names_missing_contract_before_claim(
    claim_cli, flags, missing,
):
    _, run, snapshot, prefix, _, _, _, _ = claim_cli
    before = snapshot()
    rejected = run([
        *prefix, "todo", "update", "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", "peer-a", "--status", "deferred",
        "--recover-execution", *flags,
    ], expected_exit=1)
    assert "requires --reason and --resume-when" in rejected["error"]
    assert missing in rejected["error"]
    assert snapshot() == before


@pytest.mark.parametrize("command", ["update", "complete"])
def test_empty_managed_reference_is_rejected_without_mutation(claim_cli, command):
    _, run, snapshot, prefix, claim, _, _, _ = claim_cli
    accepted = run([*prefix, *claim, "--claimed-by", "peer-a",
        "--claim-operation-id", "valid-managed-reference"])
    assert accepted["ok"] and accepted["execution_managed"]
    before = snapshot()
    arguments = [*prefix, "todo", command, "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", "peer-a", "--claim-operation-id", ""]
    arguments += (["--note", "Only a valid original execution may update this task."]
        if command == "update" else ["--evidence", "evidence:validated-output"])
    rejected = run(arguments, expected_exit=1)
    assert rejected["error_code"] == (
        "invalid_local_coordination_todo_update_request" if command == "update"
        else "invalid_local_coordination_todo_terminal_lifecycle_request")
    assert rejected["error"] == "claim_operation_id must be a non-empty trimmed string"
    assert snapshot() == before


def test_task_help_exposes_managed_reference_not_host_lease_flags():
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "todo", "claim", "--help"],
        cwd=REPO, capture_output=True, text=True, check=True)
    assert "--claim-operation-id" in result.stdout
    assert "--task-lease-idempotency-key" not in result.stdout
    assert "--task-lease-expected-version" not in result.stdout
