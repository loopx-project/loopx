"""Canonical question reuse through the existing explicit capture transport."""
from copy import deepcopy
import json

import pytest

from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_node_event, explore_result_log_path,
)
from loopx.capabilities.explore.result_writeback import prepare_result_attachment, deliver_result_attachment
from loopx.cli_commands.project_lifecycle_inputs import split_explore_result_input
from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.todos import update_goal_todo
from tests.control_plane.test_explore_result_writeback import ATTACHMENT


def packet():
    return {
        "schema_version": "goal_vision_replan_contract_v0",
        "vision_patch": {"acceptance_summary": "Require a uniform tail bound."},
        "path_delta": {
            "schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "A finite prefix might bound the tail.",
            "observed_reality": ATTACHMENT["observation"],
            "changed": [ATTACHMENT["interpretation"]],
            "evidence_refs": ATTACHMENT["evidence_refs"],
        },
        "explore_result": {
            "schema_version": "explore_result_from_path_delta_v0",
            "node_id": ATTACHMENT["node_id"], "input_revision": "fixture-v2",
            "status": "tentative",
        },
    }


def question(runtime, goal):
    log = explore_result_log_path(runtime, goal)
    append_explore_result_event(log, build_explore_node_event(
        goal_id=goal, node_id=ATTACHMENT["node_id"], node_kind="question",
        title=ATTACHMENT["question"], summary=ATTACHMENT["applicability"], status="open",
    ))
    return log


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_scope_uses_source_registry_and_canonical_todo_not_stale_shared_projection(tmp_path, provider):
    from tests.control_plane.test_native_todo_planning_update import fixture, records
    from loopx.capabilities.explore.turn_context import explore_turn_context

    source, _state = fixture(tmp_path, True, provider)
    runtime = tmp_path / "runtime"
    config = json.loads(source.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    source.write_text(json.dumps(config))
    update_goal_todo(registry_path=source, goal_id="goal-a", todo_id="todo_target",
                    agent_id="agent-a", explore_result_node_refs=[ATTACHMENT["node_id"]])
    log = question(runtime, "goal-a")
    shared = tmp_path / "shared.json"
    shared.write_text(json.dumps({"schema_version": 1, "registry_role": "global-local",
        "common_runtime_root": str(tmp_path / "shared-runtime"),
        "goals": [{**config["goals"][0], "source_registry": str(source),
                   "explore_graph": {"enabled": False}}]}))
    context = dict(registry_path=shared, runtime_root_override=None, goal_id="goal-a",
                   agent_id="agent-a", todo_id="todo_target", turn_instance_id="turn-two")
    original_todos, original_log = records(source), log.read_bytes()
    for patch in ({"node_id": "unknown-question"}, {"question": "Partial override"}):
        bad = packet()
        bad["explore_result"].update(patch)
        with pytest.raises((ValueError, EffectRuntimeRejected)):
            split_explore_result_input(bad, None, scope_context=context)
    with pytest.raises(ValueError, match="claimed Todo"):
        split_explore_result_input(packet(), None, scope_context={**context, "todo_id": "todo_other"})
    assert records(source) == original_todos and log.read_bytes() == original_log
    source_packet = packet()
    before = deepcopy(source_packet)
    vision, result = split_explore_result_input(source_packet, None, scope_context=context)
    assert source_packet == before and "explore_result" not in vision
    assert result["question"] == ATTACHMENT["question"]
    assert result["applicability"] == ATTACHMENT["applicability"]
    assert result["input_revision"] == "fixture-v2" and result["status"] == "tentative"
    args = dict(registry_path=source, runtime_root=runtime, goal_id="goal-a",
                agent_id="agent-a", todo_id="todo_target", turn_instance_id="turn-two")
    prepare_result_attachment(result, **args)
    assert records(source) == original_todos and log.read_bytes() == original_log
    delivered = deliver_result_attachment(payload={"explore_result": result,
        "generated_at": "2026-01-02T00:00:00Z", "appended": True,
        "settlement_identity": {"effect_id": "goal-a:agent-a:turn-two"}}, **args)
    assert delivered["ok"], delivered
    read = explore_turn_context(registry_path=source, runtime_root=runtime,
                                goal_id="goal-a", agent_id="agent-a")
    finding = read["graph"]["writeback_results"][0]
    assert "Input revision: fixture-v2" in finding["summary"]
    assert ATTACHMENT["applicability"] in finding["summary"]
    assert finding["status"] == "tentative"
    assert records(source)["todo_other"] == original_todos["todo_other"]
    assert not explore_result_log_path(tmp_path / "shared-runtime", "goal-a").exists()


@pytest.mark.parametrize("case", ["unknown", "unlinked", "partial"])
def test_real_cli_rejects_invalid_linked_capture_before_primary_commit_and_recovers(tmp_path, case):
    from tests.control_plane.test_quota_settlement_cli import (
        _write_fixture, _run_cli, GOAL_ID, AGENT_ID, TODO_ID, TURN_ID,
    )
    project, runtime, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    registry.write_text(json.dumps(config))
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
               "--turn-instance-id", TURN_ID)
    assert _run_cli(registry, runtime, "todo", "claim", "--claimed-by", AGENT_ID,
                    *binding[:-2], cwd=project)[0] == 0
    assert _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
                    *binding, "--scan-path", str(project), cwd=project)[0] == 0
    log = question(runtime, GOAL_ID)
    if case != "unlinked":
        update_goal_todo(registry_path=registry, goal_id=GOAL_ID, todo_id=TODO_ID,
                        agent_id=AGENT_ID, explore_result_node_refs=[ATTACHMENT["node_id"]])
    bad = packet()
    bad["explore_result"].update({"node_id": "unknown-question"} if case == "unknown"
                                 else {"question": "Partial override"} if case == "partial" else {})
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps(bad))
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    before_index = index.read_bytes() if index.exists() else b""
    before_log = log.read_bytes()
    args = ("refresh-state", *binding, "--classification", "validated_change",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--no-global-sync", "--suppress-external-sinks", "--agent-vision-json", str(vision))
    rc, rejected = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 1 and not rejected["appended"], rejected
    assert (index.read_bytes() if index.exists() else b"") == before_index
    assert log.read_bytes() == before_log
    update_goal_todo(registry_path=registry, goal_id=GOAL_ID, todo_id=TODO_ID,
                    agent_id=AGENT_ID, explore_result_node_refs=[ATTACHMENT["node_id"]])
    vision.write_text(json.dumps(packet()))
    rc, recovered = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0 and recovered["explore_result_delivery"]["ok"], recovered


def test_ordinary_vision_without_capture_does_not_read_scope(tmp_path):
    vision = {"vision_patch": {"acceptance_summary": "Continue validation."}}
    assert split_explore_result_input(vision, None, scope_context={
        "registry_path": tmp_path / "missing.json"}) == (vision, None)
