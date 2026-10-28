#!/usr/bin/env python3
"""Exercise the default-off peer supervisor configuration and prompt contract."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.cli import main as cli_main  # noqa: E402
from loopx.configure_goal import configure_goal  # noqa: E402
from loopx.control_plane.agents.supervisor import (  # noqa: E402
    HOST_CAPABILITIES_BY_DECISION,
    SupervisorDecisionKind,
    build_supervisor_observation_packet,
    build_supervisor_prompt,
    normalize_supervisor_decision,
    peer_supervisor_for_goal,
)


GOAL_ID = "peer-supervisor-fixture"
AGENTS = ["codex-alpha", "codex-beta", "codex-gamma"]


def read_goal(registry_path: Path) -> dict:
    return json.loads(registry_path.read_text(encoding="utf-8"))["goals"][0]


def observation_status() -> dict:
    return {
        "ok": True,
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "status": "active",
                    "severity": "info",
                    "recommended_action": "Compare peer effects before proposing a branch.",
                    "project_asset": {"user_todos": {"open": 0}},
                }
            ]
        },
        "agent_management_projection": {
            "schema_version": "agent_management_projection_v0",
            "generated_at": "2026-07-12T08:00:00Z",
            "agents": [
                {
                    "agent_id": AGENTS[2],
                    "state": "active",
                    "current_todo": {
                        "todo_id": "todo-gamma",
                        "text": "Validate the bounded peer branch.",
                    },
                    "next_action": "Run the focused smoke.",
                    "last_activity_at": "2026-07-12T07:59:00Z",
                    "workspace_ref": {
                        "kind": "worktree",
                        "branch": "codex/gamma",
                        "path_safe": False,
                    },
                    "handoff_refs": ["handoff-gamma"],
                    "evidence_refs": ["todo:todo-gamma:evidence"],
                }
            ],
        },
    }


def observation_evidence() -> dict:
    return {
        "ok": True,
        "schema_version": "agent_scoped_evidence_log_v0",
        "ledger_count": 2,
        "matched_count": 2,
        "truncated": False,
        "ledger": [
            {
                "source": "rollout_event_log",
                "recorded_at": "2026-07-12T07:59:30Z",
                "event_id": "event-gamma-2",
                "event_kind": "refresh_state",
                "summary": "Focused smoke passed.",
            },
            {
                "source": "run_history",
                "recorded_at": "2026-07-12T07:58:00Z",
                "run_ref": "2026-07-12T07:58:00Z",
                "delivery_outcome": "implementation_progress",
            },
        ],
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-peer-supervisor-") as tmp:
        root = Path(tmp)
        state_file = root / "ACTIVE_GOAL_STATE.md"
        state_file.write_text(
            "---\n"
            "status: active\n"
            "---\n\n"
            "# Active Goal State\n\n"
            "## Objective\n\n"
            "Exercise the opt-in peer supervisor contract.\n\n"
            "## Next Action\n\n"
            "- Observe peer projections before proposing an action.\n",
            encoding="utf-8",
        )
        registry_path = root / "registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "goals": [
                        {
                            "id": GOAL_ID,
                            "domain": "peer-supervisor-smoke",
                            "repo": str(root),
                            "state_file": state_file.name,
                            "adapter": {
                                "kind": "generic_project_goal_v0",
                                "status": "connected",
                            },
                            "coordination": {
                                "agent_model": "peer_v1",
                                "registered_agents": AGENTS,
                            },
                        }
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        from loopx.control_plane.testing.canary_harness import import_fixture_authority
        config = json.loads(registry_path.read_text(encoding="utf-8"))
        config["common_runtime_root"] = str(root / "runtime")
        registry_path.write_text(json.dumps(config), encoding="utf-8")
        import_fixture_authority(registry_path, GOAL_ID, provider="sqlite")

        assert peer_supervisor_for_goal(read_goal(registry_path)) is None

        preview = configure_goal(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            supervisor_agent=AGENTS[0],
        )
        supervisor = preview["after"]["supervisor"]
        assert preview["dry_run"] is True, preview
        assert supervisor == {
            "schema_version": "peer_supervisor_v0",
            "enabled": True,
            "agent_id": AGENTS[0],
            "supervised_agents": AGENTS[1:],
            "execution_mode": "proposal_only",
        }, supervisor
        assert read_goal(registry_path)["coordination"].get("supervisor") is None

        applied = configure_goal(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            supervisor_agent=AGENTS[0],
            supervised_agents=[AGENTS[2]],
            execute=True,
        )
        assert applied["written"] is True, applied
        assert applied["supervisor_prompt"]["status"] == "ready", applied
        assert "supervisor-prompt" in applied["supervisor_prompt"]["command"], applied
        goal = read_goal(registry_path)
        assert "primary_agent" not in goal["coordination"], goal
        configured = peer_supervisor_for_goal(goal)
        assert configured["supervised_agents"] == [AGENTS[2]], configured

        prompt = build_supervisor_prompt(
            goal_id=GOAL_ID,
            active_state=str(state_file),
            supervisor=configured,
        )
        contract = prompt["supervisor_contract"]
        assert contract["peer_authority"] == "equal_identity_authority", contract
        assert contract["supervisor_authority"] == "proposal_only", contract
        assert contract["user_interaction"]["user_may_interact_with_any_peer"] is True
        assert contract["decision_contract"]["kinds"] == [
            kind.value for kind in SupervisorDecisionKind
        ]
        assert HOST_CAPABILITIES_BY_DECISION["inject"] == [
            "session_message_injection"
        ]
        assert HOST_CAPABILITIES_BY_DECISION["discard"] == ["session_termination"]
        task_body = prompt["task_body"]
        assert "not a durable leader" in task_body, task_body
        assert "proposal-only" in task_body, task_body
        assert "supervisor-observe" in task_body and "quota should-run" in task_body
        assert "supervisor-event propose" in task_body, task_body
        assert "evidence-log" not in task_body, task_body
        assert f"status --goal-id {GOAL_ID} --agent-id {AGENTS[2]}" not in task_body
        assert f"quota should-run --goal-id {GOAL_ID} --agent-id {AGENTS[2]}" not in task_body

        observation = build_supervisor_observation_packet(
            goal_id=GOAL_ID,
            supervisor=configured,
            status_payload=observation_status(),
            evidence_logs={AGENTS[2]: observation_evidence()},
        )
        assert observation["schema_version"] == "supervisor_observation_v0", observation
        assert observation["mode"] == "read_only", observation
        assert observation["decision_input_complete"] is True, observation
        assert observation["warnings"] == [], observation
        assert observation["goal"]["user_open_count"] == 0, observation
        peer = observation["peers"][0]
        assert peer["agent_id"] == AGENTS[2], peer
        assert peer["current_todo"]["todo_id"] == "todo-gamma", peer
        assert peer["evidence"]["latest_recorded_at"] == "2026-07-12T07:59:30Z", peer
        assert peer["evidence"]["effect_refs"] == [
            "todo:todo-gamma:evidence",
            "rollout_event:event-gamma-2",
            "run_history:2026-07-12T07:58:00Z",
        ], peer
        assert observation["boundary"]["raw_transcripts_included"] is False
        assert observation["boundary"]["write_authority"] == "none"

        incomplete = build_supervisor_observation_packet(
            goal_id=GOAL_ID,
            supervisor={**configured, "supervised_agents": AGENTS[1:]},
            status_payload=observation_status(),
            evidence_logs={AGENTS[2]: observation_evidence()},
        )
        assert incomplete["decision_input_complete"] is False, incomplete
        assert incomplete["warnings"] == [
            f"missing_agent_status:{AGENTS[1]}",
            f"missing_evidence_log:{AGENTS[1]}",
        ], incomplete

        degraded_status = observation_status()
        degraded_status["ok"] = False
        degraded_status["contract_errors"] = ["fixture contract error"]
        degraded = build_supervisor_observation_packet(
            goal_id=GOAL_ID,
            supervisor=configured,
            status_payload=degraded_status,
            evidence_logs={AGENTS[2]: observation_evidence()},
        )
        assert degraded["ok"] is True, degraded
        assert degraded["decision_input_complete"] is False, degraded
        assert degraded["warnings"] == ["status_projection_degraded"], degraded
        assert degraded["status_health"]["contract_error_count"] == 1, degraded

        decision = normalize_supervisor_decision(
            {
                "decision_id": "handoff-1",
                "kind": "handoff",
                "source_agent_id": AGENTS[2],
                "target_agent_id": AGENTS[1],
                "state_ref": "runtime-state:42",
                "reason_codes": ["scope-overlap"],
                "evidence_refs": ["effect:42"],
            },
            supervisor={**configured, "supervised_agents": AGENTS[1:]},
        )
        assert decision["execution_status"] == "proposal_only", decision
        assert decision["required_host_capabilities"] == [
            "session_state_fork",
            "workspace_state_transfer",
        ], decision

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli_main(
                [
                    "--registry",
                    str(registry_path),
                    "supervisor-prompt",
                    "--format",
                    "json",
                    "--goal-id",
                    GOAL_ID,
                    "--agent-id",
                    AGENTS[0],
                ]
            )
        assert exit_code == 0, stdout.getvalue()
        cli_payload = json.loads(stdout.getvalue())
        assert cli_payload["agent_id"] == AGENTS[0], cli_payload
        assert cli_payload["supervisor_contract"]["supervised_agents"] == [
            AGENTS[2]
        ], cli_payload

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli_main(
                [
                    "--registry",
                    str(registry_path),
                    "--runtime-root",
                    str(root / "runtime"),
                    "supervisor-observe",
                    "--format",
                    "json",
                    "--goal-id",
                    GOAL_ID,
                    "--agent-id",
                    AGENTS[0],
                ]
            )
        assert exit_code == 0, stdout.getvalue()
        cli_observation = json.loads(stdout.getvalue())
        assert cli_observation["schema_version"] == "supervisor_observation_v0"
        assert [peer["agent_id"] for peer in cli_observation["peers"]] == [AGENTS[2]]

        try:
            configure_goal(
                registry_path=registry_path,
                goal_id=GOAL_ID,
                supervisor_agent=AGENTS[0],
                supervised_agents=[AGENTS[0]],
            )
        except ValueError as exc:
            assert "cannot supervise its own" in str(exc), exc
        else:
            raise AssertionError("self-supervision must fail closed")

        try:
            normalize_supervisor_decision(
                {
                    "decision_id": "bad-handoff",
                    "kind": "handoff",
                    "source_agent_id": AGENTS[1],
                    "target_agent_id": AGENTS[1],
                    "state_ref": "runtime-state:43",
                    "reason_codes": ["scope-overlap"],
                    "evidence_refs": ["effect:43"],
                },
                supervisor={**configured, "supervised_agents": AGENTS[1:]},
            )
        except ValueError as exc:
            assert "must differ" in str(exc), exc
        else:
            raise AssertionError("same-session handoff must fail closed")

        cleared = configure_goal(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            clear_supervisor=True,
            execute=True,
        )
        assert cleared["after"]["supervisor"] is None, cleared
        assert cleared["supervisor_prompt"]["status"] == "disabled", cleared

    print("peer-supervisor-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
