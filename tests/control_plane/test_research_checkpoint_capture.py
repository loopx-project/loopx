"""Inert checkpoint capture reuses the real settled-Turn source owner."""
from pathlib import Path
import json
import hashlib
import os
import pytest
import time

import test_quota_settlement_cli as cli
from canonical_authority_fixture import isolate_sqlite_runtime

from benchmark.runtime.checkpoint import capture_after_wake, capture_before_replan
from loopx.control_plane.effect_runtime import effect_runtime_result


def test_feature_off_and_failed_wakes_do_not_read_request_or_settlement(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("feature-off/wake-failure must perform no IO")
    monkeypatch.setattr("benchmark.runtime.checkpoint.read_heartbeat_settlement", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    receipt = {"ok": True, "stage": "execute", "mode": "heartbeat"}
    capture_after_wake({}, receipt)
    assert receipt == {"ok": True, "stage": "execute", "mode": "heartbeat"}
    assert capture_before_replan({}, receipt, "original body") == "original body"
    failed = {"ok": False, "stage": "execute", "mode": "heartbeat"}
    capture_after_wake({"LOOPX_CHECKPOINT_REQUEST": "not-readable"}, failed)
    assert failed["ok"] is False
    assert failed["checkpoint"]["captured"] is False


def test_closed_budget_never_reads_the_request_or_replays_work(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("closed budget must not read sources")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    receipt = {"ok": True, "stage": "execute", "mode": "heartbeat"}
    capture_after_wake({"LOOPX_CHECKPOINT_REQUEST": "not-readable",
                        "LOOPX_PHASE_DEADLINE_EPOCH": str(time.time() - 1)}, receipt)
    assert receipt["ok"] is True
    assert receipt["checkpoint"]["reason"] == "capture_window_closed"
    capture_after_wake({"LOOPX_CHECKPOINT_REQUEST": "not-readable",
                        "LOOPX_PHASE_DEADLINE_EPOCH": "nan"}, receipt)
    assert receipt["ok"] is True
    assert receipt["checkpoint"]["reason"] == "capture_failed"


@pytest.mark.parametrize("trigger", [None, "fresh_replan"])
@pytest.mark.parametrize("failure", ["missing", "denied", "malformed"])
def test_worker_keeps_work_running_when_staged_capture_declaration_fails(
    tmp_path, monkeypatch, trigger, failure,
):
    """Real worker/session/guard; synthetic executable replaces only the model."""
    import os
    import sys
    from benchmark.runtime.worker import run_once

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = cli._write_fixture(tmp_path)
    if trigger:
        cli._configure_autonomous_replan_fixture(project, runtime, registry)
    skills = tmp_path / "skills"
    skills.mkdir()
    binary = tmp_path / "codex"
    binary.write_text(f"#!{sys.executable}\n" +
        "import json,pathlib,sys\n"
        "body=sys.stdin.read()\n"
        "pathlib.Path('host-input.txt').write_text(body)\n"
        "pathlib.Path('solution.py').write_text('after_decision = 11\\n')\n"
        "print(json.dumps({'type':'thread.started','thread_id':'synthetic-capture-session'}))\n")
    binary.chmod(0o755)
    logs = tmp_path / "logs"
    request = tmp_path / "request.json"
    declaration = json.dumps({"destination": str(logs / "checkpoints" / "point"),
        "components": [{"id": "code", "source_path": str(project / "solution.py")}],
        "point_metadata": {"purpose": "synthetic worker observer failure"},
        **({"trigger": trigger, "destination": str(logs / "checkpoints")} if trigger else {})})
    request.write_text(declaration)
    env = {**os.environ, "LOOPX_PROJECT": str(project), "LOOPX_CLI": str(Path(sys.executable).parent / "loopx"),
        "LOOPX_CODEX_HOME": str(tmp_path / "home"), "LOOPX_WAKE_LOG_DIR": str(logs / "wakes"),
        "LOOPX_SHARED_SKILLS": str(skills), "CODEX_BIN": str(binary),
        "MODEL_NAME": "fixture", "REASONING_EFFORT": "high", "OPENAI_BASE_URL": "http://localhost:8123/v1",
        "OPENAI_API_KEY": "fixture-key", "LOOPX_RUNTIME_ROOT": str(runtime),
        "LOOPX_REGISTRY": str(registry), "LOOPX_GOAL_ID": cli.GOAL_ID, "LOOPX_AGENT_ID": cli.AGENT_ID}
    monkeypatch.setattr("benchmark.runtime.worker.heartbeat_body", lambda *a, **kw: "original work contract")
    off = run_once(env)
    assert off["ok"] and "checkpoint" not in off
    (project / "host-input.txt").unlink()
    if failure == "missing":
        request.unlink()
    elif failure == "malformed":
        request.write_text("{")
    read_bytes = Path.read_bytes
    with monkeypatch.context() as faults:
        if failure == "denied":
            def denied_read(path):
                if path == request:
                    raise PermissionError("synthetic staged-file permission loss")
                return read_bytes(path)
            faults.setattr(Path, "read_bytes", denied_read)
        failed = run_once({**env, "LOOPX_CHECKPOINT_REQUEST": str(request)})
    assert failed["ok"] and failed["session"]["session_id"] == "synthetic-capture-session"
    assert (project / "host-input.txt").read_text().endswith("original work contract")
    assert failed["checkpoint"]["reason"] == "capture_failed"
    assert not failed["checkpoint"]["captured"]
    saved = json.loads((logs / "wakes" / failed["turn_id"] / "receipt.json").read_text())
    assert saved == failed
    request.write_text(declaration)
    if trigger:
        # A repaired declaration cannot label the model's later code as the
        # original missed pre-decision point, even on exact guard replay.
        replay = {"turn_id": failed["turn_id"], "mode": "heartbeat", "stage": "execute", "ok": False}
        body = capture_before_replan({**env, "LOOPX_CHECKPOINT_REQUEST": str(request)}, replay,
                                     "original work contract")
        assert replay["checkpoint"]["reason"] == "pre_decision_capture_already_attempted", replay
        assert body.endswith("original work contract")
        assert not list((logs / "checkpoints").glob("replan-*/checkpoint.json"))
    recovered = run_once({**env, "LOOPX_CHECKPOINT_REQUEST": str(request)})
    assert recovered["ok"]
    if trigger:
        # An unavailable declaration is optional; an unavailable original
        # admission owner is not. This boundary must still stop before work.
        import subprocess
        (project / "host-input.txt").unlink()
        request.unlink()
        registry.rename(registry.with_suffix(".unavailable"))
        with pytest.raises(subprocess.CalledProcessError):
            run_once({**env, "LOOPX_CHECKPOINT_REQUEST": str(request)})
        assert not (project / "host-input.txt").exists()


@pytest.mark.parametrize("opportunity,corrupt,collisions", [
    (False, False, False), (True, False, False), (True, True, False),
    (True, False, True), (True, True, True), ("empty", False, True),
])
def test_real_cli_settlement_capture_verification_and_inert_materialization(
        tmp_path, monkeypatch, opportunity, corrupt, collisions):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = cli._write_fixture(tmp_path)
    binding = ("--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID, "--turn-instance-id", cli.TURN_ID)
    def run(*args):
        code, result = cli._run_cli(registry, runtime, *args)
        assert code == 0, result
        return result
    run("quota", "should-run", "--goal-id", cli.GOAL_ID, "--codex-app", *binding)
    logs = tmp_path / "logs"
    wake = logs / "wakes"
    wake.mkdir(parents=True)
    (wake / cli.TURN_ID).mkdir()
    observed = []
    incumbent_bytes = b"opaque older adopted candidate"
    opportunity_packet = {"meaning": "synthetic opportunity participant; no admission", "native_incumbent": {
        "round": "agent-1", "archive_sha256": hashlib.sha256(incumbent_bytes).hexdigest(), "archive_bytes": len(incumbent_bytes)}}
    if opportunity == "empty":
        opportunity_packet["native_incumbent"] = None
    def export(env, *, timeout, incumbent_path):
        assert 0 < timeout <= 30
        observed.append(True)
        if opportunity_packet["native_incumbent"] is not None:
            incumbent_path.write_bytes(incumbent_bytes)
        return opportunity_packet
    monkeypatch.setattr("benchmark.runtime.sforge_checkpoint.capture_opportunity", export)
    if corrupt:
        def altered_probe(method, params, **kwargs):
            if params["action"] == "probe":
                next(wake.glob("*/checkpoint-incumbent.tar.gz")).write_bytes(b"altered after transport")
            return effect_runtime_result(method, params, **kwargs)
        monkeypatch.setattr("benchmark.runtime.checkpoint.effect_runtime_result", altered_probe)
    (project / "solution.py").write_text("retained_candidate = 7\n")
    request = tmp_path / "request.json"
    target = logs / "checkpoints" / "point"
    caller_components = []
    if collisions:
        for name in ("evaluation_opportunity", "evaluation_opportunity_1", "evaluation_incumbent", "evaluation_incumbent_1"):
            member = project / name
            member.write_text("caller-owned " + name)
            caller_components.append({"id": name, "source_path": str(member)})
    request.write_text(json.dumps({"destination": str(target), "components": [
        {"id": "code", "source_path": str(project / "solution.py")},
        {"id": "research", "source_path": str(project / f".codex/goals/{cli.GOAL_ID}/ACTIVE_GOAL_STATE.md")},
        *caller_components,
    ], "point_metadata": {"meaning": "synthetic capture; no model/session or evaluator qualification"},
        **({"evaluation_opportunity": True} if opportunity else {})}))
    env = {"LOOPX_CHECKPOINT_REQUEST": str(request), "LOOPX_WAKE_LOG_DIR": str(wake),
        "LOOPX_RUNTIME_ROOT": str(runtime), "LOOPX_GOAL_ID": cli.GOAL_ID,
        "LOOPX_AGENT_ID": cli.AGENT_ID, "LOOPX_REGISTRY": str(registry)}
    receipt = {"ok": True, "stage": "execute", "mode": "heartbeat", "turn_id": cli.TURN_ID}
    capture_after_wake(env, receipt)
    assert receipt["checkpoint"]["reason"] == "owned_turn_not_settled"
    assert not target.exists()
    assert observed == []
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Synthetic source fixture checked; checkpoint work remains."},
        "path_delta": {"outcome": "continue", "prior_assumption": "Fixture source is available.",
            "observed_reality": "The isolated source fixture was verified.", "retained": ["Inert checkpoint qualification"],
            "evidence_refs": ["evidence:synthetic-capture"]}}))
    run("refresh-state", "--goal-id", cli.GOAL_ID, *binding, "--classification", "checkpoint_fixture_verified",
        "--delivery-batch-scale", "multi_surface", "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision))
    run("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *binding, "--source", "heartbeat", "--execute")
    goal_state = project / f".codex/goals/{cli.GOAL_ID}/ACTIVE_GOAL_STATE.md"
    retained_goal = goal_state.read_bytes()
    capture_after_wake(env, receipt)
    assert receipt["ok"] is True
    if corrupt:
        assert receipt["checkpoint"]["captured"] is False and not target.exists()
        assert "differs" in receipt["checkpoint"]["error"]
        assert goal_state.read_bytes() == retained_goal
        return
    assert receipt["checkpoint"].get("captured") is True, receipt
    assert receipt["checkpoint"]["research_point_qualified"] is False
    assert goal_state.read_bytes() == retained_goal
    if opportunity:
        mapping = json.loads((target / "checkpoint.json").read_text())["point_metadata"]["evaluation_opportunity"]
        assert json.loads((target / "components" / mapping["component_id"]).read_text()) == opportunity_packet
        if opportunity == "empty":
            assert mapping["incumbent_component_id"] is None
            assert not (wake / cli.TURN_ID / "checkpoint-incumbent.tar.gz").exists()
        else:
            assert (target / "components" / mapping["incumbent_component_id"]).read_bytes() == incumbent_bytes
        for member in caller_components:
            assert (target / "components" / member["id"]).read_text() == "caller-owned " + member["id"]

    digest = receipt["checkpoint"]["sha256"]
    restored = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "materialize", "root": str(target),
        "expected_sha256": digest, "destination": str(tmp_path / "inert")})
    assert restored["activation_performed"] is False
    assert (tmp_path / "inert/components/code").read_text() == "retained_candidate = 7\n"
    if opportunity:
        if opportunity != "empty":
            assert (tmp_path / "inert/components" / mapping["incumbent_component_id"]).read_bytes() == incumbent_bytes
        for member in caller_components:
            assert (tmp_path / "inert/components" / member["id"]).read_text() == "caller-owned " + member["id"]
    before = (target / "checkpoint.json").read_bytes()
    capture_after_wake(env, receipt)
    assert receipt["checkpoint"]["reason"] == "destination_already_present_no_replay"
    assert (target / "checkpoint.json").read_bytes() == before
    assert goal_state.read_bytes() == retained_goal
    assert len(observed) == int(bool(opportunity))


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
                    reason="Requires an explicit real native Host binary; static local provider only")
