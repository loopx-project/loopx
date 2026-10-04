"""Execute rejected claim repairs without weakening the real authority owner."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

REPO = Path(__file__).resolve().parents[2]
GOAL, TODO = "claim-arguments", "todo_claim_arguments"


@pytest.fixture(params=["legacy", "file", "sqlite"])
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
    if provider != "legacy":
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL, handoff_mode="hard_lease", leases=[], todos=[{
                "schema_version": "todo_item_v0", "todo_id": TODO, "role": "agent",
                "status": "open", "done": False, "text": "Claim one open task.",
                "archive_state": "active", "source_section": "Agent Todo", "index": 1,
                "task_class": "advancement_task", "required_write_scopes": ["src/**"],
            }],
        )
        initialize_canonical_authority(runtime, GOAL, projection, state_path=state, provider=provider)
        state.unlink()

    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    claim = ["todo", "claim", "--goal-id", GOAL, "--todo-id", TODO, "--agent-id", "peer-a"]
    lease = [] if provider == "legacy" else [
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
    if provider != "legacy":
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
    provider, run, snapshot, prefix, claim, _, _, _ = claim_cli
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
    if provider == "legacy":
        rejected = run(completed_args, expected_exit=1)
        assert "requires promoted canonical authority" in rejected["error"]
        assert snapshot() == before
    else:
        applied = run(completed_args)
        assert applied["lease"]["version"] == 1


def test_grammar_packet_does_not_guess_authority_mode(claim_cli):
    provider, run, snapshot, prefix, claim, _, _, _ = claim_cli
    before = snapshot()
    flags = ["--claimed-by", "peer-a"]
    if provider == "legacy":
        flags += ["--task-lease-idempotency-key", "canonical-only"]
    rejected = run([*prefix, *claim, *flags], expected_exit=1)
    assert rejected.get("error_code") != "todo_claim_invalid_arguments"
    assert snapshot() == before
    if provider == "legacy":
        assert "requires promoted canonical authority" in rejected["error"]
        assert "recovery" not in rejected
    else:
        assert rejected["error_code"] == "handoff_mode_requires_lease"
        assert rejected["recovery"]["requires_flags"] == ["--task-lease-idempotency-key"]


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
