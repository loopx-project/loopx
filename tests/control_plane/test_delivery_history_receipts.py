"""Receipt-qualified handoff history over actual File/SQLite CLI results."""

import json

import pytest

from loopx import status
from loopx.control_plane.progress_scope import AGENT_LANE_PROGRESS_SCOPE
from loopx.state_refresh import GOAL_PROGRESS_SCOPE
from loopx.control_plane.goals.checkpoint_context_io import first_delivery_progress
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from tests.control_plane.test_outcome_replan_composition_cli import _review_packet
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    SELECTED_REPLAN_TODO_ID,
    _projected_cli_args,
    _spend_run_count,
    _write_fixture,
    _configure_selected_todo_replan_fixture,
    _configure_completion_validation_todo,
    _initialize_fixture_authority,
    _run_cli,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("scope", [AGENT_LANE_PROGRESS_SCOPE, GOAL_PROGRESS_SCOPE])
def test_one_work_turn_does_not_count_retention_and_qualification_twice(
    provider, scope, tmp_path, monkeypatch
):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _configure_completion_validation_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "history-review-original-turn"

    def call(*args):
        return _run_cli(registry, runtime, *args, cwd=project)

    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        SELECTED_REPLAN_TODO_ID,
        "--turn-instance-id",
        turn,
    )
    rc, guard = call(*guard_args)
    if rc:
        [selection] = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
        rc, guard = call(*_projected_cli_args(selection, turn_instance_id=turn))
    assert rc == 0, guard
    first = list(
        _projected_cli_args(
            guard["interaction_contract"]["cli_channel"]["next_cli_actions"][0],
            turn_instance_id=turn,
        )
    )
    first[first.index("--progress-scope") + 1] = scope
    rc, retained = call(*first)
    assert rc == 0 and retained["progress_scope"] == scope, retained
    rc, context = call(
        "checkpoint-context",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        SELECTED_REPLAN_TODO_ID,
        "--turn-instance-id",
        turn,
        "--purpose",
        "supplement_checkpoint",
    )
    assert rc == 0, context
    rc, cold = call(*guard_args)
    assert rc == 0, cold
    [_, supplement, _] = cold["interaction_contract"]["cli_channel"]["next_cli_actions"]
    packet = project / "history-direction.json"
    packet.write_text(
        json.dumps(_review_packet(guard["replan_action_packet"]["stride_control"])),
        encoding="utf-8",
    )
    supplement = supplement.replace(
        "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(packet)
    )
    supplement = supplement.replace("<read-context-id>", context["read_context_id"])
    supplement_args = list(_projected_cli_args(supplement, turn_instance_id=turn))
    if "--progress-scope" in supplement_args:
        supplement_args[supplement_args.index("--progress-scope") + 1] = scope
    else:
        supplement_args += ["--progress-scope", scope]
    registry = runtime.parent / "project/.loopx/registry.json"
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    snapshots = {}

    def observe(phase):
        contexts = index.parent.parent / "checkpoint-contexts"
        before = {p: p.read_bytes() for p in [index, *contexts.glob("*.json")]}
        rows = [
            json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()
        ]
        readback = read_heartbeat_settlement(
            runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            todo_id=SELECTED_REPLAN_TODO_ID,
            turn_instance_id=turn,
            registry_path=registry,
        )
        progress = first_delivery_progress(runtime, readback, registry_path=registry)
        # Exercise the shipped projection; ready is an explicit controlled input,
        # not a claim of a packaged/installed UI or a real project's readiness.
        projected = status.project_asset_handoff_state(
            ready=True,
            project_asset={
                "execution_profile": {
                    "outcome_floor": {"outcome_markers": ["accepted"]}
                }
            },
            latest_runs=rows,
            settlement_source={
                "runtime_root": str(runtime),
                "goal_id": GOAL_ID,
                "registry_path": registry,
            },
        )
        snapshots[phase] = {
            "settlement_state": readback.progress["state"],
            "first_delivery_stage": progress["stage"],
            "spend_count": _spend_run_count(runtime),
            "small_scale_streak": projected.get("post_handoff_small_scale_streak", 0),
            "delivery_claims": [
                r.get("delivery_outcome")
                for r in projected.get("post_handoff_recent_runs", [])
            ],
            "turn_kinds": [
                r.get("delivery_turn_kind")
                for r in projected.get("post_handoff_recent_runs", [])
            ],
            "result_rows": sum(r.get("vision_checkpoint") is not None for r in rows),
            "distinct_result_effects": len(
                {
                    r["settlement_identity"]["effect_id"]
                    for r in rows
                    if r.get("vision_checkpoint")
                }
            ),
        }
        assert all(p.read_bytes() == content for p, content in before.items())
        return readback

    assert observe("retained").spend_qualification.failure is not None
    rc, qualified = call(*supplement_args)
    assert rc == 0, qualified
    assert observe("qualified").spend_qualification.failure is None
    if scope == GOAL_PROGRESS_SCOPE:
        from copy import deepcopy
        from loopx.control_plane.work_items import delivery_history

        rows = [
            json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()
        ]
        source = {
            "runtime_root": str(runtime),
            "goal_id": GOAL_ID,
            "registry_path": registry,
        }
        actual = delivery_history.effect_runtime_result
        calls = []

        def record(method, request):
            calls.append(method)
            return actual(method, request)

        monkeypatch.setattr(delivery_history, "effect_runtime_result", record)
        original = deepcopy(rows)

        def history(items, selected_source=source):
            calls.clear()
            result = status.project_asset_handoff_state(
                ready=True,
                project_asset={},
                latest_runs=items,
                settlement_source=selected_source,
            )
            assert calls == ["work_item.delivery_history.project"]
            return result["post_handoff_small_scale_streak"]

        assert history(rows, None) == 0
        assert history(rows, {**source, "goal_id": "other-goal"}) == 0
        assert (
            history(
                rows, {**source, "runtime_root": str(tmp_path / "unavailable-runtime")}
            )
            == 0
        )
        qualified_row = rows[-1]
        assert history([qualified_row, qualified_row, *rows]) == 1
        stripped = {
            key: value
            for key, value in rows[0].items()
            if key not in ("settlement_identity", "vision_checkpoint")
        }
        assert history([stripped]) == 0
        assert (
            history([{**rows[0], "generated_at": qualified_row["generated_at"]}]) == 0
        )
        # A genuine pre-receipt source row keeps its original display unit.
        legacy = {
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "turn_instance_id": "legacy-turn",
            "generated_at": "2020-01-01T00:00:00Z",
            "classification": "custom_delivery",
            "delivery_outcome": "outcome_progress",
            "delivery_batch_scale": "single_surface",
        }
        original_index = index.read_bytes()
        try:
            index.write_bytes(original_index + (json.dumps(legacy) + "\n").encode())
            assert history([legacy]) == 1
        finally:
            index.write_bytes(original_index)
        for invalid_ref in (
            None,
            {"goal_id": GOAL_ID, "goal_instance_id": "foreign", "extra": True},
            "x" * 10000,
        ):
            foreign = [{**row, "goal_ref": invalid_ref} for row in rows]
            assert history(foreign) == 0
        altered = deepcopy(rows)
        for row in altered:
            row["delivery_batch_scale"] = "implementation"
            row["delivery_outcome"] = "primary_goal_outcome"
        assert history(altered) == 1  # The verified source remains single_surface.
        damaged = index.read_bytes()
        try:
            index.write_bytes(damaged + b"invalid tail\n")
            assert history(rows) == 0
        finally:
            index.write_bytes(damaged)
        assert rows == original
        assert history(rows) == 1
        # Exercise status collection and the shipped attention/handoff adapters,
        # controlling readiness only; all history and receipts remain real.
        monkeypatch.setattr(
            status,
            "project_asset_handoff_check_projection",
            lambda _item: {
                "checks": {"fixture_ready": True},
                "codex_ready": True,
                "state_trace_ready": True,
            },
        )
        collected = status.collect_status(
            registry_path=registry,
            runtime_root_override=str(runtime),
            limit=5,
            goal_id=GOAL_ID,
            agent_lane_id=AGENT_ID,
            include_presentation_projections=False,
        )
        [item] = [
            item
            for item in collected["attention_queue"]["items"]
            if item["goal_id"] == GOAL_ID
        ]
        assert item["handoff_readiness"]["post_handoff_small_scale_streak"] == 1
    rc, paid = call(
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        SELECTED_REPLAN_TODO_ID,
        "--turn-instance-id",
        turn,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
    )
    assert rc == 0 and paid["settlement_progress"]["state"] == "settled", paid
    observe("settled")
    # Independent v3.1 requirement: R is retained, Q accepts the same work Turn.
    expected = (
        {"retained": 0, "qualified": 0, "settled": 0}
        if scope == AGENT_LANE_PROGRESS_SCOPE
        else {"retained": 0, "qualified": 1, "settled": 1}
    )
    assert {k: v["small_scale_streak"] for k, v in snapshots.items()} == expected, (
        snapshots
    )
