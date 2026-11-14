"""Original Chat + native kernel + File/SQLite; only the model/provider are synthetic."""
import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

from test_native_steward_managed_mode import original, storage
from test_native_steward_private import steward as steward_fixture, finish
from loopx.cli import main
from loopx.capabilities.native_chat.steward_turn import StewardTurnHost
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime


# Canonical stopped activation; the retired ``status: stopped`` field is now a
# registry protocol error that only the explicit upgrade decoder may consume.
STOPPED_ACTIVATION = {"schema_version": "loopx_goal_activation_v1", "state": "stopped",
                      "updated_at": "2026-01-01T00:00:00Z", "reason": "fixture stop"}

steward = steward_fixture


def managed_fixture(steward, tmp_path, monkeypatch, provider="file", *, validator=True):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    sid, managed = original(steward)
    storage(steward[1], provider)
    binding = steward[4]
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(binding["binding_id"], session_id=sid,
        expected_plan_id=plan["plan_id"], execute=True)["actor"]
    store, runtime, _, _, _, capture, _ = steward
    from loopx.agent_registry import load_goal_from_registry
    from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
    goal = load_goal_from_registry(runtime.registry_path, actor["goal_id"])
    workspace = Path(goal["repo"])
    session, _ = runtime.open_session(goal_id=actor["goal_id"], agent_id="codex", work_dir=workspace,
        objective="Observe the original task", channel_id=f"goal.{actor['goal_id']}", mode="new",
        managed_agent_id=actor["agent_id"])
    turn, _ = runtime.submit_turn(session_id=session["session_id"], client_turn_id="original-question",
        message="Inspect this source and retain our original conversation.", work_dir=workspace, objective="Observe the original task")
    assert finish(runtime, turn)["status"] == "completed"
    observation = workspace / "managed-source.txt"
    observation.write_text("ready")
    declaration = [sys.executable, "-c", "from pathlib import Path; assert Path('managed-source.txt').read_text() == 'ready'"]
    added = cli(runtime, "todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
        "--text", "Validate the readiness source before completing this task.", "--claimed-by", actor["agent_id"],
        "--action-kind", "validate", "--continuation-policy", "same_agent_non_delivery",
        *(["--validation-command-json", json.dumps(declaration)] if validator else []), "--execute")
    fake = Path(runtime.codex_bin)
    candidate = {"result_kind": "validated_completion", "classification": "implementation",
        "summary": "The synthetic readiness source is ready.", "next_action": "Continue the original authorized work.",
        "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
        "vision_unchanged_reason": "The original task remains the current route.",
        "work_evidence": ["managed-source.txt contains the original readiness signal"],
        "validation_evidence": ["The task's independent validator checks the original readiness condition"]}
    source = fake.read_text().replace('"message": "Runtime response."', '"message": ' + repr(json.dumps(candidate)))
    fake.write_text(source)
    runtime.adapters.pop(session["session_id"]).close_session()
    return ManagedSessionTurn(runtime), session, actor, added["todo_id"], observation, capture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_registered_agents_have_distinct_durable_sessions_without_adopting_legacy_history(steward, tmp_path, monkeypatch, provider):
    service, first, actor, _, _, capture = managed_fixture(steward, tmp_path, monkeypatch, provider)
    store, runtime, *_ = steward
    from loopx.chat.session_open import open_chat_session
    from loopx.agent_registry import load_goal_from_registry
    cli(runtime, "configure-goal", "--goal-id", actor["goal_id"], "--registered-agent", actor["agent_id"],
        "--registered-agent", "peer", "--execute")
    workspace = Path(load_goal_from_registry(runtime.registry_path, actor["goal_id"])["repo"])
    def opened(agent=None, mode="resume_latest"):
        return open_chat_session(controller=runtime, context_kind="goal", goal_id=actor["goal_id"],
            work_dir=workspace, objective="Independent work contexts", mode=mode, managed_agent_id=agent)
    legacy, _ = opened()
    assert not service.inspect(legacy["session_id"])["available"]
    legacy_turn, _ = runtime.submit_turn(session_id=legacy["session_id"], client_turn_id="legacy-question",
        message="Keep this unmapped history intact", work_dir=workspace, objective="Legacy conversation")
    assert finish(runtime, legacy_turn)["status"] == "completed"
    old_request = service._request(legacy["session_id"], actor["agent_id"], "old-unknown")
    old = store.accept_managed_turn(legacy["session_id"], client_turn_id="managed.old-unknown",
        message="Retained pre-binding operation", origin="native_loopx", loopx_execution=True, loopx_request=old_request).turn
    old_calls = capture.read_bytes()
    assert service.inspect(legacy["session_id"], operation_id="old-unknown")["turn"]["turn_id"] == old["turn_id"]
    assert service.submit(legacy["session_id"], agent_id=actor["agent_id"], operation_id="old-unknown") == (old, False)
    assert capture.read_bytes() == old_calls  # Readback of unknown work is never a replay.
    legacy_history = store.messages(legacy["session_id"])
    fake = Path(runtime.codex_bin)
    # The base synthetic host uses one fixed id. Give newly started threads a
    # distinct id and honor the exact original id on thread/resume/read.
    fake.write_text(fake.read_text().replace('"durable-thread"',
        'request.get("params", {}).get("threadId", "new-peer-thread")'))
    peer, resumed = opened("peer")
    assert not resumed and peer["managed_agent_id"] == "peer" and peer["agent_id"] == "codex"
    assert peer["session_id"] != first["session_id"] != legacy["session_id"]
    assert peer["upstream_thread_id"] != first["upstream_thread_id"]
    assert store.messages(peer["session_id"]) == []
    # An attached source is a peer record, never the managed executor's target.
    attached = store.create_session(goal_id=actor["goal_id"], agent_id="peer", adapter_kind="codex_app_server",
        upstream_thread_id="original-attached", session_mode="attached_host", host_surface="codex_app")
    before_calls = capture.read_bytes()
    for _ in range(2):
        same, resumed = opened("peer")
        assert resumed and same["session_id"] == peer["session_id"]
        same, resumed = opened(actor["agent_id"])
        assert resumed and same["session_id"] == first["session_id"]
    resumed_calls = [json.loads(line) for line in capture.read_bytes()[len(before_calls):].splitlines()]
    assert not [row for row in resumed_calls if row["method"] in {"thread/start", "turn/start"}]
    for row in (first, peer):
        runtime.adapters.pop(row["session_id"]).close_session()
        same, resumed = opened(row["managed_agent_id"])
        assert resumed and same["session_id"] == row["session_id"]
        assert same["upstream_thread_id"] == row["upstream_thread_id"]
    assert opened()[0]["session_id"] == legacy["session_id"]
    assert store.messages(legacy["session_id"]) == legacy_history
    assert store.load_session(attached["session_id"]) == attached
    with pytest.raises(ValueError):
        service.submit(first["session_id"], agent_id="peer", operation_id="wrong-registered-agent")
    with pytest.raises(ValueError):
        store.update_session(first["session_id"], managed_agent_id="peer")
    with pytest.raises(ValueError):
        store.update_session(first["session_id"], goal_id="another-goal")
    public = store.public_session(first)
    assert public["managed_agent_id"] == actor["agent_id"] and public["executor_endpoint_id"] == "codex"


