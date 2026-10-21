#!/usr/bin/env python3
"""Smoke-test quota agent identity on registered multi-agent goals."""

from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "examples" / "control_plane" / "quota_plan_fixtures.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.quota.slot_accounting import (  # noqa: E402
    _latest_unspent_turn_settlement_run,
)
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)
from loopx.control_plane.work_items.interaction_contract import (  # noqa: E402
    interaction_next_cli_actions,
)
from loopx.quota import (  # noqa: E402
    build_quota_monitor_poll_event,
    build_quota_slot_spend_event,
)


def load_quota_plan_fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("quota_plan_smoke_fixture", FIXTURE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_quota(root: Path, registry_path: Path, runtime: Path, *args: str) -> tuple[dict, int]:
    quota_args = list(args)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "quota",
            *quota_args,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.stdout, result.stderr
    return json.loads(result.stdout), result.returncode


def is_state_or_accounting_command(action: str) -> bool:
    tokens = shlex.split(action)
    command_index = next(
        (
            index
            for index, token in enumerate(tokens)
            if token == "loopx" or token.endswith("/loopx")
        ),
        None,
    )
    if command_index is None:
        return False
    after = tokens[command_index + 1 :]
    parsed_subcommands: list[str] = []
    skip_next = False
    for token in after:
        if skip_next:
            skip_next = False
            continue
        if token in {"--format", "--registry", "--runtime-root"}:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        parsed_subcommands.append(token)
        if len(parsed_subcommands) == 2:
            break
    subcommands = tuple(parsed_subcommands)
    return subcommands[:1] == ("refresh-state",) or subcommands in {
        ("quota", "monitor-poll"),
        ("quota", "spend-slot"),
        ("quota", "should-run"),
    }


def assert_state_or_accounting_command_classification() -> None:
    assert is_state_or_accounting_command(
        "loopx --format json refresh-state --goal-id fixture-goal"
    )
    assert is_state_or_accounting_command(
        "loopx --registry fixture.json quota spend-slot --goal-id fixture-goal"
    )
    assert not is_state_or_accounting_command(
        "loopx status --goal-id refresh-state"
    )
    assert not is_state_or_accounting_command(
        "gh pr view 123 --json statusCheckRollup"
    )


def assert_monitor_poll_event_carries_agent_id(agent_id: str) -> None:
    event = build_quota_monitor_poll_event(
        {
            "goal_id": "scoped-monitor-goal",
            "should_run": True,
            "effective_action": "monitor_quiet_skip",
            "recommended_action": "stay quiet until material transition",
            "reason": "unchanged monitor target",
            "heartbeat_recommendation": {
                "recommended_mode": "monitor_quiet_until_material_transition",
                "reason": "unchanged monitor target",
            },
            "agent_identity": {
                "agent_id": agent_id,
                "registered": True,
                "agent_model": "peer_v1",
                "registered_agents": ["codex-main-control", agent_id],
            },
        },
        source="heartbeat",
    )

    assert event["agent_id"] == agent_id, event
    assert event["monitor_event"]["agent_id"] == agent_id, event
    target = event["monitor_target"]
    assert target["schema_version"] == "quota_monitor_target_v0", target
    assert target["target_id"] == event["monitor_event"]["monitor_target"]["target_id"], event
    assert target["agent_id"] == agent_id, target
    assert target["effective_action"] == "monitor_quiet_skip", target


def assert_monitor_poll_next_cli_action_preserves_agent_id(agent_id: str) -> None:
    available_capabilities = ["shell", "network", "github_cli"]
    actions = interaction_next_cli_actions(
        {
            "goal_id": "scoped-monitor-goal",
            "agent_identity": {
                "agent_id": agent_id,
            },
        },
        mode="monitor_quiet_skip",
        available_capabilities=available_capabilities,
        scheduler_execution_context=(
            GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
        ),
    )

    assert actions == [
        f"loopx quota monitor-poll --goal-id scoped-monitor-goal --agent-id {agent_id} --available-capability shell --available-capability network --available-capability github_cli --runtime-profile outer_controller --execute",
        f"loopx --format json quota should-run --goal-id scoped-monitor-goal --agent-id {agent_id} --available-capability shell --available-capability network --available-capability github_cli --runtime-profile outer_controller",
    ], actions


def assert_interaction_cli_actions_preserve_agent_id(agent_id: str) -> None:
    available_capabilities = [
        "shell",
        "filesystem_write",
        "benchmark_runner",
        "credentials",
        "production_access",
    ]
    projected_capabilities = ["shell", "filesystem_write", "benchmark_runner"]
    scoped_payload = {
        "goal_id": "scoped-delivery-goal",
        "agent_identity": {"agent_id": agent_id},
    }
    command_modes = [
        "external_evidence_observation",
        "autonomous_replan",
        "bounded_delivery",
        "outcome_floor_recovery",
        "capability_bridge_repair",
        "control_plane_self_repair",
        "boundary_projection_repair",
        "scoped_user_gate_fallback",
        "bounded_delivery_with_user_notice",
        "successor_replan_required",
    ]
    for mode in command_modes:
        mode_payload = scoped_payload
        scheduler_execution_context = None
        if mode == "capability_bridge_repair":
            mode_payload = {
                **scoped_payload,
                "capability_gate": {
                    "repair_missing": ["network"],
                    "resolution_bindings": [
                        {
                            "owner": "agent",
                            "capability": "network",
                            "blocked_todo_ids": ["todo_network_probe"],
                        }
                    ],
                    "blocked_candidates": [
                        {
                            "todo_id": "todo_network_probe",
                            "required_capabilities": ["network"],
                            "text": "Check the task-facing public endpoint.",
                            "action_kind": "network_probe",
                        }
                    ],
                },
            }
            scheduler_execution_context = (
                GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
            )
        actions = interaction_next_cli_actions(
            mode_payload,
            mode=mode,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_execution_context,
        )
        state_or_accounting_commands = [
            action
            for action in actions
            if is_state_or_accounting_command(action)
        ]
        assert state_or_accounting_commands, (mode, actions)
        assert all(
            f"--agent-id {agent_id}" in action
            for action in state_or_accounting_commands
        ), (mode, actions)
        if mode == "capability_bridge_repair":
            assert all(
                "--available-capability network" in action
                for action in state_or_accounting_commands
            ), (mode, actions)
        assert all(
            all(
                f"--available-capability {capability}" in action
                for capability in projected_capabilities
            )
            for action in state_or_accounting_commands
        ), (mode, actions)
        assert all(
            "--available-capability credentials" not in action
            and "--available-capability production_access" not in action
            for action in state_or_accounting_commands
        ), (mode, actions)

    unscoped_actions = interaction_next_cli_actions(
        {"goal_id": "unscoped-delivery-goal"},
        mode="bounded_delivery",
        available_capabilities=available_capabilities,
    )
    assert all("--agent-id" not in action for action in unscoped_actions), unscoped_actions
    assert all(
        "--available-capability" not in action for action in unscoped_actions
    ), unscoped_actions


def assert_delivery_completion_spend_preserves_requested_agent_id(agent_id: str) -> None:
    before = {
        "goal_id": "delivery-completion-goal",
        "should_run": False,
        "normal_delivery_allowed": False,
        "recovery_delivery_allowed": False,
        "effective_action": "monitor_quiet_skip",
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "workspace_repair_allowed": False,
        "state": "eligible",
        "safe_bypass_allowed": False,
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "spent_slots": 0,
            "allowed_slots": 1440,
        },
    }
    after = {**before, "quota": {**before["quota"], "spent_slots": 1}}
    preview = {
        "ok": True,
        "mode": "spend-slot",
        "dry_run": True,
        "goal_id": "delivery-completion-goal",
        "slots": 1,
        "agent_id": agent_id,
        "before": before,
        "after": after,
        "delivery_completion_spend": True,
        "delivery_run_generated_at": "2026-01-01T00:00:00+00:00",
        "delivery_run_classification": "validated_delivery_fixture",
    }
    event = build_quota_slot_spend_event(preview, source="heartbeat")

    assert event["agent_id"] == agent_id, event
    assert event["quota_event"]["agent_id"] == agent_id, event
    assert event["quota_event"]["delivery_run_classification"] == "validated_delivery_fixture", event
    assert "validated delivery" in event["health_check"], event


