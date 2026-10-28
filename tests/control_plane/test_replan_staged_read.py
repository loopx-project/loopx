"""Captured replan reads separate decision facts from authoring without re-admission."""
from __future__ import annotations

import copy
import json
import shlex
import os
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.effect_runtime import EffectRuntimeRejected, restart_effect_runtime
from loopx.control_plane.quota.turn_envelope import (
    _turn_envelope_result, build_turn_envelope, quota_action_signature_document,
    turn_envelope_action_signature_document, prepare_context_delivery, restore_context_delivery,
)
from test_replan_host_context_projection import _quota_payload
from test_replan_progress_native_cli import (
    AGENT, GOAL, TODO, TURN, call_progress_cli, make_progress_cli_fixture,
)


def json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def without_example(contract):
    expected = copy.deepcopy(contract)
    if "vision_authoring" in expected:
        expected["vision_authoring"].pop("minimal_example")
    return expected


@pytest.mark.parametrize("stride", ["off", "evidence_conditioned_v1"])
def test_staged_packet_budget_and_lossless_authority(stride):
    source = _quota_payload(stride_strategy=stride)
    legacy = build_turn_envelope(source)
    source["replan_read_path"] = "/synthetic/decision.json"
    original = copy.deepcopy(source)
    envelope = build_turn_envelope(source, captured_decision_path=source["replan_read_path"])
    packet = envelope["replan_action_packet"]
    assert "--replan-stage review" in packet["review"]
    assert "--replan-stage writeback" in packet["writeback"]
    assert "planning_guidance" not in packet and "stride_control" not in packet
    assert json_bytes(packet) <= 2048
    assert json_bytes(packet) < json_bytes(legacy["replan_action_packet"]) / 2
    assert "settlement_plan" not in envelope["writeback"]
    assert "next_cli_actions" not in envelope["writeback"]
    assert envelope["writeback"]["settlement_read_ref"] == "$.replan_action_packet.writeback"
    for field in ("spend_allowed_now", "spend_after_validation", "spend_policy"):
        assert envelope["writeback"][field] == legacy["writeback"][field]
    for field in ("boundary", "effective_action", "user", "scheduler"):
        assert envelope.get(field) == legacy.get(field)
    assert turn_envelope_action_signature_document(envelope) == quota_action_signature_document(source)
    params = {"payload": source, "source_hash": envelope["action_signature"]["source_decision_hash"]}
    review = _turn_envelope_result("replan_view", **params, stage="review")
    writer = _turn_envelope_result("replan_view", **params, stage="writeback")
    assert writer["writeback"] == legacy["writeback"]
    assert "context_read" not in writer  # No unused instruction to repeat review.
    context = source["autonomous_replan_obligation"]["replan_context"]
    assert review["core_goal"] == context["core_goal"]
    assert review["planning_guidance"] == source["replan_action_packet"]["planning_guidance"]
    assert review["planning_horizon"] == source.get("planning_horizon")
    assert review["uncovered_frontier"] == context["uncovered_frontier"]
    assert review["coverage_count"] == context["coverage_count"]
    assert review["coverage_truncated"] == context["coverage_truncated"]
    assert [row["evidence_ref"] for row in review["evidence"]] == [row["evidence_ref"] for row in context["evidence"]]
    assert {k: v for k, v in writer["writeback_contract"].items() if k not in {"successor_binding_args", "successor_settlement"}} == without_example(source["replan_action_packet"]["writeback_contract"])
    assert writer["allowed_terminal"] == source["replan_action_packet"]["allowed_terminal"]
    assert review["context_id"] == writer["context_id"] == context["context_id"]
    assert json_bytes(review) <= 16384
    assert "guidance" not in review
    assert json_bytes(writer) <= 16384
    if stride != "off":
        control = writer["stride_control"]
        assert control["previous_decision_ref"] == source["replan_action_packet"]["stride_control"]["previous_decision_ref"]
        assert "decision_followthrough" not in control and "decision_value_review" not in control
        assert "minimal_example" not in control
        assert control["basis_options"] == ["supported_progress", "unexplained_gap", "uncertain_evidence", "useful_new_route", "decision_failure"]
        assert {"basis", "action", "axes", "next_probe"} <= set(control["required_fields"])
    assert source == original


def enable_explore(fixture):
    registry = json.loads(fixture["registry"].read_bytes())
    registry["goals"][0]["explore_graph"] = {"enabled": True}
    fixture["registry"].write_text(json.dumps(registry))