@pytest.mark.parametrize("fault", ["unregistered", "revoked", "stopped", "lifetime", "workspace", "project", "channel", "executor"])
def test_managed_identity_rejections_do_not_create_or_resume_a_host(steward, tmp_path, monkeypatch, fault):
    _, session, actor, _, _, capture = managed_fixture(steward, tmp_path, monkeypatch)
    store, runtime, *_ = steward
    from loopx.agent_registry import load_goal_from_registry
    workspace = Path(load_goal_from_registry(runtime.registry_path, actor["goal_id"])["repo"])
    args = dict(goal_id=actor["goal_id"], agent_id="codex", work_dir=workspace,
        objective="Keep original identity", mode="resume_latest", managed_agent_id=actor["agent_id"])
    if fault == "unregistered":
        args["managed_agent_id"] = "unregistered"
    elif fault in {"revoked", "stopped", "lifetime"}:
        registry = json.loads(runtime.registry_path.read_text())
        goal = next(row for row in registry["goals"] if row["id"] == actor["goal_id"])
        if fault == "revoked":
            goal["coordination"]["registered_agents"] = ["peer"]
        elif fault == "stopped":
            goal["activation"] = dict(STOPPED_ACTIVATION)
        else:
            goal["goal_instance_id"] = "replaced-lifetime"
        runtime.registry_path.write_text(json.dumps(registry))
    elif fault == "workspace":
        args["work_dir"] = tmp_path
    elif fault == "project":
        args["project_ref"] = "0" * 24
    elif fault == "channel":
        args["channel_id"] = "manager.external.other"
    elif fault == "executor":
        args["agent_id"] = "other-executor"
    before_calls, before_sessions = capture.read_bytes(), store.list_sessions()
    # A changed Goal lifetime starts a fresh registered conversation only on
    # an explicit new/resume request; the old Session cannot execute again.
    if fault == "lifetime":
        with pytest.raises(ValueError, match="identity changed"):
            runtime._ensure_adapter_locked(store.load_session(session["session_id"]), work_dir=workspace, objective="old")
    else:
        with pytest.raises(ValueError):
            runtime.open_session(**args)
    if fault in {"revoked", "stopped", "lifetime"}:
        with pytest.raises(ValueError):
            runtime.submit_turn(session_id=session["session_id"], client_turn_id="revoked-request",
                message="Must not be accepted under a stale identity", work_dir=workspace, objective="old")
        assert store.turn_for_client(session["session_id"], "revoked-request") is None
        with pytest.raises(ValueError):
            runtime.resume_session(session_id=session["session_id"], work_dir=workspace, objective="old")
    assert capture.read_bytes() == before_calls and store.list_sessions() == before_sessions


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("explicit_selection", [False, True], ids=["claim", "ready"])
def test_local_managed_session_reuses_native_turn_and_declared_validation(
    steward, tmp_path, monkeypatch, provider, explicit_selection,
):
    service, session, actor, todo_id, observation, capture = managed_fixture(steward, tmp_path, monkeypatch, provider)
    import loopx.cli_commands.turn as turn_owner
    from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
    from loopx.control_plane.quota import turn_envelope

    original_plan = turn_owner.build_loopx_turn_plan
    original_validation = ManagedSessionTurn.prepare_validation
    evaluate = turn_envelope._turn_envelope_result
    prepared_sources = []
    validation_sources = []
    legacy_builds = []

    def observe_projection(operation, **kwargs):
        if operation == "build":
            legacy_builds.append(operation)
        return evaluate(operation, **kwargs)

    def checked_validation(self, request, native_request, **kwargs):
        validation_sources.append((request["turn_instance_id"], native_request))
        return original_validation(self, request, native_request, **kwargs)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", observe_projection)
    monkeypatch.setattr(ManagedSessionTurn, "prepare_validation", checked_validation)

    def checked_plan(source, *args, **kwargs):
        preparation = source["interaction_contract"]["agent_channel"]["execution_preparation"]
        assert preparation["state"] == "ready"
        assert preparation["todo_id"] == todo_id
        assert preparation["agent_id"] == actor["agent_id"]
        prepared_sources.append(source["schema_version"])
        return original_plan(source, *args, **kwargs)
    monkeypatch.setattr(turn_owner, "build_loopx_turn_plan", checked_plan)
    store, runtime, *_ = steward
    sid = session["session_id"]
    before_sessions = store.list_sessions()
    before_calls, thread = capture.read_bytes(), session["upstream_thread_id"]
    before_history = store.messages(sid)
    assert len(before_history) == 2
    assert service.inspect(sid)["available"]
    requested_todo = todo_id if explicit_selection else None
    accepted, created = service.submit(sid, agent_id=actor["agent_id"], operation_id="local-native-1", todo_id=requested_todo)
    assert created
    completed = finish(runtime, accepted)
    assert completed["status"] == "completed", completed
    result = completed["response"]["native_loopx"]
    assert result.get("status") == "committed", json.dumps(result)
    assert result["effects"]["host_invoked"] and result["effects"]["quota_spent"]
    assert result["validation"]["status"] == "passed"
    assert prepared_sources == ["loopx_turn_envelope_v1"]
    assert legacy_builds == []
    expected_states = ["ready"] if explicit_selection else ["claim_required", "ready"]
    assert len(validation_sources) == len(expected_states)
    for (turn_id, native_request), state in zip(validation_sources, expected_states, strict=True):
        assert turn_id == accepted["loopx_request"]["turn_instance_id"]
        assert "turn_envelope" not in native_request
        source = native_request["work_contract"]
        assert source["schema_version"] == "loopx_turn_envelope_v1"
        assert source["selected_todo"]["todo_id"] == todo_id
        assert source["agent_identity"]["agent_id"] == actor["agent_id"]
        assert source["execution_preparation"]["state"] == state
        if "detail_ref" in native_request:
            assert native_request["detail_ref"]["captured_decision"]["turn_instance_id"] == turn_id
    assert observation.read_text() == "ready"
    assert store.load_session(sid)["upstream_thread_id"] == thread
    assert {row["session_id"] for row in store.list_sessions()} == {row["session_id"] for row in before_sessions}
    assert store.messages(sid)[:len(before_history)] == before_history
    calls = [json.loads(row) for row in capture.read_bytes()[len(before_calls):].splitlines()]
    assert runtime.adapters[sid].session.sandbox == "read-only"
    assert all(row.get("params", {}).get("sandbox", "read-only") == "read-only" for row in calls)
    assert not [row for row in calls if row["method"] in {"thread/start", "thread/goal/set", "thread/inject_items"}]
    after_calls = capture.read_bytes()
    replay, created = service.submit(sid, agent_id=actor["agent_id"], operation_id="local-native-1", todo_id=requested_todo)
    assert not created and replay["turn_id"] == accepted["turn_id"] and capture.read_bytes() == after_calls
    assert service.inspect(sid, operation_id="local-native-1")["turn"]["turn_id"] == accepted["turn_id"]
    with pytest.raises(ValueError, match="identity conflict"):
        service.submit(sid, agent_id=actor["agent_id"], operation_id="local-native-1", todo_id="todo_other")


