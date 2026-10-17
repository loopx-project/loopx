"""Agent preparation -> one local-owner check; real File/SQLite authority."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from loopx.chat_actions import ChatActionService
from loopx.chat_action_store import ActionConflictError, ChatActionStore
from loopx.control_plane.goals import acceptance_actions
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal


@pytest.fixture
def conversation_check(acceptance_goal):
    """An admitted conversation tool over disposable, real File/SQLite owners."""
    from loopx.chat_loopx_mode import TOOL
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore

    project, document, cli, _ = acceptance_goal
    root, registry = project.parent / "runtime", project.parent / "registry.json"
    payload = json.loads(registry.read_text())
    payload["goals"][0]["coordination"] = {
        "agent_model": "peer_v1", "registered_agents": ["coordinator", "agent-a"],
    }
    payload["goals"][0]["spawn_policy"] = {
        "mode": "multi_subagent", "allowed": True, "max_children": 1,
        "execution_config": ".loopx/config/delegations.json",
    }
    registry.write_text(json.dumps(payload))
    config = project / ".loopx/config/delegations.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"schema_version": "loopx_local_delegation_v0", "bindings": [{
        "id": "delivery", "agent_id": "agent-a", "todo_id": "todo_export", "requesters": ["coordinator"],
        "workspace": str(project), "host_args": ["--host", "dsh"],
        "timeout_seconds": 60, "output_refs": ["artifact.txt"],
    }]}))
    plan = json.loads(document.read_text())
    plan["criteria"][0]["validation_argv"] = ["python3", "check.py"]
    (project / "check.py").write_text(
        "from pathlib import Path\n"
        "p=Path('runs.txt')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        "assert Path('artifact.txt').read_text() == 'accepted'\n"
    )
    (project / "artifact.txt").write_text("accepted")
    store = ChatSessionStore(root)
    controller = ChatRuntimeController(store=store, codex_bin="unused", registry_path=registry)
    session = store.create_session(goal_id="goal-acceptance", agent_id="codex",
        channel_id="goal.goal-acceptance", upstream_thread_id="fixture", upstream_mode="chat",
        adapter_kind="codex_app_server")
    sid, turn = session["session_id"], "admitted-tool-turn"
    store.update_session(sid, active_turn_id=turn, loopx_mode={"enabled": True,
        "paused": False, "settings": {"agent_id": "coordinator", "token_budget": 10000}})
    adapter = SimpleNamespace(goal_driver=SimpleNamespace(stopped=Event()), session=SimpleNamespace())
    lock = controller.loopx_mode.prepare(sid, turn, adapter, lambda *_: {"read": True}, lambda *_: None)
    def call(**extra):
        return adapter.session.read_tool_handler(TOOL["name"], {
            "action": "prepare_check", "operation_id": "conversation-check", "document": plan, **extra})
    try:
        yield project, cli, controller, sid, plan, call, adapter
    finally:
        lock.__exit__(None, None, None)
        controller.close()


def test_conversation_prepares_existing_owner_plan_without_execution(conversation_check):
    project, cli, controller, _, plan, call, _ = conversation_check
    before = cli("inspect")[1]
    result = call()
    assert result["ok"] is True, result
    assert result["prepared"] is True and result["executed"] is False
    proposal = result["proposal"]
    assert proposal["context"] == {"kind": "goal", "goal_id": "goal-acceptance"}
    assert proposal["normalized_parameters"]["prepared_by"] == "coordinator"
    assert proposal["normalized_parameters"]["document"] == plan
    assert call()["proposal"]["proposal_id"] == proposal["proposal_id"]
    assert cli("inspect")[1] == before
    assert not (project / "runs.txt").exists()
    service = ChatActionService(store=ChatActionStore(project.parent / "runtime/chat/actions"),
                               registry_path=controller.registry_path)
    applied = service.apply(proposal["proposal_id"])
    assert applied["proposal"]["receipt"]["checks_passed"] is True
    assert (project / "runs.txt").read_text() == "x"
    assert service.apply(proposal["proposal_id"]) == applied
    assert (project / "runs.txt").read_text() == "x"


@pytest.mark.parametrize("extra", [
    {"goal_id": "other-goal"}, {"agent_id": "other-agent"},
    {"runtime_root": "other-runtime"}, {"binding_id": "delivery"},
    {"operation_id": ""}, {"document": {}}, {"action": "bindings"},
])
def test_conversation_check_rejects_invalid_scope_without_effect(conversation_check, extra):
    project, cli, _, _, _, call, _ = conversation_check
    before = cli("inspect")[1]
    assert call(**extra)["error"] == "collaboration_request_rejected"
    assert ChatActionStore(project.parent / "runtime/chat/actions").list() == []
    assert cli("inspect")[1] == before
    assert not (project / "runs.txt").exists()


@pytest.mark.parametrize("fence", ["disabled", "paused", "other_turn", "stopped", "revoked"])
def test_conversation_check_rechecks_live_admission(conversation_check, fence):
    project, cli, controller, sid, _, call, adapter = conversation_check
    before = cli("inspect")[1]
    if fence == "stopped":
        adapter.goal_driver.stopped.set()
    elif fence == "other_turn":
        controller.store.update_session(sid, active_turn_id="later-turn")
    elif fence == "revoked":
        (project / ".loopx/config/delegations.json").unlink()
    else:
        mode = controller.store.load_session(sid)["loopx_mode"]
        mode["enabled" if fence == "disabled" else "paused"] = fence != "disabled"
        controller.store.update_session(sid, loopx_mode=mode)
    result = call()
    assert result["error"] == (
        "collaboration_request_rejected" if fence == "revoked" else "conversation_execution_inactive"
    )
    assert ChatActionStore(project.parent / "runtime/chat/actions").list() == []
    assert cli("inspect")[1] == before
    assert not (project / "runs.txt").exists()


def test_conversation_check_keeps_original_intent_on_conflicting_retry(conversation_check):
    project, cli, _, _, plan, call, _ = conversation_check
    before = cli("inspect")[1]
    proposal = call()["proposal"]
    changed = json.loads(json.dumps(plan))
    changed["objective"] = "A different requested outcome"
    refused = call(document=changed)
    assert refused["error"] == "collaboration_request_rejected"
    assert "saved check plan" in refused["next_action"]
    assert ChatActionStore(project.parent / "runtime/chat/actions").list() == [proposal]
    assert call()["proposal"] == proposal
    assert cli("inspect")[1] == before
    assert not (project / "runs.txt").exists()


def test_conversation_check_returns_document_repair_context(conversation_check):
    project, cli, _, _, plan, call, _ = conversation_check
    before = cli("inspect")[1]
    invalid = json.loads(json.dumps(plan))
    invalid["objective"] = ""
    refused = call(document=invalid)
    assert refused["error"] == "collaboration_request_rejected"
    assert "acceptance owner rejected" in refused["next_action"]
    assert "objective, scope, criteria, bindings" in refused["next_action"]
    assert ChatActionStore(project.parent / "runtime/chat/actions").list() == []
    assert cli("inspect")[1] == before
    assert not (project / "runs.txt").exists()


@pytest.mark.parametrize("state", ["preview_ready", "applied", "cancelled"])
def test_preparation_retry_recovers_original_state_after_progress(conversation_check, state):
    project, cli, controller, _, _, call, _ = conversation_check
    original = call()["proposal"]  # The reply may be lost after persistence.
    store = ChatActionStore(project.parent / "runtime/chat/actions")
    service = ChatActionService(store=store, registry_path=controller.registry_path)
    if state == "applied":
        original = service.apply(original["proposal_id"])["proposal"]
    elif state == "cancelled":
        original = service.cancel(original["proposal_id"])
    code, updated = run_json_cli_result("todo", "update", "--goal-id", "goal-acceptance",
        "--todo-id", "todo_export", "--agent-id", "agent-a", "--note", "Work is progressing",
        registry_path=controller.registry_path, runtime_root=project.parent / "runtime")
    assert code == 0, updated
    authority, journal = cli("inspect")[1], store.path.read_bytes()
    recovered = call()
    assert recovered["ok"] is True and recovered["executed"] is False, recovered
    assert recovered["proposal"] == original
    assert cli("inspect")[1] == authority and store.path.read_bytes() == journal
    if state == "applied":
        assert (project / "runs.txt").read_text() == "x"
    else:
        assert not (project / "runs.txt").exists()
    if state == "preview_ready":
        applied = service.apply(original["proposal_id"])["proposal"]
        assert call()["proposal"] == applied
        assert service.apply(original["proposal_id"])["proposal"] == applied
        assert (project / "runs.txt").read_text() == "x"


def test_preparation_retry_does_not_return_another_preparers_plan(conversation_check):
    project, cli, controller, _, plan, call, _ = conversation_check
    original = call()["proposal"]
    store = ChatActionStore(project.parent / "runtime/chat/actions")
    authority, journal = cli("inspect")[1], store.path.read_bytes()
    with pytest.raises(ActionConflictError):
        acceptance_actions.prepare_acceptance_check(registry_path=controller.registry_path,
            runtime_root=str(project.parent / "runtime"), goal_id="goal-acceptance",
            agent_id="agent-a", document=plan, operation_id=original["idempotency_key"], execute=True)
    assert cli("inspect")[1] == authority and store.path.read_bytes() == journal
    assert not (project / "runs.txt").exists()


def test_preparation_retry_does_not_replace_unavailable_authority(conversation_check, monkeypatch):
    project, cli, _, _, _, call, _ = conversation_check
    call()
    store = ChatActionStore(project.parent / "runtime/chat/actions")
    authority, journal = cli("inspect")[1], store.path.read_bytes()

    def unavailable(**_kwargs):
        raise ValueError("Goal acceptance authority is unavailable")

    monkeypatch.setattr(acceptance_actions, "inspect_goal_acceptance", unavailable)
    assert call()["error"] == "collaboration_request_rejected"
    assert cli("inspect")[1] == authority and store.path.read_bytes() == journal
    assert not (project / "runs.txt").exists()


def test_prepare_recovers_competing_commit_with_a_newer_basis(conversation_check, monkeypatch):
    project, cli, controller, _, _, call, _ = conversation_check
    entered, proceed, requests = Event(), Event(), []
    native_preview = ChatActionService.preview

    def held_preview(service, request):
        requests.append(request)
        entered.set()
        assert proceed.wait(20)
        return native_preview(service, request)

    monkeypatch.setattr(ChatActionService, "preview", held_preview)
    store = ChatActionStore(project.parent / "runtime/chat/actions")
    service = ChatActionService(store=store, registry_path=controller.registry_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(call)
        try:
            assert entered.wait(20)
            winner = native_preview(service, requests[0])
            code, updated = run_json_cli_result("todo", "update", "--goal-id", "goal-acceptance",
                "--todo-id", "todo_export", "--agent-id", "agent-a", "--note", "Concurrent progress",
                registry_path=controller.registry_path, runtime_root=project.parent / "runtime")
            assert code == 0, updated
            authority, journal = cli("inspect")[1], store.path.read_bytes()
        finally:
            proceed.set()
        recovered = pending.result(timeout=20)
    assert recovered["proposal"] == winner, recovered
    assert cli("inspect")[1] == authority and store.path.read_bytes() == journal
    assert not (project / "runs.txt").exists()


def prepare(acceptance_goal):
    project, document, cli, _ = acceptance_goal
    raw = json.loads(document.read_text())
    # The Agent prepares the public-safe runtime alias, never a machine path.
    raw["criteria"][0]["validation_argv"] = ["python3", "check.py"]
    (project / "check.py").write_text(
        "from pathlib import Path\n"
        "p=Path('runs.txt')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        "assert Path('artifact.txt').read_text() == 'accepted'\n"
    )
    document.write_text(json.dumps(raw))
    (project / "artifact.txt").write_text("accepted")
    code, result = cli("prepare", "--agent-id", "agent-a", "--document", str(document),
                       "--operation-id", "prepare-check", "--execute")
    assert code == 0, result
    root = project.parent
    service = ChatActionService(store=ChatActionStore(root / "runtime/chat/actions"),
                                registry_path=root / "registry.json")
    return project, cli, service, result["proposal"]


@pytest.mark.parametrize("change", ["selected_note", "unselected_work"])
def test_reviewed_selected_plan_survives_unrelated_progress(acceptance_goal, change):
    project, document, cli, run = acceptance_goal
    raw = json.loads(document.read_text())
    raw["scope"] = {"kind": "selected_work", "todo_ids": ["todo_export"]}
    document.write_text(json.dumps(raw))
    _, _, service, proposal = prepare(acceptance_goal)
    if change == "selected_note":
        code, result = run("todo", "update", "--goal-id", "goal-acceptance",
            "--todo-id", "todo_export", "--agent-id", "agent-a", "--note", "Work is progressing")
    else:
        code, result = run("todo", "add", "--goal-id", "goal-acceptance", "--role", "agent",
            "--text", "Independent work", "--action-kind", "implement", "--claimed-by", "agent-a")
    assert code == 0, result
    assert cli("inspect")[1]["goal_acceptance_contract"] == {"enabled": False}
    assert not (project / "runs.txt").exists()
    result = service.apply(proposal["proposal_id"])
    assert result["proposal"]["status"] == "applied"
    assert (project / "runs.txt").read_text() == "x"
    assert service.apply(proposal["proposal_id"])["proposal"] == result["proposal"]
    assert (project / "runs.txt").read_text() == "x"


def test_historical_revision_only_plan_keeps_strict_admission(acceptance_goal):
    project, _, _, run = acceptance_goal
    _, _, service, proposal = prepare(acceptance_goal)
    historical = service.store.create_preview(
        **{key: proposal[key] for key in (
            "action_kind", "summary", "normalized_parameters", "context",
            "permission_classification", "validation_evidence", "available_transitions")},
        expected_state_fingerprint=json.loads(proposal["expected_state_fingerprint"])["provider_revision"],
        idempotency_key="historical-revision-only-plan",
    )
    code, result = run("todo", "update", "--goal-id", "goal-acceptance", "--todo-id", "todo_export",
        "--agent-id", "agent-a", "--note", "Progress after a historical plan")
    assert code == 0, result
    assert service.apply(historical["proposal_id"])["proposal"]["status"] == "stale"
    assert not (project / "runs.txt").exists()


def test_reviewed_plan_rejects_changed_covered_work(acceptance_goal):
    project, document, cli, run = acceptance_goal
    _, _, service, proposal = prepare(acceptance_goal)
    code, changed = run("todo", "update", "--goal-id", "goal-acceptance", "--todo-id", "todo_export",
        "--agent-id", "agent-a", "--text", "Deliver a different export")
    assert code == 0, changed
    code, recovered = cli("prepare", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "prepare-check", "--execute")
    assert code == 0 and recovered["proposal"] == proposal
    assert service.apply(proposal["proposal_id"])["proposal"]["status"] == "stale"
    code, recovered = cli("prepare", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "prepare-check", "--execute")
    assert code == 0 and recovered["proposal"]["status"] == "stale"
    assert not (project / "runs.txt").exists()


@pytest.mark.parametrize("changed", [False, True])
def test_configuration_response_loss_keeps_its_original_review_basis(acceptance_goal, monkeypatch, changed):
    project, document, cli, run = acceptance_goal
    _, _, service, proposal = prepare(acceptance_goal)
    original = service.store.save_checkpoint

    def lost(*args, **kwargs):
        if kwargs["step"] == "acceptance_configured":
            raise RuntimeError("lost configured checkpoint")
        return original(*args, **kwargs)

    monkeypatch.setattr(service.store, "save_checkpoint", lost)
    with pytest.raises(RuntimeError, match="lost"):
        service.apply(proposal["proposal_id"])
    monkeypatch.setattr(service.store, "save_checkpoint", original)
    if changed:
        before = cli("inspect")[1]
        code, result = cli("configure", "--document", str(document),
            "--expected-provider-revision", before["provider_revision"],
            "--operation-id", "new-owner-configuration", "--execute")
    else:
        code, result = run("todo", "update", "--goal-id", "goal-acceptance", "--todo-id", "todo_export",
            "--agent-id", "agent-a", "--note", "Progress after the response was lost")
    assert code == 0, result
    if changed:
        before = cli("inspect")[1]
        with pytest.raises(ActionConflictError, match="basis changed"):
            service.apply(proposal["proposal_id"])
        assert cli("inspect")[1] == before
        assert not (project / "runs.txt").exists()
    else:
        assert service.apply(proposal["proposal_id"])["proposal"]["status"] == "applied"
        assert (project / "runs.txt").read_text() == "x"


def test_agent_preparation_does_not_approve_or_execute(acceptance_goal):
    project, cli, service, proposal = prepare(acceptance_goal)
    assert cli("inspect")[1]["goal_acceptance_contract"] == {"enabled": False}
    assert not (project / "runs.txt").exists()
    assert proposal["permission_classification"] == "local_command_execution"
    result = service.apply(proposal["proposal_id"])
    assert result["proposal"]["status"] == "applied"
    assert result["proposal"]["receipt"]["checks_passed"] is True
    assert (project / "runs.txt").read_text() == "x"
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "accepted"
    assert service.apply(proposal["proposal_id"]) == result
    assert (project / "runs.txt").read_text() == "x"


def test_concurrent_apply_consumes_one_prepared_intent(acceptance_goal):
    project, _, service, proposal = prepare(acceptance_goal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.apply(proposal["proposal_id"]), range(2)))
    assert all(r["proposal"]["status"] == "applied" for r in results)
    assert (project / "runs.txt").read_text() == "x"


@pytest.mark.parametrize("lost_step", ["acceptance_configured", "native_check_response"])
def test_lost_response_recovers_native_receipt_without_rerunning(
    acceptance_goal, monkeypatch, lost_step,
):
    project, _, service, proposal = prepare(acceptance_goal)
    original_save = service.store.save_checkpoint
    original_verify = acceptance_actions.verify_goal_acceptance
    if lost_step == "acceptance_configured":
        def lose_configuration(*args, **kwargs):
            if kwargs["step"] == lost_step:
                raise RuntimeError("lost configuration response")
            return original_save(*args, **kwargs)
        monkeypatch.setattr(service.store, "save_checkpoint", lose_configuration)
    else:
        def lose_native_receipt(**kwargs):
            original_verify(**kwargs)
            raise RuntimeError("lost check response")
        monkeypatch.setattr(acceptance_actions, "verify_goal_acceptance", lose_native_receipt)
    with pytest.raises(RuntimeError, match="lost"):
        service.apply(proposal["proposal_id"])
    monkeypatch.setattr(service.store, "save_checkpoint", original_save)
    monkeypatch.setattr(acceptance_actions, "verify_goal_acceptance", original_verify)
    result = service.apply(proposal["proposal_id"])
    assert result["proposal"]["status"] == "applied"
    assert (project / "runs.txt").read_text() == "x"


def test_missing_native_receipt_does_not_authorize_automatic_retry(acceptance_goal, monkeypatch):
    project, cli, service, proposal = prepare(acceptance_goal)
    def interrupted(**_kwargs):
        raise RuntimeError("host interrupted after execution fence")
    monkeypatch.setattr(acceptance_actions, "verify_goal_acceptance", interrupted)
    with pytest.raises(RuntimeError):
        service.apply(proposal["proposal_id"])
    with pytest.raises(ActionConflictError, match="unconfirmed"):
        service.apply(proposal["proposal_id"])
    assert not (project / "runs.txt").exists()
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "unverified"


def test_stale_prepared_plan_cannot_replace_new_owner_configuration(acceptance_goal):
    project, document, cli, _ = acceptance_goal
    _, _, service, proposal = prepare(acceptance_goal)
    before = cli("inspect")[1]
    code, configured = cli("configure", "--document", str(document),
        "--expected-provider-revision", before["provider_revision"],
        "--operation-id", "another-owner-change", "--execute")
    assert code == 0, configured
    code, recovered = cli("prepare", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "prepare-check", "--execute")
    assert code == 0 and recovered["proposal"] == proposal
    result = service.apply(proposal["proposal_id"])
    assert result["proposal"]["status"] == "stale"
    assert not (project / "runs.txt").exists()


def test_prepare_dry_run_and_unknown_agent_do_not_write(acceptance_goal):
    project, document, cli, _ = acceptance_goal
    before = cli("inspect")[1]
    code, preview = cli("prepare", "--agent-id", "agent-a", "--document", str(document),
                        "--operation-id", "read-only-preparation")
    assert code == 0 and preview["executed"] is False and preview["prepared"] is False
    assert cli("inspect")[1] == before
    assert not (project.parent / "runtime/chat/actions").exists()
    code, rejected = cli("prepare", "--agent-id", "unknown", "--document", str(document),
                         "--operation-id", "unknown-preparation", "--execute")
    assert code != 0 and rejected["ok"] is False
    assert cli("inspect")[1] == before


def test_check_failure_is_recorded_without_goal_completion(acceptance_goal):
    project, cli, service, proposal = prepare(acceptance_goal)
    (project / "artifact.txt").write_text("not accepted")
    applied = service.apply(proposal["proposal_id"])["proposal"]
    assert applied["status"] == "applied"
    assert applied["receipt"]["checks_passed"] is False
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "failed"
    assert (project / "runs.txt").read_text() == "x"


def test_prepared_plan_cannot_claim_another_goal_context(acceptance_goal):
    project, cli, service, proposal = prepare(acceptance_goal)
    with pytest.raises(ValueError, match="context must match"):
        service.preview({"action_kind": "goal.acceptance.check", "summary": "Wrong context",
            "idempotency_key": "wrong-context", "context": {"kind": "goal", "goal_id": "other"},
            "normalized_parameters": proposal["normalized_parameters"]})
    assert cli("inspect")[1]["goal_acceptance_contract"]["enabled"] is False
    assert not (project / "runs.txt").exists()
