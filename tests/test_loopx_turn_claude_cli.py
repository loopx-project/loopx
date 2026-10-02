"""Real subprocess transport and governed settlement with a synthetic CLI."""

from __future__ import annotations

import json
from functools import partial

import pytest

from loopx.cli import build_parser
from loopx.control_plane.turn_driver import build_loopx_turn_plan, run_loopx_turn_once
from loopx.control_plane.turn_driver.claude_cli import run_claude_cli_host
from loopx.control_plane.turn_driver.claude_cli_session import (
    claude_cli_session_binding,
    lineage,
    read_session,
    session_path,
)
from loopx.control_plane.turn_driver.host_failure import BuiltInHostError
from loopx.control_plane.turn_driver.host_candidate import _canonical_hash
from loopx.control_plane.quota.turn_envelope import (
    turn_envelope_action_signature_document,
)
from tests.test_dsh_goal_mode import _signed_request
from tests.test_loopx_turn_executor import _callbacks
from tests.test_loopx_turn_codex_cli import _source_admission, SOURCE_GOAL_REF
from tests.test_loopx_turn_executor import _replace_source_goal, INSTANCE_B
from loopx.control_plane.goals.first_party_host_admission import (
    FirstPartyHostRuntimeRejected,
)


def fixture(tmp_path, monkeypatch):
    executable = tmp_path / "synthetic-claude"
    log = tmp_path / "launches.jsonl"
    executable.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys,time
args=sys.argv[1:]
prompt=sys.stdin.read()
session=args[args.index('--resume' if '--resume' in args else '--session-id')+1]
with open(os.environ['CLAUDE_FIXTURE_LOG'],'a') as f:
 f.write(json.dumps({'args':args,'goal':os.environ['LOOPX_TURN_GOAL_ID'],'agent':os.environ['LOOPX_TURN_AGENT_ID'],'todo':os.environ['LOOPX_TURN_TODO_ID']})+'\\n')
print(json.dumps({'type':'system','subtype':'init','session_id':session}),flush=True)
if os.environ.get('CLAUDE_FIXTURE_SLEEP'):time.sleep(30)
if os.environ.get('CLAUDE_FIXTURE_ERROR'):
 print(json.dumps({'type':'result','session_id':session,'is_error':True,'result':'private-error-sentinel'}))
 sys.exit(0)
