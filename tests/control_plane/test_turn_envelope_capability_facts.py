"""One captured native decision must preserve its capability refusal facts."""
from __future__ import annotations

from copy import deepcopy
import json
from datetime import datetime, timezone
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError
from loopx.control_plane.quota.turn_envelope import (
    assert_complete_turn_decision,
    bind_turn_decision_capture,
    build_turn_envelope,
)
from loopx.control_plane.turn_driver.codex_cli import _prompt
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import (
    build_loopx_turn_host_request,
    validate_loopx_turn_host_result,
)
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos,
)
from tests.control_plane.host_process_fixture import assert_host_task_context
from tests.control_plane.reward_memory_host_fixture import enable_live_memory
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _bind_selected_replan_guard, _run_cli, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["off", "recall_only", "ingest_only", "stale"])
@pytest.mark.parametrize("network_available", [False, True])
def test_real_captured_guard_preserves_required_and_missing_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    mode: str, network_available: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("action_kind=validate ",
        "action_kind=validate continuation_policy=same_agent_non_delivery "))
    # Initialize only this disposable fixture; ordinary reads must refuse
    # unregistered projections instead of restoring canonical work from them.
    code, refused = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert code == 1 and refused["error_code"] == "canonical_authority_required"
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode="soft_claim", todos=read_fixture_todos(state),
    )
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    if mode != "off":
        config = enable_live_memory(registry, recall=mode == "recall_only", ingest=mode == "ingest_only")
        if mode == "stale":
            config.write_text(config.read_text() + "\n")
    capabilities = ["--available-capability", "filesystem_read"]
    if network_available:
        capabilities.extend(["--available-capability", "network"])
    capture = tmp_path / "capture"
    code, envelope = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "turn-capability-facts", "--codex-app", *capabilities,
        "--turn-envelope", "--decision-output-dir", str(capture), cwd=project)
    assert code == (0 if network_available else 1), envelope
    full = json.loads((capture / "decision.json").read_text())
    assert envelope == full and full["schema_version"] == "loopx_turn_envelope_v1"
    gate = full["capability_gate"]
    assert "network" in gate["required"]
    assert ("network" in gate["missing"]) is (not network_available)
    assert full["interaction_contract"]["agent_channel"]["delivery_allowed"] is network_available
    # Both readers must preserve the same admitted or refused facts without
    # re-entering quota or writing another receipt.
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    assert_complete_turn_decision(full)
    legacy = build_turn_envelope(full)
    compact = legacy["boundary"]["capability_gate"]
    for field in ("required", "missing", "action"):
        assert compact[field] == gate[field]
    assert legacy["action"]["delivery_allowed"] is network_available
    assert legacy["action_signature"]["matches"] is True

    changed = deepcopy(full)
    changed["capability_gate"]["missing"] = ["different-capability"]
    with pytest.raises(EffectRuntimeRemoteError, match="content integrity mismatch"):
        assert_complete_turn_decision(changed)
    if mode in {"off", "stale"}:
        assert "reward_memory" not in legacy["boundary"].get("capabilities", {})
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("replan", [False, True], ids=["continuing-work", "missing-outcome"])
def test_captured_vision_contract_preserves_closeout_and_original_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, replan: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("action_kind=validate ",
        "action_kind=validate continuation_policy=same_agent_non_delivery "))
    # Initialize only this disposable fixture; ordinary reads must refuse
    # unregistered projections instead of restoring canonical work from them.
    code, refused = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert code == 1 and refused["error_code"] == "canonical_authority_required"
    initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode="soft_claim", todos=read_fixture_todos(state),
    ), state_path=state, provider=provider)

    # A synthetic retained checkpoint drives the real frontier and CLI owners.
    # Missing outcome evidence must remain a replan; a continuing vision is not
    # closed merely because the selected Task is runnable.
    from tests.control_plane.test_final_outcome_diagnostics import material_run

    run = material_run()
    stamp = datetime.now(timezone.utc).isoformat()
    run.update(goal_id=GOAL_ID, agent_id=AGENT_ID, generated_at=stamp)
    run["agent_vision"].update(agent_id=AGENT_ID, generated_at=stamp)
    run["vision_checkpoint"].update(agent_id=AGENT_ID, generated_at=stamp)
    if not replan:
        run["agent_vision"]["vision_patch"].update(
            acceptance_summary="Verify the remaining real outcome.",
            replan_trigger_summary="The final acceptance remains open.",
        )
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps(run) + "\n")
    if replan:
        code, initial = _run_cli(registry, runtime, "quota", "should-run",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--turn-instance-id", "vision-contract-turn", "--codex-app", cwd=project)
        assert code == 0 and initial["decision"] == "autonomous_replan_required", initial
        _bind_selected_replan_guard(registry, runtime, project, "vision-contract-turn", todo_id=TODO_ID)
    capture = tmp_path / "vision-capture"
    code, envelope = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "vision-contract-turn", "--codex-app",
        "--available-capability", "filesystem_read", "--turn-envelope",
        "--decision-output-dir", str(capture), cwd=project)
    assert code == 0, envelope
    saved = capture / "decision.json"
    original = saved.read_bytes()
    full = json.loads(original)
    assert envelope == full and full["schema_version"] == "loopx_turn_envelope_v1"
    audit = full["vision_continuation_audit"]
    assert audit["required"] and audit["required_before_closeout"]
    assert audit["vision_gap_judge"]["done"] is False
    assert full["goal_frontier_projection"]["replan_required"] is replan
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    legacy = build_turn_envelope(full, captured_decision_path=str(saved))
    projected = legacy["contract_capsule"]["vision_continuation_audit"]
    for field in ("required_before_closeout", "recommended_action", "vision_gap_judge",
                  "authoritative_evidence_kinds", "not_satisfied_by"):
        assert projected[field] == audit[field]
    assert legacy["action_signature"]["matches"] is True
    assert "reward_memory" not in legacy["boundary"].get("capabilities", {})

    # Exercise the current Host request and DSH's stdio boundary using the
    # same immutable observation the quota CLI saved.
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(*, prompt, workspace, **kwargs):\n"
        "    Path(workspace, 'received.txt').write_text(prompt)\n"
        "    return json.dumps({\n"
        "        'result_kind':'validated_progress',\n"
        "        'classification':'profile work validated',\n"
        "        'summary':'The bounded profile work and validation completed.',\n"
        "        'recommended_action':'Retain the validated implementation evidence.',\n"
        "        'next_action':'Use the validated result in the next accepted segment.',\n"
        "        'delivery_batch_scale':'implementation',\n"
        "        'delivery_outcome':'primary_goal_outcome',\n"
        "        'vision_unchanged_reason':'The accepted goal path is unchanged.',\n"
        "        'work_evidence':['artifact:profile-driven-change'],\n"
        "        'validation_evidence':['check:profile-targeted-validation'],\n"
        "    })\n"
    )
    command = [sys.executable, "-m", "loopx.dsh_goal_mode", "--workspace", str(project),
               "--dsh-home", str(tmp_path / "host-home"), "--dsh-runner", str(runner)]
    plan = build_loopx_turn_plan(full, host="dsh", execution_mode="isolated-headless",
                                turn_instance_id="vision-contract-turn")
    assert plan["route"]["would_invoke_host"] is True
    assert full["interaction_contract"]["agent_channel"]["delivery_allowed"] is (not replan)
    assert plan["transaction"]["settlement_plan"]["identity"] == (
        full["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]
    )
    request = build_loopx_turn_host_request(plan)
    assert request["route"] == ("replan_required" if replan else "ready_for_host")
    assert request["work_contract"] == full
    request.update(bind_turn_decision_capture(
        full, captured_decision_path=str(saved), turn_instance_id="vision-contract-turn",
        turn_key=request["turn_key"],
        complete_host_request={
            key: request[key] for key in ("schema_version", "turn_key", "source_decision_hash")
        },
    ))
    result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    host_result = json.loads(result.stdout)
    assert host_result["result_kind"] == "validated_progress"
    assert host_result["delivery_batch_scale"] == "implementation"
    assert host_result["delivery_outcome"] == "primary_goal_outcome"
    assert host_result["work_evidence"] == ["artifact:profile-driven-change"]
    assert host_result["validation_evidence"] == [
        "check:profile-targeted-validation"
    ]
    verdict = validate_loopx_turn_host_result(plan, host_result)
    assert verdict["ok"], verdict["errors"]
    assert verdict["result"]["delivery_batch_scale"] == "implementation"
    assert verdict["result"]["delivery_outcome"] == "primary_goal_outcome"
    assert verdict["result"]["work_evidence"] == host_result["work_evidence"]
    assert verdict["result"]["validation_evidence"] == host_result["validation_evidence"]
    prompt_path = project / "received.txt"
    prompt = prompt_path.read_text()
    decoder = json.JSONDecoder()
    model_input, _ = decoder.raw_decode(prompt.split("Turn request:\n", 1)[1])
    codex_input, _ = decoder.raw_decode(_prompt(request).split("Turn request:\n", 1)[1])
    assert model_input == codex_input
    expected_source = full
    if replan:
        # The Host resolves the original authoring reference before hiding
        # executor-owned commands from the model.
        expected_source = deepcopy(full)
        writer = expected_source["replan_action_packet"]["writeback_contract"]
        steps = full["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]
        [(index, writeback)] = [
            (index, step) for index, step in enumerate(steps) if step["kind"] == "durable_writeback"
        ]
        assert writer.pop("vision_authoring_ref") == (
            f"$.interaction_contract.cli_channel.settlement_plan.ordered_steps[{index}].vision_authoring"
        )
        writer["vision_authoring"] = writeback["vision_authoring"]
    task = assert_host_task_context(model_input, expected_source)
    assert task["vision_continuation_audit"] == audit
    assert task["execution_profile"] == full["execution_profile"]
    assert model_input["detail_ref"] == request["detail_ref"]
    detail = model_input["detail_ref"]
    captured = detail["captured_decision"]
    assert (captured["goal_id"], captured["agent_id"], captured["turn_instance_id"]) == (
        GOAL_ID, AGENT_ID, "vision-contract-turn",
    )
    read = subprocess.run(shlex.split(detail["full_decision"]), capture_output=True, check=True).stdout
    assert read == original
    assert_complete_turn_decision(json.loads(read))
    assert model_input["source_decision_hash"] == full["contract_binding"]["content_hash"]
    assert "Do not write LoopX state" in prompt
    for field, value in (
        ("required_before_closeout", list(reversed(projected["required_before_closeout"]))),
        ("vision_gap_judge", {**projected["vision_gap_judge"], "done": True}),
        ("not_satisfied_by", []),
    ):
        invalid = deepcopy(request)
        invalid["work_contract"]["vision_continuation_audit"][field] = value
        refused = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
        assert refused.returncode != 0 and "complete Turn contract rejected" in refused.stderr
        assert prompt_path.read_text() == prompt
    for field, value in (
        ("minimum_scale", "any_operation"),
        ("must_include", []),
        ("outcome_floor", {"configured": False, "must_advance": []}),
    ):
        invalid = deepcopy(request)
        invalid["work_contract"]["execution_profile"][field] = value
        refused = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
        assert refused.returncode != 0 and "complete Turn contract rejected" in refused.stderr
        assert prompt_path.read_text() == prompt
    assert saved.read_bytes() == original
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before
