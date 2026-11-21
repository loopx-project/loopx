"""Real CLI and conversation effects; scripted callers are not model evidence."""
import json
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from threading import Event, Thread, get_ident

import pytest

from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from test_goal_acceptance_prepared_action import conversation_check as conversation_check
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService


def prepare_source(project):
    state = project / "state.md"
    state.write_text(state.read_text() + "\n## Objective\nBootstrap the project\n")
    path = project.parent / "registry.json"
    registry = json.loads(path.read_text())
    registry["goals"][0]["coordination"]["registered_agents"] = ["agent-a", "agent-b"]
    path.write_text(json.dumps(registry))


def apply_objective(project, cli, objective):
    document = {"objective": objective, "reason": "Keep the current direction explicit", "source_ref": "message:owner"}
    path = project / "objective.json"
    path.write_text(json.dumps(document))
    code, prepared = cli("prepare-objective", "--agent-id", "agent-a", "--document", str(path),
        "--operation-id", "prepare-guidance", "--execute")
    assert code == 0 and prepared["proposal"]["status"] == "preview_ready", prepared
    service = ChatActionService(
        store=ChatActionStore(project.parent / "runtime" / "chat" / "actions"),
        registry_path=project.parent / "registry.json",
    )
    assert service.apply(prepared["proposal"]["proposal_id"])["proposal"]["status"] == "applied"


@pytest.mark.parametrize("peer_id", ["later-peer", "later@host"])
def test_later_registered_peer_is_visible_and_requires_its_own_adoption(acceptance_goal, peer_id):
    project, _, cli, _ = acceptance_goal
    prepare_source(project)
    apply_objective(project, cli, "Maintain the accepted export")
    before = cli("inspect")[1]
    registry_path = project.parent / "registry.json"
    registry = json.loads(registry_path.read_text())
    registry["goals"][0]["coordination"]["registered_agents"].append(peer_id)
    registry_path.write_text(json.dumps(registry))

    code, inspected = cli("inspect", "--agent-id", peer_id)
    assert code == 0, inspected
    assert inspected["provider_revision"] == before["provider_revision"], "inspection cannot persist membership or adoption"
    assert inspected["goal_objective_guard"]["allowed"] is False
    objective = inspected["goal_acceptance_contract"]["goal_objective"]
    assert objective["digest"] == before["goal_acceptance_contract"]["goal_objective"]["digest"]
    peer = next((row for row in objective["peer_adoption"] if row["agent_id"] == peer_id), None)
    assert peer == {"agent_id": peer_id, "revision": None, "current": False}
    assert any(row["agent_id"] == peer_id for row in objective["acceptance_gaps"])
    assert cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["peer_adoption"] == objective["peer_adoption"]
    adoption = project / "later-adoption.json"
    adoption.write_text(json.dumps({"revision": objective["revision"], "digest": objective["digest"],
        "work_digest": inspected["objective_work_digest"], "review_summary": "Reviewed my current work against the shared objective."}))
    code, adopted = cli("adopt-objective", "--agent-id", peer_id, "--document", str(adoption),
        "--operation-id", "later-peer-adoption", "--expected-provider-revision", inspected["provider_revision"], "--execute")
    assert code == 0, adopted
    current = cli("inspect", "--agent-id", peer_id)[1]
    assert current["goal_objective_guard"] is None
    assert next(row for row in current["goal_acceptance_contract"]["goal_objective"]["peer_adoption"]
        if row["agent_id"] == peer_id)["current"] is True


