"""Native checkpoint history must be complete and cannot become execution authority."""
import hashlib
import json
import os
import time
from contextlib import contextmanager

import pytest

from tests.control_plane.codex_checkpoint_fixture import real_native_source

from loopx.hosts.codex_checkpoint import native_session_checkpoint
from benchmark.tests.test_checkpoint_controller import controllers  # noqa: F401 -- shared real HTTP fixture


def fixture(tmp_path, monkeypatch, pages, *, expected_history_query=None):
    home = tmp_path / "native"
    home.mkdir()
    for name in ("state_5.sqlite", "thread_history_1.sqlite", "config.toml", "auth.json", "logs_1.sqlite"):
        (home / name).write_text("synthetic private fixture")
    (home / "sessions").mkdir()
    (home / "skills").symlink_to(home / "sessions", target_is_directory=True)
    calls = []

    class Native:
        response_timeout_sec = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def notify(self, method, params):
            calls.append(method)

        def request(self, method, params):
            calls.append(method)
            if method == "initialize":
                return {}
            if method == "thread/read":
                return {"thread": {"id": "fixture-session", "cwd": str(tmp_path)}}
            assert method == "thread/turns/list"  # No model or fork is requested.
            if expected_history_query is not None:
                assert params == expected_history_query
            return pages.pop(0)

    monkeypatch.setattr("loopx.hosts.codex_checkpoint.StdioNativeGoalTransport.spawn", lambda *a, **k: Native())
    monkeypatch.setattr("loopx.hosts.codex_checkpoint.subprocess.check_output", lambda *a, **k: "codex-cli fixture")
    return {"CODEX_HOME": str(home), "CODEX_BIN": "/fixture/codex", "LOOPX_PROJECT": str(tmp_path)}, calls


