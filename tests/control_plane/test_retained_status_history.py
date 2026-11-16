"""File-backed CLI status preserves retained and settled Turn history."""

import json
from pathlib import Path

import pytest

from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.control_plane.test_quota_settlement_cli import _projected_cli_args
from tests.test_goal_mode_mcp_settlement import AGENT_ID, GOAL_ID, _run_cli


@pytest.mark.parametrize("provider", ["file"])
def test_status_counts_an_original_turn_only_after_settlement(
    provider, tmp_path, monkeypatch
):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    document = json.loads(registry.read_text())
    goal = next(item for item in document["goals"] if item["id"] == GOAL_ID)
    goal["execution_profile"] = {
        **goal.get("execution_profile", {}),
        "replan_after_effective_turns": 1,
    }
    registry.write_text(json.dumps(document), encoding="utf-8")
    runtime = tmp_path / "runtime"
    project = state.parent
    turn = "status-history-original-turn"
    guard_args = (
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        todo,
        "--turn-instance-id",
        turn,
        "--codex-app",
    )
    rc, guard = _run_cli(registry, *guard_args)
    assert rc == 0, guard
    (project / "candidate.txt").write_text("verified candidate", encoding="utf-8")
    rc, retained = _run_cli(
        registry,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        todo,
        "--turn-instance-id",
        turn,
        "--classification",
        "bounded_candidate",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-boundary",
        "semantic_closeout",
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert rc == 0 and retained["vision_checkpoint"]["satisfied"] is False, retained
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    phases = []

    retained_paths = [Path(retained[key]) for key in ("json_path", "markdown_path")]
    frozen_result = {path: path.read_bytes() for path in retained_paths}

    def observe(phase, expected):
        before = index.read_bytes()
        rc, status = _run_cli(
            registry, "status", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID
        )
        assert rc == 0, status
        item = next(
            row
            for row in status["attention_queue"]["items"]
            if row["goal_id"] == GOAL_ID
        )
        obligation = item.get("autonomous_replan_obligations_by_agent", {}).get(
            AGENT_ID
        )
        periodic = [
            trigger
            for trigger in (obligation or {}).get("triggers", [])
            if trigger["kind"] == "periodic_review_due"
        ]
        actual = [(trigger["run_count"], trigger["threshold"]) for trigger in periodic]
        assert actual == ([(1, 1)] if expected else []), item
        phases.append((phase, periodic[0]["run_count"] if periodic else 0))
        assert index.read_bytes() == before
        assert {path: path.read_bytes() for path in retained_paths} == frozen_result

    observe("R", 0)
    rc, context = _run_cli(
        registry,
        "checkpoint-context",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        todo,
        "--turn-instance-id",
        turn,
        "--purpose",
        "supplement_checkpoint",
    )
    assert rc == 0, context
    rc, cold = _run_cli(registry, *guard_args)
    assert rc == 0, cold
    packet = project / "direction.json"
    packet.write_text(
        json.dumps(
            {
                "schema_version": "goal_vision_replan_contract_v0",
                "state": "vision_patch_proposed",
                "vision_patch": {
                    "vision_summary": "Verify the original route.",
                    "acceptance_summary": "Verified retained candidate; the original Todo remains open.",
                    "advancement_policy": "repeat_until_closed",
                },
                "todo_delta": ["retain:" + todo],
                "path_delta": {
                    "outcome": "continue",
                    "prior_assumption": "A scoped candidate needs current review.",
                    "observed_reality": "The original candidate is retained and current work remains.",
                    "retained": ["Original candidate and remaining work"],
                    "evidence_refs": ["artifact:candidate.txt"],
                },
            }
        ),
        encoding="utf-8",
    )
    commands = cold["interaction_contract"]["cli_channel"]["next_cli_actions"]
    command = next(c for c in commands if " refresh-state " in c)
    command = command.replace("<read-context-id>", context["read_context_id"])
    command = command.replace(
        "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(packet)
    )
    args = list(_projected_cli_args(command, turn_instance_id=turn))
    args.extend(["--suppress-external-sinks", "--no-global-sync"])
    if "--agent-vision-json" not in args:
        args.extend(["--agent-vision-json", str(packet)])
    rc, qualified = _run_cli(registry, *args)
    assert rc == 0 and qualified["vision_checkpoint"]["satisfied"], qualified
    observe("Q", 0)
    owed = qualified["settlement_owed"]["command"]
    rc, settled = _run_cli(registry, *_projected_cli_args(owed, turn_instance_id=turn))
    assert rc == 0 and settled["appended"], settled
    assert (settled["before"]["spent_slots"], settled["after"]["spent_slots"]) == (0, 1)
    observe("settled", 1)
    assert phases == [("R", 0), ("Q", 0), ("settled", 1)], phases
    before = index.read_bytes()
    rc, replay = _run_cli(registry, *_projected_cli_args(owed, turn_instance_id=turn))
    assert rc == 0 and replay["idempotent_replay"] is True, replay
    assert index.read_bytes() == before
    observe("replay", 1)
    assert phases == [("R", 0), ("Q", 0), ("settled", 1), ("replay", 1)]
    assert (project / "candidate.txt").read_text() == "verified candidate"
