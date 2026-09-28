from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from loopx.agent_onboarding import (
    build_agent_onboarding_packet,
    render_agent_onboarding_markdown,
)
from loopx.bootstrap_command_pack import build_loopx_bootstrap_command_pack
from loopx.heartbeat_prompt import (
    build_heartbeat_prompt,
    render_heartbeat_prompt_markdown,
    uses_native_goal_host_loop,
)
from loopx.host_loop_activation import (
    AgentTypeError,
    agent_type_for_host_surface,
    agent_type_uses_host_managed_skills,
    build_host_loop_activation_packet,
    normalize_agent_type,
    scheduler_command_binding_for_agent_type,
)
from loopx.project_prompt import render_accountable_progress_refresh_command


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_onboarding_goal(
    tmp_path: Path,
    *,
    goal_id: str,
    registered_agents: list[str],
) -> tuple[Path, Path]:
    project = tmp_path / "project"
    home = tmp_path / "home"
    state_file = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
    project_registry = project / ".loopx" / "registry.json"
    global_registry = home / ".codex" / "loopx" / "registry.global.json"
    state_file.parent.mkdir(parents=True)
    project_registry.parent.mkdir(parents=True)
    global_registry.parent.mkdir(parents=True)
    state_file.write_text("# Active Goal State\n", encoding="utf-8")
    registry = {
        "goals": [
            {
                "id": goal_id,
                "status": "active",
                "repo": str(project),
                "state_file": str(state_file.relative_to(project)),
                "coordination": {
                    "agent_model": "peer_v1",
                    "registered_agents": registered_agents,
                },
            }
        ]
    }
    serialized_registry = json.dumps(registry)
    project_registry.write_text(serialized_registry, encoding="utf-8")
    global_registry.write_text(serialized_registry, encoding="utf-8")
    return project, home


