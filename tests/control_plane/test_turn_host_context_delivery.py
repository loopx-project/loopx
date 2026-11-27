"""Real File/SQLite capture to DSH stdio; the explicit runner invokes no model."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

import loopx

from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope, turn_envelope_action_signature_document,
)
from loopx.control_plane.turn_driver.decision_capture import prepare_turn_decision_capture
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.turn_driver.host_candidate import _canonical_hash
from loopx.control_plane.turn_driver import codex_context
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.host_process_fixture import assert_host_task_context
from tests.control_plane.test_agent_context import POLICY
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, SELECTED_REPLAN_TODO_ID,
    _bind_selected_replan_guard, _configure_selected_todo_replan_fixture,
    _initialize_fixture_authority, _initialize_git_checkout, _run_cli, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["off", "inline", "legacy_reference"])
def test_captured_context_reaches_actual_host_without_readmission(tmp_path, monkeypatch, provider, mode):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    # Keep subprocess IO on the Core under test, including an isolated wheel;
    # the test harness directory must never become a borrowed Core checkout.
    checkout = Path(loopx.__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None,
        [str(checkout), os.environ.get("PYTHONPATH")])))
    imported = subprocess.run([sys.executable, "-c", "import loopx; print(loopx.__file__)"],
        cwd=project, text=True, capture_output=True, check=True)
    assert Path(imported.stdout.strip()).resolve() == checkout / "loopx" / "__init__.py"
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        "- [ ] [P1] Validate and settle the selected delivery.",
        "- [ ] [P1] Validate the complete acceptance: " + "preserve original evidence; " * 180,
    ))
    config = json.loads(registry.read_text())
    config["goals"][0]["spawn_policy"] = {**POLICY, "spawn_allowed": mode != "off"}
    registry.write_text(json.dumps(config))
    # Ordinary reads reject unmigrated Goals. Bootstrap the disposable canonical
    # fixture through its explicit conversion helper before exercising delivery.
    _initialize_fixture_authority(project, runtime, provider=provider)

    capture = tmp_path / "capture"
    if mode != "legacy_reference":
        native_directory = tmp_path / "context"
        native_directory.mkdir(mode=0o700)
        codex_context.codex_context_hook(str(native_directory), {
            "session_id": "host-context-fixture", "cwd": str(project),
            "hook_event_name": "SessionStart", "source": "startup",
        })
    delivery = (["--context-delivery", "on", "--codex-context-dir", str(tmp_path / "context")]
        if mode != "legacy_reference" else ["--context-delivery", "off"])
    binary = Path(sys.executable).with_name("loopx.exe" if sys.platform == "win32" else "loopx")
    guard = [str(binary), "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "host-context-turn", "--codex-app",
        "--available-capability", "filesystem_read", "--available-capability", "subagent_spawn",
        "--decision-output-dir", str(capture), *delivery]
    produced = subprocess.run(guard, cwd=project, text=True, capture_output=True, timeout=30)
    assert produced.returncode == 0, produced.stdout + produced.stderr
    original = (capture / "decision.json").read_bytes()
    source = json.loads(original)
    assert source["schema_version"] == "loopx_turn_envelope_v1"
    context = source["interaction_contract"].get("agent_context")
    assert bool(context) is (mode != "off")
    if mode == "legacy_reference":
        assert json.loads(produced.stdout) == source
        # The historical decoder receives an explicit adapter over the original
        # source and its recorded host binding.
        envelope = build_turn_envelope(source, captured_decision_path=str(capture / "decision.json"))
        envelope["agent_context"] = {
            **{k: context[k] for k in ("schema_version", "phase", "scope", "target", "authority", "delivery")},
            "content_hash": _canonical_hash(context),
            "detail_ref": "full_decision.interaction_contract.agent_context",
            "instruction": "Read original capability context before planning.",
        }
        goal_source = next(row for row in source["interaction_contract"]["agent_channel"]["work_context"]["sources"]
                           if row["source"] == "goal_state")
        envelope["required_reads"] = [{
            **{key: goal_source[key] for key in ("source", "ordering", "command")},
            "reason": "Read the original Goal file before work.",
        }]
        digest = _canonical_hash(turn_envelope_action_signature_document(envelope))
        envelope["action_signature"].update(source_hash=digest, envelope_hash=digest)

    runner = tmp_path / "runner.py"
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(*, prompt, workspace, **kwargs):\n"
        "    Path(workspace, 'received.txt').write_text(prompt)\n"
        "    return json.dumps({'result_kind':'wait','summary':'Protocol input recorded.'})\n"
    )
    command = [sys.executable, "-m", "loopx.hosts.dsh", "--workspace", str(project),
               "--dsh-home", str(tmp_path / "host-home"), "--dsh-runner", str(runner)]
    plan = build_loopx_turn_plan(envelope if mode == "legacy_reference" else source,
        host="dsh", execution_mode="isolated-headless", turn_instance_id="host-context-turn")
    if mode != "legacy_reference":
        assert plan["work_contract"] == source
        plan = prepare_turn_decision_capture(plan, journal_path=tmp_path / "current-turn" / "journal.json",
            decision=plan["work_contract"], existing_journal=False)
    request = build_loopx_turn_host_request(plan)
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["result_kind"] == "wait"
    prompt_path = project / "received.txt"
    prompt = prompt_path.read_text()
    if mode == "legacy_reference":
        # Historical decoder remains explicitly qualified until its reader exits.
        authority, _ = json.JSONDecoder().raw_decode(prompt.split("Turn authority JSON:\n", 1)[1])
        assert authority["agent_context"] == envelope["agent_context"]
        assert authority["action_signature"] == envelope["action_signature"]
        assert authority["required_reads"] == envelope["required_reads"]
        assert authority["required_reads"]
        if os.name != "nt":
            read_goal = subprocess.run(shlex.split(authority["required_reads"][0]["command"]),
                capture_output=True, check=True)
            assert read_goal.stdout == state.read_bytes()
        assert authority["detail_ref"] == envelope["detail_ref"]
        detail = authority["detail_ref"]
        read = subprocess.run(shlex.split(detail["full_decision"]), capture_output=True, check=True).stdout
        assert read == original
        assert build_turn_envelope(json.loads(read))["action_signature"]["source_decision_hash"] == authority["action_signature"]["source_decision_hash"]
        assert _canonical_hash(json.loads(read)["interaction_contract"]["agent_context"]) == authority["agent_context"]["content_hash"]
        invalid = deepcopy(request)
        invalid["turn_envelope"]["agent_context"]["scope"]["agent_id"] = "wrong-agent"
        rejected = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
        assert rejected.returncode != 0 and "action signature" in rejected.stderr
        assert prompt_path.read_text() == prompt
        invalid = deepcopy(request)
        invalid["turn_envelope"]["required_reads"] = []
        rejected = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
        assert rejected.returncode != 0 and "action signature" in rejected.stderr
        assert prompt_path.read_text() == prompt
    else:
        actual, _ = json.JSONDecoder().raw_decode(prompt.split("Turn request:\n", 1)[1])
        # The current producer's own immutable capture is the transport oracle;
        # the original long task and optional context remain independent inputs.
        detail = actual["detail_ref"]["captured_decision"]
        delivered_work = json.loads(Path(detail["path"]).read_text())
        assert delivered_work == plan["work_contract"]
        model_context = assert_host_task_context(actual, delivered_work)
        assert actual["source_decision_hash"] == plan["source_decision_hash"]
        assert "Validate the complete acceptance: " + "preserve original evidence; " * 180 in json.dumps(model_context)
        delivered_context = delivered_work["interaction_contract"].get("agent_context")
        assert delivered_context == context
        if context:
            assert "detail_ref" not in delivered_context
        for field, value in (("reason", "Changed original facts"),
                             ("schema_version", "loopx_turn_envelope_v999")):
            invalid = deepcopy(request)
            invalid["work_contract"][field] = value
            rejected = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
            assert rejected.returncode != 0 and "complete Turn contract rejected" in rejected.stderr
            assert prompt_path.read_text() == prompt  # Refusal precedes the provider seam.
        channel = source["interaction_contract"]["agent_channel"]
        assert not any(read.get("source") == "goal_state" for read in channel["required_reads"])
        assert any(row.get("source") == "goal_state"
            for row in channel["work_context"]["sources"])
        invalid = deepcopy(request)
        invalid["work_contract"]["interaction_contract"]["agent_channel"]["work_context"]["sources"] = []
        rejected = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
        assert rejected.returncode != 0 and "complete Turn contract rejected" in rejected.stderr
        assert prompt_path.read_text() == prompt
    assert (capture / "decision.json").read_bytes() == original
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_bound_replan_reaches_actual_host_with_original_evidence(tmp_path, monkeypatch, provider):
    from loopx.control_plane.turn_driver.codex_cli import prepare_codex_cli_input

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    stop = "Retain every accepted outcome; stop before modifying production data."
    state.write_text(state.read_text() + f"\n## Accepted scope and stop\n\n{stop}\n")
    goal_bytes = state.read_bytes()
    _initialize_git_checkout(project)
    # Ordinary reads reject unmigrated Goals. Bootstrap the disposable canonical
    # fixture through its explicit conversion helper before exercising delivery.
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "host-replan-turn"
    code, initial = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", turn,
        "--codex-app",  cwd=project)
    assert code == 0 and initial["decision"] == "autonomous_replan_required", initial
    _bind_selected_replan_guard(registry, runtime, project, turn)
    capture = tmp_path / "capture"
    code, emitted = _run_cli(registry, runtime, "quota", "should-run",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-instance-id", turn,
        "--codex-app", "--decision-output-dir", str(capture), cwd=project)
    assert code == 0, emitted
    original = (capture / "decision.json").read_bytes()
    source = json.loads(original)
    assert emitted == source and source["schema_version"] == "loopx_turn_envelope_v1"
    agent_channel = source["interaction_contract"]["agent_channel"]
    assert agent_channel["delivery_allowed"] is False
    assert agent_channel["must_attempt"] is True
    plan = build_loopx_turn_plan(source, host="dsh", execution_mode="isolated-headless", turn_instance_id=turn)
    assert plan["work_contract"] == source
    plan = prepare_turn_decision_capture(plan, journal_path=tmp_path / "turn" / "journal.json",
        decision=plan["work_contract"], existing_journal=False)
    assert plan["route"]["kind"] == "replan_required"
    assert plan["route"]["would_invoke_host"] is True
    assert source["heartbeat_receipt"]["settlement_identity"]["todo_id"] == SELECTED_REPLAN_TODO_ID

    runner = tmp_path / "runner.py"
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(*, prompt, workspace, **kwargs):\n"
        "    Path(workspace, 'received.txt').write_text(prompt)\n"
        "    return json.dumps({'result_kind':'wait','summary':'Replan input recorded.'})\n"
    )
    command = [sys.executable, "-m", "loopx.hosts.dsh", "--workspace", str(project),
               "--dsh-home", str(tmp_path / "host-home"), "--dsh-runner", str(runner)]
    request = build_loopx_turn_host_request(plan)
    codex_prompt, _ = prepare_codex_cli_input(request)
    assert "Read detail_ref.replan_action_packet.review" in codex_prompt
    assert "before reading detail_ref.replan_action_packet.writeback" in codex_prompt
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["result_kind"] == "wait"
    prompt_path = project / "received.txt"
    prompt = prompt_path.read_text()
    actual, _ = json.JSONDecoder().raw_decode(prompt.split("Turn request:\n", 1)[1])
    assert actual["schema_version"] == "loopx_turn_model_context_v0"
    assert actual["source_decision_hash"] == plan["source_decision_hash"]
    assert not {"work_contract", "turn_envelope", "turn_authority"}.intersection(actual)
    model_context = actual["task_context"]
    model_replan = model_context["replan_action_packet"]
    original_replan = plan["work_contract"]["replan_action_packet"]
    for key in ("obligation_id", "planning_guidance", "uncovered_frontier"):
        assert model_replan[key] == original_replan[key]
    original_plan = source["interaction_contract"]["cli_channel"]["settlement_plan"]
    authoring = next(step["vision_authoring"] for step in original_plan["ordered_steps"]
                     if step["kind"] == "durable_writeback")
    assert model_replan["writeback_contract"]["vision_authoring"] == authoring
    # Execute the original observation readers actually delivered to the provider.
    detail = actual["detail_ref"]
    packet = detail["replan_action_packet"]
    review = subprocess.run(shlex.split(packet["review"]), capture_output=True, text=True, check=True)
    context = source["autonomous_replan_obligation"]["replan_context"]
    assert json.loads(review.stdout)["context_id"] == context["context_id"]
    captured = detail["captured_decision"]
    assert (captured["goal_id"], captured["agent_id"], captured["turn_instance_id"]) == (GOAL_ID, AGENT_ID, turn)
    read = subprocess.run(shlex.split(detail["full_decision"]), capture_output=True, check=True).stdout
    recovered = json.loads(read)
    assert recovered == plan["work_contract"]
    assert recovered["contract_binding"]["content_hash"] == actual["source_decision_hash"]
    assert context["obligation_id"] == source["replan_action_packet"]["obligation_id"]
    delivered = model_context["interaction_contract"]["agent_channel"]
    goal_source = next(row["content"] for row in delivered["work_context"]["sources"]
                       if row["source"] == "goal_state")
    assert goal_source["full_source"] is True
    assert goal_source["text"].encode() == goal_bytes and stop in goal_source["text"]
    assert not any(row["source"] == "goal_state" for row in delivered["required_reads"])
    assert "Read detail_ref.replan_action_packet.review" in prompt
    assert "before reading detail_ref.replan_action_packet.writeback" in prompt
    assert "Never re-enter quota admission" in prompt
    assert "Do not execute writeback commands" in prompt
    for field in ("obligation_id", "planning_guidance", "uncovered_frontier"):
        invalid = deepcopy(request)
        invalid["work_contract"]["replan_action_packet"][field] = "changed after signing"
        rejected = subprocess.run(command, input=json.dumps(invalid), text=True, capture_output=True, timeout=30)
        assert rejected.returncode != 0 and "complete Turn contract rejected" in rejected.stderr
        assert prompt_path.read_text() == prompt
    assert (capture / "decision.json").read_bytes() == original
    assert state.read_bytes() == goal_bytes
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before