def explore_source(packet):
    sources = packet["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    return next(row for row in sources if row.get("hook_id") == "explore.turn_context")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_pure_replan_does_not_advertise_unavailable_explore_attachment(tmp_path, monkeypatch, provider):
    from test_replan_successor_guard_reentry import _fixture, GOAL as goal, AGENT as agent, TURN as turn
    from loopx.capabilities.explore.result_writeback import prepare_result_attachment

    call, runtime, _ = _fixture(tmp_path, monkeypatch, provider)
    enable_explore({"registry": tmp_path / "registry.json"})
    capture = tmp_path / "capture"
    try:
        entry = call("quota", "should-run", "--codex-app", "--goal-id", goal,
            "--agent-id", agent, "--turn-instance-id", turn, "--decision-output-dir", str(capture))
        source = json.loads((capture / "decision.json").read_bytes())
        original = explore_source(source)["content"]["graph"]
        graph = explore_source(entry)["content"]["graph"]
        assert entry["heartbeat_receipt"]["settlement_identity"]["binding_kind"] == "autonomous_replan"
        assert not entry.get("selected_todo")
        assert "result_attachment_template" not in graph and "result_writeback_read" not in graph
        assert "unavailable for this pure replan" in graph["result_writeback_guidance"]
        assert "Read measurement_feedback before repeating a measured route" in graph["result_writeback_guidance"]
        assert "never invent a shared version" in graph["result_writeback_guidance"]
        assert "Capture is optional, not a settlement obligation." in graph["result_writeback_guidance"]
        for key in ("writeback_results", "measurement_feedback", "result_page", "recent_findings",
                    "record_node_template", "record_finding_template", "guidance"):
            assert graph.get(key) == original.get(key)
        assert json_bytes(original) - json_bytes(graph) > 2000
        writer_run = subprocess.run(shlex.split(entry["replan_action_packet"]["writeback"]),
            capture_output=True, text=True, check=True)
        writer = json.loads(writer_run.stdout)
        assert all(not row.get("optional_attachments")
            for row in writer["writeback"]["settlement_plan"]["ordered_steps"])
        # Prove the real attachment owner rejects this binding; projection must
        # not suggest a new way around it or change its primary commit behavior.
        attachment = {**original["result_attachment_template"], "question": "Is the source valid?",
            "applicability": "Synthetic source", "input_revision": "source-v1", "observation": "Reviewed",
            "interpretation": "Unresolved", "status": "tentative", "evidence_refs": ["evidence:source-review"]}
        with pytest.raises(ValueError, match="requires agent, Todo and Turn identity"):
            prepare_result_attachment(attachment, registry_path=tmp_path / "registry.json",
                runtime_root=runtime, goal_id=goal, agent_id=agent, todo_id=None, turn_instance_id=turn)
        # No capture, unknown instructions, ambiguous authoring and unproven
        # identity keep the old payload rather than guessing applicability.
        for mutation in ("capture", "custom", "identity", "attachment"):
            changed = copy.deepcopy(source)
            expected = explore_source(changed)["content"]
            if mutation == "capture":
                changed.pop("replan_read_path")
            elif mutation == "custom":
                expected["graph"]["result_writeback_guidance"] += " Preserve a custom obligation."
            elif mutation == "identity":
                changed["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"].pop("binding_kind")
            else:
                step = next(row for row in changed["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]
                    if row["kind"] == "durable_writeback")
                step["optional_attachments"] = [{"capability_id": "explore", "guidance": "Custom authoring"}]
            projected = _turn_envelope_result("replan_cli_view", payload=changed)
            assert explore_source(projected)["content"] == expected
        assert json.loads((capture / "decision.json").read_bytes()) == source
    finally:
        restart_effect_runtime(wait_for_exit=True)


def assert_explore_entry_and_writer(source, entry, writer):
    original = explore_source(source)["content"]["graph"]
    graph = explore_source(entry)["content"]["graph"]
    if "settlement_plan" not in writer["writeback"]:
        assert graph == original  # Selection has not yet bound a captured writer.
        return
    assert graph["result_writeback_read"] == entry["replan_action_packet"]["writeback"]
    assert "result_attachment_template" not in graph
    # These obligations apply BEFORE choosing or executing work, not at closeout.
    assert "Read measurement_feedback before repeating a measured route" in graph["result_writeback_guidance"]
    assert "keep the active task lease through refresh-state" in graph["result_writeback_guidance"]
    assert "Keep raw logs local." in graph["result_writeback_guidance"]
    assert "Capture is optional, not a settlement obligation." in graph["result_writeback_guidance"]
    assert "never invent a shared version" in graph["result_writeback_guidance"]
    assert "Result evidence must name the candidate it actually evaluated" in graph["result_writeback_guidance"]
    for key in ("writeback_results", "measurement_feedback", "recent_findings", "result_page", "guidance"):
        assert graph.get(key) == original.get(key)
    step = next(row for row in writer["writeback"]["settlement_plan"]["ordered_steps"] if row["kind"] == "durable_writeback")
    attachment = next(row for row in step["optional_attachments"] if row["capability_id"] == "explore")
    assert attachment["guidance"] == original["result_writeback_guidance"]
    assert attachment["attachment_template"] == original["result_attachment_template"]
    assert json_bytes(original) - json_bytes(graph) > 2000


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("replan", [True, False])
@pytest.mark.parametrize("context_delivery", [False, True])
def test_checkpoint_preflight_delivers_the_real_cli_view_and_retains_full_source(tmp_path, monkeypatch, provider, replan, context_delivery):
    """Exercise the production preflight without a model or evaluator.

    A full-source fixture alone missed the original bug: the model must be sent
    the captured reader, while checkpoint selection still sees full context.
    """
    from benchmark.runtime import checkpoint

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    enable_explore(fixture)
    with fixture["state"].open("a") as stream:
        stream.write("\n## Original additional requirements\nPreserve this final independent stop.\n")
    if not replan:
        fixture["index"].write_text("")
    wake = tmp_path / "wakes" / TURN
    wake.mkdir(parents=True)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"trigger": "fresh_replan", "destination": str(tmp_path / "checkpoints"),
        "components": [{"id": "code", "source_path": str(tmp_path / "code")}], "point_metadata": {}}))
    executable = Path(sys.executable).with_name("loopx")
    env = {**os.environ, "LOOPX_USAGE_PING": "0", "LOOPX_CLI": str(executable),
        "LOOPX_CHECKPOINT_REQUEST": str(request), "LOOPX_WAKE_LOG_DIR": str(wake.parent),
        "LOOPX_PROJECT": str(fixture["project"]), "LOOPX_RUNTIME_ROOT": str(fixture["runtime"]),
        "LOOPX_REGISTRY": str(fixture["registry"]), "LOOPX_GOAL_ID": GOAL, "LOOPX_AGENT_ID": AGENT,
        "LOOPX_TURN_ENVELOPE": "0"}
    if context_delivery:
        native = tmp_path / "native-context"
        native.mkdir(mode=0o700)
        env.update(LOOPX_CONTEXT_DELIVERY="on", LOOPX_EXECUTION_MODE="heartbeat", LOOPX_CODEX_CONTEXT_DIR=str(native))
    else:
        env["LOOPX_CONTEXT_DELIVERY"] = "off"
    # This boundary has no native session to capture. Stop only the optional
    # artifact operation; real quota, selector and reader remain unmocked.
    monkeypatch.setattr("benchmark.runtime.checkpoint_continuation.restored_capture_gate",
        lambda *a, **k: {"capture_allowed": False, "reason": "synthetic_no_native_capture"})
    run, commands = checkpoint.subprocess.run, []
    def observe(command, **kwargs):
        commands.append(command)
        return run(command, **kwargs)
    monkeypatch.setattr(checkpoint.subprocess, "run", observe)
    receipt = {"turn_id": TURN, "mode": "heartbeat", "stage": "execute"}
    try:
        body = checkpoint.capture_before_replan(env, receipt, "original heartbeat instructions")
        assert len(commands) == 1 and "--turn-envelope" not in commands[0]
        assert "--decision-output-root" in commands[0]
        source_path, view_path = wake / "checkpoint-start-guard.json", wake / "checkpoint-start-view.json"
        source, view = json.loads(source_path.read_bytes()), json.loads(view_path.read_bytes())
        assert str(view_path) in body and str(source_path) not in body
        assert body.endswith("original heartbeat instructions")
        if context_delivery:
            assert "--defer-context-presentation" in commands[0]
            assert "--context-delivery" in commands[0]
            assert not list(native.iterdir()), "Host preflight must not alter native continuity"
            assert not any(read.get("source") == "goal_state" for read in
                view["interaction_contract"]["agent_channel"]["required_reads"])
            delivered = [row["content"] for row in view["interaction_contract"]["agent_channel"]
                ["work_context"]["sources"] if row.get("source") == "goal_state"]
            if replan:
                # This fixture has no bound work: the staged review owns the
                # original Goal read, not ordinary work-context delivery.
                core = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]
                assert shlex.split(core["read_action"]) == ["cat", "--", str(fixture["state"].resolve())]
            else:
                assert len(delivered) == 1 and delivered[0]["full_source"] is True
                assert delivered[0]["text"] == fixture["state"].read_text()
            # The optional full/delta transport must present the staged CLI
            # view too. Its full recovery must not restore old inline hints.
            command = body.split("```sh\n", 1)[1].split("\n```", 1)[0]
            projected = run(shlex.split(command), cwd=fixture["project"], env=env,
                capture_output=True, text=True, timeout=60)
            assert projected.returncode == 0, projected.stdout
            presentation = json.loads(projected.stdout)
            assert presentation["ok"] and presentation["mode"] == "full"
            assert presentation["recovery"]["path"] == str(view_path)
        assert source["heartbeat_receipt"]["turn_instance_id"] == TURN
        assert source["heartbeat_receipt"] == view["heartbeat_receipt"]
        assert receipt["checkpoint"]["reason"] == "synthetic_no_native_capture"
        selection = view["interaction_contract"]["cli_channel"].get("selection_command")
        if selection:
            assert "--decision-output-root" in selection["command_args_template"]
        selector = checkpoint.effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "select_replan", "quota": source, "goal_id": GOAL, "agent_id": AGENT,
            "turn_instance_id": TURN}, large_local_snapshot=True)
        assert selector["capture"] is replan
        if not replan:
            assert "result_writeback_read" not in explore_source(view)["content"]["graph"]
            assert "replan_action_packet" not in view
            return
        assert source["autonomous_replan_obligation"]["replan_context"]["context_id"]
        assert "replan_context" not in view["autonomous_replan_obligation"]
        packet = view["replan_action_packet"]
        assert "planning_guidance" not in packet
        capture = Path(source["replan_read_path"])
        assert capture.read_bytes() == source_path.read_bytes()
        before = {key: fixture[key].read_bytes() for key in ("registry", "state", "index")}
        for stage in ("review", "writeback"):
            result = run(shlex.split(packet[stage]), cwd=fixture["project"], env=env,
                capture_output=True, text=True, timeout=60, check=True)
            read = json.loads(result.stdout)
            assert read["obligation_id"] == selector["obligation_id"]
            assert read["stage"] == stage
            if stage == "writeback":
                assert_explore_entry_and_writer(source, view, read)
        assert {key: fixture[key].read_bytes() for key in before} == before
        assert capture.read_bytes() == source_path.read_bytes()
        assert len(commands) == 1  # Reads did not re-enter checkpoint preflight.
    finally:
        restart_effect_runtime(wait_for_exit=True)