def _run_activation_command(command: str, *, home: Path) -> dict[str, object]:
    completed = subprocess.run(
        shlex.split(command),
        cwd=REPO_ROOT,
        env={
            "HOME": str(home),
            "PATH": os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def _assert_traex_visible_goal_body_contract(task_body: str) -> None:
    lower_task_body = task_body.lower()
    assert re.search(
        r"(?<![a-z0-9_/])/loop(?![a-z0-9_-])",
        lower_task_body,
    ) is None, f"forbidden /loop command in TraeX task body: {task_body}"
    normalized_words = " ".join(re.findall(r"[a-z0-9]+", lower_task_body))
    for forbidden in (
        "heartbeat",
        "heartbeat prequota",
        "automation",
        "automation update",
        "rrule",
        "receipt",
        "current time",
        "first turn",
        "first turn receipt",
        "scheduler",
        "scheduler execution context",
        "turn instance id",
        "loopx turn",
    ):
        assert re.search(rf"\b{re.escape(forbidden)}\b", normalized_words) is None, (
            f"forbidden {forbidden} content in TraeX task body: {task_body}"
        )


@pytest.mark.parametrize(
    "forbidden_body",
    (
        "Run /loop now.",
        "Run `/loop` now.",
        "capability heartbeat_prequota",
        "set current_time",
        "call automation-update",
        "apply RRULE",
        "emit first_turn_receipt",
        "emit first-turn receipt",
        "use scheduler_execution_context",
    ),
)
def test_traex_visible_goal_body_contract_rejects_forbidden_variants(
    forbidden_body: str,
) -> None:
    with pytest.raises(AssertionError):
        _assert_traex_visible_goal_body_contract(forbidden_body)


def test_codex_ide_plugin_is_an_exact_host_type_with_visible_goal_activation() -> None:
    assert normalize_agent_type("codex-ide-plugin") == "codex-ide-plugin"
    assert normalize_agent_type("VSCode Codex") == "codex-ide-plugin"
    assert normalize_agent_type("codex-ide") == "codex-ide-plugin"
    assert agent_type_for_host_surface("codex-ide-plugin") == "codex-ide-plugin"
    assert agent_type_for_host_surface("codex-ide") == "codex-ide-plugin"
    assert agent_type_for_host_surface("codex-app") == "codex-app"
    assert agent_type_for_host_surface("codex-cli-tui") == "codex-cli"
    assert normalize_agent_type("Open Code") == "opencode"
    assert agent_type_for_host_surface("opencode") == "opencode"
    assert normalize_agent_type("Kunlun Code") == "kunluncode"
    assert agent_type_for_host_surface("kunlun") == "kunluncode"
    assert agent_type_for_host_surface("ark-managed-agent") == "ark-managed-agent"

    packet = build_host_loop_activation_packet(
        agent_type="codex-ide-plugin",
        goal_id="fixture-goal",
        agent_id="codex-fixture",
        registered_agents=["codex-fixture"],
    )

    assert packet["host_surface"] == "codex_ide_visible_goal_mode"
    assert packet["activation_method"] == "set_visible_goal"
    assert packet["host_mutation"]["owner"] == "Codex IDE plugin composer"
    assert packet["host_mutation"]["host_command"] == "/goal <task_body>"
    assert "automation_update" not in str(packet)
    assert (
        "--runtime-profile codex_cli"
        in packet["commands"]["heartbeat_prompt"]
    )
    assert " -H " not in packet["commands"]["heartbeat_prompt"]
    assert " -O " not in packet["commands"]["heartbeat_prompt"]
    assert " -M " not in packet["commands"]["heartbeat_prompt"]


@pytest.mark.parametrize(
    ("agent_type", "runtime_profile"),
    (
        ("ark-managed-agent", "ark_managed_agent_goal"),
        ("codex-app", "codex_app_heartbeat"),
        ("trae_app", "trae_app"),
        ("codex-app-ssh", "codex_app_ssh_goal"),
        ("codex-cli", "codex_cli"),
        ("codex-ide-plugin", "codex_cli"),
        ("claude-code", "claude_code"),
        ("kunluncode", "kunluncode"),
        ("kiro-cli", "kiro_cli"),
        ("opencode", "generic_cli"),
        ("traex-cli", "generic_cli"),
        ("pi", "generic_cli"),
    ),
)
def test_first_class_hosts_bind_one_runtime_profile(
    agent_type: str,
    runtime_profile: str,
) -> None:
    assert scheduler_command_binding_for_agent_type(agent_type) == {
        "runtime_profile": runtime_profile
    }


@pytest.mark.parametrize(
    ("runtime_profile", "expected"),
    (
        ("ark_managed_agent_goal", True),
        ("codex_app_ssh_goal", True),
        ("codex_cli", True),
        ("codex_app_heartbeat", False),
        ("claude_code", False),
        # Kiro settles as a visible Goal but must not render the Codex native
        # `/goal` task body or its blocked-state rules.
        ("kiro_cli", False),
    ),
)
def test_native_goal_host_family_is_profile_driven(
    runtime_profile: str,
    expected: bool,
) -> None:
    assert uses_native_goal_host_loop(
        runtime_profile=runtime_profile,
        scheduler_execution_context=None,
    ) is expected


def test_pi_is_an_exact_host_type_with_visible_goal_extension_activation() -> None:
    assert normalize_agent_type("pi") == "pi"
    assert normalize_agent_type("Pi") == "pi"
    assert normalize_agent_type("pi-agent") == "pi"
    assert normalize_agent_type("pi_agent") == "pi"
    assert normalize_agent_type("earendil-pi") == "pi"
    assert agent_type_for_host_surface("pi") == "pi"
    assert agent_type_for_host_surface("pi-tui") == "pi"
    assert scheduler_command_binding_for_agent_type("pi") == {
        "runtime_profile": "generic_cli"
    }

    packet = build_host_loop_activation_packet(
        agent_type="pi",
        goal_id="fixture-goal",
        agent_id="pi-fixture",
        registered_agents=["pi-fixture"],
    )

    assert packet["host_surface"] == "pi_visible_goal_mode"
    assert packet["activation_method"] == "activate_loopx_pi_goal_extension"
    assert packet["setup_command"] == "loopx slash-commands --install --surface pi"
    assert packet["host_mutation"]["owner"] == "Pi LoopX goal extension"
    assert packet["host_mutation"]["host_tool"] == "loopx_goal_activate"
    assert packet["host_mutation"]["tool_argument_mapping"]["activationToken"] == (
        "pi_session_authority.token from the host startup/session packet"
    )
    assert packet["host_mutation"]["tool_argument_mapping"]["goalId"] == (
        "optional compatibility echo; host authority derives the value"
    )
    assert packet["host_mutation"]["tool_argument_mapping"]["objective"] == (
        "heartbeat_prompt.task_body"
    )
    assert "automation_update" not in str(packet)
    assert (
        "--runtime-profile generic_cli"
        in packet["commands"]["heartbeat_prompt"]
    )
    assert (
        packet["success_criteria"][0]
        == "The visible Pi session has a LoopX-backed goal bound through loopx_goal_activate."
    )


def test_pi_is_not_a_native_goal_host() -> None:
    # Pi's visible loop is extension-driven and gated by LoopX quota, so it is
    # not part of the native goal host family (like opencode, unlike codex-cli).
    assert scheduler_command_binding_for_agent_type("pi") == {
        "runtime_profile": "generic_cli"
    }


def test_deepseek_harness_is_an_exact_host_type_with_external_loop_activation() -> None:
    assert normalize_agent_type("dsh") == "deepseek-harness"
    assert normalize_agent_type("DeepSeek Harness") == "deepseek-harness"
    assert agent_type_for_host_surface("deepseek-harness") == "deepseek-harness"
    assert agent_type_for_host_surface("dsh") == "deepseek-harness"
    assert scheduler_command_binding_for_agent_type("deepseek-harness") == {
        "runtime_profile": "generic_cli"
    }

    packet = build_host_loop_activation_packet(
        agent_type="deepseek-harness",
        goal_id="fixture-goal",
        agent_id="dsh-fixture",
        registered_agents=["dsh-fixture"],
    )
    assert packet["host_surface"] == "deepseek_harness_automation_loop", packet
    assert packet["activation_method"] == "external_loop_driver", packet
    assert "--runtime-profile generic_cli" in packet["commands"]["heartbeat_prompt"], packet
    assert "--host dsh" in packet["entry_command_hint"], packet
    assert "loopx.dsh_goal_mode" in packet["entry_command_hint"], packet
    # The historical launcher stays a documented compatibility path.
    assert "scripts/dsh_turn_host_adapter.py" in packet["entry_command_hint"], packet


def test_deepseek_harness_native_is_distinct_same_session_host() -> None:
    assert normalize_agent_type("dsh-native") == "deepseek-harness-native"
    assert normalize_agent_type("DeepSeek Harness Native") == "deepseek-harness-native"
    assert agent_type_for_host_surface("deepseek-harness-native") == (
        "deepseek-harness-native"
    )
    assert scheduler_command_binding_for_agent_type("deepseek-harness-native") == {
        "runtime_profile": "generic_cli"
    }

    packet = build_host_loop_activation_packet(
        agent_type="deepseek-harness-native",
        goal_id="fixture-goal",
        agent_id="dsh-native-fixture",
        registered_agents=["dsh-native-fixture"],
    )
    assert packet["host_surface"] == "deepseek_harness_native_same_session"
    assert packet["activation_method"] == "same_session_plugin_driver"
    assert packet["host_mutation"]["host_loop_primitive"] == "exact live Agent.followup"
    assert "/loopx-init" in packet["entry_command_hint"]
    assert "loopx.dsh_goal_mode" not in packet["entry_command_hint"]

    from loopx.agent_onboarding import _skill_delivery_contract

    skill_delivery = _skill_delivery_contract("deepseek-harness-native")
    assert skill_delivery["owner"] == "dsh_loopx_plugin"
    assert skill_delivery["preferred_delivery"] == "dsh_loopx_init_command"
    assert skill_delivery["install_command"] == "/loopx-init"
    assert skill_delivery["entry_host_surface"] == "deepseek-harness-native"


@pytest.mark.parametrize(
    "runtime_profile",
    ("ark_managed_agent_goal", "codex_app_ssh_goal"),
)
def test_goal_hosts_delegate_spend_to_live_settlement_not_static_templates(
    runtime_profile: str,
) -> None:
    payload = build_heartbeat_prompt(
        goal_id="goal-spend-attribution-fixture",
        thin=True,
        runtime_profile=runtime_profile,
    )
    task_body = payload["task_body"]
    refresh_command = f"`{payload['progress_refresh_state_command']}`"
    spend_command = f"`{payload['quota_spend_command']}`"

    assert refresh_command not in task_body
    assert spend_command not in task_body
    assert "settlement_plan.ordered_steps" in task_body
    assert "preserve identities/flags" in task_body
    assert "<PUBLIC_SAFE_PROGRESS_CLASSIFICATION>" in refresh_command
    assert "<ACTUAL_DELIVERY_BATCH_SCALE>" in refresh_command
    assert "<ACTUAL_DELIVERY_OUTCOME>" in refresh_command
    assert "--delivery-batch-scale multi_surface" not in refresh_command
    assert "--delivery-outcome outcome_progress" not in refresh_command
    assert payload["quota_spend_command"].startswith("loopx --format json ")
    assert "actual outcomes" in task_body
    assert "readback/recovery" in task_body


def test_heartbeat_prompt_commands_keep_explicit_runtime_root() -> None:
    runtime_root = Path("/tmp/loopx-runtime-root-fixture")

    payload = build_heartbeat_prompt(
        goal_id="runtime-root-fixture",
        thin=True,
        runtime_root=runtime_root,
        runtime_profile="codex_cli",
        agent_id="runtime-agent",
        registered_agents=["runtime-agent"],
    )

    command_prefix = f"loopx --runtime-root {runtime_root}"

    assert payload["quota_guard_command"].startswith(
        f"{command_prefix} --format json quota should-run "
    )
    assert payload["quota_spend_command"].startswith(
        f"{command_prefix} --format json quota spend-slot "
    )
    assert payload["refresh_state_command"].startswith(
        f"{command_prefix} refresh-state "
    )
    assert payload["progress_refresh_state_command"].startswith(
        f"{command_prefix} refresh-state "
    )
    assert payload["thin_prompt_command"].startswith(
        f"{command_prefix} heartbeat-prompt "
    )
    assert '"$HOME/.codex/loopx/registry.global.json"' not in payload["task_body"]
    assert f"--runtime-root {runtime_root}" in payload["task_body"]


@pytest.mark.parametrize(
    "runtime_profile",
    ("ark_managed_agent_goal", "codex_app_ssh_goal"),
)
def test_goal_hosts_enter_live_contract_without_a_mandatory_skill_detour(
    runtime_profile: str,
) -> None:
    payload = build_heartbeat_prompt(
        goal_id="goal-runtime-routing-fixture",
        thin=True,
        runtime_profile=runtime_profile,
    )
    task_body = " ".join(payload["task_body"].split())

    assert "Use the current `interaction_contract`, not remembered commands" in task_body
    assert "loopx-project" in task_body
    assert "loopx-self-repair" in task_body
    assert "Progress is not a new Goal boundary" in task_body
    assert "do not create a new host Goal merely to continue" in task_body


def test_goal_hosts_reuse_thin_dispatch_and_stay_compact() -> None:
    shared_rules = (
        "use selection_command when required",
        "No learning queue unless asked.",
    )
    common = {
        "goal_id": "goal-prompt-composition-fixture",
        "thin": True,
        "agent_id": "codex-main-control",
        "agent_scopes": ["visible goal delivery lane"],
        "registered_agents": ["codex-main-control"],
    }
    generic = build_heartbeat_prompt(
        **common,
        runtime_profile="codex_app_heartbeat",
    )
    goal_hosts = [
        build_heartbeat_prompt(**common, runtime_profile="codex_app_ssh_goal"),
        build_heartbeat_prompt(**common, runtime_profile="codex_cli"),
        build_heartbeat_prompt(**common, runtime_profile="ark_managed_agent_goal"),
        build_heartbeat_prompt(
            **common,
            runtime_profile="generic_cli",
            visible_goal_host="traex-cli",
        ),
    ]

    for rule in shared_rules:
        assert rule in generic["task_body"]
    for payload in goal_hosts:
        assert "selection_command" in payload["task_body"]
        assert "No learning queue unless asked." in payload["task_body"]
        assert (
            "do/verify authorized work, then follow next_cli_actions for writeback/spend"
            in payload["task_body"]
        )
        assert payload["interface_budget"]["budget_char_count"] <= 2_800
        assert payload["interface_budget"]["within_budget"] is True


def test_native_codex_goal_wait_rule_matches_blocked_resume_contract() -> None:
    ssh_body = build_heartbeat_prompt(
        goal_id="ssh-wait-fixture",
        thin=True,
        runtime_profile="codex_app_ssh_goal",
    )["task_body"]
    cli_body = build_heartbeat_prompt(
        goal_id="cli-wait-fixture",
        thin=True,
        runtime_profile="codex_cli",
    )["task_body"]
    managed_body = build_heartbeat_prompt(
        goal_id="managed-wait-fixture",
        thin=True,
        runtime_profile="ark_managed_agent_goal",
    )["task_body"]

    for body in (ssh_body, cli_body):
        assert "call `update_goal` with `status=blocked`" in body
        assert "Only user `/goal resume`" in body
        assert "reactivates it; rerun quota after resume" in body
    assert "call `update_goal` with `status=blocked`" not in managed_body


@pytest.mark.parametrize(
    ("runtime_profile", "expected_host"),
    (
        ("ark_managed_agent_goal", "Ark Managed Agent goal prompt"),
        ("codex_app_ssh_goal", "visible Codex /goal task body"),
        ("codex_cli", "visible Codex /goal task body"),
    ),
)
def test_native_goal_budget_error_names_the_actual_host(
    runtime_profile: str,
    expected_host: str,
) -> None:
    with pytest.raises(ValueError, match=expected_host):
        build_heartbeat_prompt(
            goal_id="oversized-native-goal",
            thin=True,
            runtime_profile=runtime_profile,
            permission_rule="x" * 4_000,
        )


def test_accountable_refresh_preserves_explicit_validated_turn_semantics() -> None:
    command = render_accountable_progress_refresh_command(
        "validated-turn-fixture",
        classification="contract_only_preparation",
        delivery_batch_scale="single_surface",
        delivery_outcome="surface_only",
    )

    assert "--classification contract_only_preparation" in command
    assert "--delivery-batch-scale single_surface" in command
    assert "--delivery-outcome surface_only" in command
    assert "multi_surface" not in command
    assert "outcome_progress" not in command


def test_codex_app_activation_uses_narrow_runtime_profile() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="codex-app",
        goal_id="fixture-goal",
        agent_id="codex-fixture",
        registered_agents=["codex-fixture"],
    )

    command = packet["commands"]["heartbeat_prompt"]
    assert "--codex-app" in command
    assert "--runtime-profile" not in command
    assert "--host-surface" not in command
    assert "--scheduler-owner" not in command
    assert "--execution-mode" not in command


def test_trae_app_activation_uses_host_automation_and_stays_distinct_from_cli() -> None:
    assert normalize_agent_type("trae_app") == "trae_app"
    assert agent_type_for_host_surface("trae_app") == "trae_app"
    assert normalize_agent_type("traex") == "traex-cli"
    with pytest.raises(AgentTypeError, match="unsupported agent_type"):
        normalize_agent_type("Trae App")

    packet = build_host_loop_activation_packet(
        agent_type="trae_app",
        goal_id="fixture-goal",
        agent_id="trae_app-fixture",
        registered_agents=["trae_app-fixture"],
    )

    command = packet["commands"]["heartbeat_prompt"]
    assert packet["host_surface"] == "trae_app"
    assert packet["activation_method"] == (
        "create_or_update_trae_app_automation"
    )
    assert packet["host_mutation"]["preferred_tool"] == "automation_update"
    assert "--trae_app" in command
    assert "--codex-app" not in command
    assert "--runtime-profile" not in command
    assert any(
        "settled non-terminal turn leaves the automation active" in criterion
        for criterion in packet["success_criteria"]
    )

    prompt = build_heartbeat_prompt(
        goal_id="trae_app-prompt-fixture",
        thin=True,
        runtime_profile="trae_app",
    )
    assert "--trae_app" in prompt["quota_guard_command"]
    assert "--trae_app" in prompt["task_body"]
    rendered = render_heartbeat_prompt_markdown(prompt)
    assert "Trae App heartbeat automation" in rendered
    assert "Codex App heartbeat automation" not in rendered


def test_new_agent_onboarding_defaults_to_fresh_identity() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="codex-app",
        goal_id="fixture-goal",
        registered_agents=["codex-existing"],
        fresh_agent_default=True,
    )

    assert packet["activation_state"] == "fresh_agent_registration_required"
    assert packet["activation_allowed"] is False
    gate = packet["identity_selection_gate"]
    assert gate["default_action"] == "register_fresh_agent"
    assert gate["fresh_agent_registration"]["recommended"] is True
    assert "register-agent --goal-id fixture-goal" in gate[
        "fresh_agent_registration"
    ]["preview_command"]
    assert "--require-new" in gate["fresh_agent_registration"]["preview_command"]
    assert gate["fresh_agent_registration"]["execute_command"].endswith(
        "--execute"
    )
    continuation = gate["fresh_agent_registration"]["continuation_contract"]
    assert continuation["requires_execute_result"] is True
    assert continuation["required_result"] == {
        "ok": True,
        "changed": True,
        "written": True,
        "global_sync": {"ok": True},
        "registration_readback": {"verified": True},
    }
    assert "preview as advisory" in gate["fresh_agent_registration"]["continuation"]
    assert len(gate["choices"]) == 1
    takeover = gate["choices"][0]
    assert takeover["agent_id"] == "codex-existing"
    assert takeover["mode"] == "takeover_existing_agent"
    assert takeover["requires_explicit_takeover_intent"] is True


