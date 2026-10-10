from __future__ import annotations

import json
from pathlib import Path

from canonical_authority_fixture import initialize_canonical_authority

from loopx.capabilities.issue_fix.feasibility import build_issue_fix_feasibility_packet
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.domain_packs.issue_fix import (
    default_issue_fix_feasibility_ledger_path,
    upsert_issue_fix_feasibility_ledger_jsonl,
)
from loopx.todos import list_goal_todos
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    TODO_ID,
    TURN_ID,
    _run_cli,
    _write_fixture,
)


def test_sqlite_cli_private_evidence_refresh_and_original_turn_replay_spend_once(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    registry.write_text(json.dumps(config))
    state = project / config["goals"][0]["state_file"]
    evidence = project / "private-evidence.md"
    evidence.write_text("Private fixture proof.\n")
    state.write_text(
        state.read_text().replace(
            "task_class=advancement_task",
            "target_capabilities=issue_fix_explore_projection "
            f"explore_result_node_refs=cap_projection evidence={evidence} task_class=advancement_task",
        )
    )
    upsert_issue_fix_feasibility_ledger_jsonl(
        default_issue_fix_feasibility_ledger_path(project=project, goal_id=GOAL_ID),
        build_issue_fix_feasibility_packet(
            url="https://github.com/public-fixture/widgets/issues/7",
            reproduction_status="confirmed",
            scope_class="bounded",
            reproduction_label="focused reproduction",
            validation_label="focused validation",
        ),
    )

    def run(*command: str) -> dict:
        code, result = _run_cli(registry, runtime, *command, cwd=project)
        assert code == 0, json.dumps(result, indent=2)
        return result

    run(
        "todo",
        "claim",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--agent-id",
        AGENT_ID,
        "--claimed-by",
        AGENT_ID,
    )
    todos = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=todos, handoff_mode="hard_lease"
        ),
        state_path=state,
        provider="sqlite",
    )
    binding = (
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_ID,
    )
    run("quota", "should-run", "--codex-app", *binding, "--scan-path", str(project))
    run(
        "task-lease",
        "acquire",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--owner",
        AGENT_ID,
        "--idempotency-key",
        "private-evidence-fixture",
    )
    refresh = (
        "refresh-state",
        *binding,
        "--classification",
        "validated_change",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--no-global-sync",
    )
    first = run(*refresh)
    assert first["ok"] and first["appended"]
    assert first["explore_graph_sync"]["status"] == "not_configured"
    replay = run(*refresh)
    assert replay["idempotent_replay"]
    assert replay["settlement_identity"] == first["settlement_identity"]
    spend = (
        "quota",
        "spend-slot",
        *binding,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
        "--scan-path",
        str(project),
    )
    assert run(*spend)["appended"] is True
    assert run(*spend)["idempotent_replay"] is True
    todo = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, todo_id=TODO_ID)[
        "todo"
    ]
    assert todo["evidence"] == str(evidence)
    assert evidence.read_text() == "Private fixture proof.\n"
