"""First capture and identity-conflict recovery through the real leased CLI."""
from copy import deepcopy
import json

import pytest

from loopx.capabilities.explore.result_log import explore_result_log_path
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.work_items.task_lease import acquire_task_lease
from loopx.todos import list_goal_todos
from tests.control_plane.test_explore_result_writeback import ATTACHMENT
from tests.control_plane.test_quota_settlement_cli import (
    _run_cli, _write_fixture, AGENT_ID, GOAL_ID, TODO_ID, TURN_ID,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("conflict", [None, "node_kind", "title", "summary"])
def test_first_capture_allocates_identity_and_recovers_without_rewriting_scope(
    tmp_path, provider, conflict,
):
    from canonical_authority_fixture import initialize_canonical_authority

    project, runtime, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
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
                      todo_id=TODO_ID, owner=AGENT_ID, idempotency_key="capture-identity")
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
                        *binding, "--scan-path", str(project), cwd=project)
    assert rc == 0, guard
    attachment = deepcopy(ATTACHMENT)
    del attachment["node_id"]
    packet = {"schema_version": "goal_vision_replan_contract_v0",
              "vision_patch": {"acceptance_summary": "Require a uniform tail bound."},
              "explore_result": attachment}
    if provider == "sqlite" and conflict is None:
        packet["path_delta"] = {
            "schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "A finite prefix might bound the tail.",
            "observed_reality": attachment.pop("observation"),
            "changed": [attachment.pop("interpretation")],
            "evidence_refs": attachment.pop("evidence_refs"),
        }
        attachment["schema_version"] = "explore_result_from_path_delta_v0"
    vision = tmp_path / "vision.json"
    args = ("refresh-state", *binding, "--classification", "validated_change",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--no-global-sync", "--suppress-external-sinks", "--agent-vision-json", str(vision))
    log = explore_result_log_path(runtime, GOAL_ID)
    if conflict:
        # The public node command defaults to an area; it is not a capture prerequisite.
        kind = () if conflict == "node_kind" else ("--kind", "question")
        rc, created = _run_cli(registry, runtime, "explore", "node", "--goal-id", GOAL_ID,
            "--node-id", "existing-area", "--title",
            "A different question" if conflict == "title" else ATTACHMENT["question"],
            "--summary", "A different scope" if conflict == "summary" else ATTACHMENT["applicability"],
            *kind, cwd=project)
        assert rc == 0, created
        original_log = log.read_bytes()
        index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        original_index = index.read_bytes() if index.exists() else b""
        original_todo = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, todo_id=TODO_ID)["todo"]
        packet["explore_result"]["node_id"] = "existing-area"
        vision.write_text(json.dumps(packet))
        rc, rejected = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 1 and not rejected["appended"], rejected
        assert f"({conflict})" in rejected["error"] and "omit node_id" in rejected["error"]
        assert log.read_bytes() == original_log and (index.read_bytes() if index.exists() else b"") == original_index
        assert list_goal_todos(registry_path=registry, goal_id=GOAL_ID, todo_id=TODO_ID)["todo"] == original_todo
        del packet["explore_result"]["node_id"]
    vision.write_text(json.dumps(packet))
    rc, captured = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and captured["explore_result_delivery"]["ok"], captured
    todo = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, todo_id=TODO_ID)["todo"]
    assert len(todo["explore_result_node_refs"]) == 1
    node = todo["explore_result_node_refs"][0]
    assert node != "existing-area"
    before_replay = log.read_bytes()
    rc, replay = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and replay["explore_result_delivery"]["ok"], replay
    assert log.read_bytes() == before_replay
    rc, context = _run_cli(registry, runtime, "explore", "turn-context",
                          "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, cwd=project)
    assert rc == 0, context
    finding = context["graph"]["writeback_results"][0]
    assert finding["node_id"] == node and finding["status"] == "refuted"
    assert ATTACHMENT["applicability"] in finding["summary"]
    assert ATTACHMENT["interpretation"] in finding["summary"]
    if conflict:
        assert log.read_bytes().startswith(original_log)