@pytest.mark.parametrize(
    "agent_type",
    (
        "ark-managed-agent",
        "codex-app",
        "codex-app-ssh",
        "codex-ide-plugin",
        "codex-cli",
        "claude-code",
        "opencode",
        "manual",
        "other-agent",
    ),
)
def test_non_traex_identity_selection_preserves_v0_prompt_fields(
    agent_type: str,
) -> None:
    packet = build_host_loop_activation_packet(
        agent_type=agent_type,
        goal_id="v0-identity-selection-fixture",
        registered_agents=["agent-main", "agent-reviewer"],
    )

    choice = packet["identity_selection_gate"]["choices"][0]
    assert choice["heartbeat_prompt_json"]
    assert choice["heartbeat_prompt"]
    assert choice["activation_input_command"] == choice["heartbeat_prompt_json"]


def test_new_agent_onboarding_gates_an_empty_agent_registry() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="codex-app",
        goal_id="fixture-goal",
        registered_agents=[],
        fresh_agent_default=True,
    )

    gate = packet["identity_selection_gate"]
    assert packet["activation_allowed"] is False
    assert gate["default_action"] == "register_fresh_agent"
    assert gate["choices"] == []


def test_explicit_identity_preserves_existing_agent_continuation() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="codex-app",
        goal_id="fixture-goal",
        agent_id="codex-existing",
        registered_agents=["codex-existing"],
        fresh_agent_default=True,
    )

    assert packet["activation_state"] == "selected"
    assert packet["activation_allowed"] is True
    assert packet["agent_id"] == "codex-existing"
    assert packet["identity_selection_gate"] is None


