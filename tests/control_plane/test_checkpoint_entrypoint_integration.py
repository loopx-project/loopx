"""Original checkpoint identity survives bounded native CLI delivery."""
from __future__ import annotations

import hashlib
import argparse
import io
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.cli import build_parser
from loopx.cli_commands.quota_capture import read_context_capture
from loopx.control_plane.runtime.private_permissions import require_private_path
from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference
from tests.control_plane.test_checkpoint_read_context import _missing, _initialize_fixture_authority
from tests.control_plane.test_quota_settlement_cli import GOAL_ID, _run_cli


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_bounded_cli_preserves_complete_basis_and_original_read(tmp_path, monkeypatch, provider):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, binding, _, original = _missing(tmp_path,
        initialize=lambda project, runtime, _: _initialize_fixture_authority(project, runtime, provider=provider))
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    prose = "Preserve the independently accepted direction.\n" * 76000
    state.write_text(state.read_text(encoding="utf-8") + "\n## Source\n" + prose, encoding="utf-8")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = (state.read_bytes(), index.read_bytes())
    rc, view = _run_cli(registry, runtime, "checkpoint-context", *binding,
        "--max-page-output-bytes", "64000", cwd=project)
    assert rc == 0 and view["ok"], view
    assert view["settlement_identity"] == original["settlement_identity"]
    reference, descriptor = read_context_read_reference(view["result_details"]["reference_file"])
    require_private_path(reference)
    saved = reference.parent / "result.json"
    require_private_path(saved)
    raw = saved.read_bytes()
    complete = json.loads(raw)
    assert complete["read_context_id"] == view["read_context_id"]
    assert complete["basis"]["goal"]["prose"].endswith(prose.strip())
    assert complete["basis"]["source"]["authority"] == f"{provider}_v0"
    assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
    assert (state.read_bytes(), index.read_bytes()) == before

    command = shlex.split(view["result_details"]["command"])
    args = command[command.index("quota"):]
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=project, stdin=subprocess.DEVNULL, capture_output=True, timeout=40)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) <= 64000  # Physical bytes, including Windows CRLF.
    page = json.loads(result.stdout)
    parts = bytearray()
    while True:
        assert page["ok"] and page["offset"] == len(parts)
        parts.extend(page["content"].encode("utf-8"))
        if page["eof"]:
            break
        args = shlex.split(page["next_command"])
        page = read_context_capture(build_parser().parse_args(["--format", "json", *args[args.index("quota"):]]))
    assert bytes(parts) == raw
    assert (state.read_bytes(), index.read_bytes()) == before
    saved.write_bytes(raw + b" ")
    failed = read_context_capture(build_parser().parse_args(["--format", "json", *command[command.index("quota"):]]))
    assert failed["ok"] is False and "digest" in failed["reason"]