@pytest.mark.parametrize("fault", ["busy", "queued", "attached", "foreign_goal", "stopped_goal", "private", "native_goal", "unregistered", "host_closed"])
def test_local_managed_session_denials_never_launch_or_accept(steward, tmp_path, monkeypatch, fault):
    service, session, actor, todo_id, _, capture = managed_fixture(steward, tmp_path, monkeypatch)
    store, runtime, *_ = steward
    sid = session["session_id"]
    changes = {"busy": {"active_turn_id": "f" * 32},
        "native_goal": {"upstream_mode": "goal"}}
    if fault in changes:
        store.update_session(sid, **changes[fault])
    elif fault == "queued":
        store.create_queued_turn(sid, client_turn_id="human-first", message="Keep my pending work first", origin="web")
    elif fault in {"stopped_goal", "foreign_goal"}:
        registry = json.loads(runtime.registry_path.read_text())
        if fault == "stopped_goal":
            registry["goals"][0]["activation"] = dict(STOPPED_ACTIVATION)
        else:
            registry["goals"][0]["goal_instance_id"] = "a-replaced-lifetime"
        runtime.registry_path.write_text(json.dumps(registry))
    elif fault == "attached":
        sid = store.create_session(goal_id=session["goal_id"], agent_id="codex", adapter_kind="codex_app_server",
            upstream_thread_id="existing-attached-thread", session_mode="attached_host", host_surface="codex_app")["session_id"]
    elif fault == "private":
        sid = next(row["session_id"] for row in store.list_sessions() if row.get("goal_id") == "loopx-manager")
    elif fault == "host_closed":
        runtime.close()
    before_calls, before_history = capture.read_bytes(), store.messages(sid)
    with pytest.raises(ValueError):
        service.submit(sid, agent_id="unregistered" if fault == "unregistered" else actor["agent_id"],
            operation_id="denied", todo_id=todo_id)
    assert capture.read_bytes() == before_calls and store.messages(sid) == before_history