def test_codex_app_thin_prompt_embeds_profile_only_in_quota_command() -> None:
    prompt = build_heartbeat_prompt(
        goal_id="fixture-goal",
        thin=True,
        runtime_profile="codex_app_heartbeat",
    )

    assert "--codex-app" in prompt["quota_guard_command"]
    assert "--codex-app" in prompt["task_body"]
    assert "host_surface" not in prompt["task_body"]
    assert "scheduler_owner" not in prompt["task_body"]
    assert (
        "use selection_command when required"
    ) in prompt["task_body"]
    assert "compact_prompt_command" not in prompt
    assert "brief_prompt_command" not in prompt
    assert prompt["interface_budget"]["within_budget"] is True


def test_opencode_activation_uses_bridge_tool_and_generic_cli_quota() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="opencode",
        goal_id="fixture-goal",
        agent_id="opencode-fixture",
        registered_agents=["opencode-fixture"],
    )

    assert packet["host_surface"] == "opencode_visible_goal_mode"
    assert packet["activation_method"] == "activate_loopx_opencode_goal_bridge"
    assert packet["host_mutation"]["host_tool"] == "loopx_goal_activate"
    assert packet["setup_command"].endswith(
        "--surface opencode --with-goal-bridge"
    )
    assert "--runtime-profile generic_cli" in packet["commands"]["heartbeat_prompt"]