@pytest.mark.parametrize("source_moves", [False, True, "binding"])
@pytest.mark.parametrize("mutation_stage", ["probe", "capture"])
def test_real_native_cursor_must_match_sealed_store_basis(tmp_path, monkeypatch, source_moves, mutation_stage,
                                                       caller_id="code"):
    """Actual CLI settlement, native completed Turns and TS SQLite artifacts."""
    from tests.control_plane.codex_checkpoint_fixture import real_native_source
    from benchmark.runtime.codex import Execution
    from benchmark.runtime.sessions import BenchmarkSessionWake
    from loopx.control_plane.turn_driver.codex_sessions import _store_codex_cli_session

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = cli._write_fixture(tmp_path)
    binding = ("--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID, "--turn-instance-id", cli.TURN_ID)
    def run(*args):
        code, result = cli._run_cli(registry, runtime, *args, cwd=project)
        assert code == 0, result
    run("quota", "should-run", "--goal-id", cli.GOAL_ID, "--codex-app", *binding)
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Synthetic source boundary; full continuation remains open."},
        "path_delta": {"outcome": "continue", "prior_assumption": "Declared source is available.",
            "observed_reality": "The isolated fixture source is checked.", "retained": ["Original capture acceptance"],
            "evidence_refs": ["evidence:native-source-fixture"]}}))
    run("refresh-state", "--goal-id", cli.GOAL_ID, *binding, "--classification", "native_fixture_verified",
        "--delivery-batch-scale", "multi_surface", "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision))
    run("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *binding, "--source", "heartbeat", "--execute")
    logs = tmp_path / "logs"
    wake = logs / "wakes" / cli.TURN_ID
    wake.mkdir(parents=True)
    destination = logs / "checkpoints/point"
    destination.parent.mkdir()
    candidate = project / "solution.py"
    candidate.write_text("source_candidate = 7\n")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"destination": str(destination), "components": [
        {"id": caller_id, "source_path": str(candidate)}], "native_session": True,
        "point_metadata": {"meaning": "Native/CLI/TS fixture, not research or model utility"}}))
    with real_native_source(tmp_path / "native", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"], project) as (env, source, complete, calls):
        env.update(LOOPX_CHECKPOINT_REQUEST=str(request), LOOPX_WAKE_LOG_DIR=str(logs / "wakes"),
            LOOPX_RUNTIME_ROOT=str(runtime), LOOPX_REGISTRY=str(registry), LOOPX_GOAL_ID=cli.GOAL_ID,
            LOOPX_AGENT_ID=cli.AGENT_ID, MODEL_NAME="fixture", REASONING_EFFORT="high",
            LOOPX_CODEX_SANDBOX="read-only")
        source_wake = BenchmarkSessionWake(env, Execution(context="resume", sandbox="read-only"), {})
        source_wake.admission.accept_result(lambda: _store_codex_cli_session(runtime,
            lineage={"goal_id": cli.GOAL_ID, "agent_id": cli.AGENT_ID}, session_scope="agent", session_id=source,
            goal_ref=source_wake.goal_ref, session_profile_digest=source_wake.digest))
        receipt = {"ok": True, "stage": "execute", "mode": "heartbeat", "turn_id": cli.TURN_ID,
                   "session": {"session_id": source}}
        from contextlib import contextmanager
        from loopx.hosts.codex_checkpoint import native_checkpoint_reader
        inspections = []
        @contextmanager
        def observed_reader(*args, **kwargs):
            with native_checkpoint_reader(*args, **kwargs) as reader:
                inspections.append(reader)
                yield reader
        monkeypatch.setattr("loopx.hosts.codex_checkpoint.native_checkpoint_reader", observed_reader)
        injected = []
        def between_read_and_probe(method, params, **kwargs):
            if source_moves and params["action"] == mutation_stage and not injected:
                if source_moves == "binding":
                    injected.append(True)
                    _store_codex_cli_session(runtime, lineage={"goal_id": cli.GOAL_ID, "agent_id": cli.AGENT_ID},
                        session_scope="agent", session_id=source, goal_ref=source_wake.goal_ref,
                        session_profile_digest="b" * 64)
                else:
                    injected.append(complete())  # Native writer commits later history before the byte witness.
            return effect_runtime_result(method, params, **kwargs)
        monkeypatch.setattr("benchmark.runtime.checkpoint.effect_runtime_result", between_read_and_probe)
        capture_after_wake(env, receipt)
        # Both full observations use one owned reader, also on rejection. The
        # real later-Turn mutation above must remain visible through that reader.
        assert len(inspections) == 1
        assert inspections[0].process.poll() is not None
        assert receipt["ok"] and candidate.read_text() == "source_candidate = 7\n"
        assert len(calls) == (2 if source_moves is True else 1)  # Static local streams only; no implicit new work.
        if source_moves:
            assert not receipt["checkpoint"]["captured"], receipt
            assert not destination.exists()
            expected_error = ("checkpoint source moved" if mutation_stage == "capture" else
                              "profile changed" if source_moves == "binding" else "native")
            assert expected_error in receipt["checkpoint"]["error"]
        else:
            assert receipt["checkpoint"]["captured"], receipt
            manifest = json.loads((destination / "checkpoint.json").read_text())
            assert len(manifest["point_metadata"]["native_session"]["turn_ids"]) == 1
            assert (destination / "components" / caller_id).read_bytes() == candidate.read_bytes()
            assert receipt["checkpoint"]["research_point_qualified"] is False

    if not source_moves and caller_id.startswith("native_"):
        # The source app-server is now closed. Reconstruct through the actual
        # provider fork entry, retaining the original home for byte comparison.
        # Fixed paths are swapped in this fixture; this is not guest isolation.
        import shutil
        from loopx.hosts.codex_checkpoint import fork_native_checkpoint

        native = manifest["point_metadata"]["native_session"]
        assert all(member["id"] != caller_id for member in native["store_members"])
        config = tmp_path / "native-config.toml"
        home = Path(env["CODEX_HOME"])
        config.write_bytes((home / "config.toml").read_bytes())
        retained_home = tmp_path / "retained-native-home"
        shutil.move(home, retained_home)
        def source_bytes():
            return {p.relative_to(retained_home).as_posix(): p.read_bytes()
                    for p in retained_home.rglob("*") if p.is_file()}
        before = source_bytes()
        if caller_id == "native_sessions":
            import copy
            # Even a correctly sealed artifact cannot select undeclared bytes,
            # alias a participant, duplicate a destination or import credentials.
            for fault in ("undeclared", "duplicate_id", "duplicate_name", "credential"):
                bad_metadata = copy.deepcopy(manifest["point_metadata"])
                members = bad_metadata["native_session"]["store_members"]
                if fault == "undeclared":
                    members[0]["id"] = "not_declared"
                elif fault == "duplicate_id":
                    members[1]["id"] = members[0]["id"]
                elif fault == "duplicate_name":
                    members[1]["name"] = members[0]["name"]
                else:
                    members[0]["name"] = "auth.json"
                bad_unit = tmp_path / fault
                entries = [{"id": item["id"], "source_path": str(destination / "components" / item["id"])}
                           for item in manifest["components"]]
                witness = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": entries})
                bad = effect_runtime_result("runtime.checkpoint_artifacts", {
                    "action": "capture", "destination": str(bad_unit), "components": entries,
                    "point_metadata": bad_metadata, "expected_source_sha256": witness["source_sha256"]}, retry_safe=False)
                with pytest.raises(ValueError, match="member inventory is invalid"):
                    fork_native_checkpoint(env, bad_unit, bad["sha256"], config, timeout=30)
                assert not home.exists() and len(calls) == 1
        fork = fork_native_checkpoint(env, destination, receipt["checkpoint"]["sha256"], config, timeout=30)
        assert fork["history_verified"] and fork["session_id"] != source
        assert fork["visible_history_sha256"] == native["visible_history_sha256"]
        assert source_bytes() == before and len(calls) == 1
        assert (destination / "components" / caller_id).read_bytes() == candidate.read_bytes()


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Requires a real native Host")
@pytest.mark.parametrize("caller_id", ["native_state_5_sqlite", "native_sessions"])
@pytest.mark.parametrize("source_moves", [False, True])
def test_native_store_participants_preserve_caller_ids_and_source_witness(
        tmp_path, monkeypatch, caller_id, source_moves):
    test_real_native_cursor_must_match_sealed_store_basis(
        tmp_path, monkeypatch, source_moves, "probe", caller_id=caller_id)


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="Requires a real native Host")
def test_native_binding_participant_preserves_legal_caller_id(tmp_path, monkeypatch):
    test_real_native_cursor_must_match_sealed_store_basis(tmp_path, monkeypatch, False, "probe",
                                                        caller_id="source_session_binding")