@pytest.mark.parametrize("next_action", [False, True])
def test_first_writeback_composes_recommendation_and_checkpoint_locks(next_action, tmp_path):
    from tests.control_plane import test_refresh_checkpoint_recovery as fixture

    project, runtime, registry = fixture._write_fixture(tmp_path, provider="sqlite")
    scope = ("--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID)
    bound = (*scope, "--todo-id", fixture.TODO_ID, "--turn-instance-id", fixture.TURN_ID)
    local = ("--no-global-sync", "--suppress-external-sinks")
    rc, initial = fixture._run_cli(registry, runtime, "refresh-state", *scope, *local,
        "--vision-summary", "Validate the scoped change.",
        "--vision-acceptance", "Focused validation passes.", cwd=project)
    assert rc == 0, initial
    rc, guard = fixture._run_cli(registry, runtime, "quota", "should-run", "--codex-app", *bound, cwd=project)
    assert rc == 0, guard
    flag = ("--next-action", "Verify the scoped delivery evidence.") if next_action else ()
    rc, result = fixture._run_cli(registry, runtime, "refresh-state", *bound, *local, *flag,
        "--classification", "validated_change", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--progress-scope", "goal", cwd=project)
    assert rc == 0 and result["appended"], result
    assert result["settlement_identity"] == guard["heartbeat_receipt"]["settlement_identity"]
    assert not registry.with_suffix(".json.lock.incidents.jsonl").exists()
    assert fixture._spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_observers_execute_real_preview_and_leave_work_admission_to_mcp(provider, tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "loopx/claude_goal_mode/hooks"))
    from loopx.claude_goal_mode.hooks import goal_policy
    from loopx.claude_goal_mode.statusline import goal_status
    from loopx.claude_goal_mode.scripts import goalmode_cmd
    from scripts import external_scheduler_worker as worker
    from tests.control_plane.test_default_checkpoint_continuation import _fixture
    from tests.test_goal_mode_mcp_settlement import AGENT_ID, GOAL_ID, _control

    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    monkeypatch.chdir(state.parent)
    runtime = tmp_path / "runtime"
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    contexts = runtime / "goals" / GOAL_ID / "checkpoint-contexts"
    before_contexts = {p.name: p.read_bytes() for p in contexts.glob("*.json")}
    def prefix():
        return [sys.executable, "-m", "loopx.cli"]
    monkeypatch.setattr(goal_policy, "_gh_prefix", prefix)
    monkeypatch.setattr(goal_status, "_gh_prefix", prefix)
    monkeypatch.setattr(goalmode_cmd, "gh_prefix", prefix)
    context = {"goal_id": GOAL_ID, "registry": str(registry), "agent_id": AGENT_ID}
    monkeypatch.setattr(goal_status, "active_context", lambda _: context)
    original_run = subprocess.run
    observed = []

    def execute(command, **kwargs):
        if "should-run" in command:
            assert "--dry-run" in command
            result = original_run(command, **kwargs)
            assert result.returncode == 0, result.stderr
            payload = json.loads(result.stdout)
            assert payload["ok"] and payload["should_run"]
            assert not payload.get("heartbeat_receipt") and not payload.get("delivery_result_context")
            observed.append(payload)
            return result
        return original_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", execute)
    assert goal_policy.should_run(str(registry), GOAL_ID, AGENT_ID) is True
    _, detail = goalmode_cmd.goal_detail(context)
    assert detail["ok"]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(state.parent)})))
    goal_status.main()
    assert "▶" in capsys.readouterr().out
    args = argparse.Namespace(cli_bin=sys.executable, registry=registry, runtime_root=runtime,
        goal_id=GOAL_ID, agent_id=AGENT_ID, runtime_profile="generic_cli")
    command = worker.build_should_run_command(args)
    command[1:1] = ["-m", "loopx.cli"]
    assert "--dry-run" in command
    worker_result = worker.run_quota_should_run(command, timeout_seconds=30)
    assert worker_result["ok"] and worker_result["should_run"]
    assert not worker_result.get("heartbeat_receipt") and not worker_result.get("delivery_result_context")
    # The scheduler's capped-process provider uses Popen, while the other
    # three scripts use run. Inspect both real transports, never stub a result.
    assert len(observed) == 3
    observed.append(worker_result)
    assert len(observed) == 4
    assert index.read_bytes() == before
    assert {p.name: p.read_bytes() for p in contexts.glob("*.json")} == before_contexts
    monkeypatch.setattr(subprocess, "run", original_run)
    guard = json.loads(_control(registry).should_run())
    assert guard["ok"] and guard["delivery_result_context"]["settlement_identity"]["todo_id"] == todo
    assert guard["heartbeat_receipt"]["settlement_identity"] == guard["delivery_result_context"]["settlement_identity"]
    assert index.read_bytes() == before  # Admission is not qualified progress or a debit.


@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize("source_kind", ["items", "terminal"])
def test_summary_transport_keeps_full_unicode_and_private_large_response(tmp_path, monkeypatch, large, source_kind):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.todos.summary_item import compact_todo_summary_items, todo_planning_source_items
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    text = "保留完整方向😀" * (400000 if large else 2)
    row = {"todo_id": "todo_summary_transport", "text": text, "status": "open",
           "task_class": "advancement_task", "note": "Continue the original work.",
           "successor_todo_ids": ["todo_original_successor"]}
    original, encoded, calls = effect_runtime._encode_rpc_json, [], []

    def encode(value):
        raw = original(value)
        if value.get("method") == "todo.context.page":
            encoded.append(len(raw) + 1)
            if "params_ref" in value:
                path = Path(value["params_ref"]["path"])
                require_private_path(path)
                assert hashlib.sha256(path.read_bytes()).hexdigest() == value["params_ref"]["sha256"]
        return raw

    real = effect_runtime.effect_runtime_result

    def invoke(method, params, **kwargs):
        calls.append((method, kwargs.get("large_local_snapshot", False)))
        return real(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "_encode_rpc_json", encode)
    monkeypatch.setattr(effect_runtime, "effect_runtime_result", invoke)
    if source_kind == "items":
        result = compact_todo_summary_items([row])
    else:
        result = todo_planning_source_items({"items": [row]}, include_terminal=True)
    assert len(result) == 1 and result[0]["todo_id"] == row["todo_id"]
    assert result[0]["text"] == text
    assert result[0]["handoff_note"]["successor_todo_ids"] == row["successor_todo_ids"]
    assert row["text"] == text and row["note"] == "Continue the original work."
    assert calls == [("todo.context.page", large)]
    assert encoded[-1] <= effect_runtime.MAX_REQUEST_BYTES