pathlib.Path('proof.txt').write_text('independent artifact\\n')
candidate={'result_kind':'validated_progress','classification':'artifact_ready','summary':'Created a fixture artifact.','next_action':'Validate the artifact.','vision_unchanged_reason':'The original objective remains.'}
print(json.dumps({'type':'result','session_id':session,'is_error':False,'structured_output':candidate}))
""")
    executable.chmod(0o700)
    monkeypatch.setenv("CLAUDE_FIXTURE_LOG", str(log))
    request = _signed_request(
        goal_id="fixture-goal", agent_id="worker", todo_id="todo_fixture"
    )
    request["session"] = {
        "action": "start_new",
        "context_policy": {"mode": "resume_if_available"},
    }
    return executable, log, request


def runner(tmp_path, executable, **kwargs):
    return partial(
        run_claude_cli_host,
        runtime_root=tmp_path / "runtime",
        project=tmp_path,
        claude_bin=str(executable),
        **kwargs,
    )


def test_cli_exposes_native_host_without_changing_default_tools():
    args = build_parser().parse_args(
        [
            "turn",
            "run-once",
            "--goal-id",
            "g",
            "--agent-id",
            "a",
            "--project",
            ".",
            "--host",
            "claude-code",
            "--claude-model",
            "operator-model",
        ]
    )
    assert args.claude_workspace_write is False
    assert args.claude_model == "operator-model"


def test_real_native_process_retains_lineage_model_and_explicit_resume(
    tmp_path, monkeypatch
):
    executable, log, request = fixture(tmp_path, monkeypatch)
    run = runner(tmp_path, executable, model="operator-model", reasoning_effort="high")
    result = run(request)
    assert result["result_kind"] == "validated_progress"
    assert result["completed_phases"] == ["host_execute", "typed_result"]
    identity = lineage(request["turn_envelope"])
    first = read_session(tmp_path / "runtime", identity)
    binding = claude_cli_session_binding(tmp_path / "runtime", request["turn_envelope"])
    assert binding == {"schema_version": "loopx_turn_session_binding_v0", **identity}
    assert session_path(tmp_path / "runtime", identity).stat().st_mode & 0o777 == 0o600
    request["session"]["action"] = "resume"
    run(request)
    launches = [json.loads(line) for line in log.read_text().splitlines()]
    assert (
        launches[0]["args"][launches[0]["args"].index("--tools") + 1]
        == "Read,Glob,Grep"
    )
    assert (
        launches[0]["args"][launches[0]["args"].index("--model") + 1]
        == "operator-model"
    )
    assert (
        launches[1]["args"][launches[1]["args"].index("--resume") + 1]
        == first["session_id"]
    )
    assert launches[0]["agent"] == "worker" and launches[0]["todo"] == "todo_fixture"
    assert "--strict-mcp-config" in launches[0]["args"]
    assert (
        "private-error-sentinel"
        not in session_path(tmp_path / "runtime", identity).read_text()
    )


def test_fresh_context_refuses_ambient_resume_and_corrupted_binding(
    tmp_path, monkeypatch
):
    executable, log, request = fixture(tmp_path, monkeypatch)
    run = runner(tmp_path, executable)
    run(request)
    request["session"]["context_policy"]["mode"] = "fresh"
    run(request)
    launches = [json.loads(line)["args"] for line in log.read_text().splitlines()]
    assert "--resume" not in launches[1]
    assert (
        launches[0][launches[0].index("--session-id") + 1]
        != launches[1][launches[1].index("--session-id") + 1]
    )
    session_path(tmp_path / "runtime", lineage(request["turn_envelope"])).write_text(
        "[]"
    )
    with pytest.raises(ValueError, match="binding is invalid"):
        run(request)
    assert len(log.read_text().splitlines()) == 2


def plan_for(request):
    envelope = request["turn_envelope"]
    envelope.update(
        ok=True,
        should_run=True,
        effective_action="normal_run",
        compaction={"within_budget": True},
    )
    envelope["action"]["delivery_allowed"] = True
    signature = _canonical_hash(turn_envelope_action_signature_document(envelope))
    envelope["action_signature"].update(source_hash=signature, envelope_hash=signature)
    return build_loopx_turn_plan(
        envelope, host="claude-code", execution_mode="isolated-headless"
    )


def test_native_turn_independent_validation_and_replay_settle_once(
    tmp_path, monkeypatch
):
    executable, log, request = fixture(tmp_path, monkeypatch)
    plan = plan_for(request)
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def validator(_plan, _result):
        assert (tmp_path / "proof.txt").read_text() == "independent artifact\n"
        return {
            "status": "passed",
            "validator_kind": "artifact",
            "summary": "Artifact contents match.",
        }

    common = dict(
        host_runner=runner(tmp_path, executable, writable=True),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        execute=True,
        timeout_seconds=10,
        task_validator=validator,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )
    first = run_loopx_turn_once(plan, **common)
    replay = run_loopx_turn_once(plan, **common)
    assert first["status"] == "committed", first
    assert replay["replayed"] is True
    assert len(log.read_text().splitlines()) == 1
    assert calls == {"writeback": 1, "spend": 1, "scheduler": 1}


def test_native_error_event_with_zero_exit_never_spends(tmp_path, monkeypatch):
    executable, log, request = fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_FIXTURE_ERROR", "1")
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)
    result = run_loopx_turn_once(
        plan_for(request),
        host_runner=runner(tmp_path, executable),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        execute=True,
        timeout_seconds=10,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )
    assert result["ok"] is False
    assert result["host_failure"]["retryable"] is False
    assert calls == {"writeback": 0, "spend": 0, "scheduler": 0}
    assert "private-error-sentinel" not in json.dumps(result)


def test_timeout_reaps_child_and_retains_exact_recovery_binding(tmp_path, monkeypatch):
    executable, log, request = fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_FIXTURE_SLEEP", "1")
    with pytest.raises(BuiltInHostError) as captured:
        runner(tmp_path, executable, timeout_seconds=0.3)(request)
    assert captured.value.failure_kind == "executor_timeout"
    assert captured.value.recovery_kind == "resume_session"
    assert claude_cli_session_binding(tmp_path / "runtime", request["turn_envelope"])


def test_signed_authority_tampering_never_starts_native_host(tmp_path, monkeypatch):
    executable, log, request = fixture(tmp_path, monkeypatch)
    request["turn_envelope"]["action"]["primary_action"] = "Unsigned change"
    with pytest.raises(ValueError, match="signature"):
        runner(tmp_path, executable)(request)
    assert not log.exists()


def test_rejected_validation_retries_only_validation(tmp_path, monkeypatch):
    executable, log, request = fixture(tmp_path, monkeypatch)
    plan = plan_for(request)
    calls = {"writeback": 0, "spend": 0, "scheduler": 0}
    writeback, spend, scheduler = _callbacks(calls)
    common = dict(
        host_runner=runner(tmp_path, executable, writable=True),
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        execute=True,
        timeout_seconds=10,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )
    rejected = run_loopx_turn_once(
        plan,
        task_validator=lambda *_: {
            "status": "failed",
            "validator_kind": "artifact",
            "summary": "Acceptance is incomplete.",
            "recovery_kind": "repair_required",
        },
        **common,
    )
    assert rejected["result_kind"] == "validation_failed"
    assert calls == {"writeback": 0, "spend": 0, "scheduler": 0}
    accepted = run_loopx_turn_once(
        plan,
        retry_failed=True,
        task_validator=lambda *_: {
            "status": "passed",
            "validator_kind": "artifact",
            "summary": "Independent acceptance now passes.",
        },
        **common,
    )
    assert accepted["status"] == "committed"
    assert len(log.read_text().splitlines()) == 1
    assert calls == {"writeback": 1, "spend": 1, "scheduler": 1}


def test_recreated_goal_cannot_resume_old_claude_session(tmp_path, monkeypatch):
    executable, log, request = fixture(tmp_path, monkeypatch)
    admission = _source_admission(tmp_path)
    request["goal_ref"] = SOURCE_GOAL_REF
    run = runner(tmp_path, executable, goal_admission=admission)
    run(request)
    _replace_source_goal(admission.registry_path, INSTANCE_B)
    request["session"]["action"] = "resume"
    with pytest.raises(FirstPartyHostRuntimeRejected):
        run(request)
    assert len(log.read_text().splitlines()) == 1
