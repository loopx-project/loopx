"""Product CLI, task-planning checkpoint, and HTTP tests for general planning_batch."""

from __future__ import annotations

import contextlib
import http.client
import io
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from canonical_authority_fixture import initialize_canonical_source_fixture
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.cli import main
from loopx.control_plane.goals.planning_batch import (
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


def test_http_goal_acceptance_plan_batch_preview_apply_and_replay(
    product_goal: dict[str, object],
):
    registry = Path(str(product_goal["registry"]))
    runtime_root = Path(str(product_goal["runtime_root"]))
    goal_id = str(product_goal["goal_id"])
    agent_id = str(product_goal["agent_id"])

    store = ChatActionStore(runtime_root / "chat/actions")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root = registry, runtime_root
    server.verbose = False
    server.action_store = store
    server.action_service = ChatActionService(
        store=store, registry_path=registry, runtime_root_override=str(runtime_root)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method: str, path: str, body: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        conn = http.client.HTTPConnection(*server.server_address, timeout=30)
        conn.request(
            method,
            path,
            body=json.dumps(body) if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    try:
        status_code, inspected = request("GET", f"/api/chat/goal-acceptance?goal_id={goal_id}")
        assert status_code == 200, inspected
        initial_rev = str(inspected["provider_revision"])

        doc = {
            "outcome_id": "http-plan-batch",
            "outcome": {
                "objective": "Plan initial work through the local HTTP product API.",
                "requirements": [
                    {"id": "http-preview", "description": "Support dry-run preview without writes."},
                    {"id": "http-apply", "description": "Commit and link Todos with idempotent replay."},
                ],
                "source": {"ref": "api:goal-acceptance", "summary": "Local product HTTP request."},
            },
            "todos": [
                {"text": "[P0] Wire HTTP plan-batch endpoint", "action_kind": "implement"},
                {"text": "[P1] Verify HTTP readback", "action_kind": "validate", "target_key": "http-plan"},
            ],
        }

        status_code, preview = request(
            "POST",
            "/api/chat/goal-acceptance",
            {
                "goal_id": goal_id,
                "action": "preview-plan-batch",
                "agent_id": agent_id,
                "document": doc,
                "expected_provider_revision": initial_rev,
                "operation_id": "http-batch-op",
            },
        )
        assert status_code == 200, preview
        assert preview["status"] == "planned"
        assert preview["dry_run"] is True
        assert preview["changed"] is False
        assert preview["planned_todo_count"] == 2
        _, after_preview = request("GET", f"/api/chat/goal-acceptance?goal_id={goal_id}")
        assert after_preview["provider_revision"] == initial_rev

        status_code, applied = request(
            "POST",
            "/api/chat/goal-acceptance",
            {
                "goal_id": goal_id,
                "action": "plan-batch",
                "agent_id": agent_id,
                "document": doc,
                "expected_provider_revision": initial_rev,
                "operation_id": "http-batch-op",
            },
        )
        assert status_code == 200, applied
        assert applied["status"] == "ready"
        assert applied["dry_run"] is False
        assert applied["state_readback_verified"] is True
        assert len(applied["todo_ids"]) == 2
        outcomes = applied["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
        assert len(outcomes) == 1
        assert outcomes[0]["id"] == "http-plan-batch"
        assert outcomes[0]["todo_ids"] == applied["todo_ids"]

        # Replay via HTTP returns the same Todo IDs and provider revision without re-writing.
        status_code, replayed = request(
            "POST",
            "/api/chat/goal-acceptance",
            {
                "goal_id": goal_id,
                "action": "plan-batch",
                "agent_id": agent_id,
                "document": doc,
                "expected_provider_revision": initial_rev,
                "operation_id": "http-batch-op",
            },
        )
        assert status_code == 200, replayed
        assert replayed["todo_ids"] == applied["todo_ids"]
        assert replayed["provider_revision"] == applied["provider_revision"]
        assert replayed["changed"] is False

        # Reject unregistered agent with 409 error envelope.
        bad_code, bad_resp = request(
            "POST",
            "/api/chat/goal-acceptance",
            {
                "goal_id": goal_id,
                "action": "plan-batch",
                "agent_id": "intruder",
                "document": doc,
                "expected_provider_revision": str(applied["provider_revision"]),
                "operation_id": "http-batch-intruder",
            },
        )
        assert bad_code == 409, bad_resp
        assert bad_resp["error_code"] == "goal_acceptance_operation_failed"

        # Reject explicit expected_provider_revision: null inside document.
        null_rev_code, _ = request(
            "POST",
            "/api/chat/goal-acceptance",
            {
                "goal_id": goal_id,
                "action": "preview-plan-batch",
                "agent_id": agent_id,
                "document": {**doc, "expected_provider_revision": None},
                "expected_provider_revision": str(applied["provider_revision"]),
            },
        )
        assert null_rev_code == 409

        # Reject agent_id field on non-batch actions such as configure.
        extra_field_code, _ = request(
            "POST",
            "/api/chat/goal-acceptance",
            {
                "goal_id": goal_id,
                "action": "clear-checks",
                "agent_id": agent_id,
                "expected_provider_revision": str(applied["provider_revision"]),
                "operation_id": "http-clear-extra-field",
            },
        )
        assert extra_field_code == 409
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
