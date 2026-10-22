"""The real public reconciler must execute with its own fence on every backend."""
import json

import pytest

from loopx.capabilities.issue_fix import pr_monitor_materialization as materialization
from loopx.control_plane.work_items.task_lease import acquire_task_lease, inspect_task_lease
from loopx.domain_packs.issue_fix import upsert_issue_fix_pr_lifecycle_ledger_jsonl
from tests.capabilities.test_issue_fix_grouped_monitor_materialization import (
    _fixture, _use_provider, _packet, _monitor_todos, GOAL_ID, AGENT_ID,
)


def scenario(tmp_path, monkeypatch, provider):
    project, state, registry = _fixture(tmp_path)
    _use_provider(state, registry, provider, monkeypatch, handoff_mode="hard_lease")
    ledger = tmp_path / "lifecycle.jsonl"
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(101))
    arguments = dict(registry_path=registry, goal_id=GOAL_ID, project=project,
                     ledger_path=ledger, claimed_by=AGENT_ID, cadence="30m")
    materialization.materialize_issue_fix_grouped_monitors(**arguments, generated_at="2030-01-01T00:00:00Z")
    monitor = _monitor_todos(registry, project)[0]
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger, _packet(102))
    return arguments, monitor, state


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_hard_lease_membership_observation_releases_only_its_execution(tmp_path, monkeypatch, provider):
    args, before, state = scenario(tmp_path, monkeypatch, provider)
    result = materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:00Z")
    assert result["write_performed"] is True
    after = _monitor_todos(args["registry_path"], args["project"])[0]
    assert after["material_change_generation"] == int(before.get("material_change_generation") or 0) + 1
    lease = inspect_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
                              goal_id=GOAL_ID, todo_id=before["todo_id"])["lease"]
    assert lease["status"] == "released"
    snapshot = state.read_bytes()
    assert materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:00Z")["write_performed"] is False
    assert snapshot == state.read_bytes()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_another_execution_even_for_same_actor_is_not_borrowed(tmp_path, monkeypatch, provider):
    args, monitor, state = scenario(tmp_path, monkeypatch, provider)
    acquired = acquire_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
        goal_id=GOAL_ID, todo_id=monitor["todo_id"], owner=AGENT_ID, idempotency_key="unrelated-execution", ttl_seconds=60)
    before = state.read_bytes()
    with pytest.raises(ValueError):
        materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:00Z")
    assert state.read_bytes() == before
    assert inspect_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
        goal_id=GOAL_ID, todo_id=monitor["todo_id"])["lease"] == acquired["lease"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_retry_after_interrupted_acquisition_recovers_its_own_attempt(tmp_path, monkeypatch, provider):
    args, monitor, _ = scenario(tmp_path, monkeypatch, provider)
    original = materialization.acquire_task_lease
    def interrupt(**kwargs):
        original(**kwargs)
        raise SystemExit("simulated process exit after durable acquire")
    monkeypatch.setattr(materialization, "acquire_task_lease", interrupt)
    with pytest.raises(SystemExit):
        materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:00Z")
    lease_before = inspect_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
        goal_id=GOAL_ID, todo_id=monitor["todo_id"])["lease"]
    monkeypatch.setattr(materialization, "acquire_task_lease", original)
    # A real retry is a new invocation: the observation time moves on while the
    # intended work is unchanged, so the recovery must not depend on it.
    assert materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:01Z")["write_performed"]
    lease_after = inspect_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
        goal_id=GOAL_ID, todo_id=monitor["todo_id"])["lease"]
    assert lease_after["idempotency_key"] == lease_before["idempotency_key"]
    assert lease_after["lease_epoch"] == lease_before["lease_epoch"]
    assert lease_after["status"] == "released"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_retry_recovers_its_own_attempt_despite_reordered_ledger(tmp_path, monkeypatch, provider):
    """The attempt identity is the reconciliation subject, not the row order."""
    args, monitor, _ = scenario(tmp_path, monkeypatch, provider)
    original = materialization.acquire_task_lease
    def interrupt(**kwargs):
        original(**kwargs)
        raise SystemExit("simulated process exit after durable acquire")
    monkeypatch.setattr(materialization, "acquire_task_lease", interrupt)
    with pytest.raises(SystemExit):
        materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:00Z")
    lease_before = inspect_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
        goal_id=GOAL_ID, todo_id=monitor["todo_id"])["lease"]
    monkeypatch.setattr(materialization, "acquire_task_lease", original)
    lines = args["ledger_path"].read_text(encoding="utf-8").splitlines()
    assert len(lines) > 1
    args["ledger_path"].write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    assert materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:02Z")["write_performed"]
    lease_after = inspect_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
        goal_id=GOAL_ID, todo_id=monitor["todo_id"])["lease"]
    assert lease_after["idempotency_key"] == lease_before["idempotency_key"]
    assert lease_after["lease_epoch"] == lease_before["lease_epoch"]
    assert lease_after["status"] == "released"


def test_attempt_prefix_identifies_the_subject_not_the_observation():
    """Only the reconciliation subject identifies a recoverable attempt."""
    subject = dict(goal_id=GOAL_ID, todo_id="todo_1", owner=AGENT_ID, target_key="github-pr-state-x")
    prefix = materialization._attempt_prefix(**subject)
    assert prefix == materialization._attempt_prefix(**subject)
    assert prefix.startswith("issue-fix-monitor:")
    for field, other in (("goal_id", "other-goal"), ("todo_id", "todo_2"),
                         ("owner", "other-agent"), ("target_key", "github-pr-state-y")):
        assert materialization._attempt_prefix(**(subject | {field: other})) != prefix


