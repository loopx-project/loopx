"""Actual CLI R/Q/settled lifecycle against the shipped replan history reader."""

import json
from copy import deepcopy

import pytest

from loopx.control_plane.work_items.replan_history_codec import project_replan_history
from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.control_plane.test_quota_settlement_cli import _projected_cli_args
from tests.test_goal_mode_mcp_settlement import AGENT_ID, GOAL_ID, _run_cli


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_normal_retained_turn_counts_only_after_settlement(
    provider, tmp_path, monkeypatch
):
    registry, state, todo = _fixture(provider, tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    project = state.parent
    turn = "effective-history-original-turn"
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

    def observe(phase):
        before = index.read_bytes()
        rows = [json.loads(line) for line in before.decode().splitlines()]

        def project(threshold):
            return project_replan_history(
                reversed(rows),
                operation="periodic",
                agent_id=AGENT_ID,
                effective_turn_cadence={
                    "registry_path": registry,
                    "threshold": threshold,
                    "stride_strategy": "off",
                    "settlement_source": {
                        "runtime_root": str(runtime),
                        "goal_id": GOAL_ID,
                    },
                },
            )

        one, two = project(1), project(2)
        assert two is None, two
        phases.append((phase, one["run_count"] if one else 0))
        assert index.read_bytes() == before

    observe("R")
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
    observe("Q")
    owed = qualified["settlement_owed"]["command"]
    rc, settled = _run_cli(registry, *_projected_cli_args(owed, turn_instance_id=turn))
    assert rc == 0, settled
    observe("settled")
    assert phases == [("R", 0), ("Q", 0), ("settled", 1)], phases
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    cadence = {
        "registry_path": registry,
        "threshold": 2,
        "stride_strategy": "off",
        "settlement_source": {"runtime_root": str(runtime), "goal_id": GOAL_ID},
    }
    # Repeated caller rows and a fabricated Turn cannot create another receipt.
    forged = deepcopy(rows[-2])
    forged["turn_instance_id"] = "unadmitted-turn"
    forged["settlement_identity"]["turn_instance_id"] = "unadmitted-turn"
    forged["settlement_identity"]["effect_id"] = (
        f"{GOAL_ID}:{AGENT_ID}:{todo}:unadmitted-turn"
    )
    before = index.read_bytes()
    assert (
        project_replan_history(
            [forged, *reversed(rows), *reversed(rows)],
            operation="periodic",
            agent_id=AGENT_ID,
            effective_turn_cadence=cadence,
        )
        is None
    )
    cadence["threshold"] = 1
    assert (
        project_replan_history(
            [forged],
            operation="periodic",
            agent_id=AGENT_ID,
            effective_turn_cadence=cadence,
        )
        is None
    )
    assert (
        project_replan_history(
            reversed(rows),
            operation="periodic",
            agent_id="peer",
            effective_turn_cadence=cadence,
        )
        is None
    )
    assert index.read_bytes() == before
    rc, replay = _run_cli(registry, *_projected_cli_args(owed, turn_instance_id=turn))
    assert rc == 0 and replay["idempotent_replay"] is True, replay
    assert index.read_bytes() == before
    observe("replay")
    assert phases[-1] == ("replay", 1)
    assert (project / "candidate.txt").read_text() == "verified candidate"
    print(json.dumps({"provider": provider, "phases": phases}))
