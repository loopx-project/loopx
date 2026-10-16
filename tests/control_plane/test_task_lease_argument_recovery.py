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


@pytest.fixture(params=["file", "sqlite"])
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


def test_transfer_repair_retains_explicit_claim_handover(lease_cli):
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


def _retained_lifecycle_cli(tmp_path: Path, operation: str):
    """Restore fixed 1.x records; never invoke a retired writer to seed them."""
    fixture = Path(__file__).parent / "fixtures" / "retained_lease_receipts_1x.json"
    history = json.loads(fixture.read_text().replace("$ROOT", str(tmp_path)))["scenarios"]
    scenario = history["original" if operation == "release" else operation]
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    state.write_text("# Historical Goal\n\n## Agent Todo\n\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "goal-a", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"]},
    }]}))
    lease = runtime / "goals/goal-a/task-leases/todo_target.json"
    receipt = None
    for relative, value in scenario["files"].items():
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        if ".lifecycle-operations" in path.parts:
            receipt = path

    def run(**overrides):
        request = {**scenario["request"], **overrides}
        args = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                "task-lease", operation, "--goal-id", "goal-a", "--todo-id", "todo_target",
                "--owner", request["owner"], "--idempotency-key", request["idempotency_key"],
                "--expected-version", str(request["expected_version"] if operation != "release" else 1)]
        if operation != "release":
            args += ["--ttl-seconds", str(request["ttl_seconds"])]
        if operation == "transfer":
            args += ["--new-owner", request["new_owner"], "--new-idempotency-key", request["new_idempotency_key"]]
        if request.get("transfer_claim") is True:
            args += ["--transfer-claim"]
        completed = subprocess.run([sys.executable, "-m", "loopx.cli", *args], cwd=ROOT,
                                   capture_output=True, text=True, timeout=90)
        return completed.returncode, json.loads(completed.stdout)

    return run, runtime, state, registry, lease, receipt, history


@pytest.mark.parametrize("operation", ["renew", "transfer"])
@pytest.mark.parametrize("receipt_state", ["prepared", "committed"])
def test_retained_lifecycle_public_cli_recovers_only_the_original_effect(tmp_path, operation, receipt_state):
    run, runtime, state, registry, lease, receipt, _history = _retained_lifecycle_cli(tmp_path, operation)
    assert receipt is not None
    original = json.loads(receipt.read_text())
    receipt.write_text(json.dumps({**original, "state": receipt_state}))
    source_before = {path: path.read_bytes() for path in (state, registry, lease)}
    receipt_before = receipt.read_bytes()
    rc, replay = run()
    assert rc == 0 and replay["idempotent"] is True, replay
    assert replay["lease"] == json.loads(source_before[lease])
    assert replay["settlement"]["receipts"][-1]["status"] == "replayed"
    assert {path: path.read_bytes() for path in source_before} == source_before
    if receipt_state == "committed":
        assert receipt.read_bytes() == receipt_before
    else:
        assert json.loads(receipt.read_text())["state"] == "committed"
    repaired_receipt = receipt.read_bytes()
    rc, again = run()
    assert rc == 0 and again["lease"] == replay["lease"] and again["idempotent"] is True, again
    assert receipt.read_bytes() == repaired_receipt
    assert {path: path.read_bytes() for path in source_before} == source_before
    assert not (runtime / "authority").exists()


