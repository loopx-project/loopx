from __future__ import annotations

import pytest

from loopx.heartbeat_prompt import (
    HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION,
    build_heartbeat_prompt,
    build_heartbeat_prompt_error_payload,
    project_heartbeat_agent_input,
)


@pytest.mark.parametrize("mode", ["full", "compact", "brief", "thin"])
@pytest.mark.parametrize("agents", [["agent-a"], ["agent-a", "agent-b"]])
def test_peer_prompt_defers_workspace_and_lease_requirements_to_current_contract(mode, agents):
    # Registration count alone cannot describe repository or task admission.
    # The task body must not create a second, unconditional workspace policy.
    payload = build_heartbeat_prompt(
        goal_id="workspace-guidance", agent_id="agent-a", registered_agents=agents,
        runtime_profile="codex_app_heartbeat", **{mode: True},
    )
    body = payload["task_body"]
    assert "quota claim/lease and workspace contract plus repo" in body
    assert "use an independent worktree for repository writes" not in body
    assert "independent repo worktree" not in body
    if mode != "full":
        assert payload["interface_budget"]["within_budget"] is True


def test_thin_agent_input_excludes_generator_and_embedded_command_duplicates() -> None:
    generated = build_heartbeat_prompt(
        goal_id="heartbeat-agent-input",
        thin=True,
        agent_id="agent-a",
        registered_agents=["agent-a", "agent-b"],
        runtime_profile="codex_app_heartbeat",
    )

    projected = project_heartbeat_agent_input(generated)

    assert set(projected) == {
        "schema_version",
        "ok",
        "goal_id",
        "agent_id",
        "task_body",
        "interface_budget",
    }
    assert projected["schema_version"] == HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION
    assert projected["task_body"] == generated["task_body"]
    assert projected["interface_budget"] == {
        "mode": "thin",
        "budget_char_count": generated["interface_budget"]["budget_char_count"],
        "max_chars": generated["interface_budget"]["max_chars"],
        "within_budget": generated["interface_budget"]["within_budget"],
    }
    for duplicate in (
        "active_state",
        "active_state_source",
        "resolved_active_state",
        "cli_bin",
        "agent_model",
        "agent_role",
        "registered_agents",
        "runtime_profile",
        "scheduler_execution_context",
        "expanded_prompt_command",
        "thin_prompt_command",
        "quota_guard_command",
        "quota_spend_command",
        "refresh_state_command",
        "progress_refresh_state_command",
        "cli_preflight",
        "material_queue_rule",
        "permission_rule",
    ):
        assert duplicate not in projected


def test_thin_agent_input_keeps_exact_turn_and_bootstrap_identity_when_present() -> None:
    generated = build_heartbeat_prompt(
        goal_id="heartbeat-agent-input",
        thin=True,
        agent_id="agent-a",
        registered_agents=["agent-a"],
        runtime_profile="codex_app_heartbeat",
        turn_instance_id="turn-2026-09-12",
    )
    generated["bootstrap"] = True

    projected = project_heartbeat_agent_input(generated)

    assert projected["turn_instance_id"] == "turn-2026-09-12"
    assert projected["bootstrap"] is True


def test_thin_agent_input_error_is_actionable_without_generator_diagnostics() -> None:
    generated = build_heartbeat_prompt_error_payload(
        goal_id="heartbeat-agent-input",
        error="registered agent identity is required",
        thin=True,
    )

    projected = project_heartbeat_agent_input(generated)

    assert projected == {
        "schema_version": HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION,
        "ok": False,
        "goal_id": "heartbeat-agent-input",
        "error": "registered agent identity is required",
    }


def test_agent_input_projection_rejects_non_thin_generator_payload() -> None:
    generated = build_heartbeat_prompt(
        goal_id="heartbeat-agent-input",
        full=True,
    )

    with pytest.raises(ValueError, match="requires thin mode"):
        project_heartbeat_agent_input(generated)