def test_attempt_decision_ignores_only_the_observation_clock():
    """A later observation may repeat the decision; a changed one may not."""
    step = {"operation": "observe", "todo_id": "todo_1", "write_scopes": [],
        "observation": {"target_key": "github-pr-state-x", "result_hash": "digest",
            "material_change": True, "generated_at": "2030-01-01T00:00:00Z",
            "next_due_at": "2030-01-01T00:30:00Z"}}
    later = json.loads(json.dumps(step))
    later["observation"].update(generated_at="2030-01-01T01:00:00Z", next_due_at="2030-01-01T01:30:00Z")
    assert materialization._decision_signature(step) == materialization._decision_signature(later)
    for mutate in (
        lambda candidate: candidate.update(operation="complete"),
        lambda candidate: candidate["observation"].update(result_hash="other-digest"),
        lambda candidate: candidate["observation"].update(material_change=False),
        lambda candidate: candidate.update(write_scopes=["tasks.write"]),
    ):
        changed = json.loads(json.dumps(step))
        mutate(changed)
        assert materialization._decision_signature(changed) != materialization._decision_signature(step)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_failed_write_cleans_attempt_and_identical_retry_can_acquire_again(tmp_path, monkeypatch, provider):
    args, monitor, _ = scenario(tmp_path, monkeypatch, provider)
    original = materialization.update_goal_todo
    def unavailable(**kwargs):
        raise OSError("synthetic write failure")
    monkeypatch.setattr(materialization, "update_goal_todo", unavailable)
    with pytest.raises(OSError):
        materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:00Z")
    released = inspect_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
        goal_id=GOAL_ID, todo_id=monitor["todo_id"])["lease"]
    assert released["status"] == "released"
    monkeypatch.setattr(materialization, "update_goal_todo", original)
    assert materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:00Z")["write_performed"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_missing_or_stale_ledger_never_means_empty_current_work(tmp_path, monkeypatch, provider):
    args, monitor, state = scenario(tmp_path, monkeypatch, provider)
    before = state.read_bytes()
    args["ledger_path"].unlink()
    with pytest.raises(FileNotFoundError):
        materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:00Z")
    assert state.read_bytes() == before
    args["ledger_path"].write_text("")
    with pytest.raises(ValueError, match="older"):
        materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2029-01-01T00:00:00Z")
    assert state.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_runtime_override_keeps_reconciliation_on_selected_authority(tmp_path, monkeypatch, provider):
    import contextlib
    import io
    from loopx.cli import main
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.todos import list_goal_todos
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos
    project, state, registry = _fixture(tmp_path)
    runtime = tmp_path / "selected-runtime"
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    todos = read_fixture_todos(state)
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, leases=[], handoff_mode="hard_lease"),
        state_path=state, provider=provider)
    state.unlink()
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"state": "OPEN", "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "CLEAN", "statusCheckRollup": [{"name": "integration", "status": "IN_PROGRESS"}]}))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "issue-fix", "pr-lifecycle", "--url", "https://github.com/example/repo/pull/1",
            "--metadata-json", str(metadata), "--goal-id", GOAL_ID, "--project", str(project),
            "--claimed-by", AGENT_ID, "--execute-transition", "--generated-at", "2030-01-01T00:00:00Z"])
    assert code == 0
    result = json.loads(output.getvalue())["grouped_monitor_writeback"]
    assert result["write_performed"] is True
    assert result["source_authority"] == f"{provider}_v0"
    assert result["projection_delivery"] in {"delivered", "current"}
    assert not (tmp_path / "authority").exists()
    assert len(list_goal_todos(registry_path=registry, goal_id=GOAL_ID,
        runtime_root_arg=str(runtime))["todos"]) == len(todos) + 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_process_exit_after_observation_recovers_cleanup_without_business_replay(tmp_path, monkeypatch, provider):
    import subprocess
    import sys
    args, monitor, _ = scenario(tmp_path, monkeypatch, provider)
    script = """
import json, os, sys
from pathlib import Path
from loopx.capabilities.issue_fix import pr_monitor_materialization as m
args=json.loads(sys.argv[1])
for k in ('registry_path','project','ledger_path'): args[k]=Path(args[k])
original=m.update_goal_todo
def crash(**kwargs):
    original(**kwargs)
    os._exit(73)
m.update_goal_todo=crash
m.materialize_issue_fix_grouped_monitors(**args,generated_at='2030-01-01T01:00:00Z')
"""
    process = subprocess.run([sys.executable, "-c", script, json.dumps(args, default=str)],
                             capture_output=True, text=True, timeout=45)
    assert process.returncode == 73, process.stderr
    before = _monitor_todos(args["registry_path"], args["project"])[0]
    held = inspect_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
                             goal_id=GOAL_ID, todo_id=monitor["todo_id"])["lease"]
    assert held["status"] == "active"
    # The retry is a fresh invocation with its own observation time.
    retried = materialization.materialize_issue_fix_grouped_monitors(**args, generated_at="2030-01-01T01:00:01Z")
    assert retried["write_performed"] is False
    after = _monitor_todos(args["registry_path"], args["project"])[0]
    assert after == before
    released = inspect_task_lease(registry_path=args["registry_path"], runtime_root=tmp_path,
                                 goal_id=GOAL_ID, todo_id=monitor["todo_id"])["lease"]
    assert released["idempotency_key"] == held["idempotency_key"]
    assert released["status"] == "released"