def assert_quota_neutral_events_do_not_hide_same_agent_delivery(agent_id: str) -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-quota-spend-neutral-events-") as tmp:
        runtime = Path(tmp)
        goal_id = "neutral-events-goal"
        index_path = runtime / "goals" / goal_id / "runs" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        delivery = {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "classification": "validated_delivery_fixture",
            "delivery_outcome": "outcome_progress",
            "agent_id": agent_id,
        }
        neutral_runs = [
            delivery,
            {
                "generated_at": "2026-01-01T00:01:00+00:00",
                "classification": "quota_monitor_poll",
                "delivery_outcome": "surface_only",
                "material_change": False,
                "agent_id": agent_id,
            },
            {
                "generated_at": "2026-01-01T00:02:00+00:00",
                "classification": "quota_scheduler_ack",
                "agent_id": agent_id,
            },
        ]
        index_path.write_text(
            "".join(json.dumps(row) + "\n" for row in neutral_runs),
            encoding="utf-8",
        )
        selected = _latest_unspent_turn_settlement_run(
            runtime, goal_id, agent_id=agent_id
        )
        assert selected == delivery, selected

        material_monitor = {
            "generated_at": "2026-01-01T00:03:00+00:00",
            "classification": "quota_monitor_poll",
            "delivery_outcome": "outcome_progress",
            "material_change": True,
            "agent_id": agent_id,
        }
        with index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(material_monitor) + "\n")
        selected = _latest_unspent_turn_settlement_run(
            runtime, goal_id, agent_id=agent_id
        )
        assert selected == material_monitor, selected

        with index_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "generated_at": "2026-01-01T00:04:00+00:00",
                        "classification": "quota_slot_spent",
                        "agent_id": agent_id,
                    }
                )
                + "\n"
            )
        assert (
            _latest_unspent_turn_settlement_run(
                runtime, goal_id, agent_id=agent_id
            )
            is None
        )


