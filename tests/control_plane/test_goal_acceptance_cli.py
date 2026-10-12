"""One owner contract, actual validation and independent canonical readback."""

from __future__ import annotations

import hashlib
import json
import sys

import pytest
from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)

from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.testing.canary_harness import (
    run_json_cli_result,
    write_fixture_registry,
)


@pytest.fixture(params=["file", "sqlite"])
def acceptance_goal(tmp_path, monkeypatch, request):
    if request.param == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    state = project / "state.md"
    state.write_text("---\nstatus: active\n---\n# Fixture\n## Agent Todo\n")
    runtime, registry = tmp_path / "runtime", tmp_path / "registry.json"
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id="goal-acceptance",
        domain="acceptance",
        adapter_kind="generic_project_goal_v0",
        state_file=str(state),
        registered_agents=["agent-a"],
    )
    todo = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_export",
        "role": "agent",
        "text": "Write the export artifact",
        "status": "open",
        "done": False,
        "task_class": "advancement_task",
        "action_kind": "implement",
        "index": 1,
        "source_section": "Agent Todo",
        "archive_state": "active",
        "claimed_by": "agent-a",
    }
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-acceptance", todos=[todo], handoff_mode="soft_claim"
    )
    initialize_canonical_authority(
        runtime, "goal-acceptance", projection, state_path=state, provider=request.param
    )
    document = {
        "scope": {"kind": "all_advancement"},
        "objective": "Produce a usable export",
        "non_goals": ["No unrelated code cleanup"],
        "criteria": [
            {
                "id": "export",
                "description": "The exported artifact has the expected content",
                "validation_argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; assert Path('artifact.txt').read_text() == 'accepted'",
                ],
                "validation_timeout_seconds": 5,
            }
        ],
        "bindings": [{"todo_id": "todo_export", "criterion_ids": ["export"]}],
    }
    document_path = tmp_path / "acceptance.json"
    document_path.write_text(json.dumps(document))

    def run(*arguments):
        return run_json_cli_result(
            *arguments,
            registry_path=registry,
            runtime_root=runtime,
        )

    def cli(*arguments):
        return run("goal-acceptance", *arguments, "--goal-id", "goal-acceptance")

    return project, document_path, cli, run


def test_owner_configure_validation_failure_success_and_disable(acceptance_goal):
    project, document, cli, _ = acceptance_goal
    code, before = cli("inspect")
    assert code == 0 and before["goal_acceptance_contract"] == {"enabled": False}
    revision = before["provider_revision"]
    code, preview = cli(
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        revision,
    )
    assert code == 0 and preview["status"] == "planned"
    assert cli("inspect")[1] == before
    code, configured = cli(
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        revision,
        "--operation-id",
        "configure-acceptance",
        "--execute",
    )
    assert code == 0, configured
    contract = configured["goal_acceptance_contract"]
    assert contract["enabled"] and contract["tasks"][0]["state"] == "ready"
    assert "validation_argv" not in json.dumps(configured)
    code, failed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 1 and failed["checks_passed"] is False
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "failed"
    (project / "artifact.txt").write_text("accepted")
    code, passed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 0 and passed["checks_passed"] is True, passed
    observed = cli("inspect")[1]
    assert observed["goal_acceptance_contract"]["status"] == "accepted"
    assert "validation_argv" not in json.dumps(observed)
    code, disabled = cli(
        "disable",
        "--expected-provider-revision",
        observed["provider_revision"],
        "--execute",
    )
    assert code == 0 and disabled["goal_acceptance_contract"] == {"enabled": False}


def test_agents_cannot_rewrite_acceptance_and_stale_owner_write_rejects(
    acceptance_goal,
):
    _, document, cli, _ = acceptance_goal
    original = cli("inspect")[1]
    args = (
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        original["provider_revision"],
        "--execute",
    )
    code, error = cli(*args, "--agent-id", "agent-a")
    assert code == 1, error
    assert cli("inspect")[1] == original
    code, configured = cli(*args)
    assert code == 0, configured
    code, error = cli(*args)
    assert code == 1 and "revision" in error["error"], error
    assert cli("inspect")[1]["goal_acceptance_contract"]["revision"] == 1