@pytest.mark.parametrize("capture_fails", [False, True])
@pytest.mark.parametrize("selection_pending", [False, True])
def test_real_pre_replan_capture_is_before_decision_and_same_node_is_not_recaptured(
        tmp_path, monkeypatch, capture_fails, selection_pending):
    import os
    import sys

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = cli._write_fixture(tmp_path)
    binding = ("--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID, "--turn-instance-id", cli.TURN_ID)

    def run(*args):
        code, result = cli._run_cli(registry, runtime, *args)
        assert code == 0, result
        return result

    run("quota", "should-run", "--goal-id", cli.GOAL_ID, "--codex-app", *binding)
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Synthetic source fixture checked; capture work remains."},
        "path_delta": {"outcome": "continue", "prior_assumption": "Source fixture is available.",
            "observed_reality": "Isolated source fixture verified.", "retained": ["Capture qualification"],
            "evidence_refs": ["evidence:synthetic-pre-replan"]}}))
    run("refresh-state", "--goal-id", cli.GOAL_ID, *binding, "--classification", "pre_replan_fixture_verified",
        "--delivery-batch-scale", "multi_surface", "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision))
    run("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *binding, "--source", "heartbeat", "--execute")
    index = runtime / "goals" / cli.GOAL_ID / "runs/index.jsonl"
    original_settlement = index.read_bytes()
    cli._configure_autonomous_replan_fixture(project, runtime, registry)
    if selection_pending:
        # Keep executable work, as a continuing research worker does. The
        # original guard commits the semantic replan before choosing its Todo;
        # no settlement identity exists for the new Turn yet.
        state_path = project / f".codex/goals/{cli.GOAL_ID}/ACTIVE_GOAL_STATE.md"
        state_path.write_text(state_path.read_text().replace("## Agent Todo\n\n",
            "## Agent Todo\n\n- [ ] [P1] Continue the retained experiment.\n"
            f"  <!-- loopx:todo todo_id={cli.TODO_ID} status=open claimed_by={cli.AGENT_ID} -->\n"))
    # This helper supplies synthetic repeated observations; retain the real
    # source settlement history instead of replacing its authoritative readback.
    index.write_bytes(original_settlement + index.read_bytes())
    logs = tmp_path / "logs"
    wakes = logs / "wakes"
    prior = wakes / "wake-100-prior"
    prior.mkdir(parents=True)
    prior_receipt = {"turn_id": cli.TURN_ID, "mode": "heartbeat", "stage": "execute", "ok": True}
    (prior / "receipt.json").write_text(json.dumps(prior_receipt))
    solution = project / "solution.py"
    solution.write_text("retained_candidate = 7\n")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"trigger": "fresh_replan", "loopx_state": True, "destination": str(logs / "checkpoints"),
        "components": [{"id": "code", "source_path": str(solution)}],
        "point_metadata": {"purpose": "synthetic original settlement and replan ordering"}}))
    env = {**os.environ, "LOOPX_CLI": str(Path(sys.executable).parent / "loopx"),
        "LOOPX_PROJECT": str(project), "LOOPX_CHECKPOINT_REQUEST": str(request),
        "LOOPX_WAKE_LOG_DIR": str(wakes), "LOOPX_RUNTIME_ROOT": str(runtime),
        "LOOPX_GOAL_ID": cli.GOAL_ID, "LOOPX_AGENT_ID": cli.AGENT_ID, "LOOPX_REGISTRY": str(registry)}
    receipt = {"turn_id": "synthetic-replan-point", "mode": "heartbeat", "stage": "execute", "ok": False}
    (wakes / receipt["turn_id"]).mkdir()
    attempted = []
    if capture_fails:
        def fail_probe(method, params, **kwargs):
            if params["action"] == "probe":
                attempted.append(True)
                raise RuntimeError("synthetic capture failure")
            return effect_runtime_result(method, params, **kwargs)
        monkeypatch.setattr("benchmark.runtime.checkpoint.effect_runtime_result", fail_probe)
    body = capture_before_replan(env, receipt, "original instructions")
    guard = json.loads((wakes / receipt["turn_id"] / "checkpoint-start-guard.json").read_text())
    if selection_pending:
        assert guard["heartbeat_receipt"]["settlement_binding_owed"] is True
        assert "settlement_identity" not in guard["heartbeat_receipt"]
        assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    if capture_fails:
        assert receipt["checkpoint"]["reason"] == "capture_failed", receipt
        solution.write_text("later_decision = 11\n")
        again = {"turn_id": receipt["turn_id"], "mode": "heartbeat", "stage": "execute", "ok": False}
        capture_before_replan(env, again, "original instructions")
        assert again["checkpoint"]["reason"] == "pre_decision_capture_already_attempted", again
        assert attempted == [True] and not list((logs / "checkpoints").glob("replan-*/checkpoint.json"))
        assert body.endswith("original instructions") and receipt["ok"] is False
        return
    assert receipt["checkpoint"]["captured"] is True, json.dumps(receipt)
    target = Path(receipt["checkpoint"]["destination"])
    captured = (target / "checkpoint.json").read_bytes()
    assert (target / "components/code").read_text() == "retained_candidate = 7\n"
    assert body.endswith("original instructions") and "original quota guard once" in body
    assert receipt["ok"] is False  # Capturing cannot declare the new work completed.
    assert (prior / "receipt.json").read_text() == json.dumps(prior_receipt)
    # The model's subsequent decision/work must not replace the pre-decision point.
    solution.write_text("new_route = 11\n")
    capture_after_wake(env, receipt)
    assert (target / "checkpoint.json").read_bytes() == captured
    # Reentry on the exact original guard reads back one point, even if code moved.
    again = {"turn_id": receipt["turn_id"], "mode": "heartbeat", "stage": "execute", "ok": False}
    capture_before_replan(env, again, "original instructions")
    assert again["checkpoint"]["reason"] == "replan_already_captured", json.dumps(again)
    assert (target / "checkpoint.json").read_bytes() == captured


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
@pytest.mark.parametrize(("drift", "mutation_stage"), [(None, "probe"),
    ("canonical_head", "probe"), ("exported_bytes", "probe"),
    ("canonical_head", "capture"), ("exported_bytes", "capture"),
    ("research_log", "probe"), ("research_log", "capture"),
    ("vision_index", "probe"), ("vision_index", "capture")])