def main() -> int:
    fixture = load_quota_plan_fixture()
    agent_id = fixture.SCOPED_AGENT_ID

    assert_state_or_accounting_command_classification()
    assert_monitor_poll_event_carries_agent_id(agent_id)
    assert_monitor_poll_next_cli_action_preserves_agent_id(agent_id)
    assert_interaction_cli_actions_preserve_agent_id(agent_id)
    assert_delivery_completion_spend_preserves_requested_agent_id(agent_id)
    assert_quota_neutral_events_do_not_hide_same_agent_delivery(agent_id)

    with tempfile.TemporaryDirectory(prefix="loopx-quota-spend-agent-identity-") as tmp:
        root = Path(tmp)
        registry_path, runtime, project = fixture.write_cli_fixture(root, scoped_agents=True)
        index_path = runtime / "goals" / "near-limit-half" / "runs" / "index.jsonl"
        registry_before = registry_path.read_text(encoding="utf-8")
        index_before = index_path.read_text(encoding="utf-8")

        quota_capabilities = ["network", "benchmark_runner"]
        scoped_decision, scoped_decision_code = run_quota(
            root,
            registry_path,
            runtime,
            "should-run",
            "--goal-id",
            "near-limit-half",
            "--agent-id",
            agent_id,
            *(
                arg
                for capability in quota_capabilities
                for arg in ("--available-capability", capability)
            ),
        )
        assert scoped_decision_code == 0, scoped_decision
        assert scoped_decision["interaction_contract"]["mode"] == "bounded_delivery", scoped_decision
        scoped_actions = scoped_decision["interaction_contract"]["cli_channel"][
            "next_cli_actions"
        ]
        state_or_accounting_actions = [
            action
            for action in scoped_actions
            if is_state_or_accounting_command(action)
        ]
        assert state_or_accounting_actions, scoped_actions
        assert all(
            f"--agent-id {agent_id}" in action
            and all(
                f"--available-capability {capability}" in action
                for capability in quota_capabilities
            )
            for action in state_or_accounting_actions
        ), scoped_actions

        unscoped_payload, unscoped_code = run_quota(
            root,
            registry_path,
            runtime,
            "spend-slot",
            "--goal-id",
            "near-limit-half",
            "--slots",
            "1",
            "--source",
            "heartbeat",
            "--execute",
        )
        before = unscoped_payload["before"]
        assert unscoped_code == 1, unscoped_payload
        assert unscoped_payload["ok"] is False, unscoped_payload
        assert unscoped_payload["dry_run"] is True, unscoped_payload
        assert unscoped_payload["appended"] is False, unscoped_payload
        assert unscoped_payload["registry_mutated"] is False, unscoped_payload
        assert unscoped_payload["agent_id"] is None, unscoped_payload
        assert before["effective_action"] == "automation_prompt_upgrade_required", unscoped_payload
        assert before["should_run"] is False, unscoped_payload
        assert registry_path.read_text(encoding="utf-8") == registry_before
        assert index_path.read_text(encoding="utf-8") == index_before

        scoped_payload, scoped_code = run_quota(
            root,
            registry_path,
            runtime,
            "spend-slot",
            "--goal-id",
            "near-limit-half",
            "--slots",
            "1",
            "--source",
            "heartbeat",
            "--execute",
            "--agent-id",
            agent_id,
        )
        assert scoped_code == 0, scoped_payload
        assert scoped_payload["ok"] is True, scoped_payload
        assert scoped_payload["appended"] is True, scoped_payload
        assert scoped_payload["agent_id"] == agent_id, scoped_payload
        assert scoped_payload["before"] == scoped_payload["quota_event"]["before"], scoped_payload
        assert scoped_payload["after"] == scoped_payload["quota_event"]["after"], scoped_payload
        assert scoped_payload["after"]["spent_slots"] == scoped_payload["before"]["spent_slots"] + 1, scoped_payload
        assert "quota" not in scoped_payload["before"], scoped_payload

    print("quota-spend-agent-identity-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