def test_opencode2_activation_starts_the_goal_worker() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="opencode2",
        goal_id="fixture-goal",
        agent_id="opencode2-fixture",
        registered_agents=["opencode2-fixture"],
    )

    assert packet["host_surface"] == "opencode2_goal_worker_mode"
    assert packet["activation_method"] == "start_opencode2_goal_worker"
    assert packet["host_mutation"]["host_tool"] == "opencode2-goal-worker"
    assert packet["host_mutation"]["cli_can_mutate_directly"] is True
    assert any(
        "opencode2-goal-worker" in str(step) for step in packet["activation_steps"]
    )
    assert "--runtime-profile generic_cli" in packet["commands"]["heartbeat_prompt"]




def test_kunluncode_activation_uses_native_goal_controller_and_runtime_profile() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="kunluncode",
        goal_id="fixture-goal",
        agent_id="kunlun-fixture",
        registered_agents=["kunlun-fixture"],
    )

    assert packet["host_surface"] == "kunluncode_native_goal_controller"
    assert packet["activation_method"] == "bind_project_then_run_native_goal"
    assert packet["host_mutation"]["managed_mcp_server"] == "loopx-kunluncode"
    assert packet["host_mutation"]["host_command"] == "loopx-kunluncode run --project ."
    assert "loopx-kunluncode connect" in packet["setup_command"]
    assert any(
        "model does not type `/goal-pro`" in step
        for step in packet["activation_steps"]
    )
    assert any(
        "independent verification" in criterion
        for criterion in packet["success_criteria"]
    )
    assert any(
        "lifecycle CLI writes" in criterion
        for criterion in packet["success_criteria"]
    )
    assert "--runtime-profile kunluncode" in packet["commands"]["heartbeat_prompt"]


def test_standard_heartbeat_omits_inactive_visible_goal_host() -> None:
    payload = build_heartbeat_prompt(goal_id="standard-heartbeat-fixture", thin=True)

    assert "visible_goal_host" not in payload