def test_review_retains_scoped_dependencies_and_unknown_outcomes():
    source = _quota_payload()
    source["replan_read_path"] = "/synthetic/decision.json"
    # These are canonical typed planning facts, never text classified as blockers.
    source["planning_horizon"] = {"items": [
        {"todo_id": "todo-report", "state": "waiting"},
        {"todo_id": "todo-implementation", "state": "runnable"},
    ], "relations": [{"from_todo_id": "todo-report", "to_todo_id": "todo-measurement", "kind": "depends_on"}]}
    context = source["autonomous_replan_obligation"]["replan_context"]
    context["decision_followthrough"] = {"recorded_progress_count": 21,
        "result_counts": {"advanced": 1, "blocked": 20, "unchanged": 0, "no_followup": 0, "exploration_exhausted": 0},
        "observations": [{"todo_id": "todo-report", "result_class": "blocked", "delivery_outcome": None}],
        "execution_and_goal_gain": "unknown"}
    envelope = build_turn_envelope(source)
    review = _turn_envelope_result("replan_view", payload=source, stage="review",
        source_hash=envelope["action_signature"]["source_decision_hash"])
    assert review["planning_horizon"] == source["planning_horizon"]
    assert review["decision_followthrough"]["observation_count"] == 21
    assert review["decision_followthrough"]["result_counts"]["advanced"] == 1
    assert review["decision_followthrough"]["execution_and_goal_gain"] == "unknown"
    assert "recommended_action" not in review  # No hardcoded pivot or automatic wait.


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("envelope", [False, True])
def test_real_guard_capture_read_and_writeback_without_another_guard(tmp_path, monkeypatch, provider, envelope):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = make_progress_cli_fixture(tmp_path, provider, "advanced")
    enable_explore(fixture)
    capture = tmp_path / "capture"
    try:
        initial = call_progress_cli(fixture, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN)
        assert initial["decision"] == "autonomous_replan_required"
        # Exercise the emitted command verbatim even with a hostile ambient
        # PATH entry. The captured reader belongs to the originating checkout.
        pinned_cli = str(Path(sys.executable).with_name("loopx"))
        guard = call_progress_cli(fixture, "--cli-bin", pinned_cli, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN,
            "--todo-id", TODO, "--decision-output-dir", str(capture),
            *(["--turn-envelope"] if envelope else []))
        packet = guard["replan_action_packet"]
        if not envelope:
            assert "settlement_plan" not in guard["interaction_contract"]["cli_channel"]
            assert guard["interaction_contract"]["cli_channel"]["settlement_read_ref"] == "$.replan_action_packet.writeback"
        saved = (capture / "decision.json").read_bytes()
        source = json.loads(saved)
        # Unknown old/custom prose, different templates, ambiguous/missing
        # writers and missing capture must remain fully inline.
        for mutation in ("custom", "template", "duplicate", "missing", "capture"):
            changed = copy.deepcopy(source)
            graph = explore_source(changed)["content"]["graph"]
            step = next(row for row in changed["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]
                if row["kind"] == "durable_writeback")
            attachment = next(row for row in step["optional_attachments"] if row["capability_id"] == "explore")
            if mutation == "custom":
                graph["result_writeback_guidance"] += " Stop before a new operation."
                attachment["guidance"] = graph["result_writeback_guidance"]
            elif mutation == "template":
                graph["result_attachment_template"]["input_revision"] = "different-revision"
            elif mutation == "duplicate":
                step["optional_attachments"].append(copy.deepcopy(attachment))
            elif mutation == "missing":
                step.pop("optional_attachments")
            else:
                changed.pop("replan_read_path")
            projected = _turn_envelope_result("replan_cli_view", payload=changed)
            assert explore_source(projected)["content"]["graph"] == graph
        entry_writeback = guard["writeback"] if envelope else guard["interaction_contract"]["cli_channel"]
        assert "settlement_plan" not in entry_writeback and "next_cli_actions" not in entry_writeback
        assert entry_writeback["settlement_identity"] == source["heartbeat_receipt"]["settlement_identity"]
        original_files = {key: fixture[key].read_bytes() for key in ("registry", "state", "index")}
        # Executable commands come from the production packet. Deliberately use
        # an absent registry: the reader must never collect state or re-admit.
        trap = tmp_path / "bin"
        trap.mkdir()
        (trap / "loopx").write_text("#!/bin/sh\nexit 73\n")
        (trap / "loopx").chmod(0o700)
        reader_fixture = {**fixture, "registry": tmp_path / "missing-registry.json"}
        def read(stage, expected_code=0):
            args = shlex.split(packet[stage])
            assert args[:3] == [pinned_cli, "--cli-bin", pinned_cli]
            result = subprocess.run([args[0], "--registry", str(reader_fixture["registry"]), *args[1:]],
                cwd=fixture["project"], env={**os.environ, "PATH": str(trap) + os.pathsep + os.environ["PATH"],
                    "LOOPX_USAGE_PING": "0"}, capture_output=True, text=True, timeout=60)
            assert result.returncode == expected_code, (result.stdout, result.stderr)
            return json.loads(result.stdout)
        review, writer = read("review"), read("writeback")
        if not envelope:
            assert_explore_entry_and_writer(source, guard, writer)
        else:
            assert_explore_entry_and_writer(source, {
                "interaction_contract": {"agent_channel": {"work_context": guard["work_context"]}},
                "replan_action_packet": packet}, writer)
        assert writer["writeback"]["settlement_plan"] == source["interaction_contract"]["cli_channel"]["settlement_plan"]
        assert writer["settlement_command"].endswith(" --settle-turn")
        # Real CLI/default-off and native readers both deliver the original
        # planning facts after admission, without a second body in the entry.
        expected_goal = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]
        assert review["core_goal"] == expected_goal
        assert review["action_portfolio"] == source.get("action_portfolio")
        if not envelope:
            assert "core_goal" not in guard and "planning_horizon" not in guard
            assert guard.get("action_portfolio") == source.get("action_portfolio")
        assert review["context_id"] == writer["context_id"]
        assert review["obligation_id"] == source["replan_action_packet"]["obligation_id"]
        assert {k: v for k, v in writer["writeback_contract"].items() if k not in {"successor_binding_args", "successor_settlement"}} == without_example(source["replan_action_packet"]["writeback_contract"])
        assert (capture / "decision.json").read_bytes() == saved
        assert {key: fixture[key].read_bytes() for key in original_files} == original_files
        assert not reader_fixture["registry"].exists()
        # Changed bytes cannot masquerade as the admitted observation.
        original_source = copy.deepcopy(source)
        for carrier, field, value in [
            ((), "goal_id", "another-goal"),
            (("agent_identity",), "agent_id", "another-agent"),
            (("heartbeat_receipt",), "turn_instance_id", "another-turn"),
            (("autonomous_replan_obligation", "replan_context"), "core_goal", {"objective": "Changed acceptance"}),
        ]:
            source = copy.deepcopy(original_source)
            target = source
            for key in carrier:
                target = target[key]
            target[field] = value
            (capture / "decision.json").write_text(json.dumps(source))
            rejected = read("review", expected_code=1)
            assert "source hash" in rejected["reason"]
        (capture / "decision.json").write_bytes(saved)
        # A valid next operation still reaches the existing writer and semantic gate.
        result = call_progress_cli(fixture, *fixture["refresh"])
        assert result["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
    finally:
        restart_effect_runtime(wait_for_exit=True)


def _views(source):
    envelope = build_turn_envelope(source)
    params = {"payload": source, "source_hash": envelope["action_signature"]["source_decision_hash"]}
    return envelope, _turn_envelope_result("replan_view", **params, stage="review"), params


def test_populated_history_and_cumulative_acceptance_have_separate_budgets(tmp_path):
    from replan_packet_fixture import populated_replan_source

    base = populated_replan_source(tmp_path / "base")
    envelope, review, _ = _views(base)
    context = base["autonomous_replan_obligation"]["replan_context"]
    assert context["evidence_count"] == 1500 and len(context["evidence"]) == 24
    assert context["from_full_index"] is True
    assert {row["planning_state"] for row in review["planning_horizon"]["work_items"]} == {"selected", "waiting", "runnable"}
    assert context["coverage_truncated"] and context["evidence_truncated"]
    assert review["decision_followthrough"]["observation_count"] == 21
    assert review["decision_followthrough"]["omitted_observation_count"] == 15
    assert sum(review["decision_followthrough"]["result_counts"].values()) == 21
    assert review["decision_followthrough"]["result_counts"]["advanced"] < 21
    no_planning = copy.deepcopy(base)
    no_planning.pop("planning_horizon")
    no_planning.pop("action_portfolio")
    _, history_review, _ = _views(no_planning)
    assert json_bytes(history_review) <= 16 * 1024
    assert 0 < json_bytes(review) - json_bytes(history_review) <= 8 * 1024
    assert json_bytes(envelope["replan_action_packet"]) <= 2 * 1024

    large = populated_replan_source(tmp_path / "acceptance", acceptance=True)
    large_envelope, expanded, params = _views(large)
    # Assert the complete emitted envelope too: moving the payload into a second
    # hot carrier would satisfy a packet-only budget while defeating the fix.
    # The Goal itself is replayed each Turn. Only cumulative outcome detail is
    # indexed; it must not become a second 200 KiB inline carrier.
    assert 0 < json_bytes(large_envelope) - json_bytes(envelope) <= 4 * 1024
    assert large_envelope["action"]["core_goal"]["objective"] == base["autonomous_replan_obligation"]["replan_context"]["core_goal"]["objective"]
    assert large_envelope["action"]["planning_horizon"]["core_goal_ref"] == "$.action.core_goal"
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority
    authority = extract_turn_authority({"turn_envelope": large_envelope})
    assert not {"core_goal", "planning_horizon", "acceptance_index"} & authority.keys()
    assert authority["replan_action_packet"]["review"]
    assert expanded["core_goal"] == large_envelope["action"]["core_goal"]
    assert large_envelope["action"]["planning_horizon"]["core_goal_ref"] == "$.action.core_goal"
    original = large["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    assert json_bytes(original) > 200 * 1024
    assert expanded["planning_horizon"]["core_goal_ref"] == "$.core_goal"
    index = expanded["acceptance_index"]
    assert len(index["outcomes"]) == 24
    # Cumulative acceptance earns its own index allocation; it cannot spend the
    # generic review allowance by duplicating entire historical reviews.
    assert 0 < json_bytes(expanded) - json_bytes(review) <= 12 * 1024
    originals = original["acceptance_contract"]["outcome_requirements"]["outcomes"]
    for row, outcome in zip(index["outcomes"], originals, strict=True):
        read = _turn_envelope_result("replan_view", **params, stage="acceptance", outcome_ref=row["outcome_ref"])
        assert read["outcome"] == outcome
        assert row["requirement_count"] == len(outcome["requirements"])
    # A different source revision with the same outcome id must remain visible.
    large["planning_horizon"]["core_goal"]["acceptance_contract"]["outcome_requirements"]["outcomes"][0]["revision"] = 3
    _, revised, params = _views(large)
    same_id = [row for row in revised["acceptance_index"]["outcomes"] if row["id"] == "outcome-0"]
    assert len(same_id) == 2 and same_id[0]["outcome_ref"] != same_id[1]["outcome_ref"]
    with pytest.raises(EffectRuntimeRejected, match="exact captured outcome reference"):
        _turn_envelope_result("replan_view", **params, stage="acceptance", outcome_ref="missing")


def test_summary_acceptance_uses_lossless_captured_index_after_cli_compaction(tmp_path):
    from replan_packet_fixture import populated_replan_source
    from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload

    source = populated_replan_source(tmp_path, acceptance=True)
    contract = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract"]
    summary = {"goal_acceptance_contract": copy.deepcopy(contract), "total": 7,
               "completed": 2, "selected_todo_id": "todo_independent"}
    source["agent_todo_summary"] = summary
    original = copy.deepcopy(source)
    projected = _turn_envelope_result("replan_cli_view", payload=source)
    cli = compact_quota_should_run_cli_payload(projected)
    delivered = cli["agent_todo_summary"]
    assert {key: delivered[key] for key in ("total", "completed", "selected_todo_id")} == {
        key: summary[key] for key in ("total", "completed", "selected_todo_id")}
    assert delivered["goal_acceptance_contract"] == {}
    index = delivered["outcome_requirements_index"]
    assert {key: value for key, value in index.items() if key != "outcome_refs"} == {
        key: value for key, value in contract["outcome_requirements"].items() if key != "outcomes"}
    _, review, params = _views(source)
    assert index["outcome_refs"] == [row["outcome_ref"] for row in review["acceptance_index"]["outcomes"]]
    for ref, outcome in zip(index["outcome_refs"], contract["outcome_requirements"]["outcomes"], strict=True):
        assert _turn_envelope_result("replan_view", **params, stage="acceptance", outcome_ref=ref)["outcome"] == outcome
    assert json_bytes(summary) - json_bytes(delivered) > 200 * 1024
    assert source == original
    # Same IDs are insufficient: changed facts and existing indices stay inline.
    changed = copy.deepcopy(source)
    changed["agent_todo_summary"]["goal_acceptance_contract"]["outcome_requirements"]["revision"] += 1
    assert _turn_envelope_result("replan_cli_view", payload=changed)["agent_todo_summary"] == changed["agent_todo_summary"]
    changed = copy.deepcopy(source)
    changed["agent_todo_summary"]["outcome_requirements_index"] = {"revision": "other"}
    assert _turn_envelope_result("replan_cli_view", payload=changed)["agent_todo_summary"] == changed["agent_todo_summary"]
    source.pop("replan_read_path")
    assert _turn_envelope_result("replan_cli_view", payload=source) == source


def test_large_acceptance_is_not_repeated_in_cli_or_envelope_work_context(tmp_path):
    from replan_packet_fixture import populated_replan_source

    source = populated_replan_source(tmp_path, acceptance=True)
    contract = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract"]
    other = copy.deepcopy(contract)
    other["outcome_requirements"]["revision"] += 1
    sources = [{"source": "goal_acceptance", "content": {"status": "loaded", "provider_revision": "fixture:1",
        "goal_acceptance_contract": value}} for value in (contract, other)]
    work = {"complete": True, "sources": sources}
    source["interaction_contract"]["agent_channel"]["work_context"] = work
    original = copy.deepcopy(source)
    cli = _turn_envelope_result("replan_cli_view", payload=source)
    envelope = build_turn_envelope(source)
    for delivered in (cli["interaction_contract"]["agent_channel"]["work_context"], envelope["work_context"]):
        matched, different = delivered["sources"]
        assert matched["content"] == {"status": "loaded", "provider_revision": "fixture:1",
            "context_read": cli["replan_action_packet"]["review"]}
        assert different == sources[1]  # Another revision is not covered by this reader.
        assert json_bytes(work) - json_bytes(delivered) > 200 * 1024
    assert source == original
    _, review, params = _views(source)
    rows = review["acceptance_index"]["outcomes"]
    for row, outcome in zip(rows, contract["outcome_requirements"]["outcomes"], strict=True):
        assert _turn_envelope_result("replan_view", **params, stage="acceptance", outcome_ref=row["outcome_ref"])["outcome"] == outcome
    # The ordinary quota owner can reference its identical inline acceptance.
    # Index it before deferring that source so the horizon cannot dangle.
    horizon_core = source["planning_horizon"]["core_goal"]
    horizon_core.pop("acceptance_contract")
    horizon_core["acceptance_contract_ref"] = "$.interaction_contract.agent_channel.work_context.sources[0].content.goal_acceptance_contract"
    ref_entry = build_turn_envelope(source)
    assert ref_entry["action"]["planning_horizon"]["core_goal_ref"] == "$.action.core_goal"
    horizon_core["acceptance_contract_ref"] = "$.interaction_contract.agent_channel.work_context.sources[9].content.goal_acceptance_contract"
    with pytest.raises(EffectRuntimeRejected, match="complete inline work context"):
        build_turn_envelope(source)


def test_staged_entry_replays_goal_and_changed_horizon_with_existing_delta_owner(tmp_path):
    from replan_packet_fixture import populated_replan_source

    source = populated_replan_source(tmp_path, acceptance=True)
    entry = _turn_envelope_result("replan_cli_view", payload=source)
    assert entry["action_portfolio"] == source["action_portfolio"]
    scope = {"goal_id": source["goal_id"], "agent_id": source["agent_identity"]["agent_id"],
        "session_id": "synthetic-session", "context_epoch": "one"}
    full = prepare_context_delivery(entry, scope=scope, capture_id="entry-a", surface="quota_packet")
    for changed in (False, True):
        current = copy.deepcopy(source)
        if changed:
            current["planning_horizon"]["work_items"][0].update(priority="P0", successor_todo_ids=["todo-new-dependency"])
        value = _turn_envelope_result("replan_cli_view", payload=current)
        candidate = prepare_context_delivery(value, scope=scope, capture_id="entry-b", surface="quota_packet",
            baseline=full, retained={"complete": True, "target": full["target"]})
        # Even without native delta, the entry has one immutable reader rather
        # than a second planning body. A changed dependency changes that reader's
        # source hash, so retention cannot silently reuse the preceding facts.
        assert "core_goal" not in value and "planning_horizon" not in value
        _, review, _ = _views(current)
        assert review["core_goal"]["objective"] == current["autonomous_replan_obligation"]["replan_context"]["core_goal"]["objective"]
        if changed:
            assert value["replan_action_packet"]["review"] != entry["replan_action_packet"]["review"]
            assert review["planning_horizon"]["work_items"][0]["successor_todo_ids"] == ["todo-new-dependency"]
        recovery = prepare_context_delivery(value, scope=scope, capture_id="entry-b", surface="quota_packet")
        assert restore_context_delivery(candidate, baseline=full, current=recovery)["context"] == value
        assert restore_context_delivery(candidate, baseline=None, current=recovery)["context"] == value
        reset = prepare_context_delivery(value, scope={**scope, "context_epoch": "two"}, capture_id="entry-b",
            surface="quota_packet", baseline=full, retained={"complete": True, "target": full["target"]})
        assert reset["mode"] == "full" and reset["context"] == value
    # Feature absent, settlement-only and a higher-priority governed intent do
    # not enter the staged replan lens merely because an old packet is present.
    for patch in ({"replan_read_path": None}, {"ok": False},
                  {"effective_action": "governed_capability_intent"},
                  {"replan_action_packet": {**source["replan_action_packet"], "settlement_only": True}}):
        legacy = {**source, **patch}
        assert _turn_envelope_result("replan_cli_view", payload=legacy) == legacy


def test_native_reader_keeps_its_installation_across_acceptance_and_writeback(tmp_path, monkeypatch):
    from loopx.command_invocation import current_loopx_cli_argv
    from loopx.control_plane.quota.turn_envelope import bind_turn_decision_capture
    from loopx.control_plane.turn_driver.decision_capture import write_decision_capture
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt

    source = _quota_payload()
    outcome = {"id": "original-outcome", "objective": "Deliver the original accepted artifact.",
        "requirements": [{"id": "original-stop", "description": "Stop before publication."}]}
    source["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract"] = {
        "outcome_requirements": {"revision": 1, "outcomes": [outcome]}}
    capture = tmp_path / "native.json"
    write_decision_capture(capture, source)
    original = capture.read_bytes()
    envelope = build_turn_envelope(source)
    args = {"captured_decision_path": str(capture), "turn_instance_id": "original-native-turn", "turn_key": "sha256:" + "0" * 64}
    legacy = bind_turn_decision_capture(source, envelope, **args)
    assert "read autonomous_replan_obligation.replan_context" in render_prompt(extract_turn_authority({"turn_envelope": legacy}))
    bound = bind_turn_decision_capture(source, envelope, **args, reader_argv=current_loopx_cli_argv())
    authority = extract_turn_authority({"turn_envelope": bound})
    trap = tmp_path / "bin"
    trap.mkdir()
    (trap / "loopx").write_text("#!/bin/sh\nexit 73\n")
    (trap / "loopx").chmod(0o700)
    monkeypatch.setenv("PATH", str(trap) + os.pathsep + os.environ["PATH"])
    def read(command):
        result = subprocess.run(shlex.split(command), cwd=tmp_path, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    review = read(authority["replan_action_packet"]["review"])
    index = review["acceptance_index"]
    acceptance = read(index["read_action_template"].replace("{outcome_ref}", index["outcomes"][0]["outcome_ref"]))
    assert acceptance["outcome"] == outcome
    assert read(review["next"]["writeback"])["context_id"] == review["context_id"]
    assert bind_turn_decision_capture(source, bound, **args)["detail_ref"] == bound["detail_ref"]
    assert bound["action_signature"] == envelope["action_signature"]
    assert capture.read_bytes() == original and not (tmp_path / "registry.json").exists()


def test_explore_uses_its_own_incremental_budget_and_real_result_log(tmp_path):
    from replan_packet_fixture import populated_explore_context, populated_replan_source

    explore = populated_explore_context(tmp_path)
    assert explore["ok"] and explore["graph_enabled"]
    base = populated_replan_source(tmp_path / "base")
    enabled = populated_replan_source(tmp_path / "enabled", explore_context=explore)
    base_envelope, base_review, _ = _views(base)
    envelope, review, params = _views(enabled)
    assert "capabilities" not in base_review
    # Optional capability context must not inflate the shared hot packet.
    assert json_bytes(envelope["replan_action_packet"]) - json_bytes(base_envelope["replan_action_packet"]) <= 128
    assert 0 < json_bytes(review) - json_bytes(base_review) <= 768
    detail = _turn_envelope_result("replan_view", **params, stage="explore")
    basis = enabled["autonomous_replan_obligation"]["replan_context"]["explore_basis"]
    assert detail["explore_basis"] == basis
    assert basis["availability"] == "available" and basis["finding_refs"]
    assert basis["result_page"]["total"] > len(basis["finding_refs"])
    assert json_bytes(detail) <= 8 * 1024
    # Execute the capability reader through the shipped CLI as well as its TS owner.
    captured = tmp_path / "explore-decision.json"
    enabled["replan_read_path"] = str(captured)
    captured.write_text(json.dumps(enabled))
    _, cli_review, _ = _views(enabled)
    command = shlex.split(cli_review["capabilities"]["explore"]["read_action"])
    reader_fixture = {"registry": tmp_path / "absent.json", "runtime": tmp_path / "absent-runtime", "project": tmp_path}
    cli_detail = call_progress_cli(reader_fixture, *command[3:])
    assert cli_detail["explore_basis"] == basis
    assert {row["status"] for row in basis["finding_refs"]} >= {"confirmed", "refuted"}
    _, _, params = _views(enabled)
    writer = _turn_envelope_result("replan_view", **params, stage="writeback")
    assert writer["stride_control"]["reference_context_id"] == review["context_id"]
    assert json_bytes(writer) <= 16 * 1024
