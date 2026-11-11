"""Approved Goal criteria outlive the first local task without check setup."""
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from test_new_goal_storage_defaults import environment as environment, app as app
from cli_output_fixture import read_cli_context_for_oracle
from loopx.control_plane.goals.acceptance import inspect_goal_acceptance


CRITERIA = "Keep the introduction and include the supporting appendix."


def preview_goal(request, *, expected_status=201, **changes):
    parameters = {"goal_id": "report", "title": "Report", "objective": "Deliver the report.",
        "completion_criteria": CRITERIA, "agent_id": "codex", "workspace_ref": "current",
        "heartbeat": {"enabled": False}, "initial_todos": ["Write the introduction"], **changes}
    status, preview = request("/api/actions/preview", {
        "action_kind": "goal.create", "summary": "Create the report Goal",
        "idempotency_key": "report-with-completion",
        "context": {"kind": "goal_channel", "goal_id": "workspace"}, "normalized_parameters": parameters})
    assert status == expected_status, preview
    return preview["proposal"] if status == 201 else preview


def inspect(project, runtime):
    return inspect_goal_acceptance(registry_path=project / ".loopx/registry.json",
        runtime_root=str(runtime), goal_id="report", agent_id="codex")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_approved_goal_criteria_are_retained_before_execution(environment, app, provider):
    configure, _, _, project, runtime = environment
    _, request, _ = app
    configure(provider, mode="hard_lease")
    criteria = CRITERIA
    proposal = preview_goal(request)
    status, applied = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 200, applied
    result = inspect_goal_acceptance(registry_path=project / ".loopx/registry.json",
        runtime_root=str(runtime), goal_id="report", agent_id="codex")
    contract = result["goal_acceptance_contract"]
    outcomes = contract.get("outcome_requirements", {}).get("outcomes", [])
    assert len(outcomes) == 1, "approved completion criteria must reach the normal Goal requirement owner"
    outcome = outcomes[0]
    assert outcome["requirements"][0]["description"] == criteria
    assert not outcome["requirements"][0]["planning_satisfied"]
    assert outcome["requirements"][0]["status"] == "unverified"
    assert outcome["todo_ids"] == applied["proposal"]["receipt"]["resource_ids"]["todo_ids"]
    assert not any(key in contract for key in ("enabled", "scope", "criteria")), "no feature activation or check contract"
    before = json.dumps(contract, sort_keys=True)
    replay_status, replay = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert replay_status == 200, replay
    assert json.dumps(inspect_goal_acceptance(registry_path=project / ".loopx/registry.json",
        runtime_root=str(runtime), goal_id="report", agent_id="codex")["goal_acceptance_contract"], sort_keys=True) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_creation_replays_original_completion_after_lost_write_response(environment, app, monkeypatch, provider):
    from loopx.control_plane.goals import acceptance
    from loopx.control_plane.todos.mutation_api import add_goal_todo

    configure, _, _, project, runtime = environment
    store, request, _ = app
    configure(provider)
    proposal = preview_goal(request)
    original = acceptance.refine_goal_outcome
    calls = []

    def lose_response(**kwargs):
        calls.append(kwargs)
        result = original(**kwargs)
        if len(calls) == 1:
            raise OSError("completion response lost after canonical commit")
        return result

    monkeypatch.setattr(acceptance, "refine_goal_outcome", lose_response)
    status, failed = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 424 and failed["error_code"] == "canonical_action_failed", failed
    committed = inspect(project, runtime)["goal_acceptance_contract"]
    assert committed["outcome_requirements"]["revision"] == 1
    assert not store.load(proposal["proposal_id"])["checkpoint"]["steps"].get("first_session_opened")
    add_goal_todo(registry_path=project / ".loopx/registry.json", goal_id="report", role="agent",
        text="Review the report", agent_id="codex", claimed_by="codex", dry_run=False)
    assert inspect(project, runtime)["provider_revision"] != calls[0]["expected_provider_revision"]
    status, recovered = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 200, recovered
    assert len(calls) == 2 and calls[0] == calls[1], "unknown writes must reuse the original operation, CAS and body"
    after = inspect(project, runtime)["goal_acceptance_contract"]["outcome_requirements"]
    assert after["revision"] == 1 and len(after["outcomes"][0]["sources"]) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("criteria", ["x" * 1000, "\U0001f4dd" * 1000])