def test_traex_cli_is_an_exact_visible_goal_host_on_the_generic_cli_loop() -> None:
    assert normalize_agent_type("traex") == "traex-cli"
    assert normalize_agent_type("TraeX CLI") == "traex-cli"
    with pytest.raises(AgentTypeError, match="unsupported agent_type"):
        normalize_agent_type("trae")
    assert agent_type_for_host_surface("traex-cli") == "traex-cli"
    assert agent_type_for_host_surface("traex") == "traex-cli"

    packet = build_host_loop_activation_packet(
        agent_type="traex-cli",
        goal_id="fixture-goal",
        agent_id="traex-fixture",
        registered_agents=["traex-fixture"],
    )

    assert packet["host_surface"] == "traex_visible_goal_mode"
    assert packet["activation_method"] == "set_visible_goal"
    assert packet["host_mutation"]["owner"] == "TraeX CLI TUI"
    assert packet["host_mutation"]["host_command"] == "/goal <task_body>"
    assert packet["host_mutation"]["requires_host_feature_flag"] == (
        "[features] goals = true in ~/.trae/traecli.toml"
    )
    # TraeX is a generic visible CLI loop, not the Codex-native goal contract, and
    # LoopX ships no Codex App automation for it.
    assert "--runtime-profile generic_cli" in packet["commands"]["heartbeat_prompt"]
    assert "automation_update" not in str(packet)
    assert agent_type_uses_host_managed_skills("traex-cli") is True


