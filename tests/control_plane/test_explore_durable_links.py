"""Durable Todo evidence links must outlive a bounded context page."""
from copy import deepcopy
import json

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx.capabilities.explore.result_log import explore_result_log_path
from loopx.capabilities.explore.result_writeback import prepare_result_attachment, deliver_result_attachment
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.work_items.task_lease import acquire_task_lease
from loopx.todos import list_goal_todos
from tests.control_plane.test_explore_result_writeback import ATTACHMENT
from tests.control_plane.test_quota_settlement_cli import (
    _run_cli, _write_fixture, AGENT_ID, GOAL_ID, TODO_ID, TURN_ID,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_ninth_capture_keeps_history_and_survives_real_cli_replay(tmp_path, provider):
    project, runtime, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0].update(explore_graph={"enabled": True}, spawn_policy={
        "allowed": False, "explore_harness": {"enabled": True}})
    registry.write_text(json.dumps(config))
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
               "--turn-instance-id", TURN_ID)
    assert _run_cli(registry, runtime, "todo", "claim", "--claimed-by", AGENT_ID,
                    *binding[:-2], cwd=project)[0] == 0
    rows = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=rows, handoff_mode="hard_lease"),
        state_path=project / config["goals"][0]["state_file"], provider=provider)
    acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
                      todo_id=TODO_ID, owner=AGENT_ID, idempotency_key="durable-evidence")
    args = dict(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
                agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID)
    # Existing evidence is seeded through the real graph/link owner, not by
    # changing active state or bypassing the canonical lease.
    for i in range(8):
        attachment = {**ATTACHMENT, "node_id": f"question-{i}", "question": f"Prior question {i}?",
                      "status": "confirmed"}
        prepare_result_attachment(attachment, **args)
        receipt = deliver_result_attachment(payload={"explore_result": attachment,
            "generated_at": "2026-01-01T00:00:00Z", "appended": True,
            "settlement_identity": {"effect_id": f"seed-{i}"}}, **args)
        assert receipt["ok"], receipt
    log = explore_result_log_path(runtime, GOAL_ID)
    history = log.read_bytes()
    with pytest.raises(ValueError, match="claimed Todo"):
        prepare_result_attachment(ATTACHMENT, **{**args, "agent_id": "foreign"})
    assert log.read_bytes() == history
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
                        *binding, "--scan-path", str(project), cwd=project)
    assert rc == 0, guard
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0",
        "vision_patch": {"acceptance_summary": "Require a uniform tail bound."},
        "explore_result": deepcopy(ATTACHMENT)}))
    command = ("refresh-state", *binding, "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--no-global-sync", "--suppress-external-sinks", "--agent-vision-json", str(vision))
    rc, captured = _run_cli(registry, runtime, *command, cwd=project)
    assert rc == 0 and captured["explore_result_delivery"]["ok"], captured
    assert log.read_bytes().startswith(history)
    after = log.read_bytes()
    rc, replay = _run_cli(registry, runtime, *command, cwd=project)
    assert rc == 0 and replay["idempotent_replay"], replay
    assert log.read_bytes() == after
    rc, exact = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID,
                         "--todo-id", TODO_ID, cwd=project)
    expected = [f"question-{i}" for i in range(8)] + [ATTACHMENT["node_id"]]
    assert rc == 0 and exact["todo"]["explore_result_node_refs"] == expected
    rc, context = _run_cli(registry, runtime, "explore", "turn-context",
                          "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, cwd=project)
    assert rc == 0, context
    graph = context["graph"]
    assert graph["result_page"]["total"] == 9
    assert len(graph["writeback_results"]) == 3
    ninth = graph["writeback_results"][0]
    assert ninth["node_id"] == ATTACHMENT["node_id"] and ninth["status"] == "refuted"
    assert ATTACHMENT["applicability"] in ninth["summary"]
    audit = context["harness"]["selected_branches"][0]["typed_evidence_audit"]
    assert len(audit["requested_node_refs"]) == 8
    assert audit["omitted_requested_node_refs"] == 1
    assert "linked_finding_refuted" in audit["hazards"]
    seen = {f["node_id"] for f in graph["writeback_results"]}
    while graph["result_page"]["next_command"]:
        next_args = graph["result_page"]["next_command"]
        rc, page = _run_cli(registry, runtime, *next_args[next_args.index("explore"):], cwd=project)
        assert rc == 0, page
        graph = page["graph"]
        seen.update(f["node_id"] for f in graph["writeback_results"])
    assert seen == set(expected)
    # Existing frontend API read model must retain the ninth association too.
    from loopx.presentation.explore_results_api import _result_rows
    rendered_rows = _result_rows(runtime, GOAL_ID, registry)
    assert len(rendered_rows) == 9
    assert all([t["todo_id"] for t in row["linked_todos"]] == [TODO_ID] for row in rendered_rows)