def test_cli_rejects_claimed_results_and_missing_configuration_basis(acceptance_goal):
    _, document, cli, _ = acceptance_goal
    code, result = cli("configure", "--document", str(document), "--execute")
    assert code == 1 and "expected-provider-revision" in result["error"]
    code, result = cli("verify", "--document", str(document), "--execute")
    assert code == 1 and "configuration arguments" in result["error"]


@pytest.mark.parametrize("change", ["before", "during"])
def test_changed_verifier_cannot_turn_missing_artifact_into_acceptance(
    acceptance_goal, change
):
    project, document_path, cli, _ = acceptance_goal
    verifier = project / "verify.py"
    verifier.write_text(
        "from pathlib import Path\nPath(__file__).write_text('pass\\n')\n"
        if change == "during"
        else "from pathlib import Path\nassert Path('artifact.txt').read_text() == 'accepted'\n"
    )
    document = json.loads(document_path.read_text())
    criterion = document["criteria"][0]
    criterion["validation_argv"] = [sys.executable, "verify.py"]
    criterion["validation_files"] = [
        {
            "path": "verify.py",
            "sha256": hashlib.sha256(verifier.read_bytes()).hexdigest(),
        }
    ]
    document_path.write_text(json.dumps(document))
    basis = cli("inspect")[1]["provider_revision"]
    code, configured = cli(
        "configure",
        "--document",
        str(document_path),
        "--expected-provider-revision",
        basis,
        "--execute",
    )
    assert code == 0, configured
    if change == "before":
        verifier.write_text("pass\n")
    code, result = cli("verify", "--execute")
    assert code == 1 and result["checks_passed"] is False, result
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "failed"


def test_passing_artifact_check_does_not_hide_unconfirmed_work(acceptance_goal):
    project, document_path, cli, _ = acceptance_goal
    document = json.loads(document_path.read_text())
    document["bindings"] = []
    document_path.write_text(json.dumps(document))
    (project / "artifact.txt").write_text("accepted")
    basis = cli("inspect")[1]["provider_revision"]
    code, result = cli(
        "configure",
        "--document",
        str(document_path),
        "--expected-provider-revision",
        basis,
        "--execute",
    )
    assert code == 0, result
    code, result = cli("verify", "--execute")
    assert (
        code == 1
        and result["checks_passed"] is True
        and result["acceptance_ready"] is False
    )
    assert result["goal_acceptance_contract"]["status"] == "held"


def test_bound_todo_completes_only_after_its_criteria_actually_run(acceptance_goal):
    """The completion plan names criteria; this proves the host runs them.

    Only real execution separates the two attempts below: the configuration,
    the binding and the command are identical, and just the artifact differs.
    """
    project, document, cli, run = acceptance_goal
    code, configured = cli(
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        cli("inspect")[1]["provider_revision"],
        "--execute",
    )
    assert code == 0 and configured["goal_acceptance_contract"]["tasks"] == [
        {
            "todo_id": "todo_export",
            "state": "ready",
            "criterion_ids": ["export"],
            "reason": "The owner confirmed this work's current acceptance association.",
            "reason_code": "goal_acceptance_ready",
            "applicable": True,
        }
    ], configured
    complete = (
        "todo",
        "complete",
        "--todo-id",
        "todo_export",
        "--goal-id",
        "goal-acceptance",
        "--agent-id",
        "agent-a",
    )
    code, refused = run(*complete)
    assert code == 1 and refused["reason_code"] == "goal_acceptance_validation_rejected", refused
    assert refused["goal_acceptance_validation_failure"]["criterion_id"] == "export"
    assert refused["goal_acceptance_validation_failure"]["validation_status"] == "command_failed"
    assert "configured criterion" in refused["reason"]
    assert "validation_argv" not in json.dumps(refused)

    (project / "artifact.txt").write_text("accepted")
    code, completed = run(*complete)
    assert code == 0 and completed["changed"] is True, completed
    evidence = completed["goal_acceptance_completion"]
    assert evidence["results"] == [
        {"criterion_id": "export", "exit_code": 0, "passed": True}
    ]
    assert evidence["contract_digest"] == configured["goal_acceptance_contract"]["digest"]
    assert evidence["source_binding"]["todo_id"] == "todo_export"
    assert "validation_argv" not in json.dumps(completed)
    # Todo criteria passing is not an independent judgment that the Goal is met.
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "unverified"