@pytest.mark.parametrize("large", [False, True])
def test_summary_transport_preserves_typed_rejection(tmp_path, monkeypatch, large):
    from loopx.control_plane.todos.summary_item import _project
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="summary items must be an array"):
        _project({"items": "invalid", "unused": "x" * (100000 if large else 0)})


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_mcp_settlement_retains_the_admitted_quota_source(provider, tmp_path, monkeypatch):
    from tests.control_plane.host_checkpoint_fixture import checkpoint_decision
    from tests.control_plane.test_default_checkpoint_continuation import _fixture
    from tests.test_goal_mode_mcp_settlement import AGENT_ID, GOAL_ID, _control

    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    monkeypatch.chdir(state.parent)
    control = _control(registry)
    guard = json.loads(control.should_run())
    source = guard["interaction_contract"]["cli_channel"]["quota_spend_source"]
    assert source == "visible-goal", "Reach the non-heartbeat production admission"
    identity = guard["delivery_result_context"]["settlement_identity"]
    index = tmp_path / "runtime" / "goals" / GOAL_ID / "runs/index.jsonl"
    calls = []
    invoke = control.run_cli

    def record(args, **kwargs):
        output = invoke(args, **kwargs)
        calls.append((list(args), json.loads(output)))
        return output  # Observe the real provider; never replace its source.

    control.run_cli = record
    intent = dict(next_agent_todo="Verify the next independent candidate.")
    retained = json.loads(control.complete_task(todo, AGENT_ID, "Candidate verified.", **intent))
    assert retained["stage"] == "direction_pending" and retained["completed"]
    assert retained["settlement_identity"] == identity
    assert not any(json.loads(line).get("classification") == "quota_slot_spent"
                   for line in index.read_text(encoding="utf-8").splitlines())
    original_rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    original_R = [row for row in original_rows if row.get("settlement_identity") == identity
                  and row.get("refresh_recovery", {}).get("decision") == "append"]
    assert len(original_R) == 1, "Direction must follow an actual original R, not only Todo completion"
    frozen_R = {Path(original_R[0][key]): hashlib.sha256(Path(original_R[0][key]).read_bytes()).hexdigest()
                for key in ("json_path", "markdown_path")}
    assert all(path.is_file() and path.stat().st_size for path in frozen_R)
    assert retained["context"]["purpose"] == "supplement_checkpoint"
    before = index.read_bytes()
    denied = json.loads(invoke(["quota", "spend-slot", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", todo, "--turn-instance-id", identity["turn_instance_id"],
        "--slots", "1", "--source", "adapter", "--execute"]))
    assert not denied["ok"] and "turn-scoped settlement is valid only for" in denied["reason"]
    assert index.read_bytes() == before

    decision = checkpoint_decision(retained["context"])
    authored = dict(read_context_id=decision["read_context_id"],
        agent_vision=decision.get("agent_vision"),
        vision_unchanged_reason=decision.get("vision_unchanged_reason") or None)
    accepted = json.loads(control.complete_task(todo, AGENT_ID, "Candidate verified.", **intent, **authored))
    assert accepted["settlement_complete"] and accepted["settlement_identity"] == identity
    debit = accepted["settlement"]["quota_spend"]
    assert debit["source"] == source
    for args, result in calls:
        if args[:2] == ["quota", "spend-slot"]:
            assert args[args.index("--source") + 1] == source
            assert result["settlement_identity"] == identity
    replay = json.loads(control.complete_task(todo, AGENT_ID, "Candidate verified.", **intent, **authored))
    assert replay.get("settlement_complete") and replay.get("settlement_identity") == identity, replay
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" and
               row["settlement_identity"] == identity for row in rows) == 1
    assert replay["completion"]["completion_receipt_id"] == accepted["completion"]["completion_receipt_id"]
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in frozen_R.items())


