#!/usr/bin/env python3
"""Smoke-test agent onboarding and host-loop activation routing."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.agent_onboarding import build_agent_onboarding_packet  # noqa: E402
from loopx.bootstrap_command_pack import (  # noqa: E402
    build_loopx_bootstrap_command_pack,
    build_start_goal_guided_packet,
)
from loopx.cli import build_parser  # noqa: E402
from loopx.control_plane.quota.usage_summary import is_automation_run  # noqa: E402
from loopx.control_plane.scheduler.automation_liveness import (  # noqa: E402
    build_automation_liveness,
)
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    scheduler_execution_context_for_runtime_profile,
)
from loopx.host_loop_activation import (  # noqa: E402
    agent_type_for_host_surface,
    build_agent_type_catalog,
    build_host_loop_activation_packet,
)


def run_cli(
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=True,
        env=env,
        timeout=300,
    )


def load_bootstrap(packet: dict, cli_bin: str, home: Path) -> dict:
    assert packet["ok"] and packet["bootstrap"]
    assert packet["interface_budget"]["within_budget"]
    loader = shlex.split(packet["task_body"].split("```sh\n", 1)[1].split("\n```", 1)[0])
    assert "--bootstrap" not in loader
    loader[0] = cli_bin
    result = subprocess.run(
        loader,
        env={**os.environ, "HOME": str(home), "LOOPX_PYTHON": sys.executable},
        check=False,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def main() -> int:
    catalog = build_agent_type_catalog()
    agent_types = {item["agent_type"] for item in catalog["canonical_agent_types"]}
    assert {
        "codex-app",
        "trae_app",
        "codex-app-ssh",
        "codex-ide-plugin",
        "codex-cli",
        "claude-code",
        "opencode",
        "traex-cli",
        "pi",
        "manual",
        "other-agent",
    } <= agent_types
    ambiguous = {item["input"]: item["use_one_of"] for item in catalog["ambiguous_inputs"]}
    assert ambiguous["codex"] == [
        "codex-app",
        "codex-app-ssh",
        "codex-ide-plugin",
        "codex-cli",
    ], ambiguous

    assert agent_type_for_host_surface("chat-box") == "codex-app"
    assert agent_type_for_host_surface("trae_app") == "trae_app"
    assert agent_type_for_host_surface("codex-app-ssh") == "codex-app-ssh"
    assert agent_type_for_host_surface("codex-ide-plugin") == "codex-ide-plugin"
    assert agent_type_for_host_surface("codex-ide") == "codex-ide-plugin"
    assert agent_type_for_host_surface("codex-cli-tui") == "codex-cli"
    assert agent_type_for_host_surface("opencode") == "opencode"
    assert agent_type_for_host_surface("pi") == "pi"
    assert agent_type_for_host_surface("pi-tui") == "pi"
    assert agent_type_for_host_surface("ark-managed-agent") == "ark-managed-agent"
    assert agent_type_for_host_surface("traex-cli") == "traex-cli"
    assert agent_type_for_host_surface("traex") == "traex-cli"
    assert agent_type_for_host_surface("deepseek-harness") == "deepseek-harness"
    assert agent_type_for_host_surface("dsh") == "deepseek-harness"

    codex_app = build_host_loop_activation_packet(agent_type="codex-app", goal_id="demo")
    trae_app = build_host_loop_activation_packet(agent_type="trae_app", goal_id="demo")
    codex_app_ssh = build_host_loop_activation_packet(
        agent_type="codex-app-ssh",
        goal_id="demo",
    )
    codex_ide = build_host_loop_activation_packet(agent_type="codex-ide-plugin", goal_id="demo")
    codex_cli = build_host_loop_activation_packet(agent_type="codex-cli", goal_id="demo")
    claude_code = build_host_loop_activation_packet(agent_type="claude-code", goal_id="demo")
    opencode = build_host_loop_activation_packet(agent_type="opencode", goal_id="demo")
    pi = build_host_loop_activation_packet(agent_type="pi", goal_id="demo")
    ark_managed_agent = build_host_loop_activation_packet(
        agent_type="ark-managed-agent",
        goal_id="demo",
    )
    traex_cli = build_host_loop_activation_packet(agent_type="traex-cli", goal_id="demo")
    dsh = build_host_loop_activation_packet(agent_type="deepseek-harness", goal_id="demo")
    assert codex_app["activation_method"] == "create_or_update_codex_app_automation", codex_app
    assert trae_app["activation_method"] == "create_or_update_trae_app_automation", trae_app
    assert trae_app["host_mutation"]["preferred_tool"] == "automation_update", trae_app
    assert "--trae_app" in trae_app["commands"]["heartbeat_prompt"], trae_app
    trae_scheduler = scheduler_execution_context_for_runtime_profile(
        "trae_app"
    )
    assert trae_scheduler.ok, trae_scheduler
    assert trae_scheduler.projection()["host_surface"] == "trae_app"
    assert trae_scheduler.projection()["scheduler_owner"] == "host_automation"
    settled = build_automation_liveness(
        {
            "effective_action": "heartbeat_settled_skip",
            "heartbeat_recommendation": {},
            "execution_obligation": {"must_attempt_work": False},
        }
    )
    assert settled["keep_active"] is True, settled
    assert settled["next_trigger"] == "next heartbeat turn with a fresh turn identity"
    assert codex_app_ssh["activation_method"] == "set_visible_goal", codex_app_ssh
    assert codex_app_ssh["host_surface"] == "codex_app_ssh_visible_goal_mode", codex_app_ssh
    assert any(
        "native update_goal marks only the host Goal blocked" in criterion
        for criterion in codex_app_ssh["success_criteria"]
    ), codex_app_ssh
    assert "--runtime-profile codex_app_ssh_goal" in (
        codex_app_ssh["commands"]["heartbeat_prompt"]
    ), codex_app_ssh
    app_ssh_scheduler = scheduler_execution_context_for_runtime_profile(
        "codex_app_ssh_goal"
    )
    assert app_ssh_scheduler.ok, app_ssh_scheduler
    assert app_ssh_scheduler.projection()["host_surface"] == "codex_app_ssh"
    assert app_ssh_scheduler.projection()["scheduler_owner"] == "agent_cli_loop"
    assert app_ssh_scheduler.projection()["execution_mode"] == "interactive"
    assert app_ssh_scheduler.projection()["codex_app_applicability"] == "not_applicable"
    assert codex_ide["activation_method"] == "set_visible_goal", codex_ide
    assert codex_ide["host_mutation"]["host_command"] == "/goal <task_body>", codex_ide
    assert codex_cli["host_mutation"]["host_command"] == "/goal <task_body>", codex_cli
    assert claude_code["host_mutation"]["host_command"] == "/loop", claude_code
    assert opencode["activation_method"] == "activate_loopx_opencode_goal_bridge", opencode
    assert opencode["host_mutation"]["host_tool"] == "loopx_goal_activate", opencode
    assert "--runtime-profile generic_cli" in opencode["commands"]["heartbeat_prompt"], opencode
    assert pi["activation_method"] == "activate_loopx_pi_goal_extension", pi
    assert pi["host_surface"] == "pi_visible_goal_mode", pi
    assert pi["host_mutation"]["host_tool"] == "loopx_goal_activate", pi
    assert pi["host_mutation"]["tool_argument_mapping"]["goalId"] == (
        "optional compatibility echo; host authority derives the value"
    ), pi
    assert "--runtime-profile generic_cli" in pi["commands"]["heartbeat_prompt"], pi
    assert ark_managed_agent["activation_method"] == "submit_goal_once", ark_managed_agent
    assert ark_managed_agent["host_surface"] == "ark_managed_agent_goal_mode", ark_managed_agent
    assert traex_cli["activation_method"] == "set_visible_goal", traex_cli
    assert traex_cli["host_surface"] == "traex_visible_goal_mode", traex_cli
    assert traex_cli["host_mutation"]["host_command"] == "/goal <task_body>", traex_cli
    assert traex_cli["host_mutation"]["requires_host_feature_flag"] == (
        "[features] goals = true in ~/.trae/traecli.toml"
    ), traex_cli
    assert "--runtime-profile generic_cli" in traex_cli["commands"]["heartbeat_prompt"], traex_cli
    assert all(
        "--visible-goal-host traex-cli" in traex_cli["commands"][key]
        for key in (
            "heartbeat_prompt",
            "heartbeat_prompt_json",
            "visible_goal_prompt_json",
        )
    ), traex_cli
    assert traex_cli["commands"]["heartbeat_prompt_json"] == (
        traex_cli["commands"]["visible_goal_prompt_json"]
    ), traex_cli
    assert traex_cli["activation_input_command"] == (
        traex_cli["commands"]["visible_goal_prompt_json"]
    ), traex_cli
    assert "automation_update" not in str(traex_cli), traex_cli
    assert dsh["activation_method"] == "external_loop_driver", dsh
    assert dsh["host_surface"] == "deepseek_harness_automation_loop", dsh
    assert "--runtime-profile generic_cli" in dsh["commands"]["heartbeat_prompt"], dsh
    assert "scripts/dsh_turn_host_adapter.py" in dsh["entry_command_hint"], dsh
    assert re.search(
        r"(?<![a-z0-9_/])/loop(?![a-z0-9_-])",
        str(traex_cli).lower(),
    ) is None, traex_cli
    gated_activation = build_host_loop_activation_packet(
        agent_type="codex-app",
        goal_id="multi-agent-demo",
        registered_agents=["codex-main-control", "codex-product-capability"],
    )
    assert gated_activation["activation_state"] == "selection_required", gated_activation
    assert gated_activation["activation_allowed"] is False, gated_activation
    assert gated_activation["activation_input_command"] is None, gated_activation
    assert len(gated_activation["identity_selection_gate"]["choices"]) == 2, gated_activation
    peer_activation = build_host_loop_activation_packet(
        agent_type="codex-app",
        goal_id="peer-agent-demo",
        registered_agents=["codex-alpha", "codex-beta"],
    )
    assert peer_activation["agent_model"] == "peer_v1", peer_activation
    assert "primary_agent" not in peer_activation["identity_contract"], peer_activation
    assert all(
        "role" not in choice
        for choice in peer_activation["identity_selection_gate"]["choices"]
    ), peer_activation
    single_agent_activation = build_host_loop_activation_packet(
        agent_type="codex-app",
        goal_id="single-agent-demo",
        registered_agents=["codex-main-control"],
    )
    assert single_agent_activation["activation_state"] == "single_registered_agent_selected"
    assert single_agent_activation["agent_id"] == "codex-main-control"
    assert single_agent_activation["activation_allowed"] is True
    assert "--agent-id codex-main-control" in single_agent_activation["activation_input_command"]

    list_result = run_cli("agent-onboard", "--list-agent-types")
    list_payload = json.loads(list_result.stdout)
    assert list_payload["schema_version"] == "loopx_agent_type_catalog_v0", list_payload

    ambiguous_result = run_cli(
        "agent-onboard",
        "--agent-type",
        "codex",
        "--project",
        ".",
        check=False,
    )
    assert ambiguous_result.returncode == 2, ambiguous_result.stdout
    ambiguous_payload = json.loads(ambiguous_result.stdout)
    assert ambiguous_payload["ok"] is False, ambiguous_payload
    assert ambiguous_payload["suggestions"] == [
        "codex-app",
        "codex-app-ssh",
        "codex-ide-plugin",
        "codex-cli",
    ], ambiguous_payload

    with tempfile.TemporaryDirectory(prefix="loopx-agent-onboard-smoke-") as tmp:
        project = Path(tmp) / "project"
        project.mkdir()
        payload = build_loopx_bootstrap_command_pack(
            project=project,
            goal_id="demo-goal",
            agent_id="codex-value-explorer",
            cli_bin="loopx",
            host_surface="codex-cli-tui",
            goal_text="build a deterministic onboarding path",
        )
    assert payload["agent_type"] == "codex-cli", payload
    activation = payload["host_loop_activation"]
    assert activation["host_surface"] == "codex_cli_visible_goal_mode", activation
    assert activation["activation_allowed"] is False, activation
    assert activation["activation_state"] == "invalid_selection", activation
    fresh_registration = activation["identity_selection_gate"][
        "fresh_agent_registration"
    ]
    assert fresh_registration["agent_id"] == "codex-value-explorer", fresh_registration
    assert fresh_registration["preview_command"].endswith(
        "--agent-id codex-value-explorer --require-new"
    ), fresh_registration
    assert fresh_registration["execute_command"].endswith("--execute"), fresh_registration
    contract = payload["goal_start_contract"]
    assert contract["activation"]["host_loop_required_after_todo_writeback"] is True, contract
    assert payload["safety_contract"]["explicit_goal_start_must_activate_host_loop"] is True, payload
    message = payload["message"]
    assert "register-agent" in message, message
    assert "explicit takeover" in message, message

    with tempfile.TemporaryDirectory(prefix="loopx-agent-onboard-identity-") as tmp:
        root = Path(tmp)
        project = root / "project"
        home = root / "home"
        state_file = project / ".codex" / "goals" / "multi-agent-goal" / "ACTIVE_GOAL_STATE.md"
        project_registry = project / ".loopx" / "registry.json"
        global_registry = home / ".codex" / "loopx" / "registry.global.json"
        state_file.parent.mkdir(parents=True)
        project_registry.parent.mkdir(parents=True)
        global_registry.parent.mkdir(parents=True)
        state_file.write_text("# Active State\n", encoding="utf-8")
        registry = {
            "goals": [
                {
                    "id": "multi-agent-goal",
                    "domain": "smoke",
                    "status": "active",
                    "repo": str(project),
                    "state_file": ".codex/goals/multi-agent-goal/ACTIVE_GOAL_STATE.md",
                    "adapter": {"kind": "generic_project_goal_v0", "status": "connected"},
                    "coordination": {
                        "agent_model": "peer_v1",
                        "registered_agents": [
                            "codex-main-control",
                            "codex-product-capability",
                        ],
                    },
                }
            ]
        }
        serialized_registry = json.dumps(registry)
        project_registry.write_text(serialized_registry, encoding="utf-8")
        global_registry.write_text(serialized_registry, encoding="utf-8")
        cli_bin = str(REPO_ROOT / "scripts" / "loopx")

        onboarding_gate = build_agent_onboarding_packet(
            project=project,
            agent_type="codex-app",
            goal_id="multi-agent-goal",
            cli_bin=cli_bin,
        )
        gate = onboarding_gate["identity_selection_gate"]
        assert onboarding_gate["host_loop_activation"]["activation_allowed"] is False
        assert onboarding_gate["commands"]["quota_guard"] is None
        assert onboarding_gate["host_loop_activation"]["activation_input_command"] is None
        assert len(gate["choices"]) == 2, onboarding_gate
        selected_choice = next(
            choice
            for choice in gate["choices"]
            if choice["agent_id"] == "codex-product-capability"
        )
        assert "--agent-id codex-product-capability" in (
            selected_choice["activation_input_command"]
        )
        assert "--agent-scope" in selected_choice["activation_input_command"]
        assert selected_choice["heartbeat_prompt_json"] == (
            selected_choice["activation_input_command"]
        )
        assert selected_choice["heartbeat_prompt"]

        choice_run = subprocess.run(
            shlex.split(selected_choice["activation_input_command"]),
            cwd=REPO_ROOT,
            env={**os.environ, "HOME": str(home), "LOOPX_PYTHON": sys.executable},
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        choice_payload = json.loads(choice_run.stdout)
        assert choice_payload["ok"] is True, choice_payload
        assert choice_payload["agent_id"] == "codex-product-capability", choice_payload
        assert choice_payload["task_body"], choice_payload

        command_pack_gate = build_loopx_bootstrap_command_pack(
            project=project,
            goal_id="multi-agent-goal",
            agent_id=None,
            cli_bin=cli_bin,
            host_surface="codex-app",
            goal_text="fix a public issue",
        )
        assert command_pack_gate["recommended_next_step"]["kind"] == "select_agent_identity"
        assert command_pack_gate["commands"]["heartbeat_prompt_json"] is None
        assert command_pack_gate["commands"]["goal_start_quota_should_run"] is None
        assert "No unscoped heartbeat or quota command" in command_pack_gate["message"]

        guided_gate = build_start_goal_guided_packet(
            project=project,
            goal_id="multi-agent-goal",
            agent_id=None,
            cli_bin=cli_bin,
            host_surface="codex-app",
            goal_text="fix a public issue",
        )
        transaction = guided_gate["guided_transaction"]
        assert transaction["blocked_by"] == "agent_identity_selection", transaction
        assert transaction["ordered_steps"][2]["id"] == "select_agent_identity", transaction

        selected_pack = build_loopx_bootstrap_command_pack(
            project=project,
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
            host_surface="codex-app",
            goal_text="fix a public issue",
            available_capabilities=["network", "external_evidence_poll"],
        )
        assert selected_pack["host_loop_activation"]["activation_allowed"] is True
        refresh_command = selected_pack["commands"]["goal_start_refresh_state"]
        assert "--agent-id codex-product-capability" in refresh_command, refresh_command
        assert "--progress-scope agent_lane" in refresh_command, refresh_command
        assert "--health-check" not in refresh_command, refresh_command
        refresh_args = build_parser().parse_args(shlex.split(refresh_command)[1:])
        assert refresh_args.command == "refresh-state", refresh_args
        assert refresh_args.agent_id == "codex-product-capability", refresh_args
        assert refresh_args.progress_scope == "agent_lane", refresh_args
        for key in (
            "heartbeat_prompt_json",
            "quota_guard",
            "goal_start_agent_onboard_recheck",
            "goal_start_quota_should_run",
        ):
            assert "--agent-id codex-product-capability" in selected_pack["commands"][key], (
                key,
                selected_pack["commands"],
            )
            assert "--available-capability network" in selected_pack["commands"][key], (
                key,
                selected_pack["commands"],
            )
            assert "--available-capability external_evidence_poll" in selected_pack["commands"][key], (
                key,
                selected_pack["commands"],
            )

        ide_onboarding = build_agent_onboarding_packet(
            project=project,
            agent_type="codex-ide-plugin",
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
        )
        ide_bootstrap = ide_onboarding["commands"]["bootstrap_command_pack"]
        assert "--host-surface codex-ide-plugin" in ide_bootstrap, ide_onboarding
        assert "worker-bridge" not in ide_bootstrap, ide_onboarding
        assert "visible IDE plugin" in ide_onboarding["recommended_start"], ide_onboarding

        app_ssh_onboarding = build_agent_onboarding_packet(
            project=project,
            agent_type="codex-app-ssh",
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
        )
        app_ssh_activation = app_ssh_onboarding["host_loop_activation"]
        assert app_ssh_activation["activation_method"] == "set_visible_goal"
        assert app_ssh_activation["host_surface"] == "codex_app_ssh_visible_goal_mode"
        assert "--host-surface codex-app-ssh" in (
            app_ssh_onboarding["commands"]["bootstrap_command_pack"]
        )
        assert "/goal <task_body>" in app_ssh_activation["host_mutation"]["host_command"]

        app_ssh_prompt_run = subprocess.run(
            shlex.split(app_ssh_activation["activation_input_command"]),
            cwd=REPO_ROOT,
            env={**os.environ, "HOME": str(home), "LOOPX_PYTHON": sys.executable},
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        app_ssh_prompt = json.loads(app_ssh_prompt_run.stdout)
        assert app_ssh_prompt["ok"] is True, app_ssh_prompt
        app_ssh_prompt = load_bootstrap(app_ssh_prompt, cli_bin, home)
        assert app_ssh_prompt["interface_budget"]["mode"] == "visible_goal", app_ssh_prompt
        assert app_ssh_prompt["interface_budget"]["max_chars"] == 4_000, app_ssh_prompt
        assert app_ssh_prompt["interface_budget"]["within_budget"] is True, app_ssh_prompt
        assert "--runtime-profile codex_app_ssh_goal" in (
            app_ssh_prompt["quota_guard_command"]
        ), app_ssh_prompt
        assert "--begin-turn" in app_ssh_prompt["quota_guard_command"], app_ssh_prompt
        assert "--turn-instance-id" not in app_ssh_prompt["quota_guard_command"], app_ssh_prompt
        assert "--source visible-goal" in app_ssh_prompt["quota_spend_command"], app_ssh_prompt
        spend_args = build_parser().parse_args(
            shlex.split(app_ssh_prompt["quota_spend_command"])[1:]
        )
        assert spend_args.command == "quota", spend_args
        assert spend_args.quota_command == "spend-slot", spend_args
        assert spend_args.source == "visible-goal", spend_args
        assert is_automation_run(
            {
                "classification": "quota_slot_spent",
                "quota_event": {"source": spend_args.source},
            }
        ) is False
        assert "host_action=" not in app_ssh_prompt["task_body"], app_ssh_prompt
        assert "automation_update stop" not in app_ssh_prompt["task_body"], app_ssh_prompt
        assert "call `update_goal` with `status=blocked`" in (
            app_ssh_prompt["task_body"]
        ), app_ssh_prompt
        assert "Only user `/goal resume`" in (
            app_ssh_prompt["task_body"]
        ), app_ssh_prompt
        assert "reactivates it; rerun quota after resume" in (
            app_ssh_prompt["task_body"]
        ), app_ssh_prompt
        app_ssh_quota_argv = shlex.split(app_ssh_prompt["quota_guard_command"])
        app_ssh_quota_argv[0] = cli_bin
        app_ssh_quota_argv = [
            str(home) + arg[len("$HOME") :] if arg.startswith("$HOME/") else arg
            for arg in app_ssh_quota_argv
        ]
        app_ssh_quota_argv.extend(["--scan-root", str(project)])
        app_ssh_quota_run = subprocess.run(
            app_ssh_quota_argv,
            cwd=project,
            env={**os.environ, "HOME": str(home), "LOOPX_PYTHON": sys.executable},
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        app_ssh_quota = json.loads(app_ssh_quota_run.stdout)
        execution_context = app_ssh_quota["scheduler_hint"]["execution_context"]
        assert execution_context["source"] == (
            "runtime_profile:codex_app_ssh_goal"
        ), app_ssh_quota
        assert "codex_app_ssh_goal" not in (
            app_ssh_quota["scheduler_hint"]["unchanged_poll"]["limits"]
        ), app_ssh_quota

        cli_onboarding = build_agent_onboarding_packet(
            project=project,
            agent_type="codex-cli",
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
        )
        cli_prompt_run = subprocess.run(
            shlex.split(
                cli_onboarding["host_loop_activation"]["activation_input_command"]
            ),
            cwd=REPO_ROOT,
            env={**os.environ, "HOME": str(home), "LOOPX_PYTHON": sys.executable},
            check=True,
            text=True,
            capture_output=True,
            timeout=120,
        )
        cli_prompt = json.loads(cli_prompt_run.stdout)
        cli_prompt = load_bootstrap(cli_prompt, cli_bin, home)
        assert cli_prompt["interface_budget"]["mode"] == "visible_goal", cli_prompt
        assert "--turn-instance-id" not in cli_prompt["quota_guard_command"], cli_prompt
        assert "--source visible-goal" in cli_prompt["quota_spend_command"], cli_prompt

        opencode_onboarding = build_agent_onboarding_packet(
            project=project,
            agent_type="opencode",
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
        )
        assert "--host-surface opencode" in opencode_onboarding["commands"]["bootstrap_command_pack"]
        assert opencode_onboarding["commands"]["install_command_facade"].endswith(
            "--surface opencode --with-goal-bridge"
        )
        assert opencode_onboarding["host_loop_activation"]["host_mutation"]["host_tool"] == (
            "loopx_goal_activate"
        )

        pi_onboarding = build_agent_onboarding_packet(
            project=project,
            agent_type="pi",
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
        )
        pi_facade = pi_onboarding["commands"]["install_command_facade"]
        assert "--surface pi" in pi_facade, pi_facade
        # The Pi install command must use the CLI's public --pi-project flag
        # with the resolved project, so it parses and targets the right
        # project even when agent-onboard runs from another cwd.
        assert f"--pi-project {shlex.quote(str(project.resolve()))}" in pi_facade, pi_facade
        assert "--project ." not in pi_facade, pi_facade
        # Execute the returned setup command from a different cwd (run_cli
        # always runs from REPO_ROOT, not the temp project): it must parse and
        # land in the project's .pi/extensions/.
        pi_install = json.loads(
            run_cli(*shlex.split(pi_facade)[1:]).stdout
        )
        assert pi_install["ok"] is True, pi_install
        assert (project / ".pi" / "extensions" / "loopx-goal.ts").is_file()
        pi_runtime = project / ".pi" / "extensions" / "pi-goal-loop-runtime.mjs"
        assert pi_runtime.is_file()
        # The quota/wait/store loop core lives in the runtime module; the
        # adapter only wires Pi events into it.
        assert "pi.on(\"session_shutdown\"" not in pi_runtime.read_text(encoding="utf-8")
        assert "terminal_no_followup" in pi_runtime.read_text(encoding="utf-8")
        assert pi_onboarding["host_loop_activation"]["host_mutation"]["host_tool"] == (
            "loopx_goal_activate"
        )

        other_agent_onboarding = build_agent_onboarding_packet(
            project=project,
            agent_type="other-agent",
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
        )
        other_commands = other_agent_onboarding["commands"]
        assert "install_command_facade" not in other_commands
        assert "doctor --agent-type other-agent" in other_commands["doctor_or_install"]
        assert "--host-surface other-agent" in other_commands["bootstrap_command_pack"]
        assert "worker-bridge" not in other_commands["bootstrap_command_pack"]
        delivery = other_agent_onboarding["skill_delivery"]
        assert delivery["mode"] == "host_managed", delivery
        assert delivery["owner"] == "custom_agent_host", delivery
        assert delivery["status"] == "pending_host_readback", delivery
        assert delivery["codex_skills_root_required"] is False, delivery
        assert delivery["required_for_cli_health"] is False, delivery
        assert delivery["required_for_loopx_workflow"] is True, delivery
        assert set(delivery["required_skill_ids"]) == {
            "loopx",
            "loopx-project",
            "loopx-pr-program",
            "loopx-pr-review",
            "loopx-doc-registry",
            "loopx-benchmark",
            "loopx-self-repair",
            "loopx-performance-diagnosis",
        }, delivery
        assert delivery["delivery_options"] == [
            "host_skill_manifest",
            "prompt_injection",
        ], delivery
        assert delivery["source_directories"] == [
            f"skills/{skill_id}"
            for skill_id in delivery["required_skill_ids"]
            if skill_id != "loopx"
        ], delivery
        assert delivery["generated_skill_ids"] == ["loopx"], delivery
        assert delivery["host_readback_required"] is True, delivery
        assert set(delivery["readback_fields"]) == {
            "integration_mode",
            "loaded_skill_ids",
            "source_revision",
        }, delivery
        assert any(
            "loaded-skill readback" in item
            for item in other_agent_onboarding["slash_command_contract"][
                "setup_complete_requires"
            ]
        ), other_agent_onboarding

        registry["goals"][0]["control_plane"] = {
            "change_quality_qualification": {
                "enabled": True,
                "safe_fix": True,
                "strict_receipt": True,
            }
        }
        serialized_registry = json.dumps(registry)
        project_registry.write_text(serialized_registry, encoding="utf-8")
        global_registry.write_text(serialized_registry, encoding="utf-8")
        quality_other_agent = build_agent_onboarding_packet(
            project=project,
            agent_type="other-agent",
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
        )
        quality_delivery = quality_other_agent["skill_delivery"]
        assert "loopx-change-quality" in quality_delivery["active_project_skill_ids"]
        assert "loopx-change-quality" in quality_delivery["required_skill_ids"]
        assert "skills/loopx-change-quality" in quality_delivery["source_directories"]

        quality_codex = build_agent_onboarding_packet(
            project=project,
            agent_type="codex-cli",
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
        )
        quality_commands = quality_codex["skill_delivery"][
            "project_skill_commands"
        ]
        assert len(quality_commands) == 1, quality_codex
        assert "project-skill status" in quality_commands[0]["status"]
        assert "project-skill install" in quality_commands[0]["apply_install"]
        assert quality_commands[0]["apply_install"].endswith("--execute")

        quality_pi = build_agent_onboarding_packet(
            project=project,
            agent_type="pi",
            goal_id="multi-agent-goal",
            agent_id="codex-product-capability",
            cli_bin=cli_bin,
        )
        pi_quality_commands = quality_pi["skill_delivery"]["project_skill_commands"]
        assert len(pi_quality_commands) == 1, quality_pi
        assert pi_quality_commands[0]["surface"] == "pi", quality_pi

        doctor_home = root / "doctor-home"
        doctor_home.mkdir()
        doctor_env = {
            **os.environ,
            "HOME": str(doctor_home),
            "PATH": f"{REPO_ROOT / 'scripts'}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        other_agent_doctor = json.loads(
            run_cli(
                "doctor",
                "--agent-type",
                "other-agent",
                env=doctor_env,
            ).stdout
        )
        assert other_agent_doctor["install_freshness"]["requires_upgrade"] is False
        assert other_agent_doctor["skill_delivery"]["mode"] == "host_managed"
        for check in other_agent_doctor["checks"]:
            if str(check.get("id", "")).startswith("installed_"):
                assert check["ok"] is True, check
                assert check["applicable"] is False, check

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
