"""Goal creation reaches its original Agent conversation and Task owners.

The Chat/Core/providers are real; the app-server/model is synthetic. Explicit
owner checks below qualify the integration, not automatic checker production.
"""
import hashlib
import json
import runpy
import sys
from pathlib import Path

import pytest

from test_new_goal_storage_defaults import environment as environment, app as app
from test_goal_create_completion import preview_goal, inspect
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.native_chat.project_context import ChatProjectContexts
from loopx.capabilities.native_chat.steward_managed_mode import ManagedSessionTurn
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted


@pytest.fixture
def host(environment, app):
    _, _, _, project, runtime = environment
    actions, request, service = app
    capture, binary = runtime / "requests.jsonl", runtime / "synthetic-codex"
    source = runpy.run_path(str(Path(__file__).parents[2] / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
    binary.write_text(source.replace('    method = request.get("method")',
        f'    with open({str(capture)!r}, "a") as stream:\n        stream.write(json.dumps(request) + "\\n")\n'
        '    method = request.get("method")'))
    binary.chmod(0o700)
    chat = ChatSessionStore(runtime / "chat")
    controller = ChatRuntimeController(store=chat, codex_bin=str(binary), registry_path=service.registry_path,
        project_contexts=ChatProjectContexts([project]))
    service.chat_store, service.runtime_controller = chat, controller
    try:
        yield actions, request, controller, capture, project, runtime
    finally:
        controller.close()


def create(host):
    _, request, controller, *_ = host
    proposal = preview_goal(request)
    status, result = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 202, result
    ids = result["proposal"]["receipt"]["resource_ids"]
    first = controller.wait_for_turn(session_id=ids["session_id"], turn_id=ids["turn_id"], timeout_sec=10)
    assert first["status"] == "completed", first
    return proposal, ids, first


def test_creation_display_preserves_user_intent_and_the_internal_execution_request(host):
    proposal, ids, first = create(host)
    store = host[2].store
    assert store.messages(ids["session_id"])[0]["text"] == proposal["normalized_parameters"]["objective"]
    assert first["message"].startswith("开始推进 Goal")


def test_creation_display_recovery_retains_the_original_historical_message(host):
    from loopx.chat.session_open import goal_creation_display_message
    create(host)
    store, project = host[2].store, host[4]
    session, _ = host[2].open_session(goal_id="report", agent_id="codex", work_dir=project,
        objective="Historical objective", mode="new", channel_id="goal.report", agent_goal_id="report")
    turn, _ = store.create_turn(session["session_id"], client_turn_id="historical-first",
        message="Historical internal execution request", display_message="The original confirmed request")
    assert goal_creation_display_message(store, session_id=session["session_id"],
        client_turn_id="historical-first", objective="Later objective") == "The original confirmed request"
    assert store.load_turn(session["session_id"], turn["turn_id"])["message"] == "Historical internal execution request"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_first_goal_retains_ordinary_messages_in_the_registered_session(environment, host, provider):
    configure, *_ = environment
    configure(provider)
    _, request, controller, capture, project, runtime = host
    proposal, ids, first = create(host)
    chat, sid = controller.store, ids["session_id"]
    original = chat.load_session(sid)
    assert original["channel_id"] == "agent.codex" and original["managed_agent_id"] == "codex"
    assert original["managed_workspace_path"] == str(project)
    assert first.get("loopx_execution") is not True and "native_loopx" not in first["response"]
    assert controller.adapters[sid].session.sandbox == "read-only"
    history = chat.messages(sid)
    followup, created = controller.submit_turn(session_id=sid, client_turn_id="original-followup",
        message="Retain the introduction while preparing the appendix.", work_dir=project, objective="Deliver the report.")
    assert created
    completed = controller.wait_for_turn(session_id=sid, turn_id=followup["turn_id"], timeout_sec=10)
    assert completed["status"] == "completed" and "native_loopx" not in completed["response"]
    assert chat.messages(sid)[:len(history)] == history
    assert chat.load_session(sid)["upstream_thread_id"] == original["upstream_thread_id"]
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(row["method"] == "thread/start" for row in calls) == 1
    assert sum(row["method"] == "turn/start" for row in calls) == 2
    before_replay = capture.read_bytes()
    status, replay = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 202 and replay["proposal"]["receipt"]["resource_ids"] == ids
    assert capture.read_bytes() == before_replay and len(chat.list_sessions(goal_id="report")) == 1
    authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="report", include_leases=True)
    assert [(todo["todo_id"], todo["status"]) for todo in authority["todos"]] == [(ids["todo_ids"][0], "open")]
    assert inspect(project, runtime)["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["requirements"][0]["status"] == "unverified"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("window", ["simple", "competing", "historical", "historical_competing", "historical_missing_metadata"])
def test_lost_session_open_response_reuses_the_original_registered_route(environment, host, monkeypatch, provider, window):
    configure, *_ = environment
    configure(provider)
    actions, request, controller, capture, *_ = host
    proposal = preview_goal(request)
    open_session = controller.open_session
    opening = {}
    def lost_response(**kwargs):
        if window.startswith("historical"):
            kwargs.pop("managed_agent_id", None)
            kwargs.pop("session_id", None)
        session, _ = open_session(**kwargs)
        opening.update(session=session, arguments=kwargs)
        raise TimeoutError("Session opening response lost before the checkpoint")
    monkeypatch.setattr(controller, "open_session", lost_response)
    path = f"/api/actions/{proposal['proposal_id']}/apply"
    assert request(path, {})[0] == 424
    sid = controller.store.list_sessions(goal_id="report")[0]["session_id"]
    assert "first_session_opened" not in actions.load(proposal["proposal_id"])["checkpoint"]["steps"]
    monkeypatch.setattr(controller, "open_session", open_session)
    if window == "historical_missing_metadata":
        path_to_metadata = controller.store._session_path(sid)
        path_to_metadata.rename(path_to_metadata.with_suffix(".saved"))
    if window.endswith("competing"):
        arguments = {**opening["arguments"], "mode": "new"}
        arguments.pop("session_id", None)
        other, _ = open_session(**arguments)
        assert other["session_id"] != sid
    status, recovered = request(path, {})
    if window.startswith("historical"):
        assert status == 400 and ("ambiguous" if window == "historical_competing" else "unconfirmed") in recovered["error"], recovered
        assert controller.store.turn_for_client(sid, f"goal-start-{proposal['proposal_id']}") is None
        return
    assert status == 202, recovered
    ids = recovered["proposal"]["receipt"]["resource_ids"]
    assert ids["session_id"] == sid
    assert ("managed_agent_id" in ids) is not window.startswith("historical")
    assert controller.wait_for_turn(session_id=sid, turn_id=ids["turn_id"], timeout_sec=10)["status"] == "completed"
    assert len(controller.store.list_sessions(goal_id="report")) == (2 if window == "competing" else 1)
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(row["method"] == "thread/start" for row in calls) == (2 if window == "competing" else 1)
    assert sum(row["method"] == "turn/start" for row in calls) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_new_open_checks_the_persisted_session_before_certifying_its_route(environment, host, monkeypatch, provider):
    configure, *_ = environment
    configure(provider)
    actions, request, controller, capture, *_ = host
    proposal = preview_goal(request)
    open_session = controller.open_session
    original = {}
    def changed_before_response(**kwargs):
        session, resumed = open_session(**kwargs)
        original.update(session)
        stored = {**session, "channel_id": "goal.report", "managed_agent_id": None}
        stored.pop("managed_workspace_path", None)
        controller.store._session_path(session["session_id"]).write_text(json.dumps(stored))
        return session, resumed
    monkeypatch.setattr(controller, "open_session", changed_before_response)
    status, failed = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 400 and "Session identity changed" in failed["error"], failed
    assert "first_session_opened" not in actions.load(proposal["proposal_id"])["checkpoint"]["steps"]
    assert controller.store.turn_for_client(original["session_id"], f"goal-start-{proposal['proposal_id']}") is None
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert not [row for row in calls if row["method"] == "turn/start"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_explicit_native_budget_retains_the_legacy_goal_channel(environment, host, monkeypatch, provider):
    configure, *_ = environment
    configure(provider)
    actions, request, controller, capture, *_ = host
    proposal = preview_goal(request, native_token_budget=100)
    save = actions.save_checkpoint
    def interrupt(*args, **kwargs):
        result = save(*args, **kwargs)
        if kwargs.get("step") == "first_session_opened":
            raise OSError("Stop the synthetic host before its explicit /goal request")
        return result
    monkeypatch.setattr(actions, "save_checkpoint", interrupt)
    status, result = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 424, result
    sid = actions.load(proposal["proposal_id"])["checkpoint"]["steps"]["first_session_opened"]["session_id"]
    session = controller.store.load_session(sid)
    assert session["channel_id"] == "goal.report" and session.get("managed_agent_id") is None
    assert controller.store.turn_for_client(sid, f"goal-start-{proposal['proposal_id']}") is None
    assert not [json.loads(line) for line in capture.read_text().splitlines() if json.loads(line)["method"] == "turn/start"]


def test_concurrent_session_opening_reuses_one_published_identity_and_no_new_adapter_on_replay(host):
    from concurrent.futures import ThreadPoolExecutor

    _, _, controller, capture, project, _ = host
    create(host)
    secondary = ChatRuntimeController(store=controller.store, codex_bin=controller.codex_bin,
        registry_path=controller.registry_path, project_contexts=ChatProjectContexts([project]))
    arguments = dict(goal_id="report", agent_id="codex", managed_agent_id="codex",
        work_dir=project, objective="Deliver the report.", mode="new", session_id="concurrent-original")
    before = capture.read_bytes()
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            opened = list(workers.map(lambda runtime: runtime.open_session(**arguments), [controller, secondary]))
        assert [row[0]["session_id"] for row in opened] == ["concurrent-original", "concurrent-original"]
        assert sorted(row[1] for row in opened) == [False, True]
        assert opened[0][0] == opened[1][0]
        calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
        assert sum(row["method"] == "thread/start" for row in calls) == 1
        before_replay = capture.read_bytes()
        original = controller.store.load_session("concurrent-original")
        result, resumed = secondary.open_session(**arguments)
        assert resumed and result == original and capture.read_bytes() == before_replay
    finally:
        secondary.close()


@pytest.mark.parametrize("reservation", ["directory", "dangling-link", "metadata-link"])
def test_unknown_physical_session_reservation_never_starts_another_native_thread(host, reservation):
    _, _, controller, capture, project, _ = host
    create(host)
    directory = controller.store._session_dir("unknown-opening")
    if reservation == "directory":
        directory.mkdir()
    elif reservation == "dangling-link":
        directory.symlink_to(directory.parent / "missing-target", target_is_directory=True)
    else:
        directory.mkdir()
        controller.store._session_path("unknown-opening").symlink_to(directory.parent / "missing-metadata.json")
    before = capture.read_bytes()
    with pytest.raises(ValueError, match="unreadable|unavailable"):
        controller.open_session(goal_id="report", agent_id="codex", managed_agent_id="codex",
            work_dir=project, objective="Deliver the report.", mode="new", session_id="unknown-opening")
    assert capture.read_bytes() == before


def test_exact_session_opening_cannot_select_the_resume_latest_branch(host):
    _, _, controller, capture, project, _ = host
    create(host)
    before = capture.read_bytes()
    with pytest.raises(ValueError, match="mode=new"):
        controller.open_session(goal_id="report", agent_id="codex", managed_agent_id="codex",
            work_dir=project, objective="Deliver the report.", mode="resume_latest", session_id="another-opening")
    assert capture.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("legacy", [False, True], ids=["registered", "historical-legacy"])
def test_interrupted_first_session_recovers_its_recorded_route(environment, host, monkeypatch, provider, legacy):
    configure, *_ = environment
    configure(provider)
    actions, request, controller, capture, *_ = host
    proposal = preview_goal(request)
    save, open_session = actions.save_checkpoint, controller.open_session
    if legacy:
        def old_open(**kwargs):
            # Stop today's producer before opening, then restore a checkpoint
            # genuinely written by the historical unbound Session producer.
            raise OSError("Restore the original historical Session checkpoint")
        monkeypatch.setattr(controller, "open_session", old_open)
    def interrupt(*args, **kwargs):
        result = save(*args, **kwargs)
        if kwargs.get("step") == "first_session_opened":
            raise OSError("Interrupted before dispatch")
        return result
    monkeypatch.setattr(actions, "save_checkpoint", interrupt)
    path = f"/api/actions/{proposal['proposal_id']}/apply"
    assert request(path, {})[0] == 424
    if legacy:
        project = host[4]
        session, _ = open_session(goal_id="report", agent_id="codex", work_dir=project,
            objective="Deliver the report.", mode="new", channel_id="goal.report", agent_goal_id="report")
        save(proposal["proposal_id"], step="first_session_opened",
            receipt={"outcome": "first_session_opened", "session_id": session["session_id"]})
    recorded = actions.load(proposal["proposal_id"])["checkpoint"]["steps"]["first_session_opened"]
    sid = recorded["session_id"]
    original = controller.store.load_session(sid)
    assert original["channel_id"] == ("goal.report" if legacy else "agent.codex")
    monkeypatch.setattr(actions, "save_checkpoint", save)
    monkeypatch.setattr(controller, "open_session", open_session)
    status, resumed = request(path, {})
    assert status == 202, resumed
    ids = resumed["proposal"]["receipt"]["resource_ids"]
    assert ids["session_id"] == sid
    turn = controller.wait_for_turn(session_id=sid, turn_id=ids["turn_id"], timeout_sec=10)
    assert turn["status"] == "completed"
    assert ("managed_agent_id" in ids) is not legacy
    assert controller.store.load_session(sid)["channel_id"] == original["channel_id"]
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert sum(row["method"] == "thread/start" for row in calls) == 1
    assert sum(row["method"] == "turn/start" for row in calls) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("field,value", [("channel_id", "goal.report"), ("managed_agent_id", "other-owner"),
    ("goal_instance_id", "replaced-lifetime"), ("managed_workspace_path", "/unavailable-workspace")])
def test_recovery_refuses_a_changed_original_session_route(environment, host, monkeypatch, provider, field, value):
    configure, *_ = environment
    configure(provider)
    actions, request, controller, capture, *_ = host
    proposal = preview_goal(request)
    save = actions.save_checkpoint
    def interrupt(*args, **kwargs):
        result = save(*args, **kwargs)
        if kwargs.get("step") == "first_session_opened":
            raise OSError("Interrupted before dispatch")
        return result
    monkeypatch.setattr(actions, "save_checkpoint", interrupt)
    path = f"/api/actions/{proposal['proposal_id']}/apply"
    assert request(path, {})[0] == 424
    monkeypatch.setattr(actions, "save_checkpoint", save)
    sid = actions.load(proposal["proposal_id"])["checkpoint"]["steps"]["first_session_opened"]["session_id"]
    session = controller.store.load_session(sid)
    controller.store._session_path(sid).write_text(json.dumps({**session, field: value}))
    before = capture.read_bytes()
    status, failed = request(path, {})
    assert status == 400, failed
    assert "Session" in failed["error"]
    assert capture.read_bytes() == before and len(controller.store.list_sessions(goal_id="report")) == 1
    assert controller.store.turn_for_client(sid, f"goal-start-{proposal['proposal_id']}") is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("checks", ["valid", "missing", "failed"])
def test_created_session_adopts_original_task_only_with_independent_owner_check(environment, host, provider, checks):
    from loopx.control_plane.goals.acceptance import configure_goal_acceptance

    configure, *_ = environment
    configure(provider)
    _, _, controller, capture, project, runtime = host
    _, ids, _ = create(host)
    sid, todo = ids["session_id"], ids["todo_ids"][0]
    original = controller.store.load_session(sid)
    history = controller.store.messages(sid)
    artifact, checker = project / "report.txt", project / "check-report.py"
    expected = "Introduction\nAppendix\n"
    checker.write_text("from pathlib import Path\nassert Path('report.txt').read_text() == 'Introduction\\nAppendix\\n'\n")
    if checks != "missing":
        basis = inspect(project, runtime)
        configure_goal_acceptance(registry_path=controller.registry_path, runtime_root=str(runtime), goal_id="report",
            expected_provider_revision=basis["provider_revision"], execute=True,
            document={"scope": {"kind": "selected_work", "todo_ids": [todo]}, "objective": "Deliver both report sections",
                "non_goals": [], "criteria": [{"id": "complete-report", "description": "Both approved sections are present",
                    "validation_argv": [sys.executable, "check-report.py"], "validation_timeout_seconds": 5,
                    "validation_files": [{"path": "check-report.py", "sha256": hashlib.sha256(checker.read_bytes()).hexdigest()}]}],
                "bindings": [{"todo_id": todo, "criterion_ids": ["complete-report"]}]})
    candidate = {"result_kind": "validated_completion", "classification": "implementation",
        "summary": "The report includes both sections.", "next_action": "Read back the original report.",
        "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
        "vision_unchanged_reason": "The original approved report remains the current task.",
        "work_evidence": ["report.txt"], "validation_evidence": ["The independent checker reads both original sections"]}
    binary = Path(controller.codex_bin)
    source = binary.read_text().replace('"message": "Runtime response."', '"message": ' + repr(json.dumps(candidate)))
    artifact_content = expected if checks != "failed" else "Introduction\n"
    source = source.replace('        turn_count += 1',
        f'        open({str(artifact)!r}, "w").write({artifact_content!r})\n        turn_count += 1')
    binary.write_text(source)
    controller.adapters.pop(sid).close_session()
    before = capture.read_bytes()
    native = ManagedSessionTurn(controller)
    accepted, created = native.submit(sid, agent_id="codex", operation_id="original-task", todo_id=todo)
    assert created
    completed = controller.wait_for_turn(session_id=sid, turn_id=accepted["turn_id"], timeout_sec=15)
    result = completed["response"]["native_loopx"]
    assert result["status"] == {"valid": "committed", "missing": "stopped", "failed": "failed"}[checks], result
    assert result["effects"]["quota_spent"] is (checks == "valid")
    assert result["effects"]["state_written"] is (checks == "valid")
    authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="report", include_leases=True)
    assert next(row for row in authority["todos"] if row["todo_id"] == todo)["status"] == ("done" if checks == "valid" else "open")
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].splitlines()]
    assert sum(row["method"] == "turn/start" for row in calls) == (0 if checks == "missing" else 1)
    assert not [row for row in calls if row["method"] == "thread/start"]
    assert controller.store.messages(sid)[:len(history)] == history
    assert controller.store.load_session(sid)["upstream_thread_id"] == original["upstream_thread_id"]
    assert len(controller.store.list_sessions(goal_id="report")) == 1
    after = capture.read_bytes()
    replay, created = native.submit(sid, agent_id="codex", operation_id="original-task", todo_id=todo)
    assert not created and replay["turn_id"] == accepted["turn_id"] and capture.read_bytes() == after