def test_all_native_pages_and_stores_are_retained_without_credentials(tmp_path, monkeypatch):
    turns = [{"id": "before-compaction", "status": "completed", "items": [{"type": "compaction"}]},
             {"id": "after-compaction", "status": "completed", "items": []}]
    env, calls = fixture(tmp_path, monkeypatch, [{"data": turns[:1], "nextCursor": "next"},
                                               {"data": turns[1:], "nextCursor": None}])
    result = native_session_checkpoint(env, "fixture-session", timeout=10)
    metadata = result["metadata"]
    assert metadata["turn_ids"] == [t["id"] for t in turns]
    assert metadata["completed_turn_id"] == "after-compaction"
    assert metadata["visible_history_sha256"] == hashlib.sha256(
        json.dumps(turns, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert metadata["execution_authority_granted"] is False
    assert {c["id"] for c in result["components"]} == {
        "native_sessions", "native_state_5_sqlite", "native_thread_history_1_sqlite"}
    assert calls == ["initialize", "initialized", "thread/read", "thread/turns/list", "thread/turns/list"]


@pytest.mark.parametrize("pages,reason", [
    ([{"data": [], "nextCursor": None}], "complete retained"),
    ([{"data": [{"id": "active", "status": "inProgress"}], "nextCursor": None}], "complete retained"),
    ([{"data": [{"id": "failed", "status": "failed"}], "nextCursor": None}], "completed final"),
    ([{"data": [], "nextCursor": "same"}, {"data": [], "nextCursor": "same"}], "repeated history"),
    ([{"data": [{"id": "same", "status": "completed"}] * 2, "nextCursor": None}], "duplicate turns"),
])
def test_incomplete_or_ambiguous_native_history_is_not_a_checkpoint(tmp_path, monkeypatch, pages, reason):
    env, _ = fixture(tmp_path, monkeypatch, pages)
    with pytest.raises(ValueError, match=reason):
        native_session_checkpoint(env, "fixture-session", timeout=10)


def test_foreign_native_session_is_rejected_before_history(tmp_path, monkeypatch):
    env, calls = fixture(tmp_path, monkeypatch, [])
    with pytest.raises(ValueError, match="identity mismatch"):
        native_session_checkpoint(env, "foreign-session", timeout=10)
    assert "thread/turns/list" not in calls


@pytest.mark.parametrize("tail,valid", [
    ({"id": "continued", "status": "completed", "items": []}, True),
    ({"id": "source", "status": "completed", "items": []}, False),
    ({"id": "continued", "status": "inProgress", "items": []}, False),
    ({"id": "continued", "status": "interrupted", "items": []}, False),
    ({"id": "continued", "status": "failed", "items": []}, False),
    ({"status": "completed", "items": []}, False),
    (None, False),
])
def test_preflight_reads_only_the_new_completed_cursor(tmp_path, monkeypatch, tail, valid):
    from loopx.hosts.codex_checkpoint import native_completed_cursor
    env, calls = fixture(tmp_path, monkeypatch,
        [{"data": [tail] if tail else [], "nextCursor": "older-history-not-requested"}],
        expected_history_query={"threadId": "fixture-session", "sortDirection": "desc",
                                "itemsView": "notLoaded", "limit": 1})
    if valid:
        assert native_completed_cursor(env, "fixture-session", retained_turn_ids=["source"], timeout=10) == "continued"
    else:
        with pytest.raises(ValueError, match="cursor"):
            native_completed_cursor(env, "fixture-session", retained_turn_ids=["source"], timeout=10)
    assert calls == ["initialize", "initialized", "thread/read", "thread/turns/list"]


@pytest.mark.parametrize("tail,accepted", [
    ({"id": "first", "status": "completed"}, True),
    ({"id": "first", "status": "inProgress"}, False),
    (None, False),
])
def test_empty_reset_prefix_still_needs_an_actual_completed_cursor(tmp_path, monkeypatch, tail, accepted):
    from loopx.hosts.codex_checkpoint import native_completed_cursor
    env, calls = fixture(tmp_path, monkeypatch, [{"data": [tail] if tail else []}],
        expected_history_query={"threadId": "fixture-session", "sortDirection": "desc",
                                "itemsView": "notLoaded", "limit": 1})
    if accepted:
        assert native_completed_cursor(env, "fixture-session", retained_turn_ids=[], timeout=10) == "first"
    else:
        with pytest.raises(ValueError, match="cursor"):
            native_completed_cursor(env, "fixture-session", retained_turn_ids=[], timeout=10)
    assert calls[-1] == "thread/turns/list"


@pytest.mark.parametrize("retained", [None, "source", [None], [""]])
def test_missing_or_malformed_prefix_is_not_an_explicit_empty_reset(tmp_path, monkeypatch, retained):
    from loopx.hosts.codex_checkpoint import native_completed_cursor
    env, calls = fixture(tmp_path, monkeypatch, [])
    with pytest.raises(ValueError, match="retained source"):
        native_completed_cursor(env, "fixture-session", retained_turn_ids=retained, timeout=10)
    assert calls == []


@pytest.mark.parametrize("fault", [None, "missing", "source_cursor", "failed_cursor", "changed_prefix"])
def test_full_capture_rechecks_prefix_and_provisional_cursor(tmp_path, monkeypatch, fault):
    original = [{"id": "source", "status": "completed", "items": [{"type": "compaction"}]}]
    retained = {"turn_ids": ["source"], "visible_history_sha256": hashlib.sha256(
        json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    turns = json.loads(json.dumps(original)) + [
        {"id": "continued", "status": "failed" if fault == "failed_cursor" else "completed", "items": []},
        {"id": "final", "status": "completed", "items": []}]
    if fault == "changed_prefix":
        turns[0]["items"] = []
    required = "absent" if fault == "missing" else "source" if fault == "source_cursor" else "continued"
    env, _ = fixture(tmp_path, monkeypatch, [{"data": turns, "nextCursor": None}])
    if fault:
        with pytest.raises(ValueError, match="retained checkpoint history|continuation cursor"):
            native_session_checkpoint(env, "fixture-session", timeout=10,
                retained_history=retained, required_completed_turn_id=required)
    else:
        result = native_session_checkpoint(env, "fixture-session", timeout=10,
            retained_history=retained, required_completed_turn_id=required)
        assert result["metadata"]["completed_turn_id"] == "final"


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
                    reason="Requires an explicit real native Host; only local static responses are used")
def test_real_cursor_preflight_still_requires_full_retained_capture_history(tmp_path):
    from loopx.hosts.codex_checkpoint import native_completed_cursor
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with real_native_source(tmp_path / "native", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"], workspace) as (
            env, session, complete, calls):
        retained = native_session_checkpoint(env, session, timeout=20)["metadata"]
        with pytest.raises(ValueError, match="new completed final cursor"):
            native_completed_cursor(env, session, retained_turn_ids=retained["turn_ids"], timeout=20)
        complete()
        cursor = native_completed_cursor(env, session, retained_turn_ids=retained["turn_ids"], timeout=20)
        captured = native_session_checkpoint(env, session, timeout=20, retained_history=retained,
            required_completed_turn_id=cursor)["metadata"]
        assert captured["turn_ids"] == [*retained["turn_ids"], cursor]
        for original, required, reason in (
                ({**retained, "visible_history_sha256": "0" * 64}, cursor, "retained checkpoint history"),
                (retained, "missing-cursor", "continuation cursor")):
            with pytest.raises(ValueError, match=reason):
                native_session_checkpoint(env, session, timeout=20, retained_history=original,
                    required_completed_turn_id=required)
        assert calls == ["/v1/responses"] * 2  # Readback never starts extra model work.


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
                    reason="Requires an explicit real native Host binary; no model service used")
def test_real_native_interrupt_retains_branch_but_does_not_qualify_checkpoint(tmp_path):
    """A resumable native interruption is not a completed research boundary.

    This characterizes the provider seam needed by midwake replan handoff. The
    existing checkpoint must keep rejecting it until the Host proves the joint
    boundary; a successful native fork alone must not loosen that contract.
    """
    import subprocess
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import StdioNativeGoalTransport

    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "candidate.txt").write_text("retained-candidate\n")
    requests = []

    def response(request, index, message):
        requests.append(request)
        if index == 1:
            message["content"][0]["text"] = "Retained research marker: retained-candidate."
        elif index in {2, 3}:
            command = (
                "python3 -c \"from pathlib import Path; import time; "
                "Path('boundary-ready').write_text('ready'); time.sleep(30)\""
                if index == 2 else
                "cat candidate.txt && printf 'continued-candidate\\n' > continued.txt"
            )
            return {"type": "function_call", "id": f"fc_{index}", "call_id": f"call_{index}",
                    "name": "exec_command", "arguments": json.dumps({"cmd": command,
                        "yield_time_ms": 1000, "max_output_tokens": 200})}
        else:
            message["content"][0]["text"] = "Verified retained inputs and produced the next candidate."
        return message

    @contextmanager
    def native_host(env):
        with StdioNativeGoalTransport.spawn(
            [env["CODEX_BIN"], "-c", "features.goals=false", "app-server", "--listen", "stdio://"],
            cwd=str(workspace), env=env, stderr=subprocess.DEVNULL, response_timeout_sec=15,
        ) as native:
            native.request("initialize", {"clientInfo": {"name": "handoff_fixture", "version": "0.1.0"},
                                          "capabilities": {"experimentalApi": True}})
            native.notify("initialized", {})
            yield native

    def history(native, session):
        return native.request("thread/turns/list", {"threadId": session,
            "sortDirection": "asc", "itemsView": "full", "limit": 100})["data"]

    def wait_terminal(native, session, turn, status):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            rows = history(native, session)
            if rows and rows[-1]["id"] == turn and rows[-1]["status"] == status:
                return rows
            time.sleep(.02)
        raise AssertionError("Native handoff did not reach its expected terminal state")

    with real_native_source(tmp_path / "native", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"],
            workspace, close_source=True, response_builder=response,
            sandbox="workspace-write") as (env, source, _, _calls):
        with native_host(env) as native:
            native.request("thread/resume", {"threadId": source, "cwd": str(workspace),
                "sandbox": "workspace-write", "approvalPolicy": "never", "persistExtendedHistory": True})
            turn = native.request("turn/start", {"threadId": source, "input": [
                {"type": "text", "text": "Reach the synthetic Host handoff boundary."}]})["turn"]["id"]
            deadline = time.monotonic() + 10
            while not (workspace / "boundary-ready").exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert (workspace / "boundary-ready").exists()
            native.request("turn/interrupt", {"threadId": source, "turnId": turn})
            retained = wait_terminal(native, source, turn, "interrupted")
            assert len(requests) == 2  # No later model decision consumed the boundary.
        with pytest.raises(ValueError, match="completed final cursor"):
            native_session_checkpoint(env, source, timeout=15)
        with native_host(env) as native:
            fork = native.request("thread/fork", {"threadId": source, "cwd": str(workspace),
                "lastTurnId": turn, "persistExtendedHistory": True})["thread"]["id"]
            assert fork != source and history(native, fork) == retained
            next_turn = native.request("turn/start", {"threadId": fork, "input": [
                {"type": "text", "text": "Verify the retained inputs and produce continued.txt."}]})["turn"]["id"]
            wait_terminal(native, fork, next_turn, "completed")
            assert (workspace / "continued.txt").read_text() == "continued-candidate\n"
            assert "retained-candidate" in json.dumps(requests[2])
            assert history(native, source) == retained


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
                    reason="Requires an explicit real native Host binary; no model service used")
