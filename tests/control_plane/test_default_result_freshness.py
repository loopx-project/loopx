"""Default admission and original-Turn result delivery on real local providers."""
from __future__ import annotations

import json
import sys
import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _run_cli, _write_fixture, _spend_run_count,
)


def _default_fixture(provider, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    todo = {"schema_version": "todo_item_v0", "todo_id": TODO_ID, "index": 1,
            "role": "agent", "status": "open", "done": False, "claimed_by": AGENT_ID,
            "text": "Validate and settle the selected delivery.", "task_class": "advancement_task",
            "archive_state": "active", "source_section": "Agent Todo"}
    if provider != "legacy":
        initialize_canonical_authority(runtime, GOAL_ID,
            build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=[todo], handoff_mode="soft_claim"),
            state_path=state, provider=provider)
    return project, runtime, registry, state


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_default_guard_restores_actual_basis_and_result_commit(provider, tmp_path, monkeypatch):
    project, runtime, registry, state = _default_fixture(provider, tmp_path, monkeypatch)
    identity = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID)
    guard_args = ("quota", "should-run", "--codex-app", *identity, "--scan-path", str(project))
    rc, first = _run_cli(registry, runtime, *guard_args, cwd=project)
    assert rc == 0 and "delivery_result_context" in first, json.dumps(first)
    context = first["delivery_result_context"]
    assert context["basis"]["todo"]["todo_id"] == TODO_ID
    rc, cold = _run_cli(registry, runtime, *guard_args, cwd=project)
    assert rc == 0 and cold["delivery_result_context"] == context, cold
    assert cold["work_input_restored"] is True
    complete = ("todo", "complete", *identity, "--note", "Candidate independently verified.")
    state.write_text(state.read_text(encoding="utf-8") + "\n## Acceptance\n\nAdditional requirement.\n", encoding="utf-8")
    rc, rejected = _run_cli(registry, runtime, *complete, cwd=project)
    assert rc == 1 and rejected.get("error_code") == "checkpoint_read_context_stale", json.dumps(rejected)
    assert _spend_run_count(runtime) == 0
    rc, refreshed = _run_cli(registry, runtime, "checkpoint-context", *identity,
                             "--turn-instance-id", context["settlement_identity"]["turn_instance_id"],
                             "--purpose", "delivery_result", cwd=project)
    assert rc == 0, refreshed
    rc, saved = _run_cli(registry, runtime, *complete, cwd=project)
    assert rc == 0 and saved["completed"], saved
    # Completing the Todo alone does not close its original refresh/spend.
    rc, pending = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--scan-path", str(project), cwd=project)
    assert pending["heartbeat_receipt"]["turn_instance_id"] == context["settlement_identity"]["turn_instance_id"], pending
    state.write_text(state.read_text(encoding="utf-8") + "\nRequirement after success.\n", encoding="utf-8")
    rc, replay = _run_cli(registry, runtime, *complete, cwd=project)
    assert rc == 0 and replay["idempotent_replay"], replay


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_concurrent_default_reads_share_original_turn_and_basis(provider, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    project, runtime, registry, _ = _default_fixture(provider, tmp_path, monkeypatch)
    barrier = Barrier(2)
    def read():
        barrier.wait()
        return _run_cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--scan-path", str(project), cwd=project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: read(), range(2)))
    assert all(rc == 0 for rc, _ in results), json.dumps(results, indent=2)
    assert results[0][1]["delivery_result_context"] == results[1][1]["delivery_result_context"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_todo_only_source_after_work_read_rejects_result_commit(provider, tmp_path, monkeypatch):
    from loopx.control_plane.todos.projection_document import recovered_todo_projection_skeleton
    from loopx.todos import list_goal_todos
    project, runtime, registry, state = _default_fixture(provider, tmp_path, monkeypatch)
    identity = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID)
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *identity, cwd=project)
    assert rc == 0 and "delivery_result_context" in guard, guard
    original_contexts = {path: path.read_bytes() for path in
                         (runtime / "goals" / GOAL_ID / "checkpoint-contexts").glob("*.json")}
    state.write_text(recovered_todo_projection_skeleton(GOAL_ID), encoding="utf-8")
    rc, rejected = _run_cli(registry, runtime, "todo", "complete", *identity,
                           "--note", "Candidate from the original valid work read.", cwd=project)
    assert rc != 0 and rejected["error_code"] == "checkpoint_goal_source_incomplete", rejected
    assert list_goal_todos(registry_path=registry, goal_id=GOAL_ID,
                          runtime_root_arg=str(runtime), todo_id=TODO_ID)["todo"]["status"] == "open"
    assert {path: path.read_bytes() for path in original_contexts} == original_contexts
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("source_loss", ["missing", "todo_only"])
def test_successful_result_replay_precedes_later_goal_source_loss(provider, source_loss, tmp_path, monkeypatch):
    from loopx.control_plane.todos.projection_document import recovered_todo_projection_skeleton
    from loopx.todos import list_goal_todos
    project, runtime, registry, state = _default_fixture(provider, tmp_path, monkeypatch)
    identity = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID)
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *identity, cwd=project)
    assert rc == 0, guard
    complete = ("todo", "complete", *identity, "--note", "Verified result committed before source loss.")
    rc, committed = _run_cli(registry, runtime, *complete, cwd=project)
    assert rc == 0 and committed["completed"], committed
    original_todo = list_goal_todos(registry_path=registry, goal_id=GOAL_ID,
                                   runtime_root_arg=str(runtime), todo_id=TODO_ID)["todo"]
    if source_loss == "missing":
        state.unlink()
    else:
        state.write_text(recovered_todo_projection_skeleton(GOAL_ID), encoding="utf-8")
    rc, replay = _run_cli(registry, runtime, *complete, cwd=project)
    assert rc == 0 and replay["idempotent_replay"], replay
    assert list_goal_todos(registry_path=registry, goal_id=GOAL_ID,
                          runtime_root_arg=str(runtime), todo_id=TODO_ID)["todo"] == original_todo
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_already_bound_historical_receipt_retains_original_policy(provider, tmp_path, monkeypatch):
    from tests.control_plane.test_quota_settlement_cli import _historical_turn
    project, runtime, registry, _ = _default_fixture(provider, tmp_path, monkeypatch)
    args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID,
        "--scan-path", str(project))
    rc, first = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0, first
    # Synthetic pre-default persisted admission; never rewrite active state.
    _historical_turn(runtime)
    log = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    original = log.read_bytes()
    rc, replay = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0, replay
    assert replay["heartbeat_receipt"]["settlement_identity"] == first["heartbeat_receipt"]["settlement_identity"]
    assert "delivery_result_context" not in replay
    assert log.read_bytes() == original
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_default_commit_keeps_basis_after_unrelated_todo_revision(provider, tmp_path, monkeypatch):
    project, runtime, registry, state = _default_fixture(provider, tmp_path, monkeypatch)
    identity = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID)
    rc, work = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *identity,
        "--scan-path", str(project), cwd=project)
    assert rc == 0, work
    rc, peer = _run_cli(registry, runtime, "todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--text", "Validate an independent follow-up.", "--claimed-by", AGENT_ID, cwd=project)
    assert rc == 0, peer
    rc, saved = _run_cli(registry, runtime, "todo", "complete", *identity,
        "--note", "Original candidate verified.", cwd=project)
    assert rc == 0 and saved["completed"], saved
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_damaged_work_input_blocks_default_dispatch_and_preserves_admission(provider, tmp_path, monkeypatch):
    project, runtime, registry, _ = _default_fixture(provider, tmp_path, monkeypatch)
    args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--scan-path", str(project))
    rc, first = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0, first
    path = next((runtime / "goals" / GOAL_ID / "checkpoint-contexts").glob("*.json"))
    original = path.read_bytes()
    path.write_text("{", encoding="utf-8")
    rc, rejected = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 1 and rejected["should_run"] is False, rejected
    assert rejected["status"] == "delivery_work_context_unavailable"
    assert rejected["heartbeat_receipt"]["turn_instance_id"] == first["heartbeat_receipt"]["turn_instance_id"]
    assert _spend_run_count(runtime) == 0
    path.write_bytes(original)
    rc, restored = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and restored["delivery_result_context"] == first["delivery_result_context"], restored


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("recovery_window", ["normal", "receipt_replaced", "input_persisted", "commit_response_lost", "commit_receipt_deleted", "commit_receipt_damaged", "commit_race", "binding_race", "terminal_commit_race", "terminal_host_cold", "terminal_validation_cold", "terminal_host_replaced", "terminal_validation_replaced", "terminal_commit_response_lost", "terminal_receipt_deleted", "terminal_receipt_damaged", "terminal_validation_failed", "terminal_stop"])
def test_historical_result_only_turn_retains_candidate_and_revalidates(provider, recovery_window, tmp_path, monkeypatch):
    from tests.test_loopx_turn_driver import _write_live_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    terminal_race = recovery_window.startswith("terminal_")
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider,
        **({"todo_metadata_extra": "no_followup=true"} if terminal_race else {}))
    goal, agent = "loopx-turn-fixture", "codex-fixture"
    state = project / f".codex/goals/{goal}/ACTIVE_GOAL_STATE.md"
    host = f'''
import json, pathlib, sys
r = json.load(sys.stdin)
assert r["delivery_result_context"]["basis"]["todo"]["todo_id"] == "todo_fixture0001"
candidate = pathlib.Path("candidate.txt")
if not r.get("result_revalidation"):
    assert not candidate.exists()
    candidate.write_text("valid candidate", encoding="utf-8")
    state = pathlib.Path({str(state)!r})
    if {not (recovery_window in {'commit_race', 'binding_race'} or terminal_race)!r}:
        state.write_text(state.read_text(encoding="utf-8") + "\\n## Acceptance\\nRetain the valid candidate and verify it again.\\n", encoding="utf-8")
else:
    assert candidate.read_text(encoding="utf-8") == "valid candidate"
    assert r["result_revalidation"]["original_context"]["read_context_id"] != r["delivery_result_context"]["read_context_id"]
    pathlib.Path("revalidation_started").touch()
json.dump({{"schema_version":"loopx_turn_result_v0", "turn_key":r["turn_key"],
    "result_kind": "user_action_required" if {recovery_window == 'terminal_stop'!r} and r.get("result_revalidation") else "validated_completion", "completed_phases":["host_execute","typed_result"],
    "classification":"retained_candidate_verified", "recommended_action":"Read remaining work.",
    "next_action":"Read remaining work.", "delivery_batch_scale":"implementation", "delivery_outcome":"outcome_progress",
    "vision_unchanged_reason":"The valid candidate satisfies the current requirements.", "summary":"Candidate verified."}}, sys.stdout)
'''
    verifier = 'import json,pathlib,sys; r=json.load(sys.stdin); assert "delivery_result_context" in r; assert pathlib.Path("candidate.txt").read_text()=="valid candidate"'
    if recovery_window == "terminal_validation_failed":
        verifier += '\nif pathlib.Path("revalidation_started").exists() and not pathlib.Path("failed_validation_once").exists():\n    pathlib.Path("failed_validation_once").touch()\n    sys.exit(1)\n'
    host_path, validator_path = project / "host.py", project / "validator.py"
    host_path.write_text(host, encoding="utf-8")
    validator_path.write_text(verifier, encoding="utf-8")
    args = ("turn", "run-once", "--host", "generic-cli", "--goal-id", goal, "--agent-id", agent,
            "--project", str(project), "--host-adapter-command-json", json.dumps([sys.executable, str(host_path)]),
            "--validation-command-json", json.dumps([sys.executable, str(validator_path)]),
            "--scan-root", str(project), "--no-global-sync", "--execute")
    # Gap1 admissions already in flight keep result-only semantics after the
    # new checkpoint default ships. Seed the real old receipt, never downgrade
    # a receipt admitted under current_agent_v1.
    from loopx.control_plane.quota.settlement import SettlementIdentity
    from loopx.control_plane.quota.heartbeat_receipt import ensure_turn_heartbeat_settlement_receipt
    rc, preview = _run_cli(registry, runtime, *args[:-1], cwd=project)
    assert rc == 0, preview
    ensure_turn_heartbeat_settlement_receipt(runtime,
        SettlementIdentity(goal_id=goal, agent_id=agent, todo_id='todo_fixture0001',
            turn_instance_id=preview['resume_turn_key']), semantic_replan_guard_scoped=True,
        semantic_replan_obligation_id=None, result_freshness='turn_result_freshness_required_v1')
    if recovery_window in {"commit_race", "binding_race"} or terminal_race:
        import os
        import subprocess
        from tests.control_plane.test_quota_settlement_cli import REPO_ROOT
        wrapper = project / "change_before_completion.py"
        mutation = f'''import pathlib
import loopx.cli_commands.turn_run_once as turn
original = turn.write_turn_validated_completion
def change_basis(*args, **kwargs):
    state = pathlib.Path({str(state)!r})
    state.write_text(state.read_text(encoding="utf-8") + "\\n## Acceptance\\nRevalidate the retained candidate.\\n", encoding="utf-8")
    return original(*args, **kwargs)
turn.write_turn_validated_completion = change_basis
'''
        if recovery_window == "binding_race":
            mutation = '''from loopx.control_plane.goals.checkpoint_context_io import TurnResultWork
original = TurnResultWork.bind
def replace_receipt(self, *args, **kwargs):
    self.read(reread=True)
    return original(self, *args, **kwargs)
TurnResultWork.bind = replace_receipt
'''
        wrapper.write_text(mutation + '''
from loopx.cli import main
raise SystemExit(main())
''', encoding="utf-8")
        changed = subprocess.run([sys.executable, str(wrapper), "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args], cwd=project,
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
        rc, first = changed.returncode, json.loads(changed.stdout)
    else:
        rc, first = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 1 and first.get("result_kind") == ("terminal_closeout_failed" if terminal_race else "validation_failed"), first
    journal_path = next((runtime / "goals" / goal / "turns").glob("*.json"))
    failed = json.loads(journal_path.read_text(encoding="utf-8"))
    assert failed["validation_stage"] == "result_freshness"
    assert failed["result_revalidation"]["candidate"]["summary"] == "Candidate verified."
    assert first["effects"]["state_written"] is terminal_race and first["effects"]["quota_spent"] is terminal_race
    assert not failed.get("effect_attempts"), "The rejected commit must release its prepared effect"
    if recovery_window in {"receipt_replaced", "input_persisted"}:
        from loopx.control_plane.goals.checkpoint_context_io import result_work_context
        from loopx.control_plane.quota.settlement import SettlementIdentity
        context = result_work_context(registry_path=registry, runtime_root=runtime,
            identity=SettlementIdentity.from_runtime_payload(failed["result_work_context"]["settlement_identity"]), reread=True)
        if recovery_window == "input_persisted":
            failed["result_work_context"] = context
            failed["status"] = "in_progress"
            for key in ("host_result", "result_kind", "receipt", "reason", "validation_stage"):
                failed.pop(key, None)
            failed["completed_phases"] = []
            journal_path.write_text(json.dumps(failed), encoding="utf-8")
    if recovery_window in {"terminal_host_cold", "terminal_validation_cold", "terminal_host_replaced", "terminal_validation_replaced"}:
        import os
        import subprocess
        from tests.control_plane.test_quota_settlement_cli import REPO_ROOT
        wrapper = project / "lose_revalidation_checkpoint.py"
        phase_count = 2 if recovery_window in {"terminal_host_cold", "terminal_host_replaced"} else 3
        wrapper.write_text(f'''import os
import loopx.control_plane.turn_driver.executor as executor
original = executor._write_journal
def lose_response(path, journal, **kwargs):
    result = original(path, journal, **kwargs)
    retry = journal.get("result_revalidation", {{}})
    if retry.get("phase") == "terminal_closeout" and len(retry.get("validation", {{}}).get("completed_phases", [])) == {phase_count}:
        os._exit(73)
    return result
executor._write_journal = lose_response
from loopx.cli import main
raise SystemExit(main())
''', encoding="utf-8")
        lost = subprocess.run([sys.executable, str(wrapper), "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args,
            "--resume-turn-key", failed["turn_key"], "--retry-failed-turn"], cwd=project,
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
        assert lost.returncode == 73, lost.stdout + lost.stderr
        if recovery_window.endswith("_replaced"):
            from loopx.control_plane.goals.checkpoint_context_io import result_work_context
            from loopx.control_plane.quota.settlement import SettlementIdentity
            state.write_text(state.read_text(encoding="utf-8") + "\nLatest acceptance must be adopted by the current Agent.\n", encoding="utf-8")
            result_work_context(registry_path=registry, runtime_root=runtime,
                identity=SettlementIdentity.from_runtime_payload(failed["result_work_context"]["settlement_identity"]), reread=True)
    if recovery_window in {"commit_response_lost", "terminal_commit_response_lost", "commit_receipt_deleted", "commit_receipt_damaged", "terminal_receipt_deleted", "terminal_receipt_damaged"}:
        import os
        import subprocess
        from tests.control_plane.test_quota_settlement_cli import REPO_ROOT
        wrapper = project / "lose_completion_response.py"
        wrapper.write_text('''import os
import loopx.cli_commands.turn_run_once as turn
original = turn.write_turn_validated_completion
def lose_response(*args, **kwargs):
    result = original(*args, **kwargs)
    assert result["ok"]
    os._exit(73)
turn.write_turn_validated_completion = lose_response
from loopx.cli import main
raise SystemExit(main())
''', encoding="utf-8")
        lost = subprocess.run([sys.executable, str(wrapper), "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args,
            "--resume-turn-key", failed["turn_key"], "--retry-failed-turn"], cwd=project,
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
        assert lost.returncode == 73, lost.stdout + lost.stderr
        index = runtime / "goals" / goal / "runs/index.jsonl"
        if not terminal_race:
            assert not index.exists() or not index.read_bytes(), "Writeback must not precede the lost result response"
        if "receipt_" in recovery_window:
            context_path = next((runtime / "goals" / goal / "checkpoint-contexts").glob("*.json"))
            if recovery_window.endswith("deleted"):
                context_path.unlink()
            else:
                context_path.write_text("{", encoding="utf-8")
        rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", goal, "--agent-id", agent, "--todo-id", "todo_fixture0001",
            "--scan-path", str(project), cwd=project)
        assert rc == 0 and guard.get("ok") is True, guard
        assert guard["heartbeat_receipt"]["settlement_identity"]["turn_instance_id"] == failed["turn_key"]
        assert guard.get("result_committed") is True and "delivery_result_context" not in guard, guard
    rc, resumed = _run_cli(registry, runtime, *args, "--resume-turn-key", failed["turn_key"],
                           "--retry-failed-turn", cwd=project)
    if recovery_window in {"terminal_validation_failed", "terminal_stop"}:
        assert rc == 1 and resumed["status"] == "failed", resumed
        pending = json.loads(journal_path.read_text(encoding="utf-8"))
        assert pending["completed_phases"] == failed["completed_phases"]
        assert pending["writeback"] == failed["writeback"] and pending["quota_spend"] == failed["quota_spend"]
        assert pending["host_attempt_count"] == 2
        if recovery_window == "terminal_stop":
            rc, stopped_replay = _run_cli(registry, runtime, *args, "--resume-turn-key", failed["turn_key"],
                "--retry-failed-turn", cwd=project)
            assert rc == 1 and not stopped_replay["effects"]["host_invoked"], stopped_replay
            assert json.loads(journal_path.read_text(encoding="utf-8"))["quota_spend"] == failed["quota_spend"]
            return
        rc, resumed = _run_cli(registry, runtime, *args, "--resume-turn-key", failed["turn_key"],
            "--retry-failed-turn", cwd=project)
    assert rc == 0 and resumed["status"] == "committed", resumed
    committed = json.loads(journal_path.read_text(encoding="utf-8"))
    assert committed["turn_key"] == failed["turn_key"]
    assert committed["host_attempt_count"] == (3 if recovery_window in {"terminal_validation_failed", "terminal_host_replaced", "terminal_validation_replaced"} else 2)
    if terminal_race:
        assert {key: value for key, value in committed["writeback"].items() if key != "completion"} == failed["writeback"]
        assert committed["quota_spend"] == failed["quota_spend"]
        runs = [json.loads(line) for line in (runtime / "goals" / goal / "runs/index.jsonl").read_text(encoding="utf-8").splitlines()]
        assert sum(run.get("classification") == "quota_slot_spent" for run in runs) == 1
    assert committed["result_work_binding"]["read_context_id"] == committed["result_work_context"]["read_context_id"]
    rc, replay = _run_cli(registry, runtime, *args, "--resume-turn-key", failed["turn_key"], cwd=project)
    assert rc == 0 and replay["replayed"] is True and replay["effects"]["host_invoked"] is False, replay


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("receipt_loss", [None, "deleted", "damaged", "writeback_deleted", "writeback_damaged"])
def test_default_mcp_reads_before_work_and_rejects_stale_result(provider, receipt_loss, tmp_path, monkeypatch):
    from tests.test_goal_mode_mcp_settlement import _write_fixture as mcp_fixture, _control, _complete_default_task, GOAL_ID as goal, AGENT_ID as agent
    from loopx.todos import add_goal_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = mcp_fixture(tmp_path, provider=provider)
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["adapter"] = {"kind": "fixture_v0", "status": "connected-delivery"}
    registry.write_text(json.dumps(registration), encoding="utf-8")
    added = add_goal_todo(registry_path=registry, goal_id=goal, role="agent", text="Verify a bounded candidate.",
        task_class="advancement_task", claimed_by=agent, continuation_policy="same_agent_non_delivery")
    runtime = tmp_path / "runtime"
    control = _control(registry)
    first = json.loads(control.should_run())
    assert first.get("delivery_result_context"), json.dumps(first)
    original = first["delivery_result_context"]
    state.write_text(state.read_text(encoding="utf-8") + "\n## Acceptance\nRecheck the additional requirement.\n", encoding="utf-8")
    # A new MCP facade/process keeps the same stored work key, without user token copying.
    control = _control(registry)
    cold = json.loads(control.should_run())
    assert cold["delivery_result_context"] == original
    result = json.loads(_complete_default_task(control, added["todo_id"], agent, "Candidate verified.",
        next_agent_todo="Verify remaining work."))
    assert result["ok"] is False, result
    assert result.get("error_code") == "checkpoint_read_context_stale", result
    refreshed = json.loads(control.should_run(revalidate_todo_id=added["todo_id"]))
    assert "delivery_result_context" in refreshed, json.dumps(refreshed, indent=2)
    assert refreshed["delivery_result_context"]["read_context_id"] != original["read_context_id"]
    assert refreshed["delivery_result_context"]["settlement_identity"] == original["settlement_identity"]
    if receipt_loss:
        run_cli = control.run_cli
        def lose_result_response(args, **kwargs):
            output = run_cli(args, **kwargs)
            step = ["refresh-state"] if receipt_loss.startswith("writeback_") else ["todo", "complete"]
            if args[:len(step)] == step and json.loads(output).get("ok"):
                return json.dumps({"ok": False, "error": "synthetic result response lost"})
            return output
        control.run_cli = lose_result_response
        lost = json.loads(_complete_default_task(control, added["todo_id"], agent, "Candidate revalidated.",
            next_agent_todo="Verify remaining work."))
        assert lost["ok"] is False, lost
        context_path = next((runtime / "goals" / goal / "checkpoint-contexts").glob("*.json"))
        if receipt_loss.endswith("deleted"):
            context_path.unlink()
        else:
            context_path.write_text("{", encoding="utf-8")
        control = _control(registry)
        pending = json.loads(control.should_run())
        assert pending.get("result_committed") is True, json.dumps({key: pending.get(key) for key in
            ("error", "reason", "error_code", "status", "result_committed", "heartbeat_receipt", "work_input_restored")})
        assert pending["heartbeat_receipt"]["settlement_identity"] == original["settlement_identity"]
    result = json.loads(_complete_default_task(control, added["todo_id"], agent, "Candidate revalidated.",
        next_agent_todo="Verify remaining work."))
    assert result["ok"] is True, result
    # Ordinary successful settlement releases the next default work identity.
    next_work = json.loads(control.should_run())
    assert next_work.get("delivery_result_context"), next_work
    assert next_work["delivery_result_context"]["settlement_identity"]["turn_instance_id"] != original["settlement_identity"]["turn_instance_id"]


@pytest.mark.parametrize("operation", ["add", "complete"])
def test_legacy_work_read_does_not_restore_retired_mutation_authority(operation, tmp_path, monkeypatch):
    project, runtime, registry, state = _default_fixture("legacy", tmp_path, monkeypatch)
    rc, admitted = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, cwd=project)
    assert rc == 0 and "delivery_result_context" in admitted, admitted
    original = state.read_bytes()
    args = (("--role", "agent", "--text", "Independent candidate.") if operation == "add"
            else ("--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--note", "Verified candidate."))
    rc, rejected = _run_cli(registry, runtime, "todo", operation, "--goal-id", GOAL_ID, *args, cwd=project)
    assert rc == 1 and rejected["error_code"] == "canonical_authority_required", rejected
    assert state.read_bytes() == original
    assert _spend_run_count(runtime) == 0