def _scoped_host_request():
    from loopx.control_plane.host_adapter_settlement import HostTodoSettlementRequest

    return HostTodoSettlementRequest(goal_id="source-fixture", agent_id="agent", todo_id="todo_abc123",
        runtime_profile="claude_code", legacy_host_surface="claude_code", scheduler_owner="agent_cli_loop",
        execution_mode="interactive", completion_args=("todo", "complete", "todo_abc123", "--agent-id", "agent"))


@pytest.mark.parametrize("source", [None, 0, "adapter", "future-source", {}])
def test_invalid_projected_source_stops_before_lifecycle(source, tmp_path, monkeypatch):
    from loopx.control_plane import host_adapter_settlement as adapter
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    request = _scoped_host_request()
    identity = adapter._prepare(request)["identity"]
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args[:2] == ["quota", "should-run"], "An invalid source must not permit lifecycle effects"
        return json.dumps({"ok": True, "heartbeat_receipt": {"settlement_identity": identity},
            "interaction_contract": {"cli_channel": {"quota_spend_source": source}}})

    result = json.loads(adapter.settle_host_todo_completion(request, run_cli=run))
    assert len(calls) == 1 and not result["ok"]
    assert result["settlement"]["failed_stage"] == "guard"
    assert "spend source" in result["settlement"]["reason"]


def test_provider_argument_cannot_bind_its_own_or_a_future_result(tmp_path, monkeypatch):
    from loopx.control_plane import host_adapter_settlement as adapter
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    plan = adapter._prepare(_scoped_host_request())["provider_effect"]
    spend = next(step for step in plan["steps"] if step["step_kind"] == "quota_spend")
    spend["argument_bindings"][0]["step_kind"] = "quota_spend"
    with pytest.raises(RuntimeError, match="argument binding is invalid"):
        adapter._decode_provider_steps(plan)


@pytest.mark.parametrize("state,reported,expected", [
    ("spend_required", "heartbeat", False), ("settled", None, True), ("spend_required", None, False)])
def test_finalizer_requires_matching_source_or_a_proven_original_replay(state, reported, expected, tmp_path, monkeypatch):
    from loopx.control_plane import host_adapter_settlement as adapter
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    request = _scoped_host_request()
    identity = adapter._prepare(request)["identity"]
    receipt = {"ok": True, "settlement_identity": identity, "settlement_result": {"failure": None}}
    guard = {"ok": True, "heartbeat_receipt": {"settlement_identity": identity},
        "interaction_contract": {"cli_channel": {"quota_spend_source": "visible-goal"}}}
    spend = {**receipt, "appended": True, "source": reported} if reported is not None else {
        **receipt, "appended": False, "idempotent_replay": True}
    outputs = [("guard", guard), ("lifecycle_completion", {**receipt, "completed": True, "status": "done"}),
        ("durable_writeback", {**receipt, "appended": True,
            "settlement_progress": {"state": state, "quota_spend_source": "visible-goal"}}),
        ("quota_spend", spend)]
    payload = adapter._request_payload(request, phase="finalize",
        provider_outcomes=[{"step_kind": kind, "output": json.dumps(value)} for kind, value in outputs])
    result = adapter._runtime_reduction(payload, phase="finalize")["result"]
    assert result["ok"] is expected
    if not expected:
        assert result["settlement"]["failed_stage"] == "quota_spend"
        assert "source does not match" in result["settlement"]["reason"]