def test_real_native_workspace_readback_rejects_foreign_code_basis(tmp_path):
    from pathlib import Path
    workspace = tmp_path / "source-workspace"
    workspace.mkdir()
    with real_native_source(tmp_path / "native", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"], workspace) as (env, source, _, calls):
        valid = native_session_checkpoint(env, source, timeout=20)
        assert valid["metadata"]["guest_workspace"] == str(workspace)
        assert len(valid["metadata"]["turn_ids"]) == 1 and calls == ["/v1/responses"]
        foreign = Path(env["LOOPX_PROJECT"]).with_name("foreign-workspace")
        foreign.mkdir()
        with pytest.raises(ValueError, match="workspace"):
            native_session_checkpoint({**env, "LOOPX_PROJECT": str(foreign)}, source, timeout=20)


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
                    reason="Requires an explicit real native Host binary; only a local static provider is used")
@pytest.mark.parametrize("case", ["resume", "scope_change", "profile_change", "joint", "binding_reply_loss",
                                 "fork_reply_loss", "fork_missing", "fork_ambiguous"])
def test_real_fork_admission_and_native_resume_use_retained_state(tmp_path, capsys, monkeypatch, case, request):
    """Real Host/CLI/Goal/session/artifact owners; deterministic synthetic work.

    This jointly qualifies native conversation and canonical Todo evidence
    adoption. The joint case adds actual native HTTP and Host phase entry, with
    synthetic grading. Full cross-owner barriers and research utility remain
    outside this fixture. No benchmark cell or paid model is launched.
    """
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    from benchmark.runtime.codex import Execution
    from loopx.hosts.codex_checkpoint import admit_native_checkpoint_session
    from benchmark.runtime.sessions import BenchmarkSessionWake
    from benchmark.runtime.worker import native_command
    from loopx.cli import main
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from loopx.control_plane.turn_driver.codex_sessions import load_codex_cli_session, _store_codex_cli_session
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement
    from loopx.control_plane.projects.registry_codec import ProjectRegistryProtocolError

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)  # Real settlement verifies the producing workspace.
    candidate = workspace / "candidate.txt"
    candidate.write_text("retained candidate: 7\n")
    research = workspace / "research.txt"
    research.write_text("candidate 3 was rejected by the original synthetic check\n")
    source_goal, source_agent = "source-research", "source-researcher"
    destination_goal, destination_agent = "branch-research", "branch-researcher"
    def register(name, agent):
        registry = tmp_path / name / "registry.json"
        runtime = tmp_path / name / "runtime"
        prefix = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime)]
        assert main([*prefix, "bootstrap", "--project", str(workspace), "--goal-id", name,
            "--objective", "Continue the synthetic candidate investigation.", "--adapter-kind", "read_only_project_map_v0",
            "--adapter-status", "connected-read-only", "--write-scope", "**", "--no-global-sync"]) == 0
        capsys.readouterr()
        assert main([*prefix, "configure-goal", "--goal-id", name, "--registered-agent", agent,
            "--agent-work-mode", agent + "=active", "--execute"]) == 0
        capsys.readouterr()
        assert main([*prefix, "todo", "add", "--goal-id", name, "--role", "agent", "--claimed-by", agent,
            "--task-class", "advancement_task", "--required-capability", "shell",
            "--text", "Verify the retained candidate and preserve applicable negative evidence."]) == 0
        added = json.loads(capsys.readouterr().out)
        return registry, runtime, added["todo_id"]
    source_registry, source_runtime, source_todo = register(source_goal, source_agent)
    registry, runtime, destination_todo = register(destination_goal, destination_agent)
    prefix = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime)]
    turn_id = "checkpoint-continuation-fixture"
    binding = ["--goal-id", destination_goal, "--agent-id", destination_agent,
               "--todo-id", destination_todo, "--turn-instance-id", turn_id]
    assert main([*prefix, "quota", "should-run", "--runtime-profile", "generic_cli", *binding]) == 0
    guard = json.loads(capsys.readouterr().out)
    assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
    from loopx.hosts.research_checkpoint import canonical_context, research_context, adopt_todo_context, _encoded
    source_prefix = ["--format", "json", "--registry", str(source_registry), "--runtime-root", str(source_runtime)]
    def invoke(arguments):
        code = main(arguments)
        output = capsys.readouterr().out
        assert code == 0, output
        return json.loads(output)
    source_binding = ["--goal-id", source_goal, "--todo-id", source_todo]
    invoke([*source_prefix, "task-lease", "acquire", *source_binding,
            "--owner", source_agent, "--idempotency-key", "source-context"])
    source_revision = invoke([*source_prefix, "todo", "list", *source_binding])["authority_read"]["provider_revision"]
    invoke([*source_prefix, "todo", "update", *source_binding, "--agent-id", source_agent,
        "--note", "Candidate 3 failed the synthetic check; preserve candidate 7.",
        "--update-operation-id", "source-negative-evidence", "--update-expected-provider-revision", source_revision,
        "--task-lease-idempotency-key", "source-context", "--task-lease-expected-version", "1"])
    requests = []
    retained_context_path = None
    joint_command = ""
    if case == "joint":
        import copy
        import shlex
        import requests as http
        from benchmark.edgebench import checkpoint_controller as transport
        from benchmark.tests.test_checkpoint_controller import register as register_judge, submit, host_phase_command
        source_judge, branch_judge, source_url, branch_url, controllers_files, grades = request.getfixturevalue("controllers")
        source_token = register_judge(source_judge, source_url, max_agent_submissions=3, submission_cooldown=0)
        assert submit(source_url, source_token, 7).status_code == 200
        assert submit(source_url, source_token, 3).status_code == 200
        source_judge.state.judge.tokens[source_token]["submission_cooldown"] = 3
        source_phase = tmp_path / "source-phase"
        source_phase.write_text(f"{int(time.time()) + 7200}\n")
        evaluator_unit = tmp_path / "evaluator-point"
        evaluator_point = transport.capture(controllers_files[0], token=source_token,
            capture_id="e" * 32, destination=evaluator_unit, phase_deadline_file=source_phase)
        evaluator_body = json.loads((evaluator_unit / "checkpoint.json").read_bytes())
        # Real participants are quiesced by this fixture. It is not a production
        # cross-owner capture barrier or a proof of autonomous research utility.

    def response(request, index, message):
        requests.append(request)
        if index == 2:
            return {"type": "function_call", "id": "fc_retained", "call_id": "call_retained",
                "name": "exec_command", "arguments": json.dumps({
                    "cmd": "cat candidate.txt research.txt " + retained_context_path + joint_command, "max_output_tokens": 6000})}
        if index == 3:
            message["content"][0]["text"] = "Retained candidate 7 verified; candidate 3 remains rejected."
        return message

    binary = os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"]
    execution = Execution(context="resume", sandbox="workspace-write" if case == "joint" else "read-only")
    with real_native_source(tmp_path / "native", binary, workspace, close_source=True,
                            response_builder=response, sandbox=execution.sandbox) as (env, source_session, _, calls):
        env.update(LOOPX_REGISTRY=str(source_registry), LOOPX_RUNTIME_ROOT=str(source_runtime),
                   LOOPX_GOAL_ID=source_goal, LOOPX_AGENT_ID=source_agent,
                   MODEL_NAME="fixture", REASONING_EFFORT="high", LOOPX_CODEX_SANDBOX=execution.sandbox)
        source_wake = BenchmarkSessionWake(env, execution, {})
        source_wake.admission.accept_result(lambda: _store_codex_cli_session(source_runtime,
            lineage={"goal_id": source_goal, "agent_id": source_agent}, session_scope="agent",
            session_id=source_session, goal_ref=source_wake.goal_ref, session_profile_digest=source_wake.digest))
        native = native_session_checkpoint(env, source_session, timeout=20)
        config = tmp_path / "config.toml"
        config.write_bytes((Path(env["CODEX_HOME"]) / "config.toml").read_bytes())
        from loopx.capabilities.explore.result_log import (
            append_explore_result_event, build_explore_node_event, explore_result_log_path,
        )
        graph_path = explore_result_log_path(source_runtime, source_goal)
        append_explore_result_event(graph_path, build_explore_node_event(
            goal_id=source_goal, node_id="rejected-route", title="Rejected route", status="dead_end",
            summary="Route rejected because fresh samples reversed its pilot gain."))
        source_vision = tmp_path / "source-vision.json"
        source_vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0",
            "state": "vision_patch_proposed", "vision_patch": {"acceptance_summary":
                "Complete a fresh-sample experiment before changing the retained route."}}))
        invoke([*source_prefix, "quota", "should-run", *source_binding, "--agent-id", source_agent,
            "--turn-instance-id", "source-research-evidence", "--runtime-profile", "generic_cli"])
        invoke([*source_prefix, "refresh-state", *source_binding, "--agent-id", source_agent,
            "--turn-instance-id", "source-research-evidence", "--classification", "synthetic_research_evidence",
            "--delivery-batch-scale", "multi_surface", "--delivery-outcome", "outcome_progress",
            "--agent-vision-json", str(source_vision)])
        source_context = research_context(env, deadline=time.time() + 30)
        context_raw = _encoded(source_context)
        context_export = tmp_path / "source-context.json"
        context_export.write_bytes(context_raw)
        components = [*native["components"], {"id": "code", "source_path": str(candidate)},
                      {"id": "research", "source_path": str(research)},
                      {"id": "research_context", "source_path": str(context_export)}]
        if case == "joint":
            components.append({"id": "evaluation", "source_path": str(evaluator_unit)})
        probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
        unit = tmp_path / "point"
        captured = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture",
            "destination": str(unit), "components": components, "expected_source_sha256": probe["source_sha256"],
            "point_metadata": {"native_session": native["metadata"],
                "session": {"session_id": source_session, "session_profile_digest": source_wake.digest},
                "loopx_state": {"goal_id": source_goal, "agent_id": source_agent},
                "research_context": {"component_id": "research_context", "member_path": "",
                    "sha256": hashlib.sha256(context_raw).hexdigest(),
                    "provider_revision": source_context["snapshot"]["provider_revision"],
                    "store_identity": source_context["snapshot"]["store_identity"]}}}, retry_safe=False)
        digest = captured["sha256"]
        if case == "joint":
            source_judge.state.judge.tokens[source_token]["submission_cooldown"] = 0
            assert submit(source_url, source_token, 99).status_code == 200
            judge_before = copy.deepcopy((source_judge.state.judge.tokens,
                source_judge.state.judge.submissions, source_judge.state.judge.run_history))
        invoke([*source_prefix, "todo", "update", *source_binding, "--agent-id", source_agent,
            "--note", "Candidate 999 is later evidence outside the captured point.",
            "--update-operation-id", "later-negative-evidence", "--update-expected-provider-revision",
            source_context["snapshot"]["provider_revision"], "--task-lease-idempotency-key", "source-context",
            "--task-lease-expected-version", "1"])
        append_explore_result_event(graph_path, build_explore_node_event(
            goal_id=source_goal, node_id="future-route", title="Future route outside this checkpoint",
            status="exploring"))
        later_context = canonical_context(env, deadline=time.time() + 30)
        # Qualify use of captured inputs, not the source's later working files.
        # A quiesced same-machine fixture swaps fixed paths; this is not evidence
        # of complete guest/environment isolation or a scientific matched trial.
        candidate.write_text("later candidate: 999\n")
        research.write_text("later evidence unavailable at the captured point\n")
        late_source = tmp_path / "retained-later-source"
        late_source.mkdir()
        shutil.move(candidate, late_source / candidate.name)
        shutil.move(research, late_source / research.name)
        materialized = tmp_path / "materialized"
        effect_runtime_result("runtime.checkpoint_artifacts", {"action": "materialize", "root": str(unit),
            "expected_sha256": digest, "destination": str(materialized)}, retry_safe=False)
        shutil.copy2(materialized / "components/code", candidate)
        shutil.copy2(materialized / "components/research", research)
        original_home = tmp_path / "original-home"
        shutil.move(env["CODEX_HOME"], original_home)
        retained_names = {m["name"] for m in native["metadata"]["store_members"]} | {"config.toml"}
        def retained_source():
            return {p.relative_to(original_home).as_posix(): p.read_bytes()
                for p in original_home.rglob("*") if p.is_file() and p.relative_to(original_home).parts[0] in retained_names}
        before = retained_source()
        env.update(LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime),
                   LOOPX_GOAL_ID=destination_goal, LOOPX_AGENT_ID=destination_agent,
                   MODEL_NAME="fixture", REASONING_EFFORT="high")
        lifecycle_registry = tmp_path / "lifecycle-only" / "registry.json"
        assert main(["--format", "json", "--registry", str(lifecycle_registry),
            "--runtime-root", str(tmp_path / "lifecycle-only/runtime"), "project", "register",
            "--goal-instance-profile", "source_session_v1", "--operation-id", "lifecycle-only",
            "--project-id", "lifecycle-only", "--project-kind", "work", "--knowledge-root", str(workspace),
            "--goal-id", "lifecycle-only", "--objective", "Synthetic lifecycle fixture.",
            "--acceptance", "Lifecycle only.", "--next-effect", "Read.", "--stop-condition", "Stop."]) == 0
        capsys.readouterr()
        with pytest.raises(ProjectRegistryProtocolError, match="lifecycle-only"):
            admit_native_checkpoint_session({**env, "LOOPX_REGISTRY": str(lifecycle_registry),
                "LOOPX_GOAL_ID": "lifecycle-only"}, unit, digest, config, sandbox=(execution).sandbox, timeout=30)
        assert not Path(env["CODEX_HOME"]).exists() and len(calls) == 1
        unsupported_config = tmp_path / "automatic-goal-config.toml"
        unsupported_config.write_text(config.read_text().replace("goals=false", "goals=true"))
        with monkeypatch.context() as automatic:
            def forbidden_automatic_fork(*args, **kwargs):
                raise AssertionError("an unsupported automatic Goal profile must fail before native reconstruction")
            automatic.setattr("loopx.hosts.codex_checkpoint.fork_native_checkpoint", forbidden_automatic_fork)
            with pytest.raises(ValueError, match="automatic Goals explicitly disabled"):
                admit_native_checkpoint_session(env, unit, digest, unsupported_config, sandbox=(execution).sandbox, timeout=30)
        with pytest.raises(ValueError, match="distinct destination"):
            admit_native_checkpoint_session({**env, "LOOPX_GOAL_ID": source_goal}, unit, digest,
                                            config, sandbox=(execution).sandbox, timeout=30)
        assert not Path(env["CODEX_HOME"]).exists() and len(calls) == 1
        if case == "scope_change":
            from loopx.hosts import codex_checkpoint as native_checkpoint
            original_fork = native_checkpoint.fork_native_checkpoint
            def change_scope_after_real_fork(*args, **kwargs):
                result = original_fork(*args, **kwargs)
                assert main([*prefix, "configure-goal", "--goal-id", destination_goal,
                    "--agent-work-mode", destination_agent + "=monitor_only", "--execute"]) == 0
                capsys.readouterr()
                return result
            monkeypatch.setattr(native_checkpoint, "fork_native_checkpoint", change_scope_after_real_fork)
            with pytest.raises(ValueError, match="Goal configuration changed"):
                admit_native_checkpoint_session(env, unit, digest, config, sandbox=(execution).sandbox, timeout=30)
            assert load_codex_cli_session(runtime, lineage={"goal_id": destination_goal,
                "agent_id": destination_agent}, session_scope="agent") is None
            assert len(calls) == 1  # A native fork occurred; no binding or model did.
            assert retained_source() == before
            return
        if case == "profile_change":
            with pytest.raises(ValueError, match="profile changed"):
                admit_native_checkpoint_session({**env, "REASONING_EFFORT": "low"}, unit, digest,
                                                config, sandbox=(execution).sandbox, timeout=30)
            assert load_codex_cli_session(runtime, lineage={"goal_id": destination_goal,
                "agent_id": destination_agent}, session_scope="agent") is None
            assert len(calls) == 1
            assert retained_source() == before
            return
        recovered_fork = None
        if case in {"fork_reply_loss", "fork_missing", "fork_ambiguous"}:
            from loopx.hosts.codex_checkpoint import StdioNativeGoalTransport
            original_request = StdioNativeGoalTransport.request
            forks = []
            def lose_fork_reply(self, method, params):
                if method != "thread/fork":
                    return original_request(self, method, params)
                if case != "fork_missing":
                    forks.append(original_request(self, method, params)["thread"]["id"])
                if case == "fork_ambiguous":
                    forks.append(original_request(self, method, params)["thread"]["id"])
                raise TimeoutError("synthetic loss before fork reply")
            with monkeypatch.context() as lost:
                lost.setattr(StdioNativeGoalTransport, "request", lose_fork_reply)
                with pytest.raises(TimeoutError, match="loss before fork reply"):
                    admit_native_checkpoint_session(env, unit, digest, config, sandbox=(execution).sandbox, timeout=30)
            assert load_codex_cli_session(runtime, lineage={"goal_id": destination_goal,
                "agent_id": destination_agent}, session_scope="agent") is None
            assert len(calls) == 1 and retained_source() == before
            if case != "fork_reply_loss":
                def no_second_fork(self, method, params):
                    assert method != "thread/fork", "uncertainty must not dispatch a second fork"
                    return original_request(self, method, params)
                with monkeypatch.context() as retry:
                    retry.setattr(StdioNativeGoalTransport, "request", no_second_fork)
                    with pytest.raises(ValueError, match="absent or ambiguous"):
                        admit_native_checkpoint_session(env, unit, digest, config, sandbox=(execution).sandbox, timeout=30)
                assert load_codex_cli_session(runtime, lineage={"goal_id": destination_goal,
                    "agent_id": destination_agent}, session_scope="agent") is None
                assert len(calls) == 1 and retained_source() == before
                return
            recovered_fork = forks[0]
            # The isolated attempt is bound to this destination, not merely an
            # equivalent visible history or another Goal's transport directory.
            from loopx.hosts.codex_checkpoint import fork_native_checkpoint
            with pytest.raises(ValueError, match="attempt differs"):
                fork_native_checkpoint({**env, "LOOPX_AGENT_ID": "unrelated-destination"},
                    unit, digest, config, timeout=30)
        if case == "binding_reply_loss":
            from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
            original_bind = CodexAgentSession._bind_verified_fork
            def lose_binding_reply(self, *args):
                original_bind(self, *args)
                raise TimeoutError("synthetic loss after atomic binding")
            with monkeypatch.context() as lost:
                lost.setattr(CodexAgentSession, "_bind_verified_fork", lose_binding_reply)
                with pytest.raises(TimeoutError, match="loss after atomic binding"):
                    admit_native_checkpoint_session(env, unit, digest, config, sandbox=(execution).sandbox, timeout=30)
            assert load_codex_cli_session(runtime, lineage={"goal_id": destination_goal,
                "agent_id": destination_agent}, session_scope="agent")["checkpoint_artifact_sha256"] == digest
            assert len(calls) == 1
        admitted_cli = subprocess.run([sys.executable, "-m", "loopx.hosts.codex_checkpoint",
            "--root", str(unit), "--expected-sha256", digest, "--config-path", str(config), "--sandbox", execution.sandbox],
            env=env, text=True, capture_output=True, timeout=40)
        assert admitted_cli.returncode == 0, admitted_cli.stderr
        admitted = json.loads(admitted_cli.stdout)
        assert admitted["session_binding_admitted"] and admitted["history_verified"]
        assert admitted["model_calls"] == 0 and not admitted["continuation_qualified"]
        assert admitted["session_id"] != source_session and len(calls) == 1
        if recovered_fork:
            assert admitted["session_id"] == recovered_fork
        bound = load_codex_cli_session(runtime, lineage={"goal_id": destination_goal, "agent_id": destination_agent},
                                      session_scope="agent")
        assert bound["session_id"] == admitted["session_id"]
        assert bound.get("goal_ref") == admitted["destination_goal_ref"]
        with monkeypatch.context() as replay:
            def forbidden_fork(*args, **kwargs):
                raise AssertionError("a repeated admission must not create a second native fork")
            replay.setattr("loopx.hosts.codex_checkpoint.fork_native_checkpoint", forbidden_fork)
            recovered = admit_native_checkpoint_session(env, unit, digest, config, sandbox=(execution).sandbox, timeout=30)
            assert recovered == admitted
            assert bound["checkpoint_artifact_sha256"] == digest
            # Transport reply loss leaves the atomic source-bound binding as the
            # original receipt. An exact CLI retry must also recover it.
            retry_cli = subprocess.run(admitted_cli.args, env=env, text=True, capture_output=True, timeout=40)
            assert retry_cli.returncode == 0, retry_cli.stderr
            assert json.loads(retry_cli.stdout) == admitted
            with pytest.raises(ValueError, match="profile changed"):
                admit_native_checkpoint_session({**env, "REASONING_EFFORT": "low"}, unit, digest,
                                                config, sandbox=(execution).sandbox, timeout=30)
        assert len(calls) == 1 and load_codex_cli_session(runtime, lineage={
            "goal_id": destination_goal, "agent_id": destination_agent}, session_scope="agent") == bound
        if case == "resume":
            wrong_config = tmp_path / "changed-source-config.toml"
            wrong_config.write_text(config.read_text() + "\n# different source configuration\n")
            with pytest.raises(ValueError, match="effective configuration differs"):
                admit_native_checkpoint_session(env, unit, digest, wrong_config, sandbox=(execution).sandbox, timeout=30)
            from loopx.control_plane.turn_driver.codex_sessions import _session_path
            binding_path = _session_path(runtime, {"goal_id": destination_goal, "agent_id": destination_agent}, "agent")
            original_binding = binding_path.read_bytes()
            # Isolated fixture corruption: neither an unrelated binding nor a
            # binding for another checkpoint may be relabeled as this admission.
            for wrong_source in (None, "f" * 64):
                changed = dict(bound)
                if wrong_source is None:
                    changed.pop("checkpoint_artifact_sha256")
                else:
                    changed["checkpoint_artifact_sha256"] = wrong_source
                binding_path.write_text(json.dumps(changed))
                with pytest.raises(ValueError, match="without this checkpoint admission"):
                    admit_native_checkpoint_session(env, unit, digest, config, sandbox=(execution).sandbox, timeout=30)
                assert json.loads(binding_path.read_text()) == changed and len(calls) == 1
            binding_path.write_bytes(original_binding)

        # Both participants meet at the real destination continuation owner.
        # No source claim/lease is replayed; adoption alone still starts no work.
        lease = invoke([*prefix, "task-lease", "acquire", "--goal-id", destination_goal,
            "--todo-id", destination_todo, "--owner", destination_agent, "--idempotency-key", "branch-context"])
        current = invoke([*prefix, "todo", "list", "--goal-id", destination_goal, "--todo-id", destination_todo])
        context_command = [sys.executable, "-m", "loopx.hosts.research_checkpoint",
            "--root", str(unit), "--expected-sha256", digest, "--source-todo-id", source_todo,
            "--todo-id", destination_todo, "--operation-id", "branch-context",
            "--expected-revision", current["authority_read"]["provider_revision"],
            "--lease-key", "branch-context", "--lease-version", str(lease["lease"]["version"])]
        assert not explore_result_log_path(runtime, destination_goal).exists(), "session admission must not adopt knowledge"
        adopted_cli = subprocess.run(context_command, env=env, text=True, capture_output=True, timeout=60)
        assert adopted_cli.returncode == 0, adopted_cli.stderr
        context_result = json.loads(adopted_cli.stdout)
        # Retrying the exact CLI after a lost result reuses both original
        # receipts; it must not fork, charge, edit the Todo or run a model.
        replay_cli = subprocess.run(context_command, env=env, text=True, capture_output=True, timeout=60)
        assert replay_cli.returncode == 0, replay_cli.stderr
        replay_result = json.loads(replay_cli.stdout)
        assert replay_result["continuation"]["adoption"]["status"] == "replayed"
        assert replay_result["continuation"]["provider_revision"] == context_result["continuation"]["provider_revision"]
        assert len(calls) == 1
        retained_context_path = str(Path(context_result["artifact"]).relative_to(workspace))
        assert Path(context_result["artifact"]).read_bytes() == context_raw and len(calls) == 1
        adopted_todo = invoke([*prefix, "todo", "list", "--goal-id", destination_goal, "--todo-id", destination_todo])
        assert adopted_todo["todo"]["claimed_by"] == destination_agent
        assert json.loads(adopted_todo["todo"]["note"])["source_session"] == source_session
        guard = invoke([*prefix, "quota", "should-run", "--runtime-profile", "generic_cli", *binding])
        assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
        if case == "joint":
            control = tmp_path / "host-control"
            control.mkdir()
            branch_phase = control / "phase-deadline"
            # Restore only the nested capsule from the same verified artifact,
            # never the later source service or an unrelated caller file.
            fresh = transport.restore(controllers_files[1], root=materialized / "components/evaluation",
                expected_sha256=evaluator_point["sha256"], run_id="joint-branch", phase_deadline_file=branch_phase)
            assert submit(branch_url, fresh["token"], 8).status_code == 429
            assert len(grades) == 3  # No implicit regrade on restore or cooldown rejection.
            shell = host_phase_command(tmp_path, monkeypatch).replace(
                "/opt/loopx-benchmark/control", str(control))
            budget = subprocess.run(["/bin/bash", "-c", shell + 'printf "%s" "$remaining"'],
                text=True, capture_output=True, check=True)
            assert 7100 < int(budget.stdout) <= 7200
            assert int(branch_phase.read_bytes()) <= time.time() + evaluator_body["point_metadata"]["host_phase_budget"]["remaining_seconds"]
            # Explicit fresh token only, never controller/admin credentials.
            # The native resumed tool reads public history, uses captured code,
            # submits one new candidate, and observes the preserved round cap.
            solver_input = workspace / "branch-judge.json"
            solver_input.write_text(json.dumps({"url": branch_url, "token": fresh["token"]}))
            solver_input.chmod(0o600)
            solver = workspace / "continue_synthetic.py"
            solver.write_text("""import json, time
from pathlib import Path
import requests
binding = json.loads(Path('branch-judge.json').read_text())
url, token = binding['url'], binding['token']
history = requests.get(url + '/api/v1/history', params={'token': token}, timeout=5).json()
assert history['best_score'] == 7 and history['agent_submissions'] == 2
value = float(Path('candidate.txt').read_text().split(':')[1]) + 1
response = requests.post(url + '/api/v1/submit', data={'token': token},
    files={'archive': ('candidate.tar.gz', str(value).encode())}, timeout=5)
response.raise_for_status()
identifier = response.json()['submission_id']
for _ in range(100):
    result = requests.get(url + '/api/v1/result/' + identifier, timeout=5).json()
    if result['status'] == 'completed': break
    time.sleep(.01)
assert result['status'] == 'completed'
exhausted = requests.post(url + '/api/v1/submit', data={'token': token},
    files={'archive': ('candidate.tar.gz', b'999')}, timeout=5)
assert exhausted.status_code == 429
print('Captured public best 7; continued candidate 8; consumed round agent-3; no extra opportunity.')
""")
            # Wait only the inherited short synthetic cooldown, never create an
            # opportunity by changing source/destination counters or timestamps.
            time.sleep(evaluator_body["point_metadata"]["native_reference"]["opportunity"]["remaining_cooldown_seconds"] + .05)
            joint_command = " && " + shlex.quote(sys.executable) + " continue_synthetic.py"
        receipt = {}
        wake = BenchmarkSessionWake(env, execution, receipt)
        assert wake.session_id == admitted["session_id"] and receipt["session"]["action"] == "resume"
        logs = tmp_path / "continuation"
        logs.mkdir()
        command = native_command(env, execution, "execute", logs, wake)
        if case == "joint":
            command = ["/bin/bash", "-c", shell + "exec " + shlex.join(command)]
        current_body = json.dumps(guard["interaction_contract"], ensure_ascii=False)
        result = subprocess.run(command, env=env, cwd=workspace, input=current_body + "\nContinue: verify the retained files.",
                                text=True, capture_output=True, timeout=40)
        assert result.returncode == 0, result.stderr
        (logs / "stdout.jsonl").write_text(result.stdout)
        wake.observe(logs / "stdout.jsonl")
        assert wake.session_id == admitted["session_id"] and len(calls) == 3
        assert "Synthetic source result." in json.dumps(requests[1])
        assert destination_goal in json.dumps(requests[1]) and destination_todo in json.dumps(requests[1])
        assert "retained candidate: 7" in json.dumps(requests[2])
        assert "candidate 3 was rejected" in json.dumps(requests[2])
        assert "Candidate 3 failed the synthetic check" in json.dumps(requests[2])
        assert "fresh samples reversed its pilot gain" in json.dumps(requests[2])
        assert "Complete a fresh-sample experiment" in json.dumps(requests[2])
        assert source_context["research_evidence"]["explore_events"][0]["status"] == "dead_end"
        # Explicit context adoption seeds verified knowledge, never historical
        # execution observations. Native session admission alone above did not.
        from loopx.capabilities.explore.result_log import EVENT_KIND_EDGE
        adopted_events = [json.loads(line) for line in
            explore_result_log_path(runtime, destination_goal).read_text().splitlines()]
        captured_events = source_context["research_evidence"]["explore_events"]
        assert len(adopted_events) == len(captured_events)
        for captured, adopted_event in zip(captured_events, adopted_events, strict=True):
            assert adopted_event["goal_id"] == destination_goal
            assert adopted_event["run_id"] == "checkpoint:" + digest + ":" + captured["event_id"]
            assert adopted_event["event_id"] != captured["event_id"]
            assert "research_observation" not in adopted_event
            changed_identity = {"goal_id", "run_id", "event_id", "research_observation"}
            if captured["event_kind"] == EVENT_KIND_EDGE:
                changed_identity.add("result_id")
            assert all(adopted_event.get(key) == value for key, value in captured.items()
                       if key not in changed_identity)

        assert "Candidate 999" not in json.dumps(requests[2])
        assert "Future route outside this checkpoint" not in json.dumps(requests[2])
        assert canonical_context({**env, "LOOPX_GOAL_ID": source_goal,
            "LOOPX_RUNTIME_ROOT": str(source_runtime)}, deadline=time.time() + 30) == later_context
        if case == "joint":
            assert "Captured public best 7; continued candidate 8" in json.dumps(requests[2])
            history = http.get(branch_url + "/api/v1/history", params={"token": fresh["token"]}, timeout=5).json()
            assert history["best_score"] == 8 and history["best_round"] == "agent-3"
            assert history["agent_submissions"] == 3 and len(grades) == 4
            assert (source_judge.state.judge.tokens, source_judge.state.judge.submissions,
                    source_judge.state.judge.run_history) == judge_before
            for secret in (source_token, fresh["token"], source_judge.state.judge.admin_secret,
                           branch_judge.state.judge.admin_secret):
                assert secret not in json.dumps(requests)
        assert "Retained candidate 7 verified" in result.stdout
        retained = native_session_checkpoint(env, admitted["session_id"], timeout=20)
        assert retained["metadata"]["turn_ids"][:-1] == native["metadata"]["turn_ids"]
        assert retained["metadata"]["completed_turn_id"] != native["metadata"]["completed_turn_id"]
        after_work = load_codex_cli_session(runtime, lineage={"goal_id": destination_goal,
            "agent_id": destination_agent}, session_scope="agent")
        assert after_work["checkpoint_artifact_sha256"] == digest
        with pytest.raises(ValueError, match="advanced or differs"):
            admit_native_checkpoint_session(env, unit, digest, config, sandbox=(execution).sandbox, timeout=30)
        assert load_codex_cli_session(runtime, lineage={"goal_id": destination_goal,
            "agent_id": destination_agent}, session_scope="agent") == after_work
        with pytest.raises(ValueError, match="native history or configuration moved"):
            adopt_todo_context(env, unit, digest, source_todo_id=source_todo,
                todo_id=destination_todo, operation_id="must-not-adopt-later-session",
                expected_revision=adopted_todo["authority_read"]["provider_revision"],
                lease_key="branch-context", lease_version=lease["lease"]["version"])
        assert len(calls) == 3
        assert invoke([*prefix, "todo", "list", "--goal-id", destination_goal,
                       "--todo-id", destination_todo])["todo"] == adopted_todo["todo"]
        assert candidate.read_text() == "retained candidate: 7\n"
        assert (late_source / candidate.name).read_text() == "later candidate: 999\n"
        assert (late_source / research.name).read_text() == "later evidence unavailable at the captured point\n"
        assert retained_source() == before
        vision = tmp_path / "continuation-vision.json"
        vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
            "vision_patch": {"acceptance_summary": "Synthetic conversation continuation verified; full research remains open."},
            "path_delta": {"outcome": "continue", "prior_assumption": "Retained candidate and evidence are available.",
                "observed_reality": "The resumed native tool read verifies candidate 7 and the rejection of candidate 3.",
                "retained": ["Complete checkpoint qualification"], "evidence_refs": ["evidence:native-continuation-fixture"]}}))
        assert main([*prefix, "refresh-state", *binding, "--classification", "checkpoint_continuation_verified",
            "--delivery-batch-scale", "multi_surface", "--delivery-outcome", "outcome_progress",
            "--agent-vision-json", str(vision)]) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
        assert main([*prefix, "quota", "spend-slot", *binding, "--source", "heartbeat", "--execute"]) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
        settled = read_heartbeat_settlement(runtime, goal_id=destination_goal, agent_id=destination_agent,
            todo_id=destination_todo, turn_instance_id=turn_id, registry_path=registry)
        assert settled.progress["state"] == "settled"


