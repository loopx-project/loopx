"""Product CLI and task-planning checkpoint tests for general planning_batch."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_source_fixture
from loopx.bootstrap_command_pack import build_start_goal_guided_packet
from loopx.cli import main
from loopx.control_plane.goals.planning_batch import (
    DirectPlanningBatchBackend,
    PLANNING_BATCH_SCHEMA_VERSION,
    planning_batch_schema,
)
from loopx.control_plane.goals.task_planning import (
    build_task_planning_packet,
    validate_task_plan_readback,
)


@pytest.fixture(params=["file", "sqlite"])
def product_goal(tmp_path: Path, request: pytest.FixtureRequest) -> dict[str, object]:
    state = tmp_path / "state.md"
    state.write_text("# Active Goal State\n\n## Agent Todos\n\n## User Todos\n")
    runtime_root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "product-plan-goal",
                        "repo": str(tmp_path),
                        "state_file": str(state),
                        "status": "active",
                        "coordination": {"registered_agents": ["planner", "peer"]},
                    }
                ],
            }
        )
    )
    initialize_canonical_source_fixture(
        runtime_root, "product-plan-goal", state_path=state, provider=request.param
    )
    return {
        "project": tmp_path,
        "registry": registry,
        "runtime_root": runtime_root,
        "goal_id": "product-plan-goal",
        "agent_id": "planner",
        "provider": request.param,
    }


def _run_cli(*argv: str, expected_rc: int = 0) -> dict[str, object]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(list(argv))
    payload = json.loads(out.getvalue())
    assert rc == expected_rc, payload
    return payload


def test_todo_plan_batch_schema_and_help_work_without_goal_id():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(["--format", "json", "todo", "plan-batch", "--schema"])
    assert rc == 0
    schema = json.loads(out.getvalue())
    expected = planning_batch_schema()
    assert schema["schema_version"] == PLANNING_BATCH_SCHEMA_VERSION
    assert schema["required_fields"] == ["expected_provider_revision", "outcome", "todos"]
    assert schema["optional_fields"] == ["input_digest", "outcome_id", "operation_id"]
    assert schema["fields"] == expected["fields"]

    # Subprocess CLI invocation without --goal-id also succeeds cleanly.
    proc = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", "todo", "plan-batch", "--schema"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["schema_version"] == PLANNING_BATCH_SCHEMA_VERSION

    help_proc = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "todo", "plan-batch", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    assert "--document" in help_proc.stdout
    assert "--schema" in help_proc.stdout
    assert "--execute" in help_proc.stdout


def test_product_cli_plan_batch_preview_apply_replay_and_checkpoint_readback(
    product_goal: dict[str, object],
):
    project = Path(str(product_goal["project"]))
    registry = Path(str(product_goal["registry"]))
    runtime_root = Path(str(product_goal["runtime_root"]))
    goal_id = str(product_goal["goal_id"])
    agent_id = str(product_goal["agent_id"])
    task_text = "Unify batch planning across LoopX product CLI/HTTP and benchmark."

    prefix = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime_root)]
    before = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    assert before["planning_batch"]["applicable"] is True
    assert before["planning_batch"]["schema_version"] == PLANNING_BATCH_SCHEMA_VERSION
    write_step = before["ordered_steps"][1]
    assert write_step["id"] == "write_ordered_todos"
    assert "todo plan-batch" in write_step["batch_command_template"]
    assert "todo plan-batch --schema" in write_step["batch_schema_command"]

    proposal = {
        "input_digest": before["input_digest"],
        "expected_provider_revision": before["goal_acceptance"]["provider_revision"],
        "outcome_id": "unified-batch-planning",
        "operation_id": "product-batch-op-1",
        "outcome": {
            "objective": "Provide one reusable batch planning capability in LoopX.",
            "requirements": [
                {"id": "cli-reuse", "description": "Expose `loopx todo plan-batch` for product callers."},
                {"id": "bench-reuse", "description": "Reuse the core owner in `benchmark.runtime.planning_batch`."},
            ],
            "source": {"ref": "checkpoint:product", "summary": "Product task-planning checkpoint."},
        },
        "todos": [
            {"text": "[P0] Implement core planning_batch owner", "action_kind": "implement"},
            {
                "text": "Validate CLI and HTTP batch readback",
                "action_kind": "validate",
                "priority": "P1",
                "target_key": "batch-readback",
            },
        ],
    }
    proposal_path = project / "proposal.json"
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")

    # 1. Dry-run preview mutates neither Goal acceptance nor Todos nor rollout logs.
    preview = _run_cli(
        *prefix,
        "todo",
        "plan-batch",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(project),
        "--text",
        task_text,
        "--document",
        str(proposal_path),
        "--dry-run",
    )
    assert preview["status"] == "planned"
    assert preview["dry_run"] is True
    assert preview["changed"] is False
    assert preview["planned_todo_count"] == 2
    assert preview["outcome_id"] == "unified-batch-planning"
    mid = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    assert mid["existing_todos"] == []
    assert mid["goal_acceptance"]["provider_revision"] == before["goal_acceptance"]["provider_revision"]
    assert not list(runtime_root.rglob("*rollout*"))

    # 2. Reject secret/over-limit values upfront without leaking caller content.
    bad_path = project / "bad-proposal.json"
    bad_path.write_text(
        json.dumps(
            {
                **proposal,
                "outcome": {
                    **proposal["outcome"],
                    "source": {"ref": "/private/secret/path/ref", "summary": "Summary"},
                },
            }
        ),
        encoding="utf-8",
    )
    rejected = _run_cli(
        *prefix,
        "todo",
        "plan-batch",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(project),
        "--text",
        task_text,
        "--document",
        str(bad_path),
        "--execute",
        expected_rc=1,
    )
    assert "outcome.source.ref" in str(rejected["error"])
    assert "/private/secret/path/ref" not in json.dumps(rejected)

    # 3. Apply batch and verify full canonical readback + validate_task_plan_readback.
    applied = _run_cli(
        *prefix,
        "todo",
        "plan-batch",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(project),
        "--text",
        task_text,
        "--document",
        str(proposal_path),
        "--execute",
    )
    assert applied["status"] == "ready"
    assert applied["dry_run"] is False
    assert applied["changed"] is True
    assert applied["state_readback_verified"] is True
    todo_ids = applied["todo_ids"]
    assert isinstance(todo_ids, list) and len(todo_ids) == 2

    after = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    assert after["planning_batch"]["applicable"] is False
    verified = validate_task_plan_readback(
        {"input_digest": applied["input_digest"], "status": applied["status"], "todo_ids": todo_ids},
        before,
        after,
    )
    assert verified["state_readback_verified"] is True
    assert verified["goal_requirements_readback"]["outcome_ids"] == ["unified-batch-planning"]
    assert verified["goal_requirements_readback"]["linked_todo_ids"] == todo_ids

    by_id = {item["todo_id"]: item for item in after["existing_todos"]}
    assert [by_id[tid]["priority"] for tid in todo_ids] == ["P0", "P1"]
    assert by_id[todo_ids[1]]["target_key"] == "batch-readback"

    # 4. Idempotent replay with the unchanged proposal and initial provider_revision.
    replayed = _run_cli(
        *prefix,
        "todo",
        "plan-batch",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(project),
        "--text",
        task_text,
        "--document",
        str(proposal_path),
        "--execute",
    )
    assert replayed["status"] == "ready"
    assert replayed["idempotent_replay"] is True
    assert replayed["changed"] is False
    assert replayed["todo_ids"] == todo_ids
    assert replayed["provider_revision"] == applied["provider_revision"]


def test_product_cli_advertised_template_binds_input_digest_and_rejects_mismatch(
    product_goal: dict[str, object],
):
    project = Path(str(product_goal["project"]))
    registry = Path(str(product_goal["registry"]))
    runtime_root = Path(str(product_goal["runtime_root"]))
    goal_id = str(product_goal["goal_id"])
    agent_id = str(product_goal["agent_id"])
    task_text = "Advertise --text in planning packet templates and verify input_digest before writes."

    before = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    apply_template = str(before["planning_batch"]["apply_command_template"])
    preview_template = str(before["planning_batch"]["preview_command_template"])
    assert "--text=" in apply_template
    assert "--text=" in preview_template

    def run_template(template: str, proposal_file: Path) -> subprocess.CompletedProcess[str]:
        tokens = shlex.split(template.replace("'<proposal.json>'", shlex.quote(str(proposal_file))))
        assert tokens[0] == "loopx"
        return subprocess.run(
            [sys.executable, "-m", "loopx.cli", *tokens[1:]],
            capture_output=True,
            text=True,
            timeout=60,
        )

    base_proposal = {
        "input_digest": before["input_digest"],
        "expected_provider_revision": before["goal_acceptance"]["provider_revision"],
        "outcome_id": "template-input-binding",
        "operation_id": "template-binding-op-1",
        "outcome": {
            "objective": "Verify template input_digest binding.",
            "requirements": [{"id": "req-bind", "description": "Bind planning packet input_digest."}],
            "source": {"ref": "checkpoint:template", "summary": "Planning packet template check."},
        },
        "todos": [
            {"text": "[P0] Implement input binding", "action_kind": "implement"},
            {"text": "[P1] Verify input binding", "action_kind": "validate"},
        ],
    }

    # 1. Wrong input_digest submitted through the advertised template rejects with zero writes.
    wrong_path = project / "wrong-digest.json"
    wrong_path.write_text(json.dumps({**base_proposal, "input_digest": "0" * 64}), encoding="utf-8")
    wrong_proc = run_template(apply_template, wrong_path)
    assert wrong_proc.returncode == 1
    assert "Planning input changed" in json.loads(wrong_proc.stdout)["error"]
    mid = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    assert mid["goal_acceptance"]["provider_revision"] == before["goal_acceptance"]["provider_revision"]
    assert mid["existing_todos"] == []

    # 2. Direct CLI without --text must refuse a proposal carrying input_digest before any write.
    good_path = project / "good-digest.json"
    good_path.write_text(json.dumps(base_proposal), encoding="utf-8")
    no_text = _run_cli(
        "--format",
        "json",
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime_root),
        "todo",
        "plan-batch",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(project),
        "--document",
        str(good_path),
        "--execute",
        expected_rc=1,
    )
    assert "input_digest" in str(no_text["error"])
    after_no_text = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    assert after_no_text["goal_acceptance"]["provider_revision"] == before["goal_acceptance"]["provider_revision"]
    assert after_no_text["existing_todos"] == []

    # 3. Valid input_digest via advertised apply_command_template commits and passes readback.
    good_proc = run_template(apply_template, good_path)
    assert good_proc.returncode == 0, good_proc.stdout + good_proc.stderr
    applied = json.loads(good_proc.stdout)
    assert applied["status"] == "ready"
    assert applied["input_digest"] == before["input_digest"]
    after = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    readback = validate_task_plan_readback(
        {"input_digest": applied["input_digest"], "status": applied["status"], "todo_ids": applied["todo_ids"]},
        before,
        after,
    )
    assert readback["state_readback_verified"] is True


def test_product_cli_interrupted_retry_with_explicit_outcome_id_rejects_mutated_todo(
    product_goal: dict[str, object],
):
    project = Path(str(product_goal["project"]))
    registry = Path(str(product_goal["registry"]))
    runtime_root = Path(str(product_goal["runtime_root"]))
    goal_id = str(product_goal["goal_id"])
    agent_id = str(product_goal["agent_id"])
    task_text = "Reject mutated Todo retry even when explicit outcome_id and operation_id match."

    prefix = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime_root)]
    before = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    initial_rev = str(before["goal_acceptance"]["provider_revision"])
    proposal = {
        "input_digest": before["input_digest"],
        "expected_provider_revision": initial_rev,
        "outcome_id": "explicit-retry-outcome",
        "operation_id": "explicit-retry-op",
        "outcome": {
            "objective": "Verify interrupted retry binds full normalized proposal.",
            "requirements": [{"id": "req-full", "description": "Preserve original ordered Todos on retry."}],
            "source": {"ref": "checkpoint:retry", "summary": "Explicit outcome retry guard."},
        },
        "todos": [
            {"text": "[P0] Original first Todo", "action_kind": "implement"},
            {"text": "[P1] Original second Todo", "action_kind": "validate", "priority": "P1"},
        ],
    }

    # Simulate interruption after :declare and :todo:0 committed with lost response.
    from loopx.control_plane.goals.planning_batch import (
        DirectPlanningBatchBackend,
        validate_planning_batch_proposal,
    )
    import hashlib

    validated = validate_planning_batch_proposal(proposal, require_input_digest=True, allow_product_identities=True)
    proposal_hash = hashlib.sha256(json.dumps(validated, sort_keys=True).encode()).hexdigest()
    backend = DirectPlanningBatchBackend(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        project=project,
        text=task_text,
    )
    backend.inspect_state()
    declare_op = f"explicit-retry-op:declare:{proposal_hash[:32]}"
    backend.refine_outcome(
        {**validated["outcome"], "outcome_id": "explicit-retry-outcome", "owner_agent_id": agent_id},
        initial_rev,
        declare_op,
        execute=True,
    )
    first_written = backend.add_todo(validated["todos"][0], "explicit-retry-op:todo:0", execute=True)
    first_todo_id = str(first_written["todo_id"])

    interrupted = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    partial_rev = str(interrupted["goal_acceptance"]["provider_revision"])
    assert len(interrupted["existing_todos"]) == 1

    # Retrying with mutated todos[1] (on both --dry-run and --execute) must fail with zero writes.
    mutated = {
        **proposal,
        "todos": [
            proposal["todos"][0],
            {"text": "[P1] Mutated second Todo", "action_kind": "validate", "priority": "P1"},
        ],
    }
    mutated_path = project / "mutated-proposal.json"
    mutated_path.write_text(json.dumps(mutated), encoding="utf-8")

    for flag in ("--dry-run", "--execute"):
        _run_cli(
            *prefix,
            "todo",
            "plan-batch",
            "--goal-id",
            goal_id,
            "--agent-id",
            agent_id,
            "--project",
            str(project),
            "--text",
            task_text,
            "--document",
            str(mutated_path),
            flag,
            expected_rc=1,
        )
        after_mutated = build_task_planning_packet(
            registry_path=registry,
            runtime_root_arg=str(runtime_root),
            goal_id=goal_id,
            agent_id=agent_id,
            text=task_text,
            project=project,
        )
        assert after_mutated["goal_acceptance"]["provider_revision"] == partial_rev
        assert [t["todo_id"] for t in after_mutated["existing_todos"]] == [first_todo_id]

    # Retrying with the exact unchanged original proposal finishes todo:1 and binds [first_todo_id, second_id].
    orig_path = project / "original-proposal.json"
    orig_path.write_text(json.dumps(proposal), encoding="utf-8")
    recovered = _run_cli(
        *prefix,
        "todo",
        "plan-batch",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(project),
        "--text",
        task_text,
        "--document",
        str(orig_path),
        "--execute",
    )
    assert recovered["status"] == "ready"
    assert len(recovered["todo_ids"]) == 2
    assert recovered["todo_ids"][0] == first_todo_id
    final_pkt = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    assert final_pkt["goal_acceptance"]["planning_requirements"]["linked_todo_ids"] == recovered["todo_ids"]
    by_id = {item["todo_id"]: item for item in final_pkt["existing_todos"]}
    assert by_id[recovered["todo_ids"][1]]["text"] == "[P1] Original second Todo"


def test_product_cli_plan_batch_rejects_todo_priority_conflict_before_any_write(
    product_goal: dict[str, object],
):
    project = Path(str(product_goal["project"]))
    registry = Path(str(product_goal["registry"]))
    runtime_root = Path(str(product_goal["runtime_root"]))
    goal_id = str(product_goal["goal_id"])
    agent_id = str(product_goal["agent_id"])
    task_text = "Validate full ordered Todo list before committing Outcome or first Todo."

    prefix = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime_root)]
    before = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    initial_rev = str(before["goal_acceptance"]["provider_revision"])
    conflicting = {
        "input_digest": before["input_digest"],
        "expected_provider_revision": initial_rev,
        "outcome_id": "priority-precheck-outcome",
        "operation_id": "priority-precheck-op",
        "outcome": {
            "objective": "Reject Todo priority conflict before writing Outcome.",
            "requirements": [{"id": "req-priority", "description": "Atomic pre-write validation."}],
            "source": {"ref": "checkpoint:priority", "summary": "Priority precheck boundary."},
        },
        "todos": [
            {"text": "[P0] Valid first step", "action_kind": "implement"},
            {"text": "[P2] Verify the requested result", "action_kind": "validate", "priority": "P0"},
        ],
    }
    proposal_path = project / "conflicting-priority.json"
    proposal_path.write_text(json.dumps(conflicting), encoding="utf-8")

    # Direct --execute must fail before writing the Outcome or first Todo.
    failed = _run_cli(
        *prefix,
        "todo",
        "plan-batch",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(project),
        "--text",
        task_text,
        "--document",
        str(proposal_path),
        "--execute",
        expected_rc=1,
    )
    assert failed["ok"] is False

    mid = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    assert mid["goal_acceptance"]["provider_revision"] == initial_rev
    assert mid["existing_todos"] == []
    assert (
        mid["goal_acceptance"]["goal_acceptance_contract"]
        .get("outcome_requirements", {})
        .get("outcomes", [])
        == []
    )

    # Fixing only Todo 2's priority and retrying with the same batch identity commits cleanly.
    fixed = {
        **conflicting,
        "todos": [
            conflicting["todos"][0],
            {"text": "[P2] Verify the requested result", "action_kind": "validate", "priority": "P2"},
        ],
    }
    proposal_path.write_text(json.dumps(fixed), encoding="utf-8")
    applied = _run_cli(
        *prefix,
        "todo",
        "plan-batch",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
        "--project",
        str(project),
        "--text",
        task_text,
        "--document",
        str(proposal_path),
        "--execute",
    )
    assert applied["status"] == "ready"
    assert applied["changed"] is True
    assert len(applied["todo_ids"]) == 2
    after = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    verified = validate_task_plan_readback(
        {"input_digest": applied["input_digest"], "status": applied["status"], "todo_ids": applied["todo_ids"]},
        before,
        after,
    )
    assert verified["state_readback_verified"] is True


def test_product_cli_checkpoint_three_command_example_round_trip(
    product_goal: dict[str, object],
):
    project = Path(str(product_goal["project"]))
    registry = Path(str(product_goal["registry"]))
    runtime_root = Path(str(product_goal["runtime_root"]))
    goal_id = str(product_goal["goal_id"])
    agent_id = str(product_goal["agent_id"])
    task_text = "Verify checkpoint schema_command, preview_command_template, and apply_command_template round-trip."

    packet = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    batch_meta = packet["planning_batch"]

    def run_cmd(tokens: list[str]) -> dict[str, object]:
        assert tokens[0] == "loopx"
        proc = subprocess.run(
            [sys.executable, "-m", "loopx.cli", *tokens[1:]],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(proc.stdout)

    # 1. Checkpoint schema_command requires input_digest and pre-fills packet.input_digest.
    schema = run_cmd(shlex.split(str(batch_meta["schema_command"])))
    assert schema["required_fields"][0] == "input_digest"
    assert schema["fields"]["input_digest"]["optional"] is False
    example = dict(schema["example"])
    assert example["input_digest"] == packet["input_digest"]
    example["expected_provider_revision"] = packet["goal_acceptance"]["provider_revision"]

    proposal_path = project / "checkpoint-example.json"
    proposal_path.write_text(json.dumps(example), encoding="utf-8")

    # 2. Preview template validates cleanly without mutating state.
    preview_tokens = shlex.split(
        str(batch_meta["preview_command_template"]).replace("'<proposal.json>'", shlex.quote(str(proposal_path)))
    )
    preview = run_cmd(preview_tokens)
    assert preview["status"] == "planned"
    assert preview["dry_run"] is True
    assert preview["input_digest"] == packet["input_digest"]

    mid = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    assert mid["existing_todos"] == []
    assert mid["goal_acceptance"]["provider_revision"] == packet["goal_acceptance"]["provider_revision"]

    # 3. Apply template commits using that same example and passes readback.
    apply_tokens = shlex.split(
        str(batch_meta["apply_command_template"]).replace("'<proposal.json>'", shlex.quote(str(proposal_path)))
    )
    applied = run_cmd(apply_tokens)
    assert applied["status"] == "ready"
    assert applied["changed"] is True
    assert applied["input_digest"] == packet["input_digest"]
    after = build_task_planning_packet(
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        agent_id=agent_id,
        text=task_text,
        project=project,
    )
    verified = validate_task_plan_readback(
        {"input_digest": applied["input_digest"], "status": applied["status"], "todo_ids": applied["todo_ids"]},
        packet,
        after,
    )
    assert verified["state_readback_verified"] is True

    # Standalone plan-batch --schema retains optional input_digest without example placeholder.
    standalone = run_cmd(["loopx", "--format", "json", "todo", "plan-batch", "--schema"])
    assert standalone["required_fields"] == ["expected_provider_revision", "outcome", "todos"]
    assert "input_digest" not in standalone["example"]


def test_todo_plan_batch_static_schema_ignores_ambiguous_default_runtime_roots(
    tmp_path: Path,
) -> None:
    fake_home = tmp_path / "home"
    empty_cwd = tmp_path / "empty_cwd"
    empty_cwd.mkdir()
    primary_root = fake_home / ".loopx"
    legacy_root = fake_home / ".codex" / "loopx"
    initial_payload = json.dumps({"schema_version": "0.1", "goals": []}, indent=2) + "\n"
    for root in (primary_root, legacy_root):
        root.mkdir(parents=True)
        (root / "registry.global.json").write_text(initial_payload, encoding="utf-8")

    def snapshot_roots() -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        for root in (primary_root, legacy_root):
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    files[str(path.relative_to(fake_home))] = path.read_bytes()
        return files

    before = snapshot_roots()
    worktree_root = str(Path(__file__).resolve().parents[2])
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"LOOPX_REGISTRY", "LOOPX_RUNTIME_ROOT", "LOOPX_KUNLUNCODE_OUTER_CONTROLLER"}
    }
    env["HOME"] = str(fake_home)
    env["PYTHONPATH"] = worktree_root

    # Confirm default runtime-root resolution actually conflicts under this HOME.
    conflict = subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", "--format", "json", "todo", "list", "--goal-id", "g1"],
        cwd=str(empty_cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert conflict.returncode == 1
    assert "Both default LoopX" in conflict.stderr

    expected = planning_batch_schema()
    for module in ("loopx.cli", "loopx.entrypoint"):
        proc = subprocess.run(
            [sys.executable, "-m", module, "--format", "json", "todo", "plan-batch", "--schema"],
            cwd=str(empty_cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True
        assert payload["dry_run"] is True
        assert payload["schema_version"] == PLANNING_BATCH_SCHEMA_VERSION
        assert payload["fields"] == expected["fields"]
        assert snapshot_roots() == before

    # Invalid flag combinations still fail cleanly, and --schema --text still enforces routing.
    for bad_args in (
        ["--format", "json", "todo", "plan-batch", "--schema", "--execute"],
        ["--format", "json", "todo", "plan-batch", "--schema", "--turn-instance-id", "turn-1"],
        ["--format", "json", "todo", "plan-batch", "--schema", "--goal-id", "g1", "--agent-id", "a1", "--text", "plan"],
    ):
        proc = subprocess.run(
            [sys.executable, "-m", "loopx.entrypoint", *bad_args],
            cwd=str(empty_cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0
        assert snapshot_roots() == before


def test_product_task_plan_and_start_goal_ineligible_frontiers_omit_batch_commands(
    product_goal: dict[str, object],
) -> None:
    project = Path(str(product_goal["project"]))
    registry = Path(str(product_goal["registry"]))
    runtime_root = Path(str(product_goal["runtime_root"]))
    goal_id = str(product_goal["goal_id"])
    agent_id = str(product_goal["agent_id"])
    task_text = "Plan next increment without advertising ineligible initial batch."

    # Mirror registry under .loopx/registry.json so start-goal reads the same Goal & provider.
    dot_loopx = project / ".loopx"
    dot_loopx.mkdir(exist_ok=True)
    (dot_loopx / "registry.json").write_text(registry.read_text(encoding="utf-8"), encoding="utf-8")

    # 1. Empty frontier with waiting_on is ineligible on both todo plan and start-goal.
    reg_data = json.loads(registry.read_text(encoding="utf-8"))
    reg_data["goals"][0]["waiting_on"] = "owner review"
    registry.write_text(json.dumps(reg_data), encoding="utf-8")
    (dot_loopx / "registry.json").write_text(json.dumps(reg_data), encoding="utf-8")
    waiting_pkt = build_task_planning_packet(
        registry_path=registry, runtime_root_arg=str(runtime_root),
        goal_id=goal_id, agent_id=agent_id, text=task_text, project=project,
    )
    assert waiting_pkt["planning_batch"]["applicable"] is False
    write_waiting = next(s for s in waiting_pkt["ordered_steps"] if s["id"] == "write_ordered_todos")
    assert "batch_command_template" not in write_waiting
    assert "batch_schema_command" not in write_waiting
    assert "batch_command_template" not in write_waiting["purpose"]

    start_waiting = build_start_goal_guided_packet(
        project=project, goal_id=goal_id, agent_id=agent_id,
        cli_bin="loopx", host_surface="codex-app", goal_text=task_text,
        runtime_root_arg=str(runtime_root),
    )
    start_waiting_step = next(s for s in start_waiting["guided_transaction"]["ordered_steps"] if s["id"] == "write_ordered_todos")
    assert "batch_command_template" not in start_waiting_step
    assert "batch_schema_command" not in start_waiting_step

    reg_data["goals"][0].pop("waiting_on", None)
    registry.write_text(json.dumps(reg_data), encoding="utf-8")
    (dot_loopx / "registry.json").write_text(json.dumps(reg_data), encoding="utf-8")

    # 2. Eligible empty frontier advertises batch commands on both todo plan and start-goal;
    # declaring an Outcome without Todos makes both ineligible (standard and fine mode).
    before = build_task_planning_packet(
        registry_path=registry, runtime_root_arg=str(runtime_root),
        goal_id=goal_id, agent_id=agent_id, text=task_text, project=project,
    )
    assert before["planning_batch"]["applicable"] is True
    start_empty = build_start_goal_guided_packet(
        project=project, goal_id=goal_id, agent_id=agent_id,
        cli_bin="loopx", host_surface="codex-app", goal_text=task_text,
        runtime_root_arg=str(runtime_root),
    )
    start_empty_step = next(s for s in start_empty["guided_transaction"]["ordered_steps"] if s["id"] == "write_ordered_todos")
    assert "todo plan-batch" in start_empty_step["batch_command_template"]
    assert "todo plan-batch --schema" in start_empty_step["batch_schema_command"]
    backend = DirectPlanningBatchBackend(
        registry_path=registry, runtime_root_arg=str(runtime_root),
        goal_id=goal_id, agent_id=agent_id, project=project, text=task_text,
    )
    backend.refine_outcome(
        {
            "outcome_id": "existing-outcome",
            "owner_agent_id": agent_id,
            "objective": "Already declared acceptance requirement.",
            "requirements": [{"id": "req-1", "description": "Existing requirement without Todos."}],
            "source": {"ref": "checkpoint:existing", "summary": "Declared before Todo planning."},
        },
        str(before["goal_acceptance"]["provider_revision"]),
        "declare-only-outcome-op",
        execute=True,
    )
    for granularity in ("standard", "fine"):
        if granularity == "fine":
            reg_data["goals"][0]["execution_profile"] = {"turn_granularity": "fine"}
            registry.write_text(json.dumps(reg_data), encoding="utf-8")
            (dot_loopx / "registry.json").write_text(json.dumps(reg_data), encoding="utf-8")
        pkt = build_task_planning_packet(
            registry_path=registry, runtime_root_arg=str(runtime_root),
            goal_id=goal_id, agent_id=agent_id, text=task_text, project=project,
        )
        assert pkt["planning_batch"]["applicable"] is False
        write_step = next(s for s in pkt["ordered_steps"] if s["id"] == "write_ordered_todos")
        assert "batch_command_template" not in write_step
        assert "batch_schema_command" not in write_step
        assert "batch_command_template" not in write_step["purpose"]

        start_pkt = build_start_goal_guided_packet(
            project=project, goal_id=goal_id, agent_id=agent_id,
            cli_bin="loopx", host_surface="codex-app", goal_text=task_text,
            fine_grained=(granularity == "fine"), runtime_root_arg=str(runtime_root),
        )
        start_step = next(s for s in start_pkt["guided_transaction"]["ordered_steps"] if s["id"] == "write_ordered_todos")
        assert "batch_command_template" not in start_step
        assert "batch_schema_command" not in start_step


def test_product_task_plan_and_start_goal_user_todo_only_omit_batch_commands(
    product_goal: dict[str, object],
) -> None:
    project = Path(str(product_goal["project"]))
    registry = Path(str(product_goal["registry"]))
    runtime_root = Path(str(product_goal["runtime_root"]))
    goal_id = str(product_goal["goal_id"])
    agent_id = str(product_goal["agent_id"])
    task_text = "Plan work on a Goal that only has a non-blocking User Todo."
    prefix = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime_root)]

    dot_loopx = project / ".loopx"
    dot_loopx.mkdir(exist_ok=True)
    (dot_loopx / "registry.json").write_text(registry.read_text(encoding="utf-8"), encoding="utf-8")

    user_todo = _run_cli(
        *prefix, "todo", "add", "--goal-id", goal_id, "--project", str(project),
        "--role", "user", "--task-class", "user_action", "--bound-agent", agent_id,
        "--text", "Manual confirmation note",
    )
    assert user_todo["ok"] is True

    user_pkt = build_task_planning_packet(
        registry_path=registry, runtime_root_arg=str(runtime_root),
        goal_id=goal_id, agent_id=agent_id, text=task_text, project=project,
    )
    assert user_pkt["blocking_todo_ids"] == []
    assert user_pkt["planning_batch"]["applicable"] is False
    write_user = next(s for s in user_pkt["ordered_steps"] if s["id"] == "write_ordered_todos")
    assert "batch_command_template" not in write_user
    assert "batch_schema_command" not in write_user
    assert "batch_command_template" not in write_user["purpose"]

    start_pkt = build_start_goal_guided_packet(
        project=project, goal_id=goal_id, agent_id=agent_id,
        cli_bin="loopx", host_surface="codex-app", goal_text=task_text,
        runtime_root_arg=str(runtime_root),
    )
    start_step = next(s for s in start_pkt["guided_transaction"]["ordered_steps"] if s["id"] == "write_ordered_todos")
    assert "batch_command_template" not in start_step
    assert "batch_schema_command" not in start_step
