"""One owner contract, actual validation and independent canonical readback."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
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
from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload


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
        code, payload = run_json_cli_result(
            *arguments,
            registry_path=registry,
            runtime_root=runtime,
        )
        if arguments[:2] == ("quota", "should-run"):
            payload = read_cli_policy_payload(json.dumps(payload), returncode=code)
        return code, payload

    def cli(*arguments):
        return run("goal-acceptance", *arguments, "--goal-id", "goal-acceptance")

    return project, document_path, cli, run




def test_outcome_refinement_retains_legacy_active_goal_admission(acceptance_goal):
    project, _, cli, _ = acceptance_goal
    registry_path = project.parent / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0].pop("status", None)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    document = project / "outcome.json"
    document.write_text(json.dumps({"outcome_id": "export", "owner_agent_id": "agent-a",
        "objective": "Produce the export", "requirements": [{"id": "content", "description": "Retain the content"}],
        "source": {"ref": "message:initial", "summary": "The owner requested an export"}}), encoding="utf-8")
    revision = cli("inspect")[1]["provider_revision"]
    args = ("refine", "--agent-id", "agent-a", "--document", str(document),
        "--expected-provider-revision", revision, "--operation-id", "legacy-outcome", "--execute")
    code, result = cli(*args)
    assert code == 0 and result["status"] == "applied", result
    before = cli("inspect")[1]
    for status in ("achieved", "abandoned"):
        registry["goals"][0]["status"] = status
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        code, denied = cli(*args)
        assert code == 1 and "active Goal" in denied["error"], denied
        assert cli("inspect")[1] == before, "an inactive Goal cannot mutate or bypass admission through replay"

    # Stop is a separate typed owner decision, including in legacy registries.
    registry["goals"][0]["activation"] = {
        "schema_version": "loopx_goal_activation_v1", "state": "stopped",
    }
    for status in ("active", None):
        if status is None:
            registry["goals"][0].pop("status", None)
        else:
            registry["goals"][0]["status"] = status
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        code, denied = cli(*args)
        assert code == 1 and "active Goal" in denied["error"], denied
        update = project / "stopped-outcome.json"
        update.write_text(json.dumps({"outcome_id": "export",
            "requirements": [{"id": "ordering", "description": "Retain the ordering"}],
            "source": {"ref": "message:later", "summary": "The owner added an ordering requirement"}}), encoding="utf-8")
        code, denied = cli("refine", "--agent-id", "agent-a", "--document", str(update),
            "--expected-provider-revision", before["provider_revision"],
            "--operation-id", "stopped-outcome", "--execute")
        assert code == 1 and "active Goal" in denied["error"], denied
        assert cli("inspect")[1] == before, "Stop rejects both saved operation replay and new refinements"


@pytest.mark.parametrize("prior_activation", [None, "active", "stopped"])
def test_upgrade_retains_pause_then_owner_resume_enables_refinement(acceptance_goal, prior_activation):
    project, _, cli, run = acceptance_goal
    revision = cli("inspect")[1]["provider_revision"]
    registry_path = project.parent / "registry.json"
    registry = json.loads(registry_path.read_bytes())
    goal = registry["goals"][0]
    goal.update(status="paused", activation_state="active")
    if prior_activation is None:
        goal.pop("activation", None)
    else:
        goal["activation"] = {"schema_version": "loopx_goal_activation_v1", "state": prior_activation}
    registry_path.write_text(json.dumps(registry))
    original, state_before = registry_path.read_bytes(), (project / "state.md").read_bytes()
    code, preview = run("authority-archive", "upgrade")
    assert code == 0, preview
    assert registry_path.read_bytes() == original
    code, upgraded = run("authority-archive", "upgrade", "--execute")
    assert code == 0, upgraded
    migrated = json.loads(registry_path.read_bytes())["goals"][0]
    assert "activation_state" not in migrated and migrated.get("activation", {}).get("state") == "stopped", migrated
    rows = upgraded["goal_activation_upgrade"]["results"]
    assert any(Path(row["backup"]).read_bytes() == original for row in rows if row.get("backup"))
    assert migrated["status"] == "active", "registration no longer owns pause"
    document = project / "resumed-outcome.json"
    document.write_text(json.dumps({"outcome_id": "export", "owner_agent_id": "agent-a",
        "objective": "Produce the export", "requirements": [{"id": "content", "description": "Retain content"}],
        "source": {"ref": "message:owner-resume", "summary": "The owner resumed the export"}}))
    assert cli("inspect")[1]["provider_revision"] == revision, "metadata upgrade must preserve the canonical authority revision"
    args = ("refine", "--agent-id", "agent-a", "--document", str(document),
        "--expected-provider-revision", revision, "--operation-id", "resumed-outcome", "--execute")
    assert cli(*args)[0] == 1, "upgrading metadata never resumes work"
    code, resumed = run("goal-lifecycle", "--goal-id", "goal-acceptance", "--operation", "resume",
        "--actor-kind", "owner", "--execute")
    assert code == 0 and resumed["readback"]["verified"], resumed
    assert cli(*args)[0] == 0
    assert (project / "state.md").read_bytes() == state_before
    assert run("goal-lifecycle", "--goal-id", "goal-acceptance", "--operation", "stop",
        "--actor-kind", "owner", "--execute")[0] == 0
    assert cli(*args)[0] == 1, "typed stop also guards operation replay"


def test_outcome_requirement_identity_survives_configure_verify_and_readback(acceptance_goal):
    project, checks_file, cli, _ = acceptance_goal
    requirement_id = "export:content@v1"
    document = project / "outcome.json"
    document.write_text(json.dumps({"outcome_id": "export", "owner_agent_id": "agent-a",
        "objective": "Produce the export", "todo_ids": ["todo_export"],
        "requirements": [{"id": requirement_id, "description": "Retain the content"}],
        "source": {"ref": "message:initial", "summary": "The owner requested an export"}}), encoding="utf-8")
    code, result = cli("refine", "--agent-id", "agent-a", "--document", str(document),
        "--expected-provider-revision", cli("inspect")[1]["provider_revision"],
        "--operation-id", "opaque-requirement", "--execute")
    assert code == 0, result
    checks_file.write_text(json.dumps({"outcome_id": "export", "criteria": [
        {"id": requirement_id, "validation_argv": [sys.executable, "-c", "pass"]}]}), encoding="utf-8")
    code, result = cli("configure", "--document", str(checks_file), "--operation-id", "opaque-check",
        "--expected-provider-revision", cli("inspect")[1]["provider_revision"], "--execute")
    assert code == 0, result
    code, verified = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 0 and verified["checks_passed"], verified
    current = cli("inspect")[1]["goal_acceptance_contract"]
    assert current["verification"]["results"][0]["criterion_id"] == requirement_id
    requirement = current["outcome_requirements"]["outcomes"][0]["requirements"][0]
    assert requirement["id"] == requirement_id and requirement["status"] == "accepted"


def test_owner_configure_validation_failure_success_and_disable(acceptance_goal):
    project, document, cli, _ = acceptance_goal
    code, before = cli("inspect")
    assert code == 0 and before["goal_acceptance_contract"] == {}
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
    assert "enabled" not in contract and contract["tasks"][0]["state"] == "ready"
    assert "validation_argv" not in json.dumps(configured)
    code, failed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 1 and failed["checks_passed"] is False
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "failed"
    (project / "artifact.txt").write_text("accepted")
    code, passed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 0 and passed["checks_passed"] is True, passed
    assert passed["status"] == "applied" and passed["goal_id"] == "goal-acceptance"
    assert all("oracle" not in row for row in passed["goal_acceptance_contract"]["criteria"])
    observed = cli("inspect")[1]
    assert observed["goal_acceptance_contract"]["status"] == "accepted"
    assert "validation_argv" not in json.dumps(observed)
    code, disabled = cli(
        "disable",
        "--expected-provider-revision",
        observed["provider_revision"],
        "--execute",
    )
    assert code == 0 and disabled["goal_acceptance_contract"] == {}


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


def test_goal_only_checks_keep_local_work_free_and_outcome_unmet(acceptance_goal):
    project, checks_file, cli, run = acceptance_goal
    outcome_file = project / "goal-outcome.json"
    description = "The external work commitment is fulfilled"
    for outcome_id in ("campaign", "independent"):
        outcome_file.write_text(json.dumps({"outcome_id": outcome_id, "owner_agent_id": "agent-a",
            "objective": f"Complete the authorized {outcome_id} work", "requirements": [
                {"id": "commitment", "description": description}],
            "source": {"ref": f"owner:{outcome_id}", "summary": "The owner retained a Goal-wide commitment"}}))
        code, refined = cli("refine", "--agent-id", "agent-a", "--document", str(outcome_file),
            "--expected-provider-revision", cli("inspect")[1]["provider_revision"],
            "--operation-id", f"goal-only-{outcome_id}", "--execute")
        assert code == 0, refined
    default_contract = cli("inspect")[1]["goal_acceptance_contract"]
    assert default_contract.get("verification") is None
    assert {gap["outcome_id"] for gap in default_contract["outcome_requirements"]["acceptance_gaps"]} == {
        "campaign", "independent"}

    command = [sys.executable, "-c",
        "from pathlib import Path; assert Path('commitment.txt').read_text() == 'fulfilled'"]
    checks_file.write_text(json.dumps({"scope": {"kind": "goal"}, "objective": "Verify the Goal artifact",
        "non_goals": [], "bindings": [], "criteria": [
            {"id": "commitment", "description": description, "validation_argv": command}]}))
    code, configured = cli("configure", "--document", str(checks_file),
        "--expected-provider-revision", cli("inspect")[1]["provider_revision"],
        "--operation-id", "standalone-goal-check", "--execute")
    assert code == 0, configured
    artifact = project / "commitment.txt"
    artifact.write_text("fulfilled")
    code, passed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 0 and passed["checks_passed"] is True, passed
    current = cli("inspect")[1]["goal_acceptance_contract"]
    assert current["status"] == "accepted"
    outcomes = current["outcome_requirements"]["outcomes"]
    assert {outcome["id"]: outcome["requirements"][0]["status"] for outcome in outcomes} == {
        "campaign": "unverified", "independent": "unverified"}
    artifact.unlink()

    checks_file.write_text(json.dumps({"outcome_id": "campaign", "scope": {"kind": "goal"},
        "criteria": [{"id": "commitment", "validation_argv": command}]}))
    code, configured = cli("configure", "--document", str(checks_file),
        "--expected-provider-revision", cli("inspect")[1]["provider_revision"],
        "--operation-id", "goal-only-configure", "--execute")
    assert code == 0, configured
    contract = configured["goal_acceptance_contract"]
    assert contract["scope"] == {"kind": "goal"}
    assert contract["tasks"] == [] and contract["held_todo_ids"] == []
    assert contract["status"] == "stale"
    assert contract["outcome_requirements"]["acceptance_gaps"][0]["kind"] == "goal_outcome_uncovered"
    code, failed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 1 and failed["checks_passed"] is False, failed
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "failed"
    code, completed = run("todo", "complete", "--goal-id", "goal-acceptance",
        "--todo-id", "todo_export", "--agent-id", "agent-a")
    assert code == 0, completed
    code, quota = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a")
    assert code == 0, quota
    assert quota["decision"] == "autonomous_replan_required", quota
    assert quota["goal_frontier_projection"].get("terminal_state") is None
    artifact.write_text("fulfilled")
    code, passed = cli("verify", "--agent-id", "agent-a", "--execute")
    assert code == 0 and passed["checks_passed"] is True, passed
    current = cli("inspect")[1]["goal_acceptance_contract"]
    assert current["status"] == "accepted"
    outcomes = current["outcome_requirements"]["outcomes"]
    assert {outcome["id"]: outcome["requirements"][0]["status"] for outcome in outcomes} == {
        "campaign": "accepted", "independent": "unverified"}
    assert {gap["outcome_id"] for gap in current["outcome_requirements"]["acceptance_gaps"]} == {"independent"}
