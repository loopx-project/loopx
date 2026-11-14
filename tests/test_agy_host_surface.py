"""End-to-end host contract for the Antigravity CLI (agy) surface.

Installing discoverable files is not the same as being a usable LoopX host. The
generated `/loopx` facade tells the agent to run `start-goal ... --host-surface
<exact-current-host>`, so these tests execute that path for real: if agy is
missing from the CLI choices, the selection gate or the activation dispatch,
the facade dead-ends at argparse and the surface is decorative.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
from host_surface_cli_probes import (
    connected_project,
    onboarding_setup_command_installs,
    run_cli,
    selection_gate_offers_surface,
    start_goal_accepts_surface,
)

from loopx.agent_onboarding import _start_instruction, _surface_install_command
from loopx.host_loop_activation import (
    _heartbeat_commands,
    build_agent_type_catalog,
    build_host_loop_activation_packet,
    normalize_agent_type,
    scheduler_command_binding_for_agent_type,
)
from loopx.slash_command_install import install_slash_commands
from loopx.workflow_skill_install import workflow_skill_install
from loopx.doctor import collect_doctor
from loopx.control_plane.scheduler.execution_context import (
    ExecutionMode,
    HostSurface,
    SchedulerOwner,
    SchedulerRuntimeProfile,
    resolve_scheduler_execution_context,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.agy_goal_mode import (
    AGY_GOAL_CANCELLED_TOKEN,
    AGY_GOAL_COMMAND,
    AGY_GOAL_COMMAND_DESCRIPTION,
    AGY_GOAL_COMPLETE_TOKEN,
    AGY_NATIVE_WAKE_FACTS,
    AGY_NATIVE_WAKE_TOOLS,
    ANTIGRAVITY_APP_AGENT_TYPE,
    agy_home,
)

HOST_SURFACE = "agy"


@pytest.mark.parametrize(
    "entry", [
        ("bootstrap-command-pack", "--host-surface", "antigravity-app"),
        ("agent-onboard", "--agent-type", "antigravity-app"),
    ],
)
def test_antigravity_generated_commands_read_the_project_registry_from_another_directory(
    tmp_path: Path, entry: tuple[str, ...],
) -> None:
    project = connected_project(tmp_path / "project with spaces")
    registry = project / ".loopx" / "registry.json"
    runtime = tmp_path / "isolated-runtime"
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\nUse the registered project state.\n", encoding="utf-8")
    data = json.loads(registry.read_text())
    data["common_runtime_root"] = str(runtime)
    data["goals"][0].update(
        state_file=state.name,
        coordination={"registered_agents": ["probe-agent"], "agent_model": "peer_v1"},
    )
    registry.write_text(json.dumps(data), encoding="utf-8")
    foreign = tmp_path / "other"
    foreign.mkdir()
    # The host may be in another project which happens to use the same Goal id.
    other = connected_project(foreign)
    foreign_registry = other / ".loopx" / "registry.json"
    foreign_registry.write_text(json.dumps({
        "goals": [{**data["goals"][0], "repo": str(other)}],
    }), encoding="utf-8")
    env = {**os.environ, "LOOPX_RUNTIME_ROOT": str(runtime), "LOOPX_USAGE_PING": "0"}
    env.pop("LOOPX_REGISTRY", None)
    result = run_cli(
        *entry, "--project", str(project), "--goal-id", "surface-goal",
        "--agent-id", "probe-agent", cwd=other, env=env,
    )
    assert result.returncode == 0, result.stderr
    activation = json.loads(result.stdout)["host_loop_activation"]
    assert activation["activation_allowed"]
    mutation = activation["host_mutation"]
    sidecar = mutation["sidecar_template"]
    prompt = sidecar["sidecar_json"]["args"][-1]
    bootstrap, = [line for line in prompt.splitlines() if " heartbeat-prompt " in line]
    for command in (bootstrap, activation["activation_input_command"]):
        argv = shlex.split(command)
        result = run_cli(*argv[1:], cwd=other, env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"]
        guard = next(line for line in payload["task_body"].splitlines() if " quota should-run " in line)
        guard_argv = shlex.split(guard)
        assert Path(guard_argv[guard_argv.index("--registry") + 1]) == registry
    hook = shlex.split(mutation["hooks_template"]["loopx"]["Stop"][0]["command"])
    assert hook[hook.index("--registry") + 1] == str(registry)


@pytest.mark.skipif(os.name != "posix", reason="Executes real Antigravity POSIX launchers")
@pytest.mark.parametrize(
    "entry",
    [
        ("bootstrap-command-pack", "--host-surface", "antigravity-app"),
        ("agent-onboard", "--agent-type", "antigravity-app"),
        ("start-goal", "--guided", "--host-surface", "antigravity-app",
         "--goal-text", "Wake using the selected LoopX installation.", "--include-command-pack-detail"),
    ],
)
@pytest.mark.parametrize("global_option", [True, False], ids=["global", "local"])
def test_antigravity_activation_keeps_selected_cli_through_regeneration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: tuple[str, ...], global_option: bool,
) -> None:
    project = connected_project(tmp_path / "selected")
    registry = project / ".loopx" / "registry.json"
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "# Goal\n\n## Objective\n\nUse the selected command route.\n\n"
        "## Agent Todo\n\n- [ ] Verify the selected command.\n"
        "  <!-- loopx:todo todo_id=todo_launch_route status=open claimed_by=probe-agent "
        "task_class=advancement_task action_kind=validate -->\n",
        encoding="utf-8",
    )
    data = json.loads(registry.read_text())
    data["goals"][0].update(
        state_file=state.name,
        coordination={"registered_agents": ["probe-agent"], "agent_model": "peer_v1"},
    )
    registry.write_text(json.dumps(data), encoding="utf-8")
    foreign = connected_project(tmp_path / "foreign")
    runtime = tmp_path / "selected runtime"
    if entry[0] == "start-goal":
        from tests.control_plane.canonical_authority_fixture import (
            initialize_canonical_source_fixture,
            isolate_sqlite_runtime,
        )

        isolate_sqlite_runtime(tmp_path, monkeypatch)
        initialize_canonical_source_fixture(runtime, "surface-goal", state_path=state)
    fallback = tmp_path / "fallback-bin"
    fallback.mkdir()
    wrong = fallback / "loopx"
    wrong.write_text("#!/bin/sh\nexit 73\n", encoding="utf-8")
    wrong.chmod(0o700)
    pinned = tmp_path / "selected loopx"
    pinned.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m loopx.cli "$@"\n',
        encoding="utf-8",
    )
    pinned.chmod(0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LOOPX_", "CODEX_"))}
    env.update(
        PATH=str(fallback) + os.pathsep + env.get("PATH", os.defpath),
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        LOOPX_USAGE_PING="0",
    )
    def launch(command):
        argv = shlex.split(command)
        # Read the command pack as JSON without supplying any routing options.
        if "--format" not in argv:
            argv[1:1] = ["--format", "json"]
        result = subprocess.run(argv, env=env, cwd=foreign, text=True,
                                capture_output=True, timeout=30)
        assert result.returncode == 0, (argv, result.returncode, result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        return payload

    def assert_activation(payload):
        activation = payload["host_loop_activation"]
        assert activation["activation_allowed"]
        mutation = activation["host_mutation"]
        template = mutation["sidecar_template"]
        sidecar = template["sidecar"]["loopx-heartbeat"]
        assert template["sidecar_json"]["agent_permissions"]["access_grants"] == [f"command({pinned})"]
        for command in (sidecar["prompt_command"], activation["activation_input_command"]):
            heartbeat = launch(command)
            guard = next(line for line in heartbeat["task_body"].splitlines() if " quota should-run " in line)
            argv = shlex.split(guard)
            assert argv[:3] == [str(pinned), "--cli-bin", str(pinned)]
            assert argv[argv.index("--runtime-root") + 1] == str(runtime)
            assert argv[argv.index("--registry") + 1] == str(registry)
        gate = shlex.split(sidecar["gate_command"])
        assert gate[:3] == [str(pinned), "--cli-bin", str(pinned)]
        assert "--scheduler-observation" in gate
        hook = shlex.split(mutation["hooks_template"]["loopx"]["Stop"][0]["command"])
        assert hook[hook.index("--cli-bin") + 1] == str(pinned)

    override = ["--cli-bin", str(pinned)]
    argv = [*override, *entry] if global_option else [*entry, *override]
    result = run_cli("--runtime-root", str(runtime), *argv, "--project", str(project),
                     "--goal-id", "surface-goal", "--agent-id", "probe-agent", cwd=foreign, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    initial = json.loads(result.stdout)
    if entry[0] == "start-goal":
        initial = initial["command_pack"]
    assert_activation(initial)
    if entry[0] == "agent-onboard":
        for key in ("bootstrap_command_pack", "agent_onboard_recheck"):
            assert_activation(launch(initial["commands"][key]))


def test_start_goal_accepts_the_agy_host_surface(tmp_path: Path) -> None:
    payload = start_goal_accepts_surface(HOST_SURFACE, tmp_path)
    activation = payload["command_pack"]["host_loop_activation"]
    assert activation["host_surface"] == "agy_agent_loop"


def test_host_selection_gate_offers_agy_and_its_rerun_command_works(
    tmp_path: Path,
) -> None:
    selection_gate_offers_surface(HOST_SURFACE, tmp_path)


def test_agent_onboarding_setup_command_installs_the_agy_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOOPX_SKILLS_DIR", raising=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
    }
    if "PYTHONPATH" in os.environ:  # keep hermetic when run from a worktree
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]

    # agy reads skills from its fixed ~/.gemini/antigravity-cli/skills root,
    # flat layout (loopx.md), not a per-skill directory.
    onboarding_setup_command_installs(
        HOST_SURFACE,
        outside,
        env,
        expected_skill=(
            tmp_path / "home" / ".gemini" / "antigravity-cli" / "skills" / "loopx.md"
        ),
    )


def test_agy_home_is_the_documented_fixed_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The official CLI documents no home override, so LoopX exposes none:
    # only HOME itself (tests stay hermetic) and the internal injection
    # parameter move the root.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert agy_home() == tmp_path / "home" / ".gemini" / "antigravity-cli"
    assert agy_home(str(tmp_path / "explicit")) == tmp_path / "explicit"


def test_installer_preserves_user_owned_agy_skill(tmp_path: Path) -> None:
    """The skills root is shared with the user's own skills; an unmarked file
    must never be overwritten, and a rerun over a managed file is a no-op."""
    skills_dir = tmp_path / "agy-home" / "skills"
    skill_path = skills_dir / "loopx.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("user-owned skill body\n", encoding="utf-8")

    payload = install_slash_commands(
        execute=True,
        surfaces=["agy"],
        agy_home=str(tmp_path / "agy-home"),
    )
    statuses = {
        (item["surface"], item["command"]): item["status"] for item in payload["installed"]
    }
    assert statuses[("agy", "/loopx")] == "skipped_user_file"
    assert skill_path.read_text(encoding="utf-8") == "user-owned skill body\n"

    skill_path.write_text(
        "<!-- loopx-managed-slash-command:v1 command=/loopx surface=claude-skills -->\nold\n",
        encoding="utf-8",
    )
    payload = install_slash_commands(
        execute=True,
        surfaces=["agy"],
        agy_home=str(tmp_path / "agy-home"),
    )
    statuses = {
        (item["surface"], item["command"]): item["status"] for item in payload["installed"]
    }
    assert statuses[("agy", "/loopx")] == "updated"
    assert "user-owned skill body" not in skill_path.read_text(encoding="utf-8")

    retire = install_slash_commands(
        execute=True,
        uninstall=True,
        surfaces=["agy"],
        agy_home=str(tmp_path / "agy-home"),
    )
    assert not skill_path.exists()
    assert retire["ok"] is True


def test_agent_type_catalog_and_scheduler_binding() -> None:
    """A host with no scheduler binding falls through to the generic default and
    the loop it actually runs stops being visible to the control plane."""
    catalog = build_agent_type_catalog()
    entry = next(
        item
        for item in catalog["canonical_agent_types"]
        if item["agent_type"] == HOST_SURFACE
    )
    assert entry["display_name"] == "Antigravity CLI"
    assert entry["host_loop"]
    assert "gated" not in entry["host_loop"].lower()
    assert "advisory" in entry["host_loop"].lower()
    # The bare product name is what a user types.
    assert HOST_SURFACE in entry["accepted_inputs"]
    assert normalize_agent_type("agy") == HOST_SURFACE
    assert normalize_agent_type("antigravity") == HOST_SURFACE
    assert normalize_agent_type("antigravity-cli") == HOST_SURFACE
    assert scheduler_command_binding_for_agent_type(HOST_SURFACE) == {
        "runtime_profile": "generic_cli"
    }


def test_activation_binds_native_goal_and_wake_with_advisory_quota_entry() -> None:
    """Antigravity CLI owns both a native goal primitive (the `/goal` command
    with host-side forced continuation until `<!-- GOAL_COMPLETE -->`,
    live-verified in the TUI and headless `-p`) and a native in-session
    scheduler (the `schedule` tool plus background-task/subagent wakes).
    The packet has to state exactly that capability envelope: bind the
    objective via `/goal`, arm self-wakes with `schedule`, state advisory
    quota pacing without claiming host continuation interception, and admit
    the loop dies with the session — an overstated capability here is what
    makes an agent claim autonomous setup it cannot deliver."""
    packet = build_host_loop_activation_packet(
        agent_type=HOST_SURFACE,
        goal_id="surface-goal",
        agent_id="probe-agent",
        registered_agents=["probe-agent"],
    )
    assert (
        packet["activation_method"] == "bind_native_goal_with_advisory_quota_entry"
    )
    assert packet["host_mutation"]["cli_can_mutate_directly"] is False
    # Both native primitives are real and must be named, not denied.
    assert (
        packet["host_mutation"]["host_loop_primitive"] == "agy-/goal-and-schedule-tool"
    )
    assert (
        packet["host_mutation"]["loop_driver"]
        == "agy_native_goal_loop_with_schedule_wakes"
    )
    # The quota claim must be honest: advisory guidance, no host hook.
    assert (
        packet["host_mutation"]["quota_gate_enforcement"] == "advisory_only"
    )
    assert packet["host_mutation"]["native_goal_command"] == "/goal"
    assert packet["host_mutation"]["goal_complete_token"] == AGY_GOAL_COMPLETE_TOKEN
    assert packet["host_mutation"]["goal_cancelled_token"] == AGY_GOAL_CANCELLED_TOKEN
    assert "schedule" in packet["host_mutation"]["native_wake_tools"]
    assert "manage_task" in packet["host_mutation"]["native_wake_tools"]
    # The loop dies with the CLI session; the gate text must say so instead of
    # promising unattended heartbeat support.
    gate = packet["host_mutation"]["missing_host_tool_gate"]
    assert "only" in gate
    assert "alive" in gate
    assert "daemon" in gate
    assert "advisory" in gate
    steps = " ".join(packet["activation_steps"])
    assert "`/goal <task_body>`" in steps
    assert AGY_GOAL_COMPLETE_TOKEN in steps
    assert "`schedule` tool" in steps
    assert "MaxIterations" in steps
    assert "quota should-run" in steps
    assert "no host scheduler to fall back on" not in steps
    assert packet["setup_command"] == _surface_install_command(HOST_SURFACE, "loopx", ".")
    assert "quota should-run" in _start_instruction(HOST_SURFACE)
    assert "/goal" in _start_instruction(HOST_SURFACE)
    assert (
        packet["entry_command_hint"]
        == "the LoopX skill installed in ~/.gemini/antigravity-cli/skills"
    )
    # Ensure rendered heartbeat commands and scope contain no machine-gate semantics
    assert "gated" not in packet["activation_input_command"].lower()
    assert "advisory" in packet["activation_input_command"].lower()
    hb = _heartbeat_commands(
        goal_id="surface-goal",
        agent_type=HOST_SURFACE,
        cli_bin="loopx",
        agent_id="probe-agent",
    )
    assert "gated" not in hb["heartbeat_prompt"].lower()
    assert "gated" not in hb["heartbeat_prompt_json"].lower()
    assert "advisory" in hb["heartbeat_prompt"].lower()
    assert "advisory" in hb["heartbeat_prompt_json"].lower()


def test_native_goal_and_wake_facts_match_the_live_probes() -> None:
    """The host-facts constants are the single source the activation packet,
    README and PR narrative cite; they must stay pinned to what the live
    agy 1.1.18 probes actually demonstrated."""
    assert AGY_GOAL_COMMAND == "/goal"
    assert "completely finished" in AGY_GOAL_COMMAND_DESCRIPTION
    assert AGY_GOAL_COMPLETE_TOKEN == "<!-- GOAL_COMPLETE -->"
    assert AGY_GOAL_CANCELLED_TOKEN == "<!-- GOAL_CANCELLED -->"
    facts = " ".join(AGY_NATIVE_WAKE_FACTS)
    assert "DurationSeconds" in facts
    assert "Prompt" in facts
    assert "MaxIterations" in facts
    assert "hooks.json" in facts
    assert set(AGY_NATIVE_WAKE_TOOLS) >= {
        "schedule",
        "manage_task",
        "invoke_subagent",
        "send_message",
        "manage_inbox",
    }


def test_antigravity_app_first_class_host_surface_and_schedule_sidecar_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Antigravity 2.0 (antigravity-app) is a first-class host surface alongside agy."""
    start_dir = tmp_path / "start"
    start_dir.mkdir()
    payload = start_goal_accepts_surface(ANTIGRAVITY_APP_AGENT_TYPE, start_dir)
    assert (
        payload["command_pack"]["host_loop_activation"]["host_surface"]
        == "antigravity_app_loop"
    )
    activation = build_host_loop_activation_packet(
        agent_type=ANTIGRAVITY_APP_AGENT_TYPE,
        goal_id="surface-goal",
        agent_id="probe-agent",
        registered_agents=["probe-agent"],
    )
    assert activation["host_surface"] == "antigravity_app_loop"
    assert (
        activation["activation_method"]
        == "bind_antigravity_schedule_loop_and_sidecar"
    )
    mutation = activation["host_mutation"]
    assert mutation["host_loop_primitive"] == "antigravity-schedule-tool-and-sidecar"
    assert mutation["in_session_schedule_call"]["tool"] == "schedule"
    assert "DurationSeconds" in mutation["in_session_schedule_call"]["arguments"]
    assert "waiting_external" in mutation["in_session_schedule_call"]["when_to_use"]
    assert (
        mutation["sidecar_template"]["schema_version"]
        == "loopx_antigravity_sidecar_template_v0"
    )
    assert (
        mutation["sidecar_template"]["sidecar_config_path"]
        == "~/.gemini/config/sidecars/loopx-antigravity-heartbeat/sidecar.json"
    )
    sidecar_json = mutation["sidecar_template"]["sidecar_json"]
    assert sidecar_json["builtin"] == "schedule"
    assert sidecar_json["restart_policy"] == "always"
    assert sidecar_json["args"][1:5] == [
        "agentapi",
        "new-conversation",
        "--model=pro",
        "--",
    ]
    assert (
        "antigravity_app"
        in mutation["sidecar_template"]["sidecar"]["loopx-heartbeat"]["gate_command"]
    )
    assert mutation["hooks_template"]["loopx"]["Stop"][0]["type"] == "command"
    assert mutation["sidecar_template"]["sidecar_json"]["args"][0] == "CRON_TZ=UTC */3 * * * *"

    gate_dir = tmp_path / "gate"
    gate_dir.mkdir()
    selection_gate_offers_surface(ANTIGRAVITY_APP_AGENT_TYPE, gate_dir)

    assert normalize_agent_type("antigravity-app") == ANTIGRAVITY_APP_AGENT_TYPE
    assert normalize_agent_type("agy-app") == ANTIGRAVITY_APP_AGENT_TYPE
    assert normalize_agent_type("antigravity 2.0") == ANTIGRAVITY_APP_AGENT_TYPE
    assert scheduler_command_binding_for_agent_type(ANTIGRAVITY_APP_AGENT_TYPE) == {
        "runtime_profile": "antigravity_app"
    }

    from loopx.agy_goal_mode import cron_for_interval_minutes
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_context import validate_quota_command_context_request
    from loopx.control_plane.scheduler.state import (
        APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    )
    from tests.control_plane.scheduler_arbitration_fixture import (
        build_native_scheduler_hint,
    )

    # Verify antigravity_app runtime profile maps to HOST_AUTOMATION + HOSTED_AUTOMATION
    # with first-class app_automation stateful backoff (matching Codex App & Trae App),
    # while emitting --antigravity_app (never -A / --codex-app).
    from loopx.control_plane.scheduler.execution_context import (
        APP_HEARTBEAT_SETTLEMENT_RUNTIME_PROFILES,
        VISIBLE_GOAL_SETTLEMENT_RUNTIME_PROFILES,
    )

    assert (
        SchedulerRuntimeProfile.ANTIGRAVITY_APP
        in APP_HEARTBEAT_SETTLEMENT_RUNTIME_PROFILES
    )
    assert (
        SchedulerRuntimeProfile.ANTIGRAVITY_APP
        not in VISIBLE_GOAL_SETTLEMENT_RUNTIME_PROFILES
    )
    runtime_ctx = scheduler_execution_context_for_runtime_profile(
        SchedulerRuntimeProfile.ANTIGRAVITY_APP
    )
    assert runtime_ctx.ok
    assert runtime_ctx.context is not None
    assert runtime_ctx.context.host_surface is HostSurface.ANTIGRAVITY_APP
    assert runtime_ctx.context.scheduler_owner is SchedulerOwner.HOST_AUTOMATION
    assert runtime_ctx.context.execution_mode is ExecutionMode.HOSTED_AUTOMATION
    assert runtime_ctx.context.app_automation_applicable is True
    assert "codex_app_applicability" not in runtime_ctx.projection()
    assert (
        runtime_ctx.projection()["app_automation_applicability"] == "applicable"
    )

    # Invalid owner/mode combinations on antigravity_app are rejected cleanly
    invalid_ctx = resolve_scheduler_execution_context(
        {
            "host_surface": HostSurface.ANTIGRAVITY_APP.value,
            "scheduler_owner": SchedulerOwner.AGENT_CLI_LOOP.value,
            "execution_mode": ExecutionMode.INTERACTIVE.value,
        }
    )
    assert not invalid_ctx.ok
    assert (
        invalid_ctx.projection()["app_automation_applicability"]
        == "blocked_invalid_context"
    )

    # Verify build_scheduler_hint projects scheduler_hint.app_automation with
    # --antigravity_app and --surface antigravity_app (and no -A / codex_app)
    hint = build_native_scheduler_hint(
        {
            "goal_id": "surface-goal",
            "agent_identity": {"agent_id": "probe-agent"},
            "should_run": True,
            "effective_action": "normal_run",
            "recommended_action": "Advance the public fixture.",
            "heartbeat_recommendation": {
                "recommended_mode": "normal_run",
                "spend_policy": "spend only after validated writeback",
            },
            "execution_obligation": {
                "must_attempt_work": True,
                "spend_policy": "spend only after validated writeback",
            },
            "automation_liveness": {
                "automation_action": "execute_bounded_work",
                "spend_policy": "spend only after validated writeback",
            },
            "interaction_contract": {
                "schema_version": "loopx_interaction_contract_v0",
                "mode": "normal_run",
                "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
                "agent_channel": {
                    "must_attempt": True,
                    "delivery_allowed": True,
                    "quiet_noop_allowed": False,
                },
                "cli_channel": {"next_cli_actions": [], "spend_allowed_now": False},
            },
        },
        include_detail=True,
        scheduler_execution_context=runtime_ctx,
    )
    assert "codex_app" not in hint
    app_hint = hint["app_automation"]
    assert app_hint["host_surface"] == "antigravity_app"
    assert (
        app_hint["stateful_backoff"]["state_key"]
        == APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
    )
    assert app_hint["recommended_interval_minutes"] == 3
    ack_cli_args = app_hint["ack_hint"]["cli_args"]
    assert "--antigravity_app" in ack_cli_args
    assert "-A" not in ack_cli_args
    assert "--codex-app" not in ack_cli_args
    assert "--surface" in ack_cli_args
    assert "antigravity_app" in ack_cli_args

    assert (
        cron_for_interval_minutes(app_hint["recommended_interval_minutes"])
        == "*/3 * * * *"
    )

    # Verify quota scheduler follow-up parser defaults to antigravity_app + app_automation state key
    followup_args = build_parser().parse_args(
        [
            "quota",
            "scheduler-fail-current",
            "--goal-id",
            "surface-goal",
            "--agent-id",
            "probe-agent",
            "--antigravity_app",
            "--failed-rrule",
            "FREQ=MINUTELY;INTERVAL=3",
            "--execute",
        ]
    )
    validate_quota_command_context_request(followup_args)
    assert followup_args.surface == "antigravity_app"
    assert followup_args.state_key == APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY

    # Verify hooks_template includes --goal-id and --agent-id when bound, and sidecar uses
    # scheduler_hint.app_automation.recommended_interval_minutes.
    hook_command = mutation["hooks_template"]["loopx"]["Stop"][0]["command"]
    assert "--goal-id surface-goal" in hook_command
    assert "--agent-id probe-agent" in hook_command
    assert (
        mutation["sidecar_template"]["sidecar"]["loopx-heartbeat"][
            "suspend_on_waiting_external"
        ]["interval_minutes_field"]
        == "scheduler_hint.app_automation.recommended_interval_minutes"
    )
    assert (
        mutation["sidecar_template"]["sidecar"]["loopx-heartbeat"][
            "stateful_backoff"
        ]["state_key"]
        == APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
    )