@pytest.mark.parametrize("fault", ["missing_validator", "failed_validator", "stopped_after_model"])
def test_managed_work_never_commits_without_current_independent_postcondition(steward, tmp_path, monkeypatch, fault):
    service, session, actor, todo_id, observation, capture = managed_fixture(steward, tmp_path, monkeypatch,
        validator=fault != "missing_validator")
    store, runtime, *_ = steward
    if fault == "failed_validator":
        observation.write_text("not ready")
    if fault == "stopped_after_model":
        from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
        original_start = CodexAppServerAdapter.start_turn

        def stopped(adapter, message, sink):
            response = original_start(adapter, message, sink)
            registry = json.loads(runtime.registry_path.read_text())
            registry["goals"][0]["activation"] = dict(STOPPED_ACTIVATION)
            runtime.registry_path.write_text(json.dumps(registry))
            return response

        monkeypatch.setattr(CodexAppServerAdapter, "start_turn", stopped)
    calls = capture.read_bytes()
    accepted, _ = service.submit(session["session_id"], agent_id=actor["agent_id"], operation_id="unqualified", todo_id=todo_id)
    completed = finish(runtime, accepted)
    native = completed["response"]["native_loopx"]
    assert native["status"] == ("stopped" if fault == "missing_validator" else "failed") and not native["effects"]["quota_spent"]
    assert not native["effects"]["state_written"]
    if fault == "missing_validator":
        assert observation.read_text() == "ready"
        assert not [json.loads(row) for row in capture.read_bytes()[len(calls):].splitlines() if json.loads(row)["method"] == "turn/start"]
    after = capture.read_bytes()
    assert not service.submit(session["session_id"], agent_id=actor["agent_id"], operation_id="unqualified", todo_id=todo_id)[1]
    assert capture.read_bytes() == after


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["valid", "historical", "missing_complete", "changed_complete", "null_complete"])
def test_managed_postcondition_checks_complete_source_before_argv(steward, tmp_path, monkeypatch, provider, fault):
    from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
    from loopx.control_plane.turn_driver import command_validation

    service, session, actor, todo_id, _, capture = managed_fixture(steward, tmp_path, monkeypatch, provider)
    runtime = steward[1]
    invoked = []
    if fault == "historical":
        from loopx.cli_commands import turn
        from loopx.control_plane.quota.turn_envelope import build_turn_envelope

        planner = turn.build_loopx_turn_plan

        def recorded_legacy(source, **kwargs):
            plan = planner(build_turn_envelope(source), **kwargs)
            assert plan["schema_version"] == "loopx_turn_plan_v0"
            return plan

        monkeypatch.setattr(turn, "build_loopx_turn_plan", recorded_legacy)
    build = command_validation.build_loopx_turn_command_validator

    def instrument(*args, **kwargs):
        validate = build(*args, **kwargs)

        def observed(plan, result):
            invoked.append(True)
            return validate(plan, result)  # Keep the real independent argv.

        return observed

    monkeypatch.setattr(command_validation, "build_loopx_turn_command_validator", instrument)
    create = ManagedSessionTurn.validator

    def interrupted(self, *args, **kwargs):
        validate = create(self, *args, **kwargs)

        def after_model(plan, result):
            # Fault only the complete source at the actual post-model boundary;
            # actor admission, native Host, validation and settlement stay real.
            assert "work_contract" in plan
            if fault == "changed_complete":
                plan["work_contract"] = {**plan["work_contract"], "reason": "changed after model work"}
            elif fault == "null_complete":
                plan["work_contract"] = None
            elif fault in {"historical", "missing_complete"}:
                plan.pop("work_contract")
            return validate(plan, result)

        return after_model

    monkeypatch.setattr(ManagedSessionTurn, "validator", interrupted)
    before = capture.read_bytes()
    accepted, _ = service.submit(session["session_id"], agent_id=actor["agent_id"],
        operation_id="complete-postcondition", todo_id=todo_id)
    completed = finish(runtime, accepted)
    calls = [json.loads(row) for row in capture.read_bytes()[len(before):].splitlines()]
    assert sum(row["method"] == "turn/start" for row in calls) == 1
    if fault in {"valid", "historical"}:
        assert invoked == [True]
        native = completed["response"]["native_loopx"]
        assert native["status"] == "committed" and native["effects"]["quota_spent"]
    else:
        assert invoked == []  # Refuse before even the independent command runs.
        native = completed["response"]["native_loopx"]
        assert native["status"] == "failed"
        assert not native["effects"]["state_written"] and not native["effects"]["quota_spent"]
    after = capture.read_bytes()
    replay, created = service.submit(session["session_id"], agent_id=actor["agent_id"],
        operation_id="complete-postcondition", todo_id=todo_id)
    assert not created and replay["turn_id"] == accepted["turn_id"]
    assert capture.read_bytes() == after


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_managed_turn_http_correlates_readback_without_accepting_commands(steward, tmp_path, monkeypatch, provider):
    import http.client
    import threading
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    service, session, actor, todo_id, _, capture = managed_fixture(steward, tmp_path, monkeypatch, provider)
    store, runtime, *_ = steward
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_controller, server.chat_store, server.verbose = runtime, store, False
    server.registry_path, server.selected_goal_id = runtime.registry_path, actor["goal_id"]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    route = f"/api/chat/sessions/{session['session_id']}/native-turn"

    def request(body=None, suffix="", origin=None):
        conn = http.client.HTTPConnection(*server.server_address, timeout=20)
        try:
            conn.request("GET" if body is None else "POST", route + suffix,
                None if body is None else json.dumps(body), headers={"Content-Type": "application/json",
                    **({"Origin": origin} if origin else {})})
            result = conn.getresponse()
            return result.status, json.loads(result.read())
        finally:
            conn.close()

    try:
        before = capture.read_bytes()
        # Exercise the shipped producer, not only a hand-built Session fixture.
        conn = http.client.HTTPConnection(*server.server_address, timeout=20)
        conn.request("POST", "/api/chat/sessions", json.dumps({"goal_id": actor["goal_id"],
            "managed_agent_id": actor["agent_id"], "mode": "resume_latest"}),
            headers={"Content-Type": "application/json"})
        opened = conn.getresponse()
        opened_body = json.loads(opened.read())
        conn.close()
        assert opened.status == 200 and opened_body["session_id"] == session["session_id"], opened_body
        assert opened_body["session"]["managed_agent_id"] == actor["agent_id"]
        assert not [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()
                    if json.loads(line)["method"] in {"thread/start", "turn/start"}]
        before = capture.read_bytes()
        assert request()[1]["available"]
        body = {"agent_id": actor["agent_id"], "operation_id": "http-original", "todo_id": todo_id}
        assert request(body, origin="https://foreign.example")[0] == 403
        assert request({**body, "validation_command": ["arbitrary-command"]})[0] == 409
        assert capture.read_bytes() == before
        status, accepted = request(body)
        assert status == 202 and accepted["created"]
        completed = finish(runtime, accepted["turn"])
        assert completed["response"]["native_loopx"]["status"] == "committed"
        after = capture.read_bytes()
        observed = request(suffix="?operation_id=http-original")[1]["turn"]
        assert observed["turn_id"] == completed["turn_id"] and observed["status"] == "completed"
        assert request(body)[1]["created"] is False
        assert request({**body, "todo_id": "todo_different"})[0] == 409
        assert capture.read_bytes() == after
    finally:
        server.shutdown()
        server.server_close()


