"""Real CLI/provider/Host transport qualification; the model provider is synthetic."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.turn_driver.decision_capture import (
    prepare_turn_decision_capture, stage_turn_decision_capture, write_decision_capture,
)
from loopx.control_plane.turn_driver.journal_store import load_loopx_turn_plan_from_journal, turn_journal_path
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from tests.control_plane.host_process_fixture import assert_host_task_context


def _fixture_module():
    path = Path(__file__).resolve().parents[2] / "examples/loopx-turn-codex-cli-e2e-smoke.py"
    spec = importlib.util.spec_from_file_location("turn_capture_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["file", "sqlite"])
def complete_host_request(tmp_path, monkeypatch, request):
    """Real source/store with a pinned v0 carrier for decoder compatibility.

    Current producer/capture/recovery qualification lives in the cutover module;
    these tests also retain the old private carrier as an independent control.
    """
    from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = _fixture_module()
    project, runtime, workspace, registry = fixture._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / fixture.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    summary = parse_active_state_todos(state.read_text(), state_path=state, item_limit=None)
    initialize_canonical_authority(runtime, fixture.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=fixture.GOAL_ID, handoff_mode="soft_claim",
            todos=summary["agent_todos"]["items"]), state_path=state, provider=request.param)
    code, plan = fixture._run_cli(["--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "turn", "plan", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--turn-instance-id", "optional-host-source",
        "--host", "generic-cli", "--iteration-context", "fresh",
        "--execution-mode", "isolated-headless", "--include-transaction-detail"])
    assert code == 0, plan
    contract = plan["work_contract"]
    assert contract["schema_version"] == "loopx_turn_envelope_v1"
    original_state = state.read_bytes()
    legacy = {**build_loopx_turn_host_request(plan),
              "schema_version": "loopx_turn_host_request_v0", "turn_envelope": build_turn_envelope(contract)}
    legacy.pop("source_decision_hash", None)
    legacy.pop("detail_ref", None)
    yield legacy, contract
    assert state.read_bytes() == original_state
    assert fixture._quota_spend_count(runtime) == 0  # Adapter results are not Core settlement.


def _pending_goal_contract(original):
    from copy import deepcopy
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision, build_turn_envelope

    source = deepcopy({key: value for key, value in original.items()
                       if key not in {"schema_version", "contract_binding"}})
    agent = source["interaction_contract"]["agent_channel"]
    goal = next(row for row in agent["work_context"]["sources"] if row["source"] == "goal_state")
    # Keep source positions: other exact references still belong to this capture.
    # A valid source commitment does not fulfill the pending narrative read.
    goal["content"] = {key: value for key, value in goal["content"].items()
                       if key in {"state_file", "revision"}}
    agent["required_reads"] = [*agent.get("required_reads", []),
                               {key: value for key, value in goal.items() if key != "content"}]
    contract = compose_turn_decision(source)
    return contract, build_turn_envelope(contract)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_codex_workspace_write_consumes_original_decision_inline(tmp_path, monkeypatch, provider):
    from tests.control_plane.host_process_fixture import write_python_host_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = _fixture_module()
    project, runtime, workspace, registry = fixture._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / fixture.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    summary = parse_active_state_todos(state.read_text(encoding="utf-8"), state_path=state, item_limit=None)
    initialize_canonical_authority(runtime, fixture.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=fixture.GOAL_ID, handoff_mode="soft_claim",
            todos=summary["agent_todos"]["items"]), state_path=state, provider=provider)
    executable = fixture._write_fake_codex(tmp_path)
    script = executable.with_suffix(".py") if os.name == "nt" else executable
    source = script.read_text(encoding="utf-8").replace("prompt = sys.stdin.read()", '''prompt = sys.stdin.read()
assert args[args.index("--sandbox") + 1] == "workspace-write"
request = json.loads(prompt.split("Turn request:\\n", 1)[1])
contract = request["task_context"]
assert request["schema_version"] == "loopx_turn_model_context_v0"
assert "cli_channel" not in contract["interaction_contract"]
capture = request["detail_ref"]["captured_decision"]
assert capture["turn_key"] == request["turn_key"]
assert capture["goal_id"] == contract["goal_id"]
assert capture["agent_id"] == contract["agent_identity"]["agent_id"]
assert capture["source_hash_ref"] == "$.source_decision_hash"
assert "contract_binding" not in contract
assert request["source_decision_hash"].startswith("sha256:")
# Model the tool filesystem boundary independently of the stdin transport.
original_open = pathlib.Path.open
def sandbox_open(path, *args, **kwargs):
    if path == pathlib.Path(capture["path"]):
        raise PermissionError("sandbox cannot read the private original capture")
    return original_open(path, *args, **kwargs)
pathlib.Path.open = sandbox_open
try:
    pathlib.Path(capture["path"]).read_text()
except PermissionError:
    pass
else:
    raise AssertionError("capture read unexpectedly allowed")
assert "Read the supplied task_context before dependent work" in prompt
assert "not a prerequisite filesystem read" in prompt
assert contract["interaction_contract"]["agent_channel"]["work_context"]
pathlib.Path("received.json").write_text(json.dumps(request), encoding="utf-8")
''')
    executable = write_python_host_fixture(script.with_suffix(""), source)
    argv = fixture._base_argv(registry=registry, runtime=runtime, workspace=workspace,
        codex_bin=executable, model=None, timeout_seconds=20,
        expected_marker=fixture._marker_value(1), turn_instance_id="inline-original")
    code, result = fixture._run_cli([*argv, "--execute"])
    assert code == 0 and result["status"] == "committed", result
    assert result["validation"]["status"] == "passed"
    assert (workspace / fixture.MARKER_NAME).read_text() == fixture._marker_value(1)
    received = json.loads((workspace / "received.json").read_text(encoding="utf-8"))
    capture = Path(received["detail_ref"]["captured_decision"]["path"])
    assert_host_task_context(received, json.loads(capture.read_text(encoding="utf-8")))
    assert fixture._quota_spend_count(runtime) == 1


@pytest.mark.parametrize("launch", ["runner", "argv"])
@pytest.mark.parametrize("mode,capture", [
    ("current", False), ("pending_goal", False), ("historical", False),
    ("current", True), ("pending_goal", True),
])
def test_generic_dispatch_checks_complete_goal_before_host(
    complete_host_request, tmp_path, launch, mode, capture,
):
    from loopx.cli_commands.turn_cadence import managed_cadence_start
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once

    request, original = complete_host_request
    if mode == "pending_goal":
        contract, envelope = _pending_goal_contract(original)
    else:
        contract, envelope = original, request["turn_envelope"]
    plan = build_loopx_turn_plan(envelope, host="generic-cli", execution_mode="isolated-headless",
        turn_instance_id="generic-goal-interlock",
        **({} if mode == "historical" else {"work_contract": contract}))
    assert plan["route"]["would_invoke_host"] is True
    runtime = tmp_path / "generic-runtime"
    scope = {"runtime_root": str(runtime), "goal_id": original["goal_id"],
             "agent_id": original["agent_identity"]["agent_id"], "automation_id": None}
    configured = effect_runtime_result("quota.automation_cadence.manage", {
        **scope, "operation": "configure", "min_interval_minutes": 3,
        "expected_revision": 0, "owner_reference": "synthetic-owner-fixture", "execute": True,
    })
    assert configured["eligible_now"] is True
    cadence = managed_cadence_start(runtime_root=runtime, goal_id=scope["goal_id"],
        agent_id=scope["agent_id"], automation_id=None, manual_reason=None)
    calls = []
    marker = tmp_path / "host-started"

    def runner(_request):
        calls.append(_request)
        raise RuntimeError("synthetic stop after dispatch observation")

    def unexpected_settlement(*_args, **_kwargs):
        raise AssertionError("a failed Host observation must not settle work")

    host = {"host_runner": runner} if launch == "runner" else {"host_argv": [
        sys.executable, "-c",
        "from pathlib import Path; import sys; sys.stdin.read(); "
        f"Path({str(marker)!r}).write_text('started'); raise SystemExit(9)",
    ]}
    if launch == "argv" and mode != "historical":
        host["host_adapter_request_schema"] = "loopx_turn_host_request_v1"

    def execute():
        return run_loopx_turn_once(plan, **host, project=tmp_path / "project",
            runtime_root=runtime, goal_id=original["goal_id"],
            timeout_seconds=5, execute=True, writeback=unexpected_settlement,
            spend=unexpected_settlement, scheduler=unexpected_settlement,
            admit_start=cadence.admit, confirm_start=cadence.confirm,
            decision_capture_payload=contract if capture else None, require_decision_capture=capture)

    if mode == "pending_goal":
        with pytest.raises(RuntimeError, match="Goal"):
            execute()
        assert calls == [] and not marker.exists()
    else:
        result = execute()
        assert result["effects"]["host_invoked"] is True
        assert len(calls) == (1 if launch == "runner" else 0)
        assert marker.exists() is (launch == "argv")
    state = effect_runtime_result("quota.automation_cadence.manage", {**scope, "operation": "read"})
    if mode == "pending_goal":
        assert state["eligibility"]["state"] == "eligible"
        journal = turn_journal_path(runtime, goal_id=scope["goal_id"],
            turn_key=plan["transaction"]["turn_key"])
        assert not journal.exists()
        assert not (journal.parent / "decisions" / journal.name).exists()
    else:
        assert state["eligibility"]["state"] == "waiting"


def test_typed_result_recovery_does_not_start_a_pending_goal_host(
    complete_host_request, tmp_path,
):
    from loopx.control_plane.turn_driver.executor import _host_result_stage, TRANSACTION_PHASES
    from loopx.control_plane.turn_driver.host_candidate import build_result

    request, original = complete_host_request
    contract, envelope = _pending_goal_contract(original)
    request = {**request, "work_contract": contract, "turn_envelope": envelope}
    vision = {"schema_version": "goal_vision_replan_contract_v0", "state": "active",
        "vision_patch": {"acceptance_summary": "Validate the preserved work."},
        "path_delta": {"outcome": "replan", "prior_assumption": "Existing work remains useful.",
            "observed_reality": "The artifact is independently checked.",
            "changed": ["Use the verified artifact for the next work step."],
            "evidence_refs": ["fixture:checked-work"]}}
    candidate = build_result(request, {"result_kind": "replan_required",
        "classification": "validated_fixture_replan", "summary": "Original work remains useful.",
        "next_action": "Continue the original work.", "path_delta_mode": "material_replan",
        "agent_vision_json": json.dumps(vision), "work_evidence": ["fixture:artifact"],
        "validation_evidence": ["fixture:checked-work"]})
    plan = {**request, "transaction": {"turn_key": request["turn_key"]}}
    journal = {"completed_phases": list(TRANSACTION_PHASES[:2]), "host_result": candidate}
    effects = {"host_invoked": False, "state_written": False,
               "quota_spent": False, "scheduler_acknowledged": False}
    saved = []

    def no_new_host(_request):
        raise AssertionError("an existing typed result must not start another Host")

    result, phases, terminal = _host_result_stage(plan, request,
        host_runner=no_new_host, argv=None, completion_lifecycle_configured=False,
        project=tmp_path, timeout_seconds=5, journal=journal,
        persist_journal=lambda value: saved.append(dict(value)), effects=effects)
    assert terminal is None and result["result_kind"] == "replan_required"
    assert "typed_result" in phases and not effects["host_invoked"]
    assert saved and "host_attempt_count" not in journal


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("first_attempt_fails", [False, True])
@pytest.mark.parametrize("legacy_pretty", [False, True])
def test_real_host_reads_original_capture_after_source_change(
    tmp_path, monkeypatch, provider, first_attempt_fails, legacy_pretty,
):
    from loopx.cli_commands import turn_decision

    launch_plans = []
    original_planner = turn_decision.build_loopx_turn_plan

    def observe_launch_plan(envelope, **kwargs):
        # Observe the real live reader and planner; neither is replaced by a
        # fixture result. Every launch, including retry, uses the same contract.
        assert envelope["schema_version"] == "loopx_turn_envelope_v1"
        assert "work_contract" not in kwargs
        # The actual native caller must consume the typed owner's selected
        # work, not independently decode the historical adapter again.
        from loopx.control_plane.turn_driver import driver

        def legacy_selection(*_args, **_kwargs):
            raise AssertionError("native planning reinterpreted historical selection")

        with monkeypatch.context() as planning:
            planning.setattr(driver, "selected_turn_todo", legacy_selection)
            planned = original_planner(envelope, **kwargs)
        launch_plans.append(planned)
        return planned

    monkeypatch.setattr(turn_decision, "build_loopx_turn_plan", observe_launch_plan)
    def legacy_launch_selection(*_args, **_kwargs):
        raise AssertionError("native launch reinterpreted historical selection")

    monkeypatch.setattr(turn_decision, "selected_turn_todo", legacy_launch_selection, raising=False)
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    f = _fixture_module()
    project, runtime, workspace, registry = f._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / f.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_bytes = state.read_bytes()
    code, listed = f._run_cli(["--registry", str(registry), "--runtime-root", str(runtime),
                             "--format", "json", "todo", "list", "--goal-id", f.GOAL_ID])
    assert code == 1 and listed["error_code"] == "canonical_authority_required", listed
    assert listed["legacy_fallback_used"] is False and state.read_bytes() == state_bytes
    # Convert the disposable fixture explicitly; ordinary reads must not
    # recover unmigrated work from its Markdown projection.
    summary = parse_active_state_todos(state_bytes.decode(), state_path=state, item_limit=None)
    initialize_canonical_authority(runtime, f.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=f.GOAL_ID, handoff_mode="soft_claim",
                                             todos=summary["agent_todos"]["items"]),
        state_path=state, provider=provider)
    executable = f._write_fake_codex(tmp_path)
    source = executable.read_text().replace("#!/usr/bin/env python3", "#!" + sys.executable)
    source = source.replace("prompt = sys.stdin.read()", 'prompt = sys.stdin.read()\npathlib.Path("received.txt").write_text(prompt)')
    if first_attempt_fails:
        source = source.replace("marker = pathlib.Path(",
            'failed = pathlib.Path("failed-once")\n'
            'if not failed.exists():\n'
            '    failed.touch()\n'
            '    print(json.dumps({"type":"thread.started","thread_id":"session-fixture-0001"}), flush=True)\n'
            '    print("Selected model is at capacity. Please try a different model.", file=sys.stderr)\n'
            '    raise SystemExit(9)\n'
            'marker = pathlib.Path(')
    executable.write_text(source)
    base = f._base_argv(registry=registry, runtime=runtime, workspace=workspace,
        codex_bin=executable, model=None, timeout_seconds=20,
        expected_marker=f._marker_value(1), turn_instance_id="original-observation")
    # Preview must not allocate a capture or invoke the provider.
    code, preview = f._run_cli(base)
    assert code == 0, preview
    assert not list(runtime.rglob("decisions/*.json"))
    assert not (workspace / "received.txt").exists()
    code, result = f._run_cli([*base, "--execute"])
    assert len(launch_plans) == 1
    assert launch_plans[0]["transaction"]["settlement_plan"]["identity"]["turn_instance_id"] == "original-observation"
    if first_attempt_fails:
        assert code == 1 and result["status"] == "failed", result
        assert f._quota_spend_count(runtime) == 0
    else:
        assert code == 0 and result["status"] == "committed", result
    request = json.loads((workspace / "received.txt").read_text().split("Turn request:\n", 1)[1])
    assert "turn_envelope" not in request
    detail = request["detail_ref"]
    capture = detail["captured_decision"]
    assert capture["turn_instance_id"] == "original-observation"
    assert capture["turn_key"] == request["turn_key"]
    path = Path(capture["path"])
    original = path.read_bytes()
    assert path.stat().st_mode & 0o077 == 0
    if legacy_pretty:
        # Model an observation produced by the older writer. Recovery must
        # preserve those original bytes rather than rewriting its layout.
        original = (json.dumps(json.loads(original), ensure_ascii=False, indent=2) + "\n").encode()
        path.write_bytes(original)
    else:
        assert original.endswith(b"\n") and b"\n" not in original[:-1]
    source_a = json.loads(original)
    assert_host_task_context(request, source_a)
    assert source_a["goal_id"] == f.GOAL_ID
    assert source_a["agent_identity"]["agent_id"] == f.AGENT_ID
    # Direct `turn` bypasses quota CLI emission. Check its actual subprocess
    # prompt against the same original contracts, including recovery below.
    for field in ("work_lane_contract", "execution_obligation"):
        assert source_a[field]
        assert request["task_context"][field] == source_a[field]
    # Change the real provider through its native CAS CLI, not the projection.
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    code, current = f._run_cli([*prefix, "todo", "list", "--goal-id", f.GOAL_ID, "--todo-id", f.TODO_ID])
    assert code == 0
    code, update = f._run_cli([*prefix, "todo", "update", "--goal-id", f.GOAL_ID,
        "--todo-id", f.TODO_ID, "--agent-id", f.AGENT_ID, "--text", current["todo"]["text"] + " SOURCE_B",
        "--update-operation-id", "source-b", "--update-expected-provider-revision",
        current["authority_read"]["provider_revision"], "--execute"])
    assert code == 0, update
    # Execute precisely what the actual subprocess received.
    detail_bytes = subprocess.run(shlex.split(detail["full_decision"]), check=True,
                                  capture_output=True).stdout
    assert detail_bytes == original and b"SOURCE_B" not in detail_bytes
    code, current = f._run_cli([*prefix, "todo", "list", "--goal-id", f.GOAL_ID, "--todo-id", f.TODO_ID])
    assert code == 0 and "SOURCE_B" in current["todo"]["text"]
    plan = load_loopx_turn_plan_from_journal(runtime, goal_id=f.GOAL_ID, turn_key=request["turn_key"])
    journal_path = turn_journal_path(runtime, goal_id=f.GOAL_ID, turn_key=request["turn_key"])
    # An existing receipt must not let a malformed complete request bypass the
    # source gate on recovery. Reuse the real CLI journal and provider state;
    # none of these rejected calls may invoke a host or settlement callback.
    from copy import deepcopy
    from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request, run_loopx_turn_once

    def unexpected_effect(*_args, **_kwargs):
        raise AssertionError("rejected recovery invoked an effect")

    original_journal = journal_path.read_bytes()
    spent_before = f._quota_spend_count(runtime)
    for bad_contract in (None, {**plan["work_contract"], "reason": "changed after capture"}):
        malformed = deepcopy(plan)
        malformed["work_contract"] = bad_contract
        with pytest.raises(RuntimeError):
            run_loopx_turn_once(malformed, project=project, runtime_root=runtime,
                goal_id=f.GOAL_ID, timeout_seconds=10, execute=True,
                host_runner=unexpected_effect, writeback=unexpected_effect,
                spend=unexpected_effect, scheduler=unexpected_effect)
        assert journal_path.read_bytes() == original_journal
        assert f._quota_spend_count(runtime) == spent_before
    rebound = prepare_turn_decision_capture(plan, journal_path=journal_path,
                                          decision=None, existing_journal=True)
    from loopx.control_plane.turn_driver.host_candidate import complete_host_input
    rebound_input = complete_host_input(build_loopx_turn_host_request(rebound))
    assert rebound_input["detail_ref"] == detail
    assert rebound_input["work_contract"] == source_a
    replay = list(base)
    index = replay.index("--turn-instance-id")
    del replay[index:index + 2]
    if first_attempt_fails:
        retry = [*replay, "--resume-turn-key", request["turn_key"], "--retry-failed-turn", "--execute"]
        received_before = (workspace / "received.txt").read_bytes()
        if hasattr(os, "mkfifo"):
            journal_before_fifo = journal_path.read_bytes()
            path.unlink()
            os.mkfifo(path, 0o600)
            # Bound the real recovery process: opening a pipe in blocking mode
            # would wait for an absent writer under the Turn journal lock.
            rejected_pipe = subprocess.run(
                [sys.executable, "-m", "loopx.entrypoint", *retry], capture_output=True, timeout=15,
            )
            assert rejected_pipe.returncode == 1
            assert "regular file" in json.loads(rejected_pipe.stdout)["error"]
            assert journal_path.read_bytes() == journal_before_fifo
            assert (workspace / "received.txt").read_bytes() == received_before
            assert f._quota_spend_count(runtime) == 0
            path.unlink()
            path.write_bytes(original)
        for corrupt in (True, False):
            if corrupt:
                path.write_text(json.dumps({**source_a, "reason": "replaced evidence"}))
            else:
                path.unlink()
            code, rejected = f._run_cli(retry)
            assert code == 1, rejected
            assert (workspace / "received.txt").read_bytes() == received_before
            assert f._quota_spend_count(runtime) == 0
            path.write_bytes(original)
        code, recovered = f._run_cli([*replay, "--resume-turn-key", request["turn_key"],
                                      "--retry-failed-turn", "--execute"])
        assert code == 0 and recovered["status"] == "committed", json.dumps(recovered)
        retried = json.loads((workspace / "received.txt").read_text().split("Turn request:\n", 1)[1])
        assert retried["task_context"] == request["task_context"]
        assert retried["detail_ref"] == detail
        assert path.read_bytes() == original
    code, repeated = f._run_cli([*replay, "--resume-turn-key", request["turn_key"], "--execute"])
    assert code == 0, repeated
    assert not any(repeated["effects"].values())
    assert f._quota_spend_count(runtime) == 1
    assert path.read_bytes() == original
    # Admission can run between source validation and publication. It cannot
    # replace or silently recreate an original capture changed in that window.
    staged = stage_turn_decision_capture(plan, journal_path=journal_path,
                                        decision=None, existing_journal=True)
    # Numeric lookalikes compare equal to booleans in Python, but change the
    # original JSON observation and its typed source hash.
    for numeric in (1, 1.0):
        path.write_text(json.dumps({**source_a, "ok": numeric}))
        changed = path.read_bytes()
        with pytest.raises(ValueError, match="changed before publication"):
            staged.publish()
        assert path.read_bytes() == changed
    path.write_text(json.dumps(dict(reversed(source_a.items()))))
    assert complete_host_input(build_loopx_turn_host_request(staged.publish()))["detail_ref"] == detail
    path.write_bytes(original)
    path.write_text(json.dumps({**source_a, "reason": "changed during admission"}))
    changed = path.read_bytes()
    with pytest.raises(ValueError, match="changed before publication"):
        staged.publish()
    assert path.read_bytes() == changed
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        staged.publish()
    assert not path.exists()
    path.write_bytes(original)
    assert complete_host_input(build_loopx_turn_host_request(staged.publish()))["detail_ref"] == detail
    assert path.read_bytes() == original
    # Corrupt/missing evidence cannot be replaced by the caller's new guard.
    path.write_text(json.dumps({**source_a, "reason": "wrong original"}))
    with pytest.raises((ValueError, RuntimeError), match="original observation"):
        prepare_turn_decision_capture(plan, journal_path=journal_path,
                                      decision=source_a, existing_journal=True)
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        prepare_turn_decision_capture(plan, journal_path=journal_path,
                                      decision=source_a, existing_journal=True)
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope

    legacy = {**plan, "schema_version": "loopx_turn_plan_v0",
              "turn_envelope": {**build_turn_envelope(source_a), "detail_ref": {}}}
    for field in ("work_contract", "source_decision_hash", "detail_ref"):
        legacy.pop(field, None)
    with pytest.raises(ValueError, match="no original decision capture"):
        prepare_turn_decision_capture(legacy, journal_path=journal_path,
                                      decision=source_a, existing_journal=True)


@pytest.mark.parametrize("caller", ["native", "quota"])
def test_capture_compacts_layout_without_changing_values(tmp_path, caller):
    from loopx.cli_commands.quota_capture import capture_decision

    payload = {"ok": True, "context": {"text": '保留 two  spaces\nnew line\tand "quotes"',
        "values": [True, False, None, -0.0, 1.25], "nested": {"stop": "Do not publish."}}}
    path = tmp_path / "decision.json"
    if caller == "quota":
        capture_decision(tmp_path, payload)
    else:
        write_decision_capture(path, payload)
    raw = path.read_bytes()
    assert json.loads(raw) == payload
    assert raw.endswith(b"\n") and b"\n" not in raw[:-1]
    assert "保留".encode() in raw
    assert b"two  spaces" in raw and b"\\nnew line\\t" in raw
    assert len(raw) < len((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


def test_publication_digest_covers_exact_bytes_without_a_file_readback(tmp_path, monkeypatch):
    from loopx.control_plane.turn_driver.decision_capture import capture_context_projection

    payload = {"requirement": "保留每一条约束。", "counter": 2**80 + 1,
               "values": [True, 1, -0.0, 1.25, None]}
    expected = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
    with monkeypatch.context() as writing:
        def no_readback(*args, **kwargs):
            pytest.fail("Publishing this new capture must not reread it merely to hash its bytes")
        writing.setattr(Path, "read_bytes", no_readback)
        reference = capture_context_projection(payload, directory=tmp_path)
    assert Path(reference["path"]).read_bytes() == expected
    assert reference["sha256"] == hashlib.sha256(expected).hexdigest()
    assert Path(reference["path"]).stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("adaptive", [False, True])
def test_complete_session_lineage_preserves_saved_host_sessions(complete_host_request, tmp_path, adaptive):
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision
    from loopx.extensions.turn_host import extract_turn_work_identity
    from loopx.control_plane.turn_driver.codex_sessions import (
        _lineage, _store_codex_cli_session, load_codex_cli_session, codex_cli_session_binding,
    )

    request, contract = complete_host_request
    if adaptive:
        source = {key: value for key, value in contract.items()
                  if key not in {"schema_version", "contract_binding"}}
        source["task_orchestration_contract"] = {
            "schema_version": "task_orchestration_contract_v2", "mode": "adaptive",
            "primary_todo_id": "todo_adaptive_owner",
        }
        contract = compose_turn_decision(source)
        request = {**request, "work_contract": contract, "turn_envelope": build_turn_envelope(contract)}
    fixture = _fixture_module()
    expected = {"goal_id": fixture.GOAL_ID, "agent_id": fixture.AGENT_ID,
                "todo_id": "todo_adaptive_owner" if adaptive else fixture.TODO_ID}
    historical = {key: value for key, value in request.items() if key != "work_contract"}
    assert extract_turn_work_identity(request) == expected
    # Historical optional hosts read the declared id directly; changing their
    # adaptive fallback would be a separate compatibility change.
    assert extract_turn_work_identity(historical) == {**expected, "todo_id": fixture.TODO_ID}
    for scope in ("todo", "agent"):
        assert _lineage(request, session_scope=scope) == expected
        assert _lineage(historical, session_scope=scope) == expected
        for host in ("codex-cli", "dsh"):
            runtime = tmp_path / host / scope
            _store_codex_cli_session(runtime, lineage=expected, session_scope=scope,
                                    host=host, session_id="existing-native-session")
            saved = next(runtime.rglob("turn-sessions/*.json"))
            before = saved.read_bytes()
            restored = load_codex_cli_session(runtime, lineage=_lineage(request, session_scope=scope),
                                              session_scope=scope, host=host)
            assert restored["session_id"] == "existing-native-session"
            for fields in ({}, {"work_contract": contract}):
                binding = codex_cli_session_binding(runtime, request["turn_envelope"],
                    session_scope=scope, host=host, **fields)
                assert binding == {"schema_version": "loopx_turn_session_binding_v0", **expected}
            with pytest.raises(RuntimeError):
                codex_cli_session_binding(runtime, request["turn_envelope"],
                    session_scope=scope, host=host, work_contract=None)
            assert saved.read_bytes() == before


@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("context_mode", ["fresh", "reuse"])
def test_complete_dsh_binds_session_and_tools_to_shared_work(
    complete_host_request, tmp_path, monkeypatch, adaptive, context_mode,
):
    from hashlib import sha256
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision
    from loopx.dsh_goal_mode import turn_host_adapter

    request, original = complete_host_request
    source = {key: value for key, value in original.items()
              if key not in {"schema_version", "contract_binding"}}
    expected_todo = original["selected_todo"]["todo_id"]
    if adaptive:
        expected_todo = "todo_adaptive_owner"
        source["task_orchestration_contract"] = {
            "schema_version": "task_orchestration_contract_v2", "mode": "adaptive",
            "primary_todo_id": expected_todo,
        }
    contract = compose_turn_decision(source)
    request = {**request, "work_contract": contract, "turn_envelope": build_turn_envelope(contract),
               "session": {**request["session"], "context_policy": {"mode": context_mode}}}
    calls = []

    def runner(**kwargs):
        calls.append(kwargs)
        return '{"result_kind":"wait"}'

    monkeypatch.setattr(turn_host_adapter, "run_dsh_turn", runner)
    pinned = {"LOOPX_TURN_TODO_ID": "stale-ambient-todo", "DSH_PERMISSION_MODE": "read-only"}
    config = turn_host_adapter.DshHostConfig(workspace=tmp_path, env=pinned)
    result = turn_host_adapter.run_dsh_host(request, config=config)
    assert result["result_kind"] == "wait"
    [call] = calls
    assert call["env"] == {**pinned, "LOOPX_TURN_GOAL_ID": original["goal_id"],
                           "LOOPX_TURN_AGENT_ID": original["agent_identity"]["agent_id"],
                           "LOOPX_TURN_TODO_ID": expected_todo,
                           "LOOPX_TURN_WORKSPACE": str(tmp_path.resolve())}
    if context_mode == "fresh":
        prefix, identity = "dsh-iteration-v1-", [request["turn_key"]]
    else:
        prefix = "dsh-lineage-v1-"
        identity = [original["goal_id"], original["agent_identity"]["agent_id"], expected_todo]
        instance = request.get("goal_ref", {}).get("goal_instance_id")
        if instance:
            prefix, identity = "dsh-lineage-v2-", [*identity, instance]
    expected_session = prefix + sha256(json.dumps(identity, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert call["session_id"] == expected_session
    model_input, _ = json.JSONDecoder().raw_decode(call["prompt"].split("Turn request:\n", 1)[1])
    assert_host_task_context(model_input, contract)
    assert pinned["LOOPX_TURN_TODO_ID"] == "stale-ambient-todo"
    for invalid in (None, {**contract, "reason": "changed source"},
                    {**contract, "schema_version": "unsupported_fixture_version"}):
        with pytest.raises(turn_host_adapter.BuiltInHostError) as rejected:
            turn_host_adapter.run_dsh_host({**request, "work_contract": invalid}, config=config)
        assert rejected.value.failure_kind == "contract_rejected"
        assert len(calls) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("launch", ["builtin", "generic_stdio"])
def test_real_dsh_cli_preserves_complete_input_and_once_only_settlement(tmp_path, monkeypatch, provider, launch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = _fixture_module()
    project, runtime, workspace, registry = fixture._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / fixture.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    summary = parse_active_state_todos(state.read_text(), state_path=state, item_limit=None)
    initialize_canonical_authority(runtime, fixture.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=fixture.GOAL_ID, handoff_mode="soft_claim",
            todos=summary["agent_todos"]["items"]), state_path=state, provider=provider)
    runner = tmp_path / "dsh_runner.py"
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(**kwargs):\n"
        "    source, _ = json.JSONDecoder().raw_decode(kwargs['prompt'].split('Turn request:\\n', 1)[1])\n"
        "    contract = source['task_context']\n"
        "    assert source['schema_version'] == 'loopx_turn_model_context_v0'\n"
        "    assert 'schema_version' not in contract and 'contract_binding' not in contract\n"
        "    assert 'turn_envelope' not in source\n"
        f"    assert contract['goal_id'] == {fixture.GOAL_ID!r}\n"
        f"    assert contract['selected_todo']['todo_id'] == {fixture.TODO_ID!r}\n"
        "    root = Path(kwargs['workspace'])\n"
        "    calls = root / 'runner-calls.txt'\n"
        "    assert not calls.exists(), 'settlement replay dispatched the runner again'\n"
        "    calls.write_text('1')\n"
        f"    (root / {fixture.MARKER_NAME!r}).write_text({fixture._marker_value(1)!r})\n"
        "    return json.dumps({'result_kind':'validated_progress','classification':'dsh_complete_work',\n"
        f"        'work_evidence':['artifact:{fixture.MARKER_NAME}'],\n"
        "        'validation_evidence':['fixture:independent-marker-validator'],\n"
        "        'summary':'Completed step 1; step 2 remains open.', 'next_action':'Advance step 2.'})\n",
        encoding="utf-8",
    )
    validator = [sys.executable, "-c", "import json,pathlib,sys; json.load(sys.stdin); "
                 f"assert pathlib.Path({fixture.MARKER_NAME!r}).read_text() == {fixture._marker_value(1)!r}"]
    host_args = (["--host", "dsh", "--dsh-runner", str(runner)] if launch == "builtin" else [
        "--host", "generic-cli", "--host-adapter-request-schema", "loopx_turn_host_request_v1", "--host-adapter-command-json", json.dumps([
            sys.executable, "-m", "loopx.dsh_goal_mode", "--workspace", str(workspace),
            "--dsh-runner", str(runner), "--dsh-home", str(tmp_path / "stdio-dsh-home")])])
    argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "turn", "run-once", "--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID,
            *host_args, "--execution-mode", "isolated-headless", "--project", str(workspace),
            "--validation-command-json", json.dumps(validator),
            "--validation-failure-kind", "repair_required", "--no-global-sync", "--timeout-seconds", "20"]
    code, first = fixture._run_cli([*argv, "--turn-instance-id", "dsh-complete-source", "--execute"])
    assert code == 0 and first["status"] == "committed", first
    assert first["result_kind"] == "validated_progress" and fixture._quota_spend_count(runtime) == 1
    code, replay = fixture._run_cli([*argv, "--resume-turn-key", first["resume_turn_key"], "--execute"])
    assert code == 0 and replay["status"] == "committed", replay
    assert (workspace / "runner-calls.txt").read_text() == "1"
    assert fixture._quota_spend_count(runtime) == 1
    code, current = fixture._run_cli(["--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "todo", "list", "--goal-id", fixture.GOAL_ID, "--todo-id", fixture.TODO_ID])
    assert code == 0 and current["todo"]["status"] == "open"
    assert current["authority_read"]["source_authority"] == provider + "_v0"



@pytest.mark.skipif(os.name != "posix", reason="the deterministic TraeX executable uses a POSIX shebang")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_traex_cli_checks_the_adapter_before_dispatch_and_replays_once(
    tmp_path, monkeypatch, provider,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = _fixture_module()
    project, runtime, workspace, registry = fixture._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / fixture.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    summary = parse_active_state_todos(state.read_text(), state_path=state, item_limit=None)
    initialize_canonical_authority(runtime, fixture.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=fixture.GOAL_ID, handoff_mode="soft_claim",
            todos=summary["agent_todos"]["items"]), state_path=state, provider=provider)

    traex = tmp_path / "traex-fixture"
    traex_calls = tmp_path / "traex-calls.txt"
    traex.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\nfrom pathlib import Path\nfrom jsonschema import validate\n"
        f"open({str(traex_calls)!r}, 'a').write('invoked\\n')\n"
        "args = sys.argv[1:]\n"
        "prompt = args[-1]\n"
        "assert 'Never re-enter admission for detail reads.' in prompt\n"
        "source, _ = json.JSONDecoder().raw_decode(prompt.split('Turn request:\\n', 1)[1])\n"
        "assert source['schema_version'] == 'loopx_turn_model_context_v0'\n"
        "assert 'turn_envelope' not in source\n"
        "contract = source['task_context']\n"
        f"assert contract['selected_todo']['todo_id'] == {fixture.TODO_ID!r}\n"
        "ref = source['detail_ref']['captured_decision']\n"
        "assert ref['turn_key'] == source['turn_key']\n"
        "original = Path(ref['path']).read_bytes()\n"
        "assert ref['source_hash_ref'] == '$.source_decision_hash'\n"
        "assert source['source_decision_hash'] == json.loads(original)['contract_binding']['content_hash']\n"
        "assert json.loads(original)['interaction_contract']['agent_channel'] == contract['interaction_contract']['agent_channel']\n"
        f"Path({fixture.MARKER_NAME!r}).write_text({fixture._marker_value(1)!r})\n"
        "candidate = {'result_kind':'validated_progress','classification':'traex_complete_work',\n"
        "    'delivery_batch_scale':'implementation','delivery_outcome':'outcome_progress',\n"
        "    'path_delta_mode':'unchanged','vision_unchanged_reason':'Step 2 remains.',\n"
        "    'next_action':'Advance step 2.','work_evidence':['artifact:marker'],\n"
        "    'validation_evidence':['fixture:independent-marker-validator']}\n"
        "schema = Path(args[args.index('--output-schema') + 1])\n"
        "validate(candidate, json.loads(schema.read_text()))\n"
        "Path(args[args.index('--output-last-message') + 1]).write_text(json.dumps(candidate))\n",
        encoding="utf-8",
    )
    traex.chmod(0o700)

    adapter = Path(__file__).resolve().parents[2] / "scripts/traex_turn_host_adapter.py"
    adapter_calls = tmp_path / "adapter-calls.txt"
    adapter_runner = [
        sys.executable, "-c",
        "import os,sys; "
        f"open({str(adapter_calls)!r}, 'a').write('invoked\\n'); "
        "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])",
        str(adapter), "--traex-bin", str(traex), "--workspace", str(workspace),
    ]
    validation_calls = tmp_path / "validation-calls.txt"
    validator = [sys.executable, "-c", "import json,pathlib,sys; json.load(sys.stdin); "
                 f"assert pathlib.Path({fixture.MARKER_NAME!r}).read_text() == {fixture._marker_value(1)!r}; "
                 f"open({str(validation_calls)!r}, 'a').write('validated\\n')"]
    argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "turn", "run-once", "--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID,
            "--host", "generic-cli", "--execution-mode", "isolated-headless", "--project", str(workspace),
            "--host-adapter-command-json", json.dumps(adapter_runner),
            "--validation-command-json", json.dumps(validator), "--no-global-sync", "--timeout-seconds", "30"]
    fresh = [*argv, "--turn-instance-id", "traex-complete-source", "--execute"]
    for incompatible in ([], ["--host-adapter-request-schema", "loopx_turn_host_request_v0"]):
        code, refused = fixture._run_cli([*fresh, *incompatible])
        assert code == 1 and refused["status"] == "unavailable", refused
        assert "--host-adapter-request-schema loopx_turn_host_request_v1" in refused["reason"]
        assert not any(refused["effects"].values())
        assert not turn_journal_path(runtime, goal_id=fixture.GOAL_ID, turn_key=refused["resume_turn_key"]).exists()
        assert not adapter_calls.exists() and not validation_calls.exists()
        assert fixture._quota_spend_count(runtime) == 0

    compatible = ["--host-adapter-request-schema", "loopx_turn_host_request_v1"]
    code, first = fixture._run_cli([*fresh, *compatible])
    assert code == 0 and first["status"] == "committed", json.dumps(first, ensure_ascii=False)
    assert first["result_kind"] == "validated_progress"
    assert first["effects"]["state_written"] and first["effects"]["quota_spent"]
    code, replay = fixture._run_cli([*argv, "--resume-turn-key", first["resume_turn_key"], "--execute"])
    assert code == 0 and replay["status"] == "committed", replay
    assert replay["replayed"] and not any(replay["effects"].values())
    assert adapter_calls.read_text().splitlines() == ["invoked"]
    assert validation_calls.read_text().splitlines() == ["validated"]
    assert fixture._quota_spend_count(runtime) == 1

    from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request

    plan = load_loopx_turn_plan_from_journal(runtime, goal_id=fixture.GOAL_ID,
        agent_id=fixture.AGENT_ID, turn_key=first["resume_turn_key"])
    request = build_loopx_turn_host_request(plan)
    for invalid in ({**request, "turn_envelope": {}}, {**request, "work_contract": None}):
        result = subprocess.run(
            [sys.executable, str(adapter), "--traex-bin", str(traex), "--workspace", str(workspace)],
            input=json.dumps(invalid), text=True, capture_output=True, cwd=workspace, timeout=15,
        )
        assert result.returncode == 2 and not result.stdout
        assert "invalid LoopX Turn Host request" in result.stderr
        assert traex_calls.read_text().splitlines() == ["invoked"]


def test_historical_tool_identity_preserves_raw_values(complete_host_request):
    from copy import deepcopy
    from hashlib import sha256
    from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
    from loopx.extensions.turn_host import extract_turn_work_identity

    original, _ = complete_host_request
    fixture = _fixture_module()
    for field in ("goal_id", "agent_id", "todo_id"):
        for value in (None, False, 0, 23, [], " \ufeffraw identity "):
            request = deepcopy({key: value for key, value in original.items() if key != "work_contract"})
            envelope = request["turn_envelope"]
            target = envelope["action"]["selected_todo"] if field == "todo_id" else envelope
            target[field] = value
            digest = "sha256:" + sha256(json.dumps(turn_envelope_action_signature_document(envelope),
                sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            envelope["action_signature"].update(source_hash=digest, envelope_hash=digest, matches=True)
            expected = {"goal_id": fixture.GOAL_ID, "agent_id": fixture.AGENT_ID, "todo_id": fixture.TODO_ID,
                        field: value}
            assert extract_turn_work_identity(request) == expected


def test_complete_tool_identity_never_falls_back_to_historical_input(complete_host_request):
    from copy import deepcopy
    from loopx.extensions.turn_host import extract_turn_work_identity

    request, contract = complete_host_request
    before = deepcopy(request)
    for invalid in (None, {**contract, "reason": "changed source"},
                    {**contract, "schema_version": "unsupported_fixture_version"}):
        with pytest.raises(ValueError, match="complete Turn contract rejected"):
            extract_turn_work_identity({**request, "work_contract": invalid})
    assert request == before


def test_complete_host_rejects_self_consistent_wrong_effect_identity(complete_host_request):
    from copy import deepcopy
    from hashlib import sha256
    from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
    from loopx.control_plane.turn_driver.codex_sessions import _lineage
    from loopx.control_plane.turn_driver.host_candidate import complete_host_input, extract_turn_work_identity

    original, _ = complete_host_request
    for field in ("goal_id", "agent_id", "todo_id"):
        request = deepcopy(original)
        envelope = request["turn_envelope"]
        if field == "todo_id":
            envelope["action"]["selected_todo"][field] = "different-work"
        else:
            envelope[field] = "different-owner"
        # Both digests are individually valid. The original complete input must
        # still reject a compatibility adapter redirected to another identity.
        digest = "sha256:" + sha256(json.dumps(turn_envelope_action_signature_document(envelope),
            sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        envelope["action_signature"].update(source_hash=digest, envelope_hash=digest, matches=True)
        for consume in (_lineage, complete_host_input):
            with pytest.raises(RuntimeError, match="lineage differs"):
                consume(request)
        with pytest.raises(ValueError, match="complete Turn contract rejected"):
            extract_turn_work_identity(request)


@pytest.mark.parametrize("from_journal", [False, True])
def test_complete_result_vision_uses_original_identity(complete_host_request, from_journal):
    from copy import deepcopy
    from hashlib import sha256
    from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
    from loopx.control_plane.turn_driver.executor import validate_loopx_turn_host_result
    from loopx.control_plane.turn_driver.host_candidate import build_result

    request, _ = complete_host_request
    vision = {"schema_version": "goal_vision_replan_contract_v0", "state": "active",
        "vision_patch": {"acceptance_summary": "Validate the preserved work."},
        "path_delta": {"outcome": "replan", "prior_assumption": "Existing work remains useful.",
            "observed_reality": "The artifact is independently checked.",
            "changed": ["Use the verified artifact for the next work step."], "evidence_refs": ["fixture:checked-work"]}}
    candidate = build_result(request, {"result_kind": "replan_required",
        "classification": "validated_fixture_replan", "summary": "Original work remains useful.",
        "next_action": "Continue the original work.", "path_delta_mode": "material_replan",
        "agent_vision_json": json.dumps(vision), "work_evidence": ["fixture:artifact"],
        "validation_evidence": ["fixture:checked-work"]})
    plan = {**request, "transaction": {"turn_key": request["turn_key"]}}
    accepted = validate_loopx_turn_host_result(plan, candidate)
    assert accepted["ok"], accepted
    original_vision = accepted["result"]["agent_vision"]
    fixture = _fixture_module()
    assert (original_vision["goal_id"], original_vision["agent_id"]) == (fixture.GOAL_ID, fixture.AGENT_ID)
    if from_journal:
        candidate = accepted["result"]
    assert validate_loopx_turn_host_result(plan, candidate, from_journal=from_journal)["ok"]
    changed = deepcopy(plan)
    envelope = changed["turn_envelope"]
    envelope["agent_id"] = "another-agent"
    digest = "sha256:" + sha256(json.dumps(turn_envelope_action_signature_document(envelope),
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    envelope["action_signature"].update(source_hash=digest, envelope_hash=digest, matches=True)
    rejected = validate_loopx_turn_host_result(changed, candidate, from_journal=from_journal)
    assert not rejected["ok"]
    assert any("lineage differs" in error for error in rejected["errors"])


@pytest.mark.parametrize("carrier", ["canonical_detail", "deduplicated_text", "missing_source", "ambiguous_source"])
def test_complete_completion_source_uses_admitted_contract(complete_host_request, carrier):
    from copy import deepcopy
    from hashlib import sha256
    from loopx.control_plane.turn_driver.driver import captured_todo_text_sha256
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision

    _, contract = complete_host_request
    source = deepcopy({key: value for key, value in contract.items()
                       if key not in {"schema_version", "contract_binding"}})
    todo_id = source["selected_todo"]["todo_id"]
    text = "Preserve every requirement. 保留全部要求。\n" * 50 + "  final whitespace\n"
    source["selected_todo"]["text"] = text if carrier == "deduplicated_text" else "Display prefix"
    context = {"complete": True, "selected_todo_text_verified": True}
    if carrier == "deduplicated_text":
        context["selected_todo_ref"] = "selected_todo"
    elif carrier != "missing_source":
        record = {"source": "selected_todo", "content": {"todo": {"todo_id": todo_id, "text": text}}}
        context["sources"] = [record, record] if carrier == "ambiguous_source" else [record]
    source["interaction_contract"]["agent_channel"]["work_context"] = context
    # This synthetic source replaces the task record, so do not retain the old
    # lane title reference to its previous source index.
    source["agent_lane_next_action"].pop("title_ref", None)
    source["agent_lane_next_action"]["title"] = "Completion source qualification"
    contract = compose_turn_decision(source)
    envelope = build_turn_envelope(contract)
    if carrier in {"missing_source", "ambiguous_source"}:
        with pytest.raises(ValueError, match="full task source|ambiguous"):
            captured_todo_text_sha256(envelope, todo_id=todo_id, work_contract=contract)
        return
    assert captured_todo_text_sha256(envelope, todo_id=todo_id, work_contract=contract) == sha256(text.encode()).hexdigest()
    assert captured_todo_text_sha256(envelope, todo_id=todo_id) == sha256(text.encode()).hexdigest()
    for bad in (None, {**contract, "reason": "changed after capture"}):
        with pytest.raises(ValueError):
            captured_todo_text_sha256(envelope, todo_id=todo_id, work_contract=bad)
    with pytest.raises(ValueError, match="selected Todo"):
        captured_todo_text_sha256(envelope, todo_id="foreign_todo", work_contract=contract)
    altered = deepcopy(envelope)
    altered["action"]["selected_todo"]["text"] = "changed internal effect body"
    with pytest.raises(ValueError, match="captured executor source"):
        captured_todo_text_sha256(altered, todo_id=todo_id, work_contract=contract)


def test_complete_result_evidence_applicability_uses_admitted_profile(complete_host_request):
    from copy import deepcopy
    from hashlib import sha256
    from loopx.control_plane.quota.turn_envelope import (
        build_turn_envelope, compose_turn_decision, turn_envelope_action_signature_document,
    )
    from loopx.control_plane.turn_driver.codex_cli import (
        codex_cli_result_schema, normalize_codex_result, prepare_codex_cli_input,
    )
    from loopx.control_plane.turn_driver.host_failure import BuiltInHostError
    from loopx.control_plane.turn_driver.host_candidate import build_result

    request, original = complete_host_request
    candidate = {"result_kind": "validated_progress", "next_action": "Continue checked work."}
    # The complete source and historical projection agree for every applicability
    # case. Profile content remains owned by admission, not this result adapter.
    for profile in ("omitted", None, {}, "not a profile", {"work_requirements": ["Check the artifact."]}):
        source = deepcopy({key: value for key, value in original.items()
                           if key not in {"schema_version", "contract_binding"}})
        if profile == "omitted":
            source.pop("execution_profile", None)
        else:
            source["execution_profile"] = profile
        contract = compose_turn_decision(source)
        envelope = build_turn_envelope(contract)
        complete = {**request, "work_contract": contract, "turn_envelope": envelope}
        historical = {key: value for key, value in complete.items() if key != "work_contract"}
        expected = isinstance(profile, dict) and bool(profile)
        for consumer_request in (complete, historical):
            schema = codex_cli_result_schema(consumer_request)
            assert set(schema["required"]) == set(schema["properties"])
            assert not {"allOf", "if", "then"}.intersection(schema)
            assert prepare_codex_cli_input(consumer_request)[1] == schema
            null_result = {"result_kind": "validated_progress",
                           "work_evidence": None, "validation_evidence": None}
            if expected:
                with pytest.raises(BuiltInHostError, match="codex_material_result_missing_evidence"):
                    normalize_codex_result(consumer_request, null_result)
            else:
                assert normalize_codex_result(consumer_request, null_result) == null_result
            result = build_result(consumer_request, candidate)
            assert (result["classification"] == "missing_host_execution_evidence") is expected
            for field in ("work_evidence", "validation_evidence"):
                partial = build_result(consumer_request, {**candidate, field: ["fixture:checked"]})
                assert (partial["classification"] == "missing_host_execution_evidence") is expected
            completion = build_result(consumer_request, {"result_kind": "validated_completion"}, allow_completion=True)
            assert (completion["classification"] == "missing_host_execution_evidence") is expected
            assert build_result(consumer_request, {"result_kind": "wait"})["result_kind"] == "wait"
            # Supplied evidence is still validated by the executor; conversion
            # must not manufacture evidence or silently sanitize invalid values.
            supplied = {**candidate, "work_evidence": None, "validation_evidence": []}
            result = build_result(consumer_request, supplied)
            assert result["result_kind"] == "validated_progress"
            assert result["work_evidence"] is None and result["validation_evidence"] == []

        # A separately signed effect representation cannot become the profile
        # authority of a complete request. Historical-only requests still read it.
        altered = deepcopy(envelope)
        altered["contract_capsule"]["execution_profile"] = {} if expected else {"work_requirements": ["Legacy"]}
        digest = "sha256:" + sha256(json.dumps(turn_envelope_action_signature_document(altered),
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        altered["action_signature"].update(source_hash=digest, envelope_hash=digest, matches=True)
        changed = {**complete, "turn_envelope": altered}
        with pytest.raises(RuntimeError, match="projection differs"):
            codex_cli_result_schema(changed)
        with pytest.raises(RuntimeError, match="projection differs"):
            build_result(changed, candidate)
        with pytest.raises(RuntimeError, match="projection differs"):
            normalize_codex_result(changed, null_result)
        historical_changed = {key: value for key, value in changed.items() if key != "work_contract"}
        assert not codex_cli_result_schema(historical_changed).get("allOf")
        if not expected:
            with pytest.raises(BuiltInHostError, match="codex_material_result_missing_evidence"):
                normalize_codex_result(historical_changed, null_result)
        else:
            assert normalize_codex_result(historical_changed, null_result) == null_result
        assert (build_result(historical_changed, candidate)["classification"] == "missing_host_execution_evidence") is not expected

    for bad_contract in (None, {**original, "reason": "changed after admission"}):
        malformed = {**request, "work_contract": bad_contract}
        with pytest.raises(RuntimeError):
            codex_cli_result_schema(malformed)
        with pytest.raises(RuntimeError):
            build_result(malformed, candidate)
    malformed = deepcopy(request)
    malformed["turn_envelope"]["contract_capsule"]["execution_profile"] = {"changed": True}
    with pytest.raises(RuntimeError):
        codex_cli_result_schema(malformed)
    with pytest.raises(RuntimeError):
        build_result(malformed, candidate)


def test_complete_settlement_fallback_keeps_original_identity(complete_host_request):
    from copy import deepcopy
    from loopx.control_plane.turn_driver.executor import _ensure_turn_settlement_plan

    request, contract = complete_host_request
    plan = {**request, "host": {"kind": "generic-cli", "execution_mode": "isolated-headless"}}
    transaction = {"turn_instance_id": "original-settlement", "turn_key": request["turn_key"]}
    lineage = _ensure_turn_settlement_plan(plan, transaction)
    identity = transaction["settlement_plan"]["identity"]
    assert (identity["goal_id"], identity["agent_id"], identity["todo_id"]) == (
        contract["goal_id"], contract["agent_identity"]["agent_id"], contract["selected_todo"]["todo_id"])
    assert identity["turn_instance_id"] == "original-settlement"
    before = deepcopy(transaction)
    assert _ensure_turn_settlement_plan(plan, transaction) == lineage
    assert transaction == before  # A recorded settlement is never rebuilt.
    historical = {key: value for key, value in plan.items() if key != "work_contract"}
    legacy_transaction = {"turn_instance_id": "original-settlement", "turn_key": request["turn_key"]}
    _ensure_turn_settlement_plan(historical, legacy_transaction)
    assert legacy_transaction == transaction
    for existing in (False, True):
        for bad_contract in (None, {**contract, "reason": "changed source"}):
            target = deepcopy(transaction) if existing else {"turn_instance_id": "original-settlement"}
            original = deepcopy(target)
            with pytest.raises(RuntimeError):
                _ensure_turn_settlement_plan({**plan, "work_contract": bad_contract}, target)
            assert target == original


def test_complete_terminal_intent_rechecks_original_source(complete_host_request, monkeypatch):
    from copy import deepcopy
    from loopx.control_plane.turn_driver import settlement

    request, contract = complete_host_request
    todo_id = contract["selected_todo"]["todo_id"]

    def legacy_selection(*_args, **_kwargs):
        raise AssertionError("complete terminal intent reinterpreted historical selection")

    monkeypatch.setattr(settlement, "selected_turn_todo", legacy_selection)
    before = deepcopy(request)
    for continuation in ("active_goal", "successor", "no_followup"):
        outcome = {"todo_id": todo_id, "continuation": continuation}
        if continuation == "successor":
            outcome["successor_todo_ids"] = ["todo_checked_successor"]
        for phase in (None, "writeback", "terminal_closeout"):
            journal = {phase: {"completion": outcome}} if phase else {}
            def intent(_result):
                assert phase is None, "stored completion must not repeat lifecycle intent"
                return outcome

            actual = settlement.terminal_closeout_requirement(plan=request,
                result={"result_kind": "validated_completion"}, journal=journal,
                completion_intent=intent)
            assert actual == (continuation == "no_followup", None)
            # Another attempt or journal recovery must recheck its source;
            # prior successful consumption cannot become a retained proof.
            for malformed in (None, {**contract, "reason": "replaced after execution"}):
                rejected = settlement.terminal_closeout_requirement(
                    plan={**request, "work_contract": malformed},
                    result={"result_kind": "validated_completion"}, journal=journal,
                    completion_intent=intent)
                assert rejected[0] is False and rejected[1]
    assert request == before


def test_codex_prompt_preserves_complete_source_and_historical_version_gate(complete_host_request):
    from copy import deepcopy
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.host_candidate import complete_host_input

    request, contract = complete_host_request
    prompt = _prompt(request)
    payload = json.loads(prompt.split("Turn request:\n", 1)[1])
    assert_host_task_context(payload, contract)
    # The adapter wire stays complete even though model presentation differs.
    assert complete_host_input(request)["work_contract"] == contract
    with pytest.raises(ValueError, match="unsupported"):
        complete_host_input(payload)
    historical = {key: value for key, value in request.items() if key != "work_contract"}
    assert "TurnEnvelope as the source of truth" in _prompt(historical)
    for source in (request, historical):
        broken = deepcopy(source)
        broken["turn_envelope"]["schema_version"] = "unsupported_fixture_version"
        with pytest.raises(RuntimeError):
            _prompt(broken)
    for invalid in (None, {**contract, "reason": "changed source"},
                    {**contract, "schema_version": "unsupported_fixture_version"}):
        with pytest.raises(RuntimeError):
            _prompt({**request, "work_contract": invalid})
    broken = deepcopy(request)
    broken["turn_envelope"]["agent_id"] = "another-agent"
    with pytest.raises(RuntimeError):
        _prompt(broken)
    broken["work_contract"] = None
    broken["turn_envelope"]["schema_version"] = "unsupported_fixture_version"
    with pytest.raises(RuntimeError, match="unsupported TurnEnvelope schema_version"):
        _prompt(broken)


def test_complete_structured_authority_keeps_detached_source(complete_host_request):
    from copy import deepcopy
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority

    request, contract = complete_host_request
    before = deepcopy(contract)
    authority = extract_turn_authority(request)
    authority["contract_binding"]["source_decision_hash"] = "changed local authority"
    authority["selected_todo"]["text"] = "changed local work"
    assert contract == before and request["work_contract"] == before


def test_task_presentation_retains_unknown_values_and_source_gate(complete_host_request):
    from copy import deepcopy
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.host_candidate import complete_host_input

    request, original = complete_host_request
    source = deepcopy({k: v for k, v in original.items() if k not in {"schema_version", "contract_binding"}})
    source["future_task_data"] = {"numbers": [2**60 + 1, 1.0, -0.0],
                                "text": "保留原要求。\n" * 200 + "Stop before publication.  \n"}
    contract = compose_turn_decision(source)
    request = {**request, "work_contract": contract, "turn_envelope": build_turn_envelope(contract)}
    before = json.dumps(request, sort_keys=True, ensure_ascii=False)
    model = json.loads(_prompt(request).split("Turn request:\n", 1)[1])
    assert_host_task_context(model, contract)
    assert len(json.dumps(model)) < len(json.dumps(complete_host_input(request)))
    assert json.dumps(request, sort_keys=True, ensure_ascii=False) == before
    # Projected input has no valid source commitment. It must not be accepted
    # as an adapter request, even if a caller changes the presentation tag.
    for changed in (model, {**model, "schema_version": "loopx_turn_host_request_v1"},
                    {**request, "work_contract": model["task_context"]}):
        with pytest.raises((ValueError, RuntimeError)):
            _prompt(changed)


def test_task_view_resolves_authoring_and_explore_before_hiding_effects(complete_host_request):
    from copy import deepcopy
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision
    from loopx.control_plane.turn_driver.codex_cli import _prompt

    request, original = complete_host_request
    source = deepcopy({k: v for k, v in original.items() if k not in {"schema_version", "contract_binding"}})
    authoring = {"instruction": "Preserve the complete stops and writeback order. " * 40,
                 "future": {"numbers": [2**60 + 1, 1.0, -0.0], "allowed": False}}
    guidance = "Record result evidence; do not apply the outer writeback or spend. " * 40
    source["interaction_contract"]["cli_channel"]["settlement_plan"] = {"ordered_steps": [
        {"kind": "durable_writeback", "vision_authoring": authoring,
         "optional_attachments": [{"capability_id": "explore", "guidance": guidance}]}]}
    source["interaction_contract"]["agent_channel"]["work_context"]["sources"].append({
        "kind": "explore_turn_context", "content": {"graph": {"result_writeback_guidance": guidance}}})
    source["replan_action_packet"] = {"obligation_id": "source-obligation",
                                     "writeback_contract": {"vision_authoring": deepcopy(authoring)}}
    source["autonomous_replan_obligation"] = {"replan_context": {
        "obligation_id": "source-obligation", "context_id": "source-context"}}
    source["replan_read_path"] = str(Path(request["workspace"]) / "original.json") if "workspace" in request else "original.json"
    contract = compose_turn_decision(source)
    assert "vision_authoring_ref" in contract["replan_action_packet"]["writeback_contract"]
    before = json.dumps(contract, sort_keys=True, ensure_ascii=False)
    model = json.loads(_prompt({**request, "work_contract": contract,
                               "turn_envelope": build_turn_envelope(contract)}).split("Turn request:\n", 1)[1])
    context = model["task_context"]
    writer = context["replan_action_packet"]["writeback_contract"]
    assert "vision_authoring_ref" not in writer
    assert json.dumps(writer["vision_authoring"], sort_keys=True) == json.dumps(authoring, sort_keys=True)
    graph = context["interaction_contract"]["agent_channel"]["work_context"]["sources"][-1]["content"]["graph"]
    assert "result_writeback_guidance_ref" not in graph and graph["result_writeback_guidance"] == guidance
    assert "cli_channel" not in context["interaction_contract"]
    assert json.dumps(contract, sort_keys=True, ensure_ascii=False) == before


@pytest.mark.parametrize("operation_tools", [False, True])
def test_complete_codex_launch_shares_input_check_only_within_attempt(
    complete_host_request, tmp_path, monkeypatch, operation_tools,
):
    from copy import deepcopy
    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.turn_driver import codex_cli, codex_operation_host
    from loopx.control_plane.turn_driver.codex_cli import _prompt, codex_cli_result_schema, run_codex_cli_host
    from loopx.control_plane.turn_driver.codex_operation_host import run_codex_operation_host
    from loopx.control_plane.turn_driver.executor import MATERIAL_HOST_RESULT_KINDS
    from tests.test_codex_operation_host import FAKE_SERVER

    request, _ = complete_host_request
    expected_prompt, expected_schema = _prompt(request), codex_cli_result_schema(request)
    prompt_file, schema_file = tmp_path / "prompt.txt", tmp_path / "schema.json"
    if operation_tools:
        executable = tmp_path / "fake-operation-host"
        executable.write_text(FAKE_SERVER.replace(
            'assert row["params"]["outputSchema"]["type"] == "object"',
            'import pathlib\n        '
            f'pathlib.Path({str(schema_file)!r}).write_text(json.dumps(row["params"]["outputSchema"]))',
        ))
        executable.chmod(0o700)
        monkeypatch.setenv("FAKE_OPERATION_PROMPT_FILE", str(prompt_file))
    else:
        executable = _fixture_module()._write_fake_codex(tmp_path)
        executable.write_text(executable.read_text().replace(
            "prompt = sys.stdin.read()", "prompt = sys.stdin.read()\n"
            f"pathlib.Path({str(prompt_file)!r}).write_text(prompt)\n"
            f"pathlib.Path({str(schema_file)!r}).write_text(pathlib.Path(args[args.index('--output-schema') + 1]).read_text())",
        ))
    observed = []
    original = turn_envelope.effect_runtime_projection_result
    phase = "prepare"
    normalizer = codex_cli.normalize_codex_result
    expected_source = request["work_contract"]

    def normalize_after_provider(request, result):
        nonlocal phase
        assert phase == "prepare"
        phase = "result"
        return normalizer(request, result)

    def observe(method, arguments, **kwargs):
        if arguments.get("operation") in {"host_context", "host_dispatch_context", "validate_complete"}:
            assert arguments["payload"] == expected_source
            observed.append((phase, arguments["operation"], arguments.get("include_session_lineage", False)))
        return original(method, arguments, **kwargs)

    monkeypatch.setattr(turn_envelope, "effect_runtime_projection_result", observe)
    monkeypatch.setattr(codex_cli, "normalize_codex_result", normalize_after_provider)
    monkeypatch.setattr(codex_operation_host, "normalize_codex_result", normalize_after_provider)
    options = dict(runtime_root=tmp_path / "runtime", project=tmp_path / "project",
        codex_bin=str(executable), timeout_seconds=10)
    run = run_codex_cli_host
    if operation_tools:
        run = run_codex_operation_host
        options.update(registry_path=tmp_path / "project/.loopx/registry.json",
            model="test-model", reasoning_effort="xhigh")
    for _ in range(2):
        observed.clear()
        phase = "prepare"
        result = run(request, **options)
        assert result["turn_key"] == request["turn_key"]
        # Both transports preserve the original complete input. Native operation
        # guidance may follow it, but must not fold its whitespace or JSON prose.
        assert prompt_file.read_text().startswith(expected_prompt)
        assert json.loads(schema_file.read_text()) == expected_schema
        # Operation preflight checks Goal delivery before native initialization
        # and shares that call's normalized lineage. Its post-start preparation
        # still rechecks before send; ordinary CLI preparation remains one call.
        # Material results independently check again after the provider.
        assert observed == (
            ([("prepare", "host_dispatch_context", True)] if operation_tools else [])
            + [("prepare", "host_dispatch_context", False)]
            + ([("result", "host_context", False)] if result["result_kind"] in MATERIAL_HOST_RESULT_KINDS else []))
    changed = deepcopy(request)
    changed["work_contract"]["reason"] = "source changed after a successful launch"
    before = prompt_file.read_bytes(), schema_file.read_bytes()
    phase = "prepare"
    expected_source = changed["work_contract"]
    with pytest.raises((RuntimeError, ValueError)):
        run(changed, **options)
    assert before == (prompt_file.read_bytes(), schema_file.read_bytes())


@pytest.mark.parametrize("host", ["codex", "structured"])
def test_complete_host_prompt_uses_source_without_decoding_effect_layout(complete_host_request, host, monkeypatch):
    from loopx.control_plane.turn_driver import codex_cli, host_candidate
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt

    request, contract = complete_host_request

    class OpaqueEffectEnvelope(dict):
        def get(self, key, default=None):
            # The real typed owner still receives the entire envelope for its
            # signature/source fence. Only original-capture transport metadata
            # may be read here; Host policy must use the complete source.
            if key not in {"detail_ref", "action_signature"}:
                raise AssertionError("Host decoded the historical effect layout: " + key)
            return super().get(key, default)

    original = json.dumps(request, ensure_ascii=False, sort_keys=True)
    guarded = {**request, "turn_envelope": OpaqueEffectEnvelope(request["turn_envelope"])}
    for adapter in (codex_cli, host_candidate):
        original_mapping = adapter._mapping

        def observed_mapping(value, *, convert=original_mapping):
            return value if isinstance(value, OpaqueEffectEnvelope) else convert(value)

        # Keep the observation wrapper across the adapters' shallow defensive
        # copies; otherwise the assertion would never see their policy reads.
        monkeypatch.setattr(adapter, "_mapping", observed_mapping)
    if host == "codex":
        prompt = _prompt(guarded)
    else:
        authority = extract_turn_authority(guarded)
        assert authority["primary_action"] == request["turn_envelope"]["action"]["primary_action"]
        prompt = render_prompt(authority)
    model_input, _ = json.JSONDecoder().raw_decode(prompt.split("Turn request:\n", 1)[1])
    assert_host_task_context(model_input, contract)
    assert "turn_envelope" not in model_input and "turn_authority" not in model_input
    assert json.dumps(request, ensure_ascii=False, sort_keys=True) == original


def test_complete_codex_approval_guidance_uses_resolved_boundary(complete_host_request):
    from copy import deepcopy
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision
    from loopx.control_plane.turn_driver.codex_cli import _prompt

    request, original = complete_host_request
    active = {"schema_version": "checkpointed_boundary_authority_v0", "active_count": 1,
              "active_write_scope": ["artifacts/**"]}
    for approval, expected in (
        (None, False), ({}, False), ({**active, "schema_version": "future"}, False),
        ({**active, "active_count": 0}, False), ({**active, "active_write_scope": []}, False),
        ({**active, "active_write_scope": [None, ""]}, False), (active, True),
    ):
        source = deepcopy({key: value for key, value in original.items()
                           if key not in {"schema_version", "contract_binding"}})
        source.setdefault("goal_boundary", {})["checkpointed_boundary_authority"] = approval
        contract = compose_turn_decision(source)
        envelope = build_turn_envelope(contract)
        prompt = _prompt({**request, "work_contract": contract, "turn_envelope": envelope})
        assert ("It satisfies the write approval requirement" in prompt) is expected


def test_complete_planning_and_failed_recovery_share_lineage(complete_host_request, monkeypatch):
    from copy import deepcopy
    from hashlib import sha256
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision
    from loopx.control_plane.turn_driver.driver import (
        FailedTurnSessionRecoveryError, _turn_lineage, build_loopx_turn_plan, reconcile_failed_turn_session_request,
    )

    request, original = complete_host_request
    from loopx.control_plane.turn_driver import driver

    def legacy_selection(*_args, **_kwargs):
        raise AssertionError("planning/recovery reinterpreted historical selection")

    monkeypatch.setattr(driver, "selected_turn_todo", legacy_selection)
    source = {k: v for k, v in original.items() if k not in {"schema_version", "contract_binding"}}
    # Hash parity protects existing journals, including numeric encodings and
    # Unicode in retained source facts; a new hash silently forks the Turn.
    source["execution_profile"] = {"probe": [1e-7, 1e-6, 1e20, -0.0, "原文", "\U00010000", "\ue000"]}
    for adaptive in (False, True):
        if adaptive:
            source["task_orchestration_contract"] = {
                "schema_version": "task_orchestration_contract_v2", "mode": "adaptive",
                "primary_todo_id": "todo_adaptive_owner",
            }
        contract = compose_turn_decision(source)
        envelope = build_turn_envelope(contract)
        kwargs = dict(host="codex-cli", execution_mode="isolated-headless", turn_instance_id="planning-parity")
        historical = build_loopx_turn_plan(envelope, **kwargs)
        complete = build_loopx_turn_plan(envelope, work_contract=contract, **kwargs)
        expected = {**historical, "schema_version": "loopx_turn_plan_v1", "work_contract": contract,
                    "source_decision_hash": envelope["action_signature"]["source_decision_hash"],
                    "boundary": {k: v for k, v in historical["boundary"].items() if k != "preserves_turn_envelope"}}
        del expected["turn_envelope"]
        assert complete == expected
        expected_todo = "todo_adaptive_owner" if adaptive else original["selected_todo"]["todo_id"]
        from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
        signature = turn_envelope_action_signature_document(envelope)
        signature["action"]["selected_todo"] = complete["route"]["selected_todo"]
        expected_hash = "sha256:" + sha256(json.dumps(signature, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        lineage = _turn_lineage(envelope, work_contract=contract)
        assert lineage["action_hash"] == expected_hash
        assert lineage["todo_id"] == expected_todo
        binding = {"schema_version": "loopx_turn_session_binding_v0", "host_session_id": "fixture-session",
                   "goal_id": original["goal_id"], "agent_id": original["agent_identity"]["agent_id"],
                   "todo_id": expected_todo}
        recovery = {**request, "turn_envelope": envelope, "work_contract": contract,
                    "session": complete["session"]}
        recovered = reconcile_failed_turn_session_request(recovery, session_binding=binding)
        assert recovered["session"]["action"] == "resume"
        assert recovered["session"]["binding_status"] == "failed_turn_recovery"
        assert recovered["work_contract"] == contract and recovered["turn_envelope"] == envelope
        assert reconcile_failed_turn_session_request(recovered, session_binding=binding) == recovered
        for bad in (None, {**contract, "reason": "changed source"},
                    {**contract, "schema_version": "unsupported_fixture_version"}):
            with pytest.raises(RuntimeError):
                build_loopx_turn_plan(envelope, work_contract=bad, **kwargs)
            for previous in (recovery, recovered):
                with pytest.raises(FailedTurnSessionRecoveryError, match="complete Turn"):
                    reconcile_failed_turn_session_request({**previous, "work_contract": bad}, session_binding=binding)
        altered = deepcopy(envelope)
        altered["agent_id"] = "wrong-agent"
        with pytest.raises(RuntimeError):
            build_loopx_turn_plan(altered, work_contract=contract, **kwargs)
        assert envelope["agent_id"] == original["agent_identity"]["agent_id"]


def test_complete_planning_keeps_receipt_bound_todoless_replan(complete_host_request):
    from loopx.control_plane.quota.settlement import build_turn_scoped_cli_settlement_plan
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan, reconcile_failed_turn_session_request

    _, original = complete_host_request
    goal, agent, obligation = original["goal_id"], original["agent_identity"]["agent_id"], "replan-" + "a" * 16
    source = {"ok": True, "goal_id": goal, "agent_identity": {"agent_id": agent}, "decision": "run",
              "interaction_contract": {"schema_version": "loopx_interaction_contract_v0", "mode": "autonomous_replan",
                  "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
                  "agent_channel": {}, "cli_channel": {"spend_after_validation": True}}}
    settlement = build_turn_scoped_cli_settlement_plan(goal_id=goal, agent_id=agent,
        replan_obligation_id=obligation, turn_instance_id="fixture.replan", command_prefix="loopx",
        scoped_cli_args="", lifecycle_actor_args="").as_dict()
    source.update(selected_todo=None, should_run=True, effective_action="autonomous_replan_required",
        replan_action_packet={"schema_version": "replan_action_packet_v0", "decision": "replan_required",
                              "obligation_id": obligation})
    source.pop("task_orchestration_contract", None)
    interaction = source["interaction_contract"]
    interaction["mode"] = "autonomous_replan"
    interaction["agent_channel"].update(must_attempt=True, delivery_allowed=False, quiet_noop_allowed=False)
    interaction["cli_channel"]["settlement_plan"] = settlement
    contract = compose_turn_decision(source)
    envelope = build_turn_envelope(contract)
    kwargs = dict(host="codex-cli", execution_mode="isolated-headless", session_scope="agent",
                  turn_instance_id="fixture.replan")
    historical = build_loopx_turn_plan(envelope, **kwargs)
    plan = build_loopx_turn_plan(envelope, work_contract=contract, **kwargs)
    expected = {**historical, "schema_version": "loopx_turn_plan_v1", "work_contract": contract,
                "source_decision_hash": envelope["action_signature"]["source_decision_hash"],
                "boundary": {k: v for k, v in historical["boundary"].items() if k != "preserves_turn_envelope"}}
    del expected["turn_envelope"]
    assert plan == expected
    assert plan["route"]["kind"] == "replan_required" and plan["route"]["selected_todo"] is None
    assert plan["transaction"]["settlement_plan"]["identity"] == settlement["identity"]
    binding = {"schema_version": "loopx_turn_session_binding_v0", "goal_id": goal, "agent_id": agent,
               "host_session_id": "fixture-session"}
    assert reconcile_failed_turn_session_request(plan, session_binding=binding)["session"]["action"] == "resume"
    wrong_turn = build_loopx_turn_plan(envelope, work_contract=contract, **{**kwargs, "turn_instance_id": "other"})
    assert wrong_turn["route"]["would_invoke_host"] is False
    assert not any(wrong_turn["effects"].values())


def test_complete_plan_rejects_rehashed_adapter_decision_drift(complete_host_request, tmp_path):
    from copy import deepcopy
    from hashlib import sha256
    from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once

    request, contract = complete_host_request
    envelope = request['turn_envelope']
    options = dict(host='generic-cli', execution_mode='isolated-headless',
                   turn_instance_id='adapter-drift')
    # Retain a real transitional v0 plan as the adapter-drift control. Current
    # writers instead reject the presence of any second carrier outright.
    plan = {**build_loopx_turn_plan(envelope, **options), "work_contract": contract}
    assert plan['route']['would_invoke_host'] is True

    def unexpected_effect(*_args, **_kwargs):
        raise AssertionError('divergent adapter invoked an effect')

    # Each payload retains its Goal/Agent/Todo and correctly recomputes its own
    # integrity hash. The independent invariant is derivation from the admitted
    # decision, not self-consistency of a second decision carrier.
    for field, replacement in (
        ('should_run', False), ('effective_action', 'wait'),
        ('action', {**envelope['action'], 'delivery_allowed': False}),
        ('user', {**envelope['user'], 'action_required': True}),
        ('execution_policy', {**envelope['execution_policy'], 'normal_delivery_allowed': False}),
        ('writeback', {**envelope['writeback'], 'spend_allowed_now': True}),
    ):
        changed = deepcopy(envelope)
        changed[field] = replacement
        assert changed != envelope
        digest = 'sha256:' + sha256(json.dumps(turn_envelope_action_signature_document(changed),
            sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        changed['action_signature'].update(source_hash=digest, envelope_hash=digest, matches=True)
        with pytest.raises(RuntimeError, match='projection differs'):
            build_loopx_turn_plan(changed, work_contract=contract, **options)
        malformed = {**plan, 'turn_envelope': changed}
        with pytest.raises(RuntimeError, match='projection differs'):
            run_loopx_turn_once(malformed, project=tmp_path, runtime_root=tmp_path / 'rejected',
                goal_id=contract['goal_id'], timeout_seconds=10, execute=True,
                host_runner=unexpected_effect, writeback=unexpected_effect,
                spend=unexpected_effect, scheduler=unexpected_effect)
    # The existing lane lock may be allocated before the checked execution
    # entry. No Turn journal or settlement receipt may be produced.
    assert not list((tmp_path / 'rejected').rglob('*.json'))
    assert not list((tmp_path / 'rejected').rglob('*.jsonl'))