@pytest.mark.parametrize("operation", ["renew", "transfer"])
@pytest.mark.parametrize("change", ["ttl", "owner", "key", "version", "later_generation", "corrupt_receipt", "prepared_without_effect", "missing_receipt"])
def test_retained_lifecycle_public_cli_refuses_changes_and_fresh_source_work(tmp_path, operation, change):
    run, runtime, state, registry, lease, receipt, history = _retained_lifecycle_cli(tmp_path, operation)
    assert receipt is not None
    overrides = {"ttl": {"ttl_seconds": 1200}, "owner": {"owner": "agent-b"},
                 "key": {"idempotency_key": "another-execution"}, "version": {"expected_version": 2}}.get(change, {})
    if change == "later_generation":
        current = json.loads(lease.read_text())
        lease.write_text(json.dumps({**current, "version": current["version"] + 1, "lease_epoch": current["lease_epoch"] + 1}))
    elif change == "corrupt_receipt":
        receipt.write_text("{invalid original receipt")
    elif change == "prepared_without_effect":
        original = json.loads(receipt.read_text())
        receipt.write_text(json.dumps({**original, "state": "prepared"}))
        lease.write_text(json.dumps(history["original"]["files"]["goals/goal-a/task-leases/todo_target.json"]))
    elif change == "missing_receipt":
        receipt.unlink()
    before = {path: path.read_bytes() if path.exists() else None for path in (state, registry, lease, receipt)}
    rc, rejected = run(**overrides)
    assert rc != 0 and rejected["ok"] is False, rejected
    if change in {"prepared_without_effect", "missing_receipt"}:
        assert rejected["error_code"] == "canonical_authority_required", rejected
        assert rejected["write_admitted"] is False and rejected["legacy_fallback_used"] is False
    elif change == "ttl":
        assert rejected["error_code"] == "idempotency_key_reuse", rejected
    elif change == "later_generation":
        assert rejected["error_code"] == "lifecycle_receipt_state_mismatch", rejected
    assert {path: path.read_bytes() if path.exists() else None for path in before} == before
    assert not (runtime / "authority").exists()


@pytest.mark.parametrize("change", [{"new_owner": "agent-a"}, {"new_idempotency_key": "different-receiver"}, {"transfer_claim": True}])
def test_retained_transfer_cli_cannot_change_the_receiver_or_add_claim_authority(tmp_path, change):
    run, runtime, state, registry, lease, receipt, _history = _retained_lifecycle_cli(tmp_path, "transfer")
    before = {path: path.read_bytes() for path in (state, registry, lease, receipt)}
    rc, rejected = run(**change)
    assert rc != 0 and rejected["ok"] is False, rejected
    assert {path: path.read_bytes() for path in before} == before
    assert not (runtime / "authority").exists()


@pytest.mark.parametrize("operation", ["renew", "transfer"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_retained_lifecycle_cli_never_falls_back_after_selected_provider_loss(tmp_path, monkeypatch, operation, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    run, runtime, state, registry, lease, receipt, _history = _retained_lifecycle_cli(tmp_path, operation)
    initialize_canonical_authority(runtime, "goal-a", build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=[], leases=[], handoff_mode="hard_lease"), state_path=state, provider=provider)
    backend = runtime / "authority" / f"{provider}-v0"
    offline = backend.with_name(backend.name + "-offline")
    backend.rename(offline)
    before = {path: path.read_bytes() for path in (state, registry, lease, receipt)}
    try:
        rc, rejected = run()
        assert rc != 0 and rejected["ok"] is False, rejected
        assert rejected["legacy_fallback_used"] is False
        assert {path: path.read_bytes() for path in before} == before
        assert not backend.exists()
    finally:
        offline.rename(backend)


def test_retained_expired_lease_public_release_still_settles_once(tmp_path):
    run, runtime, state, registry, lease, _receipt, _history = _retained_lifecycle_cli(tmp_path, "release")
    original = json.loads(lease.read_text())
    before = {path: path.read_bytes() for path in (state, registry)}
    rc, released = run()
    assert rc == 0 and released["lease"]["status"] == "released", released
    assert released["lease"]["owner"] == original["owner"]
    assert released["lease"]["idempotency_key"] == original["idempotency_key"]
    after = lease.read_bytes()
    rc, replay = run()
    assert rc == 0 and replay["idempotent"] is True and replay["lease"] == released["lease"], replay
    assert lease.read_bytes() == after
    assert {path: path.read_bytes() for path in before} == before
    assert not (runtime / "authority").exists()