def test_controller_participant_keeps_canonical_state_and_journals_inert(tmp_path, monkeypatch, provider, drift,
                                                                      mutation_stage, caller_ids=False):
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.goals.first_party_host_admission import capture_first_party_host_goal_ref
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement
    import subprocess

    def authority_head(root, advance=False):
        module = Path(__file__).resolve().parents[2]/'loopx/control_plane/coordination/local_authority_provider.ts'
        script = ('import {openLocalAuthorityStore} from '+json.dumps(module.as_uri())+';'
            "let input='';for await(const chunk of process.stdin)input+=chunk;"
            'const x=JSON.parse(input);const store=await openLocalAuthorityStore(x.root,x.goal,{}, {existingOnly:true});'
            "if(x.advance){const head=await store.loadAuthority();const result=await store.commitAuthority({"
            "expected_provider_revision:head.provider_revision,operation_id:'synthetic-source-observation',"
            "events:[],receipts:[],next_projection:head.head});if(result.status!=='applied')throw new Error(JSON.stringify(result));}"
            'process.stdout.write(JSON.stringify(await store.loadAuthority()));')
        result = subprocess.run(['node','--no-warnings','--experimental-strip-types','--input-type=module','-e',script],
            input=json.dumps({'root':str(root),'goal':cli.GOAL_ID,'advance':advance}),capture_output=True,text=True,timeout=45)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = cli._write_fixture(tmp_path)
    source_ref = capture_first_party_host_goal_ref(registry_path=registry, goal_id=cli.GOAL_ID)
    state = project / f'.codex/goals/{cli.GOAL_ID}/ACTIVE_GOAL_STATE.md'
    code, listed = cli._run_cli(registry, runtime, 'todo', 'list', '--goal-id', cli.GOAL_ID)
    assert code == 0, listed
    initialize_canonical_authority(runtime, cli.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=cli.GOAL_ID, todos=listed['todos'],
                                            handoff_mode='hard_lease', leases=[]),
        state_path=state, provider=provider)
    def run(*args):
        code, result = cli._run_cli(registry, runtime, *args)
        assert code == 0 and result['ok'] is True, result
        return result
    lease = run('task-lease', 'acquire', '--goal-id', cli.GOAL_ID, '--todo-id', cli.TODO_ID,
                '--owner', cli.AGENT_ID, '--idempotency-key', 'source-lease')
    binding = ('--agent-id', cli.AGENT_ID, '--todo-id', cli.TODO_ID, '--turn-instance-id', cli.TURN_ID)
    run('quota', 'should-run', '--goal-id', cli.GOAL_ID, '--codex-app', *binding)
    vision = tmp_path / 'vision.json'
    vision.write_text(json.dumps({'schema_version':'goal_vision_replan_contract_v0', 'state':'vision_patch_proposed',
        'vision_patch':{'acceptance_summary':'Controlled participant probe passed; research continuation remains.'},
        'path_delta':{'outcome':'continue','prior_assumption':'Capture requires settled original work.',
            'observed_reality':'Isolated source work was verified.', 'retained':['Participant capture'],
            'evidence_refs':['evidence:controller-fixture']}}))
    run('refresh-state', '--goal-id', cli.GOAL_ID, *binding, '--classification','controller_fixture_verified',
        '--delivery-batch-scale','multi_surface','--delivery-outcome','outcome_progress','--agent-vision-json',str(vision))
    run('quota','spend-slot','--goal-id',cli.GOAL_ID,*binding,'--source','heartbeat','--execute')
    before = run('todo','list','--goal-id',cli.GOAL_ID,'--todo-id',cli.TODO_ID)
    from loopx.capabilities.explore.result_log import (
        explore_result_log_path, append_explore_result_event, build_explore_node_event,
    )
    graph_path = explore_result_log_path(runtime, cli.GOAL_ID)
    def append_source_evidence(title):
        append_explore_result_event(graph_path, build_explore_node_event(goal_id=cli.GOAL_ID,
            title=title, status="dead_end", summary="Fresh validation rejected this route."))
    append_source_evidence("Original rejected route")
    original_head = authority_head(runtime)
    assert original_head['status'] == 'loaded'
    source_settlement = read_heartbeat_settlement(runtime, goal_id=cli.GOAL_ID,agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID,turn_instance_id=cli.TURN_ID,registry_path=registry)
    assert source_settlement.progress['state'] == 'settled'
    logs = tmp_path / 'logs'
    wakes = logs / 'wakes'
    (wakes / cli.TURN_ID).mkdir(parents=True)
    (project / 'solver.py').write_text('candidate = 7\n')
    target = logs / 'checkpoints/controller'
    request = tmp_path / 'request.json'
    components = [{'id':'research_context' if caller_ids else 'code','source_path':str(project/'solver.py')}]
    if caller_ids:
        (project/'notes.txt').write_text('Retained caller-owned context.')
        components.append({'id':'loopx_state_2','source_path':str(project/'notes.txt')})
    request.write_text(json.dumps({'destination':str(target), 'loopx_state':True,
        'components':components, 'point_metadata':{}}))
    env = {'LOOPX_CHECKPOINT_REQUEST':str(request),'LOOPX_WAKE_LOG_DIR':str(wakes),
        'LOOPX_PROJECT':str(project),'LOOPX_RUNTIME_ROOT':str(runtime),'LOOPX_REGISTRY':str(registry),
        'LOOPX_GOAL_ID':cli.GOAL_ID,'LOOPX_AGENT_ID':cli.AGENT_ID}
    receipt = {'ok':True,'stage':'execute','mode':'heartbeat','turn_id':cli.TURN_ID}
    if drift:
        def changed_context(method, params, **kwargs):
            if method == "runtime.checkpoint_artifacts" and params.get("action") == mutation_stage:
                if drift == "canonical_head":
                    # Receipt-only provider commits also move the consistency
                    # cursor; no new knowledge is needed to invalidate the point.
                    authority_head(runtime, advance=True)
                elif drift == "research_log":
                    append_source_evidence("Later evidence outside the point")
                elif drift == "vision_index":
                    with (runtime / "goals" / cli.GOAL_ID / "runs/index.jsonl").open("a") as stream:
                        stream.write(json.dumps({"goal_id": cli.GOAL_ID, "agent_id": cli.AGENT_ID,
                            "generated_at": "2099-01-01T00:00:00Z", "agent_vision": {
                                "vision_patch": {"acceptance_summary": "Later source vision"}}}) + "\n")
                else:
                    (wakes/cli.TURN_ID/'checkpoint-research-context.json').write_text('{"later":true}')
            return effect_runtime_result(method, params, **kwargs)
        monkeypatch.setattr('benchmark.runtime.checkpoint.effect_runtime_result', changed_context)
    capture_after_wake(env, receipt)
    if drift:
        assert not receipt['checkpoint']['captured'] and not target.exists(), receipt
        assert ('context' if mutation_stage == 'probe' else 'source') in receipt['checkpoint']['error']
        assert receipt['ok'] is True
        return
    assert receipt['checkpoint'].get('captured') is True, json.dumps(receipt['checkpoint'])
    manifest = json.loads((target/'checkpoint.json').read_text())
    for component in components:
        assert (target/'components'/component['id']).read_bytes() == Path(component['source_path']).read_bytes()
    declared_context = manifest['point_metadata']['research_context']
    context_root = target/'components'/declared_context['component_id']
    exported_context = context_root/declared_context['member_path'] if declared_context['member_path'] else context_root
    assert hashlib.sha256(exported_context.read_bytes()).hexdigest() == declared_context['sha256']
    context = json.loads(exported_context.read_bytes())
    assert context['snapshot']['provider_revision'] == before['authority_read']['provider_revision']
    assert context['goal_id'] == cli.GOAL_ID and 'leases' not in context['snapshot']
    assert context['research_evidence']['explore_events'][0]['title'] == 'Original rejected route'
    assert any('Controlled participant probe passed' in json.dumps(row)
               for row in context['research_evidence']['vision_history'])
    coverage = manifest['point_metadata']['loopx_state']['source_inventory']
    covered = {entry['source_path']:entry['component_id'] for entry in coverage}
    assert str(runtime) in covered and str(registry) in covered and str(state) in covered
    materialized = tmp_path / 'materialized'
    effect_runtime_result('runtime.checkpoint_artifacts', {'action':'materialize','root':str(target),
        'expected_sha256':receipt['checkpoint']['sha256'],'destination':str(materialized)})
    copied_runtime = materialized/'components'/covered[str(runtime)]
    assert authority_head(copied_runtime) == original_head
    copied_code, copied_todos = cli._run_cli(registry, copied_runtime, 'todo', 'list',
        '--goal-id', cli.GOAL_ID, '--todo-id', cli.TODO_ID)
    assert copied_code == 0, copied_todos
    assert copied_todos['todo'] == before['todo']
    assert copied_todos['authority_read']['source_authority'] == provider + '_v0'
    copied = read_heartbeat_settlement(copied_runtime,goal_id=cli.GOAL_ID,agent_id=cli.AGENT_ID,
        todo_id=cli.TODO_ID,turn_instance_id=cli.TURN_ID,registry_path=registry)
    assert copied.progress['state'] == 'settled' and copied.identity.value == source_settlement.identity.value
    registry_copy = materialized/'components'/covered[str(registry)]/manifest['point_metadata']['loopx_state']['registry_member_path']
    assert registry_copy.read_bytes() == registry.read_bytes()
    assert manifest['research_point_qualified'] is False and manifest['execution_authority_granted'] is False
    assert source_ref is None  # Preserve this legacy fixture; capture does not create an exact GoalRef.
    assert lease['lease']['status'] == 'active'
    after = run('todo','list','--goal-id',cli.GOAL_ID,'--todo-id',cli.TODO_ID)
    assert after['todo'] == before['todo']
    assert after['authority_read']['provider_revision'] == before['authority_read']['provider_revision']


