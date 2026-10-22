"""Native bound planning reaches a process and settles without a surrogate Todo."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import shlex
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota.turn_envelope import build_turn_envelope, turn_envelope_action_signature_document
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import validate_loopx_turn_host_result
from tests.control_plane.host_process_fixture import write_python_host_fixture
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, _classification_count, _configure_autonomous_replan_fixture,
    _initialize_git_checkout, _run_cli, _write_fixture,
)


def _fixture(tmp_path, monkeypatch, provider, *, replan=True):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    if replan:
        _configure_autonomous_replan_fixture(project, runtime, registry)
    else:
        state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
        state.write_text('---\nstatus: active\nowner_mode: goal\nobjective: "Answer the fixture request."\n---\n\n'
                         '# Request fixture\n\n## Objective\n\nAnswer the fixture request.\n\n'
                         '## Next Action\n\n## Agent Todo\n')
        data = json.loads(registry.read_text())
        data["goals"][0]["status"] = "active"
        registry.write_text(json.dumps(data))
    _initialize_git_checkout(project)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    code, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert code == 0, listed
    original = listed["todos"]
    initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode="soft_claim", todos=original,
    ), state_path=state, provider=provider)
    turn = "empty-frontier-planning-turn"
    code, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", turn,
        cwd=project)
    assert code == 0 and guard["decision"] == ("autonomous_replan_required" if replan else "run"), guard
    assert guard.get("selected_todo") is None
    return project, runtime, registry, turn, guard, original


def _host(tmp_path, *, observation=True, changed=True, fail_once=False):
    # An explicit local process seam, no model or network. It consumes the
    # actual native prompt and produces an independently validated artifact.
    source = '''
import json, pathlib, re, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
root = pathlib.Path.cwd()
counter = root / "host-count.txt"
counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
(root / "received.txt").write_text(prompt)
(root / "plan.json").write_text(json.dumps({"inputs": [17, 25]}))
vision = {"schema_version": "goal_vision_replan_contract_v0", "state": "active",
    "vision_patch": {"acceptance_summary": "Use the validated plan; retain the future monitor.",
                     "advancement_policy": "as_needed"},
    "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "replan",
        "prior_assumption": "Another unchanged probe might improve the plan.",
        "observed_reality": "The verified plan makes another probe unnecessary.",
        "changed": ["Use the verified plan instead of repeating the probe."],
        "evidence_refs": ["fixture:validated-plan"]}}
candidate = {"schema_version": "loopx_turn_result_v0",
    "turn_key": re.search(r'"turn_key":"([^"]+)"', prompt).group(1),
    "completed_phases": ["host_execute", "typed_result"],
    "result_kind": "replan_required", "classification": "fixture_bound_planning",
    "summary": "A verified plan replaces repeated observations.",
    "recommended_action": "Use the verified plan and retain the future monitor.",
    "next_action": "Use the verified plan.", "delivery_batch_scale": "single_surface",
    "delivery_outcome": "outcome_progress", "path_delta_mode": "material_replan",
    "agent_vision_json": json.dumps(vision), "vision_unchanged_reason": "",
    "progress_observation_json": json.dumps({"schema_version": "typed_progress_observation_v0",
        "result_class": "advanced", "surface_id": "surface-validated-plan",
        "hypothesis_id": "hypothesis-existing", "probe_kind": "probe-existing",
        "evidence_ids": ["fixture:validated-plan"]}),
    "work_evidence": ["artifact:plan.json"], "validation_evidence": ["check:plan-inputs"]}
pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(candidate))
print(json.dumps({"type": "thread.started", "thread_id": "bound-planning-session"}), flush=True)
'''
    if not observation:
        source = source.replace('pathlib.Path(args[args.index', 'candidate.pop("progress_observation_json")\npathlib.Path(args[args.index')
    elif not changed:
        source = source.replace("surface-validated-plan", "surface-existing")
    if fail_once:
        source = source.replace('(root / "plan.json").write_text', '''
failed = root / "failed-once"
if not failed.exists():
    failed.touch()
    print(json.dumps({"type": "thread.started", "thread_id": "bound-planning-session"}), flush=True)
    print(json.dumps({"type": "error", "error": {"code": "rate_limit_exceeded"}}), flush=True)
    raise SystemExit(9)
(root / "plan.json").write_text''')
    return write_python_host_fixture(tmp_path / "fixture-codex", f"#!{sys.executable}\n" + source.lstrip())


def _run_turn(project, runtime, registry, host, *, turn=None, resume=None, valid=True, retry=False):
    validator = ("import json; from pathlib import Path; "
        "assert sum(json.loads(Path('plan.json').read_text())['inputs']) == 42")
    if not valid:
        validator = validator.replace("== 42", "== 99")
    return _run_cli(registry, runtime, "turn", "run-once", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--host", "codex-cli", "--codex-bin", str(host),
        "--execution-mode", "isolated-headless", "--project", str(project),
        "--validation-command-json", json.dumps([sys.executable, "-c", validator]),
        "--no-global-sync", "--execute",
        *(["--turn-instance-id", turn] if turn else []),
        *(["--resume-turn-key", resume] if resume else []),
        *(["--retry-failed-turn"] if retry else []), cwd=project)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_bound_todoless_planning_executes_and_settles_once(tmp_path, monkeypatch, provider):
    project, runtime, registry, turn, guard, original = _fixture(tmp_path, monkeypatch, provider)
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    host = _host(tmp_path)
    code, result = _run_turn(project, runtime, registry, host, turn=turn)
    assert code == 0 and result["status"] == "committed", json.dumps(result, indent=2)
    [journal_path] = (runtime / "goals" / GOAL_ID / "turns").glob("*.json")
    journal = json.loads(journal_path.read_text())
    assert journal["plan"]["transaction"]["settlement_plan"]["identity"] == identity
    assert journal["task_validation"]["status"] == "passed"
    assert journal["host_result"]["result_kind"] == "replan_required"
    assert journal["host_result"]["progress_observation"]["surface_id"] == "surface-validated-plan"
    assert (project / "host-count.txt").read_text() == "1"
    assert _classification_count(runtime, "quota_slot_spent") == 1
    code, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert code == 0 and listed["todos"] == original  # No hidden task or completion.
    prompt = (project / "received.txt").read_text()
    assert identity["replan_obligation_id"] in prompt
    # Read the actual source referenced in the Host prompt, preserving the
    # original observation instead of re-entering quota to reconstruct it.
    envelope = journal["plan"]["turn_envelope"]
    detail = envelope["detail_ref"]
    captured = subprocess.run(shlex.split(detail["full_decision"]), capture_output=True, check=True)
    source = json.loads(captured.stdout)
    assert source["replan_action_packet"]["obligation_id"] == identity["replan_obligation_id"]
    assert source["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"] == identity
    code, replay = _run_turn(project, runtime, registry, host, resume=result["resume_turn_key"])
    assert code == 0 and not any(replay["effects"].values()), replay
    assert (project / "host-count.txt").read_text() == "1"
    assert _classification_count(runtime, "quota_slot_spent") == 1
    code, next_guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", turn + "-next")
    assert code == 0 and next_guard.get("autonomous_replan_obligation") is None, next_guard


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_no_todo_requires_the_original_signed_scoped_binding(tmp_path, monkeypatch, provider):
    _project, runtime, _registry, turn, guard, _original = _fixture(tmp_path, monkeypatch, provider)
    envelope = build_turn_envelope(guard)
    valid = build_loopx_turn_plan(envelope, host="codex-cli", execution_mode="isolated-headless",
                                  turn_instance_id=turn)
    assert valid["route"]["would_invoke_host"] is True
    inherited = build_loopx_turn_plan(envelope, host="codex-cli", execution_mode="isolated-headless")
    assert inherited["transaction"]["turn_instance_id"] == turn
    assert inherited["transaction"]["turn_key"] == valid["transaction"]["turn_key"]
    for field, value in (("goal_id", "other-goal"), ("agent_id", "other-agent"),
                         ("turn_instance_id", "other-turn"),
                         ("replan_obligation_id", "other-obligation"),
                         ("effect_id", "other-effect"), ("todo_id", "todo_surrogate")):
        changed = deepcopy(envelope)
        changed["writeback"]["settlement_plan"]["identity"][field] = value
        # Recompute the integrity hash too: a semantic mismatch must not be
        # accepted merely because its internally consistent bytes are intact.
        digest = "sha256:" + sha256(json.dumps(turn_envelope_action_signature_document(changed),
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        changed["action_signature"].update(source_hash=digest, envelope_hash=digest)
        refused = build_loopx_turn_plan(changed, host="codex-cli",
            execution_mode="isolated-headless", turn_instance_id=turn)
        assert refused["route"]["would_invoke_host"] is False, field
    for missing in ("settlement_plan", "replan_action_packet"):
        changed = deepcopy(envelope)
        if missing == "settlement_plan":
            changed["writeback"].pop(missing)
        else:
            changed.pop(missing)
        refused = build_loopx_turn_plan(changed, host="codex-cli",
            execution_mode="isolated-headless", turn_instance_id=turn)
        assert refused["route"]["would_invoke_host"] is False
    conflict = deepcopy(envelope)
    conflict["action"]["selected_todo"] = {"todo_id": "todo_surrogate"}
    refused = build_loopx_turn_plan(conflict, host="codex-cli",
        execution_mode="isolated-headless", turn_instance_id=turn)
    assert refused["route"]["would_invoke_host"] is False
    assert _classification_count(runtime, "quota_slot_spent") == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("missing", ["semantic_observation", "unchanged_observation", "independent_validation"])
def test_missing_qualification_keeps_original_host_and_no_spend(tmp_path, monkeypatch, provider, missing):
    project, runtime, registry, turn, _guard, original = _fixture(tmp_path, monkeypatch, provider)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    before = state.read_bytes()
    host = _host(tmp_path, observation=missing != "semantic_observation",
                 changed=missing != "unchanged_observation")
    code, result = _run_turn(project, runtime, registry, host, turn=turn,
                             valid=missing != "independent_validation")
    assert code == 1, result
    assert _classification_count(runtime, "quota_slot_spent") == 0
    assert state.read_bytes() == before
    code, replay = _run_turn(project, runtime, registry, host, resume=result["resume_turn_key"],
                             valid=missing != "independent_validation")
    assert code == 1, replay
    assert (project / "host-count.txt").read_text() == "1"
    assert _classification_count(runtime, "quota_slot_spent") == 0
    assert state.read_bytes() == before
    code, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert code == 0 and listed["todos"] == original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unbound_request_does_not_gain_a_planning_binding(tmp_path, monkeypatch, provider):
    project, runtime, registry, turn, guard, original = _fixture(tmp_path, monkeypatch, provider, replan=False)
    assert guard["effective_action"] == "normal_run"
    assert guard.get("replan_action_packet") is None
    assert original == []
    host = _host(tmp_path)
    code, result = _run_turn(project, runtime, registry, host, turn=turn)
    assert code == 1, result
    assert not (project / "host-count.txt").exists()
    assert not list((runtime / "goals" / GOAL_ID / "turns").glob("*.json"))
    assert _classification_count(runtime, "quota_slot_spent") == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_explicit_retry_keeps_the_bound_planning_session(tmp_path, monkeypatch, provider):
    project, runtime, registry, turn, _guard, _original = _fixture(tmp_path, monkeypatch, provider)
    host = _host(tmp_path, fail_once=True)
    code, failed = _run_turn(project, runtime, registry, host, turn=turn)
    assert code == 1 and failed["result_kind"] == "host_failure", failed
    assert (project / "host-count.txt").read_text() == "1"
    assert _classification_count(runtime, "quota_slot_spent") == 0
    code, refused = _run_turn(project, runtime, registry, host, resume=failed["resume_turn_key"])
    assert code == 1 and (project / "host-count.txt").read_text() == "1", refused
    code, recovered = _run_turn(project, runtime, registry, host, resume=failed["resume_turn_key"], retry=True)
    assert code == 0 and recovered["status"] == "committed", json.dumps(recovered, indent=2)
    assert (project / "host-count.txt").read_text() == "2"
    assert _classification_count(runtime, "quota_slot_spent") == 1


@pytest.mark.parametrize("progress", [
    '{"result_class":"advanced","evidence_ids":["evidence:plan"]}', None, False, 0, [], {},
])
def test_stop_result_cannot_carry_a_progress_observation(progress):
    from tests.test_loopx_turn_driver import _envelope

    plan = build_loopx_turn_plan(_envelope(), host="codex-cli", execution_mode="isolated-headless")
    candidate = {"schema_version": "loopx_turn_result_v0", "turn_key": plan["transaction"]["turn_key"],
        "result_kind": "wait", "completed_phases": ["host_execute", "typed_result"],
        "progress_observation_json": progress}
    assert validate_loopx_turn_host_result(plan, candidate)["ok"] is False
    candidate["progress_observation_json"] = ""
    assert validate_loopx_turn_host_result(plan, candidate)["ok"] is True