def test_modern_writeback_cannot_fall_back_to_a_different_literal_source(tmp_path, monkeypatch):
    from loopx.control_plane import host_adapter_settlement as adapter
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    request = _scoped_host_request()
    identity = adapter._prepare(request)["identity"]
    receipt = {"ok": True, "settlement_identity": identity, "settlement_result": {"failure": None}}
    calls = []

    def run(args, **kwargs):
        calls.append(args[:2])
        if args[:2] == ["quota", "should-run"]:
            return json.dumps({"ok": True, "heartbeat_receipt": {"settlement_identity": identity},
                "interaction_contract": {"cli_channel": {"quota_spend_source": "visible-goal"}}})
        if args[:2] == ["todo", "complete"]:
            return json.dumps({**receipt, "completed": True, "status": "done"})
        assert args[0] == "refresh-state", "No debit is allowed without the original source readback"
        return json.dumps({**receipt, "appended": True})

    with pytest.raises(RuntimeError, match="required argument binding is unavailable"):
        adapter.settle_host_todo_completion(request, run_cli=run)
    assert calls == [["quota", "should-run"], ["todo", "complete"], ["refresh-state", "--goal-id"]]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_legacy_pending_completion_cold_read_keeps_its_original_protocol(tmp_path, monkeypatch, provider):
    from dataclasses import replace
    from loopx.todos import add_goal_todo
    from loopx.control_plane.host_adapter_settlement import HostTodoSettlementRequest, settle_host_todo_completion
    from tests import test_goal_mode_mcp_completion_validation as legacy
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.host_checkpoint_fixture import checkpoint_decision

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, state = legacy._write_fixture(tmp_path, provider=provider)
    monkeypatch.chdir(state.parent)
    todo = add_goal_todo(registry_path=registry, goal_id=legacy.GOAL_ID, role="agent",
        text="Recover the original pending candidate.", task_class="advancement_task", claimed_by=legacy.AGENT)["todo_id"]
    control = legacy._control(registry)
    request = HostTodoSettlementRequest(goal_id=legacy.GOAL_ID, agent_id=legacy.AGENT, todo_id=todo,
        runtime_profile="claude_code", legacy_host_surface="claude_code", scheduler_owner="agent_cli_loop",
        execution_mode="interactive", first_delivery=True,
        completion_args=("todo", "complete", "--goal-id", legacy.GOAL_ID, "--todo-id", todo,
            "--claimed-by", legacy.AGENT, "--agent-id", legacy.AGENT, "--evidence", "Verified candidate."))
    work = json.loads(settle_host_todo_completion(request, run_cli=control.run_cli))
    assert work["stage"] == "result_review_pending"
    pending = json.loads(settle_host_todo_completion(
        replace(request, delivery_read_context_id=work["context"]["read_context_id"]), run_cli=control.run_cli))
    assert pending["stage"] == "direction_pending"
    identity = pending["settlement_identity"]
    index = tmp_path / "runtime/goals" / legacy.GOAL_ID / "runs/index.jsonl"
    assert not index.exists(), "The historical explicit protocol asks direction before its first writeback"
    cold = legacy._control(registry)
    context = json.loads(cold.review_task_vision(todo, legacy.AGENT))
    assert context["ok"] and context["purpose"] == "first_delivery"
    assert context["settlement_identity"] == identity
    decision = checkpoint_decision(context)
    authored = dict(first_delivery=True, read_context_id=decision["read_context_id"], agent_vision=decision.get("agent_vision"),
        vision_unchanged_reason=decision.get("vision_unchanged_reason") or "")
    accepted = json.loads(cold.complete_task(todo, legacy.AGENT, "Verified candidate.", **authored))
    (tmp_path / "legacy-acceptance.json").write_text(json.dumps(accepted, indent=2), encoding="utf-8")
    assert accepted["ok"] and accepted["settlement_identity"] == identity, accepted
    assert accepted["settlement"]["quota_spend"]["ok"]
    assert accepted["completion"]["completion_receipt_id"] == pending["completion"]["completion_receipt_id"]
    before = index.read_bytes()
    replay = json.loads(cold.complete_task(todo, legacy.AGENT, "Verified candidate.", **authored))
    assert replay["settlement"]["quota_spend"]["idempotent_replay"]
    assert index.read_bytes() == before


def test_absent_legacy_enrollment_cannot_select_a_pre_result_direction(tmp_path, monkeypatch):
    from dataclasses import replace
    from loopx.control_plane import host_adapter_settlement as adapter
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    request = replace(_scoped_host_request(), current_agent_checkpoint=True, retained_result_present=False)
    context = adapter._runtime_reduction(adapter._request_payload(request, phase="vision_context"), phase="vision_context")
    assert context["args"][context["args"].index("--purpose") + 1] == "supplement_checkpoint"