@pytest.mark.parametrize('provider', ['file', 'sqlite'])
def test_research_participants_preserve_legal_caller_ids(tmp_path, monkeypatch, provider):
    test_controller_participant_keeps_canonical_state_and_journals_inert(
        tmp_path, monkeypatch, provider, None, 'probe', caller_ids=True)


@pytest.mark.parametrize('gap', ['shared_registry','wrong_actor','wrong_runtime','missing_state','symlink_state','overlap'])
def test_loopx_participant_rejects_wrong_or_ambiguous_routes(tmp_path, gap):
    from loopx.hosts.research_checkpoint import _loopx_state_components
    project, runtime, registry = cli._write_fixture(tmp_path)
    runtime.mkdir()
    (project/'solver.py').write_text('candidate = 7\n')
    env = {'LOOPX_PROJECT':str(project),'LOOPX_RUNTIME_ROOT':str(runtime),'LOOPX_REGISTRY':str(registry),
        'LOOPX_GOAL_ID':cli.GOAL_ID,'LOOPX_AGENT_ID':cli.AGENT_ID}
    state = project/f'.codex/goals/{cli.GOAL_ID}/ACTIVE_GOAL_STATE.md'
    components = [{'id':'code','source_path':str(project/'solver.py')}]
    source = json.loads(registry.read_text())
    if gap == 'shared_registry':
        source['goals'].append({**source['goals'][0],'id':'other-goal'})
        registry.write_text(json.dumps(source))
    elif gap == 'wrong_actor':
        env['LOOPX_AGENT_ID'] = 'other-agent'
    elif gap == 'wrong_runtime':
        other = tmp_path/'other'
        other.mkdir()
        env['LOOPX_RUNTIME_ROOT'] = str(other)
    elif gap == 'missing_state':
        state.unlink()
    elif gap == 'symlink_state':
        state.unlink()
        state.symlink_to(project/'solver.py')
    else:
        components.append({'id':'narrow','source_path':str(registry)})
    with pytest.raises(ValueError):
        _loopx_state_components(env, components, tmp_path/'logs/checkpoints/point')
    assert not (tmp_path/'logs').exists()