def test_native_task_validator_cannot_replace_a_cli_host(tmp_path):
    from loopx.cli import build_parser
    from loopx.cli_commands.turn import handle_turn_command
    args = build_parser().parse_args(["turn", "run-once", "--goal-id", "example", "--agent-id", "worker",
        "--host", "codex-cli", "--project", str(tmp_path), "--execute"])
    payload, calls = {}, []
    handle_turn_command(args, registry_path=tmp_path / "registry.json",
        runtime_root_arg=str(tmp_path / "runtime"), output_format=lambda *_a, **_kw: "json",
        print_payload=lambda value, *_a: payload.update(value),
        native_task_validator=lambda *_a: calls.append("called"))
    assert not payload["ok"] and "requires its original host connection" in payload["error"]
    assert calls == [] and not (tmp_path / "registry.json").exists()


def cli(runtime, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["--registry", str(runtime.registry_path), "--runtime-root",
            str(runtime.coordination_runtime_root), "--format", "json", *args])
    result = json.loads(out.getvalue())
    assert code == 0 and result.get("ok"), json.dumps(result, ensure_ascii=False)
    return result


def prepare(steward, provider, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, capture, _ = steward
    sid, managed = original(steward)
    storage(runtime, provider)
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(binding["binding_id"], session_id=sid,
        expected_plan_id=plan["plan_id"], execute=True)["actor"]
    added = cli(runtime, "todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
        "--text", "Inspect the synthetic monitor source and retain its evidence.",
        "--claimed-by", "steward", "--action-kind", "observe",
        "--continuation-policy", "same_agent_non_delivery", "--execute")
    return sid, actor, added


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["queued_input", "binding_scope", "closed_session"])
def test_native_identity_io_leaves_input_and_binding_publication_available(steward, tmp_path, monkeypatch, provider, change):
    import threading
    from loopx.file_lock import exclusive_file_lock
    from loopx.capabilities.native_chat.steward_turn import native_steward_client_id

    sid, actor, _ = prepare(steward, provider, tmp_path, monkeypatch)
    store, runtime, identity, transport, binding, capture, _ = steward
    entered, released = threading.Event(), threading.Event()
    observe, errors = transport.bindings.observe, []
    before = capture.read_bytes()

    def blocked_observation(*args, **kwargs):
        if threading.current_thread() is worker:
            entered.set()
            assert released.wait(10), "identity fixture was not released"
        return observe(*args, **kwargs)

    def submit():
        try:
            StewardTurnHost(runtime).submit(binding["binding_id"], turn_instance_id="waiting-identity")
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=submit)
    monkeypatch.setattr(transport.bindings, "observe", blocked_observation)
    worker.start()
    available = False
    try:
        assert entered.wait(5)
        available = runtime.lock.acquire(timeout=.5)
        assert available, "remote identity IO prevented ordinary input admission"
        with exclusive_file_lock(transport.bindings.path, timeout_seconds=.5):
            if change == "queued_input":
                store.create_queued_turn(sid, client_turn_id="human-first", message="Keep this input first", origin="web")
            elif change == "closed_session":
                store.update_session(sid, status="closed")
        if change == "binding_scope":
            transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
                executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
    finally:
        if available:
            runtime.lock.release()
        else:
            identity.owner_ready = False  # The failing baseline must not start synthetic model work.
        released.set()
        worker.join(10)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ValueError), errors
    assert store.turn_for_client(sid, native_steward_client_id(actor, "waiting-identity")) is None
    assert capture.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("phase", ["initial", "after_report_quota"])