def test_traex_activation_command_renders_visible_goal_task_body(tmp_path: Path) -> None:
    goal_id = "traex-visible-goal-fixture"
    project = tmp_path / "project"
    state_file = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
    registry = tmp_path / ".codex" / "loopx" / "registry.global.json"
    state_file.parent.mkdir(parents=True)
    registry.parent.mkdir(parents=True)
    state_file.write_text("# Active Goal State\n", encoding="utf-8")
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": goal_id,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_file.relative_to(project)),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    packet = build_host_loop_activation_packet(
        agent_type="traex-cli",
        goal_id=goal_id,
        cli_bin=str(REPO_ROOT / "scripts" / "loopx"),
    )

    command = packet["commands"]["visible_goal_prompt_json"]
    assert packet["activation_input_command"] == command
    completed = subprocess.run(
        shlex.split(command),
        cwd=REPO_ROOT,
        env={
            "HOME": str(tmp_path),
            "PATH": os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)
    task_body = payload["task_body"]
    normalized_task_body = " ".join(task_body.split())

    assert payload["interface_budget"]["mode"] == "visible_goal"
    assert payload["interface_budget"]["within_budget"] is True
    assert "--runtime-profile generic_cli" in payload["quota_guard_command"]
    assert "--source visible-goal" in payload["quota_spend_command"]
    assert "visible TraeX `/goal` task" in normalized_task_body
    _assert_traex_visible_goal_body_contract(task_body)
    assert re.search(
        r"(?<![a-z0-9_/])/loop(?![a-z0-9_-])",
        str(packet).lower(),
    ) is None


def test_connected_traex_commands_all_render_the_canonical_visible_goal(
    tmp_path: Path,
) -> None:
    goal_id = "traex-connected-project-fixture"
    project, home = _write_onboarding_goal(
        tmp_path,
        goal_id=goal_id,
        registered_agents=["traex-connected-agent"],
    )
    packet = build_loopx_bootstrap_command_pack(
        project=project,
        goal_id=goal_id,
        agent_id="traex-connected-agent",
        cli_bin=str(REPO_ROOT / "scripts" / "loopx"),
        host_surface="traex-cli",
    )

    commands = packet["commands"]
    activation = packet["host_loop_activation"]
    public_activation_commands = {
        commands["heartbeat_prompt"],
        commands["heartbeat_prompt_json"],
        commands["goal_start_host_loop_activation"],
        activation["activation_input_command"],
        activation["commands"]["heartbeat_prompt"],
        activation["commands"]["heartbeat_prompt_json"],
        activation["commands"]["visible_goal_prompt_json"],
    }
    assert all(
        "--visible-goal-host traex-cli" in command
        for command in public_activation_commands
    )
    message_command = str(commands["heartbeat_prompt"])
    assert message_command in packet["message"]

    completed = subprocess.run(
        shlex.split(message_command),
        cwd=REPO_ROOT,
        env={
            "HOME": str(home),
            "PATH": os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=True,
        text=True,
        capture_output=True,
    )
    task_body = completed.stdout.split("````text\n", 1)[1].split("\n````", 1)[0]
    assert "visible TraeX `/goal` task" in " ".join(task_body.split())
    _assert_traex_visible_goal_body_contract(task_body)


def test_traex_multi_agent_onboarding_choice_executes_visible_goal_command(
    tmp_path: Path,
) -> None:
    goal_id = "traex-multi-agent-fixture"
    project, home = _write_onboarding_goal(
        tmp_path,
        goal_id=goal_id,
        registered_agents=["traex-main", "traex-reviewer"],
    )
    packet = build_agent_onboarding_packet(
        project=project,
        agent_type="traex-cli",
        goal_id=goal_id,
        cli_bin=str(REPO_ROOT / "scripts" / "loopx"),
    )

    choice = next(
        item
        for item in packet["identity_selection_gate"]["choices"]
        if item["agent_id"] == "traex-reviewer"
    )
    assert set(choice) == {
        "agent_id",
        "activation_input_command",
        "mode",
        "requires_explicit_takeover_intent",
    }
    command = choice["activation_input_command"]
    assert "--runtime-profile generic_cli" in command
    assert "--visible-goal-host traex-cli" in command
    assert "--agent-id traex-reviewer" in command
    assert command in render_agent_onboarding_markdown(packet)

    payload = _run_activation_command(command, home=home)
    task_body = str(payload["task_body"])
    assert payload["agent_id"] == "traex-reviewer"
    assert payload["visible_goal_host"] == "traex-cli"
    assert "visible\nTraeX `/goal` task" in task_body
    _assert_traex_visible_goal_body_contract(task_body)


def test_traex_visible_goal_projects_initial_runtime_capabilities_without_user_gate(
    tmp_path: Path,
) -> None:
    goal_id = "traex-capability-fixture"
    _, home = _write_onboarding_goal(
        tmp_path,
        goal_id=goal_id,
        registered_agents=["traex-capability-agent"],
    )
    packet = build_host_loop_activation_packet(
        agent_type="traex-cli",
        goal_id=goal_id,
        agent_id="traex-capability-agent",
        registered_agents=["traex-capability-agent"],
        available_capabilities=["network", "external_evidence_poll"],
        cli_bin=str(REPO_ROOT / "scripts" / "loopx"),
    )

    payload = _run_activation_command(packet["activation_input_command"], home=home)
    task_body = str(payload["task_body"])
    projection = payload["initial_runtime_capability_projection"]
    assert "heartbeat-prequota" in str(payload["pr_review_pre_quota_command"])
    assert projection == {
        "schema_version": "visible_goal_initial_runtime_capability_projection_v0",
        "source": "activation_available_capabilities",
        "scope": "visible_goal_session",
        "capabilities": ["network", "external_evidence_poll"],
        "capability_count": 2,
        "max_capabilities": 8,
        "first_quota_path": "task_body.quota_guard_command",
        "user_gate": False,
        "durable_grant_written": False,
        "dynamic_reentry_schema_version": "runtime_capability_reentry_v0",
    }
    for capability in projection["capabilities"]:
        assert f"--available-capability {capability}" in str(
            payload["quota_guard_command"]
        )
        assert f"--available-capability {capability}" in task_body
    assert "user gate" not in task_body.lower()
    _assert_traex_visible_goal_body_contract(task_body)


def test_traex_visible_goal_rejects_unbounded_initial_runtime_capabilities() -> None:
    with pytest.raises(
        ValueError,
        match="visible Goal initial runtime capabilities exceed the limit of 8",
    ):
        build_heartbeat_prompt(
            goal_id="traex-capability-limit-fixture",
            thin=True,
            visible_goal_host="traex-cli",
            runtime_profile="generic_cli",
            available_capabilities=[
                f"runtime_capability_{index}" for index in range(9)
            ],
        )


@pytest.mark.parametrize(
    ("rule_flag", "rule_field", "rule_value"),
    (
        (
            "--material-rule",
            "material_queue_rule",
            "Apply RRULE every three minutes.",
        ),
        (
            "--permission-rule",
            "permission_rule",
            "Call automation_update before continuing.",
        ),
        (
            "--material-rule",
            "material_queue_rule",
            "Run /loop after reading the queue.",
        ),
        (
            "--permission-rule",
            "permission_rule",
            "Emit first_turn_receipt with current_time_iso.",
        ),
        (
            "--agent-scope",
            "agent_scope",
            "Review scheduler automation; run /loop when idle.",
        ),
    ),
)
def test_traex_visible_goal_rejects_heartbeat_only_policy_injection(
    tmp_path: Path,
    rule_flag: str,
    rule_field: str,
    rule_value: str,
) -> None:
    goal_id = "traex-policy-injection-fixture"
    _, home = _write_onboarding_goal(
        tmp_path,
        goal_id=goal_id,
        registered_agents=["traex-policy-agent"],
    )
    completed = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "loopx"),
            "--format",
            "json",
            "heartbeat-prompt",
            "--thin",
            "--goal-id",
            goal_id,
            "--agent-id",
            "traex-policy-agent",
            "--runtime-profile",
            "generic_cli",
            "--visible-goal-host",
            "traex-cli",
            rule_flag,
            rule_value,
        ],
        cwd=REPO_ROOT,
        env={
            "HOME": str(home),
            "PATH": os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=False,
        text=True,
        capture_output=True,
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["ok"] is False
    assert payload["task_body"] is None
    assert payload["error"] == (
        f"visible Goal {rule_field} contains heartbeat-only control vocabulary"
    )


def test_traex_visible_goal_rejects_heartbeat_only_agent_profile_scope() -> None:
    with pytest.raises(
        ValueError,
        match="visible Goal agent_scope contains heartbeat-only control vocabulary",
    ):
        build_heartbeat_prompt(
            goal_id="traex-profile-scope-fixture",
            thin=True,
            visible_goal_host="traex-cli",
            runtime_profile="generic_cli",
            agent_id="traex-profile-agent",
            registered_agents=["traex-profile-agent"],
            agent_profile={
                "scope_summary": "Maintain scheduler automation and run /loop.",
            },
        )


def test_traex_visible_goal_allows_policy_words_in_goal_identity() -> None:
    prompt = build_heartbeat_prompt(
        goal_id="scheduler-reliability",
        thin=True,
        visible_goal_host="traex-cli",
        runtime_profile="generic_cli",
    )

    assert prompt["ok"] is True
    assert "LoopX goal `scheduler-reliability`" in prompt["task_body"]


def test_traex_visible_goal_keeps_adversarial_capabilities_outside_task_body(
    tmp_path: Path,
) -> None:
    goal_id = "traex-adversarial-capability-fixture"
    _, home = _write_onboarding_goal(
        tmp_path,
        goal_id=goal_id,
        registered_agents=["traex-adversarial-agent"],
    )
    capabilities = [
        "heartbeat_prequota",
        "current-time",
        "automation update",
        "rrule",
        "first_turn_receipt",
        "scheduler_execution_context",
    ]
    packet = build_host_loop_activation_packet(
        agent_type="traex-cli",
        goal_id=goal_id,
        agent_id="traex-adversarial-agent",
        registered_agents=["traex-adversarial-agent"],
        available_capabilities=capabilities,
        cli_bin=str(REPO_ROOT / "scripts" / "loopx"),
    )

    payload = _run_activation_command(packet["activation_input_command"], home=home)
    normalized_capabilities = [
        "heartbeat_prequota",
        "current_time",
        "automation_update",
        "rrule",
        "first_turn_receipt",
        "scheduler_execution_context",
    ]
    assert packet["available_capabilities"] == normalized_capabilities
    for capability in normalized_capabilities:
        assert (
            f"--available-capability {capability}"
            in str(packet["activation_input_command"])
        )
        assert (
            f"--available-capability {capability}"
            in str(payload["quota_guard_command"])
        )
        assert capability not in str(payload["task_body"]).lower()
    assert "--available-capability" not in str(payload["task_body"])
    _assert_traex_visible_goal_body_contract(str(payload["task_body"]))


@pytest.mark.parametrize(
    ("runtime_profile", "scheduler_execution_context"),
    (
        (None, None),
        ("codex_app_heartbeat", None),
        ("claude_code", None),
        ("codex_cli", None),
        (
            "generic_cli",
            {
                "host_surface": "codex_cli",
                "scheduler_owner": "agent_cli_loop",
                "execution_mode": "interactive",
            },
        ),
    ),
)
def test_traex_visible_goal_requires_exact_generic_cli_runtime_profile(
    runtime_profile: str | None,
    scheduler_execution_context: dict[str, str] | None,
) -> None:
    with pytest.raises(
        ValueError,
        match="visible_goal_host='traex-cli' requires runtime_profile='generic_cli'",
    ):
        build_heartbeat_prompt(
            goal_id="traex-runtime-binding-fixture",
            thin=True,
            visible_goal_host="traex-cli",
            runtime_profile=runtime_profile,
            scheduler_execution_context=scheduler_execution_context,
        )


def test_traex_visible_goal_markdown_is_not_a_codex_heartbeat_wrapper() -> None:
    payload = build_heartbeat_prompt(
        goal_id="traex-markdown-fixture",
        thin=True,
        visible_goal_host="traex-cli",
        runtime_profile="generic_cli",
    )

    markdown = render_heartbeat_prompt_markdown(payload)
    assert "# Visible TraeX Goal Prompt" in markdown
    assert "Paste this task body into the visible TraeX `/goal` task." in markdown
    assert "Heartbeat Automation Prompt" not in markdown
    assert "Codex App heartbeat automation" not in markdown


def test_traex_activation_omits_unused_plaintext_visible_goal_command() -> None:
    packet = build_host_loop_activation_packet(
        agent_type="traex-cli",
        goal_id="traex-json-only-fixture",
    )

    assert "visible_goal_prompt_json" in packet["commands"]
    assert "visible_goal_prompt" not in packet["commands"]


def test_generic_cli_prompt_does_not_imply_traex_visible_goal() -> None:
    payload = build_heartbeat_prompt(
        goal_id="generic-cli-fixture",
        thin=True,
        runtime_profile="generic_cli",
    )

    assert payload["interface_budget"]["mode"] == "thin"
    assert "--turn-instance-id" in payload["quota_guard_command"]
    assert "visible TraeX `/goal` task" not in payload["task_body"]


def test_ambiguous_codex_requires_app_ide_or_cli_selection() -> None:
    with pytest.raises(AgentTypeError) as caught:
        normalize_agent_type("codex")

    assert caught.value.suggestions == [
        "codex-app",
        "codex-app-ssh",
        "codex-ide-plugin",
        "codex-cli",
    ]


def test_codex_app_startup_saves_v2_and_loads_the_current_contract(tmp_path: Path) -> None:
    goal_id = "app-bootstrap-fixture"
    project, home = _write_onboarding_goal(
        tmp_path, goal_id=goal_id, registered_agents=["worker-a"])
    packet = build_agent_onboarding_packet(
        project=project, agent_type="codex-app", goal_id=goal_id,
        agent_id="worker-a", cli_bin=str(REPO_ROOT / "scripts" / "loopx"))
    initial = _run_activation_command(packet["host_loop_activation"]["activation_input_command"], home=home)
    assert initial["ok"] and initial["bootstrap"]
    prompt = initial["task_body"]
    assert prompt.startswith("LoopX managed heartbeat bootstrap v2\n每次唤醒先执行：\n")
    command = prompt.split("```sh\n", 1)[1].split("\n```", 1)[0]
    tokens = shlex.split(command)
    assert "--bootstrap" not in tokens and "--codex-app" in tokens
    assert tokens[tokens.index("--agent-id") + 1] == "worker-a"
    loaded = _run_activation_command(command, home=home)
    assert loaded["ok"] and not loaded.get("bootstrap")
    assert "interaction_contract" in loaded["task_body"]
