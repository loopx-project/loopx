"""Real Turn/Native proof/validator/store path; Host responses are deterministic."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from loopx.cli import build_parser
from loopx.cli_commands.turn import handle_turn_command
from loopx.capabilities.native_chat.steward_turn import StewardTurnHost
from loopx.control_plane.turn_driver.host_candidate import build_result
from loopx.control_plane.work_items.task_lease import inspect_task_lease
from tests.control_plane.test_default_result_freshness import _default_fixture
from tests.control_plane.test_checkpoint_semantic_basis import admitted as _admitted_fixture
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, _run_cli, REPO_ROOT

admitted = _admitted_fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("renew_at", ["none", "generation", "validation"])
def test_native_completion_uses_current_proof_without_repeating_work(provider, renew_at, tmp_path, monkeypatch):
    project, runtime, registry, _ = _default_fixture(provider, tmp_path, monkeypatch, handoff_mode="hard_lease")
    task = ("--goal-id", GOAL_ID, "--todo-id", TODO_ID)

    def cli(*args):
        return _run_cli(registry, runtime, *args, cwd=project)

    rc, baseline = cli("refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--vision-summary", "Verify the original output.", "--vision-acceptance", "The independent validator passes.",
        "--no-global-sync", "--suppress-external-sinks")
    assert rc == 0, baseline
    rc, acquired = cli("task-lease", "acquire", *task, "--owner", AGENT_ID,
        "--idempotency-key", "native-original-execution", "--ttl-seconds", "1800")
    assert rc == 0, acquired
    frozen = acquired["lease"]
    renewal_args = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "task-lease", "renew", *task, "--owner", AGENT_ID, "--idempotency-key", frozen["idempotency_key"],
        "--expected-version", str(frozen["version"]), "--ttl-seconds", "2400"]
    validator = project / "validate.py"
    validator.write_text(f'''import json,os,pathlib,subprocess,sys
assert pathlib.Path('candidate.txt').read_text() == 'verified output'
with pathlib.Path('validation-count.txt').open('a') as count: count.write('validated\\n')
if {renew_at == 'validation'!r}:
    result=subprocess.run([sys.executable,'-m','loopx.entrypoint',*{renewal_args!r}],
        env={{**os.environ,'PYTHONPATH':{str(REPO_ROOT)!r}}},capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['renewed']
''', encoding="utf-8")
    generations, directions, proof_errors = [], [], []

    def host(request, launch_scope):
        with launch_scope():
            pass
        if request.get("phase") == "checkpoint_direction":
            context = request["checkpoint_context"]
            assert context["basis"]["todo"]["status"] == "done"
            directions.append(context["read_context_id"])
            return {"read_context_id": context["read_context_id"],
                    "vision_unchanged_reason": "The committed output preserves the current route."}
        assert not (project / "candidate.txt").exists()
        (project / "candidate.txt").write_text("verified output")
        generations.append(request["delivery_result_context"]["read_context_id"])
        if renew_at == "generation":
            rc, renewed = cli(*renewal_args[6:])
            assert rc == 0 and renewed["renewed"], renewed
        return build_result(request, {"result_kind": "validated_completion", "classification": "candidate_verified",
            "summary": "Verified the original output.", "next_action": "Review remaining authorized work.",
            "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
            "work_evidence": ["artifact:candidate.txt"], "validation_evidence": ["check:independent-output"]},
            allow_completion=True)

    controller = SimpleNamespace(registry_path=registry, coordination_runtime_root=runtime)
    native = SimpleNamespace(controller=controller)

    def proof():
        try:
            return StewardTurnHost._task_lease_proof(native,
                {"actor": {"goal_id": GOAL_ID, "agent_id": AGENT_ID}, "todo_id": TODO_ID}, frozen)
        except (ValueError, RuntimeError) as error:
            proof_errors.append(str(error))
            raise

    args = build_parser().parse_args(["turn", "run-once", "--host", "generic-cli",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--project", str(project), "--execute",
        "--validation-command-json", json.dumps([sys.executable, str(validator)]), "--no-global-sync"])
    # The production Native host owns this internal binding; it is deliberately
    # absent from public CLI host choices.
    args.host, args.session_scope = "codex-app-server", "agent"
    payload = {}
    options = dict(registry_path=registry, runtime_root_arg=str(runtime),
        output_format=lambda *_a, **_kw: "json", print_payload=lambda value, *_a, **_kw: payload.update(value),
        native_host_runner=host,
        native_session_binding_resolver=lambda _envelope: {"schema_version": "loopx_turn_session_binding_v0",
            "goal_id": GOAL_ID, "agent_id": AGENT_ID}, native_completion_lease_resolver=proof)
    rc = handle_turn_command(args, **options)
    assert rc == 0 and payload["status"] == "committed", json.dumps({"result": payload, "proof_errors": proof_errors}, indent=2)
    assert len(generations) == len(directions) == 1
    journal = json.loads(next((runtime / "goals" / GOAL_ID / "turns").glob("*.json")).read_text())
    assert journal["result_work_context"]["read_context_id"] == generations[0]
    assert journal["result_work_context"]["settlement_identity"]["todo_id"] == TODO_ID
    assert (project / "validation-count.txt").read_text().splitlines() == ["validated"]
    rows = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "candidate_verified" for row in rows) == 1
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    rc, completed = cli("todo", "list", *task)
    assert rc == 0 and completed["todo"]["status"] == "done", completed
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    committed_bytes = index.read_bytes()
    args.resume_turn_key, args.retry_failed_turn = payload["resume_turn_key"], True
    payload.clear()
    rc = handle_turn_command(args, **options)
    assert rc == 0 and payload["replayed"] is True, payload
    assert len(generations) == len(directions) == 1 and not proof_errors
    assert (project / "validation-count.txt").read_text().splitlines() == ["validated"]
    assert index.read_bytes() == committed_bytes


def test_original_reference_refresh_is_read_only_and_current_proof_keeps_cas(admitted):
    cli, task, _, frozen, _, _, _, registry, runtime, snapshot, complete = admitted
    rc, renewed = cli("task-lease", "renew", *task, "--owner", AGENT_ID,
        "--idempotency-key", frozen["idempotency_key"], "--expected-version", str(frozen["version"]),
        "--ttl-seconds", "2400")
    assert rc == 0, renewed
    before = snapshot()
    ordinary = inspect_task_lease(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID, todo_id=TODO_ID)
    assert "execution_proof" not in ordinary
    current = inspect_task_lease(registry_path=registry, runtime_root=runtime,
        goal_id=GOAL_ID, todo_id=TODO_ID, original_lease=frozen)
    assert current["execution_proof"] == {"idempotency_key": frozen["idempotency_key"],
                                         "expected_version": renewed["lease"]["version"]}
    assert current["execution_policy"]["execution_authority_granted"] is False
    assert snapshot() == before
    # A further lawful renewal after inspection still makes that proof stale.
    rc, latest = cli("task-lease", "renew", *task, "--owner", AGENT_ID,
        "--idempotency-key", frozen["idempotency_key"], "--expected-version", str(renewed["lease"]["version"]),
        "--ttl-seconds", "2500")
    assert rc == 0, latest
    before = snapshot()
    rc, denied = complete(current["execution_proof"]["expected_version"])
    assert rc == 1 and denied["error_code"] == "version_mismatch", denied
    assert snapshot() == before
    rc, saved = complete(latest["lease"]["version"])
    assert rc == 0 and saved["completed"], saved


@pytest.mark.parametrize("retired", ["release", "new_epoch"])
def test_original_reference_cannot_adopt_retirement_or_another_execution(admitted, retired):
    cli, task, _, frozen, _, _, _, registry, runtime, snapshot, _ = admitted
    rc, released = cli("task-lease", "release", *task, "--owner", AGENT_ID,
        "--idempotency-key", frozen["idempotency_key"], "--expected-version", str(frozen["version"]))
    assert rc == 0, released
    if retired == "new_epoch":
        rc, acquired = cli("task-lease", "acquire", *task, "--owner", AGENT_ID,
            "--idempotency-key", "next-execution", "--expected-version", str(frozen["version"]),
            "--ttl-seconds", "1800")
        assert rc == 0 and acquired["lease"]["lease_epoch"] > frozen["lease_epoch"], acquired
    before = snapshot()
    with pytest.raises(RuntimeError) as rejected:
        inspect_task_lease(registry_path=registry, runtime_root=runtime,
            goal_id=GOAL_ID, todo_id=TODO_ID, original_lease=frozen)
    assert rejected.value.code == ("original_execution_unavailable" if retired == "release" else "original_execution_changed")
    assert snapshot() == before


def test_original_reference_preserves_scope_identity_and_unknown_fields(admitted):
    _, _, _, frozen, _, _, _, registry, runtime, snapshot, _ = admitted
    before = snapshot()
    for changed in ({"owner": "other-agent"}, {"idempotency_key": "other-execution"},
        {"lease_epoch": frozen["lease_epoch"] + 1}, {"write_scopes": ["other/**"]},
        {"write_repository": "git:example.test/team/other"}, {"future_permission": True},
        {"version": frozen["version"] + 1}):
        with pytest.raises(RuntimeError) as rejected:
            inspect_task_lease(registry_path=registry, runtime_root=runtime,
                goal_id=GOAL_ID, todo_id=TODO_ID, original_lease={**frozen, **changed})
        assert rejected.value.code == "original_execution_changed"
        assert snapshot() == before