def test_native_submission_history_read_allows_input_and_rejects_changed_binding(steward, tmp_path, monkeypatch, provider, phase):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from loopx.capabilities.native_chat.steward_turn import native_steward_client_id

    sid, actor, _ = prepare(steward, provider, tmp_path, monkeypatch)
    store, runtime, _, transport, binding, capture, workspace = steward
    host = StewardTurnHost(runtime)
    binding_id = binding["binding_id"]
    operation = "slow-inspection"
    report = phase == "after_report_quota"
    entered, release, quota_checked = Event(), Event(), Event()
    params = {}
    if report:
        window = {"window_id": "fixture-window", "subscription_revision": 1}
        operation = "report." + window["window_id"]
        params = {"work_kind": "report", "report_window": window}
    else:
        quota_checked.set()
    client = native_steward_client_id(actor, operation)
    if report:
        original = store.accept_managed_turn(sid, client_turn_id=client, message="Retain the original report",
            origin="native_loopx", loopx_execution=True, loopx_request={
                "operation": "native_turn", "host": "codex-app-server", "actor": actor,
                "turn_instance_id": operation, "todo_id": None, "work_kind": "report", "validation_command": None,
                "report_window_id": window["window_id"], "report_subscription_revision": window["subscription_revision"],
            }).turn
        store.update_turn(sid, original["turn_id"], status="failed", error_code="fixture_failure")
        store.update_session(sid, status="ready", active_turn_id=None)
        observe_quota = host.report_quota_request

        def checked_quota(turn):
            result = observe_quota(turn)
            quota_checked.set()
            return result

        monkeypatch.setattr(host, "report_quota_request", checked_quota)
    lookup = store.turn_for_client

    def slow_history(session_id, client_turn_id):
        if session_id == sid and client_turn_id == client and quota_checked.is_set() and not entered.is_set():
            entered.set()
            assert release.wait(15), "test did not release history read"
        return lookup(session_id, client_turn_id)

    def exercise_same_session():
        queued, created = runtime.enqueue_turn(session_id=sid, client_turn_id="human-during-inspection",
            message="Keep ordinary input available", work_dir=workspace, objective="Ordinary input", origin="web")
        assert created
        if report:
            transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
                executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
        return queued

    workers = []
    before = capture.read_bytes()
    monkeypatch.setattr(store, "turn_for_client", slow_history)
    monkeypatch.setattr(runtime, "_drain_session_queue", lambda **_: None)
    monkeypatch.setattr(runtime, "_start_accepted_turn_worker", lambda **call: workers.append(call))
    with ThreadPoolExecutor(max_workers=2) as pool:
        submission = pool.submit(host.submit, binding_id, turn_instance_id=operation, **params)
        try:
            assert entered.wait(8)
            queued = pool.submit(exercise_same_session).result(timeout=5)
            assert queued["status"] == "queued"
        finally:
            release.set()
        if report:
            with pytest.raises(ValueError, match="authorization changed during submission inspection"):
                submission.result(timeout=8)
            assert store.load_turn(sid, original["turn_id"])["status"] == "failed"
        else:
            with pytest.raises(ValueError, match="retaining its current work"):
                submission.result(timeout=8)
            assert lookup(sid, client) is None
    assert not workers and capture.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("material", [False, True])