def test_loopx_participant_preserves_legacy_and_current_owned_goal_directories(tmp_path):
    from loopx.hosts.research_checkpoint import _loopx_state_components
    from benchmark.runtime.checkpoint import parse_checkpoint_request
    project, runtime, registry = cli._write_fixture(tmp_path)
    # These are legitimate controller partitions, not two Goal registrations.
    # Inventory follows the backup owner, not a basename-based Goal classifier.
    for name in (cli.GOAL_ID, cli.GOAL_ID+'-partition'):
        (runtime/'goals'/name).mkdir(parents=True)
    components, metadata = _loopx_state_components({'LOOPX_PROJECT':str(project),
        'LOOPX_RUNTIME_ROOT':str(runtime),'LOOPX_REGISTRY':str(registry),
        'LOOPX_GOAL_ID':cli.GOAL_ID,'LOOPX_AGENT_ID':cli.AGENT_ID}, [], tmp_path/'logs/checkpoints/point')
    assert sum(entry['source_path'] == str(runtime) for entry in components) == 1
    assert metadata['execution_authority_granted'] is False
    with pytest.raises(ValueError, match='explicit boolean'):
        parse_checkpoint_request(json.dumps({'destination':'/logs/agent/checkpoints/point',
            'components':[{'id':'code','source_path':'/tmp/solver.py'}], 'point_metadata':{},
            'loopx_state':'true'}).encode())