def test_antigravity_dual_skills_mcp_hooks_workflow_skills_and_doctor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ~/.gemini/config/skills/<name>/SKILL.md dual compatibility,
    --agy-home override, optional mcp_config.json + hooks.json registration,
    workflow-skills --install, and doctor checks."""
    import loopx.slash_command_install as sci

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AGY_HOME", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_HOME", raising=False)
    monkeypatch.setattr(
        sci,
        "_loopx_mcp_command",
        lambda: ("/usr/bin/python3", "/opt/loopx/mcp_server.py"),
    )

    # 1. Default `agy` surface installs BOTH legacy flat ~/.gemini/antigravity-cli/skills/<name>.md
    #    AND Antigravity 2.0 ~/.gemini/config/skills/<name>/SKILL.md
    dual_payload = install_slash_commands(
        execute=True,
        surfaces=["agy"],
    )
    assert dual_payload["ok"] is True
    assert (
        tmp_path / "home" / ".gemini" / "antigravity-cli" / "skills" / "loopx.md"
    ).is_file()
    assert (
        tmp_path / "home" / ".gemini" / "config" / "skills" / "loopx" / "SKILL.md"
    ).is_file()

    # 2. Explicit --agy-home with --with-agy-mcp and --with-agy-hooks
    custom_agy_home = tmp_path / "custom-agy-config"
    app_payload = install_slash_commands(
        execute=True,
        surfaces=["antigravity-app"],
        agy_home=str(custom_agy_home),
        with_agy_mcp=True,
        with_agy_hooks=True,
    )
    assert app_payload["ok"] is True
    assert (custom_agy_home / "skills" / "loopx" / "SKILL.md").is_file()
    assert (custom_agy_home / "mcp_config.json").is_file()
    assert (custom_agy_home / "hooks.json").is_file()

    # Uninstall retires managed MCP, hooks, and skill files cleanly
    uninstall_payload = install_slash_commands(
        execute=True,
        uninstall=True,
        surfaces=["antigravity-app"],
        agy_home=str(custom_agy_home),
        with_agy_mcp=True,
        with_agy_hooks=True,
    )
    assert uninstall_payload["ok"] is True
    assert not (custom_agy_home / "skills" / "loopx" / "SKILL.md").exists()

    # 3. workflow-skills --install --host-surface antigravity-app --agy-home
    wf_payload = workflow_skill_install(
        execute=True,
        host_surface="antigravity-app",
        agy_home=str(custom_agy_home),
    )
    assert wf_payload["ok"] is True
    assert (custom_agy_home / "skills" / "loopx-project" / "SKILL.md").is_file()
    assert (custom_agy_home / "skills" / "loopx" / "SKILL.md").is_file()

    # 4. doctor --agent-type antigravity-app checks the Antigravity config home
    monkeypatch.setenv("AGY_HOME", str(custom_agy_home))
    doc = collect_doctor(agent_type="antigravity-app")
    assert "antigravity" in doc
    assert doc["antigravity"]["config_home"] == str(custom_agy_home)
    assert doc["antigravity"]["workflow_skills_readback"]["ready"] is True
    agy_check = next(
        c for c in doc["checks"] if c["id"] == "antigravity_host_surface_ready"
    )
    assert agy_check["ok"] is True


def test_antigravity_app_real_cli_ack_and_stateful_backoff_identity(
    tmp_path: Path,
) -> None:
    """Verify Antigravity App (`--antigravity_app`) runs real CLI `quota should-run`,
    `quota scheduler-ack-current`, and `quota scheduler-fail-current` with independent
    `antigravity_app` state persistence and zero `-A` / `--codex-app` leakage."""
    from examples.control_plane.quota_plan_fixtures import (
        SCOPED_AGENT_ID,
        write_cli_fixture,
    )
    from loopx.control_plane.scheduler.state import (
        APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
        load_scheduler_state,
    )
    from loopx.control_plane.testing.canary_harness import (
        run_json_cli,
        run_json_cli_result,
    )

    goal_id = "needs-operator"
    registry_path, runtime_root, project = write_cli_fixture(
        tmp_path / "fixture",
        scoped_agents=True,
    )
    _returncode, first = run_json_cli_result(
        "quota",
        "should-run",
        "--goal-id",
        goal_id,
        "--agent-id",
        SCOPED_AGENT_ID,
        "--antigravity_app",
        "--turn-instance-id",
        "2026-11-02T04:00:00Z",
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert "codex_app" not in first["scheduler_hint"]
    app = first["scheduler_hint"]["app_automation"]
    assert app["host_surface"] == "antigravity_app"
    assert (
        app["stateful_backoff"]["state_key"]
        == APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
    )
    assert "--antigravity_app" in app["ack_hint"]["cli_args"]
    assert "-A" not in app["ack_hint"]["cli_args"]

    ack = run_json_cli(
        *app["ack_hint"]["cli_args"],
        registry_path=registry_path,
        runtime_root=runtime_root,
        cwd=project,
    )
    assert ack["scheduler_state_mutated"] is True
    assert ack["surface"] == "antigravity_app"
    assert ack["state_key"] == APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY
    persisted = load_scheduler_state(
        runtime_root,
        goal_id=goal_id,
        agent_id=SCOPED_AGENT_ID,
        surface="antigravity_app",
        state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    )
    assert persisted is not None
    assert (
        load_scheduler_state(
            runtime_root,
            goal_id=goal_id,
            agent_id=SCOPED_AGENT_ID,
            surface="codex_app",
            state_key=APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
        )
        is None
    )