def test_interrupted_cursor_requires_exact_unreleased_final_guard(tmp_path, monkeypatch):
    """A stopped command is eligible only at the declared pre-output boundary."""
    import copy
    import shlex
    from loopx.hosts.codex_checkpoint import _verify_interrupted_guard

    guard = {"cli": "/fixture/loopx", "workspace": str(tmp_path), "registry": "/fixture/registry.json",
             "runtime_root": "/fixture/runtime", "goal_id": "research", "agent_id": "worker",
             "turn_instance_id": "turn-1", "capture_root": "/fixture/decisions", "socket_path": "/tmp/fixture/socket",
             "decision_sha256": "a" * 64}
    argv = [guard["cli"], "--registry", guard["registry"], "--runtime-root", guard["runtime_root"],
            "--format", "json", "quota", "should-run", "--goal-id", "research", "--agent-id", "worker",
            "--turn-instance-id", "turn-1", "--turn-envelope", "--decision-output-root", guard["capture_root"],
            "--decision-handoff-socket", guard["socket_path"]]
    command = shlex.join(["/bin/bash", "-lc", shlex.join(argv)])
    item = {"type": "commandExecution", "id": "guard-call", "command": command, "cwd": str(tmp_path),
            "status": "failed", "exitCode": 137, "aggregatedOutput": None}
    turns = [{"id": "retained-compaction", "status": "completed", "items": [{"type": "contextCompaction"}]},
             {"id": "pending", "status": "interrupted", "items": [item]}]
    env, calls = fixture(tmp_path, monkeypatch, [{"data": turns[:1], "nextCursor": "next"},
                                               {"data": turns[1:], "nextCursor": None}])
    captured = native_session_checkpoint(env, "fixture-session", timeout=10, interrupted_guard=guard)
    assert captured["metadata"]["turn_ids"] == ["retained-compaction", "pending"]
    assert "completed_turn_id" not in captured["metadata"]
    assert captured["metadata"]["interrupted_guard"]["native_call_id"] == "guard-call"
    assert "thread/fork" not in calls

    rendered = copy.deepcopy(turns)
    rendered[-1]["items"][-1]["command"] = shlex.join(["/bin/bash", "-lc",
        "LOOPX_TURN=turn-1\n" + shlex.join(argv).replace("--turn-instance-id turn-1", '--turn-instance-id "${LOOPX_TURN:?}"')])
    _verify_interrupted_guard(rendered, guard)
    variants = []
    for field, value in [("aggregatedOutput", "score already exposed"), ("cwd", "/elsewhere"),
                         ("status", "completed"), ("command", shlex.join(["/bin/bash", "-lc", shlex.join(argv) + "; touch later"]))]:
        changed = copy.deepcopy(turns)
        changed[-1]["items"][-1][field] = value
        variants.append(changed)
    for option, value in [("--turn-instance-id", "wrong-turn"), ("--decision-handoff-socket", "/tmp/other"),
                          ("--registry", "/other.json"), ("--goal-id", "other")]:
        altered = list(argv)
        altered[altered.index(option) + 1] = value
        changed = copy.deepcopy(turns)
        changed[-1]["items"][-1]["command"] = shlex.join(["/bin/bash", "-lc", shlex.join(altered)])
        variants.append(changed)
    changed = copy.deepcopy(turns)
    changed[-1]["items"].append({"type": "reasoning", "text": "later decision"})
    variants.append(changed)
    changed = copy.deepcopy(turns)
    changed[-1]["status"] = "completed"
    variants.append(changed)
    changed = copy.deepcopy(turns)
    changed[-1]["items"].insert(0, copy.deepcopy(item))
    variants.append(changed)
    for changed in variants:
        with pytest.raises(ValueError):
            _verify_interrupted_guard(changed, guard)
    with pytest.raises(ValueError, match="call identity"):
        _verify_interrupted_guard(turns, {**guard, "native_call_id": "different"})
