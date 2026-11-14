"""A restored fork needs a current, settled semantic decision before recapture."""
import copy
import hashlib
import json

import pytest

from loopx.control_plane.effect_program import SettlementIdentity
from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError, effect_runtime_result


def test_nonrestored_conversation_has_no_completed_checkpoint_review(tmp_path):
    from benchmark.runtime.checkpoint_continuation import restored_capture_gate, restored_checkpoint_review
    env = {"LOOPX_RUNTIME_ROOT": str(tmp_path), "LOOPX_GOAL_ID": "destination", "LOOPX_AGENT_ID": "worker"}
    assert restored_capture_gate(env)["capture_allowed"]
    review = restored_checkpoint_review(env)
    assert review["semantic_review_accepted"] is False and "capture_allowed" not in review


@pytest.mark.parametrize("fault", [None, "pending", "neutral", "source_actor", "unlinked", "same_node", "wrong_binding"])
def test_restored_capture_requires_current_consumption_not_local_dedup(fault):
    sha = "a" * 64
    identity = SettlementIdentity("destination", "researcher", "todo_probe", "decision-1").as_dict()
    obligation = "replan-" + "1" * 16
    review = {"identity": identity, "progress": {"state": "settled"},
        "semantic_replan_guard": {"schema_version": "semantic_replan_guard_v0", "selected_obligation_id": obligation},
        "writeback_run": {"goal_id": "destination", "agent_id": "researcher", "todo_id": "todo_probe",
            "turn_instance_id": "decision-1", "autonomous_replan_ack": {
                "schema_version": "autonomous_replan_ack_v0", "recorded": True,
                "semantic_delta": {"schema_version": "replan_semantic_delta_v0", "accepted": True,
                    "obligation_id": obligation}},
            "agent_vision": {"path_delta": {"evidence_refs": ["checkpoint:" + sha]}}}}
    current = "replan-" + "2" * 16
    if fault == "pending":
        review["progress"]["state"] = "settlement_pending"
    elif fault == "neutral":
        review["writeback_run"]["autonomous_replan_ack"]["semantic_delta"]["accepted"] = False
    elif fault == "source_actor":
        review["writeback_run"]["goal_id"] = "source"
    elif fault == "unlinked":
        review["writeback_run"]["agent_vision"]["path_delta"]["evidence_refs"] = ["checkpoint:" + "b" * 64]
    elif fault == "same_node":
        current = obligation
    elif fault == "wrong_binding":
        review["writeback_run"]["todo_id"] = "todo_other"
    request = {"action": "restore_capture", "checkpoint_sha256": sha,
        "goal_id": "destination", "agent_id": "researcher", "current_obligation_id": current,
        "review": review}
    result = effect_runtime_result("runtime.checkpoint_artifacts", request)
    assert result["capture_allowed"] is (fault is None)
    assert result["execution_authority_granted"] is False
    assert "review_basis" not in result
    detailed = effect_runtime_result("runtime.checkpoint_artifacts", {**request, "include_review_basis": True})
    assert ("review_basis" in detailed) is (fault is None)
    if fault is None:
        assert detailed["review_basis"]["identity"] == identity
        assert detailed["review_basis"]["completed_decision"]["agent_vision"] == review["writeback_run"]["agent_vision"]
    # A fresh Goal/Agent cannot inherit even a valid destination review. This
    # is the outer restore-every-point counterexample to local-ID dedup.
    another = copy.deepcopy(request)
    another.update(goal_id="another-destination", agent_id="another-researcher")
    assert not effect_runtime_result("runtime.checkpoint_artifacts", another)["capture_allowed"]