@pytest.mark.parametrize("acceptance_kind", ["configured-checks", "cumulative-outcome"])
def test_objective_and_acceptance_guidance_share_real_inline_goal_read(acceptance_goal, acceptance_kind):
    project, acceptance_file, cli, run = acceptance_goal
    prepare_source(project)
    apply_objective(project, cli, "Maintain the export with complete acceptance")
    current = cli("inspect")[1]
    if acceptance_kind == "configured-checks":
        code, configured = cli("configure", "--document", str(acceptance_file), "--operation-id", "guidance-checks",
            "--expected-provider-revision", current["provider_revision"], "--execute")
        assert code == 0 and configured["status"] == "applied", configured
    else:
        outcome = {"outcome_id": "tour", "owner_agent_id": "agent-a",
            "objective": "Maintain the export with complete acceptance",
            "requirements": [{"id": "export", "description": "Retain the exported artifact"}],
            "todo_ids": ["todo_export"],
            "source": {"ref": "message:outcome", "summary": "The Goal requires the export artifact"}}
        outcome_path = project / "outcome.json"
        outcome_path.write_text(json.dumps(outcome))
        current = cli("inspect")[1]
        code, refined = cli("refine", "--agent-id", "agent-a", "--document", str(outcome_path),
            "--operation-id", "guidance-outcome", "--expected-provider-revision",
            current["provider_revision"], "--execute")
        assert code == 0 and refined["status"] == "applied", refined

    before = cli("inspect", "--agent-id", "agent-a")[1]
    objective = before["goal_acceptance_contract"]["goal_objective"]
    adoption_path = project / "adoption.json"
    adoption_path.write_text(json.dumps({"revision": objective["revision"], "digest": objective["digest"],
        "work_digest": before["objective_work_digest"],
        "review_summary": "Reviewed the export and current acceptance before adopting the revision."}))
    code, adopted = cli("adopt-objective", "--agent-id", "agent-a", "--document", str(adoption_path),
        "--operation-id", "guidance-adoption", "--expected-provider-revision",
        before["provider_revision"], "--execute")
    assert code == 0, adopted

    admission = run("quota", "should-run", "--codex-app", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")[1]
    context = admission["interaction_contract"]["agent_channel"]["work_context"]
    source = next(row for row in context["sources"] if row["source"] == "goal_acceptance")
    reason = source["reason"]
    assert "revision/digest" in reason and "own objective_work_digest" in reason
    required = [
        clause for clause in (
            ("cumulative requirements", "switching work", "Agent review", "Scoped acceptance never replaces Goal intent")
            if acceptance_kind == "cumulative-outcome"
            else ("non-goals", "criteria", "scope/revision", "Scoped acceptance does not replace Goal intent")
        )
    ]
    assert all(clause in reason for clause in required), reason
    reads = admission["interaction_contract"]["agent_channel"]["required_reads"]
    assert not any(read["source"] == "goal_acceptance" for read in reads), "one inline read should fulfill the command"
    assert "prepare-objective" not in json.dumps([context, reads]), (
        "ordinary reads must not proactively direct objective revision")


@pytest.mark.parametrize(("field", "value"), [
    ("reason", "Explicit current direction"),
    ("source_ref", "message:current"),
])
def test_same_objective_with_changed_provenance_prepares_a_distinct_proposal(acceptance_goal, field, value):
    project, _, cli, _ = acceptance_goal
    prepare_source(project)
    document = project / "objective.json"
    objective = "Maintain reliable exports and recovery"
    original = {"objective": objective, "reason": "Earlier direction", "source_ref": "message:earlier"}
    current = {**original, field: value}

    def prepare(revision, operation_id):
        document.write_text(json.dumps(revision), encoding="utf-8")
        code, result = cli("prepare-objective", "--agent-id", "agent-a", "--document", str(document),
            "--operation-id", operation_id, "--execute")
        assert code == 0 and result["proposal"]["status"] == "preview_ready", result
        return result["proposal"]

    before = cli("inspect")[1]
    earlier = prepare(original, "prepare-earlier-direction")
    updated = prepare(current, "prepare-current-direction")
    assert updated["proposal_id"] != earlier["proposal_id"]
    assert updated["normalized_parameters"][field] == value
    assert prepare(current, "prepare-current-direction") == updated
    assert cli("inspect")[1] == before, "preparation leaves the canonical objective unchanged"

    code, applied = cli("apply-proposal", "--proposal-id", updated["proposal_id"], "--execute")
    assert code == 0 and applied["executed"], applied
    revised = cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]
    assert {key: revised[key] for key in current} == current


@pytest.mark.parametrize(("field", "value"), [
    ("reason", "A corrected reason"),
    ("source_ref", "message:corrected"),
])
def test_reused_objective_proposal_binds_the_second_operation(acceptance_goal, field, value):
    project, _, cli, _ = acceptance_goal
    prepare_source(project)
    path = project / "objective.json"
    document = {"objective": "Maintain reliable exports and recovery",
        "reason": "The owner added restore drills", "source_ref": "message:owner"}
    first = ("prepare-objective", "--agent-id", "agent-a", "--document", str(path),
        "--operation-id", "first-review", "--execute")
    second = ("prepare-objective", "--agent-id", "agent-b", "--document", str(path),
        "--operation-id", "shared-review", "--execute")
    before = cli("inspect")[1]
    path.write_text(json.dumps(document), encoding="utf-8")
    code, prepared = cli(*first)
    assert code == 0, prepared
    proposal_id = prepared["proposal"]["proposal_id"]
    code, reused = cli(*second)
    assert code == 0 and reused["proposal"]["proposal_id"] == proposal_id, reused

    path.write_text(json.dumps({**document, field: value}), encoding="utf-8")
    code, conflict = cli(*second)
    assert code == 1 and not conflict["ok"], conflict
    assert "idempotency key already belongs to another preview" in conflict["error"], conflict
    path.write_text(json.dumps(document), encoding="utf-8")
    assert cli(*second)[1]["proposal"]["proposal_id"] == proposal_id
    assert cli("inspect")[1] == before
    store = ChatActionStore(project.parent / "runtime" / "chat" / "actions")
    assert [row["proposal_id"] for row in store.list(goal_id="goal-acceptance")] == [proposal_id]

    code, applied = cli("apply-proposal", "--proposal-id", proposal_id, "--execute")
    assert code == 0 and applied["executed"], applied
    code, replayed = cli(*second)
    assert code == 0 and replayed["proposal"] == applied["proposal"], replayed
    assert cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["revision"] == 1


