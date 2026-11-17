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
from loopx.control_plane.quota.turn_envelope import (
    assert_complete_turn_decision, build_turn_envelope, turn_envelope_action_signature_document,
)
from loopx.control_plane.turn_driver.decision_capture import prepare_turn_decision_capture
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import (
    build_loopx_turn_host_request, validate_loopx_turn_host_result,
)
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos,
)
from tests.control_plane.reward_memory_host_fixture import enable_live_memory
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _bind_selected_replan_guard, _run_cli, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["off", "recall_only", "ingest_only", "stale"])
@pytest.mark.parametrize("network_available", [False, True])
@pytest.mark.parametrize("format_flag", ["--complete-turn", "--turn-envelope"], ids=["complete", "legacy-flag"])
def test_real_captured_guard_preserves_required_and_missing_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    mode: str, network_available: bool, format_flag: str,
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
    code, delivered = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "turn-capability-facts", "--codex-app", *capabilities,
        format_flag,
        "--context-delivery", "off", "--delta-delivery", "off",
        "--decision-output-dir", str(capture), cwd=project)
    assert code == (0 if network_available else 1), delivered
    full = json.loads((capture / "decision.json").read_text())
    assert_complete_turn_decision(full)
    assert delivered == full and "context_delivery" not in delivered
    envelope = build_turn_envelope(full, captured_decision_path=str(capture / "decision.json"))
    assert envelope["schema_version"] == "loopx_turn_envelope_v0"
    assert envelope["action_signature"]["source_decision_hash"] == full["contract_binding"]["content_hash"]
    gate = full["capability_gate"]
    assert "network" in gate["required"]
    assert ("network" in gate["missing"]) is (not network_available)
    assert full["interaction_contract"]["agent_channel"]["delivery_allowed"] is network_available
    compact = envelope["boundary"]["capability_gate"]
    assert compact["required"] == gate["required"]
    assert compact["missing"] == gate["missing"]
    assert compact["action"] == gate["action"]
    assert envelope["action"]["delivery_allowed"] is network_available
    assert envelope["action_signature"]["matches"] is True
    # The source remains one full observation, not another admission evaluation.
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    assert build_turn_envelope(full)["boundary"]["capability_gate"] == compact
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before
    changed = json.loads(json.dumps(envelope))
    changed["boundary"]["capability_gate"]["missing"] = ["different-capability"]
    assert turn_envelope_action_signature_document(changed) != turn_envelope_action_signature_document(envelope)
    if mode in {"off", "stale"}:
        assert "reward_memory" not in envelope["boundary"].get("capabilities", {})


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("replan", [False, True], ids=["continuing-work", "missing-outcome"])
@pytest.mark.parametrize("format_flag", ["--complete-turn", "--turn-envelope"], ids=["complete", "legacy-flag"])
def test_captured_vision_contract_preserves_closeout_and_original_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, replan: bool, format_flag: str,
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
    code, delivered = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "vision-contract-turn", "--codex-app",
        "--available-capability", "filesystem_read",
        format_flag,
        "--context-delivery", "off", "--delta-delivery", "off",
        "--decision-output-dir", str(capture), cwd=project)
    assert code == 0, delivered
    decision_path = capture / "decision.json"
    original = decision_path.read_bytes()
    full = json.loads(original)
    assert_complete_turn_decision(full)
    assert delivered == full and "context_delivery" not in delivered
    envelope = build_turn_envelope(full, captured_decision_path=str(decision_path))
    assert envelope["schema_version"] == "loopx_turn_envelope_v0"
    assert envelope["action_signature"]["source_decision_hash"] == full["contract_binding"]["content_hash"]
    audit = full["vision_continuation_audit"]
    assert audit["required"] and audit["required_before_closeout"]
    assert audit["vision_gap_judge"]["done"] is False
    assert full["goal_frontier_projection"]["replan_required"] is replan
    projected = envelope["contract_capsule"]["vision_continuation_audit"]
    for field in ("required_before_closeout", "recommended_action", "vision_gap_judge",
                  "authoritative_evidence_kinds", "not_satisfied_by"):
        assert projected[field] == audit[field]
    assert envelope["action_signature"]["matches"] is True
    assert "reward_memory" not in envelope["boundary"].get("capabilities", {})

    # Consume the saved source without reentering quota or appending a receipt.
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    replay = build_turn_envelope(full, captured_decision_path=str(capture / "decision.json"))
    assert replay["contract_capsule"]["vision_continuation_audit"] == projected
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before
    changed = json.loads(json.dumps(envelope))
    changed["contract_capsule"]["vision_continuation_audit"]["required_before_closeout"].pop()
    assert turn_envelope_action_signature_document(changed) != turn_envelope_action_signature_document(envelope)
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority
    from loopx.control_plane.turn_driver.codex_cli import _prompt

    assert extract_turn_authority({"turn_envelope": envelope})["primary_action"]
    assert json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")) in _prompt(
        {"turn_envelope": envelope, "turn_key": "vision-contract-turn"}
    )
    with pytest.raises(ValueError, match="action signature"):
        extract_turn_authority({"turn_envelope": changed})

    # Exercise the shared host's real stdio boundary, rather than only proving
    # that the envelope and Codex's full-request prompt retain the audit.
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

    current_plan = build_loopx_turn_plan(full, host="dsh", execution_mode="isolated-headless",
                                        turn_instance_id="vision-contract-turn")
    current_plan = prepare_turn_decision_capture(
        current_plan, journal_path=tmp_path / "current-turn" / "journal.json",
        decision=full, existing_journal=False,
    )
    current_request = build_loopx_turn_host_request(current_plan)
    current_result = subprocess.run(command, input=json.dumps(current_request), text=True,
                                    capture_output=True, timeout=30)
    prompt_path = project / "received.txt"
    if replan:
        assert current_result.returncode == 0, current_result.stdout + current_result.stderr
        current_verdict = validate_loopx_turn_host_result(current_plan, json.loads(current_result.stdout))
        assert current_verdict["ok"], current_verdict["errors"]
        current_prompt = prompt_path.read_text()
        actual, _ = json.JSONDecoder().raw_decode(current_prompt.split("Turn request:\n", 1)[1])
        assert actual["source_schema_version"] == "loopx_turn_host_request_v1"
        assert actual["source_decision_hash"] == full["contract_binding"]["content_hash"]
        task_context = actual["task_context"]
        assert "work_contract" not in actual and "cli_channel" not in task_context["interaction_contract"]
        assert task_context["vision_continuation_audit"] == audit
        assert task_context["execution_profile"] == full["execution_profile"]
        packet = full["replan_action_packet"]
        writeback = dict(packet["writeback_contract"])
        assert writeback.pop("vision_authoring_ref")
        steps = full["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]
        writeback["vision_authoring"] = next(step["vision_authoring"] for step in steps
                                             if step["kind"] == "durable_writeback")
        assert task_context["replan_action_packet"] == {**packet, "writeback_contract": writeback}
        read = subprocess.run(shlex.split(actual["detail_ref"]["full_decision"]),
                              capture_output=True, check=True).stdout
        assert read == original
        for field, key, value in (
            ("vision_continuation_audit", "required_before_closeout", []),
            ("execution_profile", "minimum_scale", "any_operation"),
        ):
            invalid = deepcopy(current_request)
            invalid["work_contract"][field][key] = value
            refused = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
            assert refused.returncode != 0 and "complete Turn contract rejected" in refused.stderr
            assert prompt_path.read_text() == current_prompt
    else:
        # Off still advertises the required Goal read. Admission and capture
        # cannot authorize a current Host before that source is available.
        required_reads = full["interaction_contract"]["agent_channel"]["required_reads"]
        assert any(read["source"] == "goal_state" for read in required_reads)
        assert current_result.returncode != 0 and "complete Turn contract rejected" in current_result.stderr
        assert not prompt_path.exists()

    # Historical requests use the recorded layout and signature.
    plan = build_loopx_turn_plan(envelope, host="dsh", execution_mode="isolated-headless",
                                 turn_instance_id="vision-contract-turn")
    assert plan["route"]["would_invoke_host"] is True
    # A replan route permits planning, not ordinary delivery.
    assert envelope["action"]["delivery_allowed"] is (not replan)
    request = {"schema_version": "loopx_turn_host_request_v0", "turn_key": plan["transaction"]["turn_key"],
               "route": "replan_required" if replan else "primary_delivery", "turn_envelope": envelope}
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
    prompt = prompt_path.read_text()
    authority = json.loads(prompt.split("Turn authority JSON:\n", 1)[1].split("\n\nWhen finished", 1)[0])
    assert authority["vision_continuation_audit"] == projected
    # The native work-size and outcome rules must reach the provider too;
    # retaining an audit alone cannot tell it what qualifies as useful work.
    assert authority["execution_profile"] == full["execution_profile"]
    assert authority["vision_continuation_audit"]["vision_gap_judge"]["done"] is False
    assert authority["action_signature"] == envelope["action_signature"]
    assert authority["detail_ref"] == envelope["detail_ref"]
    detail = authority["detail_ref"]
    captured = detail["captured_decision"]
    assert (captured["goal_id"], captured["agent_id"], captured["turn_instance_id"]) == (
        GOAL_ID, AGENT_ID, "vision-contract-turn",
    )
    read = subprocess.run(shlex.split(detail["full_decision"]), capture_output=True, check=True).stdout
    assert read == original
    assert build_turn_envelope(json.loads(read))["action_signature"]["source_decision_hash"] == authority["action_signature"]["source_decision_hash"]
    assert "writeback" not in authority
    assert authority["scheduler"] == envelope["scheduler"]
    assert "Do not execute its state or scheduler commands" in prompt
    for field, value in (
        ("required_before_closeout", list(reversed(projected["required_before_closeout"]))),
        ("vision_gap_judge", {**projected["vision_gap_judge"], "done": True}),
        ("not_satisfied_by", []),
    ):
        invalid = deepcopy(request)
        invalid["turn_envelope"]["contract_capsule"]["vision_continuation_audit"][field] = value
        refused = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
        assert refused.returncode != 0 and "action signature" in refused.stderr
        assert prompt_path.read_text() == prompt
    for field, value in (
        ("minimum_scale", "any_operation"),
        ("must_include", []),
        ("outcome_floor", {"configured": False, "must_advance": []}),
    ):
        invalid = deepcopy(request)
        invalid["turn_envelope"]["contract_capsule"]["execution_profile"][field] = value
        refused = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
        assert refused.returncode != 0 and "action signature" in refused.stderr
        assert prompt_path.read_text() == prompt
    assert (capture / "decision.json").read_bytes() == original
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before