def _configure(cli, document):
    code, configured = cli(
        "configure",
        "--document",
        str(document),
        "--expected-provider-revision",
        cli("inspect")[1]["provider_revision"],
        "--execute",
    )
    assert code == 0, configured
    return configured


@pytest.mark.parametrize(
    "escape",
    [
        pytest.param((), id="supersede"),
        pytest.param(("--task-class", "blocker"), id="task_class"),
        pytest.param(
            ("--status", "deferred", "--resume-when", "resume_at:2099-01-01T00:00:00Z"),
            id="deferred",
        ),
    ],
)
def test_bound_work_cannot_be_closed_by_editing_its_way_out_of_the_gate(
    acceptance_goal, escape
):
    """Acceptance binds work, so neither a second terminal verb nor a field the
    guarded party may rewrite can close it without the criteria running.

    `supersede` reaches the same `done: true` write as `complete`; `task_class`
    and `status` are inputs to the old applicability test. Each row closes the
    Todo terminally in the absence of `artifact.txt` if the gate is keyed on the
    command name or on the Todo's current shape.
    """
    _, document, cli, run = acceptance_goal
    _configure(cli, document)
    common = ("--todo-id", "todo_export", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")
    if escape:
        code, updated = run("todo", "update", *common, *escape)
        assert code == 0, updated
        terminal = ("todo", "complete", *common)
    else:
        terminal = ("todo", "supersede", *common, "--reason", "pivot")
    code, refused = run(*terminal)
    assert code == 1, refused
    if escape and "--resume-when" in escape:
        # Completion checks the unsatisfied dependency before artifact validation.
        # Removing that wait must still leave the acceptance binding in force.
        assert refused["reason_code"] == "todo_dependency_pending", refused
        code, resumed = run(
            "todo", "update", *common, "--status", "open", "--clear-resume-when"
        )
        assert code == 0, resumed
        code, refused = run(*terminal)
        assert code == 1, refused
        assert refused["reason_code"] == "goal_acceptance_validation_rejected", refused
    else:
        assert refused["reason_code"] in {
            "goal_acceptance_validation_required",
            "goal_acceptance_stale",
        }, refused
    assert "validation_argv" not in json.dumps(refused)
    # The refusal must be a refusal, not a report: the work stays open.
    contract = cli("inspect")[1]["goal_acceptance_contract"]
    assert contract["status"] != "accepted"
    assert contract["verification"] is None


def test_real_cli_stale_binding_is_projected_for_agent_replan(acceptance_goal):
    _, document, cli, run = acceptance_goal
    basis = cli("inspect")[1]["provider_revision"]
    code, configured = cli(
        "configure", "--document", str(document),
        "--expected-provider-revision", basis, "--execute",
    )
    assert code == 0, configured
    code, updated = run(
        "todo", "update", "--todo-id", "todo_export", "--goal-id", "goal-acceptance",
        "--agent-id", "agent-a", "--text", "Write the export artifact and checksum",
    )
    assert code == 0, updated
    assert cli("inspect")[1]["goal_acceptance_contract"]["tasks"][0]["state"] == "stale"
    code, projected = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")
    assert code == 0, projected
    assert projected["goal_frontier_projection"]["acceptance_gaps"][0]["kind"] == "goal_acceptance_stale"
    assert "autonomous_replan_obligation" in projected, sorted(projected)
    obligation = projected["autonomous_replan_obligation"]
    assert obligation["triggers"][0]["kind"] == "goal_acceptance_stale"
    assert obligation["triggers"][0]["vision_todo_ids"] == ["todo_export"]
    assert len(obligation["triggers"][0]["frontier_revision"]) == 64


def test_preview_discloses_the_criteria_the_real_call_will_run(acceptance_goal):
    """A preview that hid this showed an unconditional close the real call gates."""
    _, document, cli, run = acceptance_goal
    configured = _configure(cli, document)
    common = ("--todo-id", "todo_export", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")
    code, preview = run("todo", "complete", *common, "--dry-run")
    assert code == 0, preview
    assert preview["goal_acceptance_pending"] == {
        "contract_revision": configured["goal_acceptance_contract"]["revision"],
        "contract_digest": configured["goal_acceptance_contract"]["digest"],
        "criterion_ids": ["export"],
    }
    assert "validation_argv" not in json.dumps(preview)
    assert cli("inspect")[1]["goal_acceptance_contract"]["verification"] is None


def test_unbound_work_projects_recovery_without_changing_acceptance(acceptance_goal):
    _, document_path, cli, run = acceptance_goal
    code, monitor = run(
        "todo", "add", "--goal-id", "goal-acceptance", "--role", "agent",
        "--text", "Observe the public release", "--task-class", "continuous_monitor",
        "--action-kind", "monitor", "--claimed-by", "agent-a",
        "--target-key", "release:acceptance-recovery", "--cadence", "30m",
        "--next-due-at", "2000-01-01T00:00:00+00:00", "--watch-only",
    )
    assert code == 0, monitor
    document = json.loads(document_path.read_text())
    document["bindings"] = []
    document_path.write_text(json.dumps(document))
    _configure(cli, document_path)
    before = cli("inspect")[1]
    code, quota = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")
    assert code == 0, quota
    assert quota["decision"] == "autonomous_replan_required", quota
    assert quota.get("selected_todo") is None
    assert quota.get("agent_lane_next_action") is None
    packet = quota["autonomous_replan_obligation"]
    assert any(trigger["kind"] == "goal_acceptance_unbound" for trigger in packet["triggers"])
    code, refused = run("todo", "complete", "--goal-id", "goal-acceptance", "--todo-id", "todo_export", "--agent-id", "agent-a")
    assert code == 1 and refused["reason_code"] == "goal_acceptance_unbound", refused
    assert cli("inspect")[1] == before


def test_owner_scope_correction_restores_independent_work_through_real_cli(acceptance_goal):
    project, document_path, cli, run = acceptance_goal
    from loopx.control_plane.goals.goal_frontier.acceptance import acceptance_gaps_from_held_goal_binding

    document = json.loads(document_path.read_text())
    _configure(cli, document_path)
    code, added = run("todo", "add", "--goal-id", "goal-acceptance", "--role", "agent",
                      "--text", "Review independent public sources", "--claimed-by", "agent-a")
    assert code == 0, added
    code, listed = run("todo", "list", "--goal-id", "goal-acceptance", "--role", "agent")
    assert code == 0, listed
    independent = next(row for row in listed["todos"] if row["todo_id"] != "todo_export")
    todo_id = independent["todo_id"]
    assert independent["goal_acceptance_guard"]["state"] == "unbound"
    claim = ("todo", "claim", "--goal-id", "goal-acceptance", "--todo-id", todo_id,
             "--agent-id", "agent-a", "--claimed-by", "agent-a")
    refused_code, refused = run(*claim)
    assert refused_code == 1 and "goal_acceptance_unbound" in json.dumps(refused), refused
    document["scope"] = {"kind": "selected_work", "todo_ids": ["todo_export"]}
    document_path.write_text(json.dumps(document))
    configured = _configure(cli, document_path)
    contract = configured["goal_acceptance_contract"]
    assert contract["scope"] == document["scope"]
    code, claimed = run(*claim)
    assert code == 0, claimed
    after = run("todo", "list", "--goal-id", "goal-acceptance", "--role", "agent")[1]
    recovered = next(row for row in after["todos"] if row["todo_id"] == todo_id)
    assert "goal_acceptance_guard" not in recovered
    assert acceptance_gaps_from_held_goal_binding(
        {"goal_acceptance_contract": contract}, after["todos"], agent_id="agent-a") == []
    # The scoped work's real validator still fails with no artifact; narrowing
    # the scope does not confer acceptance or manufacture validation receipts.
    code, verified = cli("verify", "--execute")
    assert code == 1 and verified["checks_passed"] is False
    (project / "artifact.txt").write_text("accepted")
    assert cli("verify", "--execute")[0] == 0
