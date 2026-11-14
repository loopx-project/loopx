"""Selected work retains the complete Goal basis without a second task inventory."""
from __future__ import annotations

from copy import deepcopy
import contextlib
import io
import json
import shlex
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.goals.acceptance import inspect_goal_acceptance
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.work_items.context_readback import _source_content
from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload
from tests.control_plane.host_process_fixture import assert_host_task_context


def _recording_host(tmp_path):
    runner = tmp_path / "runner.py"
    runner.write_text("import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(*, prompt, workspace, **kwargs):\n"
        "    Path(workspace, 'received.txt').write_text(prompt)\n"
        "    return json.dumps({'result_kind':'wait','summary':'Protocol input recorded.'})\n")
    return [sys.executable, "-m", "loopx.dsh_goal_mode", "--workspace", str(tmp_path),
        "--dsh-home", str(tmp_path / "host-home"), "--dsh-runner", str(runner)]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("checks", [False, True])
def test_selected_acceptance_read_keeps_global_requirements_and_exact_binding(
    tmp_path, monkeypatch, provider, checks,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    goal, selected = "acceptance-context", "todo_selected"
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    body = "Retain every accepted export guarantee. " * 30 + "Stop before publication."
    state.write_text("---\nstatus: active\n---\n# Goal\n## Objective\nDeliver a compatible export.\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id=goal, domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None)
    todos = [{"schema_version": "todo_item_v0", "todo_id": selected, "role": "agent",
        "text": body, "status": "open", "done": False, "task_class": "advancement_task",
        "action_kind": "implement", "index": 1, "source_section": "Agent Todo",
        "archive_state": "active", "claimed_by": "agent-a"}]
    todos += [{**todos[0], "todo_id": f"todo_peer_{index}", "text": "Independent peer work.",
        "index": index + 2, "claimed_by": "agent-b"} for index in range(32)]
    initialize_canonical_authority(runtime, goal,
        build_todo_runtime_shadow_projection(goal_id=goal, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider=provider)

    def cli(*arguments):
        code, result = run_json_cli_result(*arguments, registry_path=registry, runtime_root=runtime)
        assert code == 0, result
        return result

    # Two owners' cumulative outcomes remain Goal requirements. Selection must
    # not silently narrow those requirements or their work/evidence references.
    for agent, todo in (("agent-a", selected), ("agent-b", "todo_peer_0")):
        document = tmp_path / f"{agent}.json"
        document.write_text(json.dumps({"outcome_id": f"export-{agent}", "owner_agent_id": agent,
            "objective": f"Deliver the accepted {agent} export.", "todo_ids": [todo],
            "requirements": [{"id": "content", "description": "Retain the complete export content."},
                {"id": "recovery", "description": "Retain cancellation and recovery at the requirement tail."}],
            "source": {"ref": f"message:{agent}", "summary": "The owner requested a compatible export."}}))
        before = cli("goal-acceptance", "inspect", "--goal-id", goal)
        cli("goal-acceptance", "refine", "--goal-id", goal, "--agent-id", agent,
            "--document", str(document), "--expected-provider-revision", before["provider_revision"],
            "--operation-id", f"outcome-{agent}", "--execute")
    if checks:
        document = tmp_path / "checks.json"
        document.write_text(json.dumps({"scope": {"kind": "selected_work", "todo_ids": [selected]},
            "objective": "Deliver a compatible export.", "non_goals": ["No publication."],
            "criteria": [{"id": "content", "description": "Retain the complete export content.",
                "validation_argv": [sys.executable, "-c", "pass"], "validation_timeout_seconds": 5}],
            "bindings": [{"todo_id": selected, "criterion_ids": ["content"]}]}))
        before = cli("goal-acceptance", "inspect", "--goal-id", goal)
        cli("goal-acceptance", "configure", "--goal-id", goal, "--document", str(document),
            "--expected-provider-revision", before["provider_revision"],
            "--operation-id", "configure-export", "--execute")

    full = inspect_goal_acceptance(registry_path=registry, runtime_root=str(runtime), goal_id=goal)
    read = {"source": "goal_acceptance"}
    exact = _source_content(read, registry_path=registry, runtime_root=runtime,
        goal_id=goal, todo_id=selected)
    # The canonical acceptance owner, rather than another prose/role filter,
    # determines which binding belongs to this selected work.
    assert [row["todo_id"] for row in exact["tasks"]] == [selected]
    assert exact["tasks"] == [row for row in full["tasks"] if row["todo_id"] == selected]
    assert exact == {**full, "tasks": exact["tasks"]}
    assert "todo" not in exact and "completion_requirements" not in exact
    assert exact["goal_acceptance_contract"]["outcome_requirements"] == full["goal_acceptance_contract"]["outcome_requirements"]
    if checks:
        assert exact["contract"]["non_goals"] == ["No publication."]
        assert exact["tasks"][0]["state"] == "ready"
    else:
        assert exact["tasks"][0]["state"] == "unbound"
    # A Todo-less planning read still observes the whole existing inventory.
    assert _source_content(read, registry_path=registry, runtime_root=runtime,
        goal_id=goal, todo_id=None) == full
    missing = _source_content(read, registry_path=registry, runtime_root=runtime,
        goal_id=goal, todo_id="todo_missing")
    assert missing == {**full, "tasks": []}, "missing work must not fall back to another binding"

    # The synthetic CLI subprocess defaults to raw output. Test the profile
    # that enables outcome navigation while leaving delta delivery disabled.
    guard_args = ("quota", "should-run", "--goal-id", goal, "--agent-id", "agent-a",
                  "--context-delivery", "on", "--delta-delivery", "off")
    packet = read_cli_policy_payload(json.dumps(cli(*guard_args)), returncode=0)
    channel = packet["interaction_contract"]["agent_channel"]
    assert channel["delivery_allowed"] is True and channel["work_context"]["complete"] is True
    # The public canonical reader retains its full format metadata. Host context
    # needs the same complete scoped records and source proof, not the dictionary.
    users = cli("todo", "list", "--goal-id", goal, "--role", "user",
        "--status", "open", "--agent-id", "agent-a")
    proof = deepcopy(users["authority_read"])
    assert proof["todo_read_model"].pop("contract_fields")
    assert channel["work_context"]["user_todos"] == {
        "source": users["source"], "authority_read": proof, "todos": users["todos"],
    }
    observed = next(row["content"] for row in channel["work_context"]["sources"]
        if row.get("source") == "goal_acceptance")
    navigation = inspect_goal_acceptance(registry_path=registry, runtime_root=str(runtime), goal_id=goal,
        todo_id=selected, context_agent_id="agent-a", include_work_details=False)
    assert observed == navigation
    assert {key: value for key, value in observed.items() if key != "outcome_context"} == exact
    goal_source = next(row["content"] for row in channel["work_context"]["sources"]
        if row.get("source") == "goal_state")
    assert goal_source["full_source"] is True and goal_source["text"] == state.read_text()
    assert not any(row.get("source") == "goal_state" for row in channel["required_reads"])
    # The normal guard's complete captured basis is the envelope input; no
    # second guard may substitute a different observation for the host.
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope

    envelope = build_turn_envelope(packet)
    assert channel["work_context"]["selected_todo_text_verified"] is True
    assert envelope["work_context"] == {
        key: value
        for key, value in channel["work_context"].items()
        if key != "selected_todo_text_verified"
    }
    assert envelope["action"]["selected_todo"]["text_source_verified"] is True
    request = build_loopx_turn_host_request(build_loopx_turn_plan(envelope,
        host="codex-cli", execution_mode="isolated-headless", turn_instance_id="export-context-turn"))
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    command = _recording_host(tmp_path)
    result = subprocess.run(command,
        input=json.dumps(request), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    prompt = (tmp_path / "received.txt").read_text()
    authority = json.loads(prompt.split("Turn authority JSON:\n", 1)[1].split("\n\nWhen finished", 1)[0])
    assert authority["work_context"] == envelope["work_context"]
    assert authority["selected_todo"]["text_source_verified"] is True
    changed = deepcopy(request)
    acceptance = next(row["content"] for row in changed["turn_envelope"]["work_context"]["sources"]
        if row.get("source") == "goal_acceptance")
    acceptance["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][-1]["requirements"][-1]["description"] = "Omit recovery."
    rejected = subprocess.run(command, input=json.dumps(changed), capture_output=True, text=True, timeout=30)
    assert rejected.returncode != 0 and "action signature" in rejected.stderr
    assert (tmp_path / "received.txt").read_text() == prompt
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before

    # A failed exact acceptance read is still a pending obligation, never an
    # empty/successful basis. Exercise the real CLI with only that IO fault.
    from loopx.cli import main
    from loopx.control_plane.work_items import context_readback

    def unavailable(**_kwargs):
        raise OSError("acceptance source unavailable")

    planner_sources = []
    runtime_result = context_readback.effect_runtime_result

    def observe_planner_source(method, params, **options):
        if method == "work_item.context.plan":
            planner_sources.append(params["packet"])
        return runtime_result(method, params, **options)

    with monkeypatch.context() as fault:
        fault.setattr(context_readback, "inspect_goal_acceptance", unavailable)
        fault.setattr(context_readback, "effect_runtime_result", observe_planner_source)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                *guard_args])
        failed = read_cli_policy_payload(output.getvalue(), returncode=code)["interaction_contract"]["agent_channel"]
        assert code == 0 and failed["delivery_allowed"] is False
        assert failed["work_context"]["complete"] is False
        assert any(row.get("source") == "goal_acceptance" for row in failed["required_reads"])
    assert planner_sources
    for source in planner_sources:
        assert set(source) == {"selected_todo", "task_orchestration_contract", "effective_action", "interaction_contract"}
        assert set(source["interaction_contract"]["cli_channel"]) <= {"selection_required"}
        assert set(source["task_orchestration_contract"]) <= {"schema_version", "mode", "primary_todo_id"}
    recovered = read_cli_policy_payload(json.dumps(cli(*guard_args)), returncode=0)
    assert recovered["interaction_contract"]["agent_channel"]["work_context"]["complete"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_acceptance_observation_does_not_transport_the_large_selected_body(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text("---\nstatus: active\n---\n# Goal\n## Objective\nRetain complete work requirements.\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="large-acceptance-context", domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a"], quota_allowed_slots=None)
    todo = {"schema_version": "todo_item_v0", "todo_id": "todo_large", "role": "agent",
        "text": "Retain this complete requirement. " * 100_000 + "Do not omit this tail.",
        "status": "open", "done": False, "task_class": "advancement_task", "action_kind": "implement",
        "index": 1, "source_section": "Agent Todo", "archive_state": "active", "claimed_by": "agent-a"}
    # The observation must omit unused bodies even if the wire can hold them.
    assert len(todo["text"].encode()) > 2 * 1024 * 1024
    initialize_canonical_authority(runtime, "large-acceptance-context",
        build_todo_runtime_shadow_projection(goal_id="large-acceptance-context", todos=[todo], handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    basis = _source_content({"source": "goal_acceptance"}, registry_path=registry,
        runtime_root=runtime, goal_id="large-acceptance-context", todo_id="todo_large")
    assert basis["status"] == "loaded" and [row["todo_id"] for row in basis["tasks"]] == ["todo_large"]
    assert "todo" not in basis and "completion_requirements" not in basis


@pytest.mark.parametrize("change", ["work_added", "work_changed", "timer_wait", "checks_removed"])
def test_captured_acceptance_does_not_replace_current_work_admission(acceptance_goal, tmp_path, change):
    project, document, acceptance_cli, run = acceptance_goal
    goal, agent, selected = "goal-acceptance", "agent-a", "todo_export"

    def cli(*arguments):
        code, result = run(*arguments)
        assert code == 0, result
        return result

    def configure(*arguments):
        code, result = acceptance_cli(*arguments)
        assert code == 0, result
        return result

    requirements = tmp_path / "requirements.json"
    requirements.write_text(json.dumps({"outcome_id": "export", "owner_agent_id": agent,
        "objective": "Retain the complete export", "todo_ids": [selected], "requirements": [
            {"id": "content", "description": "Retain every original section."},
            {"id": "recovery", "description": "Stop before publication; preserve the recovery tail."}],
        "source": {"ref": "message:original", "summary": "The owner requested a complete export."}}))
    configure("refine", "--agent-id", agent, "--document", str(requirements),
        "--expected-provider-revision", configure("inspect")["provider_revision"],
        "--operation-id", "original-requirements", "--execute")
    configure("configure", "--document", str(document),
        "--expected-provider-revision", configure("inspect")["provider_revision"],
        "--operation-id", "original-checks", "--execute")
    original = configure("inspect")["goal_acceptance_contract"]["outcome_requirements"]
    capture = tmp_path / "decision"
    guard = ("quota", "should-run", "--goal-id", goal, "--agent-id", agent, "--codex-app")
    # The subprocess helper defaults to raw inspection. A dispatchable
    # capture must fulfill the Goal source read before it reaches DSH.
    first = cli(*guard, "--todo-id", selected, "--turn-instance-id", "original-delivery",
        "--context-delivery", "on", "--delta-delivery", "off",
        "--turn-envelope", "--decision-output-dir", str(capture))
    first = read_cli_policy_payload(json.dumps(first), returncode=0)
    saved = (capture / "decision.json").read_bytes()
    full = json.loads(saved)
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    first_envelope = build_turn_envelope(first, captured_decision_path=str(capture / "decision.json"))
    assert first["schema_version"] == "loopx_turn_envelope_v1"
    assert first == full
    basis = next(row["content"] for row in first_envelope["work_context"]["sources"]
        if row.get("source") == "goal_acceptance")
    assert first["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
    assert basis["tasks"][0]["state"] == "ready"
    assert basis["goal_acceptance_contract"]["outcome_requirements"] == original
    from loopx.control_plane.turn_driver.decision_capture import prepare_turn_decision_capture

    plan = build_loopx_turn_plan(first, host="codex-cli", execution_mode="isolated-headless",
        turn_instance_id="original-delivery")
    journal = prepare_turn_decision_capture(plan, journal_path=tmp_path / "original-delivery.json",
        decision=full, existing_journal=False)
    request = build_loopx_turn_host_request(journal)
    result = subprocess.run(_recording_host(project), input=json.dumps(request),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    prompt = (project / "received.txt").read_text()
    authority = json.loads(prompt.split("Turn request:\n", 1)[1].split("\n\nWhen finished", 1)[0])
    assert request["work_contract"] == first
    task_context = assert_host_task_context(authority, first)
    assert task_context["interaction_contract"]["agent_channel"]["work_context"] == first["interaction_contract"]["agent_channel"]["work_context"]

    # Finish the original synthetic work before attempting another managed
    # Turn. An unsettled receipt is its own legitimate recovery boundary.
    (project / "artifact.txt").write_text("accepted")
    check = json.loads(document.read_text())["criteria"][0]["validation_argv"]
    subprocess.run(check, cwd=project, check=True)
    identity = ("--goal-id", goal, "--agent-id", agent, "--todo-id", selected,
        "--turn-instance-id", "original-delivery")
    cli("refresh-state", *identity, "--classification", "validated_progress",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--delivery-workspace-path", str(project),
        "--no-global-sync", "--suppress-external-sinks")
    for appended in (True, False):
        spent = cli("quota", "spend-slot", *identity, "--slots", "1", "--source", "heartbeat", "--execute")
        assert spent["appended"] is appended

    target = selected
    if change == "work_added":
        target = cli("todo", "add", "--goal-id", goal, "--role", "agent", "--claimed-by", agent,
            "--text", "Deliver a newly requested export", "--task-class", "advancement_task",
            "--action-kind", "implement")["todo_id"]
    elif change == "work_changed":
        cli("todo", "update", "--goal-id", goal, "--todo-id", selected, "--agent-id", agent,
            "--text", "Deliver the export and a newly required checksum")
    elif change == "timer_wait":
        cli("todo", "update", "--goal-id", goal, "--todo-id", selected, "--agent-id", agent,
            "--status", "deferred", "--resume-when", "resume_at:2099-01-01T00:00:00Z")
    else:
        configure("clear-checks", "--expected-provider-revision", configure("inspect")["provider_revision"],
            "--operation-id", "remove-checks", "--execute")

    # A capture is an immutable observation. Reading its detail cannot admit
    # changed work, remove a wait or silently rebind the owner's checks.
    refusal_capture = tmp_path / "current-decision"
    code, current = run(*guard, "--todo-id", target, "--turn-instance-id", "current-delivery",
        "--decision-output-dir", str(refusal_capture), "--turn-envelope", "--context-delivery", "on",
        "--delta-delivery", "auto")
    assert code == (0 if change == "checks_removed" else 1), current
    if code != 0:
        assert current["error_code"] == "quota_action_selection_rejected"
        assert current["heartbeat_receipt"]["status"] == "not_committed"
        from loopx.control_plane.turn_driver.decision_capture import read_context_capture_file

        detail = current["captured_decision"]
        verified_path, _raw, current = read_context_capture_file(detail["path"], detail["sha256"], private=True)
        assert verified_path.samefile(refusal_capture / "decision.json")
    else:
        current = read_cli_policy_payload(json.dumps(current), returncode=code)
    # Validate the current signed decision against its captured observation,
    # including when the response denies dispatch.
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision
    assert_complete_turn_decision(current)
    assert current == json.loads((refusal_capture / "decision.json").read_bytes())
    channel = current["interaction_contract"]["agent_channel"]
    assert channel["delivery_allowed"] is (change == "checks_removed")
    if change != "checks_removed":
        assert current["error_code"] == "quota_action_selection_rejected"
    cold = configure("inspect")
    # Work status and its derived fingerprint may change; every original
    # objective, requirement, source and work link must remain intact.
    requirements_now = cold["goal_acceptance_contract"]["outcome_requirements"]
    assert {k: v for k, v in requirements_now.items() if k != "outcomes"} == {
        k: v for k, v in original.items() if k != "outcomes"}
    old_outcome, = original["outcomes"]
    new_outcome, = requirements_now["outcomes"]
    assert {k: v for k, v in new_outcome.items() if k not in {"work", "basis_digest"}} == {
        k: v for k, v in old_outcome.items() if k not in {"work", "basis_digest"}}
    assert (new_outcome["basis_digest"] != old_outcome["basis_digest"]) is (
        change in {"work_changed", "timer_wait"})
    assert new_outcome["work"] == [
        {**old_outcome["work"][0], "status": "deferred" if change == "timer_wait" else "open"}]
    if change == "work_added":
        assert {row["todo_id"]: row["state"] for row in cold["goal_acceptance_contract"]["tasks"]} == {
            selected: "ready", target: "unbound"}
    if change == "work_changed":
        assert cold["goal_acceptance_contract"]["tasks"][0]["state"] == "stale"
    if change == "checks_removed":
        assert current["interaction_contract"]["user_channel"]["action_required"] is False
    # The unselected normal/replan/wait view follows the same current owner,
    # including all cumulative requirements whenever execution needs a read.
    inspection = read_cli_policy_payload(json.dumps(cli(*guard[:-1])), returncode=0)
    current_context = inspection["interaction_contract"]["agent_channel"].get("work_context", {})
    for source in current_context.get("sources", []):
        if source.get("source") == "goal_acceptance":
            assert source["content"]["goal_acceptance_contract"] == cold["goal_acceptance_contract"]
    before_read = {p: p.read_bytes() for p in capture.rglob("*") if p.is_file()}
    detail = subprocess.run(shlex.split(first_envelope["detail_ref"]["full_decision"]),
        capture_output=True, check=True).stdout
    assert detail == saved and json.loads(detail) == full
    assert {p: p.read_bytes() for p in capture.rglob("*") if p.is_file()} == before_read
    assert configure("inspect") == cold
    assert (project / "received.txt").read_text() == prompt