def test_new_objective_operation_uses_current_basis_after_another_revision(acceptance_goal):
    project, criteria_path, cli, _ = acceptance_goal
    prepare_source(project)
    path = project / "objective.json"
    original = {
        "objective": "Maintain reliable exports and recovery",
        "reason": "The owner added restore drills",
        "source_ref": "message:owner",
    }

    def prepare(document, operation_id, agent="agent-a"):
        path.write_text(json.dumps(document), encoding="utf-8")
        code, result = cli(
            "prepare-objective", "--agent-id", agent, "--document", str(path),
            "--operation-id", operation_id, "--execute",
        )
        assert code == 0 and result["prepared"], result
        return result["proposal"]

    first = prepare(original, "original-review")["proposal_id"]
    initial_revision = cli("inspect")[1]["provider_revision"]
    code, configured = cli(
        "configure", "--document", str(criteria_path), "--operation-id", "add-unrelated-criteria",
        "--expected-provider-revision", initial_revision, "--execute",
    )
    assert code == 0 and configured["provider_revision"] != initial_revision, configured
    assert prepare(original, "accepted-alias", "agent-b")["proposal_id"] == first

    intervening = {**original, "objective": "Maintain a reviewable export queue"}
    other = prepare(intervening, "intervening-review")["proposal_id"]
    code, applied = cli("apply-proposal", "--proposal-id", other, "--execute")
    assert code == 0 and applied["executed"], applied
    before_fresh_review = cli("inspect")[1]

    assert prepare(original, "original-review")["proposal_id"] == first
    assert prepare(original, "accepted-alias", "agent-b")["proposal_id"] == first
    fresh = prepare(original, "current-review", "agent-b")["proposal_id"]
    assert fresh != first
    assert prepare(original, "current-review", "agent-b")["proposal_id"] == fresh
    assert cli("inspect")[1] == before_fresh_review

    code, applied = cli("apply-proposal", "--proposal-id", fresh, "--execute")
    assert code == 0 and applied["executed"], applied
    objective = applied["current"]["goal_acceptance_contract"]["goal_objective"]
    assert objective["objective"] == original["objective"] and objective["revision"] == 2


def test_stopped_goal_does_not_bind_a_new_objective_operation(acceptance_goal):
    project, _, cli, run = acceptance_goal
    prepare_source(project)
    goal_id = "goal-acceptance"
    document_path = project / "objective.json"
    document = {
        "objective": "Maintain reliable exports and recovery",
        "reason": "The owner added restore drills",
        "source_ref": "message:owner",
    }
    document_path.write_text(json.dumps(document), encoding="utf-8")

    def prepare(agent_id, operation_id):
        return cli(
            "prepare-objective", "--agent-id", agent_id, "--document", str(document_path),
            "--operation-id", operation_id, "--execute",
        )

    def lifecycle(operation):
        code, result = run(
            "goal-lifecycle", "--goal-id", goal_id, "--operation", operation,
            "--actor-kind", "owner", "--execute",
        )
        assert code == 0 and result["readback"]["verified"], result

    code, prepared = prepare("agent-a", "original-review")
    assert code == 0 and prepared["prepared"], prepared
    proposal = prepared["proposal"]
    code, shared = prepare("agent-b", "accepted-shared-review")
    assert code == 0 and shared["proposal"] == proposal, shared

    lifecycle("stop")
    store = ChatActionStore(project.parent / "runtime" / "chat" / "actions")
    stopped_snapshot = store.path.read_bytes()
    stopped_goal = cli("inspect")[1]
    for agent, operation in (("agent-a", "original-review"), ("agent-b", "accepted-shared-review")):
        code, replayed = prepare(agent, operation)
        assert code == 0 and replayed["proposal"] == proposal, replayed

    code, denied = prepare("agent-b", "new-shared-review")
    assert code == 1 and "Objective updates require an active Goal" in denied["error"], denied
    assert store.path.read_bytes() == stopped_snapshot
    assert cli("inspect")[1] == stopped_goal

    lifecycle("resume")
    reason = "The owner clarified how to verify a restore"
    document_path.write_text(json.dumps({**document, "reason": reason}), encoding="utf-8")
    code, corrected = prepare("agent-b", "new-shared-review")
    assert code == 0 and corrected["prepared"], corrected
    assert corrected["proposal"]["proposal_id"] != proposal["proposal_id"]
    assert corrected["proposal"]["normalized_parameters"]["reason"] == reason

