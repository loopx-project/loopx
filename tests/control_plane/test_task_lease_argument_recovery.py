"""Execute CLI grammar repairs against disposable real lease authorities."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

ROOT = Path(__file__).resolve().parents[2]
GOAL, TODO = "lease-recovery", "todo_lease_recovery"


@pytest.fixture(params=["legacy", "file", "sqlite"])
def lease_cli(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project with spaces", tmp_path / "runtime"
    project.mkdir()
    state, registry = project / "state.md", tmp_path / "registry.json"
    state.write_text("# Lease\n\n## Agent Todo\n\n- [ ] Verify a source artifact.\n"
                     f"  <!-- loopx:todo todo_id={TODO} status=open task_class=advancement_task claimed_by=peer-a required_write_scopes=src/** -->\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(project), "state_file": state.name,
        "coordination": {"registered_agents": ["peer-a", "peer-b"]},
    }]}))
    if request.param != "legacy":
        initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
            goal_id=GOAL, handoff_mode="hard_lease", leases=[], todos=[{
                "schema_version": "todo_item_v0", "todo_id": TODO, "role": "agent",
                "status": "open", "done": False, "text": "Verify a source artifact.",
                "archive_state": "active", "source_section": "Agent Todo", "index": 1,
                "task_class": "advancement_task", "claimed_by": "peer-a", "required_write_scopes": ["src/**"],
            }]), state_path=state, provider=request.param)
        state.unlink()
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]

    def run(args, code=0):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == code, result.stdout + result.stderr
        return json.loads(result.stdout)

    def action(name, *flags):
        return [*prefix, "task-lease", name, "--goal-id", GOAL, "--todo-id", TODO, *flags]

    acquired = run(action("acquire", "--owner", "peer-a", "--idempotency-key", "original",
                          "--write-scope", "src/**", "--expected-version", "0"))
    assert acquired["lease"]["version"] == 1
    yield run, action, prefix, runtime
    subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; "
                    "effect_runtime_result('runtime.shutdown', {}, retry_safe=False)"],
                   cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)


def test_inspect_repair_removes_zero_cas_and_never_changes_lease(lease_cli):
    run, action, prefix, _ = lease_cli
    before = run(action("inspect"))
    rejected = run(action("inspect", "--owner", "peer-a", "--expected-version", "0"), code=1)
    recovery = rejected["recovery"]
    assert recovery["remove_flags"] == ["--owner", "--expected-version"]
    assert recovery["requires_flags"] == []
    for flag in ("--registry", "--runtime-root", "--format"):
        assert recovery["cli_args"][recovery["cli_args"].index(flag) + 1] == prefix[prefix.index(flag) + 1]
    assert run(recovery["cli_args"]) == before


def test_renew_repair_preserves_identity_and_exact_version(lease_cli):
    run, action, _, _ = lease_cli
    before = run(action("inspect"))["lease"]
    rejected = run(action("renew", "--owner", "peer-a", "--idempotency-key", "original",
                          "--expected-version", "1", "--write-scope", "other/**",
                          "--new-owner", "peer-b"), code=1)
    assert run(action("inspect"))["lease"] == before
    repaired = rejected["recovery"]["cli_args"]
    assert rejected["recovery"]["remove_flags"] == ["--new-owner", "--write-scope"]
    assert repaired[repaired.index("--expected-version") + 1] == "1"
    renewed = run(repaired)
    assert renewed["lease"]["version"] == 2
    assert renewed["lease"]["write_scopes"] == ["src/**"]
    # An exact retry replays the existing receipt, without performing a second renewal.
    assert run(repaired)["lease"] == renewed["lease"]
    # New intent with the old version is still rejected by the real authority.
    assert run([*repaired, "--ttl-seconds", "600"], code=1)["ok"] is False
    assert run(action("inspect"))["lease"] == renewed["lease"]


def test_missing_inputs_are_combined_and_wrong_owner_is_not_repaired(lease_cli):
    run, action, _, _ = lease_cli
    before = run(action("inspect"))["lease"]
    rejected = run(action("release", "--ttl-seconds", "0"), code=1)
    assert rejected["recovery"]["requires_flags"] == ["--owner", "--idempotency-key", "--expected-version"]
    assert rejected["recovery"]["remove_flags"] == ["--ttl-seconds"]
    assert "--owner" not in rejected["recovery"]["cli_args"]
    rejected = run(action("release", "--owner", "peer-b", "--idempotency-key", "original",
                          "--expected-version", "0", "--write-scope", "src/**"), code=1)
    repaired = rejected["recovery"]["cli_args"]
    assert repaired[repaired.index("--expected-version") + 1] == "0"
    denied = run(repaired, code=1)
    assert denied["ok"] is False and "recovery" not in denied
    assert run(action("inspect"))["lease"] == before


def test_recovery_round_trips_leading_hyphen_execution_keys(lease_cli):
    run, action, _, _ = lease_cli
    released = run(action("release", "--owner", "peer-a", "--idempotency-key", "original",
                          "--expected-version", "1"))
    acquired = run(action("acquire", "--owner", "peer-a", "--idempotency-key=-execution",
                          "--write-scope", "src/**", "--expected-version",
                          str(released["lease"]["version"])))
    before = acquired["lease"]
    for name, extra in [("renew", []), ("transfer", ["--new-owner", "peer-a",
                                                   "--new-idempotency-key=-receiver"])]:
        rejected = run(action(name, "--owner", "peer-a", "--idempotency-key=-execution",
                              "--expected-version", str(before["version"]),
                              "--write-scope", "other/**", *extra), code=1)
        assert run(action("inspect"))["lease"] == before
        repaired = rejected["recovery"]["cli_args"]
        applied = run(repaired)["lease"]
        assert applied["version"] == before["version"] + 1
        assert applied["idempotency_key"] == ("-receiver" if name == "transfer" else "-execution")
        assert applied["owner"] == "peer-a" and applied["write_scopes"] == ["src/**"]
        assert run(repaired)["lease"] == applied
        assert run(action("inspect"))["lease"] == applied
        before = applied


def test_recovery_preserves_routing_and_repeatable_string_values():
    from loopx.cli import build_parser
    from loopx.cli_commands.task_lease_arguments import validate_task_lease_arguments, TaskLeaseArgumentError

    parser = build_parser()
    original = parser.parse_args([
        "--registry=-registry.json", "--runtime-root=-runtime", "task-lease", "acquire",
        "--goal-id=-goal", "--todo-id=-todo", "--owner=-owner", "--idempotency-key=-key",
        "--write-scope=-src/**", "--write-scope=docs with spaces/**", "--write-worktree=-tree",
        "--new-idempotency-key=remove-me", "--expected-version", "0",
    ])
    with pytest.raises(TaskLeaseArgumentError) as rejected:
        validate_task_lease_arguments(original)
    recovered = parser.parse_args(rejected.value.recovery(
        original, registry_path=Path("-registry.json"), runtime_root_arg="-runtime")["cli_args"])
    for field in ("registry", "runtime_root", "goal_id", "todo_id", "owner", "idempotency_key",
                  "write_scopes", "write_worktree", "expected_version"):
        assert getattr(recovered, field) == getattr(original, field)
    assert recovered.new_idempotency_key is None


def test_transfer_repair_retains_explicit_claim_handover(lease_cli, request):
    run, action, _, _ = lease_cli
    before = run(action("inspect"))["lease"]
    rejected = run(action("transfer", "--owner", "peer-a", "--idempotency-key", "original",
                          "--expected-version", "1", "--new-owner", "peer-b",
                          "--new-idempotency-key", "receiver", "--transfer-claim",
                          "--write-worktree", str(ROOT)), code=1)
    repaired = rejected["recovery"]["cli_args"]
    assert rejected["recovery"]["remove_flags"] == ["--write-worktree"]
    assert "--transfer-claim" in repaired
    assert repaired[repaired.index("--expected-version") + 1] == "1"
    assert run(action("inspect"))["lease"] == before
    if request.node.callspec.params["lease_cli"] == "legacy":
        # Syntax repair cannot make atomic claim transfer available on legacy files.
        denied = run(repaired, code=1)
        assert denied["ok"] is False and "recovery" not in denied
        assert run(action("inspect"))["lease"] == before
    else:
        transferred = run(repaired)
        assert transferred["lease"]["owner"] == transferred["claimed_by"] == "peer-b"
        assert transferred["lease"]["version"] == 2
        assert transferred["lease"]["write_scopes"] == ["src/**"]
        released = run(action("release", "--owner", "peer-b", "--idempotency-key", "receiver",
                              "--expected-version", "2"))
        assert released["lease"]["status"] == "released"


def test_grammar_rejection_precedes_runtime_resolution(monkeypatch):
    from loopx.cli import build_parser
    from loopx.cli_commands.task_lease import handle_task_lease_command, render_task_lease_markdown
    import loopx.cli_commands.task_lease as cli
    monkeypatch.setattr(cli, "runtime_root_from_registry", lambda *_: pytest.fail("must not read state"))
    args = build_parser().parse_args(["task-lease", "inspect", "--goal-id", GOAL,
                                     "--todo-id", TODO, "--write-worktree", "/unavailable"])
    captured = []
    assert handle_task_lease_command(args, registry_path=Path("missing registry.json"),
                                    runtime_root_arg=None, output_format=lambda _: "json",
                                    print_payload=lambda payload, *_: captured.append(payload)) == 1
    assert captured[0]["recovery"]["remove_flags"] == ["--write-worktree"]
    assert "canonical lease checks" in render_task_lease_markdown(captured[0])