def test_readonly_participant_capture_and_limited_basis_read(tmp_path):
    source = tmp_path / "code"
    source.write_text("retained evidence")
    source.chmod(0o444)
    components = [{"id": "code", "source_path": str(source)}]
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    unit = tmp_path / "point"
    captured = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture", "components": components,
        "destination": str(unit), "point_metadata": {}, "expected_source_sha256": probe["source_sha256"]}, retry_safe=False)
    copied = unit / "components" / "code"
    assert copied.read_text() == source.read_text() and copied.stat().st_mode & 0o777 == 0o444
    copied.chmod(0o644)
    copied.write_text("changed member")
    # Digest-bound declaration readback is explicitly weaker than full member
    # verification. Native admission must continue using the latter.
    basis = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "read_basis", "root": str(unit),
        "expected_sha256": captured["sha256"]})
    assert basis["integrity_verified"] is False and basis["execution_authority_granted"] is False
    with pytest.raises(EffectRuntimeRemoteError):
        effect_runtime_result("runtime.checkpoint_artifacts", {"action": "verify", "root": str(unit),
            "expected_sha256": captured["sha256"]})


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_sealed_pending_basis_reuses_original_guard_and_rejects_replacements(tmp_path, monkeypatch, provider):
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision
    from benchmark.runtime.checkpoint_continuation import restored_checkpoint_review
    from benchmark.runtime.checkpoint_session_refresh import prepare_session_refresh
    from types import SimpleNamespace
    from test_replan_successor_guard_reentry import AGENT, GOAL, TURN, _fixture

    call, runtime, _ = _fixture(tmp_path, monkeypatch, provider)
    original = call("quota", "should-run", "--runtime-profile", "generic_cli", "--goal-id", GOAL,
        "--agent-id", AGENT, "--turn-instance-id", TURN,
        "--decision-output-dir", str(tmp_path / "original"))
    assert original["effective_action"] == "autonomous_replan_required"
    retained = tmp_path / "original" / "decision.json"
    source_bytes = retained.read_bytes()
    source = json.loads(source_bytes)
    identity = source["heartbeat_receipt"]["settlement_identity"]
    runs = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    original_runs = runs.read_bytes()
    pending = {"capture": True, "obligation_id": identity["replan_obligation_id"],
               "settlement_effect_id": identity["effect_id"], "execution_authority_granted": False}
    complete = compose_turn_decision({**source, "future_observation": {"exact": 2**53 + 1, "decimal": 1.0}})
    packet = tmp_path / "held-decision.json"
    packet.touch(mode=0o600)
    evidence = [{"id": "held_decision", "source_path": str(packet)}]

    def seal(name, payload, *, wanted=pending, decision_digest=None):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        packet.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        request = {"action": "capture", "components": evidence, "destination": str(tmp_path / name),
            "point_metadata": {"held_decision_component_id": "held_decision", "loopx_state": {
                "goal_id": GOAL, "agent_id": AGENT}, "replan": wanted, "decision_sha256": decision_digest or digest},
            "expected_source_sha256": effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "probe", "components": evidence})["source_sha256"]}
        sealed = effect_runtime_result("runtime.checkpoint_artifacts", request, retry_safe=False)
        return {"action": "read_basis", "root": request["destination"], "expected_sha256": sealed["sha256"],
            "include_pending_replan": True}, digest

    for name, payload in (("historical", source), ("complete", complete)):
        request, digest = seal(name, payload)
        selected = effect_runtime_result("runtime.checkpoint_artifacts", request)
        assert selected["pending_replan"] == {"intent": pending, "decision_sha256": digest,
            "decision_path": str(tmp_path / name / "components" / "held_decision")}
        assert not selected["integrity_verified"] and not selected["execution_authority_granted"]

        # Exercise both actual receivers with a sealed source and an already
        # admitted native binding. Only the external native transport is fake.
        binding = {"session_id": "synthetic-session", "checkpoint_artifact_sha256": request["expected_sha256"],
                   "checkpoint_artifact_root": request["root"]}
        declaration = tmp_path / "warm-start.json"
        declaration.write_text(json.dumps({"root": request["root"], "sha256": request["expected_sha256"],
            "goal_id": GOAL, "agent_id": AGENT, "fresh_replan": True}))
        partial = tmp_path / "context-refresh" / "pre-replan.json"
        partial.parent.mkdir(exist_ok=True)
        partial.touch()  # Recovery of a lost external reply must not re-admit.
        env = {"LOOPX_RUNTIME_ROOT": str(runtime), "LOOPX_GOAL_ID": GOAL, "LOOPX_AGENT_ID": AGENT,
               "LOOPX_WARM_START": str(declaration), "LOOPX_ITERATION_CONTEXT": "resume",
               "LOOPX_WAKE_LOG_DIR": str(tmp_path / "wakes")}
        observations = []
        session = SimpleNamespace(**binding, binding=binding, prepare_checkpoint_refresh=lambda *args, **kw:
                                 observations.append((args, kw)))
        with monkeypatch.context() as m:
            m.setattr("benchmark.runtime.checkpoint_continuation.load_codex_cli_session", lambda *a, **kw: binding)
            m.setattr("benchmark.runtime.sessions.BenchmarkSessionWake", lambda *a: session)
            notice = prepare_session_refresh(env, session, tmp_path)
            assert "BEFORE a pending replan" in notice and observations == [(
                (None, partial, tmp_path / "stdout.jsonl"),
                {"pre_replan_basis": {"pending_replan": pending, "decision_sha256": digest}},
            )]
            review = restored_checkpoint_review(env)
            assert not review["semantic_review_accepted"]
            assert selected["pending_replan"]["decision_path"] in review["instructions"]

    for name, payload, wanted, expected in (
        ("tampered", {**complete, "future_observation": {"exact": 2**53}}, pending, "integrity mismatch"),
        ("unknown", {**complete, "schema_version": "unknown"}, pending, "unsupported complete Turn decision version"),
        ("wrong-intent", source, {**pending, "obligation_id": "replan-" + "b" * 16}, "binding differs"),
        ("missing-intent", source, None, "checkpoint pending replan must be an object"),
    ):
        request, _ = seal(name, payload, wanted=wanted)
        with pytest.raises(EffectRuntimeRemoteError, match=expected):
            effect_runtime_result("runtime.checkpoint_artifacts", request)

    request, _ = seal("wrong-member", source, decision_digest="a" * 64)
    with pytest.raises(EffectRuntimeRemoteError, match="packet digest differs"):
        effect_runtime_result("runtime.checkpoint_artifacts", request)
    request, _ = seal("replaced-member", source)
    member = tmp_path / "replaced-member" / "components" / "held_decision"
    saved = member.read_bytes()
    member.write_bytes(b" " + saved[1:])  # Same size as the sealed file.
    with pytest.raises(EffectRuntimeRemoteError, match="packet digest differs"):
        effect_runtime_result("runtime.checkpoint_artifacts", request)
    assert retained.read_bytes() == source_bytes
    assert runs.read_bytes() == original_runs


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("cited", [False, True])
def test_successor_review_needs_checkpoint_linked_settlement(tmp_path, monkeypatch, provider, cited):
    """A legal successor can close out without losing the recovery-specific review.

    Exercise real CLI authoring, canonical storage and settlement. The native
    conversation completion check is a separate Host gate, not established here.
    """
    import json
    import shlex
    import subprocess

    from test_replan_successor_guard_reentry import _fixture, GOAL, AGENT, TURN
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement

    call, runtime, _ = _fixture(tmp_path, monkeypatch, provider)
    original = call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
        "--agent-id", AGENT, "--turn-instance-id", TURN,
        "--codex-app-current-rrule", "FREQ=MINUTELY;INTERVAL=3",
        "--decision-output-dir", str(tmp_path / "decision"))
    read = subprocess.run(shlex.split(original["replan_action_packet"]["writeback"]),
        capture_output=True, text=True, check=True)
    writer = json.loads(read.stdout)
    successor_binding = shlex.split(writer["writeback_contract"]["successor_binding_args"])
    obligation = original["heartbeat_receipt"]["settlement_identity"]["replan_obligation_id"]
    before_todos = call("todo", "list", "--goal-id", GOAL)["todos"]
    rejected = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Must not persist without the original Turn", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "rejected-source",
        "--replan-obligation-id", obligation, "--turn-instance-id", "unadmitted-turn", expected_code=1)
    assert rejected["ok"] is False
    assert call("todo", "list", "--goal-id", GOAL)["todos"] == before_todos
    successor = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently verify the source artifact", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "independent-source-artifact",
        "--replan-obligation-id", obligation, *successor_binding)
    assert "host_action" not in successor  # No premature stop before original settlement.
    assert successor["settlement_identity"] == original["heartbeat_receipt"]["settlement_identity"]
    # Follow only the returned receipt; no fresh guard or successor admission.
    steps = successor["settlement_plan"]["ordered_steps"]
    actions = [step["command_template"] for step in steps if step["kind"] in {"durable_writeback", "quota_spend"}]
    from loopx.control_plane.todos.markdown import render_todo_markdown
    rendered = render_todo_markdown(successor)
    assert successor["instruction"] in rendered
    assert all(command in rendered for command in actions)
    actions[0] = actions[0].replace("<validated_progress>", "source_review").replace("<scale>", "single_surface").replace("<outcome>", "outcome_progress")
    sha = "a" * 64

    def capture():
        observed = read_heartbeat_settlement(runtime, goal_id=GOAL, agent_id=AGENT,
            todo_id=None, turn_instance_id=TURN, resolve_original_binding=True,
            registry_path=tmp_path / "registry.json")
        return effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "restore_capture", "checkpoint_sha256": sha,
            "goal_id": GOAL, "agent_id": AGENT, "current_obligation_id": None,
            "review": {"identity": observed.identity.value.as_dict(),
                "progress": observed.progress, "writeback_run": observed.writeback_run,
                "semantic_replan_guard": observed.semantic_replan_guard}})

    assert capture()["capture_allowed"] is False
    packet = {"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Retained checkpoint evidence supports the next source audit."},
        "path_delta": {"outcome": "continue", "prior_assumption": "The retained source needs review.",
            "observed_reality": "Reviewed source evidence supports the independently validated successor.",
            "retained": ["Original acceptance and remaining evaluation opportunity"],
            "evidence_refs": ["checkpoint:" + sha if cited else "evidence:source-review"]}}
    vision = tmp_path / "checkpoint-review.json"
    vision.write_text(json.dumps(packet))
    refresh_args = (*shlex.split(actions[0])[1:], "--agent-vision-json", str(vision),
                    "--no-global-sync", "--suppress-external-sinks")
    if cited:
        settled = call(*refresh_args, "--settle-turn")
        assert settled["settlement_progress"]["state"] == "settled"
    else:
        call(*refresh_args)
        assert capture()["capture_allowed"] is False  # Writeback alone is not settled.
        call(*shlex.split(actions[1])[1:])
    assert capture()["capture_allowed"] is cited
    # Retrying the original debit repairs/replays its receipt, never starts the successor.
    call(*shlex.split(actions[1])[1:])
    assert capture()["capture_allowed"] is cited
    assert call("todo", "list", "--goal-id", GOAL,
                "--todo-id", successor["todo_id"])["todo"]["status"] == "open"