@pytest.mark.parametrize("first_entry", ["cli", "chat"])
def test_objective_preview_replays_an_accepted_cli_operation_in_chat(acceptance_goal, first_entry):
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    project, _, cli, _ = acceptance_goal
    prepare_source(project)
    goal_id = "goal-acceptance"
    document = {"objective": "Maintain reliable exports and recovery",
        "reason": "The owner added restore drills", "source_ref": "message:owner"}
    path = project / "objective.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    before = cli("inspect")[1]
    store = ChatActionStore(project.parent / "runtime" / "chat" / "actions")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.action_store = store
    server.action_service = ChatActionService(store=store, registry_path=project.parent / "registry.json")
    server.verbose = False
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def post(route, body):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        try:
            connection.request("POST", route, json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def prepare_cli(key, agent):
        code, prepared = cli("prepare-objective", "--agent-id", agent, "--document", str(path),
            "--operation-id", key, "--execute")
        assert code == 0 and prepared["prepared"], prepared
        return prepared["proposal"]

    def request(key, agent):
        return {"action_kind": "goal.update", "summary": document["objective"],
            "context": {"kind": "goal", "goal_id": goal_id},
            "normalized_parameters": {"goal_id": goal_id, "prepared_by": agent, **document},
            "idempotency_key": key}

    route = "/api/actions/preview"
    first = request("first-preview", "agent-a")
    shared = request("shared-preview", "agent-b")
    try:
        if first_entry == "chat":
            code, created = post(route, first)
            assert code == 201, created
            proposal = created["proposal"]
        else:
            proposal = prepare_cli("first-preview", "agent-a")
        proposal_id = proposal["proposal_id"]
        assert prepare_cli("shared-preview", "agent-b") == proposal
        stored = store.path.read_bytes()
        for original in (first, shared):
            code, replay = post(route, original)
            assert code == 201 and replay["proposal"] == proposal, replay
        for changes in ({"objective": "Different objective"}, {"reason": "Different reason"},
                        {"source_ref": "message:other"}, {"prepared_by": "agent-a"}):
            code, denied = post(route, {**shared,
                "normalized_parameters": {**shared["normalized_parameters"], **changes}})
            assert code == 409 and denied["error_code"] == "action_conflict", denied
        for original in (first, shared):
            code, denied = post(route, {**original,
                "context": {**original["context"], "binding_id": "another-binding"}})
            assert code == 409 and denied["error_code"] == "action_conflict", denied
        code, denied = post(route, {**shared, "summary": "/tmp/private-note"})
        assert code == 400 and denied["error_code"] == "invalid_action_preview", denied
        assert store.path.read_bytes() == stored
        assert cli("inspect")[1] == before

        code, applied = post(f"/api/actions/{proposal_id}/apply", {})
        assert code == 200 and applied["proposal"]["status"] == "applied", applied
        current = cli("inspect")[1]
        assert current["goal_acceptance_contract"]["goal_objective"]["revision"] == 1
        snapshot = store.path.read_bytes()
        for original in (first, shared):
            code, replay = post(route, original)
            assert code == 201 and replay["proposal"] == applied["proposal"], replay
        assert prepare_cli("shared-preview", "agent-b") == applied["proposal"]
        code, replayed_apply = post(f"/api/actions/{proposal_id}/apply", {})
        assert code == 200 and replayed_apply["proposal"]["receipt"] == applied["proposal"]["receipt"], replayed_apply
        assert store.path.read_bytes() == snapshot
        assert [row["proposal_id"] for row in store.list(goal_id=goal_id)] == [proposal_id]
        assert cli("inspect")[1] == current
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_first_concurrent_preparations_share_one_pending_review(acceptance_goal, monkeypatch):
    from loopx import chat_action_store
    from loopx.control_plane.goals.goal_objective_actions import prepare_objective_revision

    project, _, _, _ = acceptance_goal
    prepare_source(project)
    root = project.parent
    actions = root / "runtime" / "chat" / "actions" / "actions.json"
    initializer_ready, peer_arrived, release_initializer = Event(), Event(), Event()
    initialize = chat_action_store.atomic_write_json
    lock = chat_action_store.exclusive_file_lock
    first_thread = None

    def hold_first_write(path, payload, **kwargs):
        if path == actions and not payload["proposals"] and get_ident() == first_thread:
            initializer_ready.set()
            assert release_initializer.wait(20)
        return initialize(path, payload, **kwargs)

    def notice_peer_lock(path, **kwargs):
        if path == actions and get_ident() != first_thread and not actions.exists():
            peer_arrived.set()
        return lock(path, **kwargs)

    def prepare(agent):
        nonlocal first_thread
        if agent == "agent-a":
            first_thread = get_ident()
        outcome = prepare_objective_revision(
            registry_path=root / "registry.json", runtime_root=str(root / "runtime"),
            goal_id="goal-acceptance", agent_id=agent, operation_id=f"prepare-{agent}",
            document={"objective": "Maintain reliable exports", "reason": "Preserve recovery",
                "source_ref": "message:owner"},
            execute=True,
        )
        if agent == "agent-b":
            peer_arrived.set()
        return outcome["proposal"]["proposal_id"]

    monkeypatch.setattr(chat_action_store, "atomic_write_json", hold_first_write)
    monkeypatch.setattr(chat_action_store, "exclusive_file_lock", notice_peer_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(prepare, "agent-a")
        try:
            assert initializer_ready.wait(20)
            second = pool.submit(prepare, "agent-b")
            assert peer_arrived.wait(20)
        finally:
            release_initializer.set()
        assert first.result(timeout=30) == second.result(timeout=30)


def test_native_proposal_owner_apply_and_separate_peer_adoption(acceptance_goal, monkeypatch):
    project, _, cli, run = acceptance_goal
    prepare_source(project)
    original = (project / "state.md").read_bytes()
    initial_admission = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")[1]
    document = {"objective": "Maintain a reliable service", "reason": "Bootstrap is complete", "source_ref": "message:owner"}
    path = project / "revision.json"
    path.write_text(json.dumps(document))
    args = ("prepare-objective", "--agent-id", "agent-a", "--document", str(path), "--operation-id", "prepare-current", "--execute")
    code, prepared = cli(*args)
    assert code == 0 and prepared["prepared"] and not prepared["executed"], prepared
    assert "goal_objective" not in cli("inspect")[1]["goal_acceptance_contract"]
    assert cli(*args)[1]["proposal"]["proposal_id"] == prepared["proposal"]["proposal_id"]
    other_args = ("prepare-objective", "--agent-id", "agent-b", "--document", str(path), "--operation-id", "peer-same-proposal", "--execute")
    assert cli(*other_args)[1]["proposal"]["proposal_id"] == prepared["proposal"]["proposal_id"], "peers share one pending owner review"
    pending_admission = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")[1]
    for field in ("normal_delivery_allowed", "requires_user_action", "should_run"):
        assert pending_admission[field] == initial_admission[field], "surfacing a proposal cannot add an early gate"
    assert not any(gap.get("kind") == "goal_objective_review_required"
        for gap in pending_admission["goal_frontier_projection"]["acceptance_gaps"])
    from loopx.control_plane.work_items.team_plan_adapter import team_plan_state_fingerprint
    old_team_basis = team_plan_state_fingerprint(registry_path=project.parent / "registry.json", goal_id="goal-acceptance", basis_agent_id="agent-a")
    service = ChatActionService(store=ChatActionStore(project.parent / "runtime" / "chat" / "actions"), registry_path=project.parent / "registry.json")
    applied = service.apply(prepared["proposal"]["proposal_id"])
    assert applied["proposal"]["status"] == "applied", applied
    assert team_plan_state_fingerprint(registry_path=project.parent / "registry.json", goal_id="goal-acceptance", basis_agent_id="agent-a") != old_team_basis
    status = run("status", "--goal-id", "goal-acceptance")[1]
    assert status["run_history"]["goals"][0]["goal_objective"] == document["objective"]
    for agent in ("agent-a", "agent-b"):
        _, before = cli("inspect", "--agent-id", agent)
        current = before["goal_acceptance_contract"]["goal_objective"]
        own = next(row for row in current["peer_adoption"] if row["agent_id"] == agent)
        assert not own["current"]
        decision = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", agent)[1]
        assert "Maintain a reliable service" in json.dumps(decision), decision
        assert any(gap.get("kind") == "goal_objective_review_required" for gap in decision["goal_frontier_projection"]["acceptance_gaps"]), decision
        adoption = {"revision": current["revision"], "digest": current["digest"], "work_digest": before["objective_work_digest"],
            "review_summary": "Reviewed current work; the existing deliverable still supports the maintained service."}
        path.write_text(json.dumps(adoption))
        code, result = cli("adopt-objective", "--agent-id", agent, "--document", str(path), "--operation-id", f"adopt-{agent}",
            "--expected-provider-revision", before["provider_revision"], "--execute")
        assert code == 0, result
        peers = cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["peer_adoption"]
        assert next(row for row in peers if row["agent_id"] == agent)["current"]
        if agent == "agent-a":
            assert not next(row for row in peers if row["agent_id"] == "agent-b")["current"]
        next_decision = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", agent)[1]
        assert not any(gap.get("kind") == "goal_objective_review_required" for gap in next_decision["goal_frontier_projection"]["acceptance_gaps"]), next_decision
    assert (project / "state.md").read_bytes() == original
    assert service.apply(prepared["proposal"]["proposal_id"])["proposal"]["status"] == "applied"
    # A native committed revision whose response is lost recovers once, even
    # while the Chat proposal has not yet stored its completed checkpoint.
    from loopx.control_plane.goals import goal_objective_actions
    path.write_text(json.dumps({**document, "objective": "Maintain the service and prove recovery"}))
    code, next_proposal = cli("prepare-objective", "--agent-id", "agent-a", "--document", str(path), "--operation-id", "prepare-next", "--execute")
    assert code == 0, next_proposal
    original_update = goal_objective_actions.update_goal_objective
    def lose_reply(**kwargs):
        value = original_update(**kwargs)
        if kwargs.get("execute"):
            monkeypatch.setattr(goal_objective_actions, "update_goal_objective", original_update)
            raise OSError("synthetic lost response after native commit")
        return value
    monkeypatch.setattr(goal_objective_actions, "update_goal_objective", lose_reply)
    import pytest
    with pytest.raises(OSError, match="lost response"):
        service.apply(next_proposal["proposal"]["proposal_id"])
    assert service.apply(next_proposal["proposal"]["proposal_id"])["proposal"]["status"] == "applied"
    assert cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["revision"] == 2


def test_cli_proposal_preview_authority_and_lost_response_recovery(acceptance_goal, monkeypatch):
    project, checks, cli, _ = acceptance_goal
    prepare_source(project)
    original = (project / "state.md").read_bytes()
    document = project / "objective.json"
    document.write_text(json.dumps({"objective": "Maintain reliable exports", "reason": "The owner added recovery",
        "source_ref": "message:owner-direction"}))
    code, prepared = cli("prepare-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "cli-direction", "--execute")
    assert code == 0, prepared
    proposal_id = prepared["proposal"]["proposal_id"]
    code, inspected = cli("inspect-proposal", "--agent-id", "agent-a", "--proposal-id", proposal_id)
    assert code == 0 and inspected["proposal"] == prepared["proposal"], inspected
    before = cli("inspect")[1]
    apply = ("apply-proposal", "--proposal-id", proposal_id)
    code, preview = cli(*apply)
    assert code == 0 and not preview["executed"], preview
    assert preview["proposal"]["status"] == "preview_ready"
    assert cli("inspect")[1] == before
    code, denied = cli(*apply, "--agent-id", "agent-a", "--execute")
    assert code == 1 and not denied["ok"], denied
    assert cli("inspect-proposal", "--proposal-id", proposal_id)[1]["proposal"]["status"] == "preview_ready"
    assert cli("inspect")[1] == before
    code, missing = cli("inspect-proposal", "--proposal-id", "proposal-missing")
    assert code == 1 and not missing["ok"], missing
    check_document = json.loads(checks.read_text())
    check_document["criteria"][0]["validation_argv"][0] = "python3"
    checks.write_text(json.dumps(check_document))
    code, check = cli("prepare", "--agent-id", "agent-a", "--document", str(checks),
        "--operation-id", "check-is-not-objective", "--execute")
    assert code == 0, check
    code, wrong_kind = cli("apply-proposal", "--proposal-id", check["proposal"]["proposal_id"], "--execute")
    assert code == 1 and not wrong_kind["ok"], wrong_kind
    assert not (project / "artifact.txt").exists()
    from loopx.control_plane.goals import goal_objective_actions
    native_update = goal_objective_actions.update_goal_objective

    def lose_reply(**kwargs):
        value = native_update(**kwargs)
        if kwargs.get("execute"):
            monkeypatch.setattr(goal_objective_actions, "update_goal_objective", native_update)
            raise OSError("synthetic lost reply after canonical commit")
        return value

    monkeypatch.setattr(goal_objective_actions, "update_goal_objective", lose_reply)
    service = ChatActionService(store=ChatActionStore(project.parent / "runtime" / "chat" / "actions"),
        registry_path=project.parent / "registry.json")
    with pytest.raises(OSError, match="lost reply"):
        service.apply(proposal_id)
    assert cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["revision"] == 1
    code, recovered = cli(*apply, "--execute")
    assert code == 0 and recovered["executed"] and recovered["proposal"]["status"] == "applied", recovered
    own = cli("inspect", "--agent-id", "agent-a")[1]
    objective = own["goal_acceptance_contract"]["goal_objective"]
    document.write_text(json.dumps({"revision": objective["revision"], "digest": objective["digest"],
        "work_digest": own["objective_work_digest"], "review_summary": "Existing export work includes recovery."}))
    code, adopted = cli("adopt-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "cli-adopt-direction", "--expected-provider-revision", own["provider_revision"], "--execute")
    assert code == 0, adopted
    peers = adopted["goal_acceptance_contract"]["goal_objective"]["peer_adoption"]
    assert next(peer for peer in peers if peer["agent_id"] == "agent-a")["current"]
    assert not next(peer for peer in peers if peer["agent_id"] == "agent-b")["current"]
    apply_objective(project, cli, "Maintain exports and validate restored artifacts")
    code, replay = cli(*apply, "--execute")
    assert code == 0 and replay["proposal"]["status"] == "applied", replay
    assert replay["current"]["goal_acceptance_contract"]["goal_objective"]["revision"] == 2
    assert cli("inspect")[1]["goal_acceptance_contract"]["goal_objective"]["revision"] == 2
    assert (project / "state.md").read_bytes() == original


def test_pending_proposal_allows_independent_work_without_an_owner_gate(acceptance_goal):
    project, _, cli, run = acceptance_goal
    prepare_source(project)
    document = project / "objective.json"
    document.write_text(json.dumps({"objective": "Maintain export recovery", "reason": "Recorded owner direction includes recovery",
        "source_ref": "message:owner-direction"}))
    code, proposal = cli("prepare-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "pending-work", "--execute")
    assert code == 0 and proposal["proposal"]["status"] == "preview_ready", proposal
    code, work = run("todo", "add", "--goal-id", "goal-acceptance", "--role", "agent",
        "--claimed-by", "agent-a", "--text", "Validate the existing export recovery",
        "--task-class", "advancement_task", "--action-kind", "implement")
    assert code == 0 and work["ok"] and work["added"], work
    listed = run("todo", "list", "--goal-id", "goal-acceptance", "--role", "agent")[1]
    assert any(todo["text"].endswith("Validate the existing export recovery") for todo in listed["todos"])
    assert "goal_objective" not in cli("inspect")[1]["goal_acceptance_contract"]
    assert cli("inspect-proposal", "--proposal-id", proposal["proposal"]["proposal_id"])[1]["proposal"]["status"] == "preview_ready"


def test_added_responsibility_expands_the_bound_goal_and_its_work(acceptance_goal):
    project, _, cli, run = acceptance_goal
    goal_id = "goal-acceptance"
    original = "Maintain reliable exports"
    state = project / "state.md"
    state.write_text(state.read_text() + f"\n## Objective\n{original}\n")

    def document(name, value):
        path = project / f"{name}.json"
        path.write_text(json.dumps(value))
        return str(path)

    before = cli("inspect", "--agent-id", "agent-a")[1]
    outcome = {"outcome_id": "exports", "owner_agent_id": "agent-a", "objective": original,
        "requirements": [{"id": "export", "description": "Keep producing a valid export"}],
        "todo_ids": ["todo_export"], "source": {"ref": "message:exports", "summary": original}}
    code, refined = cli("refine", "--agent-id", "agent-a", "--document", document("outcome", outcome),
        "--operation-id", "retain-export-criteria", "--expected-provider-revision", before["provider_revision"], "--execute")
    assert code == 0 and refined["status"] == "applied", refined
    retained = refined["goal_acceptance_contract"]["outcome_requirements"]

    revised = f"{original} and regularly verify restore drills"
    code, prepared = cli("prepare-objective", "--agent-id", "agent-a", "--operation-id", "add-restore-duty",
        "--document", document("objective", {"objective": revised, "reason": "The owner added regular restore drills",
            "source_ref": "message:restore-duty"}), "--execute")
    assert code == 0 and prepared["proposal"]["status"] == "preview_ready", prepared
    assert "goal_objective" not in cli("inspect")[1]["goal_acceptance_contract"]
    proposal_id = prepared["proposal"]["proposal_id"]
    code, applied = cli("apply-proposal", "--proposal-id", proposal_id, "--execute")
    assert code == 0 and applied["proposal"]["status"] == "applied", applied

    text = "Run and verify the next restore drill"
    add = ("todo", "add", "--goal-id", goal_id, "--role", "agent",
        "--claimed-by", "agent-a", "--text", text, "--task-class", "advancement_task",
        "--action-kind", "implement", "--operation-id", "add-restore-work")
    code, added = run(*add)
    assert code == 0 and added["ok"] and added["added"], json.dumps(added)
    code, replay = run(*add)
    assert code == 0 and replay["ok"], replay
    code, listed = run("todo", "list", "--goal-id", goal_id, "--role", "agent")
    assert code == 0, listed
    todos = listed["todos"]
    assert len(todos) == 2 and all(row["claimed_by"] == "agent-a" for row in todos), todos
    assert any(row["todo_id"] == "todo_export" and row["status"] == "open" for row in todos)
    assert sum(row["text"].endswith(text) for row in todos) == 1

    own = cli("inspect", "--agent-id", "agent-a")[1]
    current = own["goal_acceptance_contract"]
    assert current["outcome_requirements"] == retained
    objective = current["goal_objective"]
    assert objective["original_objective"] == original and objective["objective"] == revised
    adoption = {"revision": objective["revision"], "digest": objective["digest"],
        "work_digest": own["objective_work_digest"], "review_summary": "Keep export work and add a restore drill."}
    code, adopted = cli("adopt-objective", "--agent-id", "agent-a", "--document", document("adopt", adoption),
        "--operation-id", "adopt-restore-duty", "--expected-provider-revision", own["provider_revision"], "--execute")
    assert code == 0 and adopted["goal_acceptance_contract"]["goal_objective"]["peer_adoption"][0]["current"], adopted

    code, admission = run("quota", "should-run", "--codex-app", "--goal-id", goal_id,
        "--agent-id", "agent-a", "--todo-id", "todo_export", "--begin-turn")
    assert code == 0 and admission["normal_delivery_allowed"], admission
    source = next(row for row in admission["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        if row["source"] == "goal_acceptance")
    assert source["content"]["goal_acceptance_contract"]["goal_objective"]["objective"] == revised
    reason = source["reason"]
    assert "new lasting responsibility" in reason and "Todo in the same Goal" in reason, reason
    assert "in this Goal" in reason and "existing creation for an explicit new Goal" in reason, reason
    assert "scope conflicts" in reason and "changes what counts as success" in reason, reason


@pytest.mark.parametrize("arguments", [
    ("inspect-proposal",),
    ("inspect-proposal", "--proposal-id", "proposal-example", "--execute"),
    ("apply-proposal", "--proposal-id", "proposal-example", "--operation-id", "new-request"),
    ("inspect", "--proposal-id", "proposal-example"),
])
def test_cli_proposal_rejects_ambiguous_or_mutating_inspection(acceptance_goal, arguments):
    _, _, cli, _ = acceptance_goal
    before = cli("inspect")[1]
    code, rejected = cli(*arguments)
    assert code == 1 and not rejected["ok"], rejected
    assert cli("inspect")[1] == before


def test_native_conversation_exposes_and_executes_revision_loop(conversation_check):
    project, cli, controller, sid, _, _, adapter = conversation_check
    from loopx.chat_loopx_mode import TOOL, GUIDANCE
    # Ordinary conversation can call the API without a proactive revision nudge.
    assert "prepare_goal_objective" not in GUIDANCE
    assert "new lasting responsibility" in GUIDANCE and "Todo in the same Goal" in GUIDANCE
    assert "in the current Goal" in GUIDANCE and "existing creation for an explicit new Goal" in GUIDANCE
    assert "scope conflicts" in GUIDANCE and "changed definition of success" in GUIDANCE
    assert "adopt_goal_objective" in GUIDANCE
    state = project / "state.md"
    state.write_text(state.read_text() + "\n## Objective\nBootstrap the project\n")
    handler = adapter.session.read_tool_handler
    request = {"action": "prepare_goal_objective", "operation_id": "revise-current-direction", "document": {
        "objective": "Maintain the service and recovery path", "reason": "The owner changed the ongoing operating scope"}}
    prepared = handler(TOOL["name"], request)
    assert prepared["ok"] and prepared["prepared"], prepared
    assert prepared["proposal"]["normalized_parameters"]["source_ref"].startswith("turn:")
    assert handler(TOOL["name"], request)["proposal"]["proposal_id"] == prepared["proposal"]["proposal_id"]
    assert "goal_objective" not in handler(TOOL["name"], {"action": "outcomes"})["goal_acceptance_contract"]
    service = ChatActionService(store=ChatActionStore(controller.store.root.parent / "chat" / "actions"), registry_path=controller.registry_path)
    assert service.apply(prepared["proposal"]["proposal_id"])["proposal"]["status"] == "applied"
    refused = handler(TOOL["name"], {"action": "start", "binding_id": "delivery", "operation_id": "work-before-adoption"})
    assert refused["error"] == "goal_objective_review_required", refused
    before = handler(TOOL["name"], {"action": "outcomes"})
    current = before["goal_acceptance_contract"]["goal_objective"]
    adopted = handler(TOOL["name"], {"action": "adopt_goal_objective", "operation_id": "adopt-revision",
        "expected_provider_revision": before["provider_revision"], "document": {"revision": current["revision"], "digest": current["digest"],
            "work_digest": before["objective_work_digest"], "review_summary": "Reviewed the current work against the revised scope."}})
    assert adopted["ok"], adopted
    assert any(row["agent_id"] == "coordinator" and row["current"] for row in adopted["goal_acceptance_contract"]["goal_objective"]["peer_adoption"])