def test_native_turn_uses_original_app_server_and_kernel_without_a_new_session(steward, tmp_path, monkeypatch, provider, material):
    sid, actor, added = prepare(steward, provider, tmp_path, monkeypatch)
    store, runtime, _, _, binding, capture, _ = steward
    todo_id = added.get("todo_id") or added.get("todo", {})["todo_id"]
    fake = tmp_path / "codex"
    candidate = {"result_kind": "wait", "classification": "synthetic_monitor_wait",
        "summary": "The synthetic source is unchanged; retain its due work.",
        "next_action": "Wait for its native deadline."}
    if material:
        candidate = {"result_kind": "validated_progress", "classification": "implementation",
            "summary": "Read the synthetic source and wrote its observation artifact.",
            "next_action": "Continue the original monitor integration.",
            "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "The original authorized monitor remains the current route.",
            "work_evidence": ["native-observation.json records the synthetic source"],
            "validation_evidence": ["The native host fixture independently reads the artifact after completion"]}
    text = fake.read_text().replace('"message": "Runtime response."', '"message": ' + repr(json.dumps(candidate)))
    if material:
        from loopx.agent_registry import load_goal_from_registry
        from pathlib import Path
        observation = Path(load_goal_from_registry(runtime.registry_path, actor["goal_id"])["repo"]) / "native-observation.json"
        text = text.replace('        turn_count += 1',
            f'        open({str(observation)!r}, "w").write("source observed")\n        turn_count += 1')
    fake.write_text(text)
    # Original history and exact native thread are present before native work.
    before = store.messages(sid)
    before_sessions = set(store.root.rglob(store._session_path(sid).name))
    assert store._session_path(sid) in before_sessions
    thread = store.load_session(sid)["upstream_thread_id"]
    runtime.adapters.pop(sid).close_session()  # Reopen the same synthetic native thread with this response.
    calls_before_native = capture.read_bytes()
    host = StewardTurnHost(runtime)
    validator = ([sys.executable, "-c", "from pathlib import Path; assert Path('native-observation.json').read_text() == 'source observed'"] if material else None)
    accepted, created = host.submit(binding["binding_id"], turn_instance_id="native-fixture-1", todo_id=todo_id,
        validation_command=validator)
    assert created
    completed = finish(runtime, accepted)
    assert completed["status"] == "completed", completed
    result = completed["response"]["native_loopx"]
    assert result["ok"] and result["status"] == ("committed" if material else "stopped"), json.dumps(result)
    assert result["host"]["kind"] == "codex-app-server"
    assert result["effects"]["host_invoked"] and result["effects"]["quota_spent"] is material
    if material:
        assert observation.read_text() == "source observed"
    assert store.messages(sid)[:len(before)] == before
    assert store.load_session(sid)["upstream_thread_id"] == thread
    assert set(store.root.rglob(store._session_path(sid).name)) == before_sessions
    calls = capture.read_bytes()
    native_calls = [json.loads(line) for line in calls[len(calls_before_native):].decode().splitlines()]
    assert any(row["method"] == "thread/resume" for row in native_calls)
    assert not [row for row in native_calls if row["method"] in {"thread/start", "thread/inject_items", "thread/goal/set"}]
    replay, created = host.submit(binding["binding_id"], turn_instance_id="native-fixture-1", todo_id=todo_id,
        validation_command=validator)
    assert not created and replay["turn_id"] == accepted["turn_id"]
    with pytest.raises(ValueError, match="identity conflict"):
        host.submit(binding["binding_id"], turn_instance_id="native-fixture-1", todo_id="todo_other_work")
    assert capture.read_bytes() == calls


def test_unselected_or_unavailable_work_does_not_invoke_the_model(steward, tmp_path, monkeypatch):
    sid, actor, _ = prepare(steward, "sqlite", tmp_path, monkeypatch)
    store, runtime, _, _, binding, capture, _ = steward
    before = capture.read_bytes()
    accepted, _ = StewardTurnHost(runtime).submit(binding["binding_id"],
        turn_instance_id="missing-work", todo_id="todo_no_such_work")
    completed = finish(runtime, accepted)
    result = completed["response"]["native_loopx"]
    assert not result["ok"]
    # Ensuring an adapter may read/resume the existing thread; it must never
    # start model work or change the selected Todo when native admission fails.
    calls = [json.loads(line) for line in capture.read_text()[len(before.decode()):].splitlines()]
    assert not [row for row in calls if row["method"] == "turn/start"]
    assert not result.get("effects", {}).get("quota_spent")


@pytest.mark.parametrize("fault", ["busy", "queued", "revoked", "stopped_goal", "wrong_actor", "host_closed"])
def test_native_dispatch_refuses_before_model_or_kernel_when_original_context_is_unavailable(steward, tmp_path, monkeypatch, fault):
    sid, actor, _ = prepare(steward, "file", tmp_path, monkeypatch)
    store, runtime, provider, transport, binding, capture, _ = steward
    if fault == "busy":
        store.update_session(sid, active_turn_id="f" * 32)
    elif fault == "queued":
        store.create_queued_turn(sid, client_turn_id="human-followup", message="Keep the original user first", origin="web")
    elif fault == "revoked":
        provider.owner_ready = False
    elif fault == "stopped_goal":
        data = json.loads(runtime.registry_path.read_text())
        data["goals"][0]["activation"] = dict(STOPPED_ACTIVATION)
        runtime.registry_path.write_text(json.dumps(data))
    elif fault == "host_closed":
        runtime.close()
    else:
        data = transport.bindings.read()
        data["bindings"][-1]["managed_actor"]["source_ref"] = "0" * 24
        transport.bindings.path.write_text(json.dumps(data))
    before = capture.read_bytes()
    with pytest.raises((ValueError, KeyError)):
        StewardTurnHost(runtime).submit(binding["binding_id"], turn_instance_id="native-rejected")
    assert capture.read_bytes() == before