def test_full_approved_creation_text_is_not_truncated(environment, app, provider, criteria):
    configure, _, _, project, runtime = environment
    _, request, _ = app
    configure(provider)
    proposal = preview_goal(request, completion_criteria=criteria)
    status, applied = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 200, applied
    requirement = inspect(project, runtime)["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["requirements"][0]
    assert requirement["description"] == criteria


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_first_plan_reuses_creation_requirements_without_a_second_declaration(environment, app, provider):
    from loopx.control_plane.goals.task_planning import build_task_planning_packet
    from benchmark.runtime.planning import validate_plan_readback

    configure, _, _, project, runtime = environment
    _, request, _ = app
    configure(provider)
    criteria = "\U0001f4dd" * 1000  # Main's creation limit is 2,000 UTF-16 units.
    proposal = preview_goal(request, completion_criteria=criteria)
    status, applied = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 200, applied
    before = inspect(project, runtime)
    packet = build_task_planning_packet(registry_path=project / ".loopx/registry.json",
        runtime_root_arg=str(runtime), goal_id="report", agent_id="codex",
        project=project, text="Deliver the report.")
    outcomes = packet["goal_acceptance"]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
    assert len(outcomes) == 1 and outcomes[0]["id"] == "goal-creation"
    assert outcomes[0]["requirements"][0]["description"] == criteria
    assert outcomes[0]["sources"][0]["ref"] == f"goal-create:{proposal['proposal_id']}:completion"
    ids = applied["proposal"]["receipt"]["resource_ids"]["todo_ids"]
    verified = validate_plan_readback({"input_digest": packet["input_digest"],
        "status": "ready", "todo_ids": ids}, packet, packet)
    assert verified["goal_requirements_readback"]["outcome_ids"] == ["goal-creation"]
    assert inspect(project, runtime) == before, "Reuse is read-only, without another declaration or revision"


def test_no_criteria_does_not_infer_requirements_from_objective(environment, app):
    _, _, _, project, runtime = environment
    _, request, _ = app
    proposal = preview_goal(request, completion_criteria="", objective=CRITERIA)
    status, result = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 200, result
    assert "outcome_requirements" not in inspect(project, runtime)["goal_acceptance_contract"]


def test_completion_criteria_require_an_explicit_executor_before_any_creation(environment, app):
    _, _, _, project, _ = environment
    _, request, _ = app
    rejected = preview_goal(request, expected_status=400, agent_id="")
    assert "selected Agent" in rejected["error"]
    assert not any(row["id"] == "report" for row in json.loads((project / ".loopx/registry.json").read_text())["goals"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_creation_does_not_rebase_a_conflicted_completion_write(environment, app, monkeypatch, provider):
    from loopx.control_plane.goals import acceptance
    from loopx.control_plane.todos.mutation_api import add_goal_todo

    configure, _, _, project, runtime = environment
    store, request, _ = app
    configure(provider)
    proposal = preview_goal(request)
    original = acceptance.refine_goal_outcome
    requests = []

    def conflict_before_commit(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            add_goal_todo(registry_path=project / ".loopx/registry.json", goal_id="report", role="agent",
                text="Concurrent owner work", agent_id="codex", claimed_by="codex", dry_run=False)
        return original(**kwargs)

    monkeypatch.setattr(acceptance, "refine_goal_outcome", conflict_before_commit)
    for _ in range(2):
        status, rejected = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
        assert status == 400 and "revision" in rejected["error"], rejected
        assert "outcome_requirements" not in inspect(project, runtime)["goal_acceptance_contract"]
        assert "first_session_opened" not in store.load(proposal["proposal_id"])["checkpoint"]["steps"]
    assert requests[0] == requests[1], "a known conflict needs reconciliation; retry cannot silently rebase"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["declaration_response", "first_turn_response"])
def test_managed_creation_retains_requirements_before_one_host_dispatch(environment, app, monkeypatch, provider, fault):
    import runpy
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    from loopx.control_plane.goals import acceptance

    configure, _, _, project, runtime = environment
    _, request, service = app
    configure(provider)
    capture, binary = runtime / "native-requests.jsonl", runtime / "synthetic-codex"
    source = runpy.run_path(str(Path(__file__).parents[2] / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
    source = source.replace('    method = request.get("method")',
        f'    with open({str(capture)!r}, "a") as stream:\n        stream.write(json.dumps(request) + "\\n")\n'
        '    method = request.get("method")')
    binary.write_text(source)
    binary.chmod(0o700)
    chat = ChatSessionStore(runtime / "chat")
    controller = ChatRuntimeController(store=chat, codex_bin=str(binary), registry_path=service.registry_path,
        project_contexts=ChatProjectContexts([project]))
    service.chat_store, service.runtime_controller = chat, controller
    proposal = preview_goal(request)
    target = acceptance if fault == "declaration_response" else controller
    method = "refine_goal_outcome" if fault == "declaration_response" else "submit_turn"
    original = getattr(target, method)

    def lose_response(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("Committed creation response was lost")

    monkeypatch.setattr(target, method, lose_response)
    try:
        status, failed = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
        assert status == 424, failed
        assert inspect(project, runtime)["goal_acceptance_contract"]["outcome_requirements"]["revision"] == 1
        if fault == "declaration_response":
            assert chat.list_sessions(goal_id="report") == [], "an uncertain declaration must block Host startup"
        monkeypatch.setattr(target, method, original)
        submit = controller.submit_turn

        def assert_retained_then_submit(*args, **kwargs):
            outcome = inspect(project, runtime)["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
            assert outcome["requirements"][0]["description"] == CRITERIA
            return submit(*args, **kwargs)

        monkeypatch.setattr(controller, "submit_turn", assert_retained_then_submit)
        status, recovered = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
        assert status == 202, recovered
        ids = recovered["proposal"]["receipt"]["resource_ids"]
        controller.wait_for_turn(session_id=ids["session_id"], turn_id=ids["turn_id"], timeout_sec=10)
        assert len(chat.list_sessions(goal_id="report")) == 1
        starts = [json.loads(line) for line in capture.read_text().splitlines() if json.loads(line).get("method") == "turn/start"]
        assert len(starts) == 1
        assert inspect(project, runtime)["goal_acceptance_contract"]["outcome_requirements"]["revision"] == 1
    finally:
        controller.close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["soft_claim", "hard_lease"])
def test_confirmed_task_start_recovers_the_same_claim_and_dispatch(environment, app, monkeypatch, provider, mode):
    import runpy

    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted

    configure, _, _, project, runtime = environment
    store, request, service = app
    configure(provider, mode=mode)
    goal = preview_goal(request)
    status, created = request(f"/api/actions/{goal['proposal_id']}/apply", {})
    assert status == 200, created

    capture, binary = runtime / "task-native-requests.jsonl", runtime / "task-codex"
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
        status, preview = request("/api/actions/preview", {
            "action_kind": "todo.create", "summary": "Start the appendix check", "idempotency_key": "start-appendix",
            "context": {"kind": "goal", "goal_id": "report"},
            "normalized_parameters": {"goal_id": "report", "text": "Verify the supporting appendix",
                                      "agent_id": "codex", "start_execution": True},
        })
        assert status == 201, preview
        proposal_id = preview["proposal"]["proposal_id"]
        apply = f"/api/actions/{proposal_id}/apply"
        save_checkpoint = store.save_checkpoint

        service.runtime_controller = None
        status, unavailable = request(apply, {})
        assert status == 400 and "Runtime Controller" in unavailable["error"], unavailable
        todo_id = store.load(proposal_id)["checkpoint"]["steps"]["todo_created"]["todo_id"]
        partial = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="report", include_leases=True)
        assert [todo.get("claimed_by") for todo in partial["todos"] if todo["todo_id"] == todo_id] == [None]
        assert partial["leases"] == []
        service.runtime_controller = controller

        def lose_claim_response(*args, **kwargs):
            if kwargs.get("step") == "todo_assigned":
                raise OSError("Lost the response after committing the Task assignment")
            return save_checkpoint(*args, **kwargs)

        monkeypatch.setattr(store, "save_checkpoint", lose_claim_response)
        status, failed = request(apply, {})
        assert status == 424, failed
        assert store.load(proposal_id)["checkpoint"]["steps"]["todo_created"]["todo_id"] == todo_id
        authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="report", include_leases=True)
        assert [todo["claimed_by"] for todo in authority["todos"] if todo["todo_id"] == todo_id] == ["codex"]
        assert chat.list_sessions(goal_id="report") == []

        monkeypatch.setattr(store, "save_checkpoint", save_checkpoint)
        status, recovered = request(apply, {})
        assert status == 202, recovered
        ids = recovered["proposal"]["receipt"]["resource_ids"]
        assert ids["todo_id"] == todo_id
        turn = controller.wait_for_turn(session_id=ids["session_id"], turn_id=ids["turn_id"], timeout_sec=10)
        assert turn["status"] == "completed"
        sessions = chat.list_sessions(goal_id="report")
        assert [session["channel_id"] for session in sessions] == [f"task.{todo_id}"]
        messages = [json.loads(line) for line in capture.read_text().splitlines()]
        assert [row["params"]["sandbox"] for row in messages if row.get("method") == "thread/start"] == ["workspace-write"]
        assert sum(row.get("method") == "turn/start" for row in messages) == 1
        status, replay = request(apply, {})
        assert status == 202 and replay["proposal"]["receipt"] == recovered["proposal"]["receipt"]
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="report", include_leases=True) == authority
        assert [(lease["todo_id"], lease["owner"]) for lease in authority["leases"]] == (
            [(todo_id, "codex")] if mode == "hard_lease" else [])
    finally:
        controller.close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("completion_surface", ["native-cli", "external-worker"])
def test_ordinary_goal_replans_after_first_todo_and_delivers_the_missing_section(environment, app, monkeypatch, provider, completion_surface):
    """Real creation, native CLI and authority; only the work decision is scripted."""
    configure, _, _, project, runtime = environment
    _, request, _ = app
    configure(provider, mode="soft_claim")
    proposal = preview_goal(request)
    status, created = request(f"/api/actions/{proposal['proposal_id']}/apply", {})
    assert status == 200, created
    original = created["proposal"]["receipt"]["resource_ids"]["todo_ids"][0]
    registry = project / ".loopx/registry.json"

    def cli(*args):
        run = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args], cwd=project,
            text=True, capture_output=True, timeout=60)
        assert run.returncode == 0, run.stdout + run.stderr
        return json.loads(read_cli_context_for_oracle(run.stdout)[0])

    artifact = project / "report.txt"
    artifact.write_text("Introduction\n")
    cli("todo", "complete", "--goal-id", "report", "--agent-id", "codex", "--todo-id", original,
        "--no-follow-up", "--evidence", "artifact:report-introduction")
    decision = cli("quota", "should-run", "--codex-app", "--turn-instance-id", "report-replan",
        "--goal-id", "report", "--agent-id", "codex")
    frontier = decision.get("goal_frontier_projection")
    assert frontier, decision
    assert frontier["replan_required"], decision
    gap = next(g for g in frontier["acceptance_gaps"] if g["kind"] == "goal_outcome_uncovered")
    assert gap["requirement_ids"] == ["completion"]
    assert decision.get("autonomous_replan_obligation"), decision
    assert inspect(project, runtime)["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["requirements"][0]["description"] == CRITERIA

    # The delivered requirement justifies this concrete successor, not a timer
    # or an additional benchmark-only controller. Preserve the actual artifact.
    successor = cli("todo", "add", "--goal-id", "report", "--role", "agent", "--text", "Add the missing supporting appendix",
        "--claimed-by", "codex", "--task-class", "advancement_task", "--action-kind", "implement",
        "--operation-id", "appendix-from-retained-criteria", "--target-key", "report-appendix",
        "--replan-obligation-id", decision["autonomous_replan_obligation"]["obligation_id"])["todo_id"]
    from loopx.control_plane.goals.acceptance import refine_goal_outcome
    refine_goal_outcome(registry_path=registry, runtime_root=str(runtime), goal_id="report", agent_id="codex",
        expected_provider_revision=inspect(project, runtime)["provider_revision"], operation_id="link-appendix",
        refinement={"outcome_id": "goal-creation", "todo_ids": [successor]}, execute=True)
    vision = project / "replan.json"
    def read_action(command):
        result = subprocess.run(shlex.split(command), cwd=project, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(read_cli_context_for_oracle(result.stdout)[0])

    review = read_action(decision["replan_action_packet"]["review"])
    writeback = read_action(review["next"]["writeback"])
    stride = {**writeback["stride_control"]["minimal_example"],
        "basis": "unexplained_gap", "action": "verify", "axes": {
            "work_slice": {"plan": "Add the missing appendix", "reason": "The initial introduction alone is incomplete"},
            "route_distance": {"plan": "Retain the introduction and append the required section", "reason": "The original useful text remains valid"},
            "validation_scope": {"plan": "Check both sections", "reason": "Completing only the latest task could lose the earlier requirement"}},
        "next_probe": {"scope": "One local report artifact", "prediction": "Both required sections will be present",
            "minimum_evidence": "Read the introduction and appendix from the actual report",
            "adoption_criterion": "The report contains both sections", "cost_bound": "One local edit and one content check"}}
    vision.write_text(json.dumps({"state": "vision_patch_proposed", "stride_decision": stride,
        "todo_delta": ["create:" + successor], "vision_patch": {
        "acceptance_summary": CRITERIA}, "path_delta": {"outcome": "replan",
        "prior_assumption": "Completing the introduction would finish the local task",
        "observed_reality": "The overall report still needs the explicitly required appendix",
        "retained": ["The introduction and the owner's completion criteria"], "evidence_refs": ["report.txt"]}}))
    artifact.write_text(artifact.read_text() + "Appendix\n")
    subprocess.run([sys.executable, "-c", "from pathlib import Path; assert Path('report.txt').read_text() == 'Introduction\\nAppendix\\n'"],
        cwd=project, check=True)
    obligation = decision["autonomous_replan_obligation"]["obligation_id"]
    recorded = cli("refresh-state", "--goal-id", "report", "--agent-id", "codex", "--turn-instance-id", "report-replan",
        "--replan-obligation-id", obligation, "--agent-vision-json", str(vision),
        "--classification", "bounded_replan_progress", "--delivery-batch-scale", "single_surface",
        "--delivery-outcome", "outcome_progress", "--progress-result-class", "advanced",
        "--progress-surface-id", "report-artifact", "--progress-hypothesis-id", "appendix-completes-report",
        "--progress-probe-kind", "complete-content-check", "--progress-evidence-id", "artifact:complete-report",
        "--no-global-sync", "--suppress-external-sinks")
    assert recorded["autonomous_replan_ack"]["recorded"], recorded
    cli("quota", "spend-slot", "--goal-id", "report", "--agent-id", "codex", "--turn-instance-id", "report-replan",
        "--replan-obligation-id", obligation, "--slots", "1", "--source", "heartbeat", "--execute")
    completion_args = ["todo", "complete", "--goal-id", "report", "--agent-id", "codex", "--todo-id", successor,
        "--no-follow-up", "--evidence", "artifact:complete-report"]
    if completion_surface == "native-cli":
        cli(*completion_args)
    else:
        from tests.test_external_scheduler_worker import _args, worker

        # An actual external scheduler admits one subprocess Host. Its model
        # boundary is a local deterministic validator, not a paid model call.
        host = project / "report_host.py"
        marker = project / "external-adoption.json"
        host.write_text("import json,subprocess,sys\nfrom pathlib import Path\n"
            "from loopx.control_plane.goals.acceptance import inspect_goal_acceptance\n"
            f"current=inspect_goal_acceptance(registry_path=Path({str(registry)!r}), runtime_root={str(runtime)!r}, goal_id='report', agent_id='codex')\n"
            "outcome=current['goal_acceptance_contract']['outcome_requirements']['outcomes'][0]\n"
            f"assert {successor!r} in outcome['todo_ids']\n"
            f"assert outcome['requirements'][0]['description'] == {CRITERIA!r}\n"
            "assert Path('report.txt').read_text() == 'Introduction\\nAppendix\\n'\n"
            f"command = { [sys.executable, '-m', 'loopx.entrypoint', '--registry', str(registry), '--runtime-root', str(runtime), '--format', 'json', *completion_args]!r}\n"
            "r=subprocess.run(command,text=True,capture_output=True); assert r.returncode == 0,r.stdout+r.stderr\n"
            f"Path({str(marker)!r}).write_text(json.dumps({{'adopted_criteria': outcome['requirements'][0]['description'], 'completed_todo': {successor!r}}}))\n")
        monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
        args = _args(fake_cli=Path(sys.executable).parent / "loopx",
            state_file=project / "external-scheduler.json", wake_cmd=shlex.join([sys.executable, str(host)]),
            quota_timeout_seconds=60, wake_timeout_seconds=60)
        args.registry, args.runtime_root = str(registry), str(runtime)
        args.goal_id, args.agent_id = "report", "codex"
        monkeypatch.chdir(project)
        assert worker.run_worker(args) == 0
        assert json.loads(marker.read_text())["completed_todo"] == successor
    refine_goal_outcome(registry_path=registry, runtime_root=str(runtime), goal_id="report", agent_id="codex",
        expected_provider_revision=inspect(project, runtime)["provider_revision"], operation_id="review-complete-report",
        refinement={"outcome_id": "goal-creation", "review": [{"id": "completion", "conclusion": "met",
            "evidence_refs": ["report.txt"], "note": "Read the artifact; the introduction and supporting appendix are present"}]}, execute=True)
    vision.write_text(json.dumps({"state": "no_followup", "vision_patch": {
        "acceptance_summary": CRITERIA, "last_patch_summary": "The actual report contains both required sections"},
        "path_delta": {"outcome": "stop", "prior_assumption": "The report still needed its appendix",
            "observed_reality": "Both sections are present and the complete content check passed",
            "evidence_refs": ["report.txt"], "stopped": ["No remaining work is required for this report"]}}))
    cli("refresh-state", "--goal-id", "report", "--agent-id", "codex", "--agent-vision-json", str(vision),
        "--no-global-sync", "--suppress-external-sinks")
    final = cli("quota", "should-run", "--goal-id", "report", "--agent-id", "codex")
    assert not final["goal_frontier_projection"]["acceptance_gaps"], final
    assert final["should_run"] is False and not final.get("autonomous_replan_obligation"), final
    requirement = inspect(project, runtime)["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["requirements"][0]
    assert requirement["planning_satisfied"] and requirement["status"] == "unverified", "a work review is not independent acceptance"
    assert artifact.read_text() == "Introduction\nAppendix\n"
    if completion_surface == "external-worker":
        before = marker.read_bytes()
        assert worker.run_worker(args) == 0
        assert marker.read_bytes() == before, "the completed ordinary Goal must not dispatch the Host again"