def test_failed_replan_index_cannot_relabel_later_work_after_permissions_recover(tmp_path, monkeypatch):
    """Real CLI/worker/permissions; only the model executable is synthetic."""
    import os
    import sys
    from benchmark.runtime.worker import run_once

    if os.geteuid() == 0:
        pytest.skip("Real POSIX permission denial requires an unprivileged test user")
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = cli._write_fixture(tmp_path)
    skills = tmp_path / "skills"
    skills.mkdir()
    binary = tmp_path / "codex"
    binary.write_text(f"#!{sys.executable}\n" +
        "import json,pathlib,sys\n"
        "pathlib.Path('host-input.txt').write_text(sys.stdin.read())\n"
        "pathlib.Path('solution.py').write_text('after_strategy_decision = 999\\n')\n"
        "print(json.dumps({'type':'thread.started','thread_id':'synthetic-index-session'}))\n")
    binary.chmod(0o755)
    logs = tmp_path / "logs"
    env = {**os.environ, "LOOPX_PROJECT": str(project), "LOOPX_CLI": str(Path(sys.executable).parent / "loopx"),
        "LOOPX_CODEX_HOME": str(tmp_path / "home"), "LOOPX_WAKE_LOG_DIR": str(logs / "wakes"),
        "LOOPX_SHARED_SKILLS": str(skills), "CODEX_BIN": str(binary), "MODEL_NAME": "fixture",
        "REASONING_EFFORT": "high", "OPENAI_BASE_URL": "http://localhost:8123/v1",
        "OPENAI_API_KEY": "fixture-key", "LOOPX_RUNTIME_ROOT": str(runtime),
        "LOOPX_REGISTRY": str(registry), "LOOPX_GOAL_ID": cli.GOAL_ID, "LOOPX_AGENT_ID": cli.AGENT_ID}
    monkeypatch.setattr("benchmark.runtime.worker.heartbeat_body", lambda *a, **kw: "original work contract")
    prior = run_once(env)
    assert prior["ok"]

    def run(*args):
        code, packet = cli._run_cli(registry, runtime, *args)
        assert code == 0, packet
        return packet

    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Synthetic work completed; original checkpoint qualification remains."},
        "path_delta": {"outcome": "continue", "prior_assumption": "The original worker remains available.",
            "observed_reality": "Real worker ran the explicitly synthetic host successfully.",
            "retained": ["Checkpoint source boundary"], "evidence_refs": ["evidence:index-permission-fixture"]}}))
    binding = ("--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID, "--turn-instance-id", prior["turn_id"])
    run("quota", "should-run", "--goal-id", cli.GOAL_ID, "--codex-app", *binding)
    run("refresh-state", "--goal-id", cli.GOAL_ID, *binding, "--classification", "worker_fixture_completed",
        "--delivery-batch-scale", "multi_surface", "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision))
    run("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *binding, "--source", "heartbeat", "--execute")
    index = runtime / "goals" / cli.GOAL_ID / "runs/index.jsonl"
    original = index.read_bytes()
    cli._configure_autonomous_replan_fixture(project, runtime, registry)
    index.write_bytes(original + index.read_bytes())
    solution = project / "solution.py"
    solution.write_text("retained_candidate = 7\n")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"trigger": "fresh_replan", "destination": str(logs / "checkpoints"),
        "components": [{"id": "code", "source_path": str(solution)}],
        "point_metadata": {"purpose": "Original research boundary, not post-decision code"}}))
    env["LOOPX_CHECKPOINT_REQUEST"] = str(request)
    attempts = logs / "checkpoints/.replan-attempts"
    attempts.mkdir(parents=True)
    attempts.chmod(0o500)
    try:
        failed = run_once(env)
    finally:
        attempts.chmod(0o700)
    assert failed["ok"]
    assert failed["checkpoint"]["reason"] == "capture_failed"
    assert failed["checkpoint"]["error_kind"] == "PermissionError"
    assert not list(attempts.iterdir())
    saved_path = logs / "wakes" / failed["turn_id"] / "receipt.json"
    saved = json.loads(saved_path.read_text())
    assert saved == failed
    assert solution.read_text() == "after_strategy_decision = 999\n"
    replay = {"turn_id": failed["turn_id"], "mode": "heartbeat", "stage": "execute", "ok": False}
    body = capture_before_replan(env, replay, "original work contract")
    assert body.endswith("original work contract")
    assert not replay["checkpoint"]["captured"], replay
    assert replay["checkpoint"]["reason"] == "pre_decision_capture_already_attempted", replay
    assert not list((logs / "checkpoints").glob("replan-*/checkpoint.json"))
    assert json.loads(saved_path.read_text()) == saved
    # A new wake on the same outstanding obligation is also a retry, not a
    # new original point. The durable guard remains evidence without its index.
    again = run_once(env)
    assert again["ok"] and again["turn_id"] != failed["turn_id"]
    assert not again["checkpoint"]["captured"]
    assert again["checkpoint"]["reason"] == "pre_decision_capture_already_attempted"
    assert not list((logs / "checkpoints").glob("replan-*/checkpoint.json"))
    # Settle that actual admitted review, then supply a separately declared
    # later pair of synthetic observations. The real owner must issue a new
    # obligation; the lost point must not suppress this distinct boundary.
    guard = json.loads((logs / "wakes" / again["turn_id"] / "checkpoint-start-guard.json").read_text())
    old_obligation = guard["replan_action_packet"]["obligation_id"]
    replan_binding = ("--agent-id", cli.AGENT_ID, "--replan-obligation-id", old_obligation,
                      "--turn-instance-id", again["turn_id"])
    data = json.loads(vision.read_text())
    data["vision_patch"]["acceptance_summary"] = "The missed original point stays missed; later research boundaries still qualify separately."
    data["path_delta"]["observed_reality"] = "Real permission recovery did not recapture later code as the original research point."
    data["path_delta"].update(schema_version="goal_path_delta_v0", outcome="replan")
    vision.write_text(json.dumps(data))
    run("refresh-state", "--goal-id", cli.GOAL_ID, *replan_binding,
        "--classification", "missed_original_point_preserved", "--delivery-batch-scale", "multi_surface",
        "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision),
        "--autonomous-replan-recorded", "--repair-delta-kind", "goal_vision_patch",
        "--progress-result-class", "advanced", "--progress-surface-id", "surface:checkpoint-boundary",
        "--progress-hypothesis-id", "hypothesis:missed-index", "--progress-probe-kind", "probe:real-permission",
        "--progress-evidence-id", "evidence:index-recovery")
    run("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *replan_binding, "--source", "heartbeat", "--execute")
    from datetime import datetime, timedelta, timezone
    source_history = index.read_bytes()
    cli._configure_autonomous_replan_fixture(project, runtime, registry)
    observations = [json.loads(line) for line in index.read_text().splitlines()]
    observed_at = datetime.now(timezone.utc)
    for number, row in enumerate(observations):
        row["generated_at"] = (observed_at + timedelta(microseconds=number)).isoformat()
        row["progress_observation"].update(surface_id="surface-later", hypothesis_id="hypothesis-later")
        Path(row["json_path"]).write_text(json.dumps(row))
    index.write_bytes(source_history + b"".join((json.dumps(row) + "\n").encode() for row in observations))
    fresh = run_once(env)
    assert fresh["ok"] and fresh["checkpoint"]["captured"], fresh
    point = Path(fresh["checkpoint"]["destination"])
    assert point.name != old_obligation
    assert (point / "components/code").read_text() == "after_strategy_decision = 999\n"