@pytest.mark.parametrize("fault", ["revoked", "retargeted", "closed_turn"])
def test_late_model_result_cannot_commit_after_the_original_source_changes(steward, tmp_path, monkeypatch, fault):
    sid, actor, added = prepare(steward, "sqlite", tmp_path, monkeypatch)
    store, runtime, provider, transport, binding, capture, _ = steward
    todo_id = added.get("todo_id") or added.get("todo", {})["todo_id"]
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    original_start = CodexAppServerAdapter.start_turn

    def late(adapter, message, sink):
        response = original_start(adapter, message, sink)
        if fault == "revoked":
            provider.owner_ready = False
        elif fault == "retargeted":
            current = transport.bindings.read()
            current["bindings"][-1]["managed_actor"]["source_ref"] = "0" * 24
            transport.bindings.path.write_text(json.dumps(current))
        else:
            active = store.load_session(sid)["active_turn_id"]
            store.update_turn(sid, active, status="interrupted")
        return response

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", late)
    host = StewardTurnHost(runtime)
    accepted, _ = host.submit(binding["binding_id"], turn_instance_id="late-original-source", todo_id=todo_id)
    completed = finish(runtime, accepted)
    result = completed.get("response", {}).get("native_loopx") if completed.get("response") else None
    if result:
        assert not result["ok"] and not result["effects"]["state_written"]
        assert not result["effects"]["quota_spent"]
    else:
        assert completed["status"] == "interrupted"
    before = capture.read_bytes()
    if fault == "retargeted":
        with pytest.raises(ValueError):
            host.submit(binding["binding_id"], turn_instance_id="late-original-source", todo_id=todo_id)
    else:
        assert not host.submit(binding["binding_id"], turn_instance_id="late-original-source", todo_id=todo_id)[1]
    assert capture.read_bytes() == before


def test_new_native_user_gate_wins_immediately_before_app_server_dispatch(steward, tmp_path, monkeypatch):
    sid, actor, added = prepare(steward, "sqlite", tmp_path, monkeypatch)
    store, runtime, _, _, binding, capture, _ = steward
    todo_id = added.get("todo_id") or added.get("todo", {})["todo_id"]
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    original_start = CodexAppServerAdapter.start_turn

    def gated(adapter, message, sink):
        cli(runtime, "todo", "add", "--goal-id", actor["goal_id"], "--role", "user",
            "--text", "Synthetic owner gate before model dispatch.", "--task-class", "user_gate",
            "--goal-bound", "--execute")
        return original_start(adapter, message, sink)

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", gated)
    before = capture.read_bytes()
    accepted, _ = StewardTurnHost(runtime).submit(binding["binding_id"],
        turn_instance_id="gate-before-native-dispatch", todo_id=todo_id)
    completed = finish(runtime, accepted)
    result = completed["response"]["native_loopx"]
    assert not result["ok"] and not result.get("effects", {}).get("quota_spent"), result
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].decode().splitlines()]
    assert not [row for row in calls if row["method"] == "turn/start"]


def test_inventory_replacement_during_context_read_never_forks_the_original_native_thread(steward, tmp_path, monkeypatch):
    sid, actor, _ = prepare(steward, "file", tmp_path, monkeypatch)
    store, runtime, _, _, binding, capture, _ = steward
    import loopx.chat.coordination as coordination
    prepare_context = coordination.prepare_turn_context

    def changed(*args, **kwargs):
        current = json.loads(runtime.registry_path.read_text())
        current["goals"][0]["creation_operation_id"] += ".replacement"
        runtime.registry_path.write_text(json.dumps(current))
        return prepare_context(*args, **kwargs)

    monkeypatch.setattr(coordination, "prepare_turn_context", changed)
    before = capture.read_bytes()
    thread = store.load_session(sid)["upstream_thread_id"]
    accepted, _ = StewardTurnHost(runtime).submit(binding["binding_id"], turn_instance_id="context-source-replaced")
    completed = finish(runtime, accepted)
    assert completed["status"] == "failed" and completed["error_code"] == "manager_authorization_unavailable"
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].decode().splitlines()]
    assert not [row for row in calls if row["method"] in {"thread/start", "turn/start", "thread/inject_items"}]
    assert store.load_session(sid)["upstream_thread_id"] == thread


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_managed_host_does_not_borrow_another_execution_lease(steward, tmp_path, monkeypatch, provider):
    service, session, actor, todo_id, _, capture = managed_fixture(steward, tmp_path, monkeypatch, provider)
    store, runtime, *_ = steward
    original = cli(runtime, "task-lease", "acquire", "--goal-id", actor["goal_id"],
        "--todo-id", todo_id, "--owner", actor["agent_id"], "--idempotency-key", "another-execution")["lease"]
    before_calls = capture.read_bytes()
    accepted, _ = service.submit(session["session_id"], agent_id=actor["agent_id"],
        operation_id="lease-conflict", todo_id=todo_id)
    failed = finish(runtime, accepted)
    assert failed["status"] == "failed"
    calls = [json.loads(row) for row in capture.read_bytes()[len(before_calls):].splitlines()]
    assert not [row for row in calls if row["method"] == "turn/start"]
    retained = cli(runtime, "task-lease", "inspect", "--goal-id", actor["goal_id"], "--todo-id", todo_id)
    assert retained["active"] is True and retained["lease"] == original
    assert store.load_session(session["session_id"])["upstream_thread_id"] == session["upstream_thread_id"]
