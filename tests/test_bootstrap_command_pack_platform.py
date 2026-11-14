"""Generated guided-connect commands must run on their documented host shells."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import loopx.project_prompt as project_prompt
from loopx.bootstrap_command_pack import build_start_goal_guided_packet
from host_surface_cli_probes import connected_project, run_cli


PWSH = shutil.which("pwsh")
GOAL_ID = "windows-guided-connect"
GOAL_TEXT = "Verify native PowerShell guided connection"


@pytest.mark.parametrize(
    "entry",
    [
        ("bootstrap-command-pack", "--host-surface", "antigravity-app"),
        ("agent-onboard", "--agent-type", "antigravity-app"),
    ],
)
def test_activation_uses_explicit_runtime_for_linked_project_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: tuple[str, ...],
) -> None:
    from loopx import paths

    monkeypatch.delenv("LOOPX_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("LOOPX_REGISTRY", raising=False)
    markers = {}
    for root in (paths.DEFAULT_RUNTIME_ROOT, paths.LEGACY_RUNTIME_ROOT):
        marker = root / "owned-state" / "marker.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{"preserved":true}\n', encoding="utf-8")
        markers[marker] = marker.read_bytes()
    assert paths.default_runtime_route()["status"] == "conflict"

    primary = connected_project(tmp_path / "canonical")
    registry = primary / ".loopx" / "registry.json"
    state = primary / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\nVerify the selected activation route.\n", encoding="utf-8")
    document = json.loads(registry.read_text(encoding="utf-8"))
    document["common_runtime_root"] = str(tmp_path / "other runtime")
    goal = document["goals"][0]
    goal["state_file"] = state.name
    goal["coordination"] = {"registered_agents": ["probe-agent"], "agent_model": "peer_v1"}
    registry.write_text(json.dumps(document), encoding="utf-8")
    for argv in (
        ["init"],
        ["-c", "user.name=LoopX Test", "-c", "user.email=loopx@example.invalid",
         "commit", "--allow-empty", "-m", "test fixture"],
    ):
        subprocess.run(["git", *argv], cwd=primary, check=True, capture_output=True)
    linked = tmp_path / "linked project"
    subprocess.run(["git", "worktree", "add", "--detach", str(linked)], cwd=primary,
                   check=True, capture_output=True)
    selected = primary / "selected runtime"
    selected.mkdir()
    global_registry = paths.global_registry_path(selected)
    global_registry.write_text(json.dumps({
        "goals": [{**goal, "source_registry": str(registry)}],
    }), encoding="utf-8")

    args = (
        "--registry", str(registry), *entry, "--project", str(linked),
        "--goal-id", "surface-goal", "--agent-id", "probe-agent",
    )
    for route, registry_args in (
        (selected.name, args),
        (str(selected), args[2:]),
        (str(selected), ("--registry", "@host-global", *args[2:])),
    ):
        explicit = run_cli("--runtime-root", route, *registry_args, cwd=tmp_path)
        assert explicit.returncode == 0, explicit.stdout + explicit.stderr
        packet = json.loads(explicit.stdout)
        alias = packet["project_connection"]["canonical_project_alias"]
        assert alias["applied"]
        assert Path(alias["global_registry"]) == global_registry
        assert Path(packet["project_connection"]["registry"]) == registry
        assert packet["host_loop_activation"]["activation_allowed"]
        commands = [packet["host_loop_activation"]["activation_input_command"]]
        if entry[0] == "agent-onboard":
            commands += [packet["commands"][key] for key in (
                "bootstrap_command_pack", "agent_onboard_recheck", "quota_guard",
            )]
        for command in commands:
            argv = shlex.split(command, posix=os.name != "nt")
            assert Path(argv[argv.index("--runtime-root") + 1].strip("'")) == selected

    implicit = run_cli(*args, cwd=tmp_path)
    assert implicit.returncode != 0
    assert "Both default LoopX runtime roots contain state" in implicit.stdout + implicit.stderr
    assert all(path.read_bytes() == data for path, data in markers.items())


def guided_packet(
    project: Path,
    *,
    fine_grained: bool = False,
    goal_text: str = GOAL_TEXT,
    runtime_root: Path | None = None,
) -> dict[str, object]:
    return build_start_goal_guided_packet(
        project=project,
        goal_id=GOAL_ID,
        agent_id=None,
        cli_bin="loopx",
        host_surface="shell",
        goal_text=goal_text,
        fine_grained=fine_grained,
        runtime_root_arg=str(runtime_root) if runtime_root else None,
        include_command_pack_detail=True,
    )


def connect_command(packet: dict[str, object]) -> str:
    guided_transaction = packet["guided_transaction"]
    assert isinstance(guided_transaction, dict)
    steps = guided_transaction["ordered_steps"]
    assert isinstance(steps, list)
    connect = next(step for step in steps if step["id"] == "connect_if_needed")
    assert isinstance(connect, dict)
    return str(connect["command"])


def test_guided_bootstrap_commands_do_not_require_posix_continuations(
    tmp_path: Path,
) -> None:
    packet = guided_packet(tmp_path / "project")
    command = connect_command(packet)

    assert "\\\n" not in command
    lines = command.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("cd ")
    assert lines[1].startswith("loopx bootstrap --project .")

    recommended = packet["recommended_next_step"]
    assert isinstance(recommended, dict)
    identity_gate = recommended["identity_selection_gate"]
    assert isinstance(identity_gate, dict)
    fresh_registration = identity_gate["fresh_agent_registration"]
    assert isinstance(fresh_registration, dict)
    assert fresh_registration["connect_if_needed_command"] == command

    command_pack = packet["command_pack"]
    assert isinstance(command_pack, dict)
    commands = command_pack["commands"]
    assert isinstance(commands, dict)
    for key in (
        "bootstrap_dry_run_preview",
        "bootstrap_after_user_confirmation",
        "goal_start_connect_if_needed",
    ):
        assert "\\\n" not in str(commands[key]), key

    tokens = shlex.split(lines[1])
    assert tokens == [
        "loopx",
        "bootstrap",
        "--project",
        ".",
        "--goal-id",
        GOAL_ID,
        "--objective",
        GOAL_TEXT,
        "--adapter-kind",
        "read_only_project_map_v0",
        "--adapter-status",
        "connected-read-only",
    ]


@pytest.mark.parametrize("quote", ["'", "\u2018", "\u2019", "\u201a", "\u201b"])
def test_host_shell_arg_escapes_powershell_apostrophes(
    monkeypatch: pytest.MonkeyPatch, quote: str,
) -> None:
    monkeypatch.setattr(project_prompt, "_is_windows_host", lambda: True)
    monkeypatch.setattr(project_prompt.install_contract, "_is_windows_host", lambda: True)

    assert project_prompt.shell_arg_for_host("read_only_project_map_v0") == (
        "read_only_project_map_v0"
    )
    path = f"C:\\O{quote}Neil\\loopx.ps1"
    literal = f"'C:\\O{quote}{quote}Neil\\loopx.ps1'"
    assert project_prompt.shell_arg_for_host(path) == literal
    assert project_prompt.host_command(path) == f"& {literal}"


def test_windows_guided_packet_quotes_every_executable_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_prompt, "_is_windows_host", lambda: True)
    project = tmp_path / "project's"
    runtime_root = tmp_path / "runtime's"
    goal_text = "Verify user's guided command quoting"
    packet = guided_packet(project, goal_text=goal_text, runtime_root=runtime_root)
    command_pack = packet["command_pack"]
    assert isinstance(command_pack, dict)
    commands = command_pack["commands"]
    assert isinstance(commands, dict)
    recommended = packet["recommended_next_step"]
    assert isinstance(recommended, dict)
    identity_gate = recommended["identity_selection_gate"]
    assert isinstance(identity_gate, dict)
    fresh_registration = identity_gate["fresh_agent_registration"]
    assert isinstance(fresh_registration, dict)

    direct = connect_command(packet)
    rerun = str(fresh_registration["rerun_start_goal_command"])
    register_preview = str(fresh_registration["preview_command"])
    register_execute = str(fresh_registration["execute_command"])
    status = str(commands["status"])
    agent_recheck = str(commands["goal_start_agent_onboard_recheck"])
    canonical = str(command_pack["canonical_cli_command"])
    executable_commands = [
        direct,
        rerun,
        register_preview,
        register_execute,
        status,
        agent_recheck,
        canonical,
    ]
    for command in executable_commands:
        assert "runtime''s" in command
        assert "'\\''" not in command
    for command in (direct, rerun, status, agent_recheck, canonical):
        assert "project''s" in command
    for command in (direct, rerun, canonical):
        assert "user''s" in command


def test_generated_guided_connect_command_runs_in_powershell(tmp_path: Path) -> None:
    if os.name != "nt" or PWSH is None:
        pytest.skip("requires native Windows and PowerShell 7")
    pwsh = PWSH
    assert pwsh is not None
    project = tmp_path / "project's"
    project.mkdir()
    runtime_root = tmp_path / "runtime's"
    runtime_root.mkdir()
    goal_text = "Verify user's native PowerShell guided connection"
    packet = guided_packet(
        project,
        fine_grained=True,
        goal_text=goal_text,
        runtime_root=runtime_root,
    )
    command_pack = packet["command_pack"]
    assert isinstance(command_pack, dict)
    commands = command_pack["commands"]
    assert isinstance(commands, dict)
    recommended = packet["recommended_next_step"]
    assert isinstance(recommended, dict)
    identity_gate = recommended["identity_selection_gate"]
    assert isinstance(identity_gate, dict)
    fresh_registration = identity_gate["fresh_agent_registration"]
    assert isinstance(fresh_registration, dict)

    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = (
        f"{Path(sys.executable).parent}{os.pathsep}{environment.get('PATH', '')}"
    )
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)

    def run_generated(name: str, command: str) -> subprocess.CompletedProcess[str]:
        script = tmp_path / f"{name}.ps1"
        script.write_text(command, encoding="utf-8")
        return subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-File", str(script)],
            capture_output=True,
            text=True,
            timeout=120,
            env=environment,
        )

    connect = connect_command(packet)
    assert "--fine-grained" in connect
    completed = run_generated("connect", connect)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    register_preview = str(fresh_registration["preview_command"]).replace(
        "<new-public-safe-agent-id>", "windows-guided-agent"
    )
    register = run_generated("register-preview", register_preview)
    assert register.returncode == 0, register.stdout + register.stderr
    status = run_generated("status", str(commands["status"]))
    assert status.returncode == 0, status.stdout + status.stderr
    assert "Set-Location" not in status.stdout + status.stderr
    rerun = run_generated(
        "rerun", str(fresh_registration["rerun_start_goal_command"])
    )
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    canonical = run_generated(
        "canonical", str(command_pack["canonical_cli_command"])
    )
    assert canonical.returncode == 0, canonical.stdout + canonical.stderr

    registry = project / ".loopx" / "registry.json"
    state = project / ".loopx" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    assert registry.is_file()
    assert state.is_file()
